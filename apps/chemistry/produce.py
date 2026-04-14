"""Выпуск химии: списание сырья FIFO → партия ChemistryBatch с фактической себестоимостью."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.materials.fifo import fifo_deduct, material_stock_kg
from apps.materials.serializers import quantity_to_storage_kg

from .models import ChemistryBatch, ChemistryCatalog, ChemistryRecipe

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


RAW_REASON = 'chemistry_batch_produce'


@transaction.atomic
def produce_chemistry(
    *,
    chemistry_id: int,
    quantity: Decimal,
    user: Optional['AbstractUser'] = None,
    comment: str = '',
    source_task_id: int | None = None,
) -> ChemistryBatch:
    """
    Произвести quantity в единицах карточки (kg|g) по ChemistryRecipe.
    Списывает сырьё по FIFO, создаёт ChemistryBatch (количества в кг).
    """
    qty_in = _d(quantity)
    if qty_in <= 0:
        raise ValidationError({'quantity': 'Количество должно быть > 0'})

    cat = ChemistryCatalog.objects.filter(pk=chemistry_id, is_active=True).first()
    if not cat:
        raise ValidationError({'chemistry_id': 'Химия не найдена или неактивна'})

    qty_kg = quantity_to_storage_kg(qty_in, cat.unit)

    lines = list(
        ChemistryRecipe.objects.filter(chemistry_id=chemistry_id).select_related('raw_material')
    )
    if not lines:
        raise ValidationError(
            {
                'code': 'EMPTY_CHEMISTRY_RECIPE',
                'detail': 'Задайте состав химии (рецепт) перед выпуском',
                'error': 'Задайте состав химии (рецепт) перед выпуском',
            }
        )

    missing: list[dict] = []
    for line in lines:
        need = _d(line.quantity_per_unit) * qty_kg
        if need <= 0:
            continue
        avail = material_stock_kg(line.raw_material_id)
        if avail < need:
            missing.append(
                {
                    'component': line.raw_material.name,
                    'required': float(need),
                    'available': float(avail),
                    'unit': line.raw_material.unit,
                }
            )

    if missing:
        raise ValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Недостаточно сырья для выпуска химии',
                'detail': 'Недостаточно сырья для выпуска химии',
                'missing': missing,
            }
        )

    batch = ChemistryBatch.objects.create(
        chemistry=cat,
        quantity_produced=qty_kg,
        quantity_remaining=qty_kg,
        cost_total=Decimal('0'),
        cost_per_unit=Decimal('0'),
        produced_by=user if user is not None and getattr(user, 'is_authenticated', False) else None,
        comment=comment or '',
        source_task_id=source_task_id,
    )

    total_raw = Decimal('0')
    for line in lines:
        need = (_d(line.quantity_per_unit) * qty_kg).quantize(Decimal('0.0001'))
        if need <= 0:
            continue
        cost, _ = fifo_deduct(
            line.raw_material_id,
            need,
            reason=RAW_REASON,
            reference_id=batch.pk,
        )
        total_raw += cost

    batch.cost_total = total_raw.quantize(Decimal('0.01'))
    batch.save(update_fields=['cost_total', 'cost_per_unit', 'quantity_produced', 'quantity_remaining'])

    batch.refresh_from_db()
    return batch
