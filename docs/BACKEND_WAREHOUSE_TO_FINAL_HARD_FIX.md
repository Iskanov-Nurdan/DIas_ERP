# BACKEND HARD FIX (Warehouse -> Final)

Документ фиксирует фактически внесённые hard-fix изменения backend-контракта для этапов:
`Склад`, `Клиенты`, `Заявки`, `Продажи`, `Оплаты`, `Возвраты`, `Брак/переделка`, `Аналитика`, `Документы`, `WebSocket`.

Основание: текущий код backend + изменения в этом hard-fix.

---

## 1) Что очищено в контракте

- Убраны дубли endpoint документов: оставлены только канонические `waybill`/`receipt`.
- Убран fallback-формат запросов для склада:
  - удалён alias `batchId` (теперь только `batch_id` и `warehouse_batch_id` в соответствующих endpoint).
- Убраны alias-поля клиентов в API контракте:
  - `contact_person`, `whatsapp_telegram`, и неявные маппинги `second_phone/comment`.
- Убраны alias-мэппинги в продаже:
  - `warehouse_batch_id -> warehouse_batch`, `quantity_unit -> sale_unit`, `sale_date -> date`.
- Усилены detail responses (detail-first):
  - добавлены `available_actions` и `available_status_transitions` в detail ответах ключевых сущностей.

---

## 2) Канонические endpoint и поля (теперь)

### Документы (канон)
- `GET /api/orders/{id}/waybill/` — накладная заявки.
- `GET /api/sales/{id}/waybill/` — накладная продажи.
- `GET /api/sales/{id}/receipt/` — квитанция продажи.
- `GET /api/returns/{id}/waybill/` — накладная возврата.

### Склад
- `POST /api/warehouse/batches/reserve/`:
  - только `batch_id`, `quantity`, `sale_id?`.
- `POST /api/warehouse/batches/package/`:
  - только `warehouse_batch_id`, `packages_count`, `pieces_per_package`, `comment?`.

### Клиенты
- Основной контракт клиента только по каноническим полям модели (`contact`, `messenger`, `phone_alt`, `notes` и т.д.) без alias-полей.

### Продажи
- Входные поля строго канонические:
  - `warehouse_batch`, `sale_unit`, `date`, `quantity`/`sold_pieces`, `price`, `sale_mode`, `linked_order`, `client`.
- Для смены статуса:
  - `PATCH /api/sales/{id}/status/` с `status`, `force_credit_override?`.

### Возвраты
- `ReturnLine` теперь канонически через связь `sale_line` (обязательное поле).
- `product` в строке возврата — read-only, заполняется backend.

---

## 3) Legacy-алиасы: что убрано / что оставлено

Убрано:
- `batchId` в складских reserve/package endpoint.
- Клиентские alias-поля и silent mapping (`contact_person`, `whatsapp_telegram`, `second_phone`, `comment`).
- Продажные silent alias mapping (`warehouse_batch_id`, `quantity_unit`, `sale_date`).
- Продажные документные alias endpoint (`nakladnaya`, `invoice`) для sale.

Оставлено:
- Бизнес-данные и связи, уже используемые моделями/сериализаторами (без новых alias-ключей).

---

## 4) Где relation теперь обязательна

- `ReturnLine.sale_line` — обязательно.
- `ReworkRequest.create`:
  - `defect_record` — обязательно.
  - `original_sale` — обязательно.
- `DefectRecord.create`:
  - `source_id` — обязательно.
  - при `source_type=return` `source_id` должен ссылаться на существующий `ReturnLine`.

---

## 5) Где string допустим

- `OrderLine.product` — допустим как явное текстовое наименование позиции заявки.
- `Sale.product` — допустим текстом, если продажа не строится от складской партии; при выборе `warehouse_batch` заполняется из партии.
- `DefectRecord.product` — допускается как поле учётной записи, но при `source_type=return` теперь автозаполняется из `sale_line.product`.

---

## 6) Добавленные backend-ограничения

### Склад
- Резерв партии (`warehouse/batches/reserve`) разрешён только для:
  - `status=available`
  - `quality=good`
- Упаковка партии (`warehouse/batches/package`) разрешена только для:
  - `status=available`
  - `inventory_form=unpacked`
  - `quality=good`

### Возвраты / downstream
- Возвратная строка без `sale_line` теперь невалидна.
- `product` возвратной строки не задаётся фронтом, а подставляется backend от `sale_line`.

### Брак / переделка
- `DefectRecord` нельзя создать без `source_id`.
- Для `source_type=return` проверяется наличие источника `ReturnLine`.
- `ReworkRequest` нельзя создать без `defect_record` и `original_sale`.

### Detail-first
- Для detail endpoint добавлены доступные действия:
  - `Order`, `Sale`, `Return`, `DefectRecord`, `ReworkRequest`, `WarehouseBatch`.

---

## 7) Что фронт теперь обязан использовать

- Только канонические endpoint документов (`waybill`/`receipt`).
- Только канонические request-поля без fallback-ключей.
- Для возвратов — обязательный `sale_line` как источник строки.
- Для detail UX — использовать `available_actions` и `available_status_transitions` из detail response.
- Для склада разделять:
  - `quantity` (физический остаток),
  - `reserved_quantity` (активный резерв),
  - `available_quantity` (доступно к новым операциям).

---

## 8) Что фронту теперь запрещено

- Пробовать несколько URL для одного документа.
- Отправлять legacy-алиасы полей (batchId, quantity_unit, sale_date, client alias-поля).
- Передавать `product` в строках возврата как источник истины.
- Пытаться резервировать/упаковывать `quality=defect` как обычный товар.
- Строить собственную статусную логику в обход `available_status_transitions`.

