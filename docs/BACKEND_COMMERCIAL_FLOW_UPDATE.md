# BACKEND COMMERCIAL FLOW UPDATE
## Документация коммерческого контура DIAS ERP

**Дата:** 2026-04-23  
**Версия:** 1.0  
**Миграция:** `sales/0013_commercial_flow`

---

## 1. ОБЗОР ИЗМЕНЕНИЙ

Добавлен полноценный коммерческий контур:

| Сущность | Таблица | Статус |
|---|---|---|
| `Order` (Заявка) | `client_orders` | Новая |
| `OrderLine` (Строка заявки) | `order_lines` | Новая |
| `SaleLine` (Строка продажи) | `sale_lines` | Новая |
| `Payment` (Оплата) | `payments` | Новая |
| `Return` (Возврат) | `returns` | Новая |
| `ReturnLine` (Строка возврата) | `return_lines` | Новая |
| `DefectRecord` (Брак) | `defect_records` | Новая |
| `ReworkRequest` (Переделка) | `rework_requests` | Новая |
| `Sale` (Продажа) | `sales` | Расширена |

---

## 2. НОВЫЕ МОДЕЛИ

### 2.1 Order — Заявка клиента

**Таблица:** `client_orders`

Заявка — это **намерение клиента**, не перемещение склада. Заявка не списывает склад, не создаёт движение денег. Это документ о потребности.

```
Поля:
  id                  int PK
  order_number        str(100) unique  — автогенерация ORD-{YEAR}-{NNNN}
  date                date
  client_id           FK → clients (nullable)
  source_type         str  — cashier | manager | boss | other
  comment             text
  status              str  — см. статусы ниже
  created_by_id       FK → users (nullable)
  responsible_user_id FK → users (nullable)
  created_at          datetime auto
  updated_at          datetime auto
```

**Статусы заявки:**

| Код | Название | Переходы из |
|---|---|---|
| `new` | Новая | — (начальный) |
| `confirmed` | Подтверждена | `new` |
| `in_progress` | В работе | `confirmed` |
| `partially_shipped` | Частично отгружена | `in_progress` |
| `shipped` | Отгружена | `in_progress`, `partially_shipped` |
| `closed` | Закрыта | `shipped`, `partially_shipped` |
| `canceled` | Отменена | `new`, `confirmed`, `in_progress`, `partially_shipped` |

Переходы строго валидируются на endpoint `PATCH /api/orders/{id}/status/`.

**Вычисляемые поля (свойства):**

| Поле | Описание |
|---|---|
| `total_amount` | Сумма всех строк по ценам |
| `shipped_amount` | Отгружено × цена |
| `remaining_amount` | Остаток к отгрузке × цена |
| `paid_amount` | Сумма привязанных оплат (кроме возвратов денег) |
| `has_company_debt_by_goods` | True если есть неотгруженные строки |

---

### 2.2 OrderLine — Строка заявки

**Таблица:** `order_lines`

```
Поля:
  id                int PK
  order_id          FK → client_orders
  product           str(255)       — наименование
  product_type      str(100)       — тип товара
  profile_id        FK → plastic_profiles (nullable)
  ordered_quantity  decimal(14,4)  — заказано
  shipped_quantity  decimal(14,4)  — отгружено (обновляется при продаже)
  unit_price        decimal(14,2)  — цена за ед. (если известна)
  comment           text
```

**Вычисляемые поля:**
- `remaining_quantity` = max(0, `ordered_quantity` − `shipped_quantity`)
- `line_total` = `ordered_quantity` × `unit_price`

---

### 2.3 SaleLine — Строка многострочной продажи

**Таблица:** `sale_lines`

Новый формат продажи поддерживает несколько строк. Старые продажи (`Sale`) остаются в обратной совместимости.

```
Поля:
  id                int PK
  sale_id           FK → sales
  order_line_id     FK → order_lines (nullable)
  product           str(255)
  warehouse_batch_id FK → warehouse_batches (nullable)
  stock_form        str(20)
  quantity          decimal(14,4)
  unit_price        decimal(14,2) (nullable)
  line_total        decimal(16,2)
  cost              decimal(16,2)   — себестоимость строки
  defect_flag       bool            — строка брака
  comment           text
```

---

### 2.4 Payment — Оплата

**Таблица:** `payments`

Деньги живут отдельно от товара. Оплата не равна продаже.

```
Поля:
  id                int PK
  payment_number    str(100) — автогенерация PAY-{YEAR}-{NNNN}
  date              date
  client_id         FK → clients (nullable)
  linked_order_id   FK → client_orders (nullable)
  linked_sale_id    FK → sales (nullable)
  payment_type      str  — prepayment | payment | surcharge | refund
  amount            decimal(16,2)
  payment_method    str  — cash | transfer | card | other
  comment           text
  created_by_id     FK → users (nullable)
  created_at        datetime auto
```

**Типы оплат:**

| Код | Название | Влияние на баланс |
|---|---|---|
| `prepayment` | Предоплата | +сумма (аванс) |
| `payment` | Оплата | +сумма |
| `surcharge` | Доплата | +сумма |
| `refund` | Возврат денег | −сумма |

**Логика долга/аванса клиента:**

```
net_paid = SUM(prepayment + payment + surcharge) - SUM(refund)
total_revenue = SUM(Sale.revenue)  # по клиенту

if net_paid < total_revenue:
    client_debt_money = total_revenue - net_paid   # клиент должен
    client_advance_amount = 0
else:
    client_debt_money = 0
    client_advance_amount = net_paid - total_revenue  # аванс клиента
```

---

### 2.5 Return — Возврат товара

**Таблица:** `returns`

Возврат всегда привязан к конкретной продаже. Нельзя вернуть больше, чем было отгружено по строке.

```
Поля:
  id                int PK
  return_number     str(100) — автогенерация RET-{YEAR}-{NNNN}
  date              date
  sale_id           FK → sales (PROTECT)
  linked_order_id   FK → client_orders (nullable)
  invoice_number    str(100)   — номер накладной возврата
  return_reason     text
  comment           text
  created_by_id     FK → users (nullable)
  created_at        datetime auto
```

---

### 2.6 ReturnLine — Строка возврата

**Таблица:** `return_lines`

```
Поля:
  id                int PK
  return_doc_id     FK → returns
  sale_line_id      FK → sale_lines (nullable)
  product           str(255)
  quantity          decimal(14,4)
  return_target     str  — warehouse | defect | rework
  condition_type    str  — good | damaged | defect
  comment           text
```

**return_target — автоматическая обработка:**

| Значение | Действие при сохранении |
|---|---|
| `warehouse` | Товар возвращается на WarehouseBatch (quantity+, status=available) |
| `defect` | Создаётся `DefectRecord` (status=on_stock) |
| `rework` | Создаётся `DefectRecord` (status=sent_to_rework) + `ReworkRequest` (status=pending) |

**Валидация:**  
`SUM(return_lines.quantity for sale_line) ≤ sale_line.quantity`

---

### 2.7 DefectRecord — Учёт брака

**Таблица:** `defect_records`

Отдельный контур брака. Никогда не смешивается с обычным складом.

```
Поля:
  id                int PK
  source_type       str  — otk | return
  source_id         int  — id источника (OtkCheck.id или ReturnLine.id)
  profile_id        FK → plastic_profiles (nullable)
  product           str(255)
  quantity_pcs      decimal(14,4)   — в шт или метрах
  quantity_kg       decimal(14,4)   — в кг (nullable, заполняется при переработке)
  kg_coefficient    decimal(14,6)   — коэффициент кг/ед.
  defect_reason     text
  status            str  — см. статусы
  writeoff_reason   text  — обязателен при статусе written_off
  comment           text
  created_by_id     FK → users (nullable)
  created_at        datetime auto
  updated_at        datetime auto
```

**Статусы брака:**

| Код | Название | Операция |
|---|---|---|
| `new` | Новый | — |
| `on_stock` | На складе брака | — |
| `sent_to_rework` | На переработке | POST `/api/defects/{id}/send-to-rework/` |
| `reworked` | Переработан | POST `/api/defects/{id}/complete-rework/` |
| `sold` | Продан | POST `/api/defects/{id}/sell/` |
| `written_off` | Списан | POST `/api/defects/{id}/writeoff/` |

**Важно:** `written_off` требует `writeoff_reason` — без причины не пройдёт валидацию.

---

### 2.8 ReworkRequest — Переделка

**Таблица:** `rework_requests`

Трассировка цепочки: продажа → возврат → брак → переделка → новая партия ГП.

```
Поля:
  id                       int PK
  rework_number            str(100) — автогенерация RWK-{YEAR}-{NNNN}
  return_doc_id            FK → returns (PROTECT)
  defect_record_id         FK → defect_records (nullable)
  original_sale_id         FK → sales (nullable)
  product                  str(255)
  quantity_kg              decimal(14,4)
  status                   str  — pending | in_progress | completed | canceled
  result_warehouse_batch_id FK → warehouse_batches (nullable)
  comment                  text
  created_by_id            FK → users (nullable)
  created_at               datetime auto
  updated_at               datetime auto
```

**Завершение переделки:**  
`POST /api/rework-requests/{id}/complete/` + тело `{ "result_warehouse_batch_id": N }`

При завершении: status=completed, result_warehouse_batch привязывается, DefectRecord → status=reworked.

---

## 3. ИЗМЕНЕНИЯ В СУЩЕСТВУЮЩЕЙ МОДЕЛИ Sale

Добавлены новые поля (все nullable/default — обратная совместимость сохранена):

| Поле | Тип | Описание |
|---|---|---|
| `sale_number` | str(100) | Номер продажи |
| `invoice_number` | str(100) | Номер накладной |
| `receipt_number` | str(100) | Номер квитанции |
| `sale_status` | str(25) | Статус продажи (default=`closed` для старых записей) |
| `linked_order_id` | FK | Связь с заявкой (nullable) |
| `is_defect_sale` | bool | Флаг продажи брака (default=False) |
| `created_by_id` | FK | Кто создал (nullable) |
| `created_at` | datetime | Дата создания (nullable) |

**Статусы продажи:**

| Код | Название |
|---|---|
| `draft` | Черновик |
| `confirmed` | Подтверждена |
| `partially_shipped` | Частично отгружена |
| `shipped` | Отгружена |
| `closed` | Закрыта |
| `canceled` | Отменена |

**Старые продажи** (существующие) получают `sale_status='closed'` по умолчанию — они считаются финализированными историческими записями.

---

## 4. НОВЫЕ API ENDPOINTS

### 4.1 Заявки (Orders)

| Метод | URL | Описание | Access Key |
|---|---|---|---|
| GET | `/api/orders/` | Список заявок | `client_orders` |
| POST | `/api/orders/` | Создать заявку (со строками) | `client_orders` |
| GET | `/api/orders/{id}/` | Получить заявку | `client_orders` |
| PATCH | `/api/orders/{id}/` | Изменить заявку | `client_orders` |
| DELETE | `/api/orders/{id}/` | Удалить заявку | `client_orders` |
| PATCH | `/api/orders/{id}/status/` | Сменить статус (с валидацией перехода) | `client_orders` |
| GET | `/api/orders/{id}/nakladnaya/` | HTML-накладная заявки | `client_orders` |
| GET | `/api/orders/{id}/history/` | Трассировка заявки | `client_orders` |

**Фильтры:** `client_id`, `status`, `source_type`, `date_from`, `date_to`

**Пример создания заявки:**
```json
POST /api/orders/
{
  "client": 5,
  "source_type": "manager",
  "comment": "Срочно нужен профиль",
  "lines": [
    {
      "product": "Профиль ПВХ 60×40",
      "product_type": "профиль",
      "ordered_quantity": "100.0000",
      "unit_price": "450.00"
    },
    {
      "product": "Уголок 30×30",
      "ordered_quantity": "50.0000",
      "unit_price": "120.00"
    }
  ]
}
```

---

### 4.2 Оплаты (Payments)

| Метод | URL | Описание | Access Key |
|---|---|---|---|
| GET | `/api/payments/` | Список оплат | `payments` |
| POST | `/api/payments/` | Зафиксировать оплату | `payments` |
| GET | `/api/payments/{id}/` | Получить оплату | `payments` |
| PATCH | `/api/payments/{id}/` | Изменить оплату | `payments` |
| DELETE | `/api/payments/{id}/` | Удалить оплату | `payments` |
| GET | `/api/payments/summary/?client_id=N` | Сводка по клиенту | `payments` |

**Фильтры:** `client_id`, `payment_type`, `payment_method`, `date_from`, `date_to`, `linked_order`, `linked_sale`

**Пример предоплаты:**
```json
POST /api/payments/
{
  "client": 5,
  "linked_order": 12,
  "payment_type": "prepayment",
  "amount": "15000.00",
  "payment_method": "transfer",
  "comment": "Предоплата по заявке ORD-2026-0012"
}
```

**Пример ответа /api/payments/summary/?client_id=5:**
```json
{
  "client_id": 5,
  "client_name": "ООО Ромашка",
  "total_paid_gross": "50000.00",
  "total_refunded": "5000.00",
  "total_paid_net": "45000.00",
  "total_revenue": "48000.00",
  "client_debt_money": "3000.00",
  "client_advance_amount": "0.00"
}
```

---

### 4.3 Возвраты (Returns)

| Метод | URL | Описание | Access Key |
|---|---|---|---|
| GET | `/api/returns/` | Список возвратов | `returns` |
| POST | `/api/returns/` | Создать возврат | `returns` |
| GET | `/api/returns/{id}/` | Получить возврат | `returns` |
| PATCH | `/api/returns/{id}/` | Изменить возврат | `returns` |
| DELETE | `/api/returns/{id}/` | Удалить возврат | `returns` |
| GET | `/api/returns/{id}/nakladnaya/` | HTML-акт возврата | `returns` |

**Фильтры:** `sale_id`, `client_id`, `date_from`, `date_to`

**Пример возврата на переделку:**
```json
POST /api/returns/
{
  "sale": 101,
  "return_reason": "Дефект геометрии",
  "lines": [
    {
      "sale_line": 45,
      "product": "Профиль ПВХ 60×40",
      "quantity": "10.0000",
      "return_target": "rework",
      "condition_type": "damaged",
      "comment": "Кривизна профиля"
    }
  ]
}
```

**Автоматически создаётся:** `DefectRecord` (status=sent_to_rework) + `ReworkRequest` (status=pending)

---

### 4.4 Брак (Defects)

| Метод | URL | Описание | Access Key |
|---|---|---|---|
| GET | `/api/defects/` | Список записей брака | `defects` |
| POST | `/api/defects/` | Создать запись брака | `defects` |
| GET | `/api/defects/{id}/` | Получить запись | `defects` |
| PATCH | `/api/defects/{id}/` | Изменить запись | `defects` |
| DELETE | `/api/defects/{id}/` | Удалить запись | `defects` |
| POST | `/api/defects/{id}/send-to-rework/` | Передать на переработку | `defects` |
| POST | `/api/defects/{id}/complete-rework/` | Завершить переработку | `defects` |
| POST | `/api/defects/{id}/writeoff/` | Списать брак | `defects` |
| POST | `/api/defects/{id}/sell/` | Продать брак | `defects` |

**Фильтры:** `source_type`, `status`, `profile_id`

**Пример списания:**
```json
POST /api/defects/7/writeoff/
{
  "writeoff_reason": "Утилизация — критический дефект, переработке не подлежит"
}
```

**Пример продажи брака:**
```json
POST /api/defects/7/sell/
{
  "client_id": 5,
  "price": "150.00",
  "quantity": "50.0000",
  "date": "2026-04-23"
}
```
Создаётся `Sale` с `is_defect_sale=True`, `DefectRecord` → status=sold.

---

### 4.5 Переделки (Rework Requests)

| Метод | URL | Описание | Access Key |
|---|---|---|---|
| GET | `/api/rework-requests/` | Список переделок | `defects` |
| POST | `/api/rework-requests/` | Создать переделку | `defects` |
| GET | `/api/rework-requests/{id}/` | Получить переделку | `defects` |
| PATCH | `/api/rework-requests/{id}/` | Изменить переделку | `defects` |
| POST | `/api/rework-requests/{id}/complete/` | Завершить переделку | `defects` |

**Пример завершения переделки:**
```json
POST /api/rework-requests/3/complete/
{
  "result_warehouse_batch_id": 88
}
```

---

### 4.6 Расширенные endpoints существующих сущностей

#### Клиент — агрегированная история
```
GET /api/clients/{id}/history/
```
Возвращает полную историю клиента:
```json
{
  "client_id": 5,
  "client_name": "ООО Ромашка",
  "orders": [...],
  "sales": [...],
  "payments": [...],
  "returns": [...],
  "total_ordered": "150000.00",
  "total_paid": "120000.00",
  "client_debt_money": "30000.00",
  "client_advance_amount": "0.00",
  "has_unshipped_goods": true
}
```

#### Продажа — квитанция
```
GET /api/sales/{id}/receipt/
```
HTML-квитанция с перечнем оплат.

---

## 5. ДОКУМЕНТЫ (HTML-генераторы)

| Документ | URL | Сущность |
|---|---|---|
| Накладная продажи | `GET /api/sales/{id}/nakladnaya/` | Sale |
| Накладная продажи | `GET /api/sales/{id}/waybill/` | Sale |
| Счёт | `GET /api/sales/{id}/invoice/` | Sale |
| Квитанция | `GET /api/sales/{id}/receipt/` | Sale |
| Накладная заявки | `GET /api/orders/{id}/nakladnaya/` | Order |
| Акт возврата | `GET /api/returns/{id}/nakladnaya/` | Return |

**Содержимое накладной продажи теперь включает:**
- Номер документа (invoice_number / order_number)
- Дата
- Клиент (название, ИНН, адрес, телефон)
- Строки (поддержка SaleLine — новый формат)
- Итого
- Оплачено / остаток (из привязанных Payment)
- Флаг «Продажа брака» (если is_defect_sale=True)

---

## 6. RBAC — НОВЫЕ КЛЮЧИ ДОСТУПА

Добавлены 4 новых access_key:

| Ключ | Раздел |
|---|---|
| `client_orders` | Заявки клиентов |
| `payments` | Оплаты |
| `returns` | Возвраты |
| `defects` | Брак и переделка |

Ключи добавлены в `settings.ACCESS_KEYS`.

**Управление доступом:**
```
PATCH /api/users/{id}/access/
{
  "accesses": ["sales", "clients", "client_orders", "payments", "returns", "defects"]
}
```

---

## 7. WEBSOCKET СОБЫТИЯ (push)

Новые ресурсы для push-событий:

| resource | action | extra |
|---|---|---|
| `order` | created / updated / deleted | `client_id`, `status` |
| `payment` | created / updated / deleted | `client_id`, `payment_type` |
| `return` | created / updated / deleted | `sale_id` |
| `defect_record` | created / updated / deleted | `source_type`, `status` |
| `rework_request` | created / updated / deleted | `status` |

Расширено событие `sale`:
```json
{
  "resource": "sale",
  "action": "updated",
  "id": 42,
  "payload": {
    "client_id": 5,
    "warehouse_batch_id": 12,
    "sale_status": "shipped",
    "is_defect_sale": false
  }
}
```

Фронт по каждому событию делает refetch нужного ресурса — протокол не усложнён.

---

## 8. ЛОГИКА СЦЕНАРИЕВ

### 8.1 Полная заявка → продажа → оплата

```
1. Клиент оставляет заявку
   POST /api/orders/  → status=new

2. Менеджер подтверждает
   PATCH /api/orders/{id}/status/  { "status": "confirmed" }

3. Клиент вносит предоплату
   POST /api/payments/  { "payment_type": "prepayment", "linked_order": {id} }

4. Менеджер создаёт продажу из заявки
   POST /api/sales/  { "linked_order": {id}, "sale_status": "draft" }

5. Отгрузка
   PATCH /api/sales/{id}/  { "sale_status": "shipped" }
   Одновременно: обновить OrderLine.shipped_quantity

6. Финальная оплата (если была предоплата)
   POST /api/payments/  { "payment_type": "payment" или "surcharge" }

7. Закрытие заявки
   PATCH /api/orders/{id}/status/  { "status": "closed" }
```

### 8.2 Частичная отгрузка

```
1. Заявка на 100 шт
2. Первая продажа: 60 шт → OrderLine.shipped_quantity=60
   PATCH /api/orders/{id}/status/ → "partially_shipped"
3. Вторая продажа: 40 шт → OrderLine.shipped_quantity=100
   PATCH /api/orders/{id}/status/ → "shipped"
```

### 8.3 Возврат товара на склад

```
1. Клиент вернул товар
   POST /api/returns/
   {
     "sale": 101,
     "return_reason": "Передумал",
     "lines": [{
       "quantity": 10,
       "return_target": "warehouse",
       "condition_type": "good"
     }]
   }

2. Автоматически:
   WarehouseBatch.quantity += 10
   WarehouseBatch.status = "available" (если был "shipped")
```

### 8.4 Возврат в брак → продажа брака

```
1. Клиент вернул брак
   POST /api/returns/  (return_target="defect")
   Автоматически: DefectRecord создан (status=on_stock)

2. Принять решение о продаже
   POST /api/defects/{id}/sell/
   { "client_id": 7, "price": 100, "quantity": 10 }
   Автоматически: Sale (is_defect_sale=True), DefectRecord → sold
```

### 8.5 Переделка и новый выпуск

```
1. Клиент вернул брак на переделку
   POST /api/returns/  (return_target="rework")
   Автоматически: DefectRecord + ReworkRequest созданы

2. Производство переделало, создали новую партию ГП (WarehouseBatch #99)

3. Завершить переделку
   POST /api/rework-requests/{id}/complete/
   { "result_warehouse_batch_id": 99 }
   Автоматически: DefectRecord → reworked, ReworkRequest → completed
```

---

## 9. ВЛИЯНИЕ НА СКЛАД

### Что меняется:
- `ReturnLine` с `return_target=warehouse` **увеличивает** `WarehouseBatch.quantity`
- Продажа брака (`DefectRecord.sell`) создаёт `Sale` **без списания склада ГП** (брак не на обычном складе)
- Старая логика `apply_sale_to_warehouse_batch` **не изменена**

### Что НЕ меняется:
- FIFO — не тронут
- `stock_ops.py` — не тронут
- Упаковки (packed / unpacked / open_package) — не тронуты
- Себестоимость производства — не тронута
- Существующие Sale (legacy) — обратно совместимы

---

## 10. ВЛИЯНИЕ НА АНАЛИТИКУ

### Что нужно знать:

1. **Обычные продажи** (`Sale.is_defect_sale=False`) — как раньше, идут в выручку.

2. **Продажи брака** (`Sale.is_defect_sale=True`) — технически те же `Sale`, но флаг позволяет фильтровать их отдельно. В аналитике можно различить:
   ```
   GET /api/sales/?is_defect_sale=false  # обычные
   GET /api/sales/?is_defect_sale=true   # продажи брака
   ```

3. **Предоплаты** (`Payment.payment_type=prepayment`) — **не являются выручкой** сами по себе. Выручка фиксируется через `Sale.revenue`. Предоплата — это движение денег.

4. **Возвраты денег** (`Payment.payment_type=refund`) — уменьшают `net_paid`, влияют на долг/аванс клиента. Не уменьшают `Sale.revenue` напрямую — это отдельный документ.

5. **Сводка аналитики** через `/api/payments/summary/?client_id=N` даёт: оплачено, долг, аванс.

---

## 11. ЧТО НЕЛЬЗЯ ДЕЛАТЬ (ограничения сохранены)

- Нельзя вернуть больше, чем отгружено по строке (валидация в `ReturnLineSerializer`)
- Нельзя менять `warehouse_batch`, `quantity`, `stock_form`, `piece_pick` у продажи, уже связанной со складом
- Нельзя смешивать обычный склад и склад брака — они существуют отдельно
- Нельзя списать брак без причины (`writeoff_reason` обязателен)
- Нельзя удалить историю движений склада
- Нельзя перепрыгивать статусы заявки — только по разрешённым переходам

---

## 12. МОДЕЛИ ОТК — НЕ ИЗМЕНЕНЫ

ОТК отвечает только за:
- `accepted` quantity
- `rejected` (defect) quantity  
- `reject_reason`
- `inspector`
- `comment`

Коммерческая цена живёт в `Sale`, `OrderLine`, `Payment` — НЕ в ОТК. ОТК не задаёт цену.

---

## 13. МИГРАЦИЯ И СОВМЕСТИМОСТЬ

**Файл миграции:** `apps/sales/migrations/0013_commercial_flow.py`

**Что добавлено:**
- 8 новых таблиц
- 8 новых полей в таблицу `sales`

**Что не тронуто:**
- Таблицы: `production_batches`, `recipe_runs`, `otk_checks`, `warehouse_batches`, `materials_*`, `chemistry_*`
- Таблица `sales` — существующие строки получают safe defaults

**Применить миграцию:**
```bash
python manage.py migrate sales
```

**Откат (если нужно):**
```bash
python manage.py migrate sales 0012_sale_stock_quality
```

---

## 14. ВАЖНО ДЛЯ ФРОНТЕНДА

1. **Заявка** (`/api/orders/`) — новый ресурс. Используй `client_orders` access key.

2. **Статус заявки** меняется строго через `PATCH /api/orders/{id}/status/` — только разрешённые переходы.

3. **Оплата** — отдельный документ, не поле внутри Sale. Создавай через `/api/payments/`.

4. **Предоплата ≠ продажа.** Предоплата меняет баланс клиента. Продажа фиксирует товарное движение.

5. **Возврат** — обязательно указывай `return_target` для каждой строки. По `return_target` автоматически происходит действие (на склад / в брак / на переделку).

6. **Брак** — отдельный раздел `/api/defects/`. Не смешивай с обычным складом.

7. **Накладные** теперь поддерживают многострочный формат (`SaleLine`). Для старых продаж (без строк) — обратная совместимость, одна строка из `Sale.product`.

8. **WebSocket:** новые ресурсы `order`, `payment`, `return`, `defect_record`, `rework_request`. По событию делай refetch нужного ресурса.

9. **Продажа брака** (`is_defect_sale=True`) — фильтруй отдельно от обычных продаж в аналитике.

10. **История клиента** — `GET /api/clients/{id}/history/` возвращает всё агрегировано.
