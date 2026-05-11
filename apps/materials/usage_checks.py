"""Правила удаления сырья и смены единицы (рецепты, химия, партии, движения)."""
from __future__ import annotations

from apps.chemistry.models import ChemistryRecipe
from apps.production.models import RecipeRunBatchComponent
from apps.recipes.models import RecipeComponent

from .models import MaterialBatch, MaterialStockDeduction, RawMaterial


def raw_material_unit_change_denial(m: RawMaterial) -> tuple[bool, str]:
    """(True, сообщение) если менять unit нельзя."""
    if MaterialBatch.objects.filter(material_id=m.pk).exists():
        return True, 'Нельзя менять единицу: у сырья уже есть приходы.'
    if MaterialStockDeduction.objects.filter(batch__material_id=m.pk).exists():
        return True, 'Нельзя менять единицу: есть движения по сырью.'
    if RecipeComponent.objects.filter(raw_material_id=m.pk).exists():
        return True, 'Нельзя менять единицу: сырьё используется в рецептах.'
    if ChemistryRecipe.objects.filter(raw_material_id=m.pk).exists():
        return True, 'Нельзя менять единицу: сырьё используется в составе химии.'
    if RecipeRunBatchComponent.objects.filter(raw_material_id=m.pk).exists():
        return True, 'Нельзя менять единицу: сырьё участвует в производстве.'
    return False, ''


def raw_material_is_deletable(m: RawMaterial) -> bool:
    denied, _ = raw_material_unit_change_denial(m)
    return not denied
