# BACKEND_AUDIT_DOC_V3 — фактическое состояние бэкенда ERP (код на момент составления)

Базовый префикс HTTP API: `/api/` (см. `config/urls.py`, `config/api_urls.py`). Дополнительные алиасы без `/api/` для совместимости с клиентом: `config/urls.py` (`users/…`, `warehouse/pack…`, `batches/pack_from_otk/`).

---

## 1. Общая цепочка (как реализовано в коде)

### 1.1. Сырьё (`apps/materials/`)

- **Справочник:** `RawMaterial` — создаётся через `POST /api/raw-materials/` (`RawMaterialViewSet`, `apps/materials/views.py`, сериализатор `RawMaterialSerializer`, `apps/materials/serializers.py`).
- **Приход партии:** `MaterialBatch` — создаётся через `POST /api/incoming/` (`IncomingViewSet`, только GET/POST). Количество из тела переводится в **кг хранения** в `MaterialBatchSerializer.create()` через `quantity_to_storage_kg()`; в БД `unit` партии принудительно `'kg'` (`apps/materials/serializers.py`).
- **Остаток сырья:** поле `MaterialBatch.quantity_remaining`; уменьшается только при списаниях `MaterialStockDeduction` (создаётся в `fifo_deduct`, `apps/materials/fifo.py`).
- **FIFO:** `fifo_deduct(material_id, quantity_kg, reason, reference_id)` — порядок партий `received_at`, `created_at`, `id` ASC. Откат: `reverse_stock_deductions(reason, reference_id)`.

### 1.2. Химия (`apps/chemistry/`)

- **Справочник:** `ChemistryCatalog` — CRUD `ChemistryCatalogViewSet` (`apps/chemistry/views.py`). Состав на 1 кг готовой химии: `ChemistryRecipe` (создаётся вместе с каталогом или PATCH только с `recipe_lines` / `compositions` — логика в `ChemistryCatalogSerializer`, `apps/chemistry/serializers.py`).
- **Выпуск химии:** `POST /api/chemistry/elements/produce/` → `produce_chemistry()` (`apps/chemistry/produce.py`): проверка остатков сырья, затем `ChemistryBatch` с нулевой себестоимостью, затем цикл `fifo_deduct` с `reason='chemistry_batch_produce'`, `reference_id=batch.pk`, обновление `cost_total` / пересчёт `cost_per_unit` в `ChemistryBatch.save()` (`apps/chemistry/models.py`).
- **Остаток химии:** `ChemistryBatch.quantity_remaining`; уменьшается в `fifo_deduct_chemistry` (`apps/chemistry/fifo.py`).
- **Задания:** `ChemistryTask` / `POST /api/chemistry/tasks/{id}/confirm/` вызывает тот же `produce_chemistry` с `source_task_id` и помечает задание `status='done'`.

### 1.3. Профили и рецепты (`apps/recipes/`)

- **Профиль:** `PlasticProfile` — `PlasticProfileViewSet`.
- **Рецепт:** `Recipe` + строки `RecipeComponent` (нормы **на 1 м** в `quantity_per_meter`). Компоненты при create/update рецепта пишутся вручную в `RecipeViewSet.perform_create` / `perform_update` (`apps/recipes/views.py`), не через вложенный сериализатор. При сохранении компонента `unit` жёстко `'kg'` в `perform_create`/`perform_update`.
- **Устаревшие поля рецепта:** `output_quantity`, `output_unit_kind` в модели `Recipe` (`apps/recipes/models.py`) — используются в `_recipe_run_otk_quantity()` и `submit_recipe_run_to_otk()` для объёма партии при сценарии замеса (`apps/production/views.py`).

### 1.4. Производство и партия ОТК (`apps/production/`)

**Два входа в `ProductionBatch` с единым FIFO-расходом:**

1. **`POST /api/batches/`** — `BatchViewSet` + `ProductionBatchCreateUpdateSerializer.create()` → `apply_production_batch_stock_and_cost(batch)` (`apps/production/batch_stock.py`).
2. **`POST /api/production/recipe-runs/`** или `submit-to-otk` — `submit_recipe_run_to_otk()` создаёт `Order` + `ProductionBatch`, затем `apply_production_batch_stock_and_cost(batch)` (`apps/production/views.py`).

- **Выпуск в партии:** `ProductionBatch.pieces` × `length_per_piece` → `recompute_totals()` в `ProductionBatch.save()` задаёт `total_meters` и дублирует в `quantity` (legacy) (`apps/production/models.py`).
- **Себестоимость производства:** `batch.material_cost_total` = сумма FIFO сырья + FIFO химии; `cost_per_meter`, `cost_per_piece`, `cost_price` (= `material_cost_total`) пересчитываются в `ProductionBatch.save()`.
- **План замеса:** `RecipeRun`, `RecipeRunBatch`, `RecipeRunBatchComponent` — **не** выполняют списание в коде (комментарий в модели `RecipeRun`, `apps/production/models.py`; сериализатор `RecipeRunWriteSerializer` в `apps/production/serializers.py`).

### 1.5. Отправка в ОТК и приёмка

- **`POST /api/batches/{id}/submit-for-otk/`** (`BatchViewSet.submit_for_otk`): проверка `assert_production_batch_ready_for_otk_pipeline`, снимок смены `_apply_shift_snapshot_to_batch`, `lifecycle_status='otk'`, `in_otk_queue=True` (`apps/production/views.py`).
- **`POST /api/batches/{id}/otk_accept/`** (`BatchViewSet.otk_accept`): валидация суммы `otk_accepted` + `otk_defect` = **числу штук партии** (`batch.pieces`) с шагом квантизации `0.0001`; создание `OtkCheck`; обновление `ProductionBatch.otk_status`, `lifecycle_status='done'`; вызов `create_warehouse_batches_from_otk()` (`apps/warehouse/receipt.py`).

### 1.6. Склад ГП (`apps/warehouse/`)

- После ОТК: **до двух** строк `WarehouseBatch` — отдельно `quality='good'` и/или `quality='defect'` (`create_warehouse_batches_from_otk`).
- **Резерв:** `POST /api/warehouse/batches/reserve/` — только полная строка: `quantity` в теле должно **точно** равняться `batch.quantity`, статус → `reserved` (`WarehouseBatchViewSet.reserve`, `apps/warehouse/views.py`).
- **Упаковка:** `POST /api/warehouse/batches/package/` — только `inventory_form=unpacked`, `status=available`; списание штук с исходной строки, создание новой строки `packed` (`WarehouseBatchViewSet.package`).
- **Продажа:** `SaleSerializer.create()` → `apply_sale_to_warehouse_batch()` (`apps/warehouse/stock_ops.py`) — уменьшение `quantity` или логика вскрытия упаковки для `packed`.

### 1.7. Продажи (`apps/sales/`)

- `SaleViewSet` + `SaleSerializer`: расчёт `revenue`, `cost` (= `sold_pieces * warehouse_batch.cost_per_piece`), `profit`, синхронизация `quantity` с `sold_pieces` (`apps/sales/serializers.py`).
- Модель `Shipment` существует (`apps/sales/models.py`), **отдельного ViewSet в `config/api_urls.py` нет** — используется в админке и аналитике; при удалении продажи `Shipment` удаляются в `SaleViewSet.perform_destroy`.

### 1.8. Где создаётся «good» / «defect»

- **Good / defect на складе ГП:** поле `WarehouseBatch.quality` (`good` / `defect`) при создании из ОТК (`apps/warehouse/receipt.py`).
- **ОТК-запись:** `OtkCheck.check_status` — `accepted` если не «весь брак» (`rejected > 0 and accepted == 0` → `rejected`), иначе `accepted` (`apps/production/views.py`, `apps/otk/models.py`).

---

## 2. Модели (ключевые)

Пути: `apps/materials/models.py`, `apps/chemistry/models.py`, `apps/recipes/models.py`, `apps/production/models.py`, `apps/otk/models.py`, `apps/warehouse/models.py`, `apps/sales/models.py`.

### 2.1. `RawMaterial` (`apps/materials/models.py`)

| Поле | Тип / заметка |
|------|----------------|
| `name` | обязательное в сериализаторе |
| `unit` | в API канон `kg`/`g` |
| `min_balance` | Decimal, null |
| `is_active`, `comment` | |

Связи: `MaterialBatch.material` → PROTECT; обратное `batches`.

### 2.2. `MaterialBatch`

| Поле | Заметка |
|------|---------|
| `quantity_initial`, `quantity_remaining` | хранение в **кг** после прихода через API |
| `unit` | в коде прихода выставляется `'kg'` при создании из сериализатора |
| `unit_price`, `total_price` | `save()` пересчитывает `total_price` = initial × price, quantize `0.01` |

### 2.3. `MaterialStockDeduction`

| Поле | Назначение |
|------|------------|
| `batch`, `quantity`, `unit_price`, `line_total` | снимок цены |
| `reason`, `reference_id` | связь для отката (`reverse_stock_deductions`) |

### 2.4. `ChemistryCatalog`, `ChemistryRecipe`, `ChemistryBatch`, `ChemistryStockDeduction`

- `ChemistryRecipe.quantity_per_unit` — «на 1 кг химии» (модель).
- `ChemistryBatch`: `quantity_produced`, `quantity_remaining`, `cost_total`, `cost_per_unit` (пересчёт в `save()`).
- `ChemistryStockDeduction` — аналогично сырью, откат `reverse_chemistry_deductions`.
- `ChemistryTask`, `ChemistryTaskElement` — задания (элементы в модели есть; отдельный CRUD элементов в `api_urls` не зарегистрирован — только `ChemistryTaskViewSet`).

### 2.5. `PlasticProfile`

`code` unique; удаление профиля блокируется при рецептах/партиях (`plastic_profile_deletable`, `apps/recipes/views.py`).

### 2.6. `Recipe`, `RecipeComponent`

- `Recipe`: `recipe`, `profile`, `product` (денормализация из профиля в `save()`), `base_unit` (сейчас только `per_meter`), **legacy:** `output_quantity`, `output_unit_kind`.
- `RecipeComponent`: `type` (`raw`/`chem`), FK на сырьё или химию, `quantity_per_meter`, `unit` (по умолчанию `'кг'` в модели).

### 2.7. `ProductionBatch` (`apps/production/models.py`)

Важные поля:

- Связи: `order`, `profile`, `recipe`, `line`, `shift`.
- Выпуск: `pieces` (PositiveInteger), `length_per_piece`, `total_meters`, **`quantity` = legacy копия `total_meters`**.
- ОТК: `otk_status` (`pending`/`accepted`/`rejected`), `lifecycle_status` (`pending`/`otk`/`done`), `sent_to_otk`, `in_otk_queue`, `otk_submitted_at`.
- Себестоимость: `cost_price` (**legacy**, в `save` = `material_cost_total`), `material_cost_total`, `cost_per_meter`, `cost_per_piece`.
- Снимок смены: `shift_height`, `shift_width`, `shift_angle_deg`, `shift_opener_name`, `shift_opened_at`.

### 2.8. `OtkCheck`

- `pieces`, `length_per_piece`, `total_meters` — копия с партии на момент проверки.
- **`accepted`, `rejected`** — Decimal, в коде комментарий «legacy»; фактически заполняются из `otk_accept` / `otk_defect` тел запроса.
- `check_status`, `reject_reason`, `inspector`, `inspector_name`.

### 2.9. `WarehouseBatch` (`apps/warehouse/models.py`)

- `status`: `available` / `reserved` / `shipped`.
- `inventory_form`: `unpacked` / `packed` / `open_package`.
- **`quality`:** `good` / `defect`; `defect_reason` очищается в `save()` для good.
- `quantity` — **«штук доступно»** в комментарии модели; для unpacked после ОТК = штуки принятые/брак.
- Упаковка: `unit_meters`, `package_total_meters`, `pieces_per_package`, `packages_count`.
- Снимок ОТК: `otk_accepted`, `otk_defect`, …
- `save()` пересчитывает `total_meters` из `quantity * length_per_piece` при наличии длины.

### 2.10. `Sale`

- `sale_mode`: `pieces` / `packages`.
- `sold_pieces`, `sold_packages`, `quantity` (**legacy = sold_pieces**), `quantity_input` (упаковки), `price`, `revenue`, `cost`, `profit`.
- `warehouse_batch` optional; `stock_form`, `piece_pick`, `stock_quality`, `packaging`, `sale_unit`.

### 2.11. `Line`, `Shift`, `LineHistory`, `RecipeRun`, …

- `Shift`: уникальные ограничения на одну открытую личную смену на пользователя и одну открытую смену на пару user+line (`apps/production/models.py`).
- `LineHistory` — журнал открытия/закрытия/параметров/паузы.
- `Order` — заказ на производство, связь с рецептом/линией; создаётся при `submit_recipe_run_to_otk`.

### 2.12. Резерв / упаковка / брак (сводка по моделям)

- Резерв: только поле `WarehouseBatch.status`, отдельной таблицы резерва нет.
- Упаковка: поля на `WarehouseBatch`; отдельной сущности «упаковка» нет.
- Брак: `WarehouseBatch.quality=defect` + `OtkCheck` при полном браке / смешанном исходе.

### 2.13. Полный перечень полей по моделям (как в `models.py`)

**`RawMaterial`** (`apps/materials/models.py`): `id`, `name`, `unit`, `min_balance` (null), `is_active`, `comment`.

**`MaterialBatch`:** `id`, `material_id` (FK), `quantity_initial`, `quantity_remaining`, `unit`, `unit_price`, `total_price`, `supplier_name`, `supplier_batch_number`, `comment`, `received_at`, `created_at`.

**`MaterialStockDeduction`:** `id`, `batch_id`, `quantity`, `unit_price`, `line_total`, `reason`, `reference_id` (null), `created_at`.

**`ChemistryCatalog`:** `id`, `name`, `unit`, `min_balance` (null), `is_active`, `comment`.

**`ChemistryRecipe`:** `id`, `chemistry_id`, `raw_material_id`, `quantity_per_unit`; `Meta.unique_together` (`chemistry`, `raw_material`).

**`ChemistryBatch`:** `id`, `chemistry_id`, `quantity_produced`, `quantity_remaining`, `cost_total`, `cost_per_unit`, `created_at`, `produced_by_id` (null), `comment`, `source_task_id` (null).

**`ChemistryStockDeduction`:** `id`, `batch_id`, `quantity`, `unit_price`, `line_total`, `reason`, `reference_id` (null), `created_at`.

**`ChemistryTask`:** `id`, `name`, `status` (`pending`/`in_progress`/`done`), `deadline` (null), `chemistry_id`, `quantity`, `unit`, `created_at`.

**`ChemistryTaskElement`:** `id`, `task_id`, `chemistry_id`, `quantity`, `unit` — в роутере API отдельного ресурса под элементы в `config/api_urls.py` нет.

**`PlasticProfile`:** `id`, `name`, `code`, `comment`, `is_active`; constraint unique на `code`.

**`Recipe`:** `id`, `recipe`, `profile_id`, `product`, `base_unit`, `output_quantity` (null), `output_unit_kind` (null), `comment`, `is_active`.

**`RecipeComponent`:** `id`, `recipe_id`, `type`, `raw_material_id` (null), `chemistry_id` (null), `quantity_per_meter`, `unit`.

**`Line`:** `id`, `name`, `code`, `notes`, `is_active`.

**`LineHistory`:** `id`, `line_id` (null), `line_name_snapshot`, `former_line_id` (null), `action`, `date`, `time`, `user_id` (null), `height`, `width`, `angle_deg` (null), `comment`, `session_title`.

**`Order`:** `id`, `status`, `recipe_id` (null), `recipe_name_snapshot`, `former_recipe_id` (null), `line_id` (null), `line_name_snapshot`, `former_line_id` (null), `quantity`, `product`, `operator_id` (null), `date`.

**`ProductionBatch`:** `id`, `order_id` (null), `profile_id` (null), `recipe_id` (null), `line_id` (null), `shift_id` (null), `product`, `pieces`, `length_per_piece`, `total_meters`, `quantity`, `operator_id` (null), `date`, `produced_at` (null), `comment`, `otk_status`, `lifecycle_status`, `sent_to_otk`, `in_otk_queue`, `otk_submitted_at` (null), `cost_price`, `material_cost_total`, `cost_per_meter`, `cost_per_piece`, `shift_height`, `shift_width`, `shift_angle_deg`, `shift_opener_name`, `shift_opened_at` (null).

**`Shift`:** `id`, `line_id` (null), `line_name_snapshot`, `former_line_id` (null), `user_id` (null), `opened_at`, `closed_at` (null), `status`, `comment`; constraints `uniq_shift_personal_open_per_user`, `uniq_shift_user_line_open_per_user_line` (условия на открытые смены).

**`ShiftComplaint`:** `id`, `body`, `author_id`, `shift_id` (null), `created_at` + M2M `mentioned_users`.

**`ShiftNote`:** `id`, `shift_id`, `user_id` (null), `text`, `created_at`.

**`RecipeRun`:** `id`, `recipe_id` (null), `recipe_name_snapshot`, `former_recipe_id` (null), `line_id` (null), `line_name_snapshot`, `former_line_id` (null), `created_at`, `production_batch_id` (null, OneToOne обратно к партии), `recipe_run_consumption_applied` (bool, помечено устаревшим в docstring модели).

**`RecipeRunBatch`:** `id`, `run_id`, `index`, `label`, `quantity` (null).

**`RecipeRunBatchComponent`:** `id`, `batch_id`, `recipe_component_id` (null), `raw_material_id` (null), `chemistry_id` (null), `quantity`, `unit`, `material_name_snapshot`, `chemistry_name_snapshot`.

**`OtkCheck`:** `id`, `batch_id`, `profile_id` (null), `pieces`, `length_per_piece`, `total_meters`, `accepted`, `rejected`, `check_status`, `reject_reason`, `comment`, `inspector_id` (null), `inspector_name`, `checked_date`.

**`WarehouseBatch`:** `id`, `profile_id` (null), `product`, `length_per_piece` (null), `total_meters` (null), `quantity`, `cost_per_piece`, `cost_per_meter`, `status`, `date`, `source_batch_id` (null), `inventory_form`, `unit_meters`, `package_total_meters`, `pieces_per_package`, `packages_count`, `otk_accepted`, `otk_defect`, `otk_defect_reason`, `otk_comment`, `otk_inspector_name`, `otk_checked_at` (null), `otk_status`, `quality`, `defect_reason`.

**`Client`:** `id`, `name`, `contact`, `phone`, `phone_alt`, `inn`, `address`, `client_type`, `notes`, `email`, `messenger`, `is_active`.

**`Sale`:** `id`, `order_number`, `client_id` (null), `warehouse_batch_id` (null), `product`, `sale_mode`, `sold_pieces`, `sold_packages`, `length_per_piece` (null), `total_meters`, `quantity`, `quantity_input` (null), `price` (null), `revenue`, `cost`, `date`, `comment`, `profit`, `sale_unit`, `packaging`, `stock_form`, `piece_pick`, `stock_quality`.

**`Shipment`:** `id`, `sale_id`, `quantity`, `status`, `shipment_date` (null), `delivery_date` (null), `address`, `comment` — REST CRUD в `config/api_urls.py` не зарегистрирован.

**`apps/analytics/models.py`:** только комментарий, что моделей аналитики нет; агрегаты в `apps/analytics/views.py`.

### 2.14. Состояние смены на линии (вспомогательные функции)

Файл `apps/production/shift_state.py` (используется из `LineSerializer`, `ProductionBatchCreateUpdateSerializer`, `RecipeRunWriteSerializer`, `BatchViewSet`, `submit_recipe_run_to_otk`): функции `line_shift_is_open`, `line_shift_is_paused`, `line_shift_pause_reason`, `line_current_shift_open_event`, `line_current_shift_params_event`, `prefetch_line_histories_map`, `line_history_audit_shift_context`, `shift_instance_audit_context` — определение открытой смены идёт по последним записям `LineHistory`, не только по модели `Shift`.

---

## 3. Ключевые сервисы и функции

| Процесс | Функция / класс | Файл | Что меняет |
|--------|------------------|------|------------|
| FIFO сырья | `fifo_deduct`, `reverse_stock_deductions`, `material_stock_kg`, `simulate_fifo_cost_kg` | `apps/materials/fifo.py` | `MaterialBatch.quantity_remaining`, строки `MaterialStockDeduction` |
| FIFO химии | `fifo_deduct_chemistry`, `reverse_chemistry_deductions`, `chemistry_stock_kg`, `simulate_chemistry_fifo_cost_kg` | `apps/chemistry/fifo.py` | `ChemistryBatch.quantity_remaining`, `ChemistryStockDeduction` |
| Выпуск химии | `produce_chemistry` | `apps/chemistry/produce.py` | сырьё (как выше), новый `ChemistryBatch` |
| Агрегат расхода по рецепту | `aggregate_consumption_for_recipe(recipe, total_meters)` | `apps/production/batch_stock.py` | только расчёт dict |
| Списание под партию производства | `apply_production_batch_stock_and_cost(batch)` | `apps/production/batch_stock.py` | дедукции сырья/химии, `batch.material_cost_total` + `batch.save(update_fields=…)` |
| Откат и пересчёт | `reverse_production_batch_stock`, `resync_production_batch_consumption` | `apps/production/batch_stock.py` | откат по `reason='production_batch'`, повторное списание |
| Готовность к ОТК | `assert_production_batch_ready_for_otk_pipeline`, `production_batch_has_positive_material_requirement` | `apps/production/batch_stock.py` | не меняет данные, кидает `ValidationError` |
| Плановая себестоимость | `estimate_recipe_material_cost`, `chemistry_estimated_kg_price`, `estimate_chemistry_only_recipe_cost` | `apps/production/costing.py` | только расчёт |
| Создание партии из замеса | `submit_recipe_run_to_otk` | `apps/production/views.py` | `Order`, `ProductionBatch`, связь `RecipeRun.production_batch`, `apply_production_batch_stock_and_cost` |
| ОТК поступление на склад | `create_warehouse_batches_from_otk` | `apps/warehouse/receipt.py` | 0–2 `WarehouseBatch` |
| Упаковка (план FIFO по нескольким строкам) | `plan_fifo_pack` | `apps/warehouse/packaging.py` | в текущем API **не вызывается** напрямую из views (используется разбор остатков в сериализаторе) |
| Разбор упаковки для JSON | `warehouse_packaging_breakdown` | `apps/warehouse/packaging.py` | только вычисления |
| Продажа со склада | `apply_sale_to_warehouse_batch` | `apps/warehouse/stock_ops.py` | `WarehouseBatch.quantity`, `status`, `inventory_form`, возможное разделение строк |
| Проверки удаления сырья | `raw_material_is_deletable`, `raw_material_unit_change_denial` | `apps/materials/usage_checks.py` | read-only |

Константа причины списания производства: `PRODUCTION_BATCH_REASON = 'production_batch'` в `apps/production/batch_stock.py`.

---

## 4. API (важные endpoints)

Общие правила: пагинация там, где указан `pagination_class` в ViewSet; права через `config.permissions` (`IsAdminOrHasAccess`, `IsAdminOrHasProductionOrOtk`, …) и `required_access_key` на наборах.

### 4.1. Сырьё

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST/PATCH/DELETE | `/api/raw-materials/` | справочник |
| GET/POST | `/api/incoming/` | приходы (`IncomingViewSet`, без PUT/PATCH/DELETE) |
| GET | `/api/materials/balances/` | остатки по справочнику (`MaterialsBalancesView.list`) |
| GET | `/api/materials/movements/` | синтетический журнал incoming + writeoff (`_build_movement_items`) |

**POST `/api/incoming/`** — обязательные в сериализаторе: `material_id`, `quantity`, `unit_price`, `received_at`. Read-only в ответе: `quantity_initial`, `quantity_remaining` (но в `to_representation` `quantity` заменено на float в единицах справочника), `total_price`, `created_at`, `unit`.

### 4.2. Химия

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/chemistry/elements/` | каталог |
| POST | `/api/chemistry/elements/produce/` | выпуск (`ChemistryCatalogViewSet.produce`) |
| CRUD | `/api/chemistry/tasks/` | задания |
| POST | `/api/chemistry/tasks/{id}/confirm/` | выпуск по заданию |
| GET | `/api/chemistry/balances/` | остатки |
| GET | `/api/chemistry/batches/` | история партий |

**Тело POST produce:** `ChemistryProduceSerializer` — `chemistry_id`, `quantity` (>0), `comment?`. Ответ: `ChemistryBatchSerializer` (количества как **float** в единицах карточки в `to_representation`).

### 4.3. Профили и рецепты

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/plastic-profiles/` | профили |
| CRUD | `/api/recipes/` | рецепты |
| GET | `/api/recipes/{id}/availability/` | проверка «на 1 м»: сравнение `float(quantity_per_meter)` с `float(stock_kg)` |

**POST/PATCH `/api/recipes/`:** поля карточки через `RecipeSerializer`; массив `components` обрабатывается вручную во view — см. `_normalize_component` (алиасы `material_id` / `raw_material_id`, `quantity` → `quantity_per_meter`). **Read-only в сериализаторе для components при чтении карточки** — запись только через тело запроса во view.

### 4.4. Партии производства (`BatchViewSet`)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST | `/api/batches/` | список / создание |
| GET/PATCH | `/api/batches/{id}/` | деталь / правка (ограничения в сериализаторе) |
| POST | `/api/batches/{id}/submit-for-otk/` | очередь ОТК |
| POST | `/api/batches/{id}/otk_accept/` | приёмка ОТК + склад |

**POST create:** поля см. `_ALLOWED_BATCH_CREATE_FIELDS` в `ProductionBatchCreateUpdateSerializer` (`profile`, `recipe`, `line`, `pieces`, `length_per_piece`, `comment`, `date`, `produced_at`, `product`). Лишние ключи на create → 400. `total_meters` read-only. После create вызывается `apply_production_batch_stock_and_cost`.

**PATCH после отправки в ОТК:** разрешено только `comment` (сериализатор).

**POST otk_accept — тело:** обязательны `otk_accepted` и `otk_defect` (или алиасы `accepted` / `rejected`); опционально `otk_defect_reason` / `rejectReason`, `otk_comment`, `otk_inspector`, `otk_inspector_name`, `otk_inspector_id`, `otk_checked_at`. Ответ: `BatchListSerializer`.

### 4.5. Замесы (`RecipeRunViewSet`)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST | `/api/production/recipe-runs/` | список / создать замес + партию + FIFO |
| GET/PATCH/DELETE | `/api/production/recipe-runs/{id}/` | деталь / обновление плана / удаление с откатом |
| POST | `/api/production/recipe-runs/{id}/submit-to-otk/` | то же, что внутренняя логика submit |

Ответ `submit-to-otk`: `{ production_batch: BatchListSerializer, recipe_run: RecipeRunDetailSerializer, already_submitted?: true }`.

### 4.6. ОТК

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/otk/pending/` | `{ items: BatchListSerializer[] }` |

Приёмка — только `POST /api/batches/{id}/otk_accept/`.

### 4.7. Склад ГП

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/warehouse/batches/` | список (`WarehouseBatchFilter`: `status`, `product`, `quality`, алиасы `stock_form`, `packaging_status`, `inventory_form`) |
| POST | `/api/warehouse/batches/reserve/` | полный резерв строки |
| POST | `/api/warehouse/batches/package/` | упаковка из unpacked |

Алиасы путей упаковки без `/api/`: см. `config/urls.py`.

**POST reserve:** `batch_id` или `batchId`, `quantity` (должно равняться полному остатку), опционально `sale_id` (только аудит).

**POST package:** `warehouse_batch_id` или `batchId`, `pieces_per_package`, `packages_count` (int ≥1), `comment?`.

### 4.8. Продажи и клиенты

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/sales/` | продажи |
| CRUD | `/api/clients/` | клиенты |
| GET | `/api/sales/{id}/nakladnaya/`, `waybill`, `invoice` | один и тот же HTML-черновик |

`SaleSerializer`: при наличии `warehouse_batch` queryset для выбора только `status=available`. Read-only: `profit`, `revenue`, `cost`, `cost_total` (дубль `cost`), `total_meters`, `inventory_form`, `quantity_unit`, `warehouse_batch_id`, `profile_name`, `sale_date`, `stock_quality`.

### 4.9. Линии и смены

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/lines/` | линии + actions `open`, `close`, `shift-params`, `shift-pause`, `shift-resume`, `history`, `history/session` |
| GET/POST | `/api/shifts/` | список; `open`/`close` actions |
| GET | `/api/shifts/my/` | открытая личная смена |
| GET/POST | `/api/shifts/notes/` | заметки к личной смене |
| GET | `/api/shifts/history/` | история смен пользователя |
| GET/POST | `/api/shifts/complaints/` | жалобы |

### 4.10. Аналитика

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/analytics/summary/` | крупный JSON (см. `AnalyticsSummaryView.list`, `apps/analytics/views.py`) |
| GET | `/api/analytics/revenue-details/` | обязателен `year` query |
| GET | `/api/analytics/expense-details/` | обязателен `year` |
| GET | `/api/analytics/writeoff-details/` | обязателен `year` |

### 4.11. Query-параметры фильтрации (фактически в ViewSet)

| Ресурс | Параметры |
|--------|-----------|
| `GET /api/raw-materials/` | `unit`, `is_active`, поиск `search` по `name` |
| `GET /api/incoming/` | `material_id` / `material`, диапазон `received_at_after`, `received_at_before` (`DateFromToRangeFilter`, `MaterialBatchFilter`) |
| `GET /api/chemistry/elements/` | `unit`, `is_active` |
| `GET /api/chemistry/tasks/` | `status`, `chemistry` |
| `GET /api/chemistry/batches/` | `chemistry`, поиск по `comment` |
| `GET /api/plastic-profiles/` | `is_active` |
| `GET /api/recipes/` | `is_active`, `profile`, `profile_id` |
| `GET /api/batches/` | `otk_status`, `order`, `line`, `profile`, `lifecycle_status` |
| `GET /api/production/recipe-runs/` | `recipe`, `line` |
| `GET /api/warehouse/batches/` | `WarehouseBatchFilter`: `status`, `product`, `quality`, `inventory_form`, алиасы `stock_form`, `packaging_status`; `?debug=1` отключает скрытие тестовых продуктов (`views.py`) |
| `GET /api/clients/` | `is_active` |
| `GET /api/sales/` | `client`, `client_id` (оба ведут на client) |
| `GET /api/lines/` | `eligible_for_recipe_run` / `eligible_for_production_batch` = `1|true|yes` — фильтр по открытой непаузной смене (`LineViewSet.list`) |
| `GET /api/shifts/` | `date_from`, `date_to`, `line`, `user` (строки парсятся вручную) |
| `GET /api/shifts/complaints/` | `date`, `date_from`, `date_to`, `author_id`/`author`, `mentioned_user_id`/`mentioned_user` |
| `GET /api/users/` | `role`, `is_active` |

Аналитика summary: `parse_analytics_scope` — `year`, `month`, `day`, `date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `status` (`apps/analytics/services.py`).

---

## 5. Валидации и ограничения (где в коде)

| Тема | Где |
|------|-----|
| Запрет двойного списания производства | Повторный `apply` после `reverse` в `resync_production_batch_consumption`; идемпотентность create партии по заголовкам `X-Request-Id` / `Idempotency-Key` в `BatchViewSet.create` |
| Запрет менять партию после привязки к замесу | `ProductionBatchCreateUpdateSerializer.update` |
| ОТК: сумма принято+брак = штуки партии | `BatchViewSet.otk_accept`, шаг `0.0001` |
| ОТК: при браке нужна причина | `otk_defect > 0` → обязателен `defect_reason` |
| Смешивание партий на упаковку | Не применимо в `package` — одна строка; `plan_fifo_pack` проверяет одинаковость «ключа» размеров |
| Резерв только полной строки | `WarehouseBatchViewSet.reserve` |
| Упаковка только unpacked + available | `WarehouseBatchViewSet.package` |
| Продажа: складской статус | `apply_sale_to_warehouse_batch` — только `available` |
| Продажа: согласованность packed | Проверки `quantity` vs `packages_count * pieces_per_package` в `stock_ops` |
| Decimal / float | См. раздел 7 |

---

## 6. Проблемные места (факты из кода)

- **Дубли полей / имён:** `BatchListSerializer`: `quantity` и `released` (оба из `total_meters`); `recipe_name` и `recipe_label`; `line_name` и `line_label`; `ClientSerializer`: `contact` / `contact_person`, `messenger` / `whatsapp_telegram`, `notes` / `comment`; `WarehouseBatchSerializer`: `inventory_form` / `stock_form`, множественные алиасы счётчиков упаковок (`pieces_in_package`, `pack_count`, …); `ChemistryBatchSerializer`: `cost_total`/`total_cost`, `cost_per_unit`/`unit_cost`.
- **Дубли логики смены:** открытие/закрытие смены на линии доступно и через `/api/lines/{id}/open|close/`, и через `/api/shifts/open/` с `line_id` (документировано во view).
- **Legacy:** `ProductionBatch.quantity`, `Recipe.output_quantity` / `output_unit_kind`, `Sale.quantity`, `OtkCheck.accepted`/`rejected`, поле `RecipeRun.recipe_run_consumption_applied` помечено неиспользуемым в модели.
- **Два входа в одну бизнес-операцию FIFO:** прямой `POST /api/batches/` и цепочка `recipe-runs` — оба вызывают `apply_production_batch_stock_and_cost`.
- **Замес не влияет на склад:** `RecipeRunBatchComponent` не связан с `fifo_deduct` — только UI/план.
- **`GET /api/recipes/{id}/availability/`:** сравнивает остаток **всего** склада в кг с **нормой на 1 м** (`quantity_per_meter`), без учёта запланированного выпуска партии; числа приводятся к `float`, добавка `1e-9` для сравнения.
- **Аналитика `finances.cost_price`:** `ProductionBatch.objects.aggregate(cost=Sum('material_cost_total'))` — ключ агрегата `cost`, переменная `total_cost_price` (`apps/analytics/views.py`); в JSON отдаётся как `cost_price` внутри `finances`.
- **Аналитика `chemistry.stock_quantity_sum`:** сумма `quantity_remaining` по **всем** `ChemistryBatch` без фильтра периода (`apps/analytics/views.py`).
- **Аналитика низкого остатка сырья:** если `min_balance` не задан, порог **константа 50** в сравнении с `balance` как float (`apps/analytics/views.py`).
- **Ошибка в `perform_destroy` recipe-run:** `Order.objects.filter(pk=order_pk).first().batches` — при отсутствии заказа возможен `AttributeError` (код присутствует в `apps/production/views.py`).
- **Сериализатор линии:** `LineSerializer.to_representation` добавляет поле `comment` из `notes` модели `Line` (`notes` в Meta.fields нет — подмена имени поля для клиента).
- **История линии session:** в ответе дубли `pause_resume` и `pauseResume` с одинаковыми данными (`apps/production/views.py`).
- **Смешение RU/EN:** статусы моделей на английском (`pending`, `available`, …); часть сообщений об ошибках на русском; `piece_pick` константы на английском (`loose_remainder`, …); фильтры склада принимают `not_packed` и т.д.

---

## 7. Числа и форматы

### 7.1. Хранение Decimal в БД

- Финансы партий сырья: `line_total`, `unit_price` quantize `0.01` в `fifo_deduct` (сырьё и химия).
- `MaterialBatch.save`: `total_price` = `quantity_initial * unit_price` quantize `0.01`.
- `ProductionBatch.save`: `total_meters` = `pieces * length_per_piece` quantize `0.0001`; `cost_per_meter`, `cost_per_piece` quantize `0.0001`.
- `aggregate_consumption_for_recipe`: потребность quantize `0.0001` (`apps/production/batch_stock.py`).

### 7.2. Приведение к int

- `ProductionBatch.recompute_totals`: `pieces = int(self.pieces or 0)`.
- Упаковка API: `pieces_per_package`, `packages_count` через `int(Decimal(...))` (`apps/warehouse/views.py`).
- `OtkCheck.objects.create`: `pieces=int(batch.pieces)`.

### 7.3. float в API / отчётах

- `BatchListSerializer`: `otk_accepted`, `otk_defect`, `height`, `width`, `angle_deg` как `float`.
- `MaterialsBalancesView`: `balance` как float (для `g` — перевод из кг).
- `RecipeViewSet.availability`: `float` для сравнения.
- `AnalyticsSummaryView`: множество показателей как `float(...)`.
- `WarehouseBatchSerializer.get_available_quantity`: `float(obj.quantity)`.
- `ChemistryBatchSerializer.to_representation`: количества как float.

### 7.4. Строковые Decimal (CleanDecimalField)

- `RecipeComponentSerializer.quantity_per_meter`, поля замеса — `coerce_to_string=True` с нормализацией через `format_decimal_plain` (`config/fields.py`, `config/decimal_format.py`).

### 7.5. Нули и минимумы

- `RecipeRunBatchComponentInputSerializer`: `quantity` min `0.0001` — ноль нельзя.
- `produce_chemistry`: `qty_in <= 0` запрещено.
- `MaterialBatchSerializer`: quantity incoming > 0.
- `ProductionBatchCreateUpdateSerializer`: `pieces` > 0, `length_per_piece` > 0.
- `ChemistryRecipe` в составе: допускается `quantity_per_unit >= 0` в PATCH каталога; нулевые строки могут давать нулевой расход (отдельная проверка при produce на «все need <= 0»).

### 7.6. Потеря точности

- Любое использование `float()` для денег/остатков в сериализаторах и аналитике — см. перечисление выше.

---

## 8. Что реально отдаётся фронту (кратко по разделам)

| Раздел | Endpoint | Форма объекта |
|--------|----------|----------------|
| Сырьё список | GET `/api/raw-materials/` | модель через `RawMaterialSerializer` |
| Приходы | GET `/api/incoming/` | `MaterialBatchSerializer` (quantity как float в единице справочника) |
| Балансы материалов | GET `/api/materials/balances/` | `{ items: [{ material_id, id, name, balance, … }] }` — дубли id/material_id, name/material_name |
| Химия каталог list | GET `/api/chemistry/elements/` | list serializer + annotate |
| Партии производства | GET `/api/batches/` | `BatchListSerializer` — дубли имён/единиц выпуска |
| ОТК очередь | GET `/api/otk/pending/` | `{ items: [...] }` |
| Склад | GET `/api/warehouse/batches/` | `WarehouseBatchSerializer` — много вычисляемых и алиасов упаковки |
| Продажи | GET `/api/sales/` | `SaleSerializer`; `quantity_input` скрывается в representation если не режим пакетов |
| Аналитика | GET `/api/analytics/summary/` | вложенный объект с дублирующими ключами в `finances` и `trends` |

---

## 9. Итоговые выводы (для аудита)

### 9.1. Что в бэкенде выглядит правильно

- Централизованное FIFO-списание сырья и химии для партии производства в одном месте (`apply_production_batch_stock_and_cost` + откат по `reason`/`reference_id`).
- Явное разделение строк склада ГП на `good` и `defect` при приёмке ОТК.
- Жёсткая проверка резерва «только вся строка» — снижает риск частичных неконсистентных состояний без отдельной таблицы резерва.
- Упаковка со строки `unpacked` с проверкой достаточности штук и переносом полей ОТК на новую строку.

### 9.2. Что в бэкенде выглядит спорно

- Два канала создания `ProductionBatch` с одной и той же складской логикой.
- `recipe/availability` сравнивает склад в кг с нормой «на метр» без явного сценария объёма выпуска.
- Наличие `float` в ответах для денег/количеств при внутренней модели на `Decimal`.
- Идемпотентность только для `POST /api/batches/` по заголовку, не для остальных операций.

### 9.3. Что в бэкенде выглядит переусложнённо

- Сериализаторы замеса и списка партий: множество `SerializerMethodField` с fallback на снимки и заказ.
- `WarehouseBatchSerializer.to_representation`: ветвление по форме учёта + множество алиасов имён полей упаковки.
- `AnalyticsSummaryView.list`: один метод собирает десятки несвязанных метрик и дубли ключей для совместимости.

### 9.4. Что в бэкенде точно может ломать UX/UI

- Разные имена одного и того же (`batch_id`/`batchId`, `warehouse_batch_id`/`batchId`, `recipe_name`/`recipe_label`, `stock_form`/`inventory_form`/`packaging_status`).
- Смешение типов числа в JSON (int vs float для штук в упаковке в `warehouse_packaging_breakdown._api_piece_number`).
- `GET …/availability/` и строки с `ok` на float — граничные значения могут отображаться «достаточно» при микроскопических расхождениях.
- Ошибка потенциальная при `DELETE` recipe-run при несогласованном `order_id` (см. раздел 6).

### 9.5. Что надо будет отдельно чинить после аудита

- Согласование контракта API: единые имена полей, единый тип чисел (строка Decimal vs number) для финансов и объёмов.
- Проверка и исправление `perform_destroy` у `RecipeRunViewSet` при удалении заказа.
- Ревизия аналитики: фильтрация химического «остатка в сумме», смысл `profit_simple`, соответствие `cost_price` в сводке полю `material_cost_total`.
- Ревизия `recipe availability` под реальный сценарий (метры выпуска × норма).
- Документирование/реализация API для `Shipment`, если бизнес ожидает отгрузки через REST (сейчас в коде не найдено зарегистрированного endpoint для CRUD отгрузок).

---

*Конец файла BACKEND_AUDIT_DOC_V3.md*
