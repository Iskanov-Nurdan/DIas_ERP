"""Удаление рецепта: только без производственных ссылок."""
from __future__ import annotations

from apps.production.models import Order, ProductionBatch, RecipeRun

from .models import Recipe


def recipe_deletable(r: Recipe) -> bool:
    if RecipeRun.objects.filter(recipe_id=r.pk).exists():
        return False
    if Order.objects.filter(recipe_id=r.pk).exists():
        return False
    if ProductionBatch.objects.filter(recipe_id=r.pk).exists():
        return False
    return True
