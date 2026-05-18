"""
Старт партии с заявки клиента: линия и смена выбираются по открытой смене пользователя;
расход материалов по рецепту со склада не делается — списание подготовленной заготовки с цеха (blank).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.sales import models as sales_models
from apps.production.batch_stock import apply_production_batch_stock_and_cost
from apps.production.models import Line, ProductionBatch, Shift
from apps.production.shift_state import (
    line_shift_is_open,
    line_shift_is_paused,
    prefetch_line_histories_map,
)
from apps.sales.client_order_production import recipe_for_profile
from apps.workshop.blank_run_stock import create_run_deduct_workshop_only
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import WorkshopBlank, WorkshopPreparedState
from apps.workshop.services import get_or_create_prepared, total_on_workshop


def resolve_line_and_shift_for_user(*, user) -> tuple[Line, Shift]:
    shift = (
        Shift.objects.filter(
            user=user,
            closed_at__isnull=True,
            status=Shift.STATUS_OPEN,
        )
        .select_related('line')
        .order_by('-id')
        .first()
    )
    if not shift or not shift.line_id:
        raise DRFValidationError(
            {'code': 'NO_OPEN_SHIFT', 'detail': 'Нет открытой смены на линии для текущего пользователя.'},
        )
    line = shift.line
    ctx = {'line_histories': prefetch_line_histories_map([line.pk])}
    hist_map = ctx.get('line_histories') or {}
    hist = hist_map.get(line.pk) if getattr(line, 'pk', None) else None
    if getattr(line, 'is_active', True) is False:
        raise DRFValidationError({'code': 'LINE_INACTIVE', 'detail': 'Линия неактивна.'})
    if not line_shift_is_open(line, histories=hist):
        raise DRFValidationError(
            {'code': 'LINE_SHIFT_CLOSED', 'detail': 'На линии нет открытой смены.'},
        )
    if line_shift_is_paused(line, histories=hist):
        raise DRFValidationError(
            {'code': 'LINE_SHIFT_PAUSED', 'detail': 'Смена на линии на паузе. Возобновите смену.'},
        )
    return line, shift


def _resolve_profile_recipe_qty_length_for_blank_start(*, client_order: sales_models.Order):
    """
    Профиль / кол-во / длина обязательны; рецепт — по возможности (для метаданных партии),
    но при старте с заготовкой цеха FIFO по рецепту не выполняется, рецепт может отсутствовать.
    """
    profile = client_order.production_profile
    recipe = client_order.resolved_recipe
    qty_i = client_order.production_quantity
    ln = client_order.production_length

    if profile is None:
        ln_obj = (
            client_order.lines.select_related('profile').filter(profile_id__isnull=False).order_by('id').first()
        )
        if ln_obj and ln_obj.profile_id:
            profile = ln_obj.profile

    if qty_i is None:
        ln_obj = client_order.lines.order_by('id').first()
        if ln_obj is not None:
            qty_i = int(Decimal(str(ln_obj.ordered_quantity)))

    if ln is None:
        ln = Decimal('1')

    if recipe is None and profile is not None:
        recipe = recipe_for_profile(profile.pk)

    if profile is None:
        raise DRFValidationError(
            {'code': 'INCOMPLETE_CLIENT_ORDER', 'detail': 'У заявки нет профиля (или строк с профилем).'},
        )
    if qty_i is None or int(qty_i) <= 0:
        raise DRFValidationError({'code': 'INVALID_QUANTITY', 'detail': 'Некорректное количество в заявке'})
    if ln is None or Decimal(str(ln)) <= 0:
        raise DRFValidationError({'code': 'INVALID_LENGTH', 'detail': 'Некорректная длина в заявке'})

    return profile, recipe, int(qty_i), Decimal(str(ln))


@transaction.atomic
def start_production_for_client_order(
    *,
    user,
    client_order_id: int,
    workshop_blank_id: int,
) -> ProductionBatch:
    client_order = (
        sales_models.Order.objects.select_for_update()
        .prefetch_related('lines', 'lines__profile')
        .select_related('production_profile', 'resolved_recipe', 'client')
        .get(pk=client_order_id)
    )

    if client_order.request_status in (
        sales_models.REQUEST_STATUS_IN_PRODUCTION,
        sales_models.REQUEST_STATUS_REJECTED,
    ):
        raise DRFValidationError(
            {'code': 'INVALID_REQUEST_STATUS', 'detail': 'Заявка уже в производстве или отклонена производством.'},
        )

    if ProductionBatch.objects.filter(client_order_id=client_order.pk).exists():
        raise DRFValidationError(
            {'code': 'BATCH_EXISTS', 'detail': 'По этой заявке уже создана партия'},
        )

    try:
        blank = WorkshopBlank.objects.select_for_update().get(pk=workshop_blank_id)
    except WorkshopBlank.DoesNotExist:
        raise DRFValidationError({'code': 'BLANK_NOT_FOUND', 'detail': 'Заготовка не найдена.'})

    if not blank.is_active:
        raise DRFValidationError({'code': 'BLANK_INACTIVE', 'detail': 'Заготовка неактивна.'})

    profile, recipe, pieces, length_d = _resolve_profile_recipe_qty_length_for_blank_start(client_order=client_order)

    if blank.plastic_profile_id and blank.plastic_profile_id != profile.pk:
        raise DRFValidationError(
            {'code': 'BLANK_PROFILE_MISMATCH', 'detail': 'Заготовка не соответствует профилю заявки.'},
        )

    if recipe is not None:
        if recipe.profile_id != profile.pk:
            raise DRFValidationError({'recipe': 'Рецепт заявки не относится к профилю'})
        if not recipe.components.exists():
            raise DRFValidationError({'recipe': 'У рецепта нет компонентов'})

    line, shift = resolve_line_and_shift_for_user(user=user)

    wpp = profile.weight_kg_per_piece
    if wpp is None or Decimal(str(wpp)) <= 0:
        raise DRFValidationError(
            {'code': 'MISSING_PROFILE_WEIGHT', 'detail': 'У профиля не задан вес штуки (weight_kg_per_piece).'},
        )
    weight_kg_per_piece = Decimal(str(wpp))
    need_kg = (weight_kg_per_piece * Decimal(pieces)).quantize(Decimal('0.000001'))

    get_or_create_prepared(blank)
    prepared = WorkshopPreparedState.objects.select_for_update().get(blank_id=blank.pk)
    barrel_kg = Decimal(str(blank.recipe_kg_per_barrel))
    available = total_on_workshop(prepared.barrels, prepared.extra_kg, barrel_kg)
    if available < need_kg:
        raise DRFValidationError(
            {
                'code': 'BLANK_INSUFFICIENT_STOCK',
                'detail': f'Недостаточно заготовки на цеху: нужно {need_kg} кг, доступно {available} кг.',
            },
        )

    now = timezone.now()
    today = now.date()
    product_name = (profile.name or '')[:255] if profile else '—'

    batch = ProductionBatch(
        order=None,
        client_order=client_order,
        workshop_blank=blank,
        profile=profile,
        recipe=recipe,
        line=line,
        shift=shift,
        product=product_name,
        pieces=pieces,
        length_per_piece=length_d,
        operator=user,
        date=today,
        produced_at=now,
        otk_status=ProductionBatch.OTK_PENDING,
        lifecycle_status=ProductionBatch.LIFECYCLE_PENDING,
        sent_to_otk=False,
        in_otk_queue=False,
        comment='',
    )
    batch.recompute_totals()
    batch.save()

    try:
        create_run_deduct_workshop_only(
            blank=blank,
            product=profile,
            validated={
                'blank_total_kg': available,
                'blank_used_in_production_kg': need_kg,
                'vat_max_kg_demo': Decimal('0'),
                'weight_kg_per_piece': weight_kg_per_piece,
            },
            production_batch=batch,
        )
    except WorkshopConflict as e:
        raise DRFValidationError(
            {'code': 'BLANK_INSUFFICIENT_STOCK', 'detail': getattr(e, 'detail', str(e))},
        ) from e

    apply_production_batch_stock_and_cost(batch, skip_recipe_consumption=True)

    client_order.request_status = sales_models.REQUEST_STATUS_IN_PRODUCTION
    client_order.save(update_fields=['request_status', 'updated_at'])
    return batch
