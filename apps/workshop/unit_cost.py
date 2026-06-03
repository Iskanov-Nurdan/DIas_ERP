"""Себестоимость 1 шт ГП по цеховой приёмке (FIFO сырья по составу заготовки)."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from apps.materials.fifo import simulate_fifo_cost_kg

from .models import BlankProductionRun, WorkshopBlank, WorkshopBlankCompositionLine

DEC = Decimal('0.0001')


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def workshop_blank_cost_per_kg(blank: WorkshopBlank) -> Optional[Decimal]:
    """Себестоимость 1 кг заготовки по FIFO состава на одну бочку."""
    barrel_kg = _d(blank.recipe_kg_per_barrel)
    if barrel_kg <= 0:
        return None
    lines = WorkshopBlankCompositionLine.objects.filter(blank_id=blank.pk)
    if not lines.exists():
        return None
    total_cost = Decimal('0')
    for line in lines:
        qty = _d(line.quantity_kg)
        if qty > 0 and line.raw_material_id:
            total_cost += simulate_fifo_cost_kg(line.raw_material_id, qty)
    if total_cost <= 0:
        return None
    return (total_cost / barrel_kg).quantize(DEC)


def profile_cost_price_from_blank(*, profile, blank: WorkshopBlank) -> Optional[Decimal]:
    """cost_price = blank_cost_per_kg × weight_kg_per_piece."""
    cpk = workshop_blank_cost_per_kg(blank)
    w = profile.weight_kg_per_piece
    if cpk is None or w is None:
        return None
    w_dec = _d(w)
    if w_dec <= 0:
        return None
    return (cpk * w_dec).quantize(DEC)


def workshop_run_unit_cost_per_piece(run: BlankProductionRun) -> Optional[Decimal]:
    """Материальная себестоимость 1 принятой штуки по BlankProductionRun."""
    if run.production_batch_id:
        pb = run.production_batch
        if pb is not None:
            cpp = _d(pb.cost_per_piece)
            if cpp > 0:
                return cpp.quantize(DEC)

    pieces = int(run.gp_accepted_pieces or 0)
    if pieces <= 0:
        return None

    blank = run.blank
    barrel_kg = _d(blank.recipe_kg_per_barrel)
    used_kg = _d(run.blank_used_in_production_kg)
    if barrel_kg <= 0 or used_kg <= 0:
        return None

    factor = used_kg / barrel_kg
    lines = WorkshopBlankCompositionLine.objects.filter(blank_id=blank.pk)
    if not lines.exists():
        return None

    total_cost = Decimal('0')
    for line in lines:
        need_kg = (_d(line.quantity_kg) * factor).quantize(Decimal('0.000001'))
        if need_kg > 0 and line.raw_material_id:
            total_cost += simulate_fifo_cost_kg(line.raw_material_id, need_kg)
    if total_cost <= 0:
        return None
    return (total_cost / Decimal(pieces)).quantize(DEC)


def workshop_profile_unit_cost_per_piece(profile_id: int) -> Optional[Decimal]:
    """Последняя приёмка ГП по профилю с рассчитанной себестоимостью."""
    run = (
        BlankProductionRun.objects.filter(
            product_id=profile_id,
            status=BlankProductionRun.STATUS_GP_ACCEPTED,
            gp_accepted_pieces__gt=0,
        )
        .select_related('blank', 'production_batch')
        .order_by('-gp_accepted_at', '-id')
        .first()
    )
    if run is None:
        return None
    return workshop_run_unit_cost_per_piece(run)


def infer_profile_length_meters(profile_name: str) -> Decimal:
    """Длина профиля в метрах из названия («… 6м») или 6 м по умолчанию."""
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*м', (profile_name or ''), re.IGNORECASE)
    if m:
        return Decimal(m.group(1).replace(',', '.'))
    return Decimal('6')


def recipe_estimated_unit_cost_per_piece(profile_id: int) -> Optional[Decimal]:
    """Плановая себестоимость 1 шт по активному рецепту профиля (FIFO норм на 1 м × длина)."""
    from apps.production.costing import estimate_recipe_material_cost
    from apps.recipes.models import PlasticProfile, Recipe

    recipe = (
        Recipe.objects.filter(profile_id=profile_id, is_active=True)
        .prefetch_related('components')
        .order_by('id')
        .first()
    )
    if recipe is None or not recipe.components.exists():
        return None
    prof = PlasticProfile.objects.filter(pk=profile_id).only('name').first()
    length = infer_profile_length_meters(prof.name if prof else '')
    cost = estimate_recipe_material_cost(recipe, length)
    if cost <= 0:
        return None
    return cost.quantize(DEC)
