"""Удаление профиля: без рецептов и без партий производства."""
from __future__ import annotations

import secrets

from apps.production.models import ProductionBatch

from .models import PlasticProfile


def plastic_profile_deletable(p: PlasticProfile) -> bool:
    if p.recipes.exists():
        return False
    if ProductionBatch.objects.filter(profile_id=p.pk).exists():
        return False
    return True


def generate_plastic_profile_code() -> str:
    """Уникальный код для GP, если клиент не передал code (до 100 символов, уникальный constraint)."""
    for _ in range(64):
        cand = f'GP-{secrets.token_hex(8).upper()}'
        if not PlasticProfile.objects.filter(code=cand).exists():
            return cand[:100]
    import uuid

    return f'GP-{uuid.uuid4().hex}'[:100]
