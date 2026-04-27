"""
Старт партии с заявки клиента: те же правила, что и POST /api/batches/ (смена, линия, FIFO).
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


@transaction.atomic
def start_production_for_client_order(
    *,
    user,
    line: Line,
    client_order_id: int,
) -> ProductionBatch:
    client_order = (
        sales_models.Order.objects.select_for_update()
        .select_related('production_profile', 'resolved_recipe', 'client')
        .get(pk=client_order_id)
    )
    if client_order.request_status != sales_models.REQUEST_STATUS_READY:
        raise DRFValidationError(
            {'code': 'INVALID_REQUEST_STATUS', 'detail': 'Старт только для заявки в статусе ready'},
        )
    if not client_order.resolved_recipe_id or not client_order.production_profile_id:
        raise DRFValidationError(
            {'code': 'INCOMPLETE_CLIENT_ORDER', 'detail': 'У заявки нет рецепта или профиля'},
        )
    if client_order.production_quantity is None or int(client_order.production_quantity) <= 0:
        raise DRFValidationError({'code': 'INVALID_QUANTITY', 'detail': 'Некорректное количество в заявке'})
    if client_order.production_length is None or Decimal(str(client_order.production_length)) <= 0:
        raise DRFValidationError({'code': 'INVALID_LENGTH', 'detail': 'Некорректная длина в заявке'})

    if ProductionBatch.objects.filter(client_order_id=client_order.pk).exists():
        raise DRFValidationError(
            {'code': 'BATCH_EXISTS', 'detail': 'По этой заявке уже создана партия'},
        )

    if getattr(line, 'is_active', True) is False:
        raise DRFValidationError({'line': 'Линия неактивна'})

    ctx = {'line_histories': prefetch_line_histories_map([line.pk])}
    hist_map = ctx.get('line_histories') or {}
    hist = hist_map.get(line.pk) if getattr(line, 'pk', None) else None
    if not line_shift_is_open(line, histories=hist):
        raise DRFValidationError(
            {'line': 'На линии нет открытой смены'},
        )
    if line_shift_is_paused(line, histories=hist):
        raise DRFValidationError(
            {'line': 'Смена на линии остановлена (пауза). Возобновите смену или выберите другую линию.'},
        )

    shift = (
        Shift.objects.filter(
            user=user,
            line=line,
            closed_at__isnull=True,
            status=Shift.STATUS_OPEN,
        )
        .select_for_update()
        .first()
    )
    if not shift:
        raise DRFValidationError(
            {
                'line': 'Нет активной открытой смены на этой линии для текущего пользователя.',
            },
        )
    if shift.line_id and line is not None and shift.line_id != line.pk:
        raise DRFValidationError({'line': 'Смена привязана к другой линии'})

    profile = client_order.production_profile
    recipe = client_order.resolved_recipe
    if recipe.profile_id != profile.pk:
        raise DRFValidationError(
            {'recipe': 'Рецепт заявки не относится к профилю'},
        )
    if not recipe.components.exists():
        raise DRFValidationError({'recipe': 'У рецепта нет компонентов'})

    prof = profile
    product_name = (prof.name or '')[:255] if prof else '—'
    now = timezone.now()
    today = now.date()

    batch = ProductionBatch(
        order=None,
        client_order=client_order,
        profile=profile,
        recipe=recipe,
        line=line,
        shift=shift,
        product=product_name,
        pieces=int(client_order.production_quantity),
        length_per_piece=Decimal(str(client_order.production_length)),
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
    apply_production_batch_stock_and_cost(batch)

    client_order.request_status = sales_models.REQUEST_STATUS_IN_PRODUCTION
    client_order.save(update_fields=['request_status', 'updated_at'])
    return batch
