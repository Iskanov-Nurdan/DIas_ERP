"""
FIFO списание партий химии (ChemistryBatch).
Порядок: created_at ASC, id ASC (как в ТЗ).
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from django.db import transaction
from django.db.models import F, Sum

from rest_framework.exceptions import ValidationError

from .models import ChemistryBatch, ChemistryStockDeduction, ChemistryCatalog


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def chemistry_stock_kg(chemistry_id: int) -> Decimal:
    s = (
        ChemistryBatch.objects.filter(chemistry_id=chemistry_id).aggregate(s=Sum('quantity_remaining'))['s']
    )
    return _d(s)


@transaction.atomic
def fifo_deduct_chemistry(
    chemistry_id: int,
    quantity_kg: Decimal,
    *,
    reason: str,
    reference_id: int | None,
) -> Tuple[Decimal, List[ChemistryStockDeduction]]:
    """Списать quantity_kг химии по FIFO. Возвращает (стоимость, строки списания)."""
    q_need = _d(quantity_kg)
    if q_need <= 0:
        return Decimal('0'), []

    ChemistryCatalog.objects.select_for_update().filter(pk=chemistry_id).first()

    avail = _d(
        ChemistryBatch.objects.filter(chemistry_id=chemistry_id).aggregate(s=Sum('quantity_remaining'))['s']
    )
    if avail < q_need:
        raise ValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Недостаточно остатков химии',
                'detail': 'Недостаточно остатков химии',
                'chemistry_id': chemistry_id,
                'required': float(q_need),
                'available': float(avail),
            }
        )

    total_cost = Decimal('0')
    created: List[ChemistryStockDeduction] = []
    remaining = q_need

    while remaining > 0:
        b = (
            ChemistryBatch.objects.select_for_update()
            .filter(chemistry_id=chemistry_id, quantity_remaining__gt=0)
            .order_by('created_at', 'id')
            .first()
        )
        if b is None:
            raise ValidationError(
                {
                    'code': 'INSUFFICIENT_STOCK',
                    'error': 'Недостаточно остатков химии',
                    'detail': 'Недостаточно остатков химии',
                }
            )
        br = _d(b.quantity_remaining)
        take = min(br, remaining)
        up = _d(b.cost_per_unit)
        line_total = (take * up).quantize(Decimal('0.01'))
        ded = ChemistryStockDeduction.objects.create(
            batch=b,
            quantity=take,
            unit_price=up,
            line_total=line_total,
            reason=reason or '',
            reference_id=reference_id,
        )
        ChemistryBatch.objects.filter(pk=b.pk).update(
            quantity_remaining=F('quantity_remaining') - take
        )
        total_cost += line_total
        created.append(ded)
        remaining -= take

    return total_cost.quantize(Decimal('0.01')), created


def simulate_chemistry_fifo_cost_kg(chemistry_id: int, quantity_kg: Decimal) -> Decimal:
    """Плановая стоимость quantity_kг химии по текущим партиям (без списания)."""
    need = _d(quantity_kg)
    if need <= 0:
        return Decimal('0')
    total = Decimal('0')
    for b in (
        ChemistryBatch.objects.filter(chemistry_id=chemistry_id, quantity_remaining__gt=0)
        .order_by('created_at', 'id')
        .iterator()
    ):
        if need <= 0:
            break
        br = _d(b.quantity_remaining)
        if br <= 0:
            continue
        take = min(br, need)
        up = _d(b.cost_per_unit)
        total += (take * up).quantize(Decimal('0.01'))
        need -= take
    return total.quantize(Decimal('0.01'))


@transaction.atomic
def reverse_chemistry_deductions(reason: str, reference_id: int | None) -> None:
    qs = ChemistryStockDeduction.objects.filter(reason=reason, reference_id=reference_id).order_by('-id')
    for ded in qs:
        ChemistryBatch.objects.select_for_update().filter(pk=ded.batch_id).update(
            quantity_remaining=F('quantity_remaining') + ded.quantity
        )
    qs.delete()
