# Backend API Contract Review — Возвраты (после фиксов)

## 1. Общая информация
- Модуль возвратов реализован в `ReturnViewSet` + `ReturnSerializer`/`ReturnLineSerializer`.
- Основные модели: `Return`, `ReturnLine`, `Sale`, `SaleLine`, `Order`, `Client`, `WarehouseBatch`, `DefectRecord`, `ReworkRequest`, `Payment`.
- Доступ: `IsAdminOrHasAccess`, `required_access_key='returns'`.
- Фильтры списка: `sale_id`, `client_id`, `date_from`, `date_to`.

## 2. Endpoints
- `GET /api/returns/`
- `POST /api/returns/`
- `GET /api/returns/{id}/`
- `PATCH /api/returns/{id}/`
- `PUT /api/returns/{id}/`
- `DELETE /api/returns/{id}/` -> `405 DELETE_DISABLED`
- `GET /api/returns/select-sources/`
- `GET /api/returns/select-sources/?sale_id=...`
- `POST/PATCH /api/returns/{id}/complete/`
- `POST/PATCH /api/returns/{id}/cancel/`
- `GET /api/returns/{id}/waybill/` (HTML)

## 3. Поля Return
- `id` (read-only)
- `return_number` (read-only, backend generation)
- `date` (writable)
- `sale` (writable, required)
- `sale_order_number` (read-only)
- `linked_order` (writable, optional)
- `status` (lifecycle-controlled)
- `return_reason` (writable)
- `invoice_number` (writable)
- `comment` (writable)
- `lines` (nested writable для draft)
- `client_name` (read-only)
- `created_by` (set by backend on create)
- `created_by_name` (read-only)
- `created_at` (read-only)
- `linked_entities` (read-only, retrieve only)
- `downstream_links` (read-only, retrieve only)
- `available_status_transitions` (read-only, retrieve only)
- `available_actions` (read-only, retrieve only)

Поля, которые frontend не отправляет: `id`, `return_number`, `sale_order_number`, `client_name`, `created_by_name`, `created_at`, `linked_entities`, `downstream_links`, `available_*`.

## 4. Поля ReturnLine
- `id` (read-only)
- `sale_line` (writable, required)
- `product` (read-only, вычисляется из `sale_line.product`)
- `quantity` (writable, required, `>0`)
- `return_target` (writable: `warehouse|defect|rework`)
- `condition_type` (writable: `good|damaged|defect`)
- `comment` (writable)
- `sale_line_label` (read-only)
- `sale_line_sale_id` (read-only)

## 5. Статусы Return
- `draft` (default): редактирование доступно, можно `complete`/`cancel`.
- `completed`: редактирование только `comment`, `return_reason`, `invoice_number`.
- `canceled`: редактирование запрещено.

Смена статуса:
- только через `complete`/`cancel`.
- через обычный `PATCH/PUT` запрещено (`RETURN_STATUS_UPDATE_FORBIDDEN`).

## 6. Create Return (`POST /api/returns/`)
Строгие правила:
- `sale` обязателен и не `null`.
- `sale` только в статусах `shipped|closed`.
- `lines` обязателен, минимум 1.
- `lines[].sale_line` обязателен.
- `sale_line` должен принадлежать `sale`.
- `quantity` обязателен, `>0`.
- `quantity <= returnable_quantity`.
- в лимите учитываются все возвраты, кроме `canceled`.
- `status` в payload запрещен (`RETURN_STATUS_CREATE_FORBIDDEN`).
- запись всегда создается как `draft`.

## 7. Update Return (`PATCH/PUT /api/returns/{id}/`)
- `status` через обычный update запрещен (`RETURN_STATUS_UPDATE_FORBIDDEN`).
- `draft`:
  - можно менять поля документа и `lines`.
  - для `lines` действуют все create-валидации (`sale_line` принадлежит sale, `quantity>0`, лимит).
- `completed`:
  - можно менять только `comment`, `return_reason`, `invoice_number`.
  - остальные поля -> `RETURN_UPDATE_FORBIDDEN`.
- `canceled`:
  - любое редактирование -> `RETURN_UPDATE_FORBIDDEN`.

## 8. Complete Return (`POST/PATCH /complete/`)
- Разрешено только `draft -> completed`.
- `completed` повторно -> `RETURN_ALREADY_COMPLETED`.
- `canceled` -> `RETURN_ALREADY_CANCELED`.
- Без строк -> `NO_LINES`.
- Ошибка применения эффектов -> `RETURN_COMPLETE_FAILED`.

Эффекты:
- `warehouse`: возврат quantity в исходную batch, при необходимости `status=available`.
- `defect`: создание `DefectRecord` (`source_type=return`).
- `rework`: создание `DefectRecord` (`source_type=return`) + `ReworkRequest`.

## 9. Cancel Return (`POST/PATCH /cancel/`)
- `draft`: только `status=canceled`.
- `completed`: выполняется rollback при соблюдении блокировок.
- повторный cancel -> `RETURN_ALREADY_CANCELED`.

Блокировки cancel completed:
- активный refund payment (`Payment.linked_return`, `payment_type=refund`, `status=active`) -> `REFUND_PAYMENT_EXISTS`.
- использованный downstream defect/rework -> `DOWNSTREAM_USED`.
- rollback склада уводит quantity в минус -> `WAREHOUSE_ROLLBACK_NEGATIVE`.
- технический сбой rollback -> `RETURN_ROLLBACK_FAILED`.

Rollback атомарный: при ошибке статус возврата не меняется.

## 10. Return targets / condition types
- `return_target`: `warehouse`, `defect`, `rework`.
- `condition_type`: `good`, `damaged`, `defect`.

Принятые frontend defaults:
- `warehouse -> good`
- `defect -> defect`
- `rework -> damaged`

Жесткая связка target/condition backend-правилом не enforced (кроме валидности enum-значений).

## 11. Quantity rules
- `returnable_quantity = sale_line.quantity - returned_quantity`.
- `returned_quantity` считает `ReturnLine` по `sale_line`, исключая `return_doc.status=canceled`.
- `quantity > 0` обязательно.
- превышение лимита -> `RETURN_QUANTITY_EXCEEDED`.

## 12. Select-sources
`GET /api/returns/select-sources/` и `?sale_id=...` возвращает:
- `sales`: только `shipped|closed`, только с `returnable_quantity>0`.
  - поля: `id`, `label`, `client`, `client_name`, `sale_status`, `returnable_quantity`.
- `sale_lines` (когда передан `sale_id`): только с `returnable_quantity>0`.
  - поля: `id`, `label`, `product`, `sold_quantity`, `returned_quantity`, `returnable_quantity`, `unit_price`.

## 13. Downstream links (retrieve)
`GET /api/returns/{id}/` включает:
- warehouse effects (`warehouse_batch_id`, quantity, source_return_line_id),
- defect records,
- rework requests,
- active refund payments.

`downstream_links` и `linked_entities` read-only.

## 14. Error codes
Используются стабильные коды:
- `MISSING_SALE`
- `INVALID_SALE_STATUS`
- `MISSING_LINES`
- `MISSING_SALE_LINE`
- `SALE_LINE_NOT_IN_SALE`
- `INVALID_QUANTITY`
- `RETURN_QUANTITY_EXCEEDED`
- `INVALID_RETURN_TARGET`
- `INVALID_CONDITION_TYPE`
- `RETURN_STATUS_CREATE_FORBIDDEN`
- `RETURN_STATUS_UPDATE_FORBIDDEN`
- `RETURN_UPDATE_FORBIDDEN`
- `RETURN_LINE_UPDATE_FORBIDDEN`
- `RETURN_ALREADY_COMPLETED`
- `RETURN_ALREADY_CANCELED`
- `RETURN_COMPLETE_FAILED`
- `RETURN_ROLLBACK_FAILED`
- `WAREHOUSE_ROLLBACK_NEGATIVE`
- `DOWNSTREAM_USED`
- `REFUND_PAYMENT_EXISTS`
- `NO_LINES`
- `DELETE_DISABLED`

## 15. Tests
Добавлен `apps/sales/tests/test_returns_api.py`, покрыто:
- strict create rules;
- update rules by status;
- complete lifecycle;
- cancel lifecycle и locks;
- select-sources shape/rules;
- delete contract.

## 16. Frontend contract
- Для create/update отправлять только бизнес-поля документа и `lines`.
- Не отправлять `status` в обычный PATCH/PUT.
- Смена статуса только через `complete`/`cancel`.
- Для refund использовать payment API с `linked_return`.
- Кнопка delete в UI не показывается.

## 17. Problems
- Critical: не найдено.
- Medium: не найдено.
- Minor: не найдено.

## 18. Final verdict
Возвраты backend contract:
- **OK**

Возвраты backend contract закрыт.
