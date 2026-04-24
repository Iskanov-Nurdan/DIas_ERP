# BACKEND BUSINESS LOGIC — ФИНАЛЬНОЕ ЗАКРЫТИЕ

Дата: 24 апреля 2026  
Версия: 2.0 (финал)  
Предыдущая версия: `docs/BACKEND_BUSINESS_LOGIC_UPGRADE_FINAL.md`

---

## Что было закрыто

Данный документ описывает **финальное ужесточение** бизнес-логики backend-а ERP.  
Все изменения выполнены в рамках принципа: **фронтенд не может обойти критичную бизнес-логику**.

---

## 1. Кредитный лимит (Hard Credit Limit)

### Что изменилось

**До:** `hard`-режим лишь возвращал предупреждение в ответе `/credit-check/`. Фронт мог проигнорировать.  
**После:** Backend сам блокирует создание Sale и смену статуса при нарушении hard-лимита.

### Где блокируется

| Точка | Действие |
|---|---|
| `POST /api/sales/` | При создании Sale, после расчёта `revenue` |
| `PATCH /api/sales/{id}/status/` | При переводе в `shipped` / `closed` |

### Механизм

В `apps/sales/credit_check.py` добавлены:

- `enforce_credit_limit(client, additional_amount, user, force_override)` — поднимает `CreditLimitBlocked` если режим `hard` и лимит превышен.
- `CreditLimitBlocked` — кастомный Exception, конвертируется в HTTP 422 с кодом `credit_limit`.
- `can_override_credit_limit(user)` — возвращает `True` если у пользователя есть `access_key = 'credit_limit_override'` или `is_staff`.

### Override

Для обхода hard-блокировки (только авторизованным пользователям):

```
POST /api/sales/
{
  "client": 5,
  "force_credit_override": true,
  ...
}
```

```
PATCH /api/sales/{id}/status/
{
  "status": "shipped",
  "force_credit_override": true
}
```

`force_credit_override` работает **только если** пользователь имеет право `credit_limit_override`. Без этого права флаг игнорируется и блокировка применяется.

### Учёт финансов

`compute_client_debt()` учитывает:
- Выручку по всем не-черновиковым, не-отменённым Sale
- Входящие оплаты (`prepayment`, `payment`, `surcharge`)
- Возврат денег (`refund`) — уменьшает чистые поступления

---

## 2. Автоматическое исполнение резерва при продаже

### Что изменилось

**До:** `fulfill_reservation()` существовала, но **не вызывалась автоматически** при создании Sale.  
**После:** При создании Sale с `linked_order` + `warehouse_batch`, резервы исполняются автоматически.

### Функция `auto_fulfill_for_sale()`

Файл: `apps/sales/reservations.py`

- Ищет активные `OrderReservation` по заказу и партии
- Исполняет FIFO (по дате создания резерва)
- Поддерживает **частичное исполнение**: если продажа меньше резерва — резерв уменьшается, остаток остаётся активным
- Обновляет `OrderLine.shipped_quantity` при каждом исполнении
- Устанавливает `OrderReservation.sale_line = sale` для прямой трассировки
- Записывает событие в аудит

### `restore_reservations_for_sale()`

При отмене/удалении Sale:
- Возвращает `OrderReservation.status` из `fulfilled` → `active`
- Откатывает `OrderLine.shipped_quantity`
- Очищает `sale_line` на резерве
- Записывает событие в аудит

### Частичное исполнение

```
Резерв: 100 шт
Продажа: 60 шт

→ fulfilled_quantity = 60
→ quantity (остаток резерва) = 40
→ status остаётся ACTIVE
→ OrderLine.shipped_quantity += 60
```

---

## 3. Политика "Продажа без резерва"

### Что изменилось

**До:** Продажа без резерва разрешалась всегда — явной политики не было.  
**После:** В backend-е существует явная настройка.

### Настройка

`config/settings.py`:
```python
SALE_REQUIRES_RESERVATION = False  # по умолчанию — допускается
```

Установите `True` в окружении production если хотите запретить продажи без резерва.

### Логика

Если `SALE_REQUIRES_RESERVATION = True`:
- При создании Sale с `linked_order` и `warehouse_batch`
- Backend проверяет наличие хотя бы одного **активного** `OrderReservation` по паре `(order, warehouse_batch)`
- Если нет — возвращает HTTP 400:
  ```json
  { "linked_order": "Продажа без активного резерва запрещена политикой системы. Сначала создайте резерв через /api/orders/{id}/reserve/." }
  ```

---

## 4. Полный аудит резервов

### Что изменилось

**До:** Операции с резервами не логировались.  
**После:** Каждое событие пишется в `UserActivity` через `schedule_entity_audit`.

### Логируемые события

| Операция | action | Описание |
|---|---|---|
| Создание резерва | `create` | Партия, строка заявки, количество |
| Снятие резерва | `update` | Изменение статуса на `released` |
| Частичное исполнение | `update` | Исполненное количество, остаток |
| Полное исполнение | `update` | Статус `fulfilled`, ссылка на SaleLine |
| Восстановление при отмене Sale | `update` | Статус обратно в `active`, откат qty |
| Авто-снятие при отмене Order | `update` | `release_all_for_order()` |

### Поля в payload

```json
{
  "order_line_id": 42,
  "warehouse_batch_id": 17,
  "quantity": "100.0000",
  "fulfilled_quantity": "60.0000",
  "sale_line_id": 88
}
```

---

## 5. Backend как источник истины по остаткам

### WarehouseBatch — новые поля в API

Эндпоинт: `GET /api/warehouse/batches/`

| Поле | Описание |
|---|---|
| `quantity` | Физический остаток (что фактически есть на складе) |
| `reserved_quantity` | Сумма активных резервов по этой партии |
| `available_quantity` | `quantity − reserved_quantity` (свободно для продажи) |

Фронтенд **не должен** вычислять `available_quantity` самостоятельно.

### OrderLine — поля источника истины

| Поле | Тип | Откуда |
|---|---|---|
| `ordered_quantity` | DB | Из заявки |
| `reserved_quantity` | DB | Обновляется сервисом резервирования |
| `shipped_quantity` | DB | Обновляется при исполнении резерва |
| `available_to_ship` | property | `ordered_quantity − shipped_quantity` |
| `remaining_to_reserve` | property | `ordered_quantity − reserved_quantity − shipped_quantity` |

---

## 6. Расширенный State Machine

### OrderViewSet.set_status()

Теперь использует `validate_order_transition()` из `state_machine.py` (единый источник).

Добавлена `validate_order_close(order)`:
- Блокирует закрытие если есть **активные резервы** по строкам заявки
- Блокирует закрытие если хотя бы одна строка не полностью отгружена (`shipped_quantity < ordered_quantity`)

### SaleViewSet.set_status() — НОВЫЙ ENDPOINT

`PATCH /api/sales/{id}/status/`

Body:
```json
{ "status": "shipped", "force_credit_override": false }
```

Проверки при переводе в `shipped` / `closed`:
1. `validate_sale_transition()` — допустимость перехода
2. `validate_sale_ship()` — партия доступна, остаток покрывает количество
3. `enforce_credit_limit()` — hard-лимит (если клиент указан)

### validate_rework_complete()

Переделку нельзя завершить из статуса, отличного от `in_progress`.

### validate_defect_sell()

Брак продаётся только из статусов `on_stock` или `reworked`.

---

## 7. Модель OrderReservation — новые поля

Файл: `apps/sales/models.py`  
Миграция: `0015_orderreservation_sale_line_fulfilled.py`

| Поле | Тип | Описание |
|---|---|---|
| `fulfilled_quantity` | DecimalField | Сколько из резерва уже исполнено |
| `sale_line` | FK → SaleLine (nullable) | Какая строка продажи исполнила резерв |

### Сериализатор OrderReservationSerializer

Добавлены поля: `fulfilled_quantity`, `sale_line`.  
Поля `read_only`: `fulfilled_quantity`, `sale_line`, `status`.

---

## 8. Trace endpoint — полная коммерческая цепочка

`GET /api/warehouse/batches/{id}/trace/`

Теперь возвращает **все резервы** (не только активные):

```json
{
  "reservations": [
    {
      "reservation_id": 1,
      "status": "fulfilled",
      "order_line_id": 42,
      "order_number": "ZAY-2026-001",
      "quantity": "40.0000",
      "fulfilled_quantity": "60.0000",
      "sale_line_id": 88,
      "sale_order_number": "ORD-2026-012",
      "created_at": "2026-04-10T10:00:00+05:00"
    }
  ],
  "active_reservations": [ ... ]  // backward-compat alias
}
```

Полная цепочка трассировки:  
`WarehouseBatch → reservations[].order_line_id → reservations[].sale_line_id → SaleLine → ReturnLine → DefectRecord → ReworkRequest`

---

## 9. Изменённые / новые endpoints

| Метод | URL | Изменение |
|---|---|---|
| `POST` | `/api/sales/` | Hard credit limit блокировка; auto-fulfill резерва |
| `PATCH` | `/api/orders/{id}/status/` | Использует state_machine; validate_order_close() |
| `PATCH` | `/api/sales/{id}/status/` | **НОВЫЙ** — state machine + credit check |
| `GET` | `/api/warehouse/batches/` | +`reserved_quantity`, +`available_quantity` |
| `GET` | `/api/warehouse/batches/{id}/trace/` | Все резервы + sale_line ссылка |

---

## 10. Устранённые риски

| Риск | Статус |
|---|---|
| Фронт создаёт продажу игнорируя hard-лимит | ✅ Закрыт на backend |
| Резерв остаётся активным после продажи | ✅ Auto-fulfill |
| shipped_quantity не обновляется при продаже | ✅ Обновляется в auto_fulfill |
| Резерв не восстанавливается при отмене Sale | ✅ restore_reservations_for_sale() |
| Продажа без резерва — серая зона | ✅ Явная политика SALE_REQUIRES_RESERVATION |
| Закрытие Order с открытыми резервами | ✅ validate_order_close() |
| Смена статуса Sale в обход проверок | ✅ Централизованный set_status() |
| Трассировка не показывает fulfilled резервы | ✅ Исправлено |
| available_quantity вычислялся на фронте | ✅ Backend поле |

---

## 11. Что фронтенд ОБЯЗАН учитывать

1. При получении HTTP **422** с `code: "credit_limit"` — показывать ошибку превышения лимита; предлагать контакт с менеджером, у которого есть `credit_limit_override`.
2. Для получения актуального остатка — использовать `available_quantity` из `/api/warehouse/batches/`, **не вычислять самостоятельно**.
3. Смену статуса Sale делать через `PATCH /api/sales/{id}/status/`, а не через прямой PATCH на объект.
4. При отмене Order — статус автоматически снимает резервы; фронт не должен делать это вручную.
5. При создании Sale с `linked_order` — резервы исполняются автоматически; не нужно вызывать `fulfill_reservation` отдельно.

---

## 12. Что не изменилось (защита стабильности)

- FIFO-логика складских операций — **не тронута**
- Производственный контур (`ProductionBatch`, `OTKCheck`) — **не тронут**
- Существующие API `/api/orders/`, `/api/sales/`, `/api/warehouse/batches/` — **обратно совместимы**
- Аналитические эндпоинты — **не тронуты**
- Механизм расчёта прайса (`suggest_price`) — **не тронут**
