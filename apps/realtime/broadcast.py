"""
Рассылка операционных событий в группу WebSocket (без тяжёлых payload — только resource/action/id).
Контракт: Dias_Front/docs/WEBSOCKET_API.md
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

OPERATIONAL_GROUP = 'operational'
OPERATIONAL_WS_PROTOCOL_VERSION = 1

_WS_BROADCAST_DISABLED = os.environ.get('REALTIME_WS_BROADCAST', '1').lower() in ('0', 'false', 'no')

_RESERVED_KEYS = frozenset({'event', 'protocol_version', 'resource', 'action', 'id', 'at'})


def _iso_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def push_operational_event(
    *,
    resource: str,
    action: str,
    entity_id: Optional[int | str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """
    resource: логическое имя сущности (см. docs/WEBSOCKET_API.md).
    action: created | updated | deleted | changed
    entity_id: pk сущности (в JSON — поле id)
    extra: лёгкие поля верхнего уровня (line_id, shift_id, …)
    """
    if _WS_BROADCAST_DISABLED:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    payload: dict[str, Any] = {
        'protocol_version': OPERATIONAL_WS_PROTOCOL_VERSION,
        'event': 'change',
        'resource': resource,
        'action': action,
        'at': _iso_at(),
    }
    if entity_id is not None:
        payload['id'] = entity_id
    if extra:
        for key, val in extra.items():
            if key not in _RESERVED_KEYS:
                payload[key] = val
    async_to_sync(layer.group_send)(
        OPERATIONAL_GROUP,
        {
            'type': 'operational.push',
            'payload': payload,
        },
    )


# Алиас из документации бэкенда
broadcast_operational = push_operational_event


def schedule_push(**kwargs) -> None:
    """Вызывать после мутации внутри transaction.atomic — сработает on_commit."""
    from django.db import transaction

    transaction.on_commit(lambda: push_operational_event(**kwargs))


def schedule_order_operational_push(
    *,
    action: str,
    entity_id: int | None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Заявка: фронт подписан на order и orders."""
    from django.db import transaction

    def _fanout() -> None:
        push_operational_event(resource='order', action=action, entity_id=entity_id, extra=extra)
        push_operational_event(resource='orders', action=action, entity_id=entity_id, extra=extra)

    transaction.on_commit(_fanout)


def schedule_otk_push(
    *,
    action: str,
    entity_id: int | None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """События домена ОТК (пул, учёт)."""
    from django.db import transaction

    def _fanout() -> None:
        push_operational_event(resource='otk', action=action, entity_id=entity_id, extra=extra)

    transaction.on_commit(_fanout)


def schedule_blank_run_push(
    *,
    action: str,
    entity_id: int | None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """blank_production_run + алиас workshop_run (фронт слушает оба)."""
    from django.db import transaction

    def _fanout() -> None:
        push_operational_event(
            resource='blank_production_run', action=action, entity_id=entity_id, extra=extra
        )
        push_operational_event(resource='workshop_run', action=action, entity_id=entity_id, extra=extra)

    transaction.on_commit(_fanout)
