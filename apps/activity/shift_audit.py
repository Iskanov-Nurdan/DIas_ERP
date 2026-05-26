"""
Политика журнала «Действия за смену»: whitelist entity_type и привязка shift_id.

Запись: только типы из AUDIT_SHIFT_ENTITY_TYPES получают shift_id / line_id / session_open_event_id.
Чтение (?shift_id=): только whitelist; привязка shift_id ИЛИ (legacy) интервал смены без shift_id.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone


# Формат: app_label.model_name (как entity_type_for_model в audit_service).
DEFAULT_AUDIT_SHIFT_ENTITY_TYPES: Tuple[str, ...] = (
    # Сырьё
    'materials.rawmaterial',
    'materials.materialbatch',
    # Химия
    'chemistry.chemistrycatalog',
    'chemistry.chemistrytask',
    'chemistry.chemistrybatch',
    # Заготовка / цех
    'workshop.workshopblank',
    'workshop.blankproductionrun',
    # Производство и линии
    'production.line',
    'production.linehistory',
    'production.order',
    'production.productionbatch',
    'production.reciperun',
    # Склад
    'warehouse.warehousebatch',
    # Касса / коммерция
    'sales.client',
    'sales.order',
    'sales.orderline',
    'sales.orderreservation',
    'sales.sale',
    'sales.saleline',
    'sales.payment',
    'sales.return',
    'sales.defectrecord',
    'sales.reworkrequest',
    # Смена
    'production.shift',
    'production.shiftnote',
    'production.shiftcomplaint',
)


@lru_cache(maxsize=1)
def get_audit_shift_entity_types() -> FrozenSet[str]:
    raw_env = os.environ.get('AUDIT_SHIFT_ENTITY_TYPES', '').strip()
    if raw_env:
        return frozenset(x.strip() for x in raw_env.split(',') if x.strip())
    configured = getattr(settings, 'AUDIT_SHIFT_ENTITY_TYPES', None)
    if configured:
        return frozenset(configured)
    return frozenset(DEFAULT_AUDIT_SHIFT_ENTITY_TYPES)


def is_shift_audited_entity_type(entity_type: str) -> bool:
    if not entity_type:
        return False
    return entity_type in get_audit_shift_entity_types()


def apply_shift_context_policy(
    entity_type: str,
    shift_id: Optional[int],
    line_id: Optional[int],
    session_open_event_id: Optional[int],
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Сбрасывает привязку к смене для типов вне whitelist (запись в UserActivity)."""
    if is_shift_audited_entity_type(entity_type):
        return shift_id, line_id, session_open_event_id
    return None, None, None


def filter_activities_for_shift(qs: QuerySet, shift) -> QuerySet:
    """
    Фильтр для GET ?shift_id=: только операционные типы;
    shift_id=смена ИЛИ legacy (без shift_id, но в opened_at…closed_at).
    """
    types = get_audit_shift_entity_types()
    qs = qs.filter(entity_type__in=types)
    end = shift.closed_at or timezone.now()
    return qs.filter(
        Q(shift_id=shift.pk)
        | (
            Q(shift_id__isnull=True)
            & Q(created_at__gte=shift.opened_at)
            & Q(created_at__lte=end)
        )
    )
