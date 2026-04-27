# Backend API Contract — Sales UI Final

## 1) Endpoints

- `GET /api/sales/select-sources/?client={id?}&order={id?}`
- `POST /api/sales/preview/`
- `POST /api/sales/`
- `GET /api/sales/`
- `GET /api/sales/{id}/`
- `GET /api/clients/{id}/profile/`

---

## 2) Что показывает frontend / что скрывает

Показывать пользователю:
- `display`, `order_display`, `warehouse_batch_display`
- `client_name`, `profile_name`, `status_label`
- суммы/долг/оплату

Скрывать в UI (но использовать в payload):
- `id`, `warehouse_batch`, `order`, `client`

---

## 3) Отображение заявки (для селекта)

`available_orders[]`:
- `id`
- `display` / `order_display`
- `order_number`
- `client_name`
- `profile_name`
- `quantity`
- `length`
- `total_meters`
- `request_status`
- `status_label`

Пример `display`:
- `ORD-2026-0005 — 60 мм белый — 20 шт × 5 м — готово`

---

## 4) Отображение товара склада (для селекта)

`available_warehouse_batches[]`:
- `id`
- `display` / `warehouse_batch_display`
- `profile_name`
- `length_per_piece`
- `available_pieces`
- `available_packages`
- `total_meters`
- `quality`
- `status`
- `unit_labels` (`pieces`, `packages`, `meters`)

Пример `display`:
- `60 мм белый — 5 м — остаток: 25 шт / 5 уп.`

---

## 5) Продажа: request поля

`POST /api/sales/preview/` и `POST /api/sales/`:
- `client` (required)
- `order` (optional)
- `unit_type` (`pieces` | `packages`)
- `sale_lines` (required, min 1):
  - `warehouse_batch`
  - `quantity`
  - `unit_price`
  - `product` (optional)
- `payment_type` (`full` | `partial` | `debt`)
- `payment_method` (`cash` | `card` | `transfer`)
- `paid_amount`

Ограничения:
- нельзя без `client`
- нельзя без `sale_lines`
- нельзя продать больше остатка
- нельзя `paid_amount > total_amount`

---

## 6) Preview продажи (без списания)

`POST /api/sales/preview/` ничего не списывает и не создаёт.

Возвращает:
- `total_amount`
- `paid_amount`
- `debt_amount`
- `payment_status`
- `payment_type_label`
- `payment_method_label`
- `payment_status_label`
- `summary`
- `unit_type`
- `normalized_lines`
- ошибки валидации (если есть)

---

## 7) Создание продажи (сразу списывает)

`POST /api/sales/`:
- списывает склад сразу;
- фиксирует продажу;
- фиксирует оплату/долг;
- связывает с заявкой (если `order` передан);
- при полной отгрузке заявки закрывает её.

---

## 8) Логика оплаты

- `full`:
  - `paid_amount = total_amount`
  - `debt_amount = 0`
  - `paid_amount` пользователю не редактируется
- `partial`:
  - пользователь вводит `paid_amount`
  - `debt_amount = total_amount - paid_amount`
- `debt`:
  - `paid_amount = 0`
  - `debt_amount = total_amount`
  - показывать: `Весь заказ в долг`

Лейблы для UI:
- `payment_type_label`
- `payment_method_label`
- `payment_status_label`

---

## 9) Карточка клиента

`GET /api/clients/{id}/profile/`:

- `client`:
  - `id`, `name`, `phone`, `phone_extra`, `status`, `comment`
- `summary`:
  - `total_sales_amount`
  - `total_paid_amount`
  - `total_debt`
  - `total_orders`
  - `total_returns`
- `orders` (с display полями)
- `sales` / `purchases` (история покупок)
- `returns`
- `debts` (только продажи с `debt_amount > 0`)

---

## 10) Read-only поля

- `total_amount`
- `payment_status`
- `debt_amount`
- `warehouse_stock_applied`
- вычисляемые display/label поля

---

## 11) JSON examples

### A. select-sources response fragment

```json
{
  "available_orders": [
    {
      "id": 5,
      "display": "ORD-2026-0005 — 60 мм белый — 20 шт × 5 м — готово",
      "order_number": "ORD-2026-0005",
      "client_name": "ТОО Almaz",
      "profile_name": "60 мм белый",
      "quantity": 20,
      "length": "5",
      "total_meters": "100",
      "request_status": "ready",
      "status_label": "Готово"
    }
  ],
  "available_warehouse_batches": [
    {
      "id": 34,
      "display": "60 мм белый — 5 м — остаток: 25 шт / 5 уп.",
      "profile_name": "60 мм белый",
      "length_per_piece": "5",
      "available_pieces": "25",
      "available_packages": "5",
      "total_meters": "125",
      "quality": "good",
      "status": "available",
      "unit_labels": {
        "pieces": "шт",
        "packages": "уп",
        "meters": "м"
      }
    }
  ]
}
```

### B. preview request/response

```json
{
  "client": 1,
  "order": 5,
  "unit_type": "pieces",
  "sale_lines": [
    { "warehouse_batch": 34, "quantity": "10", "unit_price": "15000" }
  ],
  "payment_type": "partial",
  "payment_method": "cash",
  "paid_amount": "50000"
}
```

```json
{
  "total_amount": "150000",
  "paid_amount": "50000",
  "debt_amount": "100000",
  "payment_status": "partial",
  "payment_type_label": "Частичная оплата",
  "payment_method_label": "Наличные",
  "payment_status_label": "Частично оплачено",
  "summary": "Итого 150000; оплачено 50000; долг 100000"
}
```

### C. create sale request

```json
{
  "client": 1,
  "order": 5,
  "unit_type": "pieces",
  "sale_lines": [
    { "warehouse_batch": 34, "quantity": "10", "unit_price": "15000" }
  ],
  "payment_type": "full",
  "payment_method": "card",
  "paid_amount": "150000"
}
```
