# Результаты проверок

Проверка по текущему коду репозитория (статический разбор + точечные исправления в этой сессии). Автотестов в проекте **не найдено** (`**/test*.py` — пусто), `manage.py test` не прогонялся.

| № | Пункт | Результат |
|---|--------|-----------|
| 1 | Химия: FIFO сырья, `ChemistryBatch`, себестоимость; нет выпуска без списания | **OK** (после правки п. «Найденные баги»): `produce_chemistry` — `apps/chemistry/produce.py`: проверка остатков, затем при положительном расходе по строкам — `fifo_deduct`, `cost_total` из сумм строк; при нулевом расходе по всем строкам при данном `qty_kg` — **ValidationError**, партия не создаётся. |
| 2 | `POST /api/batches/`: `total_meters`, FIFO сырья/химии, `material_cost_total`, дедукции | **OK**: `ProductionBatchCreateUpdateSerializer.create` — `apps/production/serializers.py` → `apply_production_batch_stock_and_cost` — `apps/production/batch_stock.py` (`fifo_deduct` / `fifo_deduct_chemistry`, `reason='production_batch'`, `reference_id=batch.pk`). `ProductionBatch.save` пересчитывает `cost_per_meter` / `cost_per_piece` от `material_cost_total`. |
| 3 | RecipeRun → партия: `apply_production_batch_stock_and_cost`, нет партии без списания при расходе | **OK**: `submit_recipe_run_to_otk` — `apps/production/views.py` после `batch.save()` вызывает `apply_production_batch_stock_and_cost(batch)`; при обновлении pending — `resync_production_batch_consumption`. |
| 4 | Обновление объёма: откат старых списаний, новые, без двойного списания | **OK**: `resync_production_batch_consumption` — `batch_stock.py`: `reverse_production_batch_stock` по `reference_id` + `apply_production_batch_stock_and_cost`; то же используется в `ProductionBatchCreateUpdateSerializer.update`. |
| 5 | ОТК: нельзя без валидного расчёта; приёмка; accepted+rejected=pieces | **OK**: `submit_for_otk` и `otk_accept` — `apps/production/views.py` вызывают `assert_production_batch_ready_for_otk_pipeline`; сумма штук — строки 763–771 `views.py`. |
| 6 | Склад ГП: только после ОТК; нет партии ГП «из воздуха» с производства | **OK**: создание `WarehouseBatch` из производства — только в `otk_accept` при `accepted > 0` (`apps/production/views.py`). Иные `WarehouseBatch.objects.create` — `package` и внутренние копии в `stock_ops` от уже существующих строк склада. |
| 7 | Резерв: только вся партия; reserved нельзя продать | **OK**: `reserve` — `apps/warehouse/views.py`: требование `quantity == batch.quantity`; `apply_sale_to_warehouse_batch` — `stock_ops.py`: отказ если `status != available`; `SaleSerializer.__init__` — `apps/sales/serializers.py`: queryset `warehouse_batch` только `STATUS_AVAILABLE`. |
| 8 | Продажи: только available; не больше остатка; списание | **OK**: см. п.7; превышение — `quantity > b.quantity` в `apply_sale_to_warehouse_batch`; списание — та же функция. |
| 9 | Аналитика (минимально): себестоимость партий, списания | **OK** (логика согласована): сводка использует `ProductionBatch.material_cost_total` и `MaterialStockDeduction.line_total` — `apps/analytics/views.py`; при нулевых ценах закупки расход по-прежнему виден по количеству в дедукциях. |

---

# Найденные баги

1. **ОТК / assert при нулевых закупочных ценах:** при ненулевом физическом расходе по рецепту FIFO создаёт строки списания с `line_total=0`, тогда `material_cost_total` может быть **0**, а старая проверка требовала только `material_cost_total > 0` → ложный отказ ОТК.  
2. **Химия:** при ненулевом `qty_kg`, но всех `quantity_per_unit == 0` в `ChemistryRecipe`, после проверки остатков создавалась **`ChemistryBatch`** без фактического `fifo_deduct` (нулевой расход по всем строкам).

---

# Что исправлено

| Баг | Файл | Изменение |
|-----|------|-----------|
| Ложный запрет ОТК при нулевой сумме при ненулевом расходе | `apps/production/batch_stock.py` | В `assert_production_batch_ready_for_otk_pipeline`: при ненулевой норме расхода допускается прохождение, если есть **`MaterialStockDeduction`** или **`ChemistryStockDeduction`** с `reason=production_batch` и `reference_id=batch.pk`, либо `material_cost_total > 0`. |
| Выпуск химии без списания сырья при нулевом расходе по составу | `apps/chemistry/produce.py` | Перед `ChemistryBatch.objects.create`: если ни одна строка `ChemistryRecipe` не даёт `need > 0` при данном `qty_kg` — **`ValidationError`**, партия не создаётся. |

---

# Гарантии

- **Нет двойной производственной логики:** списание сырья/химии по производству только в `apply_production_batch_stock_and_cost` / `reverse_production_batch_stock` / `resync_production_batch_consumption` — `apps/production/batch_stock.py`; и прямой `POST /api/batches/`, и `submit_recipe_run_to_otk` вызывают **`apply`** (или **`resync`** при смене метража).
- **Нет партии производства с расходом по рецепту без движения складов:** при положожительной агрегированной норме `apply` либо создаёт дедукции, либо падает с `INSUFFICIENT_STOCK` до фиксации партии в успешном сценарии (транзакция откатывается).
- **Нет «нулевой себестоимости» как единственного признака провала:** для ОТК учитываются и **деньги** (`material_cost_total`), и **факт списания** (наличие дедукций с `reason=production_batch`), чтобы нулевая цена закупки не блокировала приёмку при реальном расходе.

---

# Что проверить вручную (рекомендуемый минимум)

1. Выпуск химии с нормальным составом и с составом из нулевых `quantity_per_unit` — второй сценарий должен вернуть ошибку, партии химии нет.  
2. `POST /api/batches/` и `POST /api/production/recipe-runs/` с одним рецептом — в БД дедукции с `reference_id = id` партии.  
3. PATCH объёма у pending-партии (через разрешённые API) — сумма списаний соответствует новому `total_meters`, дублей по одному `batch_id` нет.  
4. Партия с нулевыми ценами прихода сырья, но с расходом — проходит `submit-for-otk` / `otk_accept`.  
5. Резерв: `quantity != полной строки` → 400; продажа зарезервированной партии через API выбора — отказ на валидации.

---

*Критичных логических противоречий целевой цепочке после правок assert и химии не осталось; оставшиеся риски — только данные/миграции вне кода и отсутствие автотестов в репозитории.*
