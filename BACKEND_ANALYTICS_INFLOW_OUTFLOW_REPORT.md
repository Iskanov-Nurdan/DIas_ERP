# Что исправлено

## summary

- В `cards` добавлено **`purchase_total`**: `Sum(MaterialBatch.total_price)` за период по дате прихода (`received_at`), тот же фильтр, что у `purchase-details` (`scope.incoming_date_q()`).
- Остальные KPI без смешивания: `revenue_total`, `sales_cost_total`, `profit_total`, `production_cost_total`, счётчики продаж/выпуска, ОТК, склад.

## detail endpoints

- **`revenue-details`**: строки только с полями `date`, `client_name`, `profile_name`, `product_name`, `quantity`, `price_per_unit`, `revenue` (+ корень `period`, `total`).
- **`sales-cost-details`**: без изменения контракта по смыслу (`date`, `sale_id`, `order_number`, `product_name`, `profile_name`, `quantity`, `cost_per_unit`, `total_cost`).
- **`production-cost-details`**: из строк убраны технические `profile_id` / `line_id`, остались `profile_name`, `line_name` и прочие поля по ТЗ.
- **`purchase-details`**: из строк убран `material_id`, остались `material_name`, `supplier_name`, количество, цена, сумма.
- **`profit-details`**: строки приведены к полям `date`, `sale_id`, `order_number`, **`object`** (краткая подпись сделки), `revenue`, `sales_cost`, `profit`; убраны лишние разрезы из строки.

## formulas

| Метрика | Формула |
|--------|---------|
| revenue_total | `Sum(Sale.revenue)` с `sale_scope_q` |
| sales_cost_total | `Sum(Sale.cost)` |
| profit_total | `revenue_total - sales_cost_total` |
| production_cost_total | `Sum(ProductionBatch.material_cost_total)` с `production_batch_scope_q` |
| purchase_total | `Sum(MaterialBatch.total_price)` с `incoming_date_q` |
| sales_count | `Count(Sale)` |
| sold_units_total | `Sum(sold_pieces если >0 иначе quantity)` |
| produced_units_total | `Sum(ProductionBatch.pieces)` |
| produced_meters_total | `Sum(ProductionBatch.total_meters)` |
| otk_* | агрегаты по `OtkCheck` для партий периода |
| warehouse_* | суммы `WarehouseBatch.quantity` по статусам (фильтры склада без периода по дате партии — как в текущей логике scope склада) |

## filters

Query-параметры как раньше: `year`, `month`, `day`, `date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `otk_status`, `trend_group`.

- **Продажи** (`revenue_*`, `sales_cost_*`, `profit_*`, `sales_count`, `sold_units_*`): применяются `sale_scope_q` — клиент, линия/профиль/рецепт/партия через склад продажи, дата продажи.
- **Производство** (`production_cost_*`, `produced_*`, ОТК по партиям): `production_batch_scope_q` — линия, профиль, рецепт, партия, `otk_status`.
- **Закупки** (`purchase_total`, тренд `purchase_total`, `purchase-details`): только **`incoming_date_q`** (дата прихода сырья); `client_id`, `line_id`, `otk_status` к партиям `MaterialBatch` не применяются (не относятся к выборке).
- **Склад ГП**: фильтры по профилю/линии/рецепту/партии производства, не по `otk_status` склада и не подмена `status` запроса статусом ОТК (см. `_analytics_batch_status_filter` в сервисах — только значения ОТК партии).

# Что теперь означает каждая метрика

- **revenue_total** — денежный приход от продаж (выручка).
- **sales_cost_total** — себестоимость уже проданного товара (`Sale.cost`), не закупка и не `material_cost_total` партии производства.
- **profit_total** — валовая прибыль по продажам: выручка минус себестоимость продаж.
- **production_cost_total** — затраты на выпуск (материальная себестоимость партий производства).
- **purchase_total** — денежный объём закупок сырья по партиям прихода за период.

# Какие endpoint'ы теперь есть

| Метод и путь | Назначение |
|--------------|------------|
| `GET /api/analytics/summary/` | KPI + тренды + разрезы |
| `GET /api/analytics/revenue-details/` | Только выручка по продажам |
| `GET /api/analytics/sales-cost-details/` | Только `Sale.cost` |
| `GET /api/analytics/production-cost-details/` | Только `ProductionBatch.material_cost_total` |
| `GET /api/analytics/purchase-details/` | Только партии прихода сырья |
| `GET /api/analytics/profit-details/` | Прибыль по строкам продаж |
| `GET /api/analytics/otk-details/` | ОТК |
| `GET /api/analytics/writeoff-details/` | Списания сырья (FIFO), отдельно от KPI закупок/продаж |

# Что убрано

- Общий **`expense-details`** (ранее удалён): не один «расход на всё».
- Из ответов детализаций убраны **двусмысленные / лишние поля** там, где по ТЗ нужен ровный набор колонок (`id`/`profile_id` в revenue, `material_id` в purchase, `profile_id`/`line_id` в production-cost, лишние поля в profit-details).
- Нет смешивания закупок с `sales_cost` или с `revenue` в одном показателе: закупки только в `purchase_total` / тренде / `purchase-details`.

# Что проверить вручную

- `GET /api/analytics/summary` — наличие `cards.purchase_total`, корректность при наличии приходов сырья в периоде; `trends[].purchase_total`.
- `GET /api/analytics/revenue-details` — только колонки продаж, `total` = сумма `revenue`.
- `GET /api/analytics/sales-cost-details` — сумма `total_cost` = `sales_cost_total` за тот же период.
- `GET /api/analytics/production-cost-details` — сумма `material_cost_total` = `production_cost_total`.
- `GET /api/analytics/purchase-details` — сумма `total_amount` = `purchase_total`.
- `GET /api/analytics/profit-details` — поля `object`, `sales_cost`, согласованность с продажами.
