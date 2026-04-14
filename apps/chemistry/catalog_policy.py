"""Удаление карточки химии и смена единицы (партии, рецепты, факт производства)."""
from __future__ import annotations

from apps.production.models import RecipeRunBatchComponent
from apps.recipes.models import RecipeComponent

from .models import ChemistryCatalog


def chemistry_catalog_deletable(cat: ChemistryCatalog) -> bool:
    if cat.batches.exists():
        return False
    if RecipeComponent.objects.filter(chemistry_id=cat.pk).exists():
        return False
    if RecipeRunBatchComponent.objects.filter(chemistry_id=cat.pk).exists():
        return False
    return True


def chemistry_unit_change_denied(cat: ChemistryCatalog) -> tuple[bool, str]:
    if cat.batches.exists():
        return True, 'Нельзя менять единицу: уже есть партии выпуска химии.'
    return False, ''
