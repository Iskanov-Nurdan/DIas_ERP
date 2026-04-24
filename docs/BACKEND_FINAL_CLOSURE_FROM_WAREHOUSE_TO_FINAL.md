# BACKEND FINAL CLOSURE (Warehouse -> Final)

Только факты по текущему коду после финальной доочистки.

---

## 1. Канонические endpoint (реально в коде)

### Документы
- `GET /api/orders/{id}/waybill/`
- `GET /api/sales/{id}/waybill/`
- `GET /api/sales/{id}/receipt/`
- `GET /api/returns/{id}/waybill/`

Формат документов:
- `Content-Type: text/html; charset=utf-8`
- `Content-Disposition: inline`
- стабильные имена файлов:
  - `order-waybill-{id}.html`
  - `sale-waybill-{id}.html`
  - `sale-receipt-{id}.html`
  - `return-waybill-{id}.html`

### Основные разделы
- Склад: `/api/warehouse/batches/...`
- Клиенты: `/api/clients/...`, `/api/client-financial-summary/`
- Заявки: `/api/orders/...`, `/api/order-reservations/...`
- Продажи: `/api/sales/...`
- Оплаты: `/api/payments/...`
- Возвраты: `/api/returns/...`
- Брак: `/api/defects/...`
- Переделка: `/api/rework-requests/...`
- Аналитика: `/api/analytics/*`
- WebSocket: `ws/operational`

---

## 2. Какие alias удалены

Удалены из рабочего контрактного слоя:
- `batchId` (склад)
- request fallback mapping:
  - `warehouse_batch_id -> warehouse_batch`
  - `sale_date -> date`
  - `quantity_unit -> sale_unit`
- клиентские alias-поля и silent mapping:
  - `contact_person`, `whatsapp_telegram`, `second_phone/comment` и т.п.
- route alias документов:
  - `nakladnaya`
  - `invoice`

---

## 3. Какие select-source endpoint реально работают

Добавлены и доступны:
- `GET /api/orders/select-sources/`
  - `clients`, `profiles`
- `GET /api/sales/select-sources/?client_id=...`
  - `clients`, `orders`, `warehouse_batches`
- `GET /api/returns/select-sources/?sale_id=...`
  - `sales`, `sale_lines`
- `GET /api/defects/select-sources/`
  - `return_lines`
- `GET /api/rework-requests/select-sources/`
  - `defect_records`, `original_sales`, `returns`, `result_warehouse_batches`

---

## 4. Какие relation теперь обязательны

- `ReturnLine.sale_line` — обязательно.
- `DefectRecord.source_id` — обязательно.
- `DefectRecord(source_type=return)`:
  - `source_id` должен ссылаться на `ReturnLine`.
- `ReworkRequest.defect_record` — обязательно.
- `ReworkRequest.original_sale` — обязательно.
- `POST /api/rework-requests/{id}/complete/`:
  - `result_warehouse_batch_id` — обязательно.

---

## 5. Какие string-поля ещё допустимы

- `OrderLine.product` — допустим как текстовое наименование позиции заявки.
- `Sale.product` — допустим текстом; при выбранной `warehouse_batch` подставляется backend из партии.
- `DefectRecord.product` хранится как текст, но при `source_type=return` автозаполняется от источника.

---

## 6. Какие detail responses расширены

Расширены detail responses для:
- `WarehouseBatch`
- `Order`
- `Sale`
- `Return`
- `DefectRecord`
- `ReworkRequest`

В них стабилизировано:
- `available_actions`
- `available_status_transitions`
- `linked_entities`/читаемые labels
- для `Return` добавлены `downstream_links` (созданные `defect_record` / `rework_request`)
- для `ReturnLine` добавлены `sale_line_label`, `sale_line_sale_id`

---

## 7. Какие backend-ограничения реально enforced

### Склад
- reserve разрешён только для `quality=good`.
- package разрешён только для `quality=good`.
- defect нельзя резервировать.
- defect нельзя упаковывать.

### Возвраты
- создание/обновление строки без `sale_line` запрещено.
- `product` не принимается как источник истины (read-only/autofill).
- количество валидируется относительно `sale_line` (нельзя вернуть больше отгруженного с учётом уже возвращённого).

### Брак / переделка
- `DefectRecord` без `source_id` создать нельзя.
- `source_type=return` валидирует `source_id` как `ReturnLine`.
- при источнике return `product` и `quantity_pcs` подтягиваются от источника.
- `ReworkRequest` без `defect_record` и `original_sale` создать нельзя.
- `complete rework` без `result_warehouse_batch_id` запрещён.

---

## 8. Что фронт теперь обязан использовать

- Только канонические endpoint документов (`waybill/receipt`).
- Только канонические request-поля (без alias).
- Для выбора сущностей — только select-source endpoint и relation-поля.
- Для detail UX — только `available_actions`/`available_status_transitions` + `linked_entities`.
- Для остатков склада:
  - `quantity`, `reserved_quantity`, `available_quantity` брать только из backend.

---

## 9. Что фронту теперь запрещено делать

- Пробовать старые document URL (`nakladnaya`, `invoice`).
- Использовать удалённые alias-ключи (`batchId`, `sale_date`, `quantity_unit`, клиентские alias-поля).
- Передавать `product` в return line как источник истины.
- Строить fallback-логику выбора сущности из случайных endpoint.
- Интерпретировать статусы/действия без полей detail-контракта.

---

## 10. Что ещё осталось (фактически)

- Аналитика остаётся **pull-only** (REST). Отдельный realtime resource для аналитики не вводился.
- В моделях могут оставаться исторические поля (`invoice_number` и др.), но канонический фронтовый контракт документов уже зафиксирован на `waybill/receipt`.

