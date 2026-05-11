"""Удаление профиля: без рецептов и без партий производства."""
from __future__ import annotations

from apps.production.models import ProductionBatch

from .models import PlasticProfile


def plastic_profile_deletable(p: PlasticProfile) -> bool:
    if p.recipes.exists():
        return False
    if ProductionBatch.objects.filter(profile_id=p.pk).exists():
        return False
    return True
