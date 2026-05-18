"""+1 бочка на цеху: списание сырья по FIFO (как в модуле «Сырьё») и учёт бочки."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError as DRFValidationError

from apps.activity.audit_service import schedule_entity_audit
from apps.materials.fifo import fifo_deduct, material_stock_kg
from apps.materials.models import MaterialStockDeduction
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import WorkshopBlank, WorkshopBlankCompositionLine, WorkshopPreparedState
from apps.workshop.services import get_or_create_prepared

logger = logging.getLogger(__name__)

# До 100 символов (поле MaterialStockDeduction.reason); reference_id = blank_id
WORKSHOP_BARREL_FIFO_REASON = 'workshop_blank_barrel'

_DEC = Decimal('0.000001')


def _q(x) -> Decimal:
    return Decimal(str(x)).quantize(_DEC)


def _insufficient_detail(raw_name: str, need: Decimal, avail: Decimal) -> str:
    from config.api_numbers import api_decimal_str

    return (
        f'Недостаточно {raw_name}: нужно {api_decimal_str(need)} кг, '
        f'доступно {api_decimal_str(avail)} кг'
    )


@transaction.atomic
def add_prepared_barrel_with_stock(*, blank_id: int, user, request) -> WorkshopBlank:
    """
    Одна транзакция: проверка остатков → FIFO по каждой строке состава → +1 бочка.
    quantity_kg в строке состава = кг сырья на одну бочку (база совпадает с recipe_kg_per_barrel).
    """
    try:
        blank = WorkshopBlank.objects.select_for_update().get(pk=blank_id)
    except WorkshopBlank.DoesNotExist:
        raise NotFound('Заготовка не найдена.')

    lines = list(
        WorkshopBlankCompositionLine.objects.select_related('raw_material').filter(blank_id=blank.pk)
    )
    if not lines:
        raise WorkshopConflict(detail='Нельзя добавить бочку: у заготовки пустой состав.')

    needs: list[tuple[WorkshopBlankCompositionLine, Decimal]] = []
    total_positive = Decimal('0')
    for line in lines:
        need = _q(line.quantity_kg)
        if need <= 0:
            continue
        needs.append((line, need))
        total_positive += need

    if total_positive <= 0:
        raise WorkshopConflict(
            detail='Нельзя добавить бочку: в составе нет положительных количеств сырья (кг).'
        )

    for line, need in needs:
        avail = material_stock_kg(line.raw_material_id)
        if avail < need:
            raise WorkshopConflict(
                detail=_insufficient_detail(line.raw_material.name, need, avail)
            )

    deduction_rows: list[dict[str, Any]] = []
    for line, need in needs:
        try:
            _cost, created = fifo_deduct(
                line.raw_material_id,
                need,
                reason=WORKSHOP_BARREL_FIFO_REASON,
                reference_id=blank.pk,
            )
        except DRFValidationError as exc:
            logger.warning('FIFO после предпроверки: blank_id=%s material_id=%s %s', blank.pk, line.raw_material_id, exc)
            avail = material_stock_kg(line.raw_material_id)
            raise WorkshopConflict(
                detail=_insufficient_detail(line.raw_material.name, need, avail)
            ) from exc
        deduction_rows.append(
            {
                'raw_material_id': line.raw_material_id,
                'raw_material_name': line.raw_material.name,
                'quantity_kg': str(need),
                'material_stock_deduction_ids': [d.pk for d in created],
            }
        )

    get_or_create_prepared(blank)
    prepared = WorkshopPreparedState.objects.select_for_update().get(blank_id=blank.pk)
    barrels_before = prepared.barrels
    prepared.barrels += 1
    prepared.save(update_fields=['barrels'])
    barrels_after = prepared.barrels

    if user is not None and getattr(user, 'is_authenticated', False):
        schedule_entity_audit(
            user=user,
            request=request,
            section='Цех заготовки',
            description=(
                f'Цех: +1 бочка заготовки #{blank.pk} «{blank.name}» '
                f'(списание сырья FIFO, reason={WORKSHOP_BARREL_FIFO_REASON})'
            ),
            action='update',
            model_cls=WorkshopBlank,
            before={'id': blank.pk, 'name': blank.name, 'prepared_barrels': barrels_before},
            after={'id': blank.pk, 'name': blank.name, 'prepared_barrels': barrels_after},
            after_instance=blank,
            payload_extra={
                'event': 'workshop_prepared_add_barrel',
                'endpoint': f'POST /api/workshop/prepared-blanks/{blank.pk}/add-barrel/',
                'blank_id': blank.pk,
                'fifo_reason': WORKSHOP_BARREL_FIFO_REASON,
                'fifo_reference_id': blank.pk,
                'barrels_before': barrels_before,
                'barrels_after': barrels_after,
                'material_deductions': deduction_rows,
            },
        )

    return WorkshopBlank.objects.prefetch_related('prepared_state').get(pk=blank.pk)
