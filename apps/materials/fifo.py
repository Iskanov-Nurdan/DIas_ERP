"""
FIFO списание партий сырья (MaterialBatch) и откат по reason/reference_id.
Количества в партиях хранятся в кг.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from django.db import transaction
from django.db.models import F, Sum

from rest_framework.exceptions import ValidationError

from .models import MaterialBatch, MaterialStockDeduction, RawMaterial


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def material_stock_kg(material_id: int) -> Decimal:
    s = (
        MaterialBatch.objects.filter(material_id=material_id).aggregate(
            s=Sum('quantity_remaining')
        )['s']
    )
    return _d(s)


@transaction.atomic
def fifo_deduct(
    material_id: int,
    quantity_kg: Decimal,
    *,
    reason: str,
    reference_id: int | None,
) -> Tuple[Decimal, List[MaterialStockDeduction]]:
    """
    Списать quantity_kг сырья по FIFO (created_at, id).
    Возвращает (суммарная стоимость, созданные строки списания по партиям).
    """
    q_need = _d(quantity_kg)
    if q_need <= 0:
        return Decimal('0'), []

    RawMaterial.objects.select_for_update().filter(pk=material_id).first()

    total_cost = Decimal('0')
    created: List[MaterialStockDeduction] = []
    remaining = q_need

    avail = _d(
        MaterialBatch.objects.filter(material_id=material_id).aggregate(
            s=Sum('quantity_remaining')
        )['s']
    )
    if avail < remaining:
        raise ValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Недостаточно остатков сырья',
                'detail': 'Недостаточно остатков сырья',
                'material_id': material_id,
                'required': float(remaining),
                'available': float(avail),
            }
        )

    while remaining > 0:
        b = (
            MaterialBatch.objects.select_for_update()
            .filter(material_id=material_id, quantity_remaining__gt=0)
            .order_by('received_at', 'created_at', 'id')
            .first()
        )
        if b is None:
            raise ValidationError(
                {
                    'code': 'INSUFFICIENT_STOCK',
                    'error': 'Недостаточно остатков сырья',
                    'detail': 'Недостаточно остатков сырья',
                }
            )
        br = _d(b.quantity_remaining)
        take = min(br, remaining)
        up = _d(b.unit_price)
        line_total = (take * up).quantize(Decimal('0.01'))
        ded = MaterialStockDeduction.objects.create(
            batch=b,
            quantity=take,
            unit_price=up,
            line_total=line_total,
            reason=reason or '',
            reference_id=reference_id,
        )
        MaterialBatch.objects.filter(pk=b.pk).update(
            quantity_remaining=F('quantity_remaining') - take
        )
        total_cost += line_total
        created.append(ded)
        remaining -= take

    return total_cost.quantize(Decimal('0.01')), created


def simulate_fifo_cost_kg(material_id: int, quantity_kg: Decimal) -> Decimal:
    """Оценка стоимости quantity_kг без изменения остатков (для плановых расчётов)."""
    need = _d(quantity_kg)
    if need <= 0:
        return Decimal('0')
    total = Decimal('0')
    for b in (
        MaterialBatch.objects.filter(material_id=material_id, quantity_remaining__gt=0)
        .order_by('received_at', 'created_at', 'id')
        .iterator()
    ):
        if need <= 0:
            break
        br = _d(b.quantity_remaining)
        if br <= 0:
            continue
        take = min(br, need)
        up = _d(b.unit_price)
        total += (take * up).quantize(Decimal('0.01'))
        need -= take
    return total.quantize(Decimal('0.01'))


@transaction.atomic
def reverse_stock_deductions(reason: str, reference_id: int | None) -> None:
    """Вернуть остатки по партиям и удалить строки списания с данным reason/reference_id."""
    qs = (
        MaterialStockDeduction.objects.select_related('batch')
        .filter(reason=reason, reference_id=reference_id)
        .order_by('-id')
    )
    for ded in qs:
        MaterialBatch.objects.select_for_update().filter(pk=ded.batch_id).update(
            quantity_remaining=F('quantity_remaining') + ded.quantity
        )
    qs.delete()
