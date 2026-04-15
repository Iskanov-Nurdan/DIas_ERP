# Что было не так

1. **Две производственные ветки.** `POST /api/batches/` вызывал `apply_production_batch_stock_and_cost` в `ProductionBatchCreateUpdateSerializer.create`, а поток **RecipeRun → `submit_recipe_run_to_otk`** создавал `ProductionBatch` и заказ **без** FIFO и без пересчёта `material_cost_total`, оставляя себестоимость нулевой при ненулевом расходе по рецепту.

2. **RecipeRun как «тихий» центр правды.** Комментарий в коде прямо говорил, что списание только у `POST /api/batches/`, из‑за чего замес формально вёл в ОТК партию без фактического производства в смысле складских движений.

3. **Обновление объёма у партии из замеса.** При изменении `quantity` у уже созданной pending‑партии пересчёта списания **не было** — остатки и себестоимость расходились с новым `total_meters`.

4. **ОТК и склад без гарантии целостности.** `submit-for-otk` и `otk_accept` не проверяли, что партия прошла расчёт материальной себестоимости при ненулевой норме расхода; теоретически в очередь могла попасть «пустая» партия (наследие старого сценария или сбоев данных).

5. **Резерв склада ГП.** Статус `reserved` не уменьшал `quantity`, при этом в `POST …/reserve/` можно было передать **частичное** `quantity`, хотя строка целиком переводилась в `reserved` — вводящая в заблуждение семантика. Продажа уже требовала `status == available` в `apply_sale_to_warehouse_batch`, но в `SaleSerializer` в `PrimaryKeyRelatedField` был queryset **всех** партий — можно было выбрать зарезервированную партию на этапе валидации, пока не доходили до списания.

---

# Что исправлено

1. **`submit_recipe_run_to_otk` (`apps/production/views.py`)**  
   - После создания новой `ProductionBatch` вызывается **`apply_production_batch_stock_and_cost(batch)`** в той же транзакции, что и заказ/партия/привязка к `RecipeRun`.  
   - Для уже существующей pending‑партии при смене объёма вызывается **`resync_production_batch_consumption`** (откат по старым метрам/рецепту + повторный `apply`).  
   - Создание партии из замеса **только при живом FK рецепта** с компонентами; **обязательны линия, открытая не на паузе смена текущего пользователя** и найденный `Shift` — те же условия, что у `POST /api/batches/`.  
   - У новой партии выставляются `lifecycle_status`, `sent_to_otk`, `in_otk_queue`, `produced_at`, как у сериализатора создания партии.

2. **Централизованный пересчёт списания (`apps/production/batch_stock.py`)**  
   - Добавлены **`resync_production_batch_consumption`**, **`assert_production_batch_ready_for_otk_pipeline`**, **`production_batch_has_positive_material_requirement`**.  
   - `ProductionBatchCreateUpdateSerializer.update` переведён на **`resync_production_batch_consumption`** вместо ручной пары `reverse` + `apply` (поведение то же, один вход).

3. **Защита ОТК и склада (`apps/production/views.py`)**  
   - Перед переводом в очередь ОТК: **`assert_production_batch_ready_for_otk_pipeline(batch)`** в `submit_for_otk`.  
   - Перед приёмкой: тот же assert в **`otk_accept`**.  
   - Условия assert: есть рецепт в БД, есть компоненты, `total_meters > 0`; если по рецепту и метражу есть **положительный** расход сырья/химии — **`material_cost_total > 0`**.

4. **Резерв и продажи**  
   - **`apps/warehouse/views.py`**, действие `reserve`: **`quantity` обязано равняться полному `batch.quantity`** (резерв только всей строки; сообщение об ошибке при несовпадении).  
   - **`apps/sales/serializers.py`**: у поля **`warehouse_batch`** queryset ограничен **`status=STATUS_AVAILABLE`**, чтобы нельзя было привязать продажу к зарезервированной/отгруженной строке на этапе сериализатора.

5. **Документация в коде**  
   - Обновлён docstring **`RecipeRunViewSet`** под новую роль замеса.

---

# Как теперь работает производство

**Итоговая фактическая бизнес-цепочка (единая):**

Сырьё (партии, FIFO) → Химия (выпуск, FIFO сырья) → Рецепт (нормы на 1 м, без списания) → **`ProductionBatch`** (здесь `total_meters`, FIFO сырья и химии, `material_cost_total`, `cost_per_meter`, `cost_per_piece`) → **ОТК** (только после проверки целостности партии) → **Склад ГП** (`WarehouseBatch` из приёмки) → **Продажи** (только `available` + списание через `apply_sale_to_warehouse_batch`).

**Пошагово:**

1. **Прямой путь:** `POST /api/batches/` с телом как раньше → валидация смены/линии/рецепта → `ProductionBatch` → **`apply_production_batch_stock_and_cost`**.

2. **Путь через замес:** `POST /api/production/recipe-runs/` (или `submit-to-otk`) → заказ + партия с теми же предпосылками (рецепт, линия, смена) → **`apply_production_batch_stock_and_cost`** (или **`resync_…`** при правке объёма) → та же партия в `pending`.

3. **`POST /api/batches/{id}/submit-for-otk/`** — перед сменой статуса вызывается **`assert_production_batch_ready_for_otk_pipeline`**.

4. **`POST /api/batches/{id}/otk_accept/`** — снова assert, затем приёмка и при `accepted > 0` создание **`WarehouseBatch`** с копией `cost_per_piece` / `cost_per_meter` с партии.

---

# Как теперь работает RecipeRun

- **Роль:** черновик/план (ёмкости `RecipeRunBatch`, строки `RecipeRunBatchComponent`) и удобный сценарий создания **той же** `ProductionBatch`, что и через `/api/batches/`, без второй модели списания.
- **Не делает:** отдельного FIFO, отдельной себестоимости, обхода `apply_production_batch_stock_and_cost`.
- **Связь с правдой:** после сохранения партии вызывается **единый** `apply_production_batch_stock_and_cost` из `apps/production/batch_stock.py`. При удалении замеса с pending‑партией по‑прежнему **`reverse_production_batch_stock`** (как и при откате в сериализаторе партии).

Ограничение: создание партии из замеса **требует живого рецепта (FK)** — сценарий «только снимок рецепта без FK» для новой партии **запрещён** (иначе нельзя гарантировать нормы и FIFO).

---

# Гарантии системы

| Гарантия | Где в коде |
|----------|------------|
| Списание сырья и химии по производству | Только **`apply_production_batch_stock_and_cost`** и откат **`reverse_production_batch_stock`** / связка в **`resync_production_batch_consumption`** — `apps/production/batch_stock.py` |
| Себестоимость партии производства | Запись **`material_cost_total`** и пересчёт **`cost_per_meter` / `cost_per_piece`** в **`ProductionBatch.save`** после обновления `material_cost_total` в `apply_*` |
| Нет второй ветки без списания из замеса | **`submit_recipe_run_to_otk`** вызывает **`apply_production_batch_stock_and_cost`** после `batch.save()` |
| Нельзя отправить в ОТК / принять брак без расчёта при ненулевом расходе | **`assert_production_batch_ready_for_otk_pipeline`** в **`submit_for_otk`** и **`otk_accept`** |
| Продажа не цепляется к не‑available партии на уровне API выбора | **`SaleSerializer.__init__`** — queryset **`WarehouseBatch`** только **`available`** |
| Резерв не оставляет двусмысленного «частичного» резерва | **`reserve`**: **`quantity == batch.quantity`** |

**Почему нет двойной логики:** и `POST /api/batches/`, и поток RecipeRun после создания/изменения метража вызывают **одну и ту же** функцию **`apply_production_batch_stock_and_cost`** (либо **`resync_production_batch_consumption`**, внутри которой тот же `apply`).

---

# Что проверить вручную

1. **POST `/api/batches/`** с достаточными остатками — партия создана, в БД есть `MaterialStockDeduction` / `ChemistryStockDeduction` с `reason=production_batch`, `reference_id=batch.id`, `material_cost_total > 0` при ненулевых нормах.

2. **POST `/api/production/recipe-runs/`** с тем же рецептом и линией — после успеха те же дедукции и ненулевая `material_cost_total`; при недостатке сырья/химии — **4xx** и **нет** «висячего» заказа/партии (откат транзакции).

3. **PATCH recipe-run** с изменением `quantity` / `output_scale` у pending‑партии — остатки соответствуют новому `total_meters` (без двойного списания: сначала откат по старым метрам).

4. **`submit-for-otk`** для партии без расчёта (ручная порча `material_cost_total` в БД) — **400** с кодом **`INCOMPLETE_PRODUCTION_COST`** (или связанным телом ошибки).

5. **`otk_accept`** — то же для «битой» партии.

6. **Резерв:** `POST …/reserve/` с `quantity` **меньше** полного остатка — **400**; с **равным** — статус `reserved`, затем попытка создать продажу с этим `warehouse_batch_id` — **ошибка валидации** на сериализаторе.

7. **Продажа** только с партии в `available` — попытка указать id зарезервированной строки в `warehouse_batch` — **400** от DRF (недопустимый PK для поля).

8. **Удаление RecipeRun** с pending‑партией — остатки возвращаются, партия и заказ удалены.

---

# Ограничения / честные замечания

- **`python manage.py check`** в среде выполнения агента не удалось прогнать до конца из‑за отсутствия пакета **`corsheaders`** в использованном интерпретаторе; синтаксис и импорты проверены линтером IDE для изменённых файлов.
- **Старые данные в БД:** партии с нулевой себестоимостью при ненулевом расходе теперь **не пройдут** `submit-for-otk` / `otk_accept` до ручного исправления или пересоздания партии через корректный поток.
- **Прямой PATCH `/api/batches/{id}/`** для партии, привязанной к RecipeRun, по‑прежнему **запрещён** (кроме ограничений сериализатора) — изменение объёма через **RecipeRun** или отвязка/другая бизнес-политика остаётся на усмотрение продукта; это не менялось, чтобы не раздувать объём правок.

---

*Изменённые файлы: `apps/production/batch_stock.py`, `apps/production/views.py`, `apps/production/serializers.py`, `apps/warehouse/views.py`, `apps/sales/serializers.py`.*
