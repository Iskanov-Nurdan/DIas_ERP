# DIAS WebSocket API (realtime)

## 1) Endpoint

WebSocket URL:

- **`/ws/operational/`** (также принимается без завершающего `/`)

Сервер: Django Channels (`config/asgi.py`), consumer: `apps.realtime.consumers.OperationalConsumer`.

## 2) Аутентификация

Требуется авторизованный пользователь (JWT).  
Если пользователь анонимный — соединение закрывается с кодом **`4001`**.

Фактическая схема передачи JWT для WS задаётся middleware `apps.realtime.middleware.JwtWsAuthMiddleware`.
См. OpenAPI/код middleware в проекте, если нужно строго повторить handshake на фронте.

## 3) Origin policy

Handshake проверяет `Origin`:
- если включён `CHANNELS_WS_ALLOW_ALL_ORIGINS` или `CORS_ALLOW_ALL_ORIGINS` → любой Origin
- иначе — список `CHANNELS_WS_ALLOWED_ORIGINS`, либо `CORS_ALLOWED_ORIGINS`

## 4) Протокол сообщений

Сокет предназначен для **операционных push-событий** вида «что-то изменилось».
Фронт после события делает **REST refetch** нужных списков/карточек.

### 4.1 Сообщение при подключении (server → client)

Сразу после connect сервер отправляет JSON:

```json
{
  "protocol_version": 1,
  "event": "connected",
  "resource": "socket",
  "action": "open",
  "payload": {
    "group": "operational",
    "hint": "После переподключения обновите списки через REST (refetch).",
    "debug": true
  }
}
```

### 4.2 Push-события (server → client)

Consumer получает события в метод `operational_push` и отправляет клиенту `event['payload']` как есть:

```json
{
  "event": "changed",
  "resource": "warehouse_batch",
  "action": "update",
  "payload": { "id": 100 }
}
```

Формат конкретных push-пейлоадов задаётся кодом, который делает broadcast в группу `operational`.

