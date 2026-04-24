# Backend API — финальная справка для frontend

Основано только на текущем коде (`config/api_urls.py`, `apps/sales/views.py`, `apps/sales/serializers.py`, `apps/sales/models.py`, `apps/sales/state_machine.py`, `apps/sales/filters.py`, `apps/sales/order_sync.py`, `apps/sales/payment_status.py`, `config/pagination.py`, `config/exceptions.py`, `config/api_numbers.py`).

**Базовый префикс:** `https://<host>/api/` (см. `config/urls.py`: `path('api/', include('config.api_urls'))`).

**Аутентификация:** JWT (`REST_FRAMEWORK` → `JWTAuthentication`). Без валидного токена — 401.

**Права:** `IsAdminOrHasAccess` + `required_access_key` на каждом ViewSet (ключи: `clients`, `client_orders`, `sales`, `payments`, `returns`, `defects`). При отказе — 403 (формат см. `config/exceptions.py`).

---

## Общие соглашения

### Пагинация списков (ModelViewSet list)

Класс: `config.pagination.StandardResultsSetPagination` (`PAGE_SIZE` 20, `page_size` в query, `max_page_size` 100).

**Ответ:**

```json
{
  "items": [ ... ],
  "meta": {
    "total": 0,
    "total_count": 0,
    "page": 1,
    "perPage": 20,
    "page_size": 20,
    "totalPages": 0,
    "total_pages": 0
  },
  "links": { "next": null, "previous": null }
}
```

**Query:** `page`, `page_size` (опционально).

Эндпоинты **без** этой обёртки: кастомные `list`/`retrieve` с `Response({...})` напрямую (см. ниже: `client-financial-summary`, `clients/{id}/history/`, `orders/{id}/history/`, `payments/summary/`, часть actions).

### Числа в JSON

Многие денежные/количественные поля сериализуются через `api_decimal_str` → **строки** (стабильный десятичный текст), не float.

### Ошибки от `_err()` в sales views

Тело (типично): `{ "code": "<CODE>", "error": "<текст>", "detail": "<текст>" }`, иногда `errors`. HTTP-статус задаётся в вызове (часто 400, 404, 409, 422).

### Ошибки DRF / валидации сериализатора

Обрабатываются `dias_exception_handler` (`config/exceptions.py`): могут быть поля `code`, `error`, `detail`, список `errors` с `{field, message}`.

---

# 1. Клиенты

## Назначение

Справочник клиентов, кредитные поля, деактивация вместо DELETE; агрегированная история по клиенту; финансовая сводка.

## Endpoints

| Method | URL | Описание |
|--------|-----|----------|
| GET | `/api/clients/` | Список (пагинация) |
| POST | `/api/clients/` | Создание |
| GET | `/api/clients/{id}/` | Детально |
| PATCH / PUT | `/api/clients/{id}/` | Обновление |
| DELETE | `/api/clients/{id}/` | **405** — удаление отключено |
| GET | `/api/clients/{id}/history/` | История без пагинации (вложенные списки) |
| GET | `/api/client-financial-summary/?client_id=` | Фин. сводка (не под `/clients/`) |

### Query params

- **GET `/api/clients/`:** фильтры `ClientFilter`: `is_active` (boolean). Поиск: `search` по `name`, `inn`, `contact`, `email`, `messenger`. Сортировка: `ordering` по полям `id`, `name`.
- **GET `/api/client-financial-summary/`:** **`client_id`** (обязателен) — иначе `code: MISSING_PARAM`.

### Request / Response JSON — `ClientSerializer`

**Поля ответа (list/detail/create/patch):**

| Поле | Тип в API | Обязательность при create | Read-only |
|------|-----------|---------------------------|-----------|
| id | number | авто | да |
| name | string | да | нет |
| contact | string | нет | нет |
| phone | string | нет | нет |
| phone_alt | string | нет | нет |
| inn | string | нет | нет |
| address | string | нет | нет |
| email | string | нет | нет |
| messenger | string | нет | нет |
| client_type | string | нет | нет |
| notes | string | нет | нет |
| is_active | boolean | нет (default true в модели) | нет |
| status | string `active` / `inactive` | нет | **да** (вычисляется из `is_active`) |
| sales_count | number | нет | **да** (аннотация в `get_queryset`) |
| sales_total | string (decimal) | нет | **да** |
| has_sales | boolean | нет | **да** |
| credit_limit | number \| null | нет | нет |
| credit_limit_mode | string `soft` \| `hard` | нет | нет |

**Не отправлять на create/patch как «истину»:** `status`, `sales_count`, `sales_total`, `has_sales` — перезаписываются/игнорируются согласно DRF (read-only).

**Ошибки create:** неактивный клиент не создаётся из других модулей здесь не проверяется; при PATCH `is_active: false` клиент остаётся в БД.

### DELETE `/api/clients/{id}/`

**405:** `code: DELETE_DISABLED`, текст про `is_active: false`.

### GET `/api/clients/{id}/history/`

**Ответ (плоский объект, без `items`):** см. `ClientViewSet.history`.

Ключи верхнего уровня: `client_id`, `client_name`, `orders` (массив **OrderSerializer**), `sales` (**SaleSerializer**), `payments` (**PaymentSerializer**, только `status=active`), `returns` (**ReturnSerializer**), `total_revenue`, `total_ordered`, `total_paid`, `total_paid_gross`, `total_refunded`, `client_debt_money`, `client_advance_amount`, `has_unshipped_goods`, `overdue_orders_count`, `total_profit`, `defect_revenue`, `credit_limit`, `credit_limit_mode`, `credit_available`, `credit_is_over_limit`, `credit_warning` — суммы/числа как **строки** через `api_decimal_str` где указано в коде.

**Важно:** в этой сводке **нет** поля `payment_status` (оно есть только в `client-financial-summary` и в serializer заказа/продажи).

### GET `/api/client-financial-summary/`

**Query:** `client_id` (обязателен).

**Ответ:** `client_id`, `client_name`, **`payment_status`** (`unpaid` \| `partially_paid` \| `paid` \| `overpaid` \| `refunded` — логика `apps/sales/payment_status.py`), `total_revenue`, `total_cost`, `total_profit`, `defect_revenue`, `total_paid_gross`, `total_refunded`, `total_paid_net`, `client_debt_money`, `client_advance_amount`, `credit_limit`, `credit_limit_mode`, `credit_available`, `is_over_limit`, `credit_warning` (числовые поля — строки через `api_decimal_str`, кроме булевых/режима).

**Ошибки:** `MISSING_PARAM`, `NOT_FOUND` (клиент).

## Select-sources

У **ClientViewSet** отдельного `select-sources` **нет**. Для выбора клиента в других вкладках используются:

- `/api/orders/select-sources/` — `clients[]`
- `/api/sales/select-sources/` — `clients[]`

## Статусы

Отдельного enum «статус клиента» в БД нет; в API поле **`status`**: `active` / `inactive` по `is_active`.

## Actions

Отдельных URL `cancel` / `status` у клиента **нет** (только PATCH).

## Связи

- Клиент → заявки: `Order.client` → `GET /api/orders/?client_id=<id>`
- Клиент → продажи: `Sale.client` → `GET /api/sales/?client_id=<id>`
- Клиент → оплаты: `Payment.client` → `GET /api/payments/?client_id=<id>`

## Примеры

**List:** `GET /api/clients/?is_active=true&page=1` → `{ items: [ { id, name, ..., status, sales_count, sales_total, ... } ], meta, links }`.

**Detail:** `GET /api/clients/1/` → один объект сериализатора.

**Create:** `POST /api/clients/` body: `{ "name": "ООО Ромашка", "inn": "123", "is_active": true }`.

**Patch:** `PATCH /api/clients/1/` body: `{ "is_active": false }`.

---

# 2. Заявки (Orders)

## Назначение

Заявка клиента, строки, статусы, резервы партий, накладная HTML, отмена.

## Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/orders/` |
| POST | `/api/orders/` |
| GET | `/api/orders/{id}/` |
| PATCH / PUT | `/api/orders/{id}/` |
| DELETE | `/api/orders/{id}/` → **405** |
| GET | `/api/orders/select-sources/` |
| PATCH | `/api/orders/{id}/status/` |
| GET | `/api/orders/{id}/waybill/` (HTML) |
| GET | `/api/orders/{id}/history/` |
| POST | `/api/orders/{id}/reserve/` |
| POST | `/api/orders/{id}/release-reserve/` |
| GET | `/api/orders/{id}/reservations/` |
| PATCH | `/api/orders/{id}/cancel/` |

Дополнительно (связанный read-only ресурс):

| Method | URL |
|--------|-----|
| GET | `/api/order-reservations/` (список всех резервов; фильтры) |

### Query params — `GET /api/orders/`

`OrderFilter`: `client_id`, `status`, `source_type`, `date_from`, `date_to` (на поле `date`). Плюс `ordering` (`id`, `date`), пагинация.

### Request / Response — `OrderSerializer` + вложенные `lines`

**Поля заказа:**

| Поле | Read-only | Примечание |
|------|-----------|------------|
| id | да | |
| order_number | **да** | автоген при create: `ORD-{year}-{n:04d}` |
| date | нет | при отсутствии — сегодня |
| client | нет | FK id |
| client_name | да | |
| source_type | нет | см. статусы источника |
| comment | нет | |
| status | нет | см. переходы |
| created_by | нет | обычно с сервера |
| created_by_name | да | |
| responsible_user | нет | |
| responsible_user_name | да | |
| created_at, updated_at | да | |
| lines | нет (вложенный массив) | при create — список строк; при update — опционально |
| total_amount, shipped_amount, remaining_amount | да | строки decimal |
| paid_amount, payment_status, debt_amount, refund_amount | да | из `order_payment_metrics` только **активные** платежи |
| has_company_debt_by_goods | да | property модели |

**`OrderLineSerializer` (элемент `lines`):**

| Поле | Read-only |
|------|-----------|
| id | нет (при update можно передать для сопоставления) |
| product, product_type, profile, ordered_quantity, unit_price, comment | нет |
| shipped_quantity, reserved_quantity | **да** |
| remaining_quantity, available_to_ship, remaining_to_reserve, line_total | **да** (строки) |

### GET `/api/orders/{id}/` — дополнительно к телу сериализатора

- `linked_entities`: `client`, `responsible_user` — `{ id, label }`
- `available_status_transitions`: массив строк — следующий статус из `ORDER_TRANSITIONS`
- `available_actions`: `{ set_status, reserve, release_reserve, cancel, waybill, history }` (booleans по коду)

### PATCH `/api/orders/{id}/status/`

**Body:** `{ "status": "<new>" }`.

**Ошибки:** `MISSING_STATUS` (400), `INVALID_STATUS_TRANSITION` (422, `validate_order_transition`), `ORDER_STATUS_BLOCKED` (422, `validate_order_for_new_status` для `shipped` \| `partially_shipped` \| `closed`).

### POST `/api/orders/{id}/reserve/`

**Body:** `{ "order_line_id", "warehouse_batch_id", "quantity", "comment"? }`.

**Ошибки:** `MISSING_FIELD`, `NOT_FOUND`, `RESERVATION_ERROR` (422, `ValueError` из `reserve_order_line`).

**Ответ 201:** объект `OrderReservationSerializer`.

### POST `/api/orders/{id}/release-reserve/`

**Body:** `{ "reservation_id" }`.

**Ошибки:** `MISSING_FIELD`, `NOT_FOUND`, `RESERVATION_ERROR`.

### PATCH `/api/orders/{id}/cancel/`

**Body:** допускается пустой объект; логика не читает поля из body в коде.

**Ответ:** `{ "status": "canceled", "reservations_released": <int>, "order": <OrderSerializer> }`.

**Ошибки:** `INVALID_TRANSITION` (422) — `validate_order_transition` или `validate_order_cancel` (есть не draft/canceled продажи).

### GET `/api/orders/{id}/history/`

`{ "order": ..., "sales": [...], "payments": [...], "returns": [...] }` — без пагинации.

## Select-sources — `GET /api/orders/select-sources/`

**Ответ:**

```json
{
  "clients": [{ "id": 1, "label": "Имя" }],
  "profiles": [{ "id": 1, "label": "Профиль" }]
}
```

**Frontend:** подписи — `label`, в заявку/строку отправлять **`id`** в поля `client`, `lines[].profile`.

## Статусы заявки (`Order.status`)

| Value | Label (models) |
|-------|----------------|
| new | Новая |
| confirmed | Подтверждена |
| in_progress | В работе |
| partially_shipped | Частично отгружена |
| shipped | Отгружена |
| closed | Закрыта |
| canceled | Отменена |

**Разрешённые переходы** (`ORDER_TRANSITIONS` в `state_machine.py`):

- `new` → `confirmed`, `canceled`
- `confirmed` → `in_progress`, `canceled`
- `in_progress` → `partially_shipped`, `shipped`, `canceled`
- `partially_shipped` → `shipped`, `closed`, `canceled`
- `shipped` → `closed`
- `closed`, `canceled` → никуда

**Доп. правила** при установке `shipped` \| `partially_shipped` \| `closed` (`validate_order_for_new_status` + `validate_order_close`):

- Перед проверкой вызывается пересчёт `shipped_quantity` строк заявки из **SaleLine** (продажи с `linked_order` и статусом `partially_shipped` \| `shipped` \| `closed`).
- `shipped`: все строки с `shipped_quantity >= ordered_quantity`; нет резервов со статусом `active`.
- `partially_shipped`: есть строки с заказом > 0; есть отгрузка > 0; не все строки полностью отгружены; нет перевышающего отгруженного.
- `closed`: нет активных резервов; по каждой строке отгружено ≥ заказано.

### `source_type`

| Value | Label |
|-------|-------|
| cashier | Кассир |
| manager | Менеджер |
| boss | Руководитель |
| other | Другое |

## Actions (резюме)

| Action | Method | URL |
|--------|--------|-----|
| Смена статуса | PATCH | `/api/orders/{id}/status/` |
| Резерв | POST | `/api/orders/{id}/reserve/` |
| Снять резерв | POST | `/api/orders/{id}/release-reserve/` |
| Отмена заявки | PATCH | `/api/orders/{id}/cancel/` |
| Список резервов заявки | GET | `/api/orders/{id}/reservations/` |

## Связи

- Заявка → клиент: `client_id`
- Заявка → продажи: `GET /api/sales/?linked_order=<order_id>` (фильтр `linked_order` в `SaleFilter`)
- Заявка → оплаты: `GET /api/payments/?linked_order=<id>`
- Заявка → возвраты: поле `Return.linked_order` (фильтр вручную или через историю)

## Примеры

**Create:** `POST /api/orders/`

```json
{
  "client": 1,
  "source_type": "manager",
  "comment": "",
  "lines": [
    {
      "product": "Профиль X",
      "product_type": "профиль",
      "profile": 2,
      "ordered_quantity": "10.0000",
      "unit_price": "100.50",
      "comment": ""
    }
  ]
}
```

**Patch status:** `PATCH /api/orders/5/status/` `{ "status": "confirmed" }`.

**Reserve:** `POST /api/orders/5/reserve/` `{ "order_line_id": 12, "warehouse_batch_id": 88, "quantity": "5", "comment": "" }`.

---

# 3. Продажи (Sales)

## Назначение

Продажа (в т.ч. многострочная через `sale_lines` в **теле create**), склад, статусы, оплаты-метрики, отмена, HTML.

## Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/sales/` |
| POST | `/api/sales/` |
| GET | `/api/sales/{id}/` |
| PATCH / PUT | `/api/sales/{id}/` |
| DELETE | `/api/sales/{id}/` → **405** |
| GET | `/api/sales/select-sources/` |
| POST / PATCH | `/api/sales/{id}/cancel/` |
| PATCH | `/api/sales/{id}/status/` |
| GET | `/api/sales/{id}/waybill/` (HTML) |
| GET | `/api/sales/{id}/receipt/` (HTML) |
| GET | `/api/sales/{id}/credit-check/` |

### Query params — `GET /api/sales/`

`SaleFilter`: `client_id`, `sale_status`, `is_defect_sale`, `linked_order`, `date_from`, `date_to`. Плюс `ordering`: `id`, `date`.

### Поля `SaleSerializer` (ядро)

Поля модели в ответе + вычисляемые:

- **Read-only в Meta:** `profit`, `revenue`, `cost`, `total_meters`, `inventory_form`, `warehouse_batch_id`, `profile_name`, `stock_quality`, `created_at`, **`sale_lines`**, `warehouse_stock_applied`, `credit_limit_bypassed`, `updated_at`, `payment_status`, `paid_amount`, `debt_amount`, `refund_amount`.
- **`sale_lines`:** только на чтение; заполняются при **POST** через **нестандартный** ключ верхнего уровня **`sale_lines`** в JSON (см. `create()` — читается из `initial_data`, не из `validated_data`).
- **`warehouse_mutation`:** в модели есть, в **serializer не входит** → **не отдаётся API** (внутренний снимок отката склада).

**Важно для UI (не показывать как редактируемые после привязки склада):** при `perform_update` если у продажи уже есть `warehouse_batch_id`, запрещены смена партии, изменение `quantity` / `quantity_input`, изменение `stock_form` / `piece_pick` (см. `SaleViewSet.perform_update`).

**`inventory_form`:** read-only, из партии или из `stock_form`.

**`piece_pick` / `stock_form`:** для create с выбранной партией выставляются/валидируются в `validate()` (логика `normalize_inventory_form` / `normalize_piece_pick` из `apps/warehouse/stock_ops.py`).

**Режим продажи:** `sale_mode`: `pieces` \| `packages`; нормализация `sale_unit`.

### POST create — обязательность строк

После create должна существовать **минимум одна** `SaleLine`: либо из массива **`sale_lines`**, либо автосоздание «legacy» строки из шапки (`_build_legacy_sale_line`). Иначе `ValidationError` по `sale_lines`.

**Разрешённые ключи внутри каждого элемента `sale_lines`:**  
`order_line`, `product`, `warehouse_batch`, `stock_form`, `piece_pick`, `quantity`, `unit_price`, `defect_flag`, `comment`.  
Любые другие ключи → ошибка `sale_lines` с текстом про недопустимые поля.

### Склад при create/update

Если статус — отгрузка (`partially_shipped`, `shipped`, `closed`), сначала **`validate_sale_ship`** (обычная продажа, `is_defect_sale=false`), затем **`apply_warehouse_for_sale`**.

**`validate_sale_ship` (обычная продажа):** без перехода в отгрузочные статусы, если нет партии **ни в одной** `sale_lines[].warehouse_batch`, **ни** в `Sale.warehouse_batch`. Если **хотя бы у одной** строки указана своя партия — режим «по строкам»: каждая строка с **`quantity` > 0** обязана иметь **`warehouse_batch`**; партия в статусе **`available`**, качество **`good`**; количество не больше остатка партии. **Legacy через шапку** (`Sale.warehouse_batch` + `Sale.quantity`) разрешён **только если ни у одной строки нет** `warehouse_batch`. Продажи брака (`is_defect_sale=true`) этими правилами **не** ограничиваются (в т.ч. сценарий `DefectRecord.sell`).

**`apply_warehouse_for_sale`:** списание **по каждой** строке с партией и `quantity` > 0; если строки есть, но **ни одна** не несёт партии, и в шапке задана партия — одно списание по **`Sale.warehouse_batch`** и **`Sale.quantity`**.

При наличии `linked_order` после отгрузки вызывается `auto_fulfill_sale_lines_after_shipping`: для каждой строки продажи с **`order_line` + `warehouse_batch`** исполняются резервы только этой строки заявки и этой партии; иначе один вызов по шапке (legacy).

### GET `/api/sales/{id}/` — дополнительно

`linked_entities` (client, linked_order, warehouse_batch), `available_status_transitions` из `SALE_TRANSITIONS`, `available_actions`: `set_status`, `credit_check` (если есть клиент), `waybill`, `receipt`.

### POST|PATCH `/api/sales/{id}/cancel/`

**Ошибки:** `ALREADY_CANCELED` (422), `HAS_RETURNS` (409), `HAS_PAYMENTS` (409), `INVALID_TRANSITION` (422).

### PATCH `/api/sales/{id}/status/`

**Body:** `{ "status": "...", "force_credit_override": true|false? }` (override проверяется только как строка `1/true/yes`).

**Ошибки:** `MISSING_STATUS`, `INVALID_STATUS_TRANSITION`, `SHIP_BLOCKED` (текст из `validate_sale_ship`: нет партии, неполные строки, не `available`/`good`, превышение остатка), `CREDIT_LIMIT_BLOCKED`, `WAREHOUSE_APPLY`.

Перед сменой на отгрузочные статусы вызывается **`validate_sale_ship`**; после сохранения статуса — `apply_warehouse_for_sale`, затем при наличии `linked_order` — `auto_fulfill_sale_lines_after_shipping` (см. выше).

## Select-sources — `GET /api/sales/select-sources/`

**Query:** `client_id` (опционально) — фильтрует список заявок по клиенту.

**Ответ:** `clients`, `orders`, `warehouse_batches` — элементы `{ id, label }`. Партии: только **`available`** и **`good`**.

**Отправлять в backend:** id в `client`, `linked_order`, `warehouse_batch`; для строк внутри create — `warehouse_batch`, `order_line`.

## Статусы продажи (`sale_status`)

| Value | Label |
|-------|-------|
| draft | Черновик |
| confirmed | Подтверждена |
| partially_shipped | Частично отгружена |
| shipped | Отгружена |
| closed | Закрыта |
| canceled | Отменена |

**Default в модели:** `draft` (если не передан другой при создании). В `SaleSerializer.create` при отсутствии `sale_status` в данных также используется **`draft`**.

**Переходы** (`SALE_TRANSITIONS`):

- `draft` → `confirmed`, `canceled`
- `confirmed` → `partially_shipped`, `shipped`, `canceled`
- `partially_shipped` → `shipped`, `closed`, `canceled`
- `shipped` → `closed`
- `closed`, `canceled` → нет

## `payment_status` (продажа)

Значения: `unpaid`, `partially_paid`, `paid`, `overpaid`, `refunded` (`payment_status.py`).  
Сумма к оплате: если есть `sale_lines` — сумма `line_total` по строкам; иначе **`revenue`** шапки.

## Actions

| Action | URL |
|--------|-----|
| Отмена | `POST` или `PATCH` `/api/sales/{id}/cancel/` |
| Статус | `PATCH /api/sales/{id}/status/` |
| Кредит-проверка | `GET /api/sales/{id}/credit-check/` |

Резервов на этом ViewSet **нет** (резервы через заявку).

## Связи

- Продажа → клиент: `client`
- Продажа → заявка: `linked_order`
- Продажа → оплаты: `Payment.linked_sale`; метрики из связанных платежей со `status=active`
- Продажа → возвраты: `Return.sale`
- Строки: `sale_lines[].order_line` → строка заявки

## Примеры

**Create (с строками):** ключ **`sale_lines`** рядом с полями шапки:

```json
{
  "client": 1,
  "linked_order": 5,
  "warehouse_batch": 10,
  "product": "",
  "quantity": "4",
  "price": "500.00",
  "date": "2026-04-25",
  "sale_status": "draft",
  "sale_lines": [
    {
      "order_line": 12,
      "warehouse_batch": 10,
      "stock_form": "packed",
      "piece_pick": "from_sealed_package",
      "quantity": "4",
      "unit_price": "500.00",
      "defect_flag": false,
      "comment": ""
    }
  ]
}
```

(Точные значения `stock_form` / `piece_pick` должны соответствовать партии — иначе 400 от складской валидации.)

Чтобы сразу списать склад при создании, укажите отгрузочный статус (`partially_shipped` / `shipped` / `closed`); иначе оставьте `draft` и переведите статус отдельным `PATCH /api/sales/{id}/status/`.

**List:** `GET /api/sales/?client_id=1&sale_status=shipped` → пагинация.

---

# 4. Оплаты (Payments)

## Назначение

Денежные движения: привязка к клиенту / заявке / продаже / возврату (для refund).

## Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/payments/` |
| POST | `/api/payments/` |
| GET | `/api/payments/{id}/` |
| PATCH / PUT | `/api/payments/{id}/` |
| DELETE | `/api/payments/{id}/` → **405** |
| POST / PATCH | `/api/payments/{id}/cancel/` |
| GET | `/api/payments/summary/?client_id=` |

**Select-sources** на PaymentViewSet **нет**.

### Query params — фильтр

`client_id`, `payment_type`, `payment_method`, `date_from`, `date_to`, `linked_order`, `linked_sale`.

### Поля `PaymentSerializer`

| Поле | Read-only |
|------|-----------|
| id | да |
| payment_number | **да** (автоген `PAY-{year}-{n:04d}`) |
| date | нет |
| client | нет |
| client_name | да |
| linked_order, linked_sale, linked_return | нет |
| payment_type | нет |
| amount | нет (в JSON ответа — **строка** через `to_representation`) |
| payment_method | нет |
| status | нет (`active` \| `canceled`) |
| manual_refund_reason | нет |
| comment | нет |
| created_by | нет |
| created_by_name | да |
| created_at | да |

**Правила `validate`:** согласованность клиента с заявкой/продажей/возвратом; для `payment_type=refund` нужен **`linked_return`** или непустой **`manual_refund_reason`**.

**PATCH/PUT после создания:** нельзя менять `amount`, `client`, `linked_sale`, `linked_order`, `linked_return`, `payment_type` (ошибка валидации). Отмена суммы/привязок — только **`/api/payments/{id}/cancel/`**. Запись со `status=canceled` **нельзя** редактировать.

### GET `/api/payments/summary/`

**Query:** `client_id` обязателен.

**Ответ:** `client_id`, `client_name`, `total_paid_gross`, `total_refunded`, `total_paid_net`, `total_revenue`, `client_debt_money`, `client_advance_amount` (все суммы — строки).

**Ошибки:** `MISSING_CLIENT`, `NOT_FOUND`.

**Примечание:** здесь **нет** `payment_status` (в отличие от `client-financial-summary`).

## Статусы записи оплаты

| Value | Label |
|-------|-------|
| active | Активна |
| canceled | Отменена |

Переходов state machine **нет** — отмена только через action `cancel`.

## Типы и способы

**payment_type:** `prepayment`, `payment`, `surcharge`, `refund`.

**payment_method:** `cash`, `transfer`, `card`, `other`.

## Actions

`POST` или `PATCH` `/api/payments/{id}/cancel/` — выставляет `status=canceled`. Ошибка: `ALREADY_CANCELED` (422).

## Связи

- Оплата → клиент, заявка, продажа, возврат (FK).
- Метрики по заявке/продаже учитывают только `status=active`.

## Пример create

```json
{
  "client": 1,
  "linked_sale": 20,
  "payment_type": "payment",
  "amount": "1500.00",
  "payment_method": "transfer",
  "date": "2026-04-25",
  "comment": ""
}
```

**Refund с возвратом:**

```json
{
  "client": 1,
  "payment_type": "refund",
  "amount": "100.00",
  "payment_method": "transfer",
  "linked_return": 3,
  "date": "2026-04-25"
}
```

---

# 5. Возвраты (Returns)

## Назначение

Возврат по продаже; строки с обязательным `sale_line`. Создаётся в **`draft`**; склад/брак/переделка — только после **`POST`/`PATCH` `/api/returns/{id}/complete/`**.

## Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/returns/` |
| POST | `/api/returns/` |
| GET | `/api/returns/{id}/` |
| PATCH / PUT | `/api/returns/{id}/` |
| DELETE | `/api/returns/{id}/` → **405** |
| GET | `/api/returns/select-sources/?sale_id=` |
| POST / PATCH | `/api/returns/{id}/complete/` |
| POST / PATCH | `/api/returns/{id}/cancel/` |
| GET | `/api/returns/{id}/waybill/` (HTML) |

### Фильтр

`sale_id`, `client_id`, `date_from`, `date_to`.

### Поля `ReturnSerializer` / `ReturnLineSerializer`

**Return:** `return_number` (read-only, автоген `RET-{year}-{n:04d}`), `date`, `status`, `sale` (**обязателен** при create), `sale_order_number` (read-only), `linked_order`, `invoice_number`, `return_reason`, `comment`, `created_by`, `created_by_name` (read-only), `created_at` (read-only), `lines`, `client_name` (read-only).

**При create:** минимум одна строка в **`lines`**; каждая строка — `ReturnLineSerializer`: **`sale_line` обязателен**; `product` read-only (подставляется с sale_line). В теле create **нельзя** передать `status: completed` — будет ошибка валидации; статус всегда сохраняется как **`draft`**.

**ReturnLine:** `return_target`: `warehouse` \| `defect` \| `rework`; `condition_type`: `good` \| `damaged` \| `defect`.

### POST|PATCH `/api/returns/{id}/complete/`

Только из **`draft`**. Выполняет `_process_return_line` по каждой строке (склад/брак/переделка), затем `status=completed`.

**Ошибки:** `INVALID_STATE` (не draft), `NO_LINES`.

### GET `/api/returns/{id}/`

Дополнительно: `linked_entities` (sale, client, linked_order), `downstream_links`, `available_status_transitions`: **[]**, `available_actions`: `{ "waybill": true, "complete": <bool> }` (`complete` = true только в **draft**).

### PATCH/PUT при `status=completed`

Разрешено менять только **`comment`**, **`return_reason`**, **`invoice_number`**. Остальные поля (включая `lines`, `sale`, `status`, количества и т.д.) — ошибка валидации.

### cancel

`POST` или `PATCH` `/api/returns/{id}/cancel/`: если был `completed`, вызывается `rollback_return_document`; затем `status=canceled`. Из **draft** — отмена без отката складских эффектов.

**Ошибка:** `ALREADY_CANCELED` (422).

## Select-sources — `GET /api/returns/select-sources/`

**Query:** `sale_id` (опционально). Если передан — заполняется `sale_lines`.

**Ответ:** `sales` — `{ id, label }` (`order_number` / клиент); `sale_lines` — `{ id, label }` (`product × quantity`).

**В create возврата:** отправлять `sale` = id продажи; в строках — `sale_line` = id из `sale_lines`.

## Статусы возврата

| Value | Label | Переходы в API |
|-------|-------|----------------|
| draft | Черновик | Default; далее `complete` → completed |
| completed | Проведён | После `complete`; отмена — `cancel` |
| canceled | Отменён | Через `cancel` |

## Связи

- Возврат → продажа: `sale` (PROTECT).
- Возврат → заявка: `linked_order` (опционально).
- Строка → `sale_line` → продажа / `SaleLine`.
- При `return_target=defect` / `rework` создаются **DefectRecord** / **ReworkRequest** (см. `_process_return_line` в serializers).

## Пример create

```json
{
  "sale": 15,
  "linked_order": 5,
  "date": "2026-04-25",
  "return_reason": "Брак",
  "lines": [
    {
      "sale_line": 101,
      "quantity": "2",
      "return_target": "defect",
      "condition_type": "defect",
      "comment": ""
    }
  ]
}
```

Затем: `POST /api/returns/{id}/complete/`.

---

# 6. Брак и переделка

## 6.1 Брак — `DefectRecordViewSet` (`/api/defects/`)

### Назначение

Учёт брака; источники ОТК / склад / возврат / ручной; действия send-to-rework, writeoff, sell. Завершение переделки с созданием партии — только **`POST /api/rework-requests/{id}/complete/`** (endpoint `complete-rework` на браке отключён).

### Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/defects/` |
| POST | `/api/defects/` |
| GET | `/api/defects/{id}/` |
| PATCH / PUT | `/api/defects/{id}/` |
| DELETE | `/api/defects/{id}/` → **405** |
| GET | `/api/defects/select-sources/` |
| POST | `/api/defects/{id}/send-to-rework/` |
| POST | `/api/defects/{id}/complete-rework/` → **405** (`USE_REWORK_COMPLETE`) |
| POST | `/api/defects/{id}/writeoff/` |
| POST | `/api/defects/{id}/sell/` |

### Фильтр

`source_type`, `status`, `profile_id`.

### Поля `DefectRecordSerializer`

Включая `source_label` (read-only), `profile_name` (read-only), `created_by_name` (read-only). Числовые поля в ответе — строки через `api_decimal_str`.

**Учёт остатка (ERP):**

| Поле | Смысл |
|------|--------|
| **`original_quantity_pcs`** | Исходно принято шт (база; read-only в API) |
| **`quantity_pcs`** | **Остаток** шт, доступный к продаже / списанию / переделке |
| **`sold_quantity_pcs`** | Накопительно продано шт |
| **`written_off_quantity_pcs`** | Накопительно списано шт |
| **`sent_to_rework_quantity_pcs`** | Накопительно отправлено в переделку шт |
| **`available_quantity_pcs`** | Read-only, дублирует остаток (`quantity_pcs`) для UI |
| **`display_quantity_label`** | Read-only, например `3 шт` или `1.5 кг` по остатку |
| `quantity_kg`, `kg_coefficient` | Кг по строке брака (при частичных операциях кг может уменьшаться пропорционально шт) |

Инвариант: `original_quantity_pcs ≈ quantity_pcs + sold + written_off + sent_to_rework` (пересчёт `quantity_pcs` после операций).

**`writeoff_reason`:** обязателен при `PATCH` в статус `written_off` (см. `validate`). Для **`POST .../writeoff/`** причина обязательна всегда.

### GET `/api/defects/{id}/`

`linked_entities.source` (для `source_type=return`), `rework_requests`, `available_status_transitions` из `DEFECT_TRANSITIONS`, `available_actions`: `send_to_rework`, **`complete_rework` всегда false** (завершение — через rework-requests), `writeoff`, `sell`. Для **`sell` / `writeoff` / `send_to_rework`** флаги **`false`**, если остаток **`quantity_pcs`** (≈ **`available_quantity_pcs`**) ≤ 0 — по закрытой записи действия недоступны.

### POST `send-to-rework/`

**Body (опционально):** `{ "quantity": "3" }` — сколько **шт** остатка отправить в переделку; если не указано — весь текущий остаток.

Создаётся **`ReworkRequest`** с входом на указанное количество; у брака увеличивается **`sent_to_rework_quantity_pcs`**, пересчитывается **`quantity_pcs`** (остаток). Если отправлен **весь** остаток одной операцией — проверяется переход в **`sent_to_rework`**; если часть — статус брака остаётся **`on_stock`** (или **`reworked`** / **`new`** по текущему значению). В **`rework_request`** — **`quantity_pcs`** / **`quantity_kg`** по отправленной порции (кг пропорционально остатку, если было на браке).

Ошибки: `REWORK_ACTIVE` (422), `INVALID_STATUS` (422), **`NO_QUANTITY`**, **`INVALID_DECIMAL`**, **`INVALID_QUANTITY`**, **`QTY_TOO_HIGH`**.

**Ответ:** `{ "defect": ..., "rework_request": ... }` — объект `rework_request` в формате **`ReworkRequestSerializer`** (см. §6.2), включая `display_quantity` / `defect_*`.

**Пример фрагмента `rework_request`:**

```json
{
  "id": 1,
  "rework_number": "RWK-2026-0001",
  "defect_record": 1,
  "defect_record_id": 1,
  "defect_product_name": "60 мм белый",
  "defect_quantity_pcs": "2",
  "defect_quantity_kg": null,
  "defect_reason": "трещина",
  "defect_source_type": "warehouse",
  "defect_source_label": "Складская партия #5",
  "display_quantity": "2",
  "display_quantity_label": "2 шт",
  "quantity_pcs": "2",
  "quantity_kg": "0",
  "status": "pending"
}
```

### POST `complete-rework/` (на **DefectRecord**)

**405:** тело с `code: USE_REWORK_COMPLETE` — завершение переделки только через `POST /api/rework-requests/{id}/complete/`.

### POST `writeoff/`

**Body:** `{ "writeoff_reason": "...", "quantity": "2" }` — **`writeoff_reason`** обязателен; **`quantity`** опционален: если не передан — списывается **весь** остаток; иначе указанное число шт (≤ остатка).

Частичное списание: растёт **`written_off_quantity_pcs`**, остаётся **`on_stock`** при ненулевом остатке. Если после операции остаток = 0: при **только** списании (весь объём ушёл списанием) — **`written_off`**; при **смешанных** каналах (продажа + списание и т.п.) — **`closed`** («Закрыт»). После каждой операции: **`recompute_remaining_pcs()`** → **`apply_terminal_status_from_counters()`** → **`save()`**.

При связи **`warehouse_batch`** со строкой брака со склада списывается **`quantity`** той же партии (как при продаже со склада), чтобы остаток партии не расходился с учётом брака.

**Ошибки:** `MISSING_REASON`, `INVALID_STATUS`, `INVALID_DECIMAL`, `INVALID_QUANTITY`, `QTY_TOO_HIGH`, **`WAREHOUSE_APPLY`** (422 — не удалось списать партию).

**Ответ:** тело **`DefectRecordSerializer`** (обновлённая запись).

### POST `sell/`

**Обязательные поля:** `client_id`, `price` (> 0), `quantity` (> 0, **≤** текущего остатка **`quantity_pcs`**).

Частичная продажа: создаётся `Sale` + `SaleLine` на указанное количество; у брака **`sold_quantity_pcs`** увеличивается, остаток пересчитывается; статус **`on_stock`**, пока остаток > 0; при продаже **всего** исходного объёма (накопительно, один канал «продажа») — **`sold`**; при **смешанных** операциях с нулевым остатком — **`closed`**.

Если у брака задана **`warehouse_batch`**, в продажу подставляется та же партия и выполняется списание со склада (**`apply_warehouse_for_sale`**) в одной транзакции с обновлением счётчиков брака.

**Ошибки:** `MISSING_CLIENT`, `MISSING_PRICE`, `MISSING_QUANTITY`, `INVALID_DECIMAL`, `INVALID_PRICE`, `INVALID_QUANTITY`, `QTY_TOO_HIGH`, `INVALID_STATUS`, **`WAREHOUSE_APPLY`** (422).

**Пример смешанного сценария (остаток 0 → не `on_stock`):** было **`original_quantity_pcs` = 5**; **`POST .../sell/`** `quantity=2`; затем **`POST .../writeoff/`** `quantity=3`, `writeoff_reason=...` → **`quantity_pcs`** = **`"0"`**, **`available_quantity_pcs`** = **`"0"`**, **`status`** = **`closed`**, **`display_quantity_label`** = **`"0 шт"`**.

**Ответ 201:** `{ "sale_id", "sale_order_number" }`.

**Пример частичной продажи (остаток 5 → продали 2):**

Запрос: `{ "client_id": 1, "price": "100.00", "quantity": "2" }`  
После: `quantity_pcs` = `"3"`, `sold_quantity_pcs` = `"2"`, `status` = `on_stock`.

## Select-sources — `GET /api/defects/select-sources/`

`return_lines` — id = **ReturnLine.id**; `warehouse_defect_batches` — id партии склада с `quality=defect`.

## Статусы брака (`DefectRecord.status`)

| Value | Label | Переходы (`DEFECT_TRANSITIONS`) |
|-------|-------|-----------------------------------|
| new | Новый | on_stock, sent_to_rework, written_off |
| on_stock | На складе брака | sent_to_rework, sold, written_off |
| sent_to_rework | Передан на переработку | reworked, on_stock |
| reworked | Переработан | sold, written_off, sent_to_rework |
| sold | Продан | — |
| written_off | Списан | — |
| closed | Закрыт | — |

Финальный статус при **нулевом остатке**: только продажи → **`sold`**; только списание → **`written_off`**; только переделка (весь объём ушёл в **`sent_to_rework_quantity_pcs`**) → **`sent_to_rework`**; **два и более каналов** (например, часть продана и часть списана) → **`closed`**. Статус **`on_stock`** при **`quantity_pcs` = 0** не используется.

## `source_type`

| Value | Label |
|-------|-------|
| otk | ОТК |
| qc | ОТК / контроль качества |
| warehouse | Склад |
| return | Возврат клиента |
| manual | Вручную |

---

## 6.2 Переделка — `ReworkRequestViewSet` (`/api/rework-requests/`)

### Endpoints

| Method | URL |
|--------|-----|
| GET | `/api/rework-requests/` |
| POST | `/api/rework-requests/` |
| GET | `/api/rework-requests/{id}/` |
| PATCH / PUT | `/api/rework-requests/{id}/` |
| DELETE | `/api/rework-requests/{id}/` → **405** |
| GET | `/api/rework-requests/select-sources/` |
| POST | `/api/rework-requests/{id}/start/` |
| POST | `/api/rework-requests/{id}/complete/` |
| POST | `/api/rework-requests/{id}/cancel/` |

### Фильтр

`status`, `original_sale`.

### Поля `ReworkRequestSerializer`

**Модель (запись в БД):** `product`, **`quantity_pcs`** (вход шт с брака, nullable), **`quantity_kg`** (вход кг), `output_quantity_kg`, `loss_kg`, `conversion_rate`, `status`, `comment`, FK: `return_doc`, `defect_record`, `original_sale`, `result_warehouse_batch`, `created_by`.

**Read-only (в т.ч. для list/detail):** `rework_number`, `created_at`, `updated_at`, `rework_loss_kg`, `recovered_output`, `return_doc_number`, `defect_status`, `original_sale_number`, `result_warehouse_batch_label`, **`defect_record_id`**, **`defect_product_name`**, **`defect_quantity_pcs`**, **`defect_quantity_kg`**, **`defect_reason`**, **`defect_source_type`**, **`defect_source_label`**, **`display_quantity`**, **`display_quantity_label`**.

- **`display_quantity` / `display_quantity_label`:** по сохранённой переделке — приоритет **`quantity_pcs`** > 0, иначе **`quantity_kg`** > 0 (строки и подпись вида `2 шт` или `1.5 кг`).
- **`defect_*`:** снимок с текущей связанной записи **DefectRecord** (продукт, количества, причина, источник).

**Create (`POST /api/rework-requests/`):** обязателен **`defect_record`**; номер `RWK-...` генерируется автоматически; **`quantity_pcs`** и **`quantity_kg`** сервер **всегда подставляет из брака** (как при `send-to-rework`), нельзя сохранить нули при положительном количестве на браке.

### GET `/api/rework-requests/` и GET `/api/rework-requests/{id}/`

Список и деталь отдают один и тот же набор полей сериализатора (включая все **`defect_*`**, **`display_*`**, **`quantity_pcs`**).

**Пример элемента списка (фрагмент):**

```json
{
  "id": 1,
  "rework_number": "RWK-2026-0001",
  "defect_record": 1,
  "defect_record_id": 1,
  "defect_product_name": "60 мм белый",
  "defect_quantity_pcs": "2",
  "display_quantity": "2",
  "display_quantity_label": "2 шт",
  "status": "pending"
}
```

### GET `/api/rework-requests/{id}/`

Помимо тела сериализатора: `linked_entities`, `available_status_transitions` (`REWORK_TRANSITIONS`), `available_actions`: `start`, `complete`, `cancel`.

### POST `complete/`

**Body:** обязательны пары полей: **`output_quantity`** или **`output_quantity_kg`**; **`loss_quantity`** или **`loss_kg`**; опционально **`quality`**: `good` \| `defect` (значения констант `WarehouseBatch.QUALITY_*`).

Создаётся **`WarehouseBatch`** (результат), пишется в `result_warehouse_batch`, статус переделки `completed`, у связанного **DefectRecord** статус принудительно **`reworked`**.

**Ошибки:** `INVALID_STATUS`, `MISSING_FIELDS`, `INVALID_QUALITY`, `NO_DEFECT`, `QTY_BOUNDS`.

**Примечание:** в коде комментарий про «кг», но в `complete` переменные названы `output_pcs` / `loss_pcs` и пишутся в поля `output_quantity_kg` / `loss_kg` модели — **фактически одни и те же числа проходят в БД в поля с суффиксом _kg** (имена полей модели **legacy**/исторические).

### POST `cancel/`

При отмене из `in_progress`/`pending` и если брак был `sent_to_rework`, брак возвращается в **`on_stock`**.

## Select-sources — `GET /api/rework-requests/select-sources/`

**`defect_records`** — массив объектов (не только `label`):

| Поле | Описание |
|------|----------|
| `id` | id **DefectRecord** |
| `label` | Человекочитаемо: `продукт — кол-во — причина — источник` (например `60 мм белый — 2 шт — трещина — Склад`) |
| `product_name` | Продукт |
| `quantity_pcs` | Строка decimal или `null`, если шт нет |
| `quantity_kg` | Строка или `null` |
| `defect_reason` | Причина |
| `source_type` | Код источника |
| `source_label` | Техническая подпись источника (партия / ReturnLine / ОТК) |
| `status` | Статус брака |

**Пример элемента `defect_records`:**

```json
{
  "id": 1,
  "label": "60 мм белый — 2 шт — трещина — Склад",
  "product_name": "60 мм белый",
  "quantity_pcs": "2",
  "quantity_kg": null,
  "defect_reason": "трещина",
  "source_type": "warehouse",
  "source_label": "Складская партия #5",
  "status": "on_stock"
}
```

- `original_sales` — `{ id, label }` продажи.
- `returns` — `{ id, label }` возврата.
- `result_warehouse_batches` — `{ id, label }` партии (good, available).

## Статусы переделки

| Value | Label | Переходы (`REWORK_TRANSITIONS`) |
|-------|-------|----------------------------------|
| pending | Ожидает | in_progress, canceled |
| in_progress | В работе | completed, canceled |
| completed | Завершено | — |
| canceled | Отменено | — |

---

# 7. Связи (сводная схема)

| От | К | Как в API |
|----|---|-----------|
| Клиент | Заявки | `Order.client_id`; фильтр `client_id` |
| Клиент | Продажи | `Sale.client_id`; фильтр `client_id` |
| Клиент | Оплаты | `Payment.client_id`; фильтр |
| Заявка | Продажи | `Sale.linked_order`; фильтр `linked_order` |
| Заявка | Оплаты | `Payment.linked_order` |
| Заявка | Резервы | `POST .../reserve/`; `GET .../reservations/`; `GET /api/order-reservations/?order_line=` |
| Заявка | Строки | `lines[].id` = `order_line_id` в резерве и в `SaleLine.order_line` |
| Продажа | Оплаты | `Payment.linked_sale` |
| Продажа | Возвраты | `Return.sale` |
| Продажа | Строки | `sale_lines` (read-only в ответе; при create — в `initial_data`) |
| Возврат | Строка продажи | `ReturnLine.sale_line` |
| Возврат | Брак | `DefectRecord.source_type=return`, `source_id=return_line.id` |
| Брак | Переделка | `ReworkRequest.defect_record`; action `send-to-rework` на defect |
| Переделка | Склад | `ReworkRequest.result_warehouse_batch` после `complete` |

---

# 8. Поля «не для frontend» / internal

- **`Sale.warehouse_mutation`**: не в serializer → клиент не видит.
- **`piece_pick` / `stock_form` / `packaging` / `sale_unit`:** технически для складской логики; после привязки партии часть полей **нельзя** менять (см. `perform_update`).
- **`credit_limit_bypassed`:** выставляется сервером при `force_credit_override` на смене статуса продажи.
- **`warehouse_stock_applied`:** read-only, флаг применения списания.

---

# 9. Права доступа по вкладкам (ключи)

| Вкладка | `required_access_key` |
|---------|-------------------------|
| Клиенты | `clients` |
| Заявки, резервы | `client_orders` |
| Продажи, client-prices, price-lists | `sales` |
| Оплаты | `payments` |
| Возвраты | `returns` |
| Брак + переделки | `defects` |

---

*Документ сгенерирован по состоянию репозитория; при изменении ViewSet/serializers сверять код.*
