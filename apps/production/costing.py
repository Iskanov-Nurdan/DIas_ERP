"""Оценка материальной себестоимости: сырьё и химия по FIFO-симуляции текущих партий."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from apps.chemistry.fifo import chemistry_stock_kg, simulate_chemistry_fifo_cost_kg
from apps.chemistry.models import ChemistryRecipe
from apps.materials.fifo import simulate_fifo_cost_kg

if TYPE_CHECKING:
    from apps.recipes.models import Recipe


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def chemistry_estimated_kg_price(chemistry_id: int) -> Decimal:
    """Оценка ₽ за 1 кг: FIFO по партиям при остатке ≥ 1 кг; иначе теория по составу и FIFO сырья."""
    if chemistry_stock_kg(chemistry_id) >= Decimal('1'):
        return simulate_chemistry_fifo_cost_kg(chemistry_id, Decimal('1'))
    total = Decimal('0')
    for row in ChemistryRecipe.objects.filter(chemistry_id=chemistry_id).select_related('raw_material'):
        q = _d(row.quantity_per_unit)
        mid = row.raw_material_id
        total += simulate_fifo_cost_kg(mid, q)
    return total.quantize(Decimal('0.0001'))


def estimate_recipe_material_cost(recipe: 'Recipe', total_meters: Decimal) -> Decimal:
    """Плановая материальная себестоимость выпуска total_meters м (нормы на 1 м)."""
    from apps.recipes.models import RecipeComponent

    tm = _d(total_meters)
    if tm <= 0:
        return Decimal('0')
    chem_prices: dict[int, Decimal] = {}
    total = Decimal('0')
    for comp in recipe.components.all():
        qpm = _d(comp.quantity_per_meter)
        need_kg = (qpm * tm).quantize(Decimal('0.0001'))
        if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
            total += simulate_fifo_cost_kg(comp.raw_material_id, need_kg)
        elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
            cid = comp.chemistry_id
            if cid not in chem_prices:
                chem_prices[cid] = chemistry_estimated_kg_price(cid)
            total += need_kg * chem_prices[cid]
    return total.quantize(Decimal('0.01'))


def estimate_chemistry_only_recipe_cost(recipe: 'Recipe', total_meters: Decimal) -> Decimal:
    """Только компоненты «химия» по рецепту (для совместимости; факт в batch_stock — FIFO)."""
    from apps.recipes.models import RecipeComponent

    tm = _d(total_meters)
    if tm <= 0:
        return Decimal('0')
    chem_prices: dict[int, Decimal] = {}
    total = Decimal('0')
    for comp in recipe.components.all():
        qpm = _d(comp.quantity_per_meter)
        need_kg = (qpm * tm).quantize(Decimal('0.0001'))
        if comp.type != RecipeComponent.TYPE_CHEM or not comp.chemistry_id:
            continue
        cid = comp.chemistry_id
        if cid not in chem_prices:
            chem_prices[cid] = chemistry_estimated_kg_price(cid)
        total += need_kg * chem_prices[cid]
    return total.quantize(Decimal('0.01'))
