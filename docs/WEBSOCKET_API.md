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

- `apps/realtime/consumers.py` — consumer (`connected`, `change`, ping/pong, idle timeout 60 с)
- `apps/realtime/middleware.py` — JWT из `?token=` / `?access=`, close `4001`
- `apps/realtime/broadcast.py` — `push_operational_event` / `schedule_push` (on_commit)
- `apps/realtime/signals.py` — post_save/post_delete → broadcast
- `apps/realtime/access.py` — resource → access keys (фильтр по UserAccess)
- `config/asgi.py` — ASGI + OriginValidator

### Запуск

```bash
python manage.py runserver          # dev (Daphne/ASGI)
# prod: daphne config.asgi:application + REDIS_URL для multi-worker
```

### Resource (фактический каталог)

Смены: `shift`, `shift_note`, `shift_complaint`, `activity`  
Сырьё: `raw_material`, `incoming`, `material_balance`, `material_writeoff`, `material_movement`  
Цех: `workshop_blank`, `prepared_blank`, `blank_production_run`, `workshop_run`, `plastic_profile`  
Производство: `order`, `orders`, `production_batch`, `batch`, `recipe_run`  
Склад: `warehouse_batch`, `warehouse_package`  
Касса: `sale`, `payment`, `return`, `client`  
Прочее: `recipe`, `recipes`, `line`, `line_history`, `defect_record`, `rework_request`, `chemistry*`, `other_expense`

Полный маппинг REST → resource: signals + `apps/realtime/access.py`.
