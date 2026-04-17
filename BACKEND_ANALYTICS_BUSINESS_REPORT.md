# Что изменено

## summary

- Ответ сводки упрощён: только бизнес-поля — `period`, `trend_group`, `cards`, `otk_summary`, `warehouse_summary`, `production_summary`, `trends`, `sales_by_profile`, `sales_by_client`, `production_by_line`.
- Удалены блоки `expenses`, `production_totals` и KPI `material_writeoff_total` из карточек.
- В `cards` добавлены недостающие агрегаты: `produced_units_total`, `produced_meters_total`; порядок полей приведён к списку главных KPI.
- `sold_units_total` считается по каноничной логике: если `sold_pieces > 0` — сумма `sold_pieces`, иначе сумма `quantity`.
- Снимок склада (`warehouse_*`) строится через `warehouse_batches_scope_qs`: учитываются фильтры `profile_id`, `line_id` (через `source_batch__line_id`), `recipe_id`, `batch_id` (партия производства). Фильтр `client_id` на склад не накладывается (клиент не свойство партии ГП).
- В объекте `period` поле статуса ОТК называется только `otk_status` (без дублирующего `status`).

## formulas

- `revenue_total` — `Sum(Sale.revenue)` в рамках периода и фильтров продаж.
- `sales_cost_total` — `Sum(Sale.cost)`.
- `profit_total` — `revenue_total - sales_cost_total`.
- `production_cost_total` — `Sum(ProductionBatch.material_cost_total)`.
- `sales_count` — `Count(Sale)`.
- `sold_units_total` — сумма по строкам продаж выражения «`sold_pieces` если > 0, иначе `quantity`».
- `produced_units_total` — `Sum(ProductionBatch.pieces)`.
- `produced_meters_total` — `Sum(ProductionBatch.total_meters)`.
- `otk_accepted_total` / `otk_defect_total` — `Sum(OtkCheck.accepted)` / `Sum(OtkCheck.rejected)` по проверкам партий, попавших в период и фильтры производства.
- `otk_defect_percent` — `defect / (accepted + defect) * 100` при ненулевой сумме.
- `warehouse_*` — суммы `WarehouseBatch.quantity` по статусам `available` / `reserved` / `shipped` и качеству `good` / `defect` в `warehouse_summary`.
- `trends[]` — по `Sale.date` и `ProductionBatch.date` в границах периода: `revenue`, `sales_cost`, `profit`, `production_cost`.

## trends

- Параметр `trend_group`: `day` | `month` (по умолчанию — авто: >62 дней периода → `month`).
- Каждая точка: `period`, `revenue`, `sales_cost`, `profit`, `production_cost`.

## details

- `GET /api/analytics/revenue-details/` — те же query-параметры периода и фильтров, что и у сводки; строки продаж с `profile_id` / `profile_name` при наличии партии склада.
- `GET /api/analytics/expense-details/` — при `expense_type=all` только `sales_cost` и `production_cost`; `material_writeoff` и `purchase_cost` — только при явном типе.
- `GET /api/analytics/profit-details/` — продажи с `revenue`, `cost`, `profit` и итоги `totals`.
- `GET /api/analytics/otk-details/` — строки ОТК по партиям периода.
- `GET /api/analytics/writeoff-details/` — без изменения назначения (внутренний FIFO по сырью), по-прежнему требует `year`.

## filters

- Поддерживаются: `year`, `month`, `day`, `date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `otk_status`, `trend_group`.
- Статус ОТК партии: только `otk_status` (в коде парсера по-прежнему принимается и `status` как алиас с тем же смыслом, но только значения `pending` | `accepted` | `rejected`; иные значения игнорируются и не обнуляют выборку).

# Что убрано

- KPI-карточка и агрегат `material_writeoff_total` из сводки.
- Блок `expenses` с `purchase_incoming` и разбивкой списаний как часть главного ответа.
- Вложенный дубль `production_totals` (выпуск перенесён в `cards`).
- Поле `status` внутри `period` (оставлен только `otk_status`).

# Какие поля теперь отдаёт summary

- `period` — год/месяц/день, `date_from`, `date_to`, фильтры id, `otk_status`.
- `trend_group` — строка.
- `cards` — `revenue_total`, `sales_cost_total`, `profit_total`, `production_cost_total`, `sales_count`, `sold_units_total`, `produced_units_total`, `produced_meters_total`, `otk_accepted_total`, `otk_defect_total`, `otk_defect_percent`, `warehouse_available_total`, `warehouse_reserved_total`, `warehouse_shipped_total` (строки с фиксированной точностью через API-хелпер).
- `otk_summary` — `accepted`, `defect`, `defect_percent`.
- `warehouse_summary` — `available`, `reserved`, `shipped`, `good`, `defect`.
- `production_summary` — `batches_count`.
- `trends` — массив точек тренда.
- `sales_by_profile` — `profile_id`, `profile_name`, `sales_count`, `sold_units`, `revenue`, `profit`.
- `sales_by_client` — `client_id`, `client_name`, `sales_count`, `sold_units`, `revenue`, `profit`.
- `production_by_line` — `line_id`, `line_name`, `produced_units`, `produced_meters`, `production_cost`, `batches`.

# Какие формулы теперь используются

См. раздел «formulas» выше; расхождений с перечислением в ТЗ нет.

# Что проверить вручную

- `GET /api/analytics/summary/?year=2026&month=…` — ненулевые KPI при наличии продаж, производства, ОТК и складских остатков.
- `GET /api/analytics/revenue-details/?year=2026&…` — соответствие строк фильтрам.
- `GET /api/analytics/expense-details/?year=2026&expense_type=all` — только продажи и производство.
- `GET /api/analytics/expense-details/?expense_type=purchase_cost` — только закупки (внутренний тип).
- `GET /api/analytics/profit-details/?…`
- `GET /api/analytics/otk-details/?…`

Подробные URL, query и примеры запросов для фронтенда: `FRONTEND_ANALYTICS_API.md`.
