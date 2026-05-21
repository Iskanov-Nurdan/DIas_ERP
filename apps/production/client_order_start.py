"""
Старт производства по заявке: line_starts (профиль → заготовка), личная смена,
отдельный BlankProductionRun на каждую позицию order_lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.sales import models as sales_models
from apps.production.batch_stock import apply_production_batch_stock_and_cost
from apps.production.models import ProductionBatch, Shift
from apps.sales.client_order_production import recipe_for_profile
from apps.sales.serializers import OrderSerializer
from apps.workshop.blank_run_stock import create_run_deduct_workshop_only
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import BlankProductionRun, WorkshopBlank, WorkshopPreparedState
from apps.workshop.services import get_or_create_prepared, total_on_workshop

if TYPE_CHECKING:
    from django.http import HttpRequest


def _api_error(code: str, message: str) -> DRFValidationError:
    return DRFValidationError({'code': code, 'detail': message, 'message': message})


def resolve_personal_shift_for_production_start(
    *,
    user,
    request: Optional['HttpRequest'] = None,
) -> Shift:
    shift_id_override = None
    if request is not None:
        raw = request.META.get('HTTP_X_AUDIT_SHIFT_ID') or request.META.get('HTTP_X_SHIFT_ID')
        if raw:
            try:
                shift_id_override = int(raw)
            except (TypeError, ValueError):
                shift_id_override = None

    shift = None
    if shift_id_override is not None:
        shift = (
            Shift.objects.filter(pk=shift_id_override, user=user, closed_at__isnull=True)
            .order_by('-opened_at')
            .first()
        )
        if shift is None:
            raise _api_error(
                'NO_OPEN_SHIFT',
                'Указанная смена не найдена или уже закрыта. Откройте смену в «Моя смена».',
            )
    else:
        shift = (
            Shift.objects.filter(
                user=user,
                line_id__isnull=True,
                closed_at__isnull=True,
            )
            .order_by('-opened_at')
            .first()
        )

    if shift is None:
        raise _api_error('NO_OPEN_SHIFT', 'Откройте смену в разделе «Моя смена».')

    if shift.status == Shift.STATUS_PAUSED:
        raise _api_error('SHIFT_PAUSED', 'Смена на паузе. Возобновите смену в «Моя смена».')

    if shift.status != Shift.STATUS_OPEN or shift.closed_at is not None:
        raise _api_error('NO_OPEN_SHIFT', 'Откройте смену в разделе «Моя смена».')

    return shift


@dataclass(frozen=True)
class LineStartSpec:
    order_line: sales_models.OrderLine
    blank_id: int


def _productive_order_lines(order: sales_models.Order) -> list[sales_models.OrderLine]:
    return list(
        order.lines.select_related('profile').filter(profile_id__isnull=False).order_by('id'),
    )


def _line_length_m(*, order_line: sales_models.OrderLine, client_order: sales_models.Order) -> Decimal:
    meta = OrderSerializer._extract_order_line_meta(order_line.comment)
    length = meta.get('length')
    if length not in (None, ''):
        try:
            return Decimal(str(length))
        except Exception:
            pass
    if client_order.production_length is not None:
        return Decimal(str(client_order.production_length))
    return Decimal('1')


def parse_line_starts_from_body(
    raw: dict,
    *,
    client_order: sales_models.Order,
) -> list[LineStartSpec]:
    """
    line_starts[] — основной формат; {blank, order_line_id} — одна позиция;
    {blank, profile_id} — fallback без order_line_id.
    blanks[] без привязки к позиции — отклоняется.
    """
    if not isinstance(raw, dict):
        raw = {}

    if raw.get('blanks') not in (None, '', []):
        raise _api_error(
            'LINE_STARTS_REQUIRED',
            'Используйте line_starts: для каждой позиции заявки укажите order_line_id и blank.',
        )

    productive = _productive_order_lines(client_order)
    if not productive:
        raise _api_error(
            'INCOMPLETE_CLIENT_ORDER',
            'У заявки нет строк с профилем для производства.',
        )

    entries: list[dict] = []

    line_starts_raw = raw.get('line_starts')
    if isinstance(line_starts_raw, list) and len(line_starts_raw) > 0:
        entries = line_starts_raw
    elif raw.get('blank') not in (None, ''):
        try:
            blank_id = int(raw['blank'])
        except (TypeError, ValueError):
            raise _api_error('MISSING_LINE_BLANK', 'blank должен быть числом.')
        oid = raw.get('order_line_id')
        if oid not in (None, ''):
            entries = [{'order_line_id': oid, 'blank': blank_id}]
        elif len(productive) == 1:
            entries = [{'order_line_id': productive[0].pk, 'blank': blank_id}]
        elif raw.get('profile_id') not in (None, ''):
            entries = [{'profile_id': raw['profile_id'], 'blank': blank_id}]
        elif len(productive) > 1:
            raise _api_error(
                'LINE_STARTS_REQUIRED',
                'Несколько позиций в заявке — передайте line_starts с order_line_id и blank для каждой.',
            )
        else:
            raise _api_error('MISSING_LINE_BLANK', 'Укажите blank и order_line_id (или line_starts).')
    else:
        if len(productive) > 1:
            raise _api_error(
                'LINE_STARTS_REQUIRED',
                'Несколько позиций в заявке — передайте line_starts с order_line_id и blank для каждой.',
            )
        raise _api_error('MISSING_LINE_BLANK', 'Укажите blank и order_line_id (или line_starts).')

    line_by_id = {ln.id: ln for ln in productive}
    line_by_profile = {ln.profile_id: ln for ln in productive}
    specs: list[LineStartSpec] = []
    seen_line_ids: set[int] = set()

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _api_error('MISSING_LINE_BLANK', f'line_starts[{idx}] должен быть объектом.')
        if entry.get('blank') in (None, ''):
            raise _api_error('MISSING_LINE_BLANK', f'line_starts[{idx}]: укажите blank.')
        try:
            blank_id = int(entry['blank'])
        except (TypeError, ValueError, KeyError):
            raise _api_error('MISSING_LINE_BLANK', f'line_starts[{idx}]: blank должен быть числом.')

        order_line = None
        oid = entry.get('order_line_id')
        if oid not in (None, ''):
            try:
                oid_i = int(oid)
            except (TypeError, ValueError):
                raise _api_error('UNKNOWN_ORDER_LINE', f'line_starts[{idx}]: некорректный order_line_id.')
            order_line = line_by_id.get(oid_i)
            if order_line is None:
                raise _api_error(
                    'UNKNOWN_ORDER_LINE',
                    f'Строка заявки #{oid_i} не найдена в этой заявке.',
                )
        else:
            pid = entry.get('profile_id')
            if pid not in (None, ''):
                try:
                    pid_i = int(pid)
                except (TypeError, ValueError):
                    raise _api_error('UNKNOWN_ORDER_LINE', f'line_starts[{idx}]: некорректный profile_id.')
                order_line = line_by_profile.get(pid_i)
                if order_line is None:
                    raise _api_error(
                        'UNKNOWN_ORDER_LINE',
                        f'Профиль #{pid_i} не найден среди строк этой заявки.',
                    )
            else:
                raise _api_error(
                    'MISSING_LINE_BLANK',
                    f'line_starts[{idx}]: укажите order_line_id или profile_id.',
                )

        if order_line.id in seen_line_ids:
            raise _api_error(
                'MISSING_LINE_BLANK',
                f'Строка заявки #{order_line.id} указана более одного раза.',
            )
        seen_line_ids.add(order_line.id)
        specs.append(LineStartSpec(order_line=order_line, blank_id=blank_id))

    required_ids = {ln.id for ln in productive}
    if seen_line_ids != required_ids:
        missing = required_ids - seen_line_ids
        raise _api_error(
            'MISSING_LINE_BLANK',
            f'Не для всех позиций заявки указана заготовка. Не хватает строк: {sorted(missing)}.',
        )

    return specs


def _load_blank_for_profile(*, blank_id: int, profile) -> tuple[WorkshopBlank, Decimal]:
    try:
        blank = WorkshopBlank.objects.select_for_update().get(pk=blank_id)
    except WorkshopBlank.DoesNotExist:
        raise _api_error('BLANK_NOT_FOUND', f'Заготовка #{blank_id} не найдена.')
    if not blank.is_active:
        raise _api_error('BLANK_NOT_FOUND', f'Заготовка #{blank_id} неактивна.')
    if blank.plastic_profile_id and blank.plastic_profile_id != profile.pk:
        raise _api_error(
            'BLANK_PROFILE_MISMATCH',
            f'Заготовка #{blank_id} не подходит к профилю «{profile.name}».',
        )
    get_or_create_prepared(blank)
    prepared = WorkshopPreparedState.objects.select_for_update().get(blank_id=blank.pk)
    barrel_kg = Decimal(str(blank.recipe_kg_per_barrel))
    available = total_on_workshop(prepared.barrels, prepared.extra_kg, barrel_kg)
    return blank, available


@transaction.atomic
def start_production_for_client_order(
    *,
    user,
    client_order_id: int,
    line_starts: list[LineStartSpec],
    request: Optional['HttpRequest'] = None,
) -> list[BlankProductionRun]:
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
        raise _api_error(
            'INVALID_REQUEST_STATUS',
            'Заявка уже в производстве или отклонена производством.',
        )

    if (
        ProductionBatch.objects.filter(client_order_id=client_order.pk).exists()
        or BlankProductionRun.objects.filter(client_request_id=client_order.pk).exists()
    ):
        raise _api_error('BATCH_EXISTS', 'По этой заявке производство уже запущено.')

    shift = resolve_personal_shift_for_production_start(user=user, request=request)
    now = timezone.now()
    today = now.date()
    created_runs: list[BlankProductionRun] = []

    for spec in line_starts:
        order_line = spec.order_line
        profile = order_line.profile
        if profile is None:
            raise _api_error('INCOMPLETE_CLIENT_ORDER', f'Строка #{order_line.id} без профиля.')

        pieces = int(Decimal(str(order_line.ordered_quantity or 0)))
        if pieces <= 0:
            raise _api_error(
                'INVALID_QUANTITY',
                f'Некорректное количество в строке #{order_line.id}.',
            )

        length_d = _line_length_m(order_line=order_line, client_order=client_order)
        if length_d <= 0:
            raise _api_error('INVALID_LENGTH', f'Некорректная длина в строке #{order_line.id}.')

        recipe = recipe_for_profile(profile.pk)
        if recipe is not None and not recipe.components.exists():
            raise _api_error('INVALID_RECIPE', 'У рецепта нет компонентов.')

        wpp = profile.weight_kg_per_piece
        if wpp is None or Decimal(str(wpp)) <= 0:
            raise _api_error(
                'MISSING_PROFILE_WEIGHT',
                f'У профиля «{profile.name}» не задан weight_kg_per_piece.',
            )
        weight_kg_per_piece = Decimal(str(wpp))
        need_kg = (weight_kg_per_piece * Decimal(pieces)).quantize(Decimal('0.000001'))

        blank, available = _load_blank_for_profile(blank_id=spec.blank_id, profile=profile)
        if available < need_kg:
            raise _api_error(
                'BLANK_INSUFFICIENT_STOCK',
                (
                    f'Недостаточно заготовки «{blank.name}» для «{profile.name}»: '
                    f'нужно {need_kg} кг, доступно {available} кг.'
                ),
            )

        product_name = (profile.name or '')[:255]
        batch = ProductionBatch(
            order=None,
            client_order=client_order,
            workshop_blank=blank,
            profile=profile,
            recipe=recipe,
            line=None,
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
            run = create_run_deduct_workshop_only(
                blank=blank,
                product=profile,
                validated={
                    'blank_total_kg': available,
                    'blank_used_in_production_kg': need_kg,
                    'vat_max_kg_demo': Decimal('0'),
                    'weight_kg_per_piece': weight_kg_per_piece,
                },
                production_batch=batch,
                order_line=order_line,
                client_request=client_order,
            )
        except WorkshopConflict as e:
            raise _api_error(
                'BLANK_INSUFFICIENT_STOCK',
                getattr(e, 'detail', str(e)),
            ) from e

        apply_production_batch_stock_and_cost(batch, skip_recipe_consumption=True)
        created_runs.append(run)

    client_order.request_status = sales_models.REQUEST_STATUS_IN_PRODUCTION
    client_order.save(update_fields=['request_status', 'updated_at'])
    return created_runs
