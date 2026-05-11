# Backend API Contract — Sales Final

## 1) Sale structure

`POST /api/sales/`, `GET /api/sales/`, `GET /api/sales/{id}/`

Основные поля (frontend):
- `client` (required)
- `order` (optional, alias к `linked_order`)
- `unit_type` (`pieces` | `packages`) — режим продажи для фронта (alias к `sale_mode`)
- `sale_lines` (required, минимум 1):
  - `warehouse_batch`
  - `quantity`
  - `unit_price`
- `payment_type` (`full` | `partial` | `debt`)
- `payment_method` (`cash` | `card` | `transfer`)
- `paid_amount` (для `full/partial`)

Поля ответа:
- `total_amount` (read-only, = `revenue`)
- `paid_amount` (вход на create; в ответе — фактическая оплата по продаже)
- `debt_amount` (read-only)
- `payment_status` (read-only)
- `unit_type` (`pieces` | `packages`)

## 2) Payment logic (inside sale)

Встроенная логика при `POST /api/sales/`:
- `full`:
  - `paid_amount` обязателен
  - `paid_amount == total_amount`
  - `debt_amount = 0`
- `partial`:
  - `paid_amount` обязателен, `0 < paid_amount <= total_amount`
  - `debt_amount = total_amount - paid_amount`
- `debt`:
  - `paid_amount = 0`
  - `debt_amount = total_amount`

## 3) Debt calculation

- `total_amount = revenue` продажи (read-only).
- `paid_amount`/`debt_amount` считаются на backend.

## 4) Warehouse write-off

При создании продажи backend сразу:
- проверяет доступный остаток партии;
- списывает `WarehouseBatch`;
- сохраняет снимок мутации для отката.

Ограничение:
- нельзя продать больше доступного остатка.

## 5) Link with order

Если передан `order` (`linked_order`):
- обновляются отгруженные количества по строкам заявки;
- если все строки полностью отгружены — заявка переводится в `closed`.

## 6) Client profile endpoint

`GET /api/clients/{id}/profile/`

Ответ:
- `client` (карточка клиента)
- `total_debt`
- `purchases` (sales list)
- `orders`
- `returns`

## 7) Validations

- нельзя создать продажу без `client`
- нельзя создать продажу без `sale_lines` (минимум 1 строка)
- нельзя продать больше, чем есть на складе
- нельзя оплатить больше, чем `total_amount`
- для `payment_type=full`:
  - `paid_amount` обязателен
  - `paid_amount` должен быть равен `total_amount`

## 8) Read-only fields

- `total_amount`
- `payment_status`
- `paid_amount` (в ответе read-only; на create принимается во входе)
- `debt_amount`
- `revenue`, `cost`, `profit`
- `warehouse_stock_applied`

## 9) JSON examples

### Create sale (full payment)

```json
{
  "client": 1,
  "order": 12,
  "unit_type": "pieces",
  "sale_lines": [
    {
      "warehouse_batch": 34,
      "quantity": "20",
      "unit_price": "15000",
      "product": "Profile A"
    }
  ],
  "payment_type": "full",
  "payment_method": "card",
  "paid_amount": "300000"
}
```

### Create sale (partial payment)

```json
{
  "client": 1,
  "unit_type": "pieces",
  "sale_lines": [
    {
      "warehouse_batch": 34,
      "quantity": "10",
      "unit_price": "10000",
      "product": "Profile B"
    }
  ],
  "payment_type": "partial",
  "payment_method": "cash",
  "paid_amount": "40000"
}
```

### Response fragment

```json
{
  "id": 101,
  "client": 1,
  "order": 12,
  "unit_type": "pieces",
  "total_amount": "300000",
  "paid_amount": "300000",
  "debt_amount": "0",
  "payment_status": "paid"
}
```
