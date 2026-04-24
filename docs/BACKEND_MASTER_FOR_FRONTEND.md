# BACKEND MASTER — ИСТОЧНИК ИСТИНЫ ДЛЯ ФРОНТЕНДА

Дата: 24 апреля 2026  
Статус: **финальный, актуальный**

Все данные взяты из текущего кода backend (`apps/sales/`, `apps/warehouse/`).  
Если этот документ расходится с другими — верить этому документу.

---

## ОГЛАВЛЕНИЕ

1. [Коммерческие сущности](#1-коммерческие-сущности)
2. [Все актуальные endpoint](#2-все-актуальные-endpoint)
3. [Статусы и переходы](#3-статусы-и-переходы)
4. [Бизнес-правила для фронта](#4-бизнес-правила-для-фронта)
5. [Поля — источники истины](#5-поля--источники-истины)
6. [Что фронт должен скрыть](#6-что-фронт-должен-скрыть)
7. [WebSocket (realtime)](#7-websocket-realtime)
8. [Сводная таблица сущностей](#8-сводная-таблица-сущностей)
9. [Что фронту запрещено делать](#9-что-фронту-запрещено-делать)

---

## 1. КОММЕРЧЕСКИЕ СУЩНОСТИ

---

### Client (Клиент)

**Назначение:** Контрагент, покупатель. Привязывается к заявкам, продажам, оплатам.

**Ключевые поля (DB):**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `name` | string | Полное наименование клиента |
| `contact` | string | Контактное лицо |
| `phone` | string | Телефон (маскируется в аудите) |
| `phone_alt` | string | Доп. телефон |
| `inn` | string | ИНН |
| `address` | text | Адрес |
| `email` | string | Email |
| `messenger` | string | WhatsApp / Telegram |
| `client_type` | string | Тип клиента (свободный текст) |
| `notes` | text | Комментарий |
| `is_active` | bool | Активен ли клиент |
| `credit_limit` | decimal/null | Кредитный лимит (null = без лимита) |
| `credit_limit_mode` | `soft` / `hard` | Режим: мягкий или жёсткая блокировка |

**Аннотированные поля (только в list/detail, не в DB):**

| Поле | Описание |
|---|---|
| `sales_count` | Количество продаж |
| `sales_total` | Суммарная выручка |
| `has_sales` | bool: есть ли продажи |
| `status` | `"active"` / `"inactive"` — строка по `is_active` |

**Фронт показывает:** name, contact, phone, inn, address, email, messenger, client_type, credit_limit, credit_limit_mode, is_active, sales_count, sales_total  
**Фронт НЕ показывает:** `id` в списке, notes (только в форме редактирования), `has_sales` (техническое)

---

### Order (Заявка)

**Назначение:** Намерение клиента купить товар. Не перемещает склад. Может иметь несколько строк (OrderLine), несколько Sale и несколько Payment.

**Ключевые поля (DB):**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `order_number` | string | Номер заявки (уникальный) |
| `date` | date | Дата заявки |
| `client` | FK | Клиент |
| `source_type` | `cashier` / `manager` / `boss` / `other` | Тип источника |
| `status` | см. раздел 3 | Статус заявки |
| `comment` | text | Комментарий |
| `created_by` | FK User | Кто создал |
| `responsible_user` | FK User | Ответственный менеджер |
| `created_at` | datetime | Создано |
| `updated_at` | datetime | Обновлено |

**Вычисляемые поля (backend property — брать только с backend):**

| Поле | Формула |
|---|---|
| `total_amount` | Σ (ordered_quantity × unit_price) по строкам |
| `shipped_amount` | Σ (shipped_quantity × unit_price) по строкам |
| `remaining_amount` | Σ (remaining_quantity × unit_price) по строкам |
| `has_company_debt_by_goods` | bool: есть ли строка где remaining_quantity > 0 |

**Вложенные объекты в ответе:**
- `lines` — массив OrderLine
- `payments` — массив Payment (через related)

**Фронт показывает:** order_number, date, client, source_type, status, total_amount, remaining_amount, has_company_debt_by_goods, comment  
**Фронт НЕ показывает:** updated_at (служебное), created_by в карточке (только в аудите)

---

### OrderLine (Строка заявки)

**Назначение:** Одна позиция в заявке. Один товар = одна строка.

**Ключевые поля (DB):**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `order` | FK | Заявка |
| `product` | string | Наименование товара |
| `product_type` | string | Тип товара |
| `profile` | FK / null | Профиль (из справочника) |
| `ordered_quantity` | decimal | Заказано |
| `shipped_quantity` | decimal | Отгружено (обновляется backend автоматически) |
| `reserved_quantity` | decimal | Зарезервировано (обновляется backend автоматически) |
| `unit_price` | decimal / null | Цена за единицу |
| `comment` | text | Комментарий строки |

**Вычисляемые поля (property, брать только с backend):**

| Поле | Формула |
|---|---|
| `remaining_quantity` | `ordered_quantity − shipped_quantity` |
| `available_to_ship` | `remaining_quantity − (reserved_quantity − shipped_quantity)` |
| `remaining_to_reserve` | `ordered_quantity − reserved_quantity` |
| `line_total` | `ordered_quantity × unit_price` |

**Фронт показывает:** product, ordered_quantity, shipped_quantity, reserved_quantity, remaining_quantity, available_to_ship, unit_price, line_total  
**Фронт НЕ показывает:** `id` как самостоятельное число; `profile` FK (использовать profile_name если нужно)

---

### OrderReservation (Резерв)

**Назначение:** Резерв конкретной партии склада под строку заявки. Управляется только через `/api/orders/{id}/reserve/` и `/api/orders/{id}/release-reserve/`.

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `order_line` | FK | Строка заявки |
| `order_line_product` | string (read) | Наименование товара (из строки заявки) |
| `warehouse_batch` | FK | Партия склада |
| `warehouse_batch_product` | string (read) | Продукт партии |
| `quantity` | decimal | Текущее активное количество резерва |
| `fulfilled_quantity` | decimal (read) | Исполнено (записывается backend) |
| `status` | `active` / `released` / `fulfilled` | Статус резерва (readonly) |
| `sale_line` | FK / null (read) | Ссылка на SaleLine, которая исполнила резерв |
| `created_by` | FK / null | Кто создал |
| `created_at` | datetime | Создано |
| `comment` | text | Комментарий |

**Правила:**
- `status`, `fulfilled_quantity`, `sale_line` — readonly, меняются только backend-ом
- Нельзя напрямую создавать/удалять через `/api/order-reservations/` (только чтение через этот эндпоинт)
- Управление: `POST /api/orders/{id}/reserve/` и `POST /api/orders/{id}/release-reserve/`

**Фронт показывает:** quantity, fulfilled_quantity, status, order_line_product, warehouse_batch_product, created_at  
**Фронт НЕ показывает:** sale_line FK (технический), created_by

---

### Sale (Продажа)

**Назначение:** Факт отгрузки товара клиенту. Списывает склад. Может быть создана как standalone или привязана к Order.

**Ключевые поля (DB):**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `order_number` | string | Номер продажи/заказа |
| `sale_number` | string | Доп. номер продажи |
| `invoice_number` | string | Номер накладной |
| `receipt_number` | string | Номер квитанции |
| `sale_status` | см. раздел 3 | Статус продажи |
| `linked_order` | FK / null | Заявка (если продажа из заявки) |
| `client` | FK / null | Клиент |
| `warehouse_batch` | FK / null | Партия склада ГП |
| `product` | string | Продукт |
| `sale_mode` | `pieces` / `packages` | Режим продажи |
| `sold_pieces` | decimal | Продано штук |
| `sold_packages` | decimal | Продано упаковок |
| `length_per_piece` | decimal / null | Длина штуки, м |
| `total_meters` | decimal | Итого метров |
| `quantity` | decimal | Количество (= sold_pieces) |
| `price` | decimal / null | Цена за единицу |
| `revenue` | decimal | **Выручка** (рассчитывается backend) |
| `cost` | decimal | **Себестоимость** (рассчитывается backend) |
| `profit` | decimal | **Прибыль** (рассчитывается backend) |
| `date` | date | Дата продажи |
| `comment` | text | Комментарий |
| `is_defect_sale` | bool | Продажа брака |
| `stock_quality` | `good` / `defect` | Качество партии на момент продажи |
| `created_by` | FK User / null | Кто создал |
| `created_at` | datetime | Создано |

**Вычисляемые поля (backend, в ответе):**

| Поле | Описание |
|---|---|
| `client_name` | Имя клиента (read-only) |
| `profile_name` | Название профиля (через партию) |
| `inventory_form` | Форма учёта партии (unpacked / packed / open_package) |
| `sale_lines` | Массив SaleLine (read-only, вложенный) |

**Фронт показывает:** order_number, date, client_name, product, sold_pieces, price, revenue, profit, sale_status, is_defect_sale, inventory_form  
**Фронт НЕ показывает:** cost, stock_form, piece_pick (технические поля складского учёта), quantity (использовать sold_pieces), quantity_input (для package-режима внутри)

---

### SaleLine (Строка продажи)

**Назначение:** Строка многопозиционной продажи (новый формат). Привязана к Sale и опционально к OrderLine и WarehouseBatch.

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `sale` | FK | Продажа |
| `order_line` | FK / null | Строка заявки |
| `product` | string | Наименование |
| `warehouse_batch` | FK / null | Партия склада |
| `quantity` | decimal | Количество |
| `unit_price` | decimal / null | Цена за ед. |
| `line_total` | decimal | Сумма строки (рассчитывается backend) |
| `cost` | decimal | Себестоимость строки (рассчитывается backend) |
| `profit` | decimal | Прибыль строки (рассчитывается backend) |
| `defect_flag` | bool | Признак брака |
| `comment` | text | Комментарий |

**Фронт показывает:** product, quantity, unit_price, line_total, defect_flag  
**Фронт НЕ показывает:** cost, profit (служебные / аналитика), stock_form

---

### Payment (Оплата / Движение денег)

**Назначение:** Денежное движение по клиенту. Деньги и товар живут раздельно — Payment не привязан к конкретной Sale жёстко.

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `payment_number` | string | Номер квитанции/оплаты |
| `date` | date | Дата |
| `client` | FK / null | Клиент |
| `linked_order` | FK / null | Заявка (необязательно) |
| `linked_sale` | FK / null | Продажа (необязательно) |
| `payment_type` | `prepayment` / `payment` / `surcharge` / `refund` | Тип движения |
| `amount` | decimal | Сумма |
| `payment_method` | `cash` / `transfer` / `card` / `other` | Способ оплаты |
| `comment` | text | Комментарий |
| `created_by` | FK User / null | Кто создал |
| `created_at` | datetime | Создано |

**Типы:** `prepayment` и `payment` и `surcharge` — входящие деньги (уменьшают долг); `refund` — возврат денег клиенту (увеличивает долг).

**Фронт показывает:** date, payment_number, payment_type, amount, payment_method, client, linked_order, linked_sale, comment  
**Фронт НЕ показывает:** created_by в списке

---

### Return (Возврат)

**Назначение:** Возврат товара от клиента. Всегда привязан к продаже (Sale). Имеет строки (ReturnLine).

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `return_number` | string | Номер возврата |
| `date` | date | Дата |
| `sale` | FK | Продажа, из которой возврат |
| `linked_order` | FK / null | Заявка (опционально) |
| `invoice_number` | string | Накладная |
| `return_reason` | text | Причина |
| `comment` | text | Комментарий |
| `created_by` | FK User / null | Кто создал |
| `created_at` | datetime | Создано |

**Вложенные объекты:** `lines` — массив ReturnLine

**Фронт показывает:** return_number, date, sale (→ order_number клиента), return_reason, lines  
**Фронт НЕ показывает:** created_by в списке

---

### ReturnLine (Строка возврата)

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `return_doc` | FK | Возврат |
| `sale_line` | FK / null | Строка продажи |
| `product` | string | Товар |
| `quantity` | decimal | Количество |
| `return_target` | `warehouse` / `defect` / `rework` | Назначение возврата |
| `condition_type` | `good` / `damaged` / `defect` | Состояние товара |
| `comment` | text | Комментарий |

**Ограничение backend:** `quantity` возврата не может превысить `sale_line.quantity` минус уже возвращённое.

**Фронт показывает:** product, quantity, return_target, condition_type, comment  
**Фронт НЕ показывает:** sale_line FK напрямую (показывать через имя товара)

---

### DefectRecord (Брак)

**Назначение:** Учётная единица брака. Источник — ОТК (`otk`) или возврат клиента (`return`).

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `source_type` | `otk` / `return` | Источник |
| `source_id` | int / null | ID источника (otk_check или return_line) |
| `profile` | FK / null | Профиль |
| `product` | string | Наименование |
| `quantity_pcs` | decimal | Количество шт/м |
| `quantity_kg` | decimal / null | Количество кг |
| `kg_coefficient` | decimal / null | Коэффициент кг/ед. |
| `defect_reason` | text | Причина брака |
| `status` | см. раздел 3 | Статус |
| `writeoff_reason` | text | Причина списания |
| `comment` | text | Комментарий |
| `created_at` | datetime | Создано |

**Фронт показывает:** product, quantity_pcs, quantity_kg, defect_reason, status, source_type  
**Фронт НЕ показывает:** source_id, kg_coefficient (технические), writeoff_reason (только в карточке при status=written_off)

---

### ReworkRequest (Переделка)

**Назначение:** Запрос на переделку/перевыпуск по возврату клиента.

**Ключевые поля:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `rework_number` | string | Номер |
| `return_doc` | FK | Возврат |
| `defect_record` | FK / null | Брак |
| `original_sale` | FK / null | Исходная продажа |
| `product` | string | Продукт |
| `quantity_kg` | decimal | Масса входа кг (сырьё) |
| `output_quantity_kg` | decimal / null | Масса выхода кг (ГП) |
| `loss_kg` | decimal / null | Потери кг |
| `conversion_rate` | decimal / null | Коэффициент переработки (выход/вход) |
| `status` | см. раздел 3 | Статус |
| `result_warehouse_batch` | FK / null | Партия ГП после переделки |
| `comment` | text | Комментарий |
| `created_at` | datetime | Создано |

**Вычисляемые поля (backend property):**

| Поле | Описание |
|---|---|
| `rework_loss_kg` | Потери = `loss_kg` если задано, иначе `quantity_kg − output_quantity_kg` |
| `recovered_output` | = `output_quantity_kg` |

**Фронт показывает:** rework_number, product, quantity_kg, output_quantity_kg, rework_loss_kg, status, result_warehouse_batch  
**Фронт НЕ показывает:** conversion_rate (аналитика), recovered_output (то же что output_quantity_kg)

---

### WarehouseBatch (Партия склада ГП)

**Назначение:** Строка склада готовой продукции. Фронт использует только для выбора при создании Sale и просмотра остатков.

**Ключевые поля для фронта:**

| Поле | Тип | Описание |
|---|---|---|
| `id` | int | PK |
| `product` | string | Наименование продукта |
| `quantity` | decimal | Физический остаток (штук/м) |
| `reserved_quantity` | decimal | Зарезервировано (backend вычисляет) |
| `available_quantity` | decimal | Свободно для продажи (backend вычисляет) |
| `status` | `available` / `reserved` / `shipped` | Статус |
| `inventory_form` | `unpacked` / `packed` / `open_package` | Форма учёта |
| `date` | date | Дата прихода |
| `quality` | `good` / `defect` | Качество |
| `length_per_piece` | decimal / null | Длина штуки, м |
| `cost_per_piece` | decimal | Себестоимость штуки |

**Поля для упаковки (только если inventory_form = packed / open_package):**

| Поле | Описание |
|---|---|
| `pieces_per_package` | Штук в упаковке |
| `packages_count` | Количество упаковок |
| `sealed_packages_count` | Целые запечатанные упаковки |
| `open_package_pieces` | Штук в открытой упаковке |

**ОТК-снимок (только в detail / trace):** otk_accepted, otk_defect, otk_inspector_name, otk_status

**Фронт показывает:** product, quantity, available_quantity, status, inventory_form, date, quality, length_per_piece  
**Фронт НЕ показывает:** cost_per_piece, cost_per_meter (конфиденциально), source_batch (производственный FK), otk_fields в основном списке

---

### Client Financial Summary

**Назначение:** Финансовая сводка по клиенту. Backend вычисляет всё — фронт только показывает.

**Эндпоинт:** `GET /api/client-financial-summary/?client_id={id}`

**Поля ответа:**

| Поле | Описание |
|---|---|
| `client_id` | ID клиента |
| `client_name` | Имя клиента |
| `total_revenue` | Суммарная выручка |
| `total_cost` | Суммарная себестоимость |
| `total_profit` | Суммарная прибыль |
| `defect_revenue` | Выручка от продажи брака |
| `total_paid_gross` | Всего оплачено (до вычета возвратов) |
| `total_refunded` | Возвращено денег |
| `total_paid_net` | Чистые поступления (gross − refunded) |
| `client_debt_money` | Денежный долг клиента перед нами |
| `client_advance_amount` | Аванс (переплата) |
| `credit_limit` | Кредитный лимит (null если не задан) |
| `credit_limit_mode` | `soft` / `hard` |
| `credit_available` | Доступный кредит (null если лимит не задан) |
| `is_over_limit` | bool: лимит превышен |
| `credit_warning` | Текст предупреждения или null |

---

### Trace (Трассировка партии)

**Эндпоинт:** `GET /api/warehouse/batches/{id}/trace/`

**Ответ:** Полная цепочка:
`production_batch → otk_checks → sale_lines → return_lines → defect_records → rework_requests → reservations`

**Поле `reservations`** (полная коммерческая цепочка резервов):

| Поле | Описание |
|---|---|
| `reservation_id` | ID резерва |
| `status` | `active` / `released` / `fulfilled` |
| `order_line_id` | Строка заявки |
| `order_number` | Номер заявки |
| `quantity` | Текущее количество резерва |
| `fulfilled_quantity` | Исполнено |
| `sale_line_id` | Строка продажи (если исполнено) |
| `sale_order_number` | Номер продажи |
| `created_at` | Дата создания |

Также есть `active_reservations` — алиас на активные резервы (backward compat).

---

## 2. ВСЕ АКТУАЛЬНЫЕ ENDPOINT

Базовый URL: `/api/`  
Аутентификация: Bearer token / session  
Формат ошибок: `{ "code": "...", "error": "...", "detail": "..." }`  
HTTP 422 — бизнес-правило нарушено (не техническая ошибка)

---

### CLIENTS

**Access key:** `clients`

---

#### `GET /api/clients/`
Список клиентов.  
Query params: `search` (name/inn/contact/email), `is_active`, `ordering` (id, name)  
Response: массив ClientSerializer + `sales_count`, `sales_total`, `has_sales`, `status`

---

#### `GET /api/clients/{id}/`
Карточка клиента. Те же поля.

---

#### `POST /api/clients/`
Создать клиента.  
Body: `name` (обяз.), `contact`, `phone`, `phone_alt`, `inn`, `address`, `email`, `messenger`, `client_type`, `notes`, `is_active`, `credit_limit`, `credit_limit_mode`

---

#### `PATCH /api/clients/{id}/`
Обновить клиента. Те же поля.

---

#### `DELETE /api/clients/{id}/`
Удалить клиента.  
Ошибка 409 `CLIENT_IN_USE` если есть продажи.

---

#### `GET /api/clients/{id}/history/`
Полная история клиента: заявки, продажи, оплаты, возвраты + финансовые итоги.  
Response:
```
client_id, client_name,
orders[], sales[], payments[], returns[],
total_revenue, total_paid, total_refunded,
client_debt_money, client_advance_amount,
has_unshipped_goods, overdue_orders_count,
total_profit, defect_revenue,
credit_limit, credit_limit_mode, credit_available, credit_is_over_limit, credit_warning
```

---

### ORDERS

**Access key:** `client_orders`

---

#### `GET /api/orders/`
Список заявок.  
Query params: `status`, `client`, `date_from`, `date_to`, `ordering` (id, date, status)  
Response: массив с полями Order + вложенными `lines[]`, `payments[]`

---

#### `GET /api/orders/{id}/`
Карточка заявки. То же.

---

#### `POST /api/orders/`
Создать заявку.  
Body: `order_number` (необяз., авто), `date`, `client`, `source_type`, `comment`, `responsible_user`, `lines[]`  
`lines[]` = массив `{product, ordered_quantity, unit_price, comment, profile?, product_type?}`

---

#### `PATCH /api/orders/{id}/`
Обновить заявку (заголовок и строки).

---

#### `DELETE /api/orders/{id}/`
Удалить заявку.

---

#### `PATCH /api/orders/{id}/status/`
Сменить статус заявки через state machine.  
Body: `{ "status": "confirmed" }`  
Ошибки: 400 `MISSING_STATUS`, 422 `INVALID_STATUS_TRANSITION`, 422 `ORDER_CLOSE_BLOCKED`

**При переводе в `closed`:**
- Блокируется если есть активные резервы по строкам
- Блокируется если хотя бы одна строка не полностью отгружена

**Допустимые переходы:** см. раздел 3.

---

#### `POST /api/orders/{id}/reserve/`
Создать резерв партии под строку заявки.  
Body:
```json
{
  "order_line_id": 42,
  "warehouse_batch_id": 17,
  "quantity": "100.0000",
  "comment": "необязательно"
}
```
Response: 201 + OrderReservation объект  
Ошибки: 400 `MISSING_FIELD`, 404 `NOT_FOUND`, 422 `RESERVATION_ERROR` (нарушение бизнес-правила)

---

#### `POST /api/orders/{id}/release-reserve/`
Снять резерв.  
Body: `{ "reservation_id": 5 }`  
Ошибки: 400 `MISSING_FIELD`, 404 `NOT_FOUND`, 422 `RESERVATION_ERROR`

---

#### `GET /api/orders/{id}/reservations/`
Список всех резервов по заявке (все статусы).  
Response: массив OrderReservation

---

#### `PATCH /api/orders/{id}/cancel/`
Отменить заявку. Автоматически снимает все активные резервы.  
Ошибка 422 если по заявке есть активные (не черновиковые / не отменённые) продажи.

---

#### `GET /api/orders/{id}/nakladnaya/`
HTML-накладная. Content-Type: `text/html`. Открывать в браузере / iframe.

---

#### `GET /api/orders/{id}/history/`
История заявки: linked sales, payments, returns.  
Response: `{ order, sales[], payments[], returns[] }`

---

### SALES

**Access key:** `sales`

---

#### `GET /api/sales/`
Список продаж.  
Query params: `client`, `sale_status`, `date_from`, `date_to`, `linked_order`, `is_defect_sale`, `ordering` (id, date)  
Response: массив Sale + вложенные `sale_lines[]`

---

#### `GET /api/sales/{id}/`
Карточка продажи.

---

#### `POST /api/sales/`
Создать продажу.  
Body:

| Поле | Обяз. | Описание |
|---|---|---|
| `client` | нет | ID клиента |
| `warehouse_batch` | нет | ID партии склада |
| `product` | да (или через batch) | Наименование |
| `sold_pieces` | да | Количество штук |
| `price` | нет | Цена за единицу |
| `date` | нет (авто сегодня) | Дата |
| `sale_mode` | нет (default: pieces) | `pieces` / `packages` |
| `linked_order` | нет | ID заявки |
| `comment` | нет | Комментарий |
| `stock_form` | нет | Форма учёта склада (для упаковок: обязательно) |
| `piece_pick` | нет (обяз. для packed) | `loose_remainder` / `from_sealed_package` / `from_open_package` |
| `force_credit_override` | нет | `true` для обхода hard-лимита (нужно право) |

**Backend автоматически:**
- Проверяет hard credit limit (если client задан)
- Рассчитывает revenue, cost, profit
- Списывает со склада
- Исполняет активные резервы (если linked_order задан)
- Обновляет OrderLine.shipped_quantity

Ошибки: 400 `credit_limit` (hard-блокировка без override), 400 по validation полей

---

#### `PATCH /api/sales/{id}/`
Обновить продажу (только поля без склада). Нельзя менять `warehouse_batch`, `quantity`, `stock_form`, `piece_pick` после создания.

---

#### `DELETE /api/sales/{id}/`
Удалить продажу (+ автоматически удаляет связанные Shipment).

---

#### `PATCH /api/sales/{id}/status/`
Сменить статус продажи через state machine.  
Body: `{ "status": "shipped", "force_credit_override": false }`

При переводе в `shipped` / `closed`:
- Проверяет наличие партии и её доступность
- Проверяет hard credit limit

Ошибки: 400 `MISSING_STATUS`, 422 `INVALID_STATUS_TRANSITION`, 422 `SHIP_BLOCKED`, 422 `CREDIT_LIMIT_BLOCKED`

---

#### `GET /api/sales/{id}/credit-check/`
Проверка кредитного лимита клиента с учётом текущей выручки продажи.  
Response:
```json
{
  "client_id": 5,
  "credit_limit": "500000.00",
  "current_debt": "120000.00",
  "credit_used": "120000.00",
  "credit_available": "380000.00",
  "is_over_limit": false,
  "block_mode": "hard",
  "warning": null,
  "blocked": false
}
```

---

#### `GET /api/sales/{id}/nakladnaya/`
HTML-накладная продажи. Content-Type: `text/html`.

---

#### `GET /api/sales/{id}/waybill/`
То же что nakladnaya (алиас).

---

#### `GET /api/sales/{id}/invoice/`
То же что nakladnaya (алиас).

---

#### `GET /api/sales/{id}/receipt/`
HTML-квитанция об оплате.

---

### PAYMENTS

**Access key:** `payments`

---

#### `GET /api/payments/`
Список оплат.  
Query params: `client`, `linked_order`, `linked_sale`, `payment_type`, `date_from`, `date_to`, `ordering` (id, date)

---

#### `GET /api/payments/{id}/`
Карточка оплаты.

---

#### `POST /api/payments/`
Создать оплату.  
Body: `date`, `client`, `payment_type`, `amount`, `payment_method`, `linked_order?`, `linked_sale?`, `payment_number?`, `comment?`

---

#### `PATCH /api/payments/{id}/`
Обновить оплату.

---

#### `DELETE /api/payments/{id}/`
Удалить оплату.

---

#### `GET /api/payments/summary/?client_id={id}`
Финансовая сводка по оплатам клиента.  
Response:
```json
{
  "client_id": 5,
  "client_name": "ООО Ромашка",
  "total_paid_gross": "200000.00",
  "total_refunded": "10000.00",
  "total_paid_net": "190000.00",
  "total_revenue": "170000.00",
  "client_debt_money": "0.00",
  "client_advance_amount": "20000.00"
}
```

---

#### `GET /api/client-financial-summary/?client_id={id}`
Расширенная финансовая сводка (см. раздел 1).  
**Access key:** `clients`

---

### RETURNS

**Access key:** `returns`

---

#### `GET /api/returns/`
Список возвратов.  
Query params: `client` (через sale__client), `date_from`, `date_to`, `ordering` (id, date)

---

#### `GET /api/returns/{id}/`
Карточка возврата + вложенные `lines[]`.

---

#### `POST /api/returns/`
Создать возврат.  
Body: `date`, `sale` (ID), `return_reason?`, `invoice_number?`, `linked_order?`, `comment?`, `lines[]`  
`lines[]` = `{product, quantity, return_target, condition_type, sale_line?, comment?}`

---

#### `PATCH /api/returns/{id}/`
Обновить возврат.

---

#### `DELETE /api/returns/{id}/`
Удалить возврат.

---

#### `GET /api/returns/{id}/nakladnaya/`
HTML-акт возврата.

---

### DEFECTS

**Access key:** `defects`

---

#### `GET /api/defects/`
Список записей брака.  
Query params: `status`, `source_type`, `ordering` (id, created_at, status)

---

#### `GET /api/defects/{id}/`
Карточка брака.

---

#### `POST /api/defects/`
Создать запись брака.  
Body: `source_type`, `source_id?`, `profile?`, `product`, `quantity_pcs`, `quantity_kg?`, `defect_reason?`, `comment?`

---

#### `PATCH /api/defects/{id}/`
Обновить брак.

---

#### `DELETE /api/defects/{id}/`
Удалить брак.

---

#### `POST /api/defects/{id}/send-to-rework/`
Передать брак на переработку (статус `new` или `on_stock` → `sent_to_rework`).  
Ошибка 422 если статус не допускает.

---

#### `POST /api/defects/{id}/complete-rework/`
Завершить переработку (`sent_to_rework` → `reworked`).  
Ошибка 422 если статус не допускает.

---

#### `POST /api/defects/{id}/writeoff/`
Списать брак.  
Body: `{ "writeoff_reason": "..." }` (обязательно)  
Ошибка 400 `MISSING_REASON` если не передан, 422 если статус не допускает.

---

#### `POST /api/defects/{id}/sell/`
Продать брак (создаёт Sale с `is_defect_sale=True`).  
Только из статуса `on_stock` или `reworked`.  
Body: `client_id?`, `price?`, `quantity?`, `date?`, `comment?`  
Response: `{ "sale_id": ..., "sale_order_number": "..." }` с HTTP 201  
Ошибка 422 если статус не допускает.

---

### REWORK REQUESTS

**Access key:** `defects`

---

#### `GET /api/rework-requests/`
Список переделок.  
Query params: `status`, `original_sale`

---

#### `GET /api/rework-requests/{id}/`
Карточка переделки.

---

#### `POST /api/rework-requests/`
Создать переделку.  
Body: `return_doc` (обяз.), `defect_record?`, `original_sale?`, `product?`, `quantity_kg`, `comment?`

---

#### `PATCH /api/rework-requests/{id}/`
Обновить переделку.

---

#### `POST /api/rework-requests/{id}/start/`
Перевести в `in_progress` (из `pending`).  
Ошибка 422 если статус не допускает.

---

#### `POST /api/rework-requests/{id}/complete/`
Завершить переделку (из `in_progress`).  
Body:
```json
{
  "result_warehouse_batch_id": 99,
  "output_quantity_kg": "45.5",
  "loss_kg": "4.5"
}
```
`result_warehouse_batch_id` — обязательно.  
Backend автоматически рассчитывает `conversion_rate` и `loss_kg` (если не передан).  
Автоматически меняет статус связанного DefectRecord на `reworked`.  
Ошибка 400 `MISSING_BATCH`, 404 `NOT_FOUND`, 422 `INVALID_STATUS`.

---

#### `POST /api/rework-requests/{id}/cancel/`
Отменить переделку (из `pending` или `in_progress`).  
Ошибка 422 если статус не допускает.

---

### WAREHOUSE

**Access key:** `warehouse`

---

#### `GET /api/warehouse/batches/`
Список партий склада ГП.  
Query params: `status`, `quality`, `product` (фильтр по тексту), `ordering` (id, date)  
Response включает: `quantity`, `reserved_quantity`, `available_quantity`, `inventory_form`, `quality`, `status`  
По умолчанию исключаются тестовые записи (product = 'test' / 'тест'). Для debug: `?debug=1`

---

#### `GET /api/warehouse/batches/{id}/`
Карточка партии. Включает снимок ОТК и параметры упаковки.

---

#### `POST /api/warehouse/batches/reserve/`
Резервирование партии (warehouse-level, без привязки к заявке).  
Body: `batch_id` (или `batchId`), `quantity`, `sale_id?`  
Ошибки: 400 `validation_error`, 404 `NOT_FOUND`

---

#### `POST /api/warehouse/batches/package/`
Упаковать партию (перевод в `packed`/`open_package` форму).

---

#### `GET /api/warehouse/batches/{id}/trace/`
Полная трассировка партии.  
Response: `production_batch`, `otk_checks[]`, `sale_lines[]`, `return_lines[]`, `defect_records[]`, `rework_requests[]`, `reservations[]`, `active_reservations[]`

---

### PRICES

**Access key:** `sales`

---

#### `GET /api/price-lists/suggest-price/?client_id=&profile_id=&product=&date=`
Рекомендованная цена. Приоритет: ClientPrice → ProductPrice → null.  
Response: `{ price, source: "client_price" / "product_price" / null, unit }`

---

## 3. СТАТУСЫ И ПЕРЕХОДЫ

---

### Order (Заявка)

| Код | Русский |
|---|---|
| `new` | Новая |
| `confirmed` | Подтверждена |
| `in_progress` | В работе |
| `partially_shipped` | Частично отгружена |
| `shipped` | Отгружена |
| `closed` | Закрыта |
| `canceled` | Отменена |

**Допустимые переходы:**

```
new → confirmed, canceled
confirmed → in_progress, canceled
in_progress → partially_shipped, shipped, canceled
partially_shipped → shipped, closed, canceled
shipped → closed
closed → (нет)
canceled → (нет)
```

**Ограничения при `closed`:**
- Нет активных резервов по строкам
- Все строки полностью отгружены

Фронт: кнопку «Закрыть» показывать только в статусах `partially_shipped`, `shipped`; блокировать если `has_company_debt_by_goods = true` и пользователь не выбрал исключение.

---

### Sale (Продажа)

| Код | Русский |
|---|---|
| `draft` | Черновик |
| `confirmed` | Подтверждена |
| `partially_shipped` | Частично отгружена |
| `shipped` | Отгружена |
| `closed` | Закрыта |
| `canceled` | Отменена |

**Допустимые переходы:**

```
draft → confirmed, canceled
confirmed → partially_shipped, shipped, canceled
partially_shipped → shipped, closed, canceled
shipped → closed
closed → (нет)
canceled → (нет)
```

**Ограничения при `shipped` / `closed`:** backend проверяет партию и hard credit limit.

Фронт: **использовать только `PATCH /api/sales/{id}/status/`** для смены статуса.  
Кнопки «Отгрузить» / «Закрыть» блокировать если `sale_status` не допускает перехода.

---

### DefectRecord (Брак)

| Код | Русский |
|---|---|
| `new` | Новый |
| `on_stock` | На складе брака |
| `sent_to_rework` | На переработке |
| `reworked` | Переработан |
| `sold` | Продан |
| `written_off` | Списан |

**Допустимые переходы:**

```
new → on_stock, sent_to_rework, written_off
on_stock → sent_to_rework, sold, written_off
sent_to_rework → reworked, on_stock
reworked → sold, written_off
sold → (нет)
written_off → (нет)
```

**Кнопки действий по статусу:**

| Кнопка | Когда показывать |
|---|---|
| «На переработку» | `new`, `on_stock` |
| «Завершить переработку» | `sent_to_rework` |
| «Списать» | `new`, `on_stock`, `sent_to_rework`, `reworked` (не `sold`, не `written_off`) |
| «Продать» | `on_stock`, `reworked` |

---

### ReworkRequest (Переделка)

| Код | Русский |
|---|---|
| `pending` | Ожидает |
| `in_progress` | В работе |
| `completed` | Завершено |
| `canceled` | Отменено |

**Допустимые переходы:**

```
pending → in_progress, canceled
in_progress → completed, canceled
completed → (нет)
canceled → (нет)
```

**Кнопки действий:**

| Кнопка | Когда показывать |
|---|---|
| «В работу» | `pending` |
| «Завершить» | `in_progress` |
| «Отменить» | `pending`, `in_progress` |

---

### WarehouseBatch (Партия склада)

| Код | Русский |
|---|---|
| `available` | Доступна |
| `reserved` | Зарезервирована (legacy) |
| `shipped` | Отгружена (остаток = 0) |

Фронт: партии в статусе `shipped` не показывать в выпадающих для выбора при создании Sale (backend фильтрует).  
Партии качества `defect` (`quality = "defect"`) нельзя резервировать — показывать только для информации.

---

## 4. БИЗНЕС-ПРАВИЛА ДЛЯ ФРОНТА

---

### Hard Credit Limit

- Backend блокирует создание Sale если `client.credit_limit_mode = "hard"` и лимит превышен.
- Backend возвращает HTTP 400 с `{ "credit_limit": "Кредитный лимит превышен..." }`.
- Фронт должен показать пользователю эту ошибку явно, не игнорировать.
- Если пользователь имеет право `credit_limit_override` (или `is_staff`) — он может передать `force_credit_override: true` в body Sale.
- Фронт должен показывать кнопку «Создать с превышением лимита» только пользователям с соответствующим правом.
- Для мягкого режима (`soft`) backend не блокирует, но возвращает `warning` в credit-check — фронт должен показать предупреждение.

---

### Смена статуса Sale

- Всегда через `PATCH /api/sales/{id}/status/`.
- **Нельзя** менять `sale_status` прямым `PATCH /api/sales/{id}/` с полем `sale_status`.
- Backend проверяет допустимость перехода, кредитный лимит, склад.

---

### Смена статуса Order

- Всегда через `PATCH /api/orders/{id}/status/`.
- Отмена заявки — через `PATCH /api/orders/{id}/cancel/` (автоматически снимает резервы).

---

### Закрытие Order

- Backend блокирует `closed` если:
  - Есть активные резервы по строкам заявки
  - Хотя бы одна строка не полностью отгружена
- Фронт должен показать понятное сообщение об ошибке из ответа `ORDER_CLOSE_BLOCKED`.

---

### available_quantity и reserved_quantity

- Фронт **не считает** `available_quantity` сам.
- Брать только из `/api/warehouse/batches/` → поле `available_quantity`.
- Это `quantity − reserved_quantity` — рассчитывается backend.

---

### Резерв

- Резерв создаётся только через `POST /api/orders/{id}/reserve/`.
- При создании Sale с `linked_order` — backend **автоматически** исполняет активные резервы.
- Фронт **не должен** вызывать `fulfill_reservation` вручную после создания Sale.
- При отмене Sale — резервы **автоматически** восстанавливаются backend-ом при вызове функции восстановления (если реализовано на уровне delete/cancel Sale).

---

### Политика продажи без резерва

- По умолчанию продажа без резерва разрешена.
- Если на сервере установлено `SALE_REQUIRES_RESERVATION = True` — backend вернёт 400 с `{ "linked_order": "Продажа без активного резерва запрещена..." }`.
- Фронт: перед созданием Sale проверить через `GET /api/orders/{id}/reservations/` наличие активного резерва и предупредить пользователя.

---

### Ограничения на количество возврата

- Backend проверяет: `return_line.quantity` не может превышать `sale_line.quantity` минус уже возвращённое.
- Фронт: показывать max-значение в поле quantity при добавлении строки возврата.

---

### Брак: продажа и списание

- Продать брак можно только из статусов `on_stock` или `reworked`.
- Списать брак можно из `new`, `on_stock`, `sent_to_rework`, `reworked` — но не из `sold` / `written_off`.
- Фронт: показывать кнопки действий только для допустимых статусов.

---

### Переделка: завершение

- Завершить переделку можно только из статуса `in_progress`.
- Нужно указать `result_warehouse_batch_id`.
- Фронт: блокировать кнопку «Завершить» если нет выбранной партии.

---

### Трассировка

- `GET /api/warehouse/batches/{id}/trace/` показывает всю цепочку от производства до возврата.
- Фронт может использовать для карточки партии с раскрытием полной истории.

---

## 5. ПОЛЯ — ИСТОЧНИКИ ИСТИНЫ

Следующие поля **вычисляются только backend-ом**. Фронт берёт их из API и **не считает самостоятельно**:

| Сущность | Поле | Откуда брать |
|---|---|---|
| Order | `total_amount` | GET /api/orders/{id}/ |
| Order | `shipped_amount` | GET /api/orders/{id}/ |
| Order | `remaining_amount` | GET /api/orders/{id}/ |
| Order | `has_company_debt_by_goods` | GET /api/orders/{id}/ |
| OrderLine | `remaining_quantity` | вложено в Order |
| OrderLine | `available_to_ship` | вложено в Order |
| OrderLine | `remaining_to_reserve` | вложено в Order |
| OrderLine | `line_total` | вложено в Order |
| OrderLine | `reserved_quantity` | вложено в Order (обновляется auto) |
| OrderLine | `shipped_quantity` | вложено в Order (обновляется auto) |
| Sale | `revenue` | GET /api/sales/{id}/ |
| Sale | `cost` | GET /api/sales/{id}/ |
| Sale | `profit` | GET /api/sales/{id}/ |
| Sale | `total_meters` | GET /api/sales/{id}/ |
| SaleLine | `line_total` | вложено в Sale |
| SaleLine | `cost` | вложено в Sale |
| SaleLine | `profit` | вложено в Sale |
| OrderReservation | `fulfilled_quantity` | GET /api/orders/{id}/reservations/ |
| OrderReservation | `status` | GET /api/orders/{id}/reservations/ |
| WarehouseBatch | `reserved_quantity` | GET /api/warehouse/batches/ |
| WarehouseBatch | `available_quantity` | GET /api/warehouse/batches/ |
| Client | `client_debt_money` | GET /api/client-financial-summary/?client_id= |
| Client | `client_advance_amount` | GET /api/client-financial-summary/?client_id= |
| Client | `credit_available` | GET /api/client-financial-summary/?client_id= |
| Client | `is_over_limit` | GET /api/client-financial-summary/?client_id= |
| ReworkRequest | `rework_loss_kg` | GET /api/rework-requests/{id}/ |
| ReworkRequest | `conversion_rate` | GET /api/rework-requests/{id}/ (при complete) |
| Payment | `client_debt_money` (итог) | GET /api/payments/summary/?client_id= |

---

## 6. ЧТО ФРОНТ ДОЛЖЕН СКРЫТЬ

### Технические поля (не показывать в UI):

| Поле | Причина |
|---|---|
| `updated_at` | Служебный timestamp |
| `source_id` (DefectRecord) | Внутренний FK на otk_check / return_line |
| `stock_form`, `piece_pick` | Внутренние коды складского учёта |
| `stock_quality` (Sale) | Внутренний снимок качества |
| `cost` (Sale, SaleLine) | Конфиденциальная себестоимость |
| `profit` (Sale, SaleLine) | Конфиденциальная прибыль |
| `cost_per_piece`, `cost_per_meter` (WarehouseBatch) | Конфиденциальная себестоимость |
| `conversion_rate` (ReworkRequest) | Аналитика, не нужна оператору |
| `recovered_output` (ReworkRequest) | Дублирует `output_quantity_kg` |
| `kg_coefficient` (DefectRecord) | Расчётный коэффициент |
| `sale_number`, `receipt_number` | Только если задано пользователем — иначе пусто |
| `quantity_input` (Sale) | Внутренний для package-режима |

### ID которые не показывать напрямую пользователю:

- `id` в списках (использовать для навигации, но не отображать)
- `source_id` (DefectRecord)
- `sale_line` FK в OrderReservation

### Поля которые не должны быть в публичных формах:

- `is_defect_sale` (только для внутренней логики)
- `created_by` (ставится backend автоматически)
- `profit`, `cost` (конфиденциально от клиента)

---

## 7. WEBSOCKET (REALTIME)

Backend публикует события через WebSocket / realtime при изменении сущностей.

### Ресурсы и когда обновляться:

| Resource | Событие | Что делать фронту |
|---|---|---|
| `order` | `created` / `updated` | Refetch списка заявок; обновить карточку |
| `sale` | `created` / `updated` | Refetch списка продаж; обновить дашборд |
| `payment` | `created` | Refetch оплат клиента; обновить сводку долга |
| `return` | `created` | Refetch возвратов; обновить историю продажи |
| `defect_record` | `created` / `updated` | Refetch списка брака |
| `rework_request` | `created` / `updated` | Refetch списка переделок |
| `warehouse_batch` | — | Нет прямого события (refetch при действии) |

### Структура события:

```json
{
  "resource": "order",
  "action": "created",
  "entity_id": 42,
  "extra": { "client_id": 5, "status": "new" }
}
```

### Какие экраны refetch:

| Экран | Что слушать |
|---|---|
| Список заявок | `order` → `created`, `updated` |
| Карточка заявки | `order` + `payment` (по client_id или entity_id) |
| Список продаж | `sale` → все |
| Карточка клиента | `payment`, `order`, `sale` по client_id |
| Дашборд долга | `payment` → все |
| Список брака | `defect_record` → все |
| Список переделок | `rework_request` → все |

---

## 8. СВОДНАЯ ТАБЛИЦА СУЩНОСТЕЙ

| Сущность | Список | Detail | Create | Update | Статус/Действия | Критичные backend-поля | Ограничения |
|---|---|---|---|---|---|---|---|
| Client | `GET /api/clients/` | `GET /api/clients/{id}/` | `POST /api/clients/` | `PATCH /api/clients/{id}/` | — | credit_limit, credit_limit_mode | DELETE запрещён при наличии продаж |
| Order | `GET /api/orders/` | `GET /api/orders/{id}/` | `POST /api/orders/` | `PATCH /api/orders/{id}/` | `/status/`, `/cancel/` | total_amount, remaining_amount, has_company_debt_by_goods | close блокируется при активных резервах и неотгруженных строках |
| OrderLine | вложено в Order | вложено | вложено | вложено | — | remaining_quantity, available_to_ship, reserved_quantity, shipped_quantity | не считать самостоятельно |
| OrderReservation | `GET /api/order-reservations/` | — | через `/orders/{id}/reserve/` | — | `/orders/{id}/release-reserve/` | status, fulfilled_quantity, sale_line | readonly, управление через Order |
| Sale | `GET /api/sales/` | `GET /api/sales/{id}/` | `POST /api/sales/` | `PATCH /api/sales/{id}/` | `/status/`, `/credit-check/` | revenue, cost, profit | смена статуса только через /status/; hard credit limit блокирует |
| SaleLine | вложено в Sale | вложено | вложено | — | — | line_total, cost, profit | не считать самостоятельно |
| Payment | `GET /api/payments/` | `GET /api/payments/{id}/` | `POST /api/payments/` | `PATCH /api/payments/{id}/` | `/summary/` | client_debt_money, client_advance_amount | тип refund уменьшает чистые поступления |
| Return | `GET /api/returns/` | `GET /api/returns/{id}/` | `POST /api/returns/` | `PATCH /api/returns/{id}/` | — | — | quantity не может превысить sale_line.quantity |
| DefectRecord | `GET /api/defects/` | `GET /api/defects/{id}/` | `POST /api/defects/` | `PATCH /api/defects/{id}/` | `/send-to-rework/`, `/complete-rework/`, `/writeoff/`, `/sell/` | status | действия строго по state machine |
| ReworkRequest | `GET /api/rework-requests/` | `GET /api/rework-requests/{id}/` | `POST /api/rework-requests/` | `PATCH /api/rework-requests/{id}/` | `/start/`, `/complete/`, `/cancel/` | rework_loss_kg, conversion_rate | complete требует result_warehouse_batch_id |
| WarehouseBatch | `GET /api/warehouse/batches/` | `GET /api/warehouse/batches/{id}/` | — | — | `/reserve/`, `/package/`, `/trace/` | available_quantity, reserved_quantity | не считать available_quantity самостоятельно |

---

## 9. ЧТО ФРОНТУ ЗАПРЕЩЕНО ДЕЛАТЬ

1. **Вычислять `available_quantity` самостоятельно.** Брать только с backend. `available_quantity = quantity − reserved_quantity`, но backend может учитывать дополнительные факторы.

2. **Менять статус Sale прямым PATCH.** Только через `PATCH /api/sales/{id}/status/`. Прямой PATCH поля `sale_status` не применяется правила state machine.

3. **Менять статус Order прямым PATCH.** Только через `/status/` или `/cancel/`.

4. **Игнорировать ошибку `credit_limit` (код 400/422).** При получении — показать пользователю понятное сообщение. Разрешать передачу `force_credit_override: true` только пользователям с соответствующим правом.

5. **Показывать `cost` и `profit` (Sale, SaleLine) клиенту.** Это конфиденциальная себестоимость — только для внутренних ролей.

6. **Вычислять `client_debt_money` самостоятельно.** Долг считается backend с учётом prepayment, refund, cancelled sales — формула сложная.

7. **Вычислять `remaining_quantity`, `reserved_quantity`, `available_to_ship` OrderLine самостоятельно.** Backend обновляет их при каждой операции.

8. **Самостоятельно вызывать fulfill_reservation после создания Sale.** Backend делает это автоматически.

9. **Строить кнопки действий для брака/переделки без проверки текущего статуса.** Действия строго ограничены по статусу — показывать только допустимые.

10. **Передавать `created_by` в POST-запросах.** Backend берёт из токена авторизации.

11. **Отображать `id` как видимый публичный идентификатор.** Использовать `order_number`, `return_number`, `rework_number`, `payment_number` — человекочитаемые номера.

12. **Давать пользователю вводить `revenue`, `cost`, `profit`, `total_meters` в форму Sale.** Эти поля рассчитываются backend автоматически.
