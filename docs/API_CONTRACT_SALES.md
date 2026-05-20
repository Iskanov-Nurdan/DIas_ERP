# Backend API Contract Review — Продажи

## 1. ОБЩАЯ ИНФОРМАЦИЯ

- Вкладка управляет продажами (`Sale`, `SaleLine`), статусами, складским списанием, печатными документами, связями с оплатами/возвратами/заявками.
- Основные модели: `Sale`, `SaleLine`, `Order`, `OrderLine`, `WarehouseBatch`, `Payment`, `Return`, `DefectRecord`.
- Основные serializer-ы: `SaleSerializer`, `SaleLineSerializer`.
- Основной viewset: `SaleViewSet`.
- Фильтры: `SaleFilter` (`client_id`, `sale_status`, `is_defect_sale`, `linked_order`, `date_from`, `date_to`).
- Права: `IsAdminOrHasAccess`, `required_access_key='sales'`.

---

## 2. ENDPOINTS ВКЛАДКИ “ПРОДАЖИ”

1. `GET /api/sales/`  
2. `POST /api/sales/`  
3. `GET /api/sales/{id}/`  
4. `PATCH /api/sales/{id}/`  
5. `PUT /api/sales/{id}/`  
6. `DELETE /api/sales/{id}/`  
7. `GET /api/sales/select-sources/`  
8. `PATCH /api/sales/{id}/status/`  
9. `POST /api/sales/{id}/cancel/`  
10. `PATCH /api/sales/{id}/cancel/`  
11. `GET /api/sales/{id}/credit-check/`  
12. `GET /api/sales/{id}/waybill/`  
13. `GET /api/sales/{id}/receipt/`  
14. Связанный endpoint продажи брака: `POST /api/defects/{id}/sell/`

---

## 3. CREATE SALE — ФАКТ ПОСЛЕ ФИКСА

`POST /api/sales/`

### Обязательные правила

- `client` обязателен, `null` запрещен.
- `client` должен быть `is_active=true`.
- `sale_lines` обязателен, минимум 1 строка.
- Header-only create для frontend запрещен.
- На строке:
  - product обязателен напрямую или выводится из `order_line/warehouse_batch`.
  - `quantity` обязателен и `> 0`.
  - `unit_price` по умолчанию `0`, но `< 0` запрещен.
- `sale_status=closed` при create запрещен.
- Разрешенные create statuses: `draft`, `confirmed`, `partially_shipped`, `shipped`.
- Для `partially_shipped/shipped`:
  - каждая строка должна иметь `warehouse_batch`,
  - batch: `status=available`, `quality=good`,
  - `quantity <= available_quantity` партии (с учетом активных резервов),
  - если указан `order_line`, то `quantity <= order_line.remaining_quantity`.

### Коды ошибок

- `MISSING_CLIENT`
- `INACTIVE_CLIENT`
- `MISSING_SALE_LINES`
- `PRODUCT_OR_ORDER_LINE_REQUIRED`
- `SALE_QUANTITY_REQUIRED`
- `SALE_QUANTITY_INVALID`
- `UNIT_PRICE_NEGATIVE`
- `CLOSED_CREATE_FORBIDDEN`
- `MISSING_WAREHOUSE_BATCH`
- `DEFECT_BATCH_FORBIDDEN`
- `INSUFFICIENT_STOCK`
- `ORDER_LINE_QUANTITY_EXCEEDED`

---

## 4. UPDATE SALE — ФАКТ ПОСЛЕ ФИКСА

`PATCH/PUT /api/sales/{id}/`

### Статус через обычный update

- Изменение `sale_status/status` через обычный PATCH/PUT запрещено.
- Статус меняется только через `PATCH /api/sales/{id}/status/`.
- Ошибка: `SALE_STATUS_UPDATE_FORBIDDEN`.

### Ограничения редактирования

- Полное редактирование только для `draft/confirmed`.
- Для `shipped/partially_shipped/closed/canceled` редактирование запрещено.
- Любое редактирование блокируется, если:
  - есть активные оплаты (`SALE_LOCKED_BY_PAYMENT`),
  - есть non-canceled возвраты (`SALE_LOCKED_BY_RETURN`),
  - склад уже списан (`warehouse_stock_applied=true`, `SALE_LOCKED_BY_WAREHOUSE`).
- Изменение `sale_lines` через PATCH/PUT продажи не поддерживается (`SALE_LINES_UPDATE_FORBIDDEN`).

### Safe-поля

При допустимом состоянии разрешены безопасные поля:
- `date`
- `comment`
- `invoice_number`
- `receipt_number`

---

## 5. STATUS ACTION

`PATCH /api/sales/{id}/status/`

- Единственный контрактный endpoint смены статуса продажи.
- Проверки:
  - `validate_sale_transition`,
  - для shipping-статусов: `validate_sale_ship`,
  - hard-credit-limit: `enforce_credit_limit`.
- `force_credit_override` поддерживается (при достаточных правах/role).
- При shipping выполняется backend-списание склада через `apply_warehouse_for_sale`.
- Двойное списание не происходит (`warehouse_stock_applied` idempotency).

UI labels:
- `partially_shipped` -> `Частично продана`
- `shipped` -> `Продана`

---

## 6. CANCEL SALE

`POST/PATCH /api/sales/{id}/cancel/`

- Блокируется при:
  - активных оплатах -> `HAS_PAYMENTS` (409),
  - non-canceled возвратах -> `HAS_RETURNS` (409).
- Выполняет:
  - rollback склада (через `warehouse_mutation`),
  - сброс `warehouse_stock_applied=false`,
  - восстановление резервов,
  - пересчет связанной заявки.
- При ошибке rollback возвращается стабильный код `WAREHOUSE_ROLLBACK`.

---

## 7. SELECT-SOURCES — ФАКТ ПОСЛЕ ФИКСА

`GET /api/sales/select-sources/?client_id=<id>&order_id=<id>`

Response:

```json
{
  "clients": [
    {"id": 1, "label": "ОсОО Альфа"}
  ],
  "orders": [
    {"id": 10, "label": "ORD-2026-001 — ОсОО Альфа — осталось 10.00"}
  ],
  "order_lines": [
    {
      "id": 101,
      "label": "60 мм белый — заказано 10 — продано 0 — осталось 10",
      "product": "60 мм белый",
      "ordered_quantity": "10",
      "shipped_quantity": "0",
      "remaining_quantity": "10",
      "unit_price": "100"
    }
  ],
  "warehouse_batches": [
    {
      "id": 55,
      "label": "#55 — 60 мм белый — свободно 18 шт — Годный — Упаковано",
      "product": "60 мм белый",
      "available_quantity": "18",
      "quality": "good",
      "status": "available",
      "inventory_form": "packed"
    }
  ]
}
```

Правила:
- clients: только active,
- orders: фильтруются по `client_id` (если передан),
- order_lines: фильтруются по `order_id` (если передан),
- warehouse_batches / available_warehouse_batches: только `status=available`, `quality=good`, сегмент `stock_bucket=standard`, остаток по `get_available_quantity>0`,
- defect batches не возвращаются.
- Поля остатка: `available_pieces_total` — суммарные штуки; `available_pieces`, `available_unpacked_pieces`, `unpacked_pieces` — неупакованный хвост (для `packed` всегда 0); `available_packages` — только для `packed` (total/ipp) или при `packages_count>0` на `unpacked`/`open_package`; если запечатанных упаковок в данных нет (`packages_count` пусто), `available_packages` = null даже при `pieces_per_package`, чтобы UI не делал двойное вычитание.
- `?unit_type=pieces`: не показываются `packed` партии и строки с `unpacked_pieces<=0`; `?unit_type=packages`: только `packed` с целой упаковкой.

---

## 8. CREDIT LIMIT

`compute_client_debt` и `check_credit_limit`:

- Продажи учитываются без `draft/canceled`.
- Оплаты учитываются только `status=active` (включая prepayment/payment/surcharge/refund).
- Canceled payments в долге/лимите не участвуют.

---

## 9. DEFECT SALE

- `POST /api/defects/{id}/sell/` сохранен и работает.
- Создает `Sale(is_defect_sale=true)`.
- Обычный `POST /api/sales/` не может продавать defect batch (валидация и коды).
- Контракт продажи брака описывается отдельно, обычный flow его не заменяет.

---

## 10. ERROR CODES (АКТУАЛЬНОЕ)

Критичные коды по продажам:

- `SALE_STATUS_UPDATE_FORBIDDEN`
- `SALE_UPDATE_FORBIDDEN`
- `SALE_LINES_UPDATE_FORBIDDEN`
- `SALE_LOCKED_BY_PAYMENT`
- `SALE_LOCKED_BY_RETURN`
- `SALE_LOCKED_BY_WAREHOUSE`
- `MISSING_CLIENT`
- `INACTIVE_CLIENT`
- `MISSING_SALE_LINES`
- `PRODUCT_OR_ORDER_LINE_REQUIRED`
- `SALE_QUANTITY_REQUIRED`
- `SALE_QUANTITY_INVALID`
- `UNIT_PRICE_NEGATIVE`
- `CLOSED_CREATE_FORBIDDEN`
- `MISSING_WAREHOUSE_BATCH`
- `DEFECT_BATCH_FORBIDDEN`
- `INSUFFICIENT_STOCK`
- `ORDER_LINE_QUANTITY_EXCEEDED`
- `MISSING_STATUS`
- `INVALID_STATUS_TRANSITION`
- `SHIP_BLOCKED`
- `CREDIT_LIMIT_BLOCKED`
- `WAREHOUSE_APPLY`
- `HAS_PAYMENTS`
- `HAS_RETURNS`
- `WAREHOUSE_ROLLBACK`
- `DELETE_DISABLED`

---

## 11. TESTS (ДОБАВЛЕНО)

Добавлен API-набор:
- `apps/sales/tests/test_sales_api.py`

Покрывает:
- create validation rules (client/sale_lines/qty/price/status/batch/stock/order_line limits),
- update restrictions (status via PATCH forbidden, lifecycle/payment/return/warehouse locks),
- status action (ok/invalid/missing),
- cancel behavior (guards + rollback path),
- select-sources (`clients/orders/order_lines/warehouse_batches`),
- HTML documents (`waybill`, `receipt`),
- credit override scenario.

---

## 12. FRONTEND CONTRACT (ИТОГ)

- frontend всегда отправляет `sale_lines[]` для `POST /api/sales/`.
- frontend не использует header-only flow.
- frontend не отправляет `sale_status/status` в обычный PATCH/PUT.
- frontend не использует `DELETE /api/sales/{id}/`.
- статусы менять только через `/status/`.

---

## 13. PROBLEMS

### Critical
- Не найдено.

### Medium
- Не найдено.

### Minor
- Не найдено.

### API contract mismatch
- Не найдено.

### Legacy
- Header-only flow сохранен только как legacy-механизм; frontend не использует.

### Frontend must not use
- `DELETE /api/sales/{id}/`
- header-only create без `sale_lines`
- status update через обычный PATCH/PUT продажи

### Missing tests
- Закрыто для ключевых sales-сценариев новым `test_sales_api.py`.

---

## 14. FINAL VERDICT

Продажи backend contract:
- **OK**
- Продажи backend contract закрыт.

