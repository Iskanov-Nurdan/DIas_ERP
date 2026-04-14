"""Списание сырья и химии по партии производства (нормы рецепта на 1 м × total_meters)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, Optional, Tuple

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.materials.fifo import fifo_deduct, material_stock_kg, reverse_stock_deductions
from apps.materials.models import RawMaterial
from apps.chemistry.fifo import fifo_deduct_chemistry, chemistry_stock_kg, reverse_chemistry_deductions
from apps.chemistry.models import ChemistryCatalog
from apps.recipes.models import Recipe, RecipeComponent
from apps.production.models import ProductionBatch

PRODUCTION_BATCH_REASON = 'production_batch'


def _q(d) -> Decimal:
    if d is None:
        return Decimal('0')
    return Decimal(str(d))


def aggregate_consumption_for_recipe(recipe: Recipe, total_meters: Decimal) -> Tuple[Dict[int, Decimal], Dict[int, Decimal]]:
    raw: Dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    chem: Dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    tm = _q(total_meters)
    if tm <= 0:
        return {}, {}
    for comp in recipe.components.all():
        per = _q(comp.quantity_per_meter)
        need = (per * tm).quantize(Decimal('0.0001'))
        if need <= 0:
            continue
        if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
            raw[comp.raw_material_id] += need
        elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
            chem[comp.chemistry_id] += need
    return dict(raw), dict(chem)


def raw_material_available(material_id: int) -> Decimal:
    return material_stock_kg(material_id)


@transaction.atomic
def reverse_production_batch_stock(
    *,
    batch_id: int,
    recipe: Optional[Recipe],
    total_meters: Decimal,
) -> None:
    ProductionBatch.objects.select_for_update().filter(pk=batch_id).first()
    reverse_stock_deductions(PRODUCTION_BATCH_REASON, batch_id)
    reverse_chemistry_deductions(PRODUCTION_BATCH_REASON, batch_id)
    if recipe is None:
        return
    # Откат химии уже через reverse_chemistry_deductions; сырьё — через reverse_stock_deductions.


@transaction.atomic
def apply_production_batch_stock_and_cost(batch: ProductionBatch) -> None:
    if not batch.recipe_id:
        raise ValidationError({'recipe_id': 'У партии должен быть рецепт'})
    recipe = Recipe.objects.prefetch_related('components').get(pk=batch.recipe_id)
    if not recipe.components.exists():
        raise ValidationError(
            {'code': 'INVALID_RECIPE', 'detail': 'Рецепт без компонентов', 'error': 'Рецепт без компонентов'},
        )
    tm = _q(batch.total_meters)
    raw_agg, chem_agg = aggregate_consumption_for_recipe(recipe, tm)
    missing: list[dict] = []

    for mid, req in raw_agg.items():
        if req <= 0:
            continue
        avail = raw_material_available(mid)
        if avail < req:
            name = RawMaterial.objects.filter(pk=mid).values_list('name', flat=True).first() or f'id={mid}'
            unit = RawMaterial.objects.filter(pk=mid).values_list('unit', flat=True).first() or 'kg'
            missing.append({
                'component': name,
                'required': float(req),
                'available': float(avail),
                'unit': unit,
            })

    for cid, req in chem_agg.items():
        if req <= 0:
            continue
        cat = ChemistryCatalog.objects.filter(pk=cid).only('id', 'name', 'unit').first()
        if not cat:
            missing.append({
                'component': f'Химия id={cid}',
                'required': float(req),
                'available': 0.0,
                'unit': '—',
            })
            continue
        avail = chemistry_stock_kg(cid)
        if avail < req:
            missing.append({
                'component': cat.name,
                'required': float(req),
                'available': float(avail),
                'unit': cat.unit or 'kg',
            })

    if missing:
        raise ValidationError({
            'code': 'INSUFFICIENT_STOCK',
            'error': 'Недостаточно остатков для партии',
            'detail': 'Недостаточно остатков для партии',
            'missing': missing,
        })

    raw_cost = Decimal('0')
    for mid, req in raw_agg.items():
        if req <= 0:
            continue
        cost, _ = fifo_deduct(mid, req, reason=PRODUCTION_BATCH_REASON, reference_id=batch.pk)
        raw_cost += cost

    chem_cost = Decimal('0')
    for cid, req in chem_agg.items():
        if req <= 0:
            continue
        cost, _ = fifo_deduct_chemistry(cid, req, reason=PRODUCTION_BATCH_REASON, reference_id=batch.pk)
        chem_cost += cost

    batch.material_cost_total = raw_cost + chem_cost
    batch.save(
        update_fields=[
            'material_cost_total', 'total_meters', 'quantity', 'cost_per_meter', 'cost_per_piece', 'cost_price',
        ],
    )
