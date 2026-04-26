# Backend API Contract Review — Оплаты (после фиксов)

## 1. Общая информация
- Вкладка “Оплаты” работает через `PaymentViewSet` и `PaymentSerializer`.
- Основные модели: `Payment`, `Client`, `Order`, `Sale`, `Return`, `ReturnLine`, `SaleLine`.
- Permissions: `IsAdminOrHasAccess`, `required_access_key='payments'`.
- Filters: `PaymentFilter` (`client_id`, `payment_type`, `payment_method`, `date_from`, `date_to`, `linked_order`, `linked_sale`).
- Финансовые метрики: `apps/sales/payment_status.py` (`sale_payment_metrics`, `order_payment_metrics`).

## 2. Endpoints вкладки “Оплаты”

### GET `/api/payments/`
- Назначение: список оплат (пагинация, фильтры).
- Query: `client_id`, `payment_type`, `payment_method`, `date_from`, `date_to`, `linked_order`, `linked_sale`.
- Response: элементы `PaymentSerializer`.

### POST `/api/payments/`
- Назначение: создание оплаты.
- Строгие правила create:
  - `client` обязателен, `client != null`, клиент должен быть `is_active=True`.
  - `amount` обязателен и строго `> 0`.
  - `payment_type` обязателен (`prepayment/payment/surcharge/refund`).
  - `payment_method` обязателен (`cash/transfer/card/other`).
  - `status` во входящем payload запрещен.
  - статус новой записи всегда `active`.
- Связи и типы:
  - `prepayment`: обязателен `linked_order`; заявка не `canceled/closed`.
  - `payment` и `surcharge`: обязателен `linked_sale` или `linked_order`; нельзя на `canceled` sale/order.
  - `refund`: обязателен `linked_return` или `manual_refund_reason`.
    - при `linked_return`: `return.status == completed`, клиент должен совпадать, сумма не больше доступного лимита возврата.
    - при отсутствии `linked_return`: `manual_refund_reason` обязателен и не пустой.

### GET `/api/payments/{id}/`
- Назначение: карточка оплаты.
- Response: `PaymentSerializer`.

### PATCH/PUT `/api/payments/{id}/`
- Назначение: ограниченное редактирование.
- Можно менять только: `date`, `payment_method`, `comment`, `manual_refund_reason`.
- Frozen поля: `amount`, `client`, `linked_sale`, `linked_order`, `linked_return`, `payment_type`, `status`.
- `status` через обычный update запрещен (только `/cancel/`).
- Если запись уже `canceled`, редактирование полностью запрещено.

### DELETE `/api/payments/{id}/`
- Физическое удаление отключено.
- HTTP 405, code `DELETE_DISABLED`.
- Frontend должен использовать `/cancel/` вместо delete.

### POST/PATCH `/api/payments/{id}/cancel/`
- `active -> canceled`.
- Повторно отменять нельзя: `PAYMENT_ALREADY_CANCELED` (HTTP 422).
- Возвращает `PaymentSerializer`.

### GET `/api/payments/summary/?client_id=...`
- `client_id` обязателен (`MISSING_CLIENT`).
- Если клиент не найден: `NOT_FOUND`.
- Учитываются только `Payment.status=active`.
- Для `total_revenue` учитываются только реальные продажи (исключены `sale_status=draft/canceled`).

### GET `/api/payments/select-sources/?client_id=&sale_id=&order_id=&return_id=`
- Новый endpoint для форм оплат.
- `clients`: только активные.
- `orders`: фильтрация по `client_id`/`order_id`, с полями `debt_amount`, `payment_status`, `status`.
- `sales`: фильтрация по `client_id`/`sale_id`, с полями `debt_amount`, `payment_status`, `sale_status`.
- `returns`: только `completed`, фильтрация по `client_id`/`sale_id`/`return_id`.

## 3. Поля Payment (API)
- `id`: read-only.
- `payment_number`: read-only, генерируется backend (`PAY-YYYY-XXXX`).
- `date`: writable.
- `client`: writable, обязателен при create.
- `client_name`: read-only.
- `linked_order`: writable, условно обязателен по `payment_type`.
- `linked_sale`: writable, условно обязателен по `payment_type`.
- `linked_return`: writable, условно обязателен для `refund`.
- `payment_type`: writable на create, frozen после create.
- `amount`: writable на create, frozen после create, `>0`.
- `payment_method`: writable.
- `status`: frozen, меняется только через `/cancel/`.
- `manual_refund_reason`: writable.
- `comment`: writable.
- `created_by`: выставляется backend в `perform_create`.
- `created_by_name`: read-only.
- `created_at`: read-only.

## 4. Payment Types
- `prepayment` / Предоплата:
  - обязателен `linked_order`;
  - запрет на `canceled/closed` order.
- `payment` / Оплата:
  - обязателен `linked_sale` или `linked_order`;
  - запрет на `canceled` sale/order.
- `surcharge` / Доплата:
  - обязателен `linked_sale` или `linked_order`;
  - запрет на `canceled` sale/order.
- `refund` / Возврат денег:
  - обязателен `linked_return` или непустой `manual_refund_reason`;
  - если `linked_return` задан: только `completed`, клиент должен совпадать, сумма ограничена доступным возвратом.

## 5. Payment Methods
- `cash` / Наличные
- `transfer` / Перевод
- `card` / Карта
- `other` / Другое

## 6. Стабильные error codes
- `MISSING_CLIENT`
- `INACTIVE_CLIENT`
- `INVALID_AMOUNT`
- `INVALID_PAYMENT_TYPE`
- `INVALID_PAYMENT_METHOD`
- `MISSING_LINKED_ENTITY`
- `CLIENT_MISMATCH`
- `REFUND_REASON_REQUIRED`
- `REFUND_RETURN_REQUIRED`
- `REFUND_RETURN_NOT_COMPLETED`
- `REFUND_AMOUNT_EXCEEDED`
- `PAYMENT_STATUS_UPDATE_FORBIDDEN`
- `PAYMENT_ALREADY_CANCELED`
- `DELETE_DISABLED`
- также используются `NOT_FOUND` (summary).

## 7. Метрики и расчеты
- `sale_payment_metrics` и `order_payment_metrics`:
  - `paid_amount = active incoming - active refund`;
  - `refund_amount = active refund`;
  - `debt_amount = max(total_due - net_paid, 0)` (с учетом ветки `refunded`);
  - `payment_status` считает только активные платежи.
- `summary`:
  - canceled payments не учитываются;
  - draft/canceled sales не учитываются в `total_revenue`.

## 8. Frontend contract (backend-канон)
- Не отправлять `status` в create/update.
- Не отправлять read-only поля (`id`, `payment_number`, `client_name`, `created_by_name`, `created_at`).
- Не показывать кнопку “Удалить” для оплат.
- Для отмены использовать только `/api/payments/{id}/cancel/`.
- Для формы создания брать источники из `/api/payments/select-sources/`.

## 9. Tests
- Добавлен полный suite: `apps/sales/tests/test_payments_api.py`.
- Покрыто:
  - strict create rules;
  - payment type rules;
  - client mismatch;
  - update/frozen/status guards;
  - cancel flow;
  - summary filters;
  - select-sources;
  - delete 405.

## 10. Problems
- Critical: не найдено.
- Medium: не найдено.
- Minor: не найдено.

## 11. Final Verdict
Оплаты backend contract:
- **OK**

Оплаты backend contract закрыт.
