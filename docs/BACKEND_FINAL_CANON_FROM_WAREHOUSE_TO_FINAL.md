# BACKEND FINAL CANON (Warehouse -> Final)

Только итоговый канон по текущему backend-коду.

---

## 1) Канонические endpoint

### Склад
- `GET /api/warehouse/batches/`
- `GET /api/warehouse/batches/{id}/`
- `POST /api/warehouse/batches/reserve/`
- `POST /api/warehouse/batches/package/`
- `GET /api/warehouse/batches/{id}/trace/`

### Клиенты
- `GET/POST /api/clients/`
- `GET/PATCH/PUT/DELETE /api/clients/{id}/`
- `GET /api/clients/{id}/history/`
- `GET /api/client-financial-summary/?client_id=...`

### Заявки
- `GET/POST /api/orders/`
- `GET/PATCH/PUT/DELETE /api/orders/{id}/`
- `PATCH /api/orders/{id}/status/`
- `PATCH /api/orders/{id}/cancel/`
- `POST /api/orders/{id}/reserve/`
- `POST /api/orders/{id}/release-reserve/`
- `GET /api/orders/{id}/reservations/`
- `GET /api/orders/{id}/history/`
- `GET /api/orders/{id}/waybill/`
- `GET /api/order-reservations/` (readonly)

### Продажи
- `GET/POST /api/sales/`
- `GET/PATCH/PUT/DELETE /api/sales/{id}/`
- `PATCH /api/sales/{id}/status/`
- `GET /api/sales/{id}/credit-check/`
- `GET /api/sales/{id}/waybill/`
- `GET /api/sales/{id}/receipt/`

### Оплаты
- `GET/POST /api/payments/`
- `GET/PATCH/PUT/DELETE /api/payments/{id}/`
- `GET /api/payments/summary/?client_id=...`

### Возвраты
- `GET/POST /api/returns/`
- `GET/PATCH/PUT/DELETE /api/returns/{id}/`
- `GET /api/returns/{id}/waybill/`

### Брак
- `GET/POST /api/defects/`
- `GET/PATCH/PUT/DELETE /api/defects/{id}/`
- `POST /api/defects/{id}/send-to-rework/`
- `POST /api/defects/{id}/complete-rework/`
- `POST /api/defects/{id}/writeoff/`
- `POST /api/defects/{id}/sell/`

### Переделка
- `GET/POST /api/rework-requests/`
- `GET/PATCH/PUT/DELETE /api/rework-requests/{id}/`
- `POST /api/rework-requests/{id}/start/`
- `POST /api/rework-requests/{id}/complete/`
- `POST /api/rework-requests/{id}/cancel/`

### Аналитика
- `GET /api/analytics/summary/`
- `GET /api/analytics/revenue-details/`
- `GET /api/analytics/sales-cost-details/`
- `GET /api/analytics/production-cost-details/`
- `GET /api/analytics/purchase-details/`
- `GET /api/analytics/profit-details/`
- `GET /api/analytics/otk-details/`
- `GET /api/analytics/writeoff-details/`
- `GET /api/analytics/defect-analytics/`
- `GET /api/analytics/rework-analytics/`
- `GET /api/analytics/client-profitability/`
- `GET /api/analytics/receivables/`

### WebSocket
- `ws/operational`

---

## 2) Канонические поля (без alias)

### Убраны из рабочего request-contract
- `batchId`
- `warehouse_batch_id -> warehouse_batch` mapping (в продаже)
- `sale_date -> date`
- `quantity_unit -> sale_unit`
- клиентские alias-поля (`contact_person`, `whatsapp_telegram`, и silent-mapping алиасы)

### Документы (канон)
- order: только `waybill`
- sale: только `waybill`, `receipt`
- return: только `waybill`

### Склад
- `quantity` = физический остаток
- `reserved_quantity` = активные резервы
- `available_quantity` = доступно к новым операциям
- `quality` = `good|defect`
- `inventory_form` = `unpacked|packed|open_package`

### Возврат
- `ReturnLine.sale_line` обязателен
- `ReturnLine.product` read-only/autofill от `sale_line`

### Брак/Переделка
- `DefectRecord.source_id` обязателен
- `ReworkRequest.defect_record` обязателен
- `ReworkRequest.original_sale` обязателен
- `complete rework`: `result_warehouse_batch_id` обязателен

---

## 3) Select-source endpoints (канонические источники выбора)

### Общие справочники
- Клиенты: `GET /api/clients/`
- Заявки: `GET /api/orders/`
- Складские партии (для продаж): `GET /api/warehouse/batches/?status=available&quality=good`

### Специализированные select-sources
- Для создания/редактирования заявки:
  - `GET /api/orders/select-sources/`
  - возвращает: `clients`, `profiles`
- Для создания/редактирования продажи:
  - `GET /api/sales/select-sources/?client_id=...`
  - возвращает: `clients`, `orders`, `warehouse_batches`
- Для возврата:
  - `GET /api/returns/select-sources/?sale_id=...`
  - возвращает: `sales`, `sale_lines`
- Для брака:
  - `GET /api/defects/select-sources/`
  - возвращает: `return_lines`
- Для переделки:
  - `GET /api/rework-requests/select-sources/`
  - возвращает: `defect_records`, `original_sales`, `returns`, `result_warehouse_batches`

---

## 4) Где relation, а где string

### Relation (обязательно)
- `ReturnLine.sale_line`
- `ReworkRequest.defect_record`
- `ReworkRequest.original_sale`
- `ReworkRequest.result_warehouse_batch` на этапе complete (через `result_warehouse_batch_id`)

### Relation (рекомендуемо/канон выбора)
- `Sale.warehouse_batch` (если продажа со склада)
- `Sale.linked_order`
- `Order.client`

### String допускается
- `OrderLine.product` (наименование позиции заявки)
- `Sale.product` (но при выбранной партии склада подставляется backend из партии)
- `DefectRecord.product` хранится как поле, но при `source_type=return` заполняется backend от источника

---

## 5) Legacy-route/alias статус

### Deprecated/вне канона (фронту нельзя использовать)
- Документные alias-route: `nakladnaya`, `invoice`
- Поля-алиасы: `batchId`, `sale_date`, `quantity_unit`, клиентские alias-поля

Канон для фронта: только endpoint/поля из этого документа.

---

## 6) Enforced backend-правила

### Склад
- reserve только для `quality=good`
- package только для `quality=good`
- defect нельзя резервировать
- defect нельзя упаковывать

### Возвраты
- нельзя создать строку возврата без `sale_line`
- нельзя вернуть больше, чем отгружено по `sale_line`

### Брак
- `source_id` обязателен
- при `source_type=return` источник обязан быть `ReturnLine`
- product/quantity подтягиваются от источника (если источник return)

### Переделка
- create без `defect_record`/`original_sale` запрещён
- complete без `result_warehouse_batch_id` запрещён

### Статусы и действия
- Detail responses для `WarehouseBatch`, `Order`, `Sale`, `Return`, `DefectRecord`, `ReworkRequest` содержат:
  - `available_actions`
  - `available_status_transitions`
  - linked entities для detail UI

---

## 7) Что фронту ОБЯЗАТЕЛЬНО использовать

- Только канонические endpoint/поля из этого документа.
- Только relation-поля там, где они обязательны.
- Только backend-вычисления для остатков/резервов/статусов/финансов.
- Для detail UX использовать `available_actions` и `available_status_transitions`.
- Для realtime использовать `ws/operational` + REST refetch.

---

## 8) Что фронту ЗАПРЕЩЕНО делать

- Гадать endpoint документов и пробовать альтернативные URL.
- Использовать legacy-алиасы полей/роутов.
- Передавать `product` как источник истины в возврате.
- Резервировать/упаковывать `quality=defect`.
- Строить action-flow без полей `available_actions`/`available_status_transitions`.
- Ожидать realtime push для аналитики: аналитика остаётся pull-only через REST.

