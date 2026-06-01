# Operational WebSocket (контракт фронта)

Синхронизировано с `Dias_Front/docs/WEBSOCKET_API.md`.

## Подключение

- **URL:** `wss://{host}/ws/operational/?token={access_jwt}`
- **Auth:** access JWT в query `token` (или `access`)
- **Отказ:** close code `4001` — невалидный/просроченный токен

## Версия протокола

`protocol_version: 1` в каждом JSON-кадре.

## События

### `connected` (сервер → клиент)

```json
{
  "event": "connected",
  "protocol_version": 1,
  "user_id": 12
}
```

### `change` (сервер → клиент)

```json
{
  "event": "change",
  "protocol_version": 1,
  "resource": "workshop_blank",
  "action": "updated",
  "id": 45,
  "at": "2026-05-29T12:00:00Z"
}
```

### Heartbeat

Сервер каждые 30 с: `{"event":"ping","protocol_version":1}`. Клиент может ответить `{"event":"pong","protocol_version":1}`.

## Реализация на бэке

- `apps/realtime/consumers.py` — consumer
- `apps/realtime/broadcast.py` — `push_operational_event` / `schedule_push`
- `apps/realtime/signals.py` — post_save/post_delete → on_commit
- Полный маппинг REST → resource: `Dias_Front/docs/WEBSOCKET_BACKEND_PROMPT.md`
