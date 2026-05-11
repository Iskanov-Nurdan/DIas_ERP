"""Минимальный аудит критических действий коммерции (UserActivity)."""
from __future__ import annotations

from typing import Any, Optional, Type

from django.db import models


def log_commercial_audit(
    *,
    user,
    request,
    section: str,
    description: str,
    model_cls: Type[models.Model],
    instance: Optional[models.Model] = None,
    payload_extra: Optional[dict[str, Any]] = None,
) -> None:
    if user is None or not getattr(user, 'is_authenticated', False):
        return
    try:
        from apps.activity.audit_service import schedule_entity_audit, instance_to_snapshot

        after = instance_to_snapshot(instance) if instance is not None else None
        schedule_entity_audit(
            user=user,
            request=request,
            section=section,
            description=description,
            action='update',
            model_cls=model_cls,
            after=after,
            after_instance=instance,
            payload_extra=payload_extra or {},
        )
    except Exception:
        pass
