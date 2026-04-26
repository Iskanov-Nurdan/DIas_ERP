# Backend API Contract Review — Брак / переделка (после фиксов)

## Изменения по контракту
- `sell_defect`: добавлены проверки `client exists/is_active`, `DEFECT_NOT_AVAILABLE`, `QUANTITY_EXCEEDED`, сохранены стабильные `MISSING_CLIENT/MISSING_PRICE/MISSING_QUANTITY/INVALID_PRICE/INVALID_QUANTITY/WAREHOUSE_APPLY`.
- `rework complete`: добавлены guards `REWORK_ALREADY_COMPLETED`, `REWORK_ALREADY_CANCELED`, `REWORK_COMPLETE_FORBIDDEN`, `INVALID_TRANSITION`, `MISSING_FIELDS`, `INVALID_QUANTITY`, `NEGATIVE_QUANTITY`, `QTY_BOUNDS`, `INVALID_QUALITY`.
- `rework cancel`: добавлены `REWORK_ALREADY_COMPLETED`, `REWORK_ALREADY_CANCELED`, `REWORK_CANCEL_FORBIDDEN`, `INVALID_TRANSITION`, `WAREHOUSE_ROLLBACK`.
- update guards:
  - `PATCH/PUT /api/defects/{id}/` запрещает прямой `status` и счетчики -> `DEFECT_UPDATE_FORBIDDEN`.
  - `PATCH/PUT /api/rework-requests/{id}/` запрещает `status/quantity/result_warehouse_batch`, завершенные/отмененные записи не редактируются, для `pending/in_progress` разрешен только `comment` -> `REWORK_UPDATE_FORBIDDEN`.
- `GET /api/defects/select-sources/` расширен:
  - `warehouse_defect_batches`: только `quality=defect`, `status=available`, `quantity>0`, без уже связанных `DefectRecord`.
  - `return_lines`: только строки без уже существующего `DefectRecord` по `source_type=return/source_id`.
  - добавлены frontend-ready label и структурные поля (`product`, `quantity_pcs`, `available_quantity_pcs`, `return_number` и т.д.).
- `GET /api/rework-requests/select-sources/` расширен:
  - исключены `sold/written_off/closed`.
  - только записи с положительным доступным количеством.
  - добавлены `display_quantity`, `display_quantity_label`, `available_quantity_pcs`, `source_label`.

## Defect Number
- Выбран вариант **B**.
- `defect_number` официально **не реализован**.
- Frontend должен показывать: `Брак #<id>`.

## Endpoints (актуальные)
- Defects: `GET/POST /api/defects/`, `GET/PATCH/PUT/DELETE /api/defects/{id}/`, `GET /api/defects/select-sources/`, `POST /api/defects/{id}/send-to-rework/`, `POST /api/defects/{id}/writeoff/`, `POST /api/defects/{id}/sell/`, `POST /api/defects/{id}/complete-rework/` (405 `USE_REWORK_COMPLETE`).
- Rework: `GET/POST /api/rework-requests/`, `GET/PATCH/PUT/DELETE /api/rework-requests/{id}/`, `GET /api/rework-requests/select-sources/`, `POST /api/rework-requests/{id}/start/`, `POST /api/rework-requests/{id}/complete/`, `POST /api/rework-requests/{id}/cancel/`.

## Коды ошибок (закреплено)
- `MISSING_DEFECT`, `NO_DEFECT`, `MISSING_QUANTITY`, `INVALID_QUANTITY`, `NEGATIVE_QUANTITY`, `QUANTITY_EXCEEDED`, `QTY_TOO_HIGH`, `INVALID_STATUS`, `INVALID_TRANSITION`, `DEFECT_ALREADY_EXISTS`, `DEFECT_NOT_AVAILABLE`, `MISSING_CLIENT`, `INACTIVE_CLIENT`, `MISSING_PRICE`, `INVALID_PRICE`, `MISSING_REASON`, `WAREHOUSE_APPLY`, `WAREHOUSE_ROLLBACK`, `REWORK_ACTIVE`, `REWORK_ALREADY_COMPLETED`, `REWORK_ALREADY_CANCELED`, `REWORK_COMPLETE_FORBIDDEN`, `REWORK_CANCEL_FORBIDDEN`, `USE_REWORK_COMPLETE`, `DELETE_DISABLED`.

## Select-sources contract
### `GET /api/defects/select-sources/`
- `return_lines[]`: `{id,label,product,quantity_pcs,return_id,return_number}`
- `warehouse_defect_batches[]`: `{id,label,product,available_quantity_pcs,quantity_pcs,quality,status}`

### `GET /api/rework-requests/select-sources/`
- `defect_records[]`: `{id,label,product_name,quantity_pcs,quantity_kg,defect_reason,source_type,source_label,display_quantity,display_quantity_label,available_quantity_pcs,status}`
- `result_warehouse_batches[]` остается только для legacy; frontend не использует его при `complete`.

## Тесты
- Добавлены:
  - `apps/sales/tests/test_defects_api.py`
  - `apps/sales/tests/test_rework_api.py`
- Покрытие: create/sell/writeoff/send-to-rework/complete-rework-disabled/delete-disabled/select-sources/update-guards для defects; create/start/complete/cancel/delete/select-sources/update-guards для rework.

## FINAL VERDICT
Брак / переделка backend contract:
- **OK**
- **Брак / переделка backend contract закрыт**

