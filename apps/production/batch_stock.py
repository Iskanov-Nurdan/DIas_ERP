"""Списание сырья и химии по партии производства (нормы рецепта на 1 м × total_meters)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, Optional, Tuple

from django.db import transaction
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.materials.fifo import fifo_deduct, material_stock_kg, reverse_stock_deductions
from apps.materials.models import MaterialStockDeduction, RawMaterial
from apps.chemistry.fifo import fifo_deduct_chemistry, chemistry_stock_kg, reverse_chemistry_deductions
from apps.chemistry.models import ChemistryCatalog, ChemistryStockDeduction
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
        raise DRFValidationError({'recipe_id': 'У партии должен быть рецепт'})
    recipe = Recipe.objects.prefetch_related('components').get(pk=batch.recipe_id)
    if not recipe.components.exists():
        raise DRFValidationError(
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
                'kind': 'raw_material',
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
                'kind': 'chemistry',
                'component': f'Химия id={cid}',
                'required': float(req),
                'available': 0.0,
                'unit': '—',
            })
            continue
        avail = chemistry_stock_kg(cid)
        if avail < req:
            missing.append({
                'kind': 'chemistry',
                'component': cat.name,
                'required': float(req),
                'available': float(avail),
                'unit': cat.unit or 'kg',
            })

    if missing:
        err_list = [
            {
                'field': m.get('kind', 'stock'),
                'message': (
                    f"{m.get('component', '?')}: нужно {m.get('required')}, доступно {m.get('available')} ({m.get('unit', 'kg')})"
                ),
            }
            for m in missing
        ]
        raise DRFValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Недостаточно остатков для партии',
                'detail': 'Недостаточно остатков для партии',
                'missing': missing,
                'errors': err_list,
            }
        )

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


def production_batch_has_positive_material_requirement(batch: ProductionBatch) -> bool:
    """Нужен ли ненулевой расход сырья/химии по рецепту и total_meters партии."""
    if not batch.recipe_id:
        return False
    tm = _q(batch.total_meters)
    if tm <= 0:
        return False
    try:
        recipe = Recipe.objects.prefetch_related('components').get(pk=batch.recipe_id)
    except Recipe.DoesNotExist:
        return False
    raw_agg, chem_agg = aggregate_consumption_for_recipe(recipe, tm)
    return any(q > 0 for q in raw_agg.values()) or any(q > 0 for q in chem_agg.values())


def assert_production_batch_ready_for_otk_pipeline(batch: ProductionBatch) -> None:
    """
    Партия должна иметь рецепт с компонентами, положительный выпуск и (при ненулевой норме расхода)
    ненулевую material_cost_total после apply_production_batch_stock_and_cost.
    """
    if not batch.recipe_id:
        raise DRFValidationError(
            {'code': 'BATCH_INCOMPLETE', 'detail': 'У партии нет рецепта', 'error': 'У партии нет рецепта'},
        )
    tm = _q(batch.total_meters)
    if tm <= 0:
        raise DRFValidationError(
            {
                'code': 'BATCH_INCOMPLETE',
                'detail': 'Выпуск партии (total_meters) должен быть > 0',
                'error': 'Выпуск партии (total_meters) должен быть > 0',
            },
        )
    try:
        recipe = Recipe.objects.prefetch_related('components').get(pk=batch.recipe_id)
    except Recipe.DoesNotExist:
        raise DRFValidationError(
            {
                'code': 'BATCH_INCOMPLETE',
                'detail': 'Рецепт партии удалён из справочника — ОТК недоступен',
                'error': 'Рецепт партии удалён из справочника — ОТК недоступен',
            },
        )
    if not recipe.components.exists():
        raise DRFValidationError(
            {
                'code': 'INVALID_RECIPE',
                'detail': 'Рецепт без компонентов — партия не может идти в ОТК',
                'error': 'Рецепт без компонентов — партия не может идти в ОТК',
            },
        )
    if production_batch_has_positive_material_requirement(batch):
        cost = batch.material_cost_total or Decimal('0')
        has_raw_mov = MaterialStockDeduction.objects.filter(
            reason=PRODUCTION_BATCH_REASON, reference_id=batch.pk
        ).exists()
        has_chem_mov = ChemistryStockDeduction.objects.filter(
            reason=PRODUCTION_BATCH_REASON, reference_id=batch.pk
        ).exists()
        if cost <= 0 and not has_raw_mov and not has_chem_mov:
            raise DRFValidationError(
                {
                    'code': 'INCOMPLETE_PRODUCTION_COST',
                    'detail': (
                        'Партия не прошла расчёт материального расхода: нет списаний сырья/химии '
                        'и нулевая material_cost_total при ненулевой норме рецепта.'
                    ),
                    'error': 'Нет списания / себестоимости производства для партии с расходом по рецепту',
                },
            )


@transaction.atomic
def resync_production_batch_consumption(
    batch: ProductionBatch,
    *,
    previous_recipe: Optional[Recipe],
    previous_total_meters: Decimal,
) -> None:
    """Откат списаний по старым метрам/рецепту и повторное FIFO-списание по текущему состоянию партии."""
    reverse_production_batch_stock(
        batch_id=batch.pk,
        recipe=previous_recipe,
        total_meters=previous_total_meters,
    )
    apply_production_batch_stock_and_cost(batch)
