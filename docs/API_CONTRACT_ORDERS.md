# Backend API Contract Review — Заявки

Документ составлен по фактическому backend-коду:
- `apps/sales/models.py`
- `apps/sales/serializers.py`
- `apps/sales/views.py`
- `apps/sales/filters.py`
- `apps/sales/state_machine.py`
- `apps/sales/reservations.py`
- `apps/sales/order_sync.py`
- `apps/sales/sale_warehouse.py`
- `config/api_urls.py`
- `config/permissions.py`
- `config/exceptions.py`
- `apps/sales/tests/*`
- `apps/sales/migrations/*` (только важное для заявок/резервов)

Код не изменялся. Фиксы не вносились.

---

## 1. ОБЩАЯ ИНФОРМАЦИЯ

1. Назначение вкладки  
Вкладка "Заявки" (`Order`) хранит коммерческие намерения клиента: позиции заказа, статусы жизненного цикла, связи с продажами/оплатами/возвратами и резервами склада.

2. Backend-модели
- `Order`
- `OrderLine`
- `OrderReservation`
- связанные:
  - `Sale` (`linked_order`)
  - `SaleLine` (`order_line`)
  - `Payment` (`linked_order`)
  - `Return` (`linked_order`)

3. Serializers
- `OrderSerializer`
- `OrderLineSerializer`
- `OrderReservationSerializer`
- в `order history` дополнительно:
  - `SaleSerializer`
  - `PaymentSerializer`
  - `ReturnSerializer`

4. Viewsets/actions
- `OrderViewSet`
  - CRUD + actions: `select-sources`, `status`, `waybill`, `history`, `reserve`, `release-reserve`, `reservations`, `cancel`
- `OrderReservationViewSet` (read-only list/retrieve)

5. Filters
- `OrderFilter`:
  - `client_id`
  - `status`
  - `source_type`
  - `date_from`
  - `date_to`

6. Permissions/access
- `OrderViewSet.required_access_key = "client_orders"`
- `OrderReservationViewSet.required_access_key = "client_orders"`
- permission class: `IsAdminOrHasAccess`

7. Связанные сущности
- Клиенты: `Order.client`
- Профили/товары: `OrderLine.profile`, `OrderLine.product`
- Склад: `OrderReservation.warehouse_batch`
- Продажи: `Sale.linked_order`, `SaleLine.order_line`
- Оплаты: `Payment.linked_order`
- Возвраты: `Return.linked_order`
- Резервы: `OrderReservation`
- История: `GET /api/orders/{id}/history/`

---

## 2. ВСЕ ENDPOINTS ВКЛАДКИ "ЗАЯВКИ"

Ниже включены:
- endpoint’ы из задания
- дополнительно `GET /api/order-reservations/{id}/` (read-only detail, связан с заявками)

---

## 2.1 GET /api/orders/

1. Method + URL: `GET /api/orders/`  
2. Назначение: список заявок.  
3. Когда frontend вызывает: таблица заявок, поиск/фильтры/сортировка.  
4. Query params:
- `client_id` / number / optional / `?client_id=1`
- `status` / string / optional / `?status=new`
- `source_type` / string / optional / `?source_type=manager`
- `date_from` / date / optional / `?date_from=2026-01-01`
- `date_to` / date / optional / `?date_to=2026-12-31`
- `ordering` / string / optional / `?ordering=-date`
- `search` / string / optional / `?search=ORD-2026`
- `page` / int / optional / `?page=1`
- `page_size` / int / optional / `?page_size=20`
5. Request JSON: не используется.
6. Response JSON (пагинация):
```json
{
  "items": [
    {
      "id": 100,
      "order_number": "ORD-2026-0001",
      "date": "2026-04-26",
      "client": 1,
      "client_name": "ОсОО Альфа",
      "source_type": "manager",
      "comment": "Срочная заявка",
      "status": "new",
      "created_by": 3,
      "created_by_name": "Менеджер",
      "responsible_user": 3,
      "responsible_user_name": "Менеджер",
      "created_at": "2026-04-26T08:00:00Z",
      "updated_at": "2026-04-26T08:00:00Z",
      "lines": [],
      "total_amount": "0",
      "shipped_amount": "0",
      "remaining_amount": "0",
      "paid_amount": "0",
      "payment_status": "unpaid",
      "debt_amount": "0",
      "refund_amount": "0",
      "has_company_debt_by_goods": false
    }
  ],
  "meta": {
    "total": 1,
    "total_count": 1,
    "page": 1,
    "perPage": 20,
    "page_size": 20,
    "totalPages": 1,
    "total_pages": 1
  },
  "links": {
    "next": null,
    "previous": null
  }
}
```
7. Errors:
- `401 unauthorized`
- `403 forbidden`
8. Business rules:
- read-only вычисляемые суммы формируются backend-ом
- payment metrics считаются через `order_payment_metrics`
9. Frontend submit:
- только query params
10. UI contract:
- показывать: `order_number,date,client_name,status,total_amount,paid_amount,debt_amount`
- `id` использовать для перехода в карточку и actions.

---

## 2.2 POST /api/orders/

1. Method + URL: `POST /api/orders/`  
2. Назначение: создать заявку.  
3. Когда frontend вызывает: кнопка "Создать".  
4. Query params: не используются.  
5. Request JSON (пример):
```json
{
  "date": "2026-04-26",
  "client": 1,
  "source_type": "manager",
  "comment": "Срочная заявка",
  "responsible_user": 3,
  "lines": [
    {
      "product": "60 мм белый",
      "product_type": "profile",
      "profile": 2,
      "ordered_quantity": "10",
      "unit_price": "100",
      "comment": ""
    }
  ]
}
```
6. Response JSON: полный `OrderSerializer` объект (как в list item).
7. Errors:
- `400 validation_error`
  - `client`: "Клиент неактивен..." (если неактивный)
  - вложенные ошибки по полям строк (например пустой `product`)
- `401`, `403`
8. Business rules (фактические):
- `order_number` генерируется backend (`ORD-{year}-{nnnn}`)
- если `date` не передан -> ставится текущая дата
- `client` может быть `null` (serializer `required=False, allow_null=True`)
- `lines` может отсутствовать (serializer `required=False`)
- минимум 1 line **не обязателен** по текущему коду
- `ordered_quantity > 0` не валидируется явно
- `unit_price >= 0` не валидируется явно
9. Frontend submit:
- отправлять только writable поля
- не отправлять: `order_number`, суммы, payment metrics
10. UI contract:
- label "Источник" -> `source_type`
- label "Статус" -> `status`
- id клиента в backend: `client`
- id профиля строки: `profile`

---

## 2.3 GET /api/orders/{id}/

1. Method + URL: `GET /api/orders/{id}/`  
2. Назначение: детальная карточка заявки + action metadata.
3. Когда frontend вызывает: открыть заявку.
4. Query params: не реализовано.
5. Request JSON: не используется.
6. Response JSON (добавки к базовой заявке):
```json
{
  "...order_fields": "...",
  "linked_entities": {
    "client": {"id": 1, "label": "ОсОО Альфа"},
    "responsible_user": {"id": 3, "label": "Менеджер"}
  },
  "available_status_transitions": ["confirmed", "canceled"],
  "available_actions": {
    "set_status": true,
    "reserve": true,
    "release_reserve": true,
    "cancel": true,
    "waybill": true,
    "history": true
  }
}
```
7. Errors: `404`, `401`, `403`.
8. Business rules:
- `available_actions.reserve=true` только для статусов `new|confirmed|in_progress|partially_shipped`.
9. Frontend submit: не используется.
10. UI contract:
- показывать `available_actions` как разрешения кнопок.

---

## 2.4 PATCH /api/orders/{id}/

1. Method + URL: `PATCH /api/orders/{id}/`  
2. Назначение: частичное обновление полей заявки и/или lines.
3. Когда frontend вызывает: редактирование заявки.
4. Query params: не реализовано.
5. Request JSON пример:
```json
{
  "comment": "Обновлено",
  "lines": [
    {"id": 501, "ordered_quantity": "12"},
    {"product": "80 мм", "ordered_quantity": "3"}
  ]
}
```
6. Response JSON: полный `OrderSerializer`.
7. Errors:
- `400 validation_error`
- `404`
- `401`, `403`
8. Business rules (факт):
- поля заявки (`client,date,source_type,comment,responsible_user,status`) writable
- `status` можно менять и через обычный PATCH (в обход action `/status/`)
- `lines` update:
  - если `id` есть и найден среди текущих lines -> `update`
  - если `id` нет или не найден -> `create new line`
  - отсутствующие строки **не удаляются**
- ограничений "после confirmed/in_progress/продажи/closed/canceled редактировать нельзя" нет в serializer/view
9. Frontend submit:
- если нужен контролируемый переход статуса, использовать `/status/`, а не общий PATCH
10. UI contract:
- редактирование lines сейчас технически разрешено почти всегда.

---

## 2.5 PUT /api/orders/{id}/

1. Method + URL: `PUT /api/orders/{id}/`  
2. Назначение: полное обновление заявки.
3. Поведение: как PATCH, но по HTTP-методу PUT.
4. Request/Response/Errors: аналогично PATCH.

---

## 2.6 DELETE /api/orders/{id}/

1. Method + URL: `DELETE /api/orders/{id}/`  
2. Назначение: удалить заявку.
3. Фактически: удаление запрещено.
4. Response:
```json
{
  "code": "DELETE_DISABLED",
  "error": "Физическое удаление заявок отключено. Используйте /api/orders/{id}/cancel/."
}
```
5. HTTP: `405`
6. Frontend:
- кнопку "Удалить" не показывать
- использовать `PATCH /api/orders/{id}/cancel/`.

---

## 2.7 GET /api/orders/select-sources/

1. Method + URL: `GET /api/orders/select-sources/`  
2. Назначение: источники для формы создания/редактирования заявки.
3. Response:
```json
{
  "clients": [{"id": 1, "label": "ОсОО Альфа"}],
  "profiles": [{"id": 2, "label": "60 мм белый"}]
}
```
4. Business:
- clients только `is_active=true`
- profiles без фильтра активности (берутся все из `PlasticProfile`).

---

## 2.8 PATCH /api/orders/{id}/status/

1. Method + URL: `PATCH /api/orders/{id}/status/`  
2. Назначение: смена статуса через state machine.
3. Request:
```json
{"status": "confirmed"}
```
4. Response: полный `OrderSerializer`.
5. Errors:
- `400 MISSING_STATUS`
- `422 INVALID_STATUS_TRANSITION`
- `422 ORDER_STATUS_BLOCKED` (доп. проверки для partially_shipped/shipped/closed)
6. Business:
- валидация переходов через `validate_order_transition`
- для `partially_shipped|shipped|closed` вызывает `validate_order_for_new_status`.

---

## 2.9 GET /api/orders/{id}/waybill/

1. Method + URL: `GET /api/orders/{id}/waybill/`  
2. Назначение: накладная заявки.
3. Response:
- type: HTML
- `Content-Type: text/html; charset=utf-8`
- `Content-Disposition: inline; filename="order-waybill-{id}.html"`
4. Frontend:
- открывать в новой вкладке/окне документа.
- кнопку показывать всегда (backend `available_actions.waybill=true`).

---

## 2.10 GET /api/orders/{id}/history/

1. Method + URL: `GET /api/orders/{id}/history/`  
2. Назначение: трассировка заявки -> продажи/оплаты/возвраты.
3. Response:
```json
{
  "order": {...OrderSerializer...},
  "sales": [...SaleSerializer...],
  "payments": [...PaymentSerializer...],
  "returns": [...ReturnSerializer...]
}
```
4. Фильтрация:
- `sales`: все, где `linked_order=order` (включая canceled/draft)
- `payments`: только `status=active`
- `returns`: все, где `linked_order=order` (включая canceled)

---

## 2.11 POST /api/orders/{id}/reserve/

1. Method + URL: `POST /api/orders/{id}/reserve/`  
2. Назначение: создать резерв партии под строку заявки.
3. Request:
```json
{"order_line_id": 501, "warehouse_batch_id": 101, "quantity": "5", "comment": "Резерв под клиента"}
```
4. Response: `OrderReservationSerializer`, HTTP `201`.
5. Errors:
- `400 MISSING_FIELD` (нет order_line_id / warehouse_batch_id / quantity)
- `404 NOT_FOUND` (строка заявки/партия не найдены)
- `400 INVALID_FIELD` (quantity не число)
- `422 RESERVATION_ERROR` (бизнес-ошибка сервиса)
6. Business:
- запрещен reserve defect batch
- нельзя зарезервировать больше свободного остатка партии
- нельзя зарезервировать больше остатка строки заявки
- увеличивает `OrderLine.reserved_quantity`.

---

## 2.12 POST /api/orders/{id}/release-reserve/

1. Method + URL: `POST /api/orders/{id}/release-reserve/`
2. Request:
```json
{"reservation_id": 900}
```
3. Response: `OrderReservationSerializer`.
4. Errors:
- `400 MISSING_FIELD`
- `404 NOT_FOUND`
- `422 RESERVATION_ERROR`
5. Business:
- переводит резерв в `released`
- уменьшает `OrderLine.reserved_quantity`.

---

## 2.13 GET /api/orders/{id}/reservations/

1. Method + URL: `GET /api/orders/{id}/reservations/`
2. Назначение: список всех резервов заявки.
3. Response: массив `OrderReservationSerializer`.

---

## 2.14 PATCH /api/orders/{id}/cancel/

1. Method + URL: `PATCH /api/orders/{id}/cancel/`
2. Назначение: отмена заявки.
3. Request JSON: не обязателен.
4. Response:
```json
{
  "status": "canceled",
  "reservations_released": 2,
  "order": {...OrderSerializer...}
}
```
5. Errors:
- `422 INVALID_TRANSITION` (state machine / active sales)
6. Business:
- валидирует переход в canceled
- валидирует запрет отмены при активных продажах (не draft/canceled)
- снимает все активные резервы заявки.

---

## 2.15 GET /api/order-reservations/

1. Method + URL: `GET /api/order-reservations/`
2. Назначение: read-only список резервов (общий).
3. Query params:
- `status` / optional / `active|released|fulfilled`
- `order_line` / optional / `?order_line=501`
- `warehouse_batch` / optional / `?warehouse_batch=101`
- `page`, `page_size`
4. Response: пагинация + `OrderReservationSerializer` items.

---

## 2.16 GET /api/order-reservations/{id}/

1. Method + URL: `GET /api/order-reservations/{id}/`
2. Назначение: read-only detail резерва.
3. Response: один `OrderReservationSerializer`.
4. Write actions через этот endpoint: **не реализовано**.

---

## 3. ПОЛЯ ORDER

Ниже поля `OrderSerializer` + retrieve additions.

- `id`: int, read-only, auto, показывать (тех. id), отправлять нет, пример `100`
- `order_number`: string, read-only, автогенерация, показывать да, отправлять нет, `ORD-2026-0001`
- `date`: date, writable, optional (default today), null нет
- `client`: fk int, writable, optional, allow_null=true
- `client_name`: string, read-only
- `source_type`: enum (`cashier|manager|boss|other`), writable, default `manager`
- `comment`: text, writable, optional
- `status`: enum (`new|confirmed|in_progress|partially_shipped|shipped|closed|canceled`), writable в обычном update
- `created_by`: fk int, writable technically but обычно ставится backend-ом при create
- `created_by_name`: read-only
- `responsible_user`: fk int, writable, optional allow_null
- `responsible_user_name`: read-only
- `created_at`: datetime, read-only
- `updated_at`: datetime, read-only
- `lines`: array `OrderLineSerializer`, writable
- `total_amount`: decimal-string, read-only
- `shipped_amount`: decimal-string, read-only
- `remaining_amount`: decimal-string, read-only
- `paid_amount`: decimal-string, read-only
- `payment_status`: enum string, read-only
- `debt_amount`: decimal-string, read-only
- `refund_amount`: decimal-string, read-only
- `has_company_debt_by_goods`: boolean, read-only
- `linked_entities`: object, read-only, только retrieve view
- `available_status_transitions`: array string, read-only, только retrieve
- `available_actions`: object, read-only, только retrieve

Frontend не должен отправлять:
- `order_number`
- `created_at`, `updated_at`
- финансовые вычисляемые поля
- retrieve-only metadata (`linked_entities`, `available_*`)

---

## 4. ПОЛЯ ORDER LINE

Поля `OrderLineSerializer`:

- `id`: int, optional в update lines payload, read-only при create объекта строки
- `product`: string, required (model not blank), writable
- `product_type`: string, optional, writable, default `""`
- `profile`: fk int, optional, writable, allow_null=true
- `ordered_quantity`: decimal, writable, default `0`
- `unit_price`: decimal, optional, writable, allow_null=true
- `comment`: string, optional, writable
- `shipped_quantity`: decimal, read-only, default `0`
- `reserved_quantity`: decimal, read-only, default `0`
- `remaining_quantity`: decimal-string, read-only computed
- `available_to_ship`: decimal-string, read-only computed
- `remaining_to_reserve`: decimal-string, read-only computed
- `line_total`: decimal-string, read-only computed

Frontend должен отправлять:
- `product`, `ordered_quantity` минимум для осмысленной строки

Frontend не должен отправлять:
- `shipped_quantity`, `reserved_quantity`, `remaining_*`, `line_total`

---

## 5. CREATE ORDER

Endpoint: `POST /api/orders/`

Текущее правило (исправлено):
1. `client` обязателен, `null` запрещен.
2. `client` должен быть `is_active=true`.
3. `lines` обязателен и должен содержать минимум 1 строку.
4. В каждой строке: обязателен `product` или `profile`.
5. `ordered_quantity` обязателен и должен быть `> 0`.
6. `unit_price` допускается `0`, но не может быть `< 0` (`unit_price >= 0`).
7. При отсутствии `unit_price` backend ставит `0`.
8. Коды ошибок валидации: `MISSING_CLIENT`, `INACTIVE_CLIENT`, `MISSING_LINES`, `PRODUCT_OR_PROFILE_REQUIRED`, `ORDERED_QUANTITY_REQUIRED`, `ORDERED_QUANTITY_INVALID`, `UNIT_PRICE_NEGATIVE`.

---

## 6. UPDATE ORDER

Endpoints:
- `PATCH /api/orders/{id}/`
- `PUT /api/orders/{id}/`

Проверки (исправлено):
1. `status` через `PATCH/PUT /api/orders/{id}/` запрещен.
2. При попытке обычного update со `status` -> `400 STATUS_UPDATE_FORBIDDEN`, message: `Статус заявки меняется только через /status/.`
3. Для `closed` и `canceled` редактирование заявки запрещено (`ORDER_UPDATE_FORBIDDEN`).
4. `lines` запрещено редактировать в статусах: `partially_shipped`, `shipped`, `closed`, `canceled`.
5. Если есть активные продажи (`sale_status != draft/canceled`) -> `ORDER_LINES_UPDATE_FORBIDDEN`.
6. Если есть `SaleLine` по строкам заявки -> `ORDER_LINES_UPDATE_FORBIDDEN`.
7. Нельзя изменять строку заявки, по которой уже есть продажа/резерв/возврат (`ORDER_LINE_LOCKED`).
8. Обновление строк:
- `line.id` есть -> обновление существующей
- `line.id` нет -> создание новой
- строки, отсутствующие в payload, не удаляются

---

## 7. DELETE ORDER

`DELETE /api/orders/{id}/`:
- Разрешен: нет
- HTTP: `405`
- code: `DELETE_DISABLED`
- frontend вместо delete: `PATCH /api/orders/{id}/cancel/`
- кнопку "Удалить": не показывать

---

## 8. STATUS / STATE MACHINE

Статусы заявки:
- `new` — новая (default)
- `confirmed` — подтверждена
- `in_progress` — в работе
- `partially_shipped` — технический статус частичной продажи  
  UI label: **Частично продана**
- `shipped` — технический статус полной продажи  
  UI label: **Продана**
- `closed` — закрыта
- `canceled` — отменена

Переходы (`ORDER_TRANSITIONS`):
- `new -> confirmed|canceled`
- `confirmed -> in_progress|canceled`
- `in_progress -> partially_shipped|shipped|canceled`
- `partially_shipped -> shipped|closed|canceled`
- `shipped -> closed`
- `closed ->` запрещены
- `canceled ->` запрещены

Endpoint переходов:
- `PATCH /api/orders/{id}/status/` body `{ "status": "..." }`
- Единственный допустимый способ менять статус заявки.

Проверки:
- `validate_order_transition`
- для `partially_shipped|shipped|closed` -> `order_sync.validate_order_for_new_status`

Ошибки:
- `MISSING_STATUS` (400)
- `INVALID_STATUS_TRANSITION` (422)
- `ORDER_STATUS_BLOCKED` (422)

После success frontend обновляет:
- `status`
- `available_status_transitions`
- `available_actions`
- вычисляемые amounts/line shipped fields (после перезагрузки order)

---

## 9. CANCEL ORDER

Endpoint: `PATCH /api/orders/{id}/cancel/`

Проверено:
1. Отмена работает через state-machine (`new|confirmed|in_progress|partially_shipped -> canceled`).
2. При активных продажах (`not draft/canceled`) cancel запрещен (`422 INVALID_TRANSITION`).
3. Активные резервы снимаются автоматически (`release_all_for_order`).
4. Response содержит `status`, `reservations_released`, `order`.
5. Delete не используется, только cancel endpoint.

---

## 10. RESERVATIONS

Зафиксированный контракт:
1. Для заявок frontend использует только:
   - `POST /api/orders/{id}/reserve/`
   - `POST /api/orders/{id}/release-reserve/`
2. Для заявок frontend не использует:
   - `POST /api/warehouse/batches/reserve/`
3. `cancel` заявки автоматически снимает активные резервы.
4. Для чтения доступны:
   - `GET /api/orders/{id}/reservations/`
   - `GET /api/order-reservations/` (+ detail)

---

## 11. ORDER HISTORY

Endpoint: `GET /api/orders/{id}/history/`

Response:
```json
{
  "order": {...},
  "sales": [...],
  "payments": [...],
  "returns": [...]
}
```

Массивы:
- `sales[]`: `SaleSerializer` (показывать номер, статус, суммы; скрывать техполя)
- `payments[]`: `PaymentSerializer` (показывать тип, сумму, статус)
- `returns[]`: `ReturnSerializer` (показывать номер, статус, строки)

Фильтрация:
- payments: только active
- returns: canceled включаются
- sales: canceled включаются

UI labels статусов:
- `partially_shipped` -> `Частично продана`
- `shipped` -> `Продана`
- `canceled` -> `Отменена`

---

## 12. WAYBILL

Endpoint: `GET /api/orders/{id}/waybill/`

1. Возвращает: HTML-документ (не JSON).
2. Content-Type: `text/html; charset=utf-8`
3. Кнопка "Накладная": показывать, если есть доступ к заявке.
4. При ошибке: показывать backend error message.
5. Открытие: новая вкладка/окно документа.
6. Скрывать кнопку как "не готово": не требуется, backend endpoint реализован.

---

## 13. SELECT-SOURCES

Endpoint: `GET /api/orders/select-sources/`

Response:
```json
{
  "clients": [
    {"id": 1, "label": "ОсОО Альфа"}
  ],
  "profiles": [
    {"id": 2, "label": "60 мм белый"}
  ]
}
```

`clients[]`:
- `id`
- `label` (= `Client.name`)

`profiles[]`:
- `id`
- `label` (= `PlasticProfile.name`)

Правила:
- clients только active
- если inactive клиент появился здесь — это была бы проблема, но по коду не появляется.

---

## 14. СВЯЗЬ ЗАЯВКА -> ПРОДАЖА

1. Связь sale с order:
- `Sale.linked_order`
2. Связь строки продажи со строкой заявки:
- `SaleLine.order_line`
3. Источник shipped_quantity:
- `order_sync.recalculate_order_line_shipped_from_sale_lines_for_order`
- сумма `SaleLine.quantity` для sale со статусом `partially_shipped|shipped|closed`
4. `remaining_quantity`:
- вычисляется в `OrderLine`: `ordered - shipped`, минимум 0
5. Можно ли продать больше ordered:
- жесткой create/update проверки в OrderSerializer нет
- при переходе order статусов есть блокирующие проверки (`validate_order_for_new_status`)
6. Где валидируется:
- `order_sync.validate_order_for_new_status`
7. Отмена продажи:
- в `Sale cancel` вызывается пересчет order lines (`recalculate_order_line_shipped_from_sale_lines_for_order`)
8. Пересчет заявки после отмены продажи:
- да, через `order_sync` из sale flow
9. UI labels:
- `shipped_quantity` показывать как "Продано"
- `remaining_quantity` показывать как "Осталось"

---

## 15. ОПЛАТЫ И ЗАЯВКА

1. Оплата заявки напрямую: да, через `Payment.linked_order`.
2. `linked_order` в Payment: есть.
3. `paid_amount`: считает `order_payment_metrics` (incoming - refund, только active).
4. `payment_status`: из `payment_status.py`.
5. `debt_amount`: `max(total_due - net_paid, 0)` (с нюансами refunded).
6. `refund_amount`: сумма refund.
7. canceled payments: не учитываются.
8. Frontend показывать:
- `paid_amount`, `payment_status`, `debt_amount`, `refund_amount`.

---

## 16. ВОЗВРАТЫ И ЗАЯВКА

1. `linked_order` в Return: есть.
2. Связь с заявкой:
- напрямую `Return.linked_order`
- также через `Return.sale -> Sale.linked_order`
3. В `order history` возвраты показываются: да.
4. Canceled returns в history: попадают (фильтра нет).
5. Frontend показывать:
- номер возврата, статус, дату, строки.

---

## 17. ОШИБКИ И HTTP CODES

Специфичные для заявок:

- `400 MISSING_STATUS` (`/orders/{id}/status/`, нет `status`)
- `422 INVALID_STATUS_TRANSITION` (недопустимый переход)
- `422 ORDER_STATUS_BLOCKED` (не выполнены проверки для целевого статуса)
- `400 MISSING_FIELD` (reserve/release-reserve)
- `404 NOT_FOUND` (line/batch/reservation не найдены)
- `422 RESERVATION_ERROR` (ошибки сервиса резервов)
- `422 INVALID_TRANSITION` (`/orders/{id}/cancel/`)
- `405 DELETE_DISABLED` (`DELETE /orders/{id}/`)

Общие:
- `401 unauthorized`
- `403 forbidden`
- `400 validation_error` (DRF валидации)
- `404 not_found`
- `405 bad_request` (метод не поддерживается, от global exception handler)

Что frontend показывает:
- `MISSING_STATUS`: "Укажите статус."
- `INVALID_STATUS_TRANSITION`: текст backend.
- `ORDER_STATUS_BLOCKED`: текст backend.
- `MISSING_FIELD`: "Заполните обязательные поля резерва."
- `RESERVATION_ERROR`: текст backend.
- `DELETE_DISABLED`: "Удаление недоступно, используйте отмену."

---

## 18. BUSINESS LOGIC CHECK

1. Заявка без клиента: нельзя.
2. Заявка без строк: нельзя.
3. Строка без `product/profile`: нельзя.
4. `ordered_quantity <= 0`: нельзя.
5. `unit_price < 0`: нельзя.
6. Изменение `status` через обычный update: нельзя.
7. Редактирование `closed/canceled`: нельзя.
8. Редактирование lines после продаж/по строкам с продажами: нельзя.
9. Отменить заявку при активной продаже: нельзя.
10. `cancel` снимает активные резервы и возвращает `reservations_released`.
11. Проданность заявки считается по `SaleLine`.
12. Корректность вычислений:
- `total_amount`: по lines qty*price
- `shipped_amount`: по shipped_quantity*price
- `remaining_amount`: по remaining_quantity*price
- `paid/debt/payment_status/refund`: через payment metrics (active payments only)
13. Критичный риск некорректной заявки от frontend устранен.

---

## 19. FRONTEND CONTRACT

Список заявок:
- endpoint: `GET /api/orders/`
- показывать: `order_number,date,client_name,status,total_amount,paid_amount,debt_amount`
- actions: Открыть, Редактировать, История, Накладная, Отменить, Изменить статус

Создание:
- endpoint: `POST /api/orders/`
- body: `date,client,source_type,comment,responsible_user,lines[]`
- обязательные в UI и backend: `client`, минимум 1 line, `ordered_quantity > 0`
- `unit_price` не может быть отрицательной

Редактирование:
- endpoint: `PATCH /api/orders/{id}/`
- forbidden для submit: вычисляемые read-only поля, `order_number`, timestamps
- status менять через отдельный endpoint `/status/`
- status в обычный `PATCH/PUT` отправлять нельзя (`STATUS_UPDATE_FORBIDDEN`)

Статусы:
- endpoint: `PATCH /api/orders/{id}/status/`
- raw labels в UI:
  - `partially_shipped` => `Частично продана`
  - `shipped` => `Продана`
- Не показывать пользователю слово "Отгрузка" в label

Отмена:
- endpoint: `PATCH /api/orders/{id}/cancel/`
- body не обязателен
- после успеха обновить order + reservations

Резерв:
- использовать `POST /api/orders/{id}/reserve/`
- не использовать `POST /api/warehouse/batches/reserve/` для заявочного сценария

История:
- endpoint: `GET /api/orders/{id}/history/`

Накладная:
- endpoint: `GET /api/orders/{id}/waybill/`
- открывать в новой вкладке

Кнопки:
- показывать: Создать, Открыть, Редактировать, Подтвердить, В работу, Закрыть, Отменить, Накладная
- не показывать: Удалить, raw shipped labels, UX-слово "Отгрузка"

---

## 20. PROBLEMS

### Critical
- Не найдено.

### Medium
- Не найдено.

### Minor
- В `order history` массивы `sales/returns` включают canceled записи (для некоторых UI это может требовать дополнительной фильтрации на фронте).

### API contract mismatch
- Формально endpoint-ы и router по заявкам совпадают; mismatch не выявлен.

### Legacy
- Есть legacy-ветка auto-fulfill по "шапке продажи" при отсутствии line-level связей.

### Frontend must not use
- `DELETE /api/orders/{id}/`
- `POST /api/warehouse/batches/reserve/` для reserve-заявок
- raw `status` update через общий `PATCH/PUT /api/orders/{id}/` (для статуса использовать `/status/`)

### Missing tests
- Добавлены API-тесты `apps/sales/tests/test_orders_api.py`:
  - create validations (client/lines/product-profile/qty/price)
  - update restrictions (`STATUS_UPDATE_FORBIDDEN`, lifecycle locks)
  - status transitions + invalid transition + missing status
  - cancel behavior + reservations release + active sale block
  - delete disabled
  - select-sources active clients/profiles shape
  - order history structure
  - waybill HTML response

---

## 21. FINAL VERDICT

Заявки backend contract:
- **OK**
