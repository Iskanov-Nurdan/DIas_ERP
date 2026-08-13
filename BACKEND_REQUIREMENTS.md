# DIAS — API Requirements для бэкенда

Документ сгенерирован полным анализом фронтенда (React, `Dias_Front`). Описывает все эндпоинты, которые фронт реально вызывает (`axios`-клиент `src/shared/api/client.js`), их запросы/ответы, коды ошибок и общие соглашения.

Base URL (dev): `http://127.0.0.1:8000/api/` (env `REACT_APP_API_URL`, всегда с завершающим `/`).
Все пути ниже — относительно `/api/`.

---

## 1. Общие соглашения

### 1.1 Формат авторизации

- **JWT Bearer**. После логина фронт хранит `token` (access) и `refresh` в `localStorage` и на каждый запрос добавляет заголовок:
  ```
  Authorization: Bearer <access_token>
  ```
- Обновление access-токена (refresh-flow) на фронте **не реализовано** отдельным вызовом — при `401` фронт просто разлогинивает пользователя (`localStorage.removeItem('token'|'refresh')` + редирект на `/login`). Если бэк поддерживает refresh, эндпоинт для него сейчас не используется фронтом.
- `POST /api/auth/logout` шлёт `{ refresh }` в теле для блэклиста refresh-токена.
- WebSocket (`/ws/operational/`) авторизуется тем же access JWT через query-параметр `?token=`.

### 1.2 Служебные заголовки (все запросы)

| Заголовок | Когда | Значение |
|---|---|---|
| `Authorization` | если есть токен | `Bearer <token>` |
| `Content-Type` | всегда | `application/json; charset=utf-8` |
| `Accept` | всегда | `application/json; charset=utf-8` |
| `X-Request-Id` | на мутирующих запросах (POST/PUT/PATCH/DELETE), если не выставлен | UUID v4 (или `timestamp-random`), максимум 64 символа. Для трассировки/идемпотентности на бэке. |
| `X-Audit-Shift-Id` / `X-Shift-Id` | на мутирующих запросах к «производственным» ресурсам (см. §1.3), если открыта личная смена сотрудника | id текущей открытой смены (`shifts/my/`) — бэк должен привязывать мутацию к смене для журнала аудита |

### 1.3 Ресурсы, помечаемые `X-Audit-Shift-Id`

Мутации (POST/PUT/PATCH/DELETE) к путям с префиксами: `raw-materials/`, `incoming/`, `materials/`, `chemistry/`, `workshop/`, `warehouse/`, `production/`, `batches/`, `clients/`, `orders/`, `sales/`, `payments/`, `shifts/` — если у пользователя есть открытая личная смена, фронт добавляет заголовок с её id, чтобы бэк мог привязать запись в журнале активности (`activity/`) к смене.

### 1.4 Пагинация списков

Фронт поддерживает **два формата** ответа списков одновременно ("стандарт DIAS" + fallback на DRF-пейджинг):

**Формат A (предпочтительный, "DIAS"):**
```json
{
  "items": [ /* массив записей */ ],
  "meta": { "page": 1, "pages": 5, "total": 97 },
  "links": { "next": "...", "previous": "..." }
}
```

**Формат B (DRF по умолчанию, тоже поддерживается):**
```json
{
  "count": 97,
  "next": "http://.../api/orders/?page=2",
  "previous": null,
  "results": [ /* массив записей */ ]
}
```

Query-параметры на списках: `page`, `page_size` (фронт часто запрашивает `page_size: 100..500` для справочников, подгружаемых целиком), `ordering` (DRF-стиль, `-field` для убывания), `search` (полнотекстовый поиск).

### 1.5 Формат дат

- Даты передаются в **ISO 8601**: `YYYY-MM-DD` для дат без времени (`date`, `sale_date`, `date_from`, `date_to`), `YYYY-MM-DDTHH:mm:ssZ` / со смещением для datetime (`created_at`, `opened_at`, `at`, `occurred_at`).
- На UI даты форматируются как `DD.MM.YYYY` / `DD.MM.YYYY HH:mm` (ru-RU locale) — это только отображение, у бэка всегда ожидается ISO 8601.
- WS-события: `at` — ISO8601 **UTC**.

### 1.6 Числа / decimal

- Денежные и весовые поля бэк отдаёт как **decimal-строки** (`"125.50"`, `"0.017"`) или числа — фронт равно принимает оба варианта (`Number(value)`).
- При отправке дробных значений с фронта (кг, цена) используется точка как разделитель, без хвостовых нулей (`"4"` вместо `"4.0000"`).
- Количество (`pieces`, `quantity` в штуках) — целое число, но нередко отправляется и как строка (`otk_accepted: "13"`) — бэк должен приводить `str → int`.

### 1.7 Именование полей

- **API — snake_case** повсеместно (`client_id`, `total_amount`, `opened_at`, `is_active`, `otk_defect_reason`).
- Фронт в большинстве частей использует snake_case «как есть» без маппинга; camelCase появляется только:
  - в клиентских mapper-функциях отдельных фич (`chemistry/api/blankWorkshopApi.js`, `otk/api/otkWorkshopApi.js`) — они переводят snake_case → camelCase **только для внутреннего состояния UI**, на бэк уходит обратно snake_case;
  - как **defensive fallback**: многие компоненты пробуют по нескольку синонимов ключа (`profile_id ?? profileId`, `total_amount ?? totalAmount`) — это следствие того, что бэк-контракт менялся со временем; в новых эндпоинтах ожидается **только snake_case**.
- ID сущностей — `number` (иногда как строка в URL/форме, но в JSON — число).

### 1.8 Общий формат ошибок

Единого строгого формата на бэке не задокументировано, фронт (`src/shared/lib/apiError.js`) умеет разбирать **любой** из следующих вариантов тела ошибки (пробует по очереди):

```json
{ "message": "Текст ошибки", "code": "SOME_CODE" }
```
```json
{ "detail": "Текст ошибки" }
```
```json
{ "detail": [ { "message": "..." } ] }
```
```json
{ "error": "Текст ошибки" }
```
```json
{ "error": { "message": "Текст ошибки" } }
```
```json
{ "errors": [ { "field": "amount", "message": "Должно быть больше 0" } ] }
```
```json
{ "non_field_errors": ["Общая ошибка формы"] }
```
```json
{ "field_name": ["Ошибка поля"] }
```
```json
{ "missing": [ { "component": "Пыма", "required": 10, "available": 3, "unit": "kg" } ] }
```
(последнее — специфично для проверки достаточности сырья при производстве)

**Рекомендуемый формат для новых эндпоинтов:**
```json
{ "error": "human_readable_message", "code": "MACHINE_CODE", "detail": {} }
```

### 1.9 Стандартные HTTP-коды ошибок

| Код | Когда | Обработка на фронте |
|---|---|---|
| `400` | Валидация тела/параметров | Показывает `message`/`detail`/`errors[]`/поле-специфичные ошибки |
| `401` | Токен невалиден/просрочен | Разлогин, очистка `localStorage`, редирект на `/login` |
| `403` | Нет доступа (`access_key`) | Тост «Нет доступа к этому ресурсу» |
| `404` | Не найдено | Показ ошибки / пустое состояние |
| `409` | Конфликт состояния (например, уже открыта смена, повторная обработка) | Тост с `detail`/`error` или дефолт «Конфликт состояния» |
| `410` | Эндпоинт снят с использования (см. `@deprecated` в коде) | — |
| `429` | Rate limit | Тело может содержать `wait` (сек. до повтора) — фронт показывает «Повторите через N с.» |
| `500` | Внутренняя ошибка сервера | «Ошибка сервера (500). Попробуйте позже.» |
| network error (no response) | Бэк недоступен | «Нет соединения с сервером» |

`429` — опциональное поле `wait: number` (секунды) в теле ответа.

### 1.10 WebSocket (real-time)

- **URL:** `ws://{host}/ws/operational/?token={access_jwt}` (или `wss://` в проде; переопределяется `REACT_APP_WS_URL`).
- **Протокол:** `protocol_version: 1` в каждом кадре.
- **Сервер → клиент, при подключении:**
  ```json
  { "event": "connected", "protocol_version": 1, "user_id": 12 }
  ```
- **Сервер → клиент, при изменении данных:**
  ```json
  { "event": "change", "protocol_version": 1, "resource": "raw_material", "action": "updated", "id": 45, "at": "2026-05-29T12:00:00Z" }
  ```
  - `action`: `created` | `updated` | `deleted` | `changed`
  - `resource` (snake_case, каталог): `shift`, `shift_note`, `shift_complaint`, `activity`, `raw_material`, `incoming`, `material_balance`, `material_writeoff`, `material_movement`, `workshop_blank`, `prepared_blank`, `blank_production_run`, `workshop_run`, `plastic_profile`, `order`, `orders`, `production_batch`, `batch`, `recipe_run`, `warehouse_batch`, `warehouse_package`, `sale`, `payment`, `return`, `client`, `recipe`, `recipes`, `line`, `line_history`, `defect_record`, `rework_request`, `chemistry*` (префикс, например `chemistry_batch`, `chemistry_element`).
- **Heartbeat:** сервер каждые 30с шлёт `{"event":"ping","protocol_version":1}`, клиент отвечает `{"event":"pong","protocol_version":1}`. Если 60с без pong от клиента — сервер закрывает с кодом `4000`.
- **Close codes:** `4000` — idle timeout (клиент переподключается), `4001` — невалидный/просроченный JWT (клиент разлогинивает пользователя).
- Payload событий **не парсится** клиентом содержательно — только триггер для REST refetch нужного ресурса (debounce 300мс).
- Рассылка должна фильтроваться по правам пользователя (`UserAccess`).

---

## 2. Enum-справочник (все каноничные значения)

### 2.1 Роли и доступ

`access` (permission key, массив строк на пользователе `user.accesses`):
`users`, `materials`, `chemistry`, `orders`, `production`, `otk`, `warehouse`, `clients`, `sales`, `client_orders`, `payments`, `shipments`, `analytics`, `shifts`, `my_shift`.

Роль (`Role.name`) — свободная строка; специальный кейс: роль с именем `Администратор` (регистронезависимо) не может быть отредактирована/удалена через UI (защищена только на фронте, но бэк тоже должен это учитывать).

### 2.2 Заявки (Order) — `request_status` / `status`

`draft` (Черновик) · `not_ready` (Не готово) · `ready` (Готово) · `in_production` (В производстве) · `closed` (Закрыта) · `approved` (Принята) · `checking` (Проверка) · `rejected` (Отклонена)

Переходы через UI: `draft` → approve/reject (`POST /orders/{id}/approve/`, `/reject/`); `not_ready` → `POST /orders/{id}/recheck/`; произвольная смена → `PATCH /orders/{id}/status/`.

### 2.3 Оплата — `payment_type`

`full` (Полная) · `partial` (Частичная) · `debt` (В долг)

### 2.4 Оплата — `payment_method`

`cash` (Наличные) · `card` (Карта) · `transfer` (Перевод) — на карте продаж (`sales/`) сейчас используются только `cash`/`card` (перевод = карта).

### 2.5 Продажи (Sale) — `payment_status`

`paid` (Оплачено) · `partially_paid` (Частично оплачено) · `unpaid` (Долг) · `overpaid` (Переплата)

### 2.6 Продажи — `unit_type`

`pieces` (штуки) · `packages` (упаковки — legacy/deprecated путь)

### 2.7 Материалы — `unit`

`kg` · `g`

### 2.8 Движение сырья — `movement_type`

`in` / `incoming` / `intake` / `receipt` (Приход) · `out` / `outgoing` / `consumption` (Расход) · `fifo_consume` (Списание FIFO) · `writeoff` / `writeoff_workshop` (Списание) · `adjustment` (Корректировка) · `transfer` (Перемещение) · `production` (Производство) · `reserve` / `unreserve` (Резерв) · `return` (Возврат)

### 2.9 ОТК — `otk_status`

`pending` / `awaiting` / `waiting` (Ожидает проверки) · `accepted` (Принято) · `rejected` (Брак)

Тело `POST /batches/{id}/otk_accept/`: `otk_accepted`, `otk_defect` — числовые строки (может быть `"0"`). `otk_status = 'rejected'` если `otk_defect > 0 && otk_accepted === 0`, иначе `'accepted'`.

### 2.10 Партии производства (ProductionBatch) — жизненный цикл

`pending` / `draft` (Производство, ждёт) · `production` (Производство) · `otk` / `at_otk` / `awaiting_otk` (На ОТК) · `done` / `completed` / `closed` (Завершено)

### 2.11 Склад ГП — `quality`

`good` (Годный) · `defect` (Брак) — плюс свободнотекстовое поле `defect_reason`.

### 2.12 Склад ГП — `status`

`available` (Доступно) · `reserved` (Зарезервировано) · `shipped` / `sold` (Продано)

### 2.13 Склад ГП — `inventory_form` / `packaging_state`

`unpacked` (Не упаковано, по умолчанию) · `packed` (Упаковано) · `open_package` (Открытая упаковка)

### 2.14 Склад — операции (`warehouse/operations/`) — `kind`

`accept` (Приёмка) · `otk_account` (Приёмка ОТК) · `package` (Упаковка) · `sale` (Продажа) · `return` (Возврат) · `defect` (Брак) · `rework` (Переделка)

`direction`: `in` | `out`

### 2.15 ОТК v2 — `shift_period`

`day` (День) · `night` (Ночь)

### 2.16 Смена (Shift) — `status`

`open` (также допускаются синонимы `opened`/`active`) · `closed`

### 2.17 Журнал активности (Activity) — `action`

`create` (Создал) · `update` (Изменил) · `delete` (Удалил) · `restore` (Восстановил) · `view` (Просмотрел)

`payload.changes[].type`: `scalar` | `enum` | `fk` | `json` | `file_meta`

### 2.18 Прочие расходы (Analytics OtherExpense) — `status`

`pending` (Ожидает) · `accepted` (Принят) · при отказе (`reject`) запись **удаляется**, статус `rejected` в списке не возвращается.

### 2.19 Рецепты — тип компонента (`components[].type`)

`raw_material` (сырьё) · `chemistry` (химия/полуфабрикат) · `rework_stock` (переделанный остаток со склада)

### 2.20 Клиент — `client_type`

`company` (юр. лицо) · `individual` (физ. лицо)

### 2.21 Клиент — `status` / `is_active`

`active` / `inactive`, либо булево `is_active`.

### 2.22 Линии — `history.action` (журнал линии)

`open` · `close` · `params_update` · `shift_pause` / `pause` / `paused` · `shift_resume` / `resume` / `resumed`

---

## 3. Auth

### POST /api/auth/login
Авторизация по логину и паролю.

**Auth:** не требуется.

**Request:**
```json
{ "name": "ivanov", "password": "secret123" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| name | string | да |
| password | string | да |

**Response 200:**
```json
{
  "token": "eyJhbGciOi...",
  "refresh": "eyJhbGciOi...",
  "user": { "id": 12, "name": "Иванов И.И.", "role": 3, "role_name": "Оператор", "is_active": true }
}
```
| Поле | Тип |
|---|---|
| token | string (JWT access) |
| refresh | string, nullable (JWT refresh) |
| user | object, nullable — если отсутствует, фронт дозапрашивает `GET /me` |

**Errors:**
- `400` — не переданы `name`/`password`
- `401` — неверный логин/пароль: `{ "error": "Неверный логин или пароль" }`
- `429` — слишком много попыток

### POST /api/auth/logout
Инвалидация refresh-токена (blacklist).

**Auth:** Bearer.

**Request:**
```json
{ "refresh": "eyJhbGciOi..." }
```

**Response:** `200`/`204`, тело не используется фронтом.

**Errors:** `401` (токен уже невалиден — фронт всё равно очищает локальное состояние, ошибку игнорирует).

### GET /api/me
Данные текущего пользователя + список доступов.

**Auth:** Bearer.

**Response 200 (вариант A):**
```json
{
  "user": { "id": 12, "name": "Иванов И.И.", "username": "ivanov", "role": 3, "role_name": "Оператор", "is_active": true },
  "accesses": ["shifts", "my_shift", "warehouse"]
}
```
**Response 200 (вариант B, плоский — тоже поддерживается фронтом):**
```json
{ "id": 12, "name": "Иванов И.И.", "role_name": "Оператор", "accesses": ["shifts"] }
```

**Errors:** `401` — токен невалиден.

---

## 4. Пользователи

### GET /api/users/
Список сотрудников.

**Auth:** Bearer, `access=users`.
**Query:** `page`, `page_size`, `search`, `role`, `is_active`.

**Response 200:**
```json
{
  "items": [
    { "id": 12, "name": "Иванов И.И.", "username": "ivanov", "role": 3, "role_name": "Оператор", "is_active": true, "accesses": ["shifts"] }
  ],
  "meta": { "page": 1, "pages": 1, "total": 1 }
}
```

### GET /api/users/{id}/
Карточка сотрудника (включая `accesses`).

**Response 200:** объект пользователя, см. выше + `accesses: string[]`.

### POST /api/users/
Создать сотрудника.

**Request:**
```json
{ "name": "Петров П.П.", "username": "petrov", "password": "initial123", "role": 3, "is_active": true }
```
| Поле | Тип | Обязательно |
|---|---|---|
| name | string | да |
| username | string | да |
| password | string | да |
| role | number (id роли) | да |
| is_active | boolean | нет (default true) |

**Response 201:** созданный объект пользователя (без `password`).

**Errors:** `400` (валидация, например `username` занят) — `{ "username": ["Уже используется"] }`.

### PATCH /api/users/{id}/
Обновить сотрудника (в т.ч. смена пароля).

**Request:** любое подмножество полей из POST, плюс опционально `password` для сброса.

**Response 200:** обновлённый объект.

### DELETE /api/users/{id}/
Удалить сотрудника.

**Response:** `204`.
**Errors:** `409` — нельзя удалить (есть связанные записи, например открытые смены).

### PATCH /api/users/{id}/access/
Обновить доступные вкладки конкретного пользователя (не роли).

**Request:**
```json
{ "access_keys": ["shifts", "my_shift", "warehouse"] }
```
| Поле | Тип |
|---|---|
| access_keys | string[] (см. §2.1) |

**Response 200:** `{ "id": 12, "accesses": ["shifts", "my_shift", "warehouse"] }`

---

## 5. Роли

### GET /api/roles/
**Auth:** Bearer, `access=users`. **Query:** `page`, `page_size`, `search`.
**Response 200:** `{ "items": [ { "id": 3, "name": "Оператор" } ], "meta": {...} }`

### GET /api/roles/{id}/
**Response 200:** `{ "id": 3, "name": "Оператор" }`

### POST /api/roles/
**Request:** `{ "name": "Мастер" }`
**Response 201:** созданная роль.

### PATCH /api/roles/{id}/
**Request:** `{ "name": "Мастер-старший" }`
**Response 200:** обновлённая роль.
**Errors:** `403`/`400` — попытка редактировать защищённую роль «Администратор».

### DELETE /api/roles/{id}/
**Response:** `204`.
**Errors:** `409` — есть пользователи с этой ролью.

---

## 6. Клиенты

### GET /api/clients/
**Auth:** Bearer, `access=clients`. **Query:** `page`, `page_size`, `search`, `is_active`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 1, "name": "ООО Стройка", "client_type": "company", "phone": "+996700111222",
      "phone_extra": null, "settlement_account": "1234567890", "inn": "01234567890123",
      "address": "г. Бишкек, ул. ...", "is_active": true, "status": "active"
    }
  ],
  "meta": { "page": 1, "pages": 3, "total": 42 }
}
```

### GET /api/clients/{id}/
**Response 200:** объект клиента (как выше).

### POST /api/clients/
**Request:**
```json
{
  "client_type": "company",
  "name": "ООО Стройка",
  "phone": "+996700111222",
  "phone_extra": null,
  "status": "active",
  "settlement_account": "1234567890",
  "inn": "01234567890123",
  "address": "г. Бишкек"
}
```
| Поле | Тип | Обязательно | Примечание |
|---|---|---|---|
| client_type | string enum (§2.20) | да | |
| name | string | да | |
| phone | string | да | |
| phone_extra | string, nullable | нет | доп. телефон |
| status | string enum (§2.21) | нет | default `active` |
| settlement_account | string, nullable | нет | только для `company` |
| inn | string, nullable | нет | |
| address | string, nullable | нет | |

**Response 201:** созданный клиент, `{ "id": 123, "name": "...", ... }`.

### PATCH /api/clients/{id}/
**Request:** подмножество полей POST (включая деактивацию `{ "is_active": false }` / `{ "status": "inactive" }`).
**Response 200:** обновлённый клиент.

### GET /api/clients/{id}/history/
История документов клиента (заявки/продажи/оплаты/возвраты).

**Response 200:**
```json
{ "items": [ { "id": 1, "type": "sale", "date": "2026-05-01", "total_amount": "50000.00" } ] }
```

### GET /api/clients/{id}/profile/
Расширенная карточка клиента.

**Response 200:**
```json
{
  "client": { "id": 1, "name": "ООО Стройка" },
  "summary": { "total_debt": "20000.00", "total_sales_amount": "500000.00", "total_paid_amount": "480000.00", "total_orders": 12 },
  "purchases": [],
  "orders": [ { "id": 42, "display": "Заявка №42", "date": "2026-05-01", "status_label": "Закрыта" } ],
  "debts": [ { "id": 7, "total_amount": "20000.00", "debt_amount": "20000.00" } ]
}
```

### GET /api/client-financial-summary/?client_id={id}
**Response 200:** `{ "total_debt": "20000.00", "total_sales_amount": "500000.00", "total_paid_amount": "480000.00", "total_orders": 12 }`

**Errors (весь раздел «Клиенты»):** `400` валидация, `404` не найден, `409` (например, `inactive_client` при попытке продажи неактивному клиенту).

---

## 7. Оплаты (Payments)

### GET /api/payments/
**Auth:** Bearer, `access=payments`. **Query:** `page`, `page_size`, `search`, `client_id`, `payment_type`, `payment_method`, `status`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 501, "date": "2026-06-01", "client": 1, "client_name": "ООО Стройка",
      "payment_type": "payment", "payment_method": "cash", "amount": "20000.00",
      "linked_sale": 88, "comment": "", "status": "active"
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 1 }
}
```

### GET /api/payments/{id}/
**Response 200:** объект платежа.

### POST /api/payments/
**Request:**
```json
{
  "date": "2026-06-01",
  "client": 1,
  "payment_type": "payment",
  "payment_method": "cash",
  "amount": "20000.00",
  "linked_sale": 88,
  "comment": "Погашение долга"
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| date | string (ISO date) | да |
| client | number | да |
| payment_type | string | да |
| payment_method | string enum (§2.4) | да |
| amount | string/number decimal | да |
| linked_sale / linked_order / linked_return | number, nullable | нет (одно из) |
| comment | string | нет |
| manual_refund_reason | string | нет (для возвратов) |

**Response 201:** созданный платёж.

### PATCH /api/payments/{id}/
**Request:** подмножество полей (реквизиты, комментарий — статус этим методом не меняется).
**Response 200:** обновлённый платёж.

### PATCH /api/payments/{id}/cancel/
Отмена платежа.

**Request:** `{}`
**Response 200:** платёж со `status: "canceled"`.
**Errors:** `409` — уже отменён / нельзя отменить.

### GET /api/payments/summary/?client_id={id}
**Response 200:** `{ "total_debt": "20000.00", "total_paid": "480000.00" }`

### GET /api/payments/select-sources/?client_id={id}
Источники для формы создания оплаты (список продаж клиента с долгом).

**Response 200:**
```json
{ "sales": [ { "id": 88, "debt_amount": "20000.00", "total_amount": "50000.00", "sale_lines": [] } ] }
```

**Errors:** `400`, `404`, `409` (`payment_status_update_forbidden` и подобные бизнес-коды в `error`/`detail`).

---

## 8. Заявки (Orders)

### GET /api/orders/
**Auth:** Bearer, `access=orders` / `client_orders`. **Query:** `page`, `page_size`, `search`, `status`/`request_status`, `client_id`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 42, "client_id": 1, "client_name": "ООО Стройка", "date": "2026-05-21",
      "request_status": "draft", "status_label": "Черновик",
      "payment_type": "debt", "payment_method": "cash",
      "total_amount": "50000.00", "paid_amount": "0.00",
      "lines_count": 2,
      "order_lines": [
        { "id": 101, "profile_id": 5, "profile_name": "Пластиковый профиль 5м", "recipe_id": null, "recipe_name": null, "quantity": "20" },
        { "id": 102, "profile_id": 12, "profile_name": "Профиль 60×40", "recipe_id": 7, "recipe_name": "Рецепт А", "quantity": "10" }
      ]
    }
  ],
  "meta": { "page": 1, "pages": 2, "total": 30 }
}
```
`order_lines` **обязателен** и должен содержать **все** позиции заявки (не только первую) — как в списке, так и в детальной карточке. Допустимые синонимы ключа массива: `lines`, `items`, `request_lines`, `positions`, `products`.

### GET /api/orders/{id}/
**Response 200:** тот же объект заявки, детально.

### GET /api/orders/select-sources/
Справочники для формы создания заявки.

**Response 200:** `{ "clients": [...], "profiles": [...], "recipes": [...] }` (состав зависит от бэка, фронт использует по мере наличия).

### POST /api/orders/
**Request:**
```json
{
  "client": 1,
  "date": "2026-05-21",
  "order_lines": [
    { "profile": 5, "quantity": 20 },
    { "profile": 12, "quantity": 10 }
  ],
  "payment_type": "debt",
  "payment_method": "cash",
  "total_amount": "50000",
  "paid_amount": "0",
  "comment": ""
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| client | number | да |
| date | string (ISO date) | да |
| order_lines | array<{profile: number, quantity: number, comment?: string}> | да, минимум 1 |
| payment_type | string enum (§2.3) | да |
| payment_method | string enum (§2.4) | да |
| total_amount | string/number decimal | да |
| paid_amount | string/number decimal | нет (default 0) |
| comment | string | нет |

**Response 201:** созданная заявка (структура как в GET), `request_status: "draft"`.

### PATCH /api/orders/{id}/
**Request:** подмножество полей POST (редактирование документа/строк).
**Response 200:** обновлённая заявка.

### PATCH /api/orders/{id}/status/
**Request:** `{ "status": "in_production" }` (см. enum §2.2)
**Response 200:** обновлённая заявка.
**Errors:** `400` — `invalid_status_transition`.

### PATCH /api/orders/{id}/cancel/
**Request:** `{}`
**Response 200:** заявка со `status: "closed"`/отменённым статусом.

### GET /api/orders/{id}/history/
**Response 200:** `{ "items": [ { "id": 1, "action": "update", "created_at": "...", "summary": "Изменён статус" } ] }`

### GET /api/orders/{id}/waybill/
Накладная (HTML/PDF/JSON — фронт строит прямую ссылку `apiClient.defaults.baseURL + 'orders/{id}/waybill/'` для открытия в новой вкладке).

**Response 200:** файл или HTML-документ.

### POST /api/orders/{id}/approve/
Принять черновик заявки.
**Request:** `{}` **Response 200:** заявка со `request_status: "ready"`/`"approved"`.

### POST /api/orders/{id}/reject/
Отклонить заявку.
**Request:** `{}` **Response 200:** заявка со `request_status: "rejected"`.

### POST /api/orders/{id}/recheck/
Повторная проверка заявки в статусе `not_ready`.
**Request:** `{}` **Response 200:** обновлённая заявка.

**Errors (весь раздел):** `400` валидация строк/цен, `403` `status_update_forbidden`, `404`, `409` конфликт статуса.

---

## 9. Продажи (Sales)

### GET /api/sales/
**Auth:** Bearer, `access=sales`. **Query:** `page`, `page_size`, `search`, `client_id`, `sale_status`/`payment_status`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 88, "sale_number": "88", "client": 1, "client_name": "ООО Стройка",
      "order": 42, "order_display": "Заявка №42",
      "sale_date": "2026-06-03", "unit_type": "pieces",
      "total_amount": "50000.00", "paid_amount": "30000.00", "debt_amount": "20000.00",
      "payment_type": "partial", "payment_method": "cash", "payment_status": "partially_paid",
      "sale_lines": [
        { "id": 1, "warehouse_batch": 12, "quantity": "2", "unit_price": "129.00", "total_amount": "258.00" }
      ]
    }
  ],
  "meta": { "page": 1, "pages": 4, "total": 76 }
}
```

### GET /api/sales/{id}/
**Response 200:** объект продажи детально (как выше).

### POST /api/sales/preview/
Предпросчёт суммы продажи без сохранения.

**Request:**
```json
{
  "client": 1,
  "sale_date": "2026-06-03",
  "unit_type": "pieces",
  "sale_lines": [ { "warehouse_batch": 12, "quantity": "2" } ],
  "payment_type": "full",
  "payment_method": "cash",
  "paid_amount": "32"
}
```
**Response 200:**
```json
{
  "total_amount": "258.00",
  "sale_lines": [ { "warehouse_batch": 12, "quantity": "2", "unit_price": "129.00", "line_total": "258.00" } ]
}
```

### POST /api/sales/
**Request:** то же тело, что `preview/`, плюс опционально смешанная оплата:
```json
{
  "client": 1,
  "sale_date": "2026-06-03",
  "unit_type": "pieces",
  "sale_lines": [ { "warehouse_batch": 12, "quantity": "2" } ],
  "payment_type": "full",
  "payment_method": "card",
  "paid_amount": "32",
  "payment_splits": [
    { "payment_method": "cash", "amount": "10" },
    { "payment_method": "card", "amount": "22" }
  ],
  "payment_reference": "+996701111544"
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| client | number | да |
| sale_date | string (ISO date) | да |
| unit_type | string enum (§2.6) | нет, default `pieces` |
| sale_lines | array<{warehouse_batch: number, quantity: string/number, unit_price?: string}> | да |
| payment_type | string enum (§2.3) | да |
| payment_method | string enum (§2.4) | да |
| paid_amount | string decimal | да (`"0"` при `debt`) |
| payment_splits | array<{payment_method, amount}> | нет |
| payment_reference | string, nullable | нет (номер карты/телефона) |

`unit_price` в строке можно не передавать — цена берётся автоматически из профиля (`cost_price + markup_amount` / `sale_unit_price`); если передан — бэк сверяет с допуском `0.01`.

**Response 201:** созданная продажа (структура как в GET), списывает остаток склада.

**Errors:** `400` (`missing_sale_lines`, недостаточно остатка), `409` (`sale_status_update_forbidden`, `inactive_client`).

### PATCH /api/sales/{id}/
**Request:** подмножество документных полей (без строк/статуса).
**Response 200:** обновлённая продажа.

### PATCH /api/sales/{id}/status/
**Request:** `{ "status": "shipped" }` (+ доп. поля по необходимости)
**Response 200:** обновлённая продажа.

### PATCH /api/sales/{id}/cancel/
**Request:** `{}`
**Response 200:** продажа со статусом отмены, возврат остатка на склад.

### GET /api/sales/{id}/credit-check/
Проверка кредитного лимита клиента перед продажей в долг.

**Response 200:** `{ "allowed": true, "credit_limit": "100000.00", "current_debt": "20000.00", "available": "80000.00" }`

### GET /api/sales/{id}/waybill/?format=json
**Response 200 (format=json):**
```json
{ "buyer_name": "ООО Стройка", "title": "Накладная №88", "total": "258.00", "sale_lines": [ { "name": "Профиль 6м", "quantity_display": "2 шт", "unit_price": "129.00", "line_total": "258.00" } ] }
```
Без `?format=json` — HTML/PDF-документ.

### GET /api/sales/{id}/receipt/
Чек (HTML/PDF).

### GET /api/sales/select-sources/?client=&order=&unit_type=pieces
Остатки склада, доступные к продаже (по клиенту/заявке).

**Response 200:**
```json
{
  "profile_stock": [
    { "id": 12, "profile_id": 5, "product_name": "Пластиковый профиль 6м премиум", "available_pieces": 9, "unit_sale_price": "129.00" }
  ]
}
```

**Errors (весь раздел «Продажи»):** `400`, `403` (`sale_status_update_forbidden`, `sale_lines_update_forbidden`), `404`, `409`.

---

## 10. Сырьё (Materials)

### GET /api/raw-materials/
**Auth:** Bearer, `access=materials`. **Query:** `page`, `page_size`, `search`, `ordering`.

**Response 200:**
```json
{
  "items": [
    { "id": 1, "name": "Пыма", "unit": "kg", "balance": 120.5, "min_balance": 10, "is_active": true, "comment": "", "deletable": true }
  ],
  "meta": { "page": 1, "pages": 1, "total": 8 }
}
```

### GET /api/raw-materials/{id}/
**Response 200:** объект сырья (как выше).

### POST /api/raw-materials/
**Request:** `{ "name": "Пыма", "unit": "kg", "min_balance": 10, "is_active": true, "comment": "" }`
**Response 201:** созданное сырьё, `balance: 0`.

### PATCH /api/raw-materials/{id}/
**Request:** подмножество полей.
**Response 200:** обновлённое сырьё. `unit` не должен меняться, если уже есть приходы/движения (`unit_locked`).

### DELETE /api/raw-materials/{id}/
**Response:** `204`.
**Errors:** `409` — есть связанные приходы/рецепты (`deletable: false`).

### GET /api/incoming/
Приходы сырья.

**Query:** `page`, `page_size`, `material_id`, `date_from`, `date_to`.
**Response 200:**
```json
{
  "items": [
    { "id": 1, "material_id": 1, "material_name": "Пыма", "quantity_initial": 100, "quantity_remaining": 60, "unit": "kg", "unit_price": "50.00", "total_price": "5000.00", "received_at": "2026-05-25T16:23:00+06:00", "supplier_name": "ИП Сидоров", "document_number": "INV-001", "comment": "" }
  ],
  "meta": { "page": 1, "pages": 1, "total": 3 }
}
```

### POST /api/incoming/
**Request:**
```json
{ "material_id": 1, "quantity": 100, "unit_price": "50.00", "received_at": "2026-05-25", "supplier_name": "ИП Сидоров", "document_number": "INV-001", "comment": "" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| material_id | number | да |
| quantity | number | да, > 0 |
| unit_price | string decimal | да |
| received_at | string (ISO date) | да |
| supplier_name | string | нет |
| document_number | string | нет |
| comment | string | нет |

**Response 201:** созданный приход (списывается по FIFO в дальнейшем).

### GET /api/materials/balances/
Остатки по всем видам сырья.

**Response 200:** `{ "items": [ { "material_id": 1, "material_name": "Пыма", "unit": "kg", "balance": 120.5 } ] }`

### GET /api/materials/movements/
Журнал движения сырья (приходы/FIFO-списания/корректировки).

**Query:** `page`, `page_size`, `material_id`, `date_from`, `date_to`, `movement_type`.
**Response 200:**
```json
{
  "items": [
    { "id": 1, "material_id": 1, "material_name": "Пыма", "occurred_at": "2026-05-25T16:23:00Z", "movement_type": "fifo_consume", "quantity": -5.0, "unit": "kg", "unit_price": "50.00" }
  ],
  "meta": { "page": 1, "pages": 1, "total": 40 }
}
```

**Errors (раздел «Сырьё»):** `400`, `404`, `409` (`delete_disabled`).

---

## 11. Химия (полуфабрикаты)

### GET /api/chemistry/elements/
Справочник химических элементов/полуфабрикатов.

**Query:** `page`, `page_size`, `search`.
**Response 200:** `{ "items": [ { "id": 1, "name": "Стабилизатор А", "unit": "kg", "is_active": true } ], "meta": {...} }`

### GET /api/chemistry/elements/{id}/
**Response 200:** объект элемента.

### POST /api/chemistry/elements/
**Request:** `{ "name": "Стабилизатор А", "unit": "kg", "is_active": true }`
**Response 201:** созданный элемент.

### PATCH /api/chemistry/elements/{id}/
**Response 200:** обновлённый элемент.

### DELETE /api/chemistry/elements/{id}/
**Response:** `204`. **Errors:** `409` (используется в рецептах/партиях).

### GET /api/chemistry/balances/
Остатки по агрегату химии.

**Query:** `page`, `page_size`.
**Response 200:** `{ "items": [ { "element_id": 1, "element_name": "Стабилизатор А", "balance": 45.2, "unit": "kg" } ] }`

### GET /api/chemistry/batches/
Партии химии (read-only, FIFO-списания ведутся по ним).

**Query:** `page`, `page_size`, `element_id`.
**Response 200:** `{ "items": [ { "id": 1, "element_id": 1, "quantity_remaining": 20.0, "created_at": "..." } ] }`

### POST /api/chemistry/elements/produce/
Выпуск полуфабриката: списание сырья по FIFO, создание партии химии, расчёт себестоимости.

**Request:**
```json
{ "element_id": 1, "quantity": 50, "components": [ { "raw_material_id": 3, "quantity_kg": "20.000" } ] }
```
**Response 201:** `{ "id": 15, "element_id": 1, "quantity_remaining": 50, "cost_per_kg": "12.30" }`
**Errors:** `400` — недостаточно сырья (`{ "missing": [ { "component": "Пыма", "required": 20, "available": 12, "unit": "kg" } ] }`).

---

## 12. Цех / заготовки (Workshop)

### GET /api/workshop/blanks/
Справочник заготовок (WorkshopBlank).

**Query:** `page`, `page_size`, `search`.
**Response 200:**
```json
{
  "items": [
    {
      "id": 12, "name": "ПВХ белая смесь", "recipe_kg_per_barrel": 25.0, "chemistry_id": 1,
      "composition": [ { "raw_material_id": 3, "quantity_kg": "20.000", "raw_material_name": "Пыма" } ]
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 5 }
}
```

### GET /api/workshop/blanks/{id}/
**Response 200:** объект заготовки (как выше).

### POST /api/workshop/blanks/
**Request:**
```json
{ "name": "ПВХ белая смесь", "recipe_kg_per_barrel": 25.0, "chemistry_id": 1, "composition": [ { "raw_material_id": 3, "quantity_kg": "20.000" } ] }
```
**Response 201:** созданная заготовка.

### PATCH /api/workshop/blanks/{id}/
**Response 200:** обновлённая заготовка.

### DELETE /api/workshop/blanks/{id}/
**Response:** `204`. **Errors:** `409` (используется в партиях/пуле ОТК).

### GET /api/workshop/prepared-blanks/
Остатки цеха (готовые к использованию заготовки).

**Query:** `page`, `page_size`.
**Response 200 (формат DIAS или DRF):**
```json
{
  "items": [
    {
      "blank_id": 12, "blank_name": "ПВХ белая смесь", "barrels": 4, "extra_kg": 2.5,
      "recipe_kg_per_barrel": 25.0, "total_kg": 102.5,
      "from_machine_remainder_kg": 1.2, "from_defect_kg": 0.3, "pure_kg": 101.0
    }
  ]
}
```

### POST /api/workshop/prepared-blanks/{blankId}/add-barrel/
Добавить бочку заготовки в остаток цеха.

**Request:** `{}`
**Response 200/201:** обновлённая строка остатка.

### GET /api/workshop/blank-production-runs/
Партии выпуска заготовки в производство.

**Query:** `page`, `page_size`, `blank_id`, `date_from`, `date_to`.
**Response 200:**
```json
{
  "items": [
    {
      "id": 88, "created_at": "2026-06-02T10:00:00Z", "source_type": "blank",
      "blank_id": 12, "blank_name": "ПВХ белая смесь",
      "blank_total_kg": 100, "blank_used_in_production_kg": 100, "vat_max_kg_demo": 180,
      "otk_fully_accounted": false, "remaining_kg_in_pool": 100
    }
  ]
}
```

### GET /api/workshop/blank-production-runs/{id}/
**Response 200:** объект прогона (как выше).

### POST /api/workshop/blank-production-runs/
Выпуск заготовки в производство — увеличивает пул ОТК по `blank_id`.

**Request:**
```json
{ "blank_id": 12, "blank_total_kg": 100, "blank_used_in_production_kg": 100, "vat_max_kg_demo": 180 }
```
| Поле | Тип | Обязательно |
|---|---|---|
| blank_id | number | да |
| blank_total_kg | number | да |
| blank_used_in_production_kg | number | да |
| vat_max_kg_demo | number | нет |

**Response 201:** созданный прогон. Побочный эффект: списание из `prepared-blanks`, `+= blank_used_in_production_kg` в пул ОТК (`workshop/otk-blanks/`), создание записи intake (`workshop/otk-blanks/intakes/`), WS-события `otk`/`workshop`.

**Errors:** `400` — недостаточно кг в остатке цеха.

### POST /api/workshop/blank-production-runs/{id}/otk-defect/
**@deprecated (410 Gone)** — учёт брака теперь через `POST workshop/otk-blanks/{blank_id}/account/` / `workshop/otk-account/`.

### POST /api/workshop/blank-production-runs/{id}/accept-gp/
**@deprecated (410 Gone)** — приёмка ГП теперь через ОТК account.

---

## 13. Рецепты

### GET /api/recipes/
**Auth:** Bearer, `access=production`/`chemistry`. **Query:** `page`, `page_size`, `search`, `is_active`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 7, "recipe": "Рецепт А", "profile_id": 12, "profile_name": "Профиль 60×40",
      "is_active": true, "base_unit": "per_meter",
      "components": [
        { "type": "raw_material", "material_id": 3, "quantity_per_meter": "0.050", "unit": "kg" },
        { "type": "chemistry", "chemistry_id": 1, "quantity_per_meter": "0.010", "unit": "kg" },
        { "type": "rework_stock", "rework_warehouse_batch_id": 40, "quantity_per_meter": "0.005", "unit": "kg" }
      ]
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 15 }
}
```

### GET /api/recipes/{id}/
**Response 200:** объект рецепта (как выше).

### GET /api/recipes/{id}/availability/
Проверка достаточности сырья/химии по рецепту.

**Response 200:**
```json
{ "items": [ { "material_id": 3, "required": 10, "available": 25, "unit": "kg", "sufficient": true } ] }
```

### POST /api/recipes/
**Request:** тело как в GET-элементе без `id`.
**Response 201:** созданный рецепт.

### PATCH /api/recipes/{id}/
**Response 200:** обновлённый рецепт.

### DELETE /api/recipes/{id}/
**Response:** `204`. **Errors:** `409` (используется в партиях).

---

## 14. Профили (Plastic Profiles)

### GET /api/plastic-profiles/
**Query:** `page`, `page_size`, `search`, `is_active`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 3, "name": "Профиль белый 6м", "code": "GP-6W", "is_active": true,
      "weight_kg_per_piece": "0.017", "blank_id": 12, "blank_name": "ПВХ белая смесь",
      "cost_price": "125.50", "markup_amount": "30.00",
      "extra_rubber": "5.00", "extra_label": "2.00", "extra_labor": "15.00",
      "extra_electricity": "3.00", "extra_repair": "1.00",
      "other_expenses_total": "26.00", "sale_unit_price": "181.50"
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 20 }
}
```
`cost_price`/`sale_unit_price` — `null`, пока по заготовке не было учёта в ОТК (см. §15). `other_expenses_total` = сумма всех `extra_*`. `sale_unit_price` = `cost_price + other_expenses_total + markup_amount`.

### GET /api/plastic-profiles/{id}/
**Response 200:** объект профиля.

### POST /api/plastic-profiles/
**Request:**
```json
{
  "name": "Профиль белый 6м", "code": "GP-6W", "is_active": true,
  "weight_kg_per_piece": 0.017, "blank_id": 12, "markup_amount": 30,
  "extra_rubber": 5, "extra_label": 2, "extra_labor": 15, "extra_electricity": 3, "extra_repair": 1,
  "comment": ""
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| name | string | да |
| code | string | нет |
| is_active | boolean | нет (default true) |
| weight_kg_per_piece | number | да |
| blank_id | number | да, активная заготовка |
| markup_amount | number | нет (default 0) |
| extra_rubber / extra_label / extra_labor / extra_electricity / extra_repair | number ≥ 0 | нет (default 0) |
| comment | string | нет |

`cost_price` в теле **игнорируется бэком** (read-only, вычисляется по факту учёта ОТК).

**Response 201:** созданный профиль, `cost_price: null`, `sale_unit_price: null`.

### PATCH /api/plastic-profiles/{id}/
**Request:** любое подмножество редактируемых полей (кроме `cost_price`).
**Response 200:** обновлённый профиль.

### DELETE /api/plastic-profiles/{id}/
**Response:** `204`. **Errors:** `409` (используется в заявках/рецептах/продажах).

---

## 15. Производство — партии (batches) и заявки на выпуск

### GET /api/batches/
Партии производства (ProductionBatch, legacy-вкладка, не «Заявки»).

**Query:** `page`, `page_size`, `id__in` (список id через запятую, санками по 40), `line_id`, `status`.
**Response 200:**
```json
{
  "items": [
    {
      "id": 501, "pieces": 200, "length_per_piece": 6.0, "total_meters": 1200.0,
      "line_name": "Линия 1", "profile_name": "Профиль 60×40", "recipe_name": "Рецепт А",
      "cost_per_meter": "10.50", "material_cost_total": "12600.00",
      "otk_status": "pending", "otk_accepted": null, "otk_defect": null,
      "lifecycle_status": "production", "in_otk_queue": false, "can_edit": true, "comment": ""
    }
  ],
  "meta": { "page": 1, "pages": 3, "total": 55 }
}
```

### GET /api/batches/{id}/
**Response 200:** объект партии (как выше).

### POST /api/batches/
**Request:**
```json
{ "profile": 12, "recipe": 7, "line": 1, "date": "2026-06-01", "pieces": 200, "length_per_piece": 6.0, "comment": "" }
```
**Response 201:** созданная партия, `otk_status: "pending"`.
**Errors:** `400` — недостаточно сырья/химии по рецепту (`missing[]`, см. §1.8).

### PATCH /api/batches/{id}/
**Response 200:** обновлённая партия (пока `can_edit: true`, до передачи в ОТК).

### DELETE /api/batches/{id}/
**Response:** `204`.

### POST /api/batches/{id}/submit-for-otk/
Передать партию в очередь ОТК.

**Request:** `{}`
**Response 200:** партия с `lifecycle_status: "otk"`, `in_otk_queue: true`.

### GET /api/production/requests/
Готовые к запуску заявки (для старта производства по заявке).

**Query:** `page`, `page_size`.
**Response 200:** `{ "items": [ { "id": 42, "client_name": "ООО Стройка", "order_lines": [...] } ] }`

### POST /api/production/requests/{id}/start/
Старт производства по заявке — одна позиция или несколько (создаёт отдельный `BlankProductionRun` на каждый профиль).

**Request (одна позиция):**
```json
{ "blank": 12, "order_line_id": 101 }
```
**Request (несколько позиций):**
```json
{ "line_starts": [ { "blank": 12, "order_line_id": 101 }, { "blank": 13, "profile_id": 20 } ] }
```
**Response 201:** `{ "runs": [ { "id": 88, "blank_id": 12 } ] }`

---

## 16. ОТК

### POST /api/batches/{id}/otk_accept/
Сохранить результат проверки ОТК по партии (legacy-поток, «партии»).

**Request:**
```json
{
  "otk_accepted": "180", "otk_defect": "20",
  "accepted": 180, "rejected": 20, "otk_status": "accepted",
  "otk_defect_reason": "Скол на кромке", "otk_comment": "",
  "otk_inspector": 5, "otk_inspector_name": "Сидорова А.", "otk_checked_at": "2026-06-01T15:00:00Z"
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| otk_accepted | string (число) | да |
| otk_defect | string (число, может быть `"0"`) | да |
| otk_status | string enum (§2.9) | да, вычисляется фронтом |
| otk_defect_reason | string | нет |
| otk_comment | string | нет |
| otk_inspector | number | нет |
| otk_inspector_name | string | нет |
| otk_checked_at | string (ISO datetime) | нет |

**Response 200:** обновлённая партия, `otk_status`, `otk_checked_at` заполнены.
**Errors:** `400` — количество превышает `pieces` партии.

### GET /api/otk/pending/
Очередь партий, ожидающих проверки ОТК. **Fallback:** если `404`/`405`, фронт использует `GET /api/batches/` с фильтром.

**Response 200:** `{ "items": [ {...ProductionBatch с otk_status: "pending"} ] }`

### GET /api/workshop/otk-blanks/
Пул заготовок ОТК (кг, доступные к учёту).

**Response 200:**
```json
{ "items": [ { "blank_id": 12, "blank_name": "ПВХ белая смесь", "remaining_kg": "220.000", "total_intake_kg": "320.000", "can_account": true, "last_intake_at": "2026-06-02T11:52:00Z" } ] }
```

### GET /api/workshop/otk-blanks/intakes/
История приходов в пул (каждый выпуск заготовки).

**Query:** `blank_id`, `date_from`, `date_to`, `ordering=-created_at`.
**Response 200:**
```json
{ "items": [ { "id": 501, "blank_id": 12, "blank_name": "ПВХ белая смесь", "kg": "100.000", "run_id": 88, "created_at": "2026-06-02T10:00:00Z", "source": "produce" } ] }
```

### GET /api/workshop/otk-accounting/
История сессий учёта ОТК.

**Response 200:**
```json
{
  "items": [
    {
      "id": 900, "blank_id": 12, "blank_name": "ПВХ белая смесь", "created_at": "2026-06-02T14:00:00Z",
      "consumed_kg": "45.000", "defect_kg": "2.500", "remaining_kg_after": "175.000",
      "operator_id": 4, "operator_name": "Иванов", "chemist_id": 5, "chemist_name": "Петров",
      "packer_ids": [6], "packer_names": ["Сидоров"], "shift_period": "day",
      "lines": [ { "profile_id": 3, "profile_name": "Профиль 60×40", "pieces": 13, "kg": "10.500" } ],
      "warehouse_posted": true
    }
  ]
}
```

### POST /api/workshop/otk-blanks/{blank_id}/account/
**@deprecated** — предпочтителен `POST /api/workshop/otk-account/`. Учёт для одной заготовки (URL содержит `blank_id`).

### POST /api/workshop/otk-account/
Учёт v2: фактическая приёмка профилей + брак, списание из пула, зачисление на склад ГП.

**Request:**
```json
{
  "lines": [ { "profile_id": 3, "pieces": 13 }, { "profile_id": 7, "pieces": 5 } ],
  "defect": { "unit": "kg", "value": "10.5" },
  "shift_period": "day",
  "operator_id": 4, "chemist_id": 5, "packer_ids": [6],
  "defect_kg": "10.5", "defect_blank_id": 12,
  "comment": ""
}
```
Брак в штуках (альтернатива): `"defect": { "unit": "pieces", "value": "2", "profile_id": 3 }` — пересчёт `defect_kg = pieces × weight_kg_per_piece`.

| Поле | Тип | Обязательно |
|---|---|---|
| lines | array<{profile_id: number, pieces: number>0}> | да |
| shift_period | string enum (§2.15) | да |
| operator_id / chemist_id | number, nullable | нет |
| packer_ids | number[] | нет |
| defect_kg | string decimal | нет |
| defect_blank_id | number | нет (если есть defect_kg) |
| comment | string | нет |

**Валидация на бэке:**
```
consumed_kg = Σ(lines.pieces × profile.weight_kg_per_piece) + defect_kg
consumed_kg <= pool.remaining_kg (+ eps)
profile.blank_id == URL/lines blank_id пула, profile.is_active == true
```

**Response 201:**
```json
{ "id": 900, "blank_id": 12, "consumed_kg": "45.000", "remaining_kg_after": "175.000", "warehouse_posted": true }
```

**Транзакция на бэке:** списать `pool.remaining_kg`; увеличить склад ГП (`warehouse/gp-stock`) на `pieces`/`pieces × weight`; вернуть `defect_kg` в `prepared-blanks`; создать сессию + строки; запись в `warehouse/operations/` с `kind: "otk_account"`; WS `warehouse`, `otk`.

**Errors:** `400` — `{ "error": "Профиль не привязан к этой заготовке", "profile_id": 3, "expected_blank_id": 12 }`, недостаточно кг в пуле.

---

## 17. Склад готовой продукции

### GET /api/warehouse/gp-stock/
Остатки ГП по `profile_id`/`blank_id` (шт и кг).

**Query:** `profile_id`, `blank_id`, `page`, `page_size`.
**Response 200:**
```json
{ "items": [ { "product_id": 5, "product_name": "Профиль 6м", "blank_id": 12, "blank_name": "ПВХ белая смесь", "pieces": 340, "available_pieces": 300 } ] }
```

### GET /api/warehouse/batches/
Партии склада (список остатков с деталями качества/упаковки).

**Query:** `page`, `page_size`, `status` (§2.12), `inventory_form` (§2.13), `stock_bucket` (например `reworked`), `ordering`.
**Response 200:**
```json
{
  "items": [
    {
      "id": 200, "product_id": 5, "product_name": "Профиль 6м", "batch": "B-2026-05-001",
      "available_quantity": "50", "quality": "good", "defect_reason": "",
      "status": "available", "inventory_form": "unpacked",
      "pieces_per_package": null, "packages_count": null, "date": "2026-06-01"
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 12 }
}
```

### POST /api/warehouse/batches/package/
Упаковать остаток склада (ручной — упаковки).

**Request:**
```json
{ "warehouse_batch_id": 200, "pieces_per_package": 10, "packages_count": 5, "comment": "" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| warehouse_batch_id | number | да |
| pieces_per_package | number ≥ 1 | да |
| packages_count | number ≥ 1 | да |
| comment | string | нет |

**Response 201:** обновлённая/новая упакованная партия.
**Errors:** `400` — недостаточно остатка (`pieces_per_package × packages_count > available_quantity`).

### GET /api/warehouse/operations/
Единая лента движений склада ГП (для вкладки «История»).

**Query:** `page`, `page_size`, `ordering`, `date_from`, `date_to` (ISO date), `product_id`, `blank_id`, `kind` (см. §2.14, можно через запятую).
**Response 200:**
```json
{
  "items": [
    {
      "id": "op-1", "at": "2026-06-02T14:00:00Z", "kind": "sale", "kind_label": "Продажа",
      "direction": "out", "product_id": 5, "product_name": "Профиль 6м",
      "blank_id": 12, "blank_name": "ПВХ белая смесь",
      "pieces": 2, "kg": null, "packages": 0,
      "warehouse_batch_id": 200, "gp_package_id": null,
      "sale_id": 88, "order_id": 42, "return_id": null,
      "label": null, "actor": "Иванов И.И.", "comment": null
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 30 }
}
```

### Deprecated (возвращают `410 Gone`):
- `GET /api/warehouse/gp-unpacked-balance/`
- `GET /api/warehouse/gp-packages/`
- `POST /api/warehouse/gp-packages/`

---

## 18. Линии

### GET /api/lines/
**Auth:** Bearer, `access=production`. **Query:** `page`, `page_size`, `eligible_for_production_batch`, `eligible_for_recipe_run` (фильтр линий с открытой сменой без паузы).

**Response 200:**
```json
{
  "items": [
    {
      "id": 1, "name": "Линия 1", "code": "L1", "is_active": true,
      "shift_is_open": true, "shift_is_paused": false, "shift_pause_reason": null,
      "shift_snapshot": { "height": 12.5, "width": 30, "angle_deg": 45, "opened_at": "2026-06-02T08:00:00Z", "opened_by_name": "Иванов" }
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 4 }
}
```

### GET /api/lines/{id}/
**Response 200:** объект линии (как выше).

### POST /api/lines/
**Request:** `{ "name": "Линия 2", "code": "L2", "is_active": true }`
**Response 201:** созданная линия.

### PATCH /api/lines/{id}/
**Response 200:** обновлённая линия.

### DELETE /api/lines/{id}/
**Response:** `204`.

### POST /api/lines/{id}/open/
Открыть смену на линии.

**Request:**
```json
{ "height": 12.5, "width": 30, "angle_deg": 45, "comment": "", "session_title": "" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| height / width / angle_deg | number | да |
| comment / session_title | string | нет |

**Response 201:** `{ "line_id": 1, "shift_id": 900, "opened_at": "..." }`
**Errors:** `409` — на линии уже открыта смена (личная смена пользователя не мешает).

### POST /api/lines/{id}/close/
Закрыть смену пользователя на линии.

**Request:** `{ "height": 12.5, "width": 30, "angle_deg": 45, "comment": "" }`
**Response 200:** `{ "line_id": 1, "closed_at": "..." }`

### PATCH /api/lines/{id}/shift-params/
Обновить параметры открытой смены на линии.

**Request:** `{ "height": 13.0 }`
**Response 200:** обновлённый снимок.

### POST /api/lines/{id}/shift-pause/
**Request:** `{ "reason": "Обед" }`
**Response 200/201:** `{ "line_id": 1, "is_paused": true, "pause_reason": "Обед" }`

### POST /api/lines/{id}/shift-resume/
**Request:** `{}`
**Response 200:** `{ "line_id": 1, "is_paused": false }`

### GET /api/lines/{id}/history/
**Response 200:**
```json
{ "items": [ { "action": "open", "date": "2026-06-02", "time": "08:00", "height": 12.5, "width": 30, "angle_deg": 45, "comment": "" } ] }
```

### GET /api/lines/history/
Общая лента истории по всем линиям.

**Query:** `page`, `page_size`.
**Response 200:** пагинация как у `shifts/history/` — `{ items, meta: { page, pages, total } }`.

### GET /api/lines/{lineId}/history/session/?open_event_id={id}
Таймлайн одной сессии смены (от открытия до закрытия).

**Response 200:** `{ "open": {...}, "close": {...} | null, "updates": [...] }`

**Errors (раздел «Линии»):** `409` — уже открыта смена на линии (личная смена не блокирует).

---

## 19. Смены и журнал активности

### GET /api/shifts/my/
Текущая открытая **личная** смена пользователя (`line = null`; смена на линии не возвращается).

**Auth:** Bearer, `access=my_shift`.
**Response 200:** `{ "shift": { "id": 900, "opened_at": "2026-06-02T08:00:00Z", "closed_at": null, "status": "open", "notes_count": 2 } | null }`

### POST /api/shifts/open/
Открыть смену. Без `line_id` — только личная (`line: null`). С `line_id` — как смена на линии (тогда `height`/`width`/`angle_deg` обязательны).

**Request:** `{ "line_id": null, "comment": "" }` или `{ "line_id": 1, "height": 12.5, "width": 30, "angle_deg": 45 }`
**Response 201:** объект смены.
**Errors:** `409` — уже открыта личная смена, или уже открыта смена на этой же линии.

### POST /api/shifts/close/
Закрыть свою открытую смену (личную либо указанной линии).

**Request:** `{ "line_id": null, "comment": "" }`
**Response 200:** объект закрытой смены.

### POST /api/shifts/notes/
Заметка к личной открытой смене.

**Request:** `{ "note": "Проверил линию 1" }`
**Response 201:** `{ "id": 1, "note": "Проверил линию 1", "created_at": "..." }`
**Errors:** `404` — нет открытой личной смены.

### GET /api/shifts/notes/
Заметки текущей открытой личной смены.

**Response 200:** `{ "items": [ { "id": 1, "note": "...", "created_at": "..." } ] }` (пустой массив, если смены нет).

### GET /api/shifts/
Список смен (с фильтрами, для админа/отчётов).

**Auth:** `access=shifts` (или суперпользователь) для чужих смен.
**Query:** `date_from`, `date_to`, `line`, `user`.
**Response 200:**
```json
{
  "items": [
    { "id": 900, "line": 1, "line_name": "Линия 1", "opened_at": "2026-06-02T08:00:00Z", "closed_at": "2026-06-02T20:00:00Z", "status": "closed", "user_name": "Иванов И.И.", "notes_count": 2 }
  ],
  "meta": { "page": 1, "pages": 2, "total": 25 }
}
```

### GET /api/shifts/{id}/
**Response 200:** деталь смены — `id`, `line`, `line_name`, `line_label`, `opened_at`, `closed_at`, `status`, `comment`, `notes_count`, `notes[]`.

### GET /api/shifts/history/
История смен текущего пользователя.

**Query:** `page`, `page_size`.
**Response 200:** `{ "items": [...], "meta": { "page": 1, "pages": 3, "total": 50 } }`

### GET /api/shifts/complaints/
Жалобы (ShiftComplaint).

**Auth:** `access=my_shift` или `access=shifts` (или суперпользователь), иначе `403`.
**Query:** `date` (`YYYY-MM-DD`), `date_from`, `date_to`, `author_id`/`author`, `mentioned_user_id`/`mentioned_user`.
**Response 200:**
```json
{ "items": [ { "id": 1, "body": "Не работает линия 2", "created_at": "...", "author_name": "Иванов", "shift_opened_at": "...", "mentioned_user_ids": [3, 4] } ], "meta": {...} }
```

### POST /api/shifts/complaints/
**Request:** `{ "body": "Не работает линия 2", "mentioned_user_ids": [3, 4], "shift_id": 900 }`
| Поле | Тип | Обязательно |
|---|---|---|
| body | string | да |
| mentioned_user_ids | number[] | нет |
| shift_id | number | нет, только своя открытая смена |

**Response 201:** созданная жалоба.

### GET /api/activity/my/
Журнал действий текущего пользователя (для вкладки «Моя смена»).

**Query:** `shift_id` (либо `date_from`/`date_to`, не комбинировать с `shift_id`).
**Response 200:**
```json
{
  "items": [
    { "id": 1, "action": "create", "action_display": "Создал", "summary": "Приход: Пыма — 1 кг, 25.05.2026 16:23", "entity_type": "materials.materialbatch", "entity_id": 12, "occurred_at": "2026-05-25T16:23:00+06:00", "shift_id": 900, "has_detail": true }
  ],
  "meta": { "page": 1, "pages": 1, "total": 20 }
}
```
`summary` — человекочитаемая строка без технических деталей (не `POST /api/...`, не сырые ISO-даты в скобках).

### GET /api/activity/my/{id}/
Детальная запись журнала (личная).

**Response 200:** объект как в списке + `payload: { changes: [ { path, type, old, new, old_display, new_display } ], meta: {}, snapshot: { before: {}, after: {} } }`, `field_labels: { "quantity_initial": "Начальное количество" }`.

### GET /api/activity/{id}/
Детальная запись журнала (права `shifts` / суперпользователь) — та же структура, для чужих записей.

### GET /api/activity/?user_id={id}
Журнал действий конкретного сотрудника (для администратора).

**Errors (раздел «Смены»):** `403` — нет прав (`my_shift`/`shifts`), `404` — нет открытой смены для заметок/жалоб без `shift_id`, `409` — конфликт открытия смены.

---

## 20. Аналитика

### GET /api/analytics/summary/
**Auth:** Bearer, `access=analytics`.
**Query:** `year` (обяз.), `month?`, `day?`, `date_from?`, `date_to?`, `line_id?`, `client_id?`, `trend_group=day|month`.

**Response 200:**
```json
{
  "cards": {
    "revenue_total": "65051.00",
    "purchase_total": "26291.00",
    "other_expenses_total": "2200.00",
    "period_expenses_total": "28491.00",
    "operating_expenses_total": "28491.00",
    "sales_count": 42,
    "sold_units_total": 980,
    "client_debt_total": "125000.00"
  },
  "trends": [
    { "period": "2026-05", "revenue": 65051, "purchase_total": 26291, "other_expenses_total": 2200 }
  ],
  "sales_by_profile": [
    { "profile_id": 5, "profile_name": "Профиль 6м", "sold_units": 300, "revenue": "38700.00" }
  ],
  "otk_summary": { "accepted": 980, "defect": 45, "defect_percent": 4.4 },
  "warehouse_summary": { "available": 1200, "reserved": 80 },
  "debt_as_of": "current_outstanding_by_sale_date_in_period",
  "packaging_summary": { "packages_count": 5, "pieces_total": 30, "weight_kg_total": 73.5 }
}
```
Пустой период — `200` с нулями, не `404`. P&L: выручка = `revenue_total`; расходы = `period_expenses_total` (= приход сырья + прочие расходы за период); прибыль = выручка − расходы.

### GET /api/analytics/revenue-details/
Те же query, что у `summary/`.

**Response 200:**
```json
{ "total": "65051.00", "items": [ { "date": "2026-05-15", "client_name": "ООО Стройка", "product_name": "Профиль 6м", "quantity": 10, "unit_price": "129.00", "revenue": "1290.00" } ] }
```

### GET /api/analytics/sales-cost-details/
**Response 200:** `{ "total": "12600.00", "items": [ { "date": "2026-05-15", "order_number": "42", "product_name": "Профиль 6м", "quantity": 10, "cost_per_unit": "95.50", "total_cost": "955.00" } ] }`

### GET /api/analytics/product-other-expenses-details/
Прочие расходы товара (профиля) по продажам за период — сумма `extra_*` × шт.

**Response 200:** `{ "total": "260.00", "items": [ { "date": "2026-05-15", "product_name": "Профиль 6м", "quantity": 10, "unit_other_expenses": "26.00", "total_other_expenses": "260.00" } ] }`

### GET /api/analytics/product-unit-costs/
Справочник профилей и себестоимости за штуку (не расход за период).

**Response 200:** `{ "items": [ { "profile_id": 5, "profile_name": "Профиль 6м", "code": "GP-6W", "is_active": true, "unit_cost_per_piece": "125.50" } ] }`

### GET /api/analytics/production-cost-details/
**Response 200:** `{ "total": "12600.00", "items": [ { "date": "2026-05-15", "production_batch_id": 501, "profile_name": "Профиль 60×40", "line_name": "Линия 1", "quantity_pieces": 200, "total_meters": 1200, "total_cost": "12600.00" } ] }`

### GET /api/analytics/purchase-details/
**Response 200:** `{ "total": "26291.00", "items": [ { "date": "2026-05-25", "material_name": "Пыма", "supplier_name": "ИП Сидоров", "quantity": 100, "unit_price": "50.00", "total_amount": "5000.00" } ] }`

### GET /api/analytics/profit-details/
**Response 200:**
```json
{
  "totals": { "profit": "38351.00", "revenue": "65051.00", "sales_cost": "26700.00" },
  "items": [ { "date": "2026-05-15", "order_number": "42", "client_name": "ООО Стройка", "profile_name": "Профиль 6м", "revenue": "1290.00", "sales_cost": "955.00", "profit": "335.00", "sale_id": 88 } ]
}
```

### GET /api/analytics/debt-details/
Те же query, что у `summary/`. `total_debt` = `cards.client_debt_total` при тех же query.

**Response 200:**
```json
{ "total_debt": "125000.00", "items": [ { "client_id": 5, "client_name": "ООО Стройка", "debt_amount": "80000.00", "sales_count": 3, "oldest_debt_date": "2026-04-10" } ] }
```

### GET /api/analytics/otk-details/
**Response 200:** `{ "items": [...] }` (детализация к `otk_summary`).

### GET /api/analytics/writeoff-details/
**Response 200:** `{ "items": [...] }` (детализация списаний).

### GET /api/analytics/other-expenses/
Прочие расходы (произвольные, не связанные с сырьём).

**Auth:** `access=analytics`. **Query:** `year` (обяз.), `month?`, `day?`.
**Response 200:**
```json
{
  "items": [
    { "id": 1, "name": "Аренда офиса", "amount": "5000.00", "date": "2026-05-15", "status": "pending" },
    { "id": 2, "name": "Доставка", "amount": "1200.00", "date": "2026-05-10", "status": "accepted" }
  ]
}
```
Фильтр — по **дате расхода** (`date`) в границах query. Отклонённые (`rejected`) — не отдаются (удаляются).

### POST /api/analytics/other-expenses/
**Request:** `{ "name": "Ремонт", "amount": "3500", "date": "2026-05-20" }`
| Поле | Тип | Обязательно |
|---|---|---|
| name | string, не пусто | да |
| amount | string/number decimal > 0 | да |
| date | string (ISO date) | да |

**Response 201:** созданная запись, `status: "pending"`.

### POST /api/analytics/other-expenses/{id}/accept/
Принять расход — сумма учитывается в `period_expenses_total` за месяц/день **даты расхода** (`date`), не даты создания.

**Request:** `{}`
**Response 200:** запись со `status: "accepted"`.
**Errors:** `404`, `409` — уже принят.

### POST /api/analytics/other-expenses/{id}/reject/
Отклонить — запись удаляется из БД (не возвращается в GET).

**Request:** `{}`
**Response:** `200 { "deleted": true }` или `204`.

**Errors (раздел «Аналитика»):** `403` — нет доступа `analytics`, `400` — невалидные `year`/`month`/`day`.

---

## 21. Сводная таблица эндпоинтов

| Метод | Путь | Auth | Назначение |
|---|---|---|---|
| POST | /api/auth/login | нет | Логин |
| POST | /api/auth/logout | Bearer | Логаут (blacklist refresh) |
| GET | /api/me | Bearer | Текущий пользователь + доступы |
| GET/POST | /api/users/ | users | Список/создание сотрудников |
| GET/PATCH/DELETE | /api/users/{id}/ | users | Карточка/изменение/удаление сотрудника |
| PATCH | /api/users/{id}/access/ | users | Доступные вкладки пользователя |
| GET/POST | /api/roles/ | users | Список/создание ролей |
| GET/PATCH/DELETE | /api/roles/{id}/ | users | Роль |
| GET/POST | /api/clients/ | clients | Список/создание клиентов |
| GET/PATCH | /api/clients/{id}/ | clients | Клиент |
| GET | /api/clients/{id}/history/ | clients | История клиента |
| GET | /api/clients/{id}/profile/ | clients | Профиль клиента (сводка) |
| GET | /api/client-financial-summary/ | clients | Финсводка клиента |
| GET/POST | /api/payments/ | payments | Список/создание оплат |
| GET/PATCH | /api/payments/{id}/ | payments | Оплата |
| PATCH | /api/payments/{id}/cancel/ | payments | Отмена оплаты |
| GET | /api/payments/summary/ | payments | Сводка по клиенту |
| GET | /api/payments/select-sources/ | payments | Источники для формы оплаты |
| GET/POST | /api/orders/ | orders/client_orders | Список/создание заявок |
| GET | /api/orders/select-sources/ | orders | Справочники формы заявки |
| GET/PATCH | /api/orders/{id}/ | orders | Заявка |
| PATCH | /api/orders/{id}/status/ | orders | Смена статуса заявки |
| PATCH | /api/orders/{id}/cancel/ | orders | Отмена заявки |
| GET | /api/orders/{id}/history/ | orders | История заявки |
| GET | /api/orders/{id}/waybill/ | orders | Накладная |
| POST | /api/orders/{id}/approve/ | orders | Принять заявку |
| POST | /api/orders/{id}/reject/ | orders | Отклонить заявку |
| POST | /api/orders/{id}/recheck/ | orders | Перепроверить заявку |
| GET/POST | /api/sales/ | sales | Список/создание продаж |
| POST | /api/sales/preview/ | sales | Предпросчёт продажи |
| GET/PATCH | /api/sales/{id}/ | sales | Продажа |
| PATCH | /api/sales/{id}/status/ | sales | Смена статуса продажи |
| PATCH | /api/sales/{id}/cancel/ | sales | Отмена продажи |
| GET | /api/sales/{id}/credit-check/ | sales | Проверка кредитлимита |
| GET | /api/sales/{id}/waybill/ | sales | Накладная |
| GET | /api/sales/{id}/receipt/ | sales | Чек |
| GET | /api/sales/select-sources/ | sales | Остатки для продажи |
| GET/POST | /api/raw-materials/ | materials | Список/создание сырья |
| GET/PATCH/DELETE | /api/raw-materials/{id}/ | materials | Сырьё |
| GET/POST | /api/incoming/ | materials | Приходы сырья |
| GET | /api/materials/balances/ | materials | Остатки сырья |
| GET | /api/materials/movements/ | materials | Журнал движения сырья |
| GET/POST | /api/chemistry/elements/ | chemistry | Справочник химии |
| GET/PATCH/DELETE | /api/chemistry/elements/{id}/ | chemistry | Элемент химии |
| GET | /api/chemistry/balances/ | chemistry | Остатки химии |
| GET | /api/chemistry/batches/ | chemistry | Партии химии |
| POST | /api/chemistry/elements/produce/ | chemistry | Выпуск полуфабриката |
| GET/POST | /api/workshop/blanks/ | chemistry | Справочник заготовок |
| GET/PATCH/DELETE | /api/workshop/blanks/{id}/ | chemistry | Заготовка |
| GET | /api/workshop/prepared-blanks/ | chemistry | Остатки цеха |
| POST | /api/workshop/prepared-blanks/{id}/add-barrel/ | chemistry | Добавить бочку |
| GET/POST | /api/workshop/blank-production-runs/ | production | Выпуск заготовки в производство |
| GET | /api/workshop/blank-production-runs/{id}/ | production | Прогон выпуска |
| GET/POST | /api/recipes/ | production | Справочник рецептов |
| GET/PATCH/DELETE | /api/recipes/{id}/ | production | Рецепт |
| GET | /api/recipes/{id}/availability/ | production | Проверка достаточности |
| GET/POST | /api/plastic-profiles/ | production | Справочник профилей |
| GET/PATCH/DELETE | /api/plastic-profiles/{id}/ | production | Профиль |
| GET/POST | /api/batches/ | production | Партии производства |
| GET/PATCH/DELETE | /api/batches/{id}/ | production | Партия |
| POST | /api/batches/{id}/submit-for-otk/ | production | В очередь ОТК |
| POST | /api/batches/{id}/otk_accept/ | otk | Приёмка ОТК (legacy) |
| GET | /api/production/requests/ | production | Готовые заявки к запуску |
| POST | /api/production/requests/{id}/start/ | production | Старт производства по заявке |
| GET | /api/otk/pending/ | otk | Очередь ОТК |
| GET | /api/workshop/otk-blanks/ | otk | Пул ОТК по заготовкам |
| GET | /api/workshop/otk-blanks/intakes/ | otk | История приходов в пул |
| GET | /api/workshop/otk-accounting/ | otk | История учёта ОТК |
| POST | /api/workshop/otk-account/ | otk | Учёт ОТК v2 |
| GET | /api/warehouse/gp-stock/ | warehouse | Остатки ГП |
| GET | /api/warehouse/batches/ | warehouse | Партии склада |
| POST | /api/warehouse/batches/package/ | warehouse | Упаковка |
| GET | /api/warehouse/operations/ | warehouse | Лента движений склада |
| GET/POST/PATCH/DELETE | /api/lines/ , /api/lines/{id}/ | production | Линии |
| POST | /api/lines/{id}/open/ | production | Открыть смену на линии |
| POST | /api/lines/{id}/close/ | production | Закрыть смену на линии |
| PATCH | /api/lines/{id}/shift-params/ | production | Параметры смены линии |
| POST | /api/lines/{id}/shift-pause/ | production | Пауза |
| POST | /api/lines/{id}/shift-resume/ | production | Снять паузу |
| GET | /api/lines/{id}/history/ , /api/lines/history/ | production | История линии/ленты |
| GET | /api/lines/{id}/history/session/ | production | Таймлайн сессии смены |
| GET | /api/shifts/my/ | my_shift | Текущая личная смена |
| POST | /api/shifts/open/ , /api/shifts/close/ | my_shift | Открыть/закрыть смену |
| GET/POST | /api/shifts/notes/ | my_shift | Заметки смены |
| GET | /api/shifts/ , /api/shifts/{id}/ | shifts | Список/деталь смен |
| GET | /api/shifts/history/ | my_shift | История смен пользователя |
| GET/POST | /api/shifts/complaints/ | my_shift/shifts | Жалобы |
| GET | /api/activity/my/ , /api/activity/my/{id}/ | my_shift | Личный журнал активности |
| GET | /api/activity/ , /api/activity/{id}/ | shifts | Журнал активности (админ) |
| GET | /api/analytics/summary/ | analytics | Сводка P&L |
| GET | /api/analytics/*-details/ | analytics | Детализации по каждой карточке |
| GET/POST | /api/analytics/other-expenses/ | analytics | Прочие расходы |
| POST | /api/analytics/other-expenses/{id}/accept/ | analytics | Принять расход |
| POST | /api/analytics/other-expenses/{id}/reject/ | analytics | Отклонить расход |
| WS | /ws/operational/?token= | Bearer (query) | Real-time уведомления об изменениях |

---

## 22. Deprecated / снятые с фронта эндпоинты (для справки, возвращают 410)

- `GET /api/warehouse/gp-unpacked-balance/`
- `GET /api/warehouse/gp-packages/`
- `POST /api/warehouse/gp-packages/`
- `POST /api/workshop/blank-production-runs/{id}/otk-defect/`
- `POST /api/workshop/blank-production-runs/{id}/accept-gp/`
- `POST /api/workshop/otk-blanks/{blank_id}/account/` (заменён на `POST /api/workshop/otk-account/`)
