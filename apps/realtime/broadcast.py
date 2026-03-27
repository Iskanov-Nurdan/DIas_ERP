"""
Рассылка операционных событий в группу WebSocket (без тяжёлых payload — только resource/action/id).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

OPERATIONAL_GROUP = 'operational'

_WS_BROADCAST_DISABLED = os.environ.get('REALTIME_WS_BROADCAST', '1').lower() in ('0', 'false', 'no')


def _iso_ts() -> str:
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
    entity_id: pk сущности, если есть (в JSON уходит как поле "id")
    extra: только лёгкие поля (material_id, line_id, shift_id, …)
    """
    if _WS_BROADCAST_DISABLED:
        return
    layer = get_channel_layer()
    if layer is None:
        return
    payload = {
        'protocol_version': 1,
        'event': 'change',
        'resource': resource,
        'action': action,
        'ts': _iso_ts(),
    }
    if entity_id is not None:
        payload['id'] = entity_id
    if extra:
        payload['payload'] = extra
    async_to_sync(layer.group_send)(
        OPERATIONAL_GROUP,
        {
            'type': 'operational.push',
            'payload': payload,
        },
    )


def schedule_push(**kwargs) -> None:
    """Вызывать из сигналов внутри on_commit."""
    from django.db import transaction

    transaction.on_commit(lambda: push_operational_event(**kwargs))
