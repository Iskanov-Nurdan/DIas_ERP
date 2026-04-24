# BACKEND BUSINESS LOGIC UPGRADE — ИТОГОВАЯ ДОКУМЕНТАЦИЯ

**Версия:** 2.0  
**Дата:** 2026-04-24  
**Миграция:** `sales/0014_business_logic_upgrade`

---

## 1. НОВЫЕ МОДЕЛИ

### `OrderReservation` — Резерв по заявке (`order_reservations`)

| Поле | Тип | Описание |
|------|-----|----------|
| `order_line` | FK → OrderLine | Строка заявки, под которую резервируется |
| `warehouse_batch` | FK → WarehouseBatch | Партия склада ГП |
| `quantity` | Decimal | Зарезервированное количество |
| `status` | CharField | `active` / `released` / `fulfilled` |
| `created_by` | FK → User | Кто создал |
| `comment` | TextField | Комментарий |
| `created_at` / `updated_at` | DateTime | Временные метки |

**Индексы:** `(order_line, status)`, `(warehouse_batch, status)`

---

### `PriceList` — Прайс-лист (`price_lists`)

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | CharField | Название |
| `is_active` | Boolean | Активен |
| `valid_from` / `valid_to` | DateField | Период действия |
| `comment` | TextField | Комментарий |

---

### `ProductPrice` — Цена по прайсу (`product_prices`)

| Поле | Тип | Описание |
|------|-----|----------|
| `price_list` | FK → PriceList | Прайс-лист |
| `profile` | FK → PlasticProfile | Профиль (опционально) |
| `product` | CharField | Текстовый ключ товара (если нет профиля) |
| `price` | Decimal | Цена |
| `unit` | CharField | Единица (`piece`, `meter` и т.д.) |

---

### `ClientPrice` — Индивидуальная цена клиента (`client_prices`)

| Поле | Тип | Описание |
|------|-----|----------|
| `client` | FK → Client | Клиент |
| `profile` | FK → PlasticProfile | Профиль (опционально) |
| `product` | CharField | Текстовый ключ |
| `price` | Decimal | Индивидуальная цена |
| `unit` | CharField | Единица |
| `valid_from` / `valid_to` | DateField | Период действия |

---

## 2. ИЗМЕНЁННЫЕ МОДЕЛИ

### `Client`

Добавлены поля:

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `credit_limit` | Decimal (null) | null | Кредитный лимит клиента |
| `credit_limit_mode` | CharField | `soft` | Режим: `soft` (предупреждение) / `hard` (блокировка) |

---

### `OrderLine`

Добавлены поля и свойства:

| Поле / Свойство | Тип | Описание |
|-----------------|-----|----------|
| `reserved_quantity` | Decimal (DB) | Суммарно зарезервировано (обновляется при резерве/снятии) |
| `available_to_ship` | property | Можно отгрузить = ordered - shipped - (reserved - shipped) |
| `remaining_to_reserve` | property | Ещё можно зарезервировать = ordered - reserved |

---

### `SaleLine`

Добавлено поле:

| Поле | Тип | Описание |
|------|-----|----------|
| `profit` | Decimal | Прибыль строки = line_total - cost (фиксируется на момент продажи) |

---

### `ReworkRequest`

Добавлены поля и свойства:

| Поле / Свойство | Тип | Описание |
|-----------------|-----|----------|
| `output_quantity_kg` | Decimal (null) | Масса выхода ГП после переработки |
| `loss_kg` | Decimal (null) | Потери кг (input - output) |
| `conversion_rate` | Decimal (null) | Коэффициент (output / input) |
| `rework_loss_kg` | property | loss_kg или вычисляется из input - output |
| `recovered_output` | property | output_quantity_kg |

---

## 3. НОВЫЕ СЕРВИСНЫЕ ФАЙЛЫ

### `apps/sales/reservations.py`

Функции:
- `reserve_order_line(order_line, warehouse_batch, quantity, user, comment)` — создать резерв с атомарной проверкой доступного остатка
- `release_reservation(reservation, user)` — снять активный резерв
- `fulfill_reservation(reservation)` — пометить резерв как исполненный (при отгрузке)
- `release_all_for_order(order)` — снять все активные резервы заявки (при отмене)
- `get_available_quantity(batch_pk)` — свободный остаток партии за вычетом активных резервов

**Защиты:**
- Нельзя зарезервировать больше свободного остатка партии
- Нельзя зарезервировать больше остатка по строке заявки
- Нельзя зарезервировать партию брака
- Нельзя зарезервировать отгруженную партию
- Все операции атомарны (`select_for_update`)

---

### `apps/sales/credit_check.py`

Функции:
- `compute_client_debt(client)` — текущий долг клиента (выручка - чистые поступления)
- `check_credit_limit(client, additional_amount)` — проверить лимит с учётом предстоящей суммы
- `credit_check_result_to_dict(result)` — сериализация результата

**Логика долга:**
- Долг = выручка (не-черновик, не-отмена) − (оплаты + предоплаты + доплаты − возвраты денег)
- Предоплата уменьшает долг
- Возврат денег уменьшает поступления
- `soft` режим: предупреждение, не блокирует
- `hard` режим: `blocked=True`, API должен запрещать отгрузку

---

### `apps/sales/pricing.py`

Функции:
- `suggest_price(client_id, profile_id, product, on_date)` — рекомендованная цена
- `price_suggestion_to_dict(suggestion)` — сериализация

**Приоритет цены:**
1. `ClientPrice` действующий на дату по профилю
2. `ClientPrice` действующий на дату по тексту товара
3. `ProductPrice` в активном `PriceList` по профилю
4. `ProductPrice` в активном `PriceList` по тексту
5. `None` — ручной ввод

---

### `apps/sales/state_machine.py`

Централизованная State Machine. Все переходы статусов проходят через неё.

**Order transitions:**
```
new → confirmed, canceled
confirmed → in_progress, canceled
in_progress → partially_shipped, shipped, canceled
partially_shipped → shipped, closed, canceled
shipped → closed
closed → (нет)
canceled → (нет)
```

**Sale transitions:**
```
draft → confirmed, canceled
confirmed → partially_shipped, shipped, canceled
partially_shipped → shipped, closed, canceled
shipped → closed
closed → (нет)
canceled → (нет)
```

**DefectRecord transitions:**
```
new → on_stock, sent_to_rework, written_off
on_stock → sent_to_rework, sold, written_off
sent_to_rework → reworked, on_stock
reworked → sold, written_off
sold → (нет)
written_off → (нет)
```

**ReworkRequest transitions:**
```
pending → in_progress, canceled
in_progress → completed, canceled
completed → (нет)
canceled → (нет)
```

---

## 4. ЛОГИКА РЕЗЕРВА

### Как резерв работает

1. Фронт вызывает `POST /api/orders/{id}/reserve/`
2. Указывает `order_line_id`, `warehouse_batch_id`, `quantity`
3. Backend проверяет:
   - партия существует, не отгружена, не брак
   - `quantity ≤ available_on_batch` (остаток минус другие активные резервы)
   - `quantity ≤ remaining_to_reserve_on_line` (заказано минус уже зарезервировано)
4. Создаёт `OrderReservation(status=active)`
5. Обновляет `OrderLine.reserved_quantity += quantity`

### Свободный остаток партии

```
available = warehouse_batch.quantity - SUM(active reservations on batch)
```

### Строка заявки — видимые поля

- `ordered_quantity` — заказано
- `shipped_quantity` — отгружено
- `reserved_quantity` — зарезервировано (DB field, обновляется сервисом)
- `remaining_quantity` — осталось = max(0, ordered - shipped)
- `available_to_ship` — можно отгрузить = max(0, remaining - reserved_over_shipped)
- `remaining_to_reserve` — можно ещё зарезервировать = max(0, ordered - reserved)

### Снятие резерва

- Явное: `POST /api/orders/{id}/release-reserve/`
- Автоматическое: `POST /api/orders/{id}/cancel/` снимает все активные резервы

### При отгрузке

При создании продажи (Sale) привязанной к OrderLine — рекомендуется пометить соответствующий резерв как `fulfilled`. Это не делается автоматически в текущей версии (продажи могут создаваться без резерва), но есть функция `fulfill_reservation()`.

---

## 5. ЛОГИКА ПРАЙСОВ

### Создание прайса

```
POST /api/price-lists/
{
  "name": "Основной 2026",
  "is_active": true,
  "valid_from": "2026-01-01",
  "product_prices": [
    {"profile": 3, "price": 1500, "unit": "piece"},
    {"product": "Профиль 60мм", "price": 1200, "unit": "meter"}
  ]
}
```

### Индивидуальная цена клиента

```
POST /api/client-prices/
{
  "client": 7,
  "profile": 3,
  "price": 1350,
  "unit": "piece",
  "valid_from": "2026-04-01"
}
```

### Получение рекомендованной цены

```
GET /api/price-lists/suggest-price/?client_id=7&profile_id=3
→ {"price": "1350.00", "source": "client_price", "source_id": 12, ...}

GET /api/price-lists/suggest-price/?profile_id=3
→ {"price": "1500.00", "source": "price_list", "source_id": 5, ...}
```

### Ручная цена

Если suggest-price вернул `null` или пользователь хочет переопределить — просто указать цену вручную в документе. Backend не запрещает ручной ввод.

---

## 6. ЛОГИКА КРЕДИТНОГО ЛИМИТА

### Настройка

Через `PATCH /api/clients/{id}/`:
```json
{"credit_limit": 500000, "credit_limit_mode": "hard"}
```

### Проверка

- `GET /api/sales/{id}/credit-check/` — проверить лимит по конкретной продаже
- `GET /api/client-financial-summary/?client_id=` — полная сводка
- `GET /api/clients/{id}/history/` — включает `credit_is_over_limit`, `credit_warning`

### Что считается долгом

- Выручка всех не-черновик, не-отменённых продаж
- Минус чистые поступления (оплаты + предоплаты - возвраты денег)

### Режимы

| Режим | Поведение |
|-------|-----------|
| `soft` | Предупреждение в ответе API, но блокировки нет |
| `hard` | `blocked: true` в ответе; фронт **ОБЯЗАН** заблокировать отгрузку |

> Backend не блокирует создание Sale при hard-режиме автоматически — проверка явная и намеренная, чтобы дать возможность override авторизованным ролям. Фронт или service layer должен проверить `blocked: true` перед созданием отгрузки.

---

## 7. ЛОГИКА ПРИБЫЛИ

### Фиксация себестоимости

- `Sale.cost` = сумма `cost_per_piece * sold_pieces` по партии на момент продажи
- `Sale.profit` = `revenue - cost`
- `SaleLine.cost` = `cost_per_piece * quantity` из `WarehouseBatch`
- `SaleLine.profit` = `line_total - cost` — **фиксируется в момент создания строки**

Значения не пересчитываются задним числом при изменении себестоимости склада.

### Прибыль по продаже брака

- `Sale.is_defect_sale = True`
- Отдельный агрегат в аналитике
- Не смешивается с обычной прибылью

### Влияние возвратов на прибыль

Возврат создаёт `ReturnLine`. Финансовый возврат — отдельный `Payment(payment_type=refund)`.
Аналитика считает возвраты денег как вычет из чистых поступлений. Выручка продаж не корректируется — история сохраняется.

---

## 8. ЛОГИКА ПЕРЕДЕЛКИ

### Статусный цикл

```
pending → in_progress → completed
pending → canceled
in_progress → canceled
```

### Новые endpoints

- `POST /api/rework-requests/{id}/start/` — перевести в `in_progress`
- `POST /api/rework-requests/{id}/complete/` — завершить с указанием:
  - `result_warehouse_batch_id` (обязательно)
  - `output_quantity_kg` (фактический выход)
  - `loss_kg` (опционально, вычисляется автоматически)
- `POST /api/rework-requests/{id}/cancel/` — отменить

### Расчёт потерь при завершении

```python
loss_kg = max(0, quantity_kg - output_quantity_kg)
conversion_rate = output_quantity_kg / quantity_kg
```

### Связи переделки

```
DefectRecord → ReworkRequest → result_warehouse_batch (WarehouseBatch)
                             → return_doc (Return)
                             → original_sale (Sale)
```

---

## 9. АНАЛИТИКА — НОВЫЕ ENDPOINTS

| Endpoint | Описание |
|----------|----------|
| `GET /api/analytics/defect-analytics/` | Брак: потери, статусы, источники, продажи брака |
| `GET /api/analytics/rework-analytics/` | Переделка: вход, выход, потери по статусам |
| `GET /api/analytics/client-profitability/` | Прибыль по клиентам за период |
| `GET /api/analytics/receivables/` | Дебиторка и авансы по всем активным клиентам |

### Существующие endpoints (расширены)

| Endpoint | Что добавлено |
|----------|---------------|
| `GET /api/analytics/profit-details/` | Уже был, строит по Sale.profit |
| `GET /api/analytics/summary/` | Без изменений |

---

## 10. ТРАССИРОВКА ПАРТИЙ

### `GET /api/warehouse/batches/{id}/trace/`

Полная цепочка:
```
WarehouseBatch
  ├── source_batch (ProductionBatch)
  │     └── otk_checks
  ├── sale_lines (SaleLine → Sale → Client)
  ├── return_lines (ReturnLine → Return)
  ├── defect_records (DefectRecord)
  ├── rework_requests (ReworkRequest → result_warehouse_batch)
  └── active_reservations (OrderReservation → OrderLine → Order)
```

---

## 11. НОВЫЕ API ENDPOINTS

### Резервы

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `POST` | `/api/orders/{id}/reserve/` | Зарезервировать товар под строку |
| `POST` | `/api/orders/{id}/release-reserve/` | Снять резерв |
| `GET` | `/api/orders/{id}/reservations/` | Список резервов по заявке |
| `PATCH` | `/api/orders/{id}/cancel/` | Отменить заявку + снять резервы |
| `GET` | `/api/order-reservations/` | Все резервы (только чтение) |

### Прайсы

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `GET/POST/PATCH/DELETE` | `/api/price-lists/` | CRUD прайс-листов |
| `GET` | `/api/price-lists/suggest-price/` | Рекомендованная цена |
| `GET/POST/PATCH/DELETE` | `/api/client-prices/` | CRUD индивидуальных цен |

### Финансы клиента

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `GET` | `/api/clients/{id}/history/` | Расширенная история (добавлен кредит, прибыль) |
| `GET` | `/api/client-financial-summary/?client_id=` | Финансовая сводка |
| `GET` | `/api/sales/{id}/credit-check/` | Проверка лимита по продаже |

### Переделка

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `POST` | `/api/rework-requests/{id}/start/` | Начать переделку |
| `POST` | `/api/rework-requests/{id}/complete/` | Завершить с результатом |
| `POST` | `/api/rework-requests/{id}/cancel/` | Отменить |

### Трассировка

| Метод | Endpoint | Описание |
|-------|----------|----------|
| `GET` | `/api/warehouse/batches/{id}/trace/` | Полная цепочка партии |

---

## 12. АВТОМАТИЧЕСКИЕ БИЗНЕС-ПОЛЯ ДЛЯ ФРОНТА

### OrderLine (сериализатор)

| Поле | Источник | Описание |
|------|----------|----------|
| `ordered_quantity` | DB | Заказано |
| `shipped_quantity` | DB | Отгружено |
| `reserved_quantity` | DB | Зарезервировано |
| `remaining_quantity` | property | Осталось отгрузить |
| `available_to_ship` | property | Можно отгрузить прямо сейчас |
| `remaining_to_reserve` | property | Можно ещё зарезервировать |
| `line_total` | property | Сумма = ordered × price |

### SaleLine (сериализатор)

| Поле | Источник | Описание |
|------|----------|----------|
| `line_total` | DB | Выручка строки |
| `cost` | DB | Себестоимость строки |
| `profit` | DB | Прибыль строки |

### Order (сериализатор)

| Поле | Источник |
|------|----------|
| `total_amount` | sum(line_total) |
| `shipped_amount` | sum(shipped × price) |
| `remaining_amount` | sum(remaining × price) |
| `paid_amount` | sum(payments) |
| `has_company_debt_by_goods` | есть ли неотгруженные строки |

### ReworkRequest (сериализатор)

| Поле | Источник |
|------|----------|
| `rework_loss_kg` | вычислено из input - output |
| `recovered_output` | output_quantity_kg |

### Client history (`/api/clients/{id}/history/`)

Добавлены поля:
- `total_revenue` — выручка
- `total_profit` — прибыль по клиенту
- `defect_revenue` — выручка от брака отдельно
- `total_paid_gross` / `total_refunded` — детализация оплат
- `credit_limit` / `credit_available` / `credit_is_over_limit` / `credit_warning`
- `overdue_orders_count` — заявки с товарным долгом старше 30 дней

---

## 13. АУДИТ И СОБЫТИЯ

Механизм `UserActivity` / `AuditOutbox` сохранён без изменений.

Новые события логируются через существующий `ActivityLoggingMixin`:
- Создание/изменение `PriceList`, `ClientPrice` — через `PriceListViewSet`, `ClientPriceViewSet`
- Резервы — текущий аудит склада через `schedule_entity_audit` для WarehouseBatch

> Для детального аудита резервов (`OrderReservation`) рекомендуется добавить `schedule_entity_audit` в `reservations.py` в следующей итерации.

---

## 14. EDGE CASES — ЗАКРЫТЫЕ

| Сценарий | Защита |
|----------|--------|
| Двойное резервирование одной партии | `select_for_update` + проверка суммы активных резервов |
| Резерв больше остатка | Проверка `quantity ≤ available_on_batch` |
| Резерв больше остатка по строке заявки | Проверка `quantity ≤ remaining_to_reserve` |
| Резерв брака под клиентскую заявку | Запрет для `quality=defect` |
| Отмена заявки с активными резервами | Авто-снятие через `release_all_for_order` |
| Возврат больше отгруженного | `validate_return_quantity` + проверка в `ReturnLineSerializer` |
| Продажа брака из неправильного статуса | `validate_defect_sell` через state machine |
| Завершение переделки без результирующей партии | Валидация `result_warehouse_batch_id` |
| Завершение переделки из неправильного статуса | `validate_rework_complete` |
| Незаконный переход статуса | Централизованная state machine для Order/Sale/DefectRecord/ReworkRequest |

---

## 15. СТАРЫЕ ОГРАНИЧЕНИЯ — СОХРАНЕНЫ

- FIFO на сырьё (`MaterialStockDeduction`) — не затронуто
- `ProductionBatch → OTK → WarehouseBatch` — не затронуто
- Обратная совместимость старых продаж (`Sale` без `SaleLine`) — сохранена (накладная умеет рендерить оба формата)
- `WarehouseBatch.status` (`available/reserved/shipped`) — сохранён, теперь `reserved` используется при полном резерве партии через старый `/warehouse/batches/reserve/`, новый механизм `OrderReservation` работает поверх него
- Существующие API без изменений — все старые endpoints продолжают работать

---

## 16. ЧТО ВАЖНО ЗНАТЬ ФРОНТЕНДУ

1. **Цена в документе** всегда фиксируется вручную или через `suggest-price`. Backend не подставляет цену автоматически при создании Sale.

2. **Кредитный лимит `hard`**: если `credit_check` вернул `blocked: true`, фронт обязан показать блокировку. Backend не блокирует сохранение Sale автоматически — это сознательное решение для override.

3. **Резерв**: `OrderLine.available_to_ship` — это поле показывает, сколько можно отгрузить из зарезервированного + свободного. `reserved_quantity` показывает только зарезервированное.

4. **Переделка**: при завершении передавать `output_quantity_kg` — backend автоматически посчитает `loss_kg` и `conversion_rate`.

5. **Прибыль строки** (`SaleLine.profit`) фиксируется на момент создания и не пересчитывается. При изменении себестоимости склада старые продажи не меняются.

6. **Дебиторка**: `GET /api/analytics/receivables/` считает долг по всем клиентам в реальном времени. Для одного клиента — `GET /api/client-financial-summary/?client_id=`.

7. **Трассировка**: `GET /api/warehouse/batches/{id}/trace/` даёт полную историю партии от производства до конечного покупателя.

---

## 17. РИСКИ И ОГРАНИЧЕНИЯ

| Риск | Статус |
|------|--------|
| `OrderReservation` не создаётся автоматически при создании Sale | Намеренно — резерв и продажа независимы |
| `Sale` без резерва не блокируется | Система разрешает продажу без предварительного резерва |
| `credit_limit_mode=hard` не блокирует автоматически | Намеренно — override через роль менеджера |
| Прибыль `SaleLine.profit` может быть 0 для legacy-записей | Поле добавлено с default=0 |
| `OrderLine.reserved_quantity` обновляется сервисом, не триггером | При прямом SQL-изменении не синхронизируется |
