# Что было неправильно

- Эндпоинт `GET /api/analytics/expense-details/` смешивал в одном ответе разные типы расходов через `expense_type` (себестоимость продаж, производства, списания сырья/химии, закупки). Для бизнес-KPI это создавало риск путаницы: закупка сырья и складской приход не должны считаться «расходом» в одной корзине с продажами, а списание сырья — отдельный учётный слой, не главный KPI экрана.
- В `profit-details` поле `cost` в строках и в `totals` было недостаточно явным (неясно, это себестоимость продаж или что-то ещё).
- В ответе `revenue-details` не было `period`, как у остальных детализаций по периоду.

# Что исправлено

## summary

- Логика `cards` без изменений по смыслу: `revenue_total` = сумма `Sale.revenue`, `sales_cost_total` = сумма `Sale.cost`, `profit_total` = разница, `production_cost_total` = сумма `ProductionBatch.material_cost_total`. Закупки и списания сырья в верхние KPI не входят.
- `trends`: по-прежнему `period`, `revenue`, `sales_cost`, `profit`, `production_cost`; группировка `day` | `month`.
- Блоки `otk_summary`, `warehouse_summary`, `sales_by_profile`, `sales_by_client`, `production_by_line` сохранены.

## details

- Удалён `GET /api/analytics/expense-details/`.
- Добавлены отдельные эндпоинты:
  - `GET /api/analytics/sales-cost-details/` — только `Sale.cost` (себестоимость продаж).
  - `GET /api/analytics/production-cost-details/` — только `ProductionBatch.material_cost_total`.
  - `GET /api/analytics/purchase-details/` — только партии прихода сырья (`MaterialBatch`), без смешивания с продажами.
- `GET /api/analytics/revenue-details/` — в ответ добавлен `period` (те же query-параметры периода, что и у summary).
- `GET /api/analytics/profit-details/` — в строках и в `totals` вместо `cost` используется явное имя `sales_cost`.

## formulas

- `revenue_total` = `Sum(Sale.revenue)` за период и фильтры scope.
- `sales_cost_total` = `Sum(Sale.cost)`.
- `profit_total` = `revenue_total - sales_cost_total`.
- `production_cost_total` = `Sum(ProductionBatch.material_cost_total)`.
- `sales_count` = `Count(Sale)`.
- `sold_units_total` = `Sum(Case(sold_pieces>0 → sold_pieces, иначе quantity))`.
- `produced_units_total` = `Sum(ProductionBatch.pieces)`.
- `produced_meters_total` = `Sum(ProductionBatch.total_meters)`.
- ОТК и склад — как в текущем коде агрегатов по `OtkCheck` и `WarehouseBatch` со статусами available / reserved / shipped.

## fields

### summary → `cards`

`revenue_total`, `sales_cost_total`, `profit_total`, `production_cost_total`, `sales_count`, `sold_units_total`, `produced_units_total`, `produced_meters_total`, `otk_accepted_total`, `otk_defect_total`, `otk_defect_percent`, `warehouse_available_total`, `warehouse_reserved_total`, `warehouse_shipped_total` (строки сумм через `api_decimal_str`).

### `GET /api/analytics/revenue-details/`

- `period`, `total`, `items[]`: `id`, `date`, `client_name`, `profile_id`, `profile_name`, `product_name`, `quantity`, `price_per_unit`, `revenue`.

### `GET /api/analytics/sales-cost-details/`

- `period`, `total_sales_cost`, `items[]`: `date`, `sale_id`, `order_number`, `product_name`, `profile_name`, `quantity`, `cost_per_unit` (или `null`), `total_cost`.

### `GET /api/analytics/production-cost-details/`

- `period`, `total_production_cost`, `items[]`: `date`, `production_batch_id`, `profile_id`, `profile_name`, `line_id`, `line_name`, `quantity_pieces`, `total_meters`, `material_cost_total`.

### `GET /api/analytics/purchase-details/`

- `period`, `total_purchase_amount`, `items[]`: `date`, `material_id`, `material_name`, `supplier_name`, `quantity`, `unit_price`, `total_amount`.

### `GET /api/analytics/profit-details/`

- `totals`: `revenue`, `sales_cost`, `profit`.
- `items[]`: поле себестоимости продажи — `sales_cost` (ранее `cost`).

Общие query-параметры периода/фильтров: как у summary (`year`, `month`, `day`, `date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `otk_status`, для summary ещё `trend_group`).

# Что теперь означает каждая метрика

- **revenue_total** — только денежный приход от продаж: выручка по проводкам `Sale`.
- **sales_cost_total** — только себестоимость уже проданного товара: поле `Sale.cost` (COGS продаж), не закупка и не производственная себестоимость партии в целом.
- **profit_total** — валовая прибыль по продажам в периоде: выручка минус себестоимость продаж; не включает закупки сырья и не заменяет управленческий P&L.
- **production_cost_total** — материальная себестоимость выпуска за период: `ProductionBatch.material_cost_total`; не подмешивается в `sales_cost_total` и не считается выручкой.

# Что убрано из главной аналитики

- Эндпоинт `analytics/expense-details` и объединённая выдача «все расходы / по типу» в одном списке.
- Использование `expense_type` с `material_writeoff` и `purchase_cost` в рамках одного URL с продажами/производством (закупки и списания — вне верхних KPI; закупки — только `purchase-details`, списания сырья по-прежнему доступны через `analytics/writeoff-details/` для внутреннего учёта FIFO).

# Что проверить вручную

- **summary** — за период с одной партией (100 шт, 100 м, OTK 90/10), одной продажей 50 шт: `revenue_total`, `sales_cost_total`, `profit_total`, `production_cost_total`, счётчики ОТК и складские поля.
- **revenue-details** — только продажи, сумма `total` = сумма строк `revenue`, есть `period`.
- **sales-cost-details** — только продажи, `total_sales_cost` = сумма `Sale.cost` за период.
- **production-cost-details** — только партии производства и `material_cost_total`.
- **purchase-details** — только приходы `MaterialBatch`, сумма `total_purchase_amount` не должна попадать в `sales_cost` или `revenue`.

---

## Промпт для фронтенда

Скопируйте текст ниже агенту/разработчику фронтенда.

```
Бэкенд аналитики ERP разделил денежный приход, себестоимость продаж, себестоимость производства и закупки сырья. Не смешивай их в одном виджете «приход/расход».

Summary: GET /api/analytics/summary/? + те же query, что раньше (year, month, day, date_from, date_to, фильтры line_id, client_id, profile_id, recipe_id, batch_id, otk_status, trend_group).

Главные KPI только в response.cards:
- revenue_total — выручка (Sum Sale.revenue)
- sales_cost_total — себестоимость продаж (Sum Sale.cost)
- profit_total — revenue_total - sales_cost_total
- production_cost_total — Sum ProductionBatch.material_cost_total
Плюс sales_count, sold_units_total, produced_*, otk_*, warehouse_*.

trends[]: period, revenue, sales_cost, profit, production_cost (группировка day|month через trend_group / авто).

Удалён маршрут GET /api/analytics/expense-details/ — убери все вызовы.

Новые маршруты (те же query-параметры периода/фильтров, что у summary, кроме trend_group):
- GET /api/analytics/sales-cost-details/ → period, total_sales_cost, items (sale_id, order_number, quantity, cost_per_unit, total_cost, …)
- GET /api/analytics/production-cost-details/ → period, total_production_cost, items (production_batch_id, profile_*, line_*, quantity_pieces, total_meters, material_cost_total)
- GET /api/analytics/purchase-details/ → period, total_purchase_amount, items (material_*, supplier_name, quantity, unit_price, total_amount)

GET /api/analytics/revenue-details/ — добавлено поле period в корень ответа; total и items без изменения смысла.

GET /api/analytics/profit-details/ — breaking change: в items и totals поле себестоимости переименовано с cost на sales_cost; totals: revenue, sales_cost, profit.

Списания сырья (FIFO) остаются на GET /api/analytics/writeoff-details/?year=… — это не главный бизнес-KPI, не подставляй в блок «выручка/прибыль».
```
