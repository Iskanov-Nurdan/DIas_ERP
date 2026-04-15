# Общая архитектура бэкенда

Префикс HTTP API: `/api/` (см. `config/urls.py`, `include('config.api_urls')`). Аутентификация: JWT (`POST /api/auth/login`, `GET /api/me`, `POST /api/auth/logout` в `config/urls.py`).

## Основные доменные сущности

| Сущность | Модель | Файл |
|----------|--------|------|
| Сырьё (справочник) | `RawMaterial` | `apps/materials/models.py` |
| Партия прихода сырья | `MaterialBatch` | `apps/materials/models.py` |
| Списание сырья (FIFO-строки) | `MaterialStockDeduction` | `apps/materials/models.py` |
| Химия (справочник) | `ChemistryCatalog` | `apps/chemistry/models.py` |
| Состав химии (нормы на 1 кг) | `ChemistryRecipe` | `apps/chemistry/models.py` |
| Партия выпущенной химии | `ChemistryBatch` | `apps/chemistry/models.py` |
| Списание химии (FIFO) | `ChemistryStockDeduction` | `apps/chemistry/models.py` |
| Профиль ГП | `PlasticProfile` | `apps/recipes/models.py` |
| Рецепт (нормы на 1 м) | `Recipe`, `RecipeComponent` | `apps/recipes/models.py` |
| Линия | `Line`, история `LineHistory` | `apps/production/models.py` |
| Смена | `Shift`, заметки `ShiftNote`, жалобы `ShiftComplaint` | `apps/production/models.py` |
| Заказ производства | `Order` | `apps/production/models.py` |
| Партия производства (ОТК) | `ProductionBatch` | `apps/production/models.py` |
| Замес | `RecipeRun`, `RecipeRunBatch`, `RecipeRunBatchComponent` | `apps/production/models.py` |
| Проверка ОТК | `OtkCheck` | `apps/otk/models.py` |
| Склад ГП | `WarehouseBatch` | `apps/warehouse/models.py` |
| Клиент | `Client` | `apps/sales/models.py` |
| Продажа | `Sale`, `Shipment` | `apps/sales/models.py` |
| Пользователь и права | `User`, `UserAccess`, `Role`, `RoleAccess` | `apps/accounts/models.py` |

## Связи модулей

- `Recipe` → `PlasticProfile` (FK `profile`). `RecipeComponent` → `RawMaterial` и/или `ChemistryCatalog`.
- `ChemistryRecipe` → `ChemistryCatalog` + `RawMaterial`.
- `ProductionBatch` → `Order`, `PlasticProfile`, `Recipe`, `Line`, `Shift` (опционально).
- `RecipeRun` → `Recipe`, `Line`, опционально `OneToOne` → `ProductionBatch` (`source_recipe_run`).
- `OtkCheck` → `ProductionBatch` (CASCADE), опционально `PlasticProfile`.
- `WarehouseBatch` → `ProductionBatch` как `source_batch` (SET_NULL).
- `Sale` → `Client` (nullable), `WarehouseBatch` (nullable).

## Фактическая цепочка движения данных и остатков

1. **Приход сырья:** `POST /api/incoming/` → `MaterialBatch` (`quantity_initial` / `quantity_remaining` в **кг** в БД, ввод в единицах справочника) → остаток сырья = сумма `quantity_remaining` по партиям (`apps/materials/fifo.py`: `material_stock_kg`).
2. **Выпуск химии:** `produce_chemistry` (`apps/chemistry/produce.py`) → FIFO сырья (`fifo_deduct`, `reason='chemistry_batch_produce'`, `reference_id=ChemistryBatch.pk`) → `ChemistryBatch` с `cost_total` / `cost_per_unit`.
3. **Производство (партия через `/api/batches/`):** `ProductionBatchCreateUpdateSerializer.create` → `apply_production_batch_stock_and_cost` (`apps/production/batch_stock.py`) → агрегат расхода по `RecipeComponent.quantity_per_meter × total_meters` → `fifo_deduct` сырья и `fifo_deduct_chemistry` химии с `reason='production_batch'`, `reference_id=ProductionBatch.pk` → поля `material_cost_total`, пересчёт `cost_per_meter` / `cost_per_piece` в `ProductionBatch.save`.
4. **Замес (`RecipeRun`):** создаётся `RecipeRun` + ёмкости/строки `RecipeRunBatch` / `RecipeRunBatchComponent` (**без** вызова `apply_production_batch_stock_and_cost` в `submit_recipe_run_to_otk`, см. `apps/production/views.py`). Связанная `ProductionBatch` создаётся с `material_cost_total=0` до отдельного сценария списания (в коде списание для партии — только через п.3).
5. **ОТК:** `POST /api/batches/{id}/submit-for-otk/` меняет жизненный цикл; `POST /api/batches/{id}/otk_accept/` создаёт `OtkCheck`, обновляет `ProductionBatch`, при `accepted > 0` создаёт `WarehouseBatch` с `cost_per_piece` / `cost_per_meter` **с копированием с партии производства**.
6. **Склад ГП:** резерв `POST /api/warehouse/batches/reserve/`, упаковка `POST /api/warehouse/batches/package/` (FIFO по строкам `unpacked` в `apps/warehouse/views.py`).
7. **Продажа:** `SaleSerializer.create` → `apply_sale_to_warehouse_batch` (`apps/warehouse/stock_ops.py`) → уменьшение `WarehouseBatch.quantity` / смена статуса; `revenue`, `cost`, `profit` на `Sale` считаются в сериализаторе.

## Источник истины (по коду)

| Вопрос | Где в коде |
|--------|------------|
| Остаток сырья | Сумма `MaterialBatch.quantity_remaining` + журнал `MaterialStockDeduction` |
| Остаток химии | Сумма `ChemistryBatch.quantity_remaining` + `ChemistryStockDeduction` |
| Фактическая себестоимость сырья при списании | `MaterialStockDeduction.line_total` / обновление партий в `apps/materials/fifo.py` |
| Фактическая себестоимость химии при списании | `ChemistryStockDeduction` от `cost_per_unit` партий в `apps/chemistry/fifo.py` |
| Себестоимость партии производства | `ProductionBatch.material_cost_total` после `apply_production_batch_stock_and_cost` |
| Себестоимость строки склада ГП | Копия `cost_per_piece` / `cost_per_meter` с `ProductionBatch` при приёмке ОТК |
| Прибыль продажи | `Sale.profit = revenue - cost`, `cost = sold_pieces × warehouse_batch.cost_per_piece` в `apps/sales/serializers.py` |
| Агрегаты аналитики | Чтение из `Sale`, `MaterialBatch`, `MaterialStockDeduction`, `ProductionBatch`, и т.д. в `apps/analytics/views.py` |

---

# Склад сырья (`apps/materials`)

## A. Модели

- **`RawMaterial`**: `name`, `unit` (в API нормализуется к `kg`/`g`), `min_balance`, `is_active`, `comment`. Главный справочный объект модуля.
- **`MaterialBatch`**: FK `material`, `quantity_initial`, `quantity_remaining` (**хранение в кг**), `unit` (партии — `'kg'`), `unit_price`, `total_price` (пересчёт в `save`), поставщик, `received_at`.
- **`MaterialStockDeduction`**: FK `batch`, `quantity`, `unit_price`, `line_total`, `reason`, `reference_id`, `created_at`.

Связи: `MaterialBatch` → `RawMaterial`; `MaterialStockDeduction` → `MaterialBatch` (PROTECT).

## B. Бизнес-логика

- **Создание прихода:** только `POST /api/incoming/` (`IncomingViewSet`), сериализатор `MaterialBatchSerializer.create` переводит количество в кг через `quantity_to_storage_kg`.
- **Состояние остатка:** уменьшение `quantity_remaining` только через `fifo_deduct` / откат `reverse_stock_deductions` (`apps/materials/fifo.py`).
- **Списание:** `fifo_deduct` (причины в коде: `chemistry_batch_produce`, `production_batch` — задаются вызывающими модулями).
- **FIFO:** порядок партий `received_at`, `created_at`, `id` (`apps/materials/fifo.py`).
- **Себестоимость списания:** сумма `take * unit_price` по строкам списания, запись в `MaterialStockDeduction.line_total`.
- **Проверка остатков:** перед списанием агрегат `quantity_remaining`; при нехватке `ValidationError` с `code: INSUFFICIENT_STOCK`.
- **Отрицательный остаток:** `MaterialBatch.clean` запрещает `quantity_remaining < 0`; на уровне БД обновление через `F()` после проверки доступного количества.
- **Ограничения удаления/редактирования сырья:** `RawMaterialViewSet.destroy` / `partial_update` + `apps/materials/usage_checks.py` (`raw_material_is_deletable`, `raw_material_unit_change_denial`).

## C. API

Базовый путь: `/api/`. Права: `IsAdminOrHasAccess` + `required_access_key='materials'` (кроме случаев, где указано иначе в коде — здесь везде `materials`).

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/raw-materials/` | Список справочника |
| POST | `/api/raw-materials/` | Создание |
| GET | `/api/raw-materials/{id}/` | Карточка |
| PATCH/PUT | `/api/raw-materials/{id}/` | Обновление (`update` делегирует в `partial_update`) |
| DELETE | `/api/raw-materials/{id}/` | Удаление при `raw_material_is_deletable` |
| GET | `/api/incoming/` | Список партий прихода |
| POST | `/api/incoming/` | Создание партии (только GET/POST у `IncomingViewSet`) |
| GET | `/api/materials/balances/` | Остатки по справочнику |
| GET | `/api/materials/movements/` | Журнал движений (синтетический список из партий и списаний) |

**`RawMaterialSerializer` (POST/PATCH):** поля `name`, `unit` (обяз., только `kg`/`g`), `min_balance`, `is_active`, `comment`. Read-only в Meta явно не перечислены кроме `id` — `id` read на ответе.

**`MaterialBatchSerializer` (POST):** обязательные: `material_id` (только `is_active=True`), `quantity`, `unit_price`, `received_at`. Опционально: `supplier_name`, `document_number` → `supplier_batch_number`, `comment`. Read-only в ответе/после создания: `quantity_initial`/`quantity_remaining` в смысле Meta — `total_price`, `quantity_initial`, `quantity_remaining`, `created_at`, `unit` read_only; вход `quantity` write_only.

**Ошибки:** `409` при смене `unit` при запрете; `409` при удалении с `code: MATERIAL_IN_USE`; стандартные `400` валидации DRF.

## D. Сервисы и внутренняя логика

- `material_stock_kg`, `fifo_deduct`, `simulate_fifo_cost_kg`, `reverse_stock_deductions` — `apps/materials/fifo.py`.
- `raw_material_is_deletable`, `raw_material_unit_change_denial` — `apps/materials/usage_checks.py`.

## E. Валидации и ограничения

- Смена единицы сырья заблокирована при наличии партий, движений, использования в рецептах/химии/`RecipeRunBatchComponent`.
- Удаление сырья — то же условие.
- `min_balance` ≥ 0.

## F. Фактическая цепочка (склад сырья)

- **Приход:** создаётся `MaterialBatch`, растёт остаток.
- **Списание:** создаются `MaterialStockDeduction`, уменьшается `quantity_remaining` у выбранных партий FIFO.
- **Дальше:** движения отражаются в `/api/materials/movements/` с маппингом `reason` → тип движения (`writeoff_chemistry`, `writeoff_production`, иначе `writeoff_other`).

## G. Спорные / опасные места (склад сырья)

- Журнал движений строится в памяти перебором всех партий и списаний (`_build_movement_items` в `apps/materials/views.py`) — масштабирование на больших данных не оптимизировано в коде.

---

# Химия (`apps/chemistry`)

## A. Модели

- `ChemistryCatalog` — справочник (аналог сырья по полям `unit`, `min_balance`, `is_active`).
- `ChemistryRecipe` — `quantity_per_unit` сырья на **1 кг** готовой химии, unique `(chemistry, raw_material)`.
- `ChemistryBatch` — выпуск: `quantity_produced`, `quantity_remaining`, `cost_total`, `cost_per_unit`, `produced_by`, `source_task` (FK на `ChemistryTask`).
- `ChemistryStockDeduction` — списание партий химии (FIFO).
- `ChemistryTask`, `ChemistryTaskElement` — задания (элементы в модели есть; API заданий в основном по `ChemistryTask`).

Главный объект учёта остатков химии — **партии `ChemistryBatch`**.

## B. Бизнес-логика

- **Выпуск:** `produce_chemistry` — проверка активного каталога, проверка остатков сырья по нормам, затем создание `ChemistryBatch` и цикл `fifo_deduct` по каждой строке `ChemistryRecipe` с `reference_id=batch.pk` (сырьё `RAW_REASON = 'chemistry_batch_produce'`).
- **Себестоимость партии химии:** сумма списаний сырья → `ChemistryBatch.cost_total`, `cost_per_unit` в `save`.
- **FIFO химии при расходе в производстве:** `fifo_deduct_chemistry` из `apps/chemistry/fifo.py` с причиной `production_batch` (вызывается из `batch_stock.py`).
- **Удаление карточки химии:** `chemistry_catalog_deletable` (`apps/chemistry/catalog_policy.py`).
- **Задание `confirm`:** тот же `produce_chemistry` с `source_task_id`, затем `task.status = 'done'`. Удаление выполненного задания запрещено в `perform_destroy`.

## C. API

`required_access_key='chemistry'`.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST/PATCH/PUT/DELETE | `/api/chemistry/elements/` | CRUD справочника (`ChemistryCatalogViewSet`) |
| POST | `/api/chemistry/elements/produce/` | Выпуск без задания (`ChemistryProduceSerializer`: `chemistry_id`, `quantity`, `comment`) |
| CRUD | `/api/chemistry/tasks/` | Задания |
| POST | `/api/chemistry/tasks/{id}/confirm/` | Выпуск по заданию |
| GET | `/api/chemistry/balances/` | Остатки по каталогу |
| GET | `/api/chemistry/batches/` | История партий (read-only viewset) |

**Ошибки produce/confirm:** тело `ValidationError`; HTTP статус из `_produce_error_status`: `409` для `INSUFFICIENT_STOCK`, `EMPTY_CHEMISTRY_RECIPE`.

**Состав каталога:** при создании — `recipe_lines` или `compositions` в теле; PATCH только с полем `recipe_lines` для замены состава (`ChemistryCatalogSerializer`).

## D. Сервисы

- `produce_chemistry` — `apps/chemistry/produce.py`.
- FIFO химии — `apps/chemistry/fifo.py`.
- Политики удаления/единицы — `apps/chemistry/catalog_policy.py`.

## E. Валидации

- Выпуск без строк `ChemistryRecipe` — ошибка `EMPTY_CHEMISTRY_RECIPE`.
- Недостаточно сырья — `INSUFFICIENT_STOCK` с массивом `missing` в produce.

## F. Цепочка

Сырьё (FIFO) → себестоимость в `ChemistryBatch` → остаток химии → далее списание химии в производстве через `fifo_deduct_chemistry`.

## G. Замечания

- `ChemistryTaskElement` в API текущих viewsets **не найдено** использования (модель есть, сериализатор задач не разворачивает элементы в отдельном CRUD в просмотренных `views.py`).

---

# Профили (`PlasticProfile`, `apps/recipes`)

## A. Модели

- `PlasticProfile`: `name`, `code` (unique), `comment`, `is_active`.
- Связь: `Recipe.profile` → `PlasticProfile`.

Главный объект — **профиль** как вид ГП и контейнер для рецептов.

## B. Бизнес-логика

- Удаление: `plastic_profile_deletable` — нельзя при наличии рецептов или `ProductionBatch` с этим `profile_id` (`apps/recipes/profile_policy.py`, `PlasticProfileViewSet.destroy`).

## C. API

`required_access_key='recipes'`.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST/… | `/api/plastic-profiles/` | `PlasticProfileViewSet` (list: `PlasticProfileListSerializer`) |

**Поля создания/правки:** `PlasticProfileSerializer` — `name`, `code`, `comment`, `is_active`, read `deletable`.

**Ошибки:** `409` `PROFILE_IN_USE`.

## D. Сервисы

Логика удаления — функция `plastic_profile_deletable` в `apps/recipes/profile_policy.py`.

## E–G

Профиль сам по себе **не** ведёт склад и **не** списывает; используется в рецептах и производстве.

---

# Рецепты (`Recipe`, `RecipeComponent`, `apps/recipes`)

## A. Модели

- `Recipe`: `recipe` (название), FK `profile`, `product`, `base_unit` (в коде по умолчанию `per_meter`), устаревшие `output_quantity`, `output_unit_kind`, `comment`, `is_active`.
- `RecipeComponent`: `type` (`raw` / `chem`), FK на сырьё или химию, `quantity_per_meter` (в коде нормы к **метру** профиля), `unit` (по умолчанию `'кг'` в create из view).

При удалении `Recipe` обновляются `Order` и `RecipeRun` снимками имён (`apps/recipes/models.py` `Recipe.delete`).

## B. Бизнес-логика

- CRUD не списывает склад (`RecipeViewSet` docstring).
- `perform_create` / `perform_update` пересобирают `RecipeComponent` из тела `components` (нормализация в `RecipeViewSet._normalize_component`).
- Доступность норм на метр: `GET /api/recipes/{id}/availability/` — сравнение `quantity_per_meter` с **полным остатком** `material_stock_kg` / `chemistry_stock_kg` (не умножается на метраж партии).

## C. API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST/PATCH/PUT/DELETE | `/api/recipes/` | Рецепты |
| GET | `/api/recipes/{id}/availability/` | Проверка остатков на 1 м (в кг в ответе) |

**`RecipeSerializer`:** для POST обязателен `profile_id`. Поля `components` в сериализаторе **read_only** — передаются в теле запроса, но пишутся в `perform_create`/`perform_update` view.

**Удаление:** `409` `RECIPE_IN_USE` если есть `RecipeRun`, `Order` или `ProductionBatch` с этим рецептом (`recipe_deletable`, `apps/recipes/recipe_policy.py`).

## D. Сервисы

Политика удаления — `apps/recipes/recipe_policy.py`. Плановая оценка себестоимости по рецепту реализована в `apps/production/costing.py` (`estimate_recipe_material_cost`, `chemistry_estimated_kg_price`), но **вызовов из других модулей по проекту не найдено** (только определения в `costing.py`).

## E. Валидации

- Компонент: ровно один из `material_id` / `chemistry_id`.
- Рецепт должен иметь компоненты для создания партии производства через `ProductionBatchCreateUpdateSerializer`.

## F. Цепочка

Рецепт задаёт нормы → используется в `aggregate_consumption_for_recipe` при списании производственной партии.

## G. Замечания

- Поля `output_quantity` / `output_unit_kind` помечены в модели как устаревшие, но используются в логике замеса/объёма ОТК (`submit_recipe_run_to_otk`, `_recipe_run_otk_quantity` в `apps/production/views.py`).

---

# Производство (`apps/production`)

## A. Модели (ключевые)

- `Order` — заказ: `recipe`, `line`, `quantity`, `product`, `status`, снимки имён при потере FK.
- `ProductionBatch` — партия: `pieces`, `length_per_piece`, `total_meters` / `quantity`, `otk_status`, `lifecycle_status`, `material_cost_total`, `cost_per_meter`, `cost_per_piece`, снимок параметров смены (`shift_*`), FK `shift`.
- Остальное: `Line`, `LineHistory`, `Shift`, `ShiftNote`, `ShiftComplaint` — см. раздел «Линии и смены».

Главный производственный объект для ОТК и себестоимости — **`ProductionBatch`**.

## B. Бизнес-логика

- **Создание партии:** `POST /api/batches/` — `ProductionBatchCreateUpdateSerializer`: обязательны открытая смена на линии **для текущего пользователя** (`Shift` OPEN, та же `line`), линия не на паузе, `profile` + `recipe` согласованы, у рецепта есть компоненты → создание + **`apply_production_batch_stock_and_cost`**.
- **Обновление партии (без связи с замесом):** откат списания `reverse_production_batch_stock` со старыми метрами/рецептом, затем снова `apply_*` при изменении полей (если lifecycle pending и `otk_pending`).
- **После отправки в ОТК:** правка ограничена `comment` в сериализаторе.
- **Связь с замесом:** если `RecipeRun.objects.filter(production_batch=instance).exists()`, update сериализатора падает с ошибкой (изменение через замес «недоступно»).

## C. API

Права: `IsAdminOrHasAccess` заменён на **`IsAdminOrHasProductionOrOtk`** для `BatchViewSet` (`config/permissions.py`): create/update/destroy только при ключе `production`; list/retrieve — `otk` или `production`.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/batches/` | Список (`BatchListSerializer`, фильтры `otk_status`, `order`, `line`, `profile`, `lifecycle_status`) |
| POST | `/api/batches/` | Создание партии (идемпотентность по заголовку `X-Request-Id` / `Idempotency-Key`) |
| GET/PATCH | `/api/batches/{id}/` | Чтение / частичное обновление (ограничения см. serializer) |
| POST | `/api/batches/{id}/submit-for-otk/` | В очередь ОТК |
| POST | `/api/batches/{id}/otk_accept/` | Приёмка (создание `WarehouseBatch` при accepted>0) |

**POST create тело (разрешённые ключи при create):** `profile`, `recipe`, `line`, `pieces`, `length_per_piece`, `comment`, `date`, `produced_at`, `product` (опц., иначе из имени профиля).

**Read-only с сервера:** `total_meters`, агрегаты себестоимости задаются списанием, не произвольным телом.

**Ошибки:** валидация смены/линии/рецепта — `400`; `INSUFFICIENT_STOCK` из `apply_production_batch_stock_and_cost` — через DRF validation; submit/otk — `409`/`400` по коду в `views.py`.

## D. Сервисы

- `apply_production_batch_stock_and_cost`, `reverse_production_batch_stock`, `aggregate_consumption_for_recipe` — `apps/production/batch_stock.py`.

## E. Валидации

- Нельзя создать партию без открытой смены пользователя на выбранной линии (и без паузы) — см. `ProductionBatchCreateUpdateSerializer.validate`.
- Отрицательный остаток сырья/химии на списании блокируется в FIFO-функциях до создания дедукций.

## F. Цепочка

Партия → (при п.3) списание сырья/химии FIFO → `material_cost_total` → ОТК → склад ГП копирует `cost_per_piece` / `cost_per_meter`.

## G. Замечания

- Партия, созданная только из потока **RecipeRun** (`submit_recipe_run_to_otk`), **не** вызывает `apply_production_batch_stock_and_cost` в показанном коде — `material_cost_total` остаётся **0** до отдельного сценария с прямым созданием/обновлением через serializer производства (такого автоматического вызова при привязке замеса **не найдено**).

---

# RecipeRun / замес (`apps/production`)

## A. Модели

- `RecipeRun` — FK `recipe`, `line`, `production_batch` (OneToOne с обратным именем `source_recipe_run`), снимки, флаг `recipe_run_consumption_applied` с комментарием в модели: **«не используется»**.
- `RecipeRunBatch` — ёмкости внутри запуска.
- `RecipeRunBatchComponent` — фактические количества по строкам (сырьё или химия), опционально `recipe_component`.

## B. Бизнес-логика

- **Создание:** `RecipeRunViewSet.create` — валидация открытой непаузной смены на линии при создании (`RecipeRunWriteSerializer`), сохранение ёмкостей, затем **`submit_recipe_run_to_otk`** — создание `Order` + `ProductionBatch` с `pieces=1`, `length_per_piece=qty`, `material_cost_total=0`, привязка `RecipeRun.production_batch`. **Списание складов при этом не выполняется** (комментарий в `submit_recipe_run_to_otk`, `apps/production/views.py`).
- **PATCH:** обновление замеса; если есть партия и она `OTK_PENDING`, пересчитывается количество через `submit_recipe_run_to_otk` с `quantity` / `output_scale` из тела.
- **DELETE:** если партия есть и `otk_status == pending`, вызывается `reverse_production_batch_stock` (откат дедукций с `reference_id` партии, если они были), удаление партии и заказа при отсутствии других партий у заказа.

## C. API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/production/recipe-runs/` | Список |
| POST | `/api/production/recipe-runs/` | Создание + постановка в ОТК (тело: `recipe_id`, `line_id`, `batches`, опц. `quantity`, `output_scale`/`scale`) |
| GET | `/api/production/recipe-runs/{id}/` | Деталка |
| PATCH | `/api/production/recipe-runs/{id}/` | Обновление (ограничение если партия не pending ОТК — `409`) |
| DELETE | `/api/production/recipe-runs/{id}/` | Удаление (конфликт `RecipeRunDeleteConflict` / `409` если партия уже не pending) |
| POST | `/api/production/recipe-runs/{id}/submit-to-otk/` | Повторная отправка/создание партии |

**`RecipeRunWriteSerializer` / вложенные:** в каждой ёмкости обязателен массив `components` с ровно одним из `material_id`/`chemistry_id`, `quantity` > 0; опционально `recipe_component_id` валидируется на принадлежность рецепту.

## D. Сервисы

- `submit_recipe_run_to_otk`, вспомогательные `_recipe_run_otk_quantity`, `_parse_output_scale` — `apps/production/views.py`.

## E. Влияние на остатки

- **Фактически по коду:** строки `RecipeRunBatchComponent` **не** вызывают `fifo_deduct` / `fifo_deduct_chemistry`. Влияние на остатки идёт только через связанную **`ProductionBatch`**, и только если для неё выполнено **`apply_production_batch_stock_and_cost`** (прямой путь `POST /api/batches/`). Поток только RecipeRun+submit **не** добавляет такой вызов.

## F. Цепочка

Замес (план расхода в БД) → партия ОТК с метражом из `quantity` / `output_quantity×scale` → далее как производство/ОТК без автоматического списания по замесу.

## G. Замечания

- Расхождение: UI/модель замеса храняет «факт» по строкам, но **учёт остатков по этим числам не подключён** к FIFO в просмотренном коде.

---

# ОТК (`apps/otk` + действия в `BatchViewSet`)

## A. Модели

- `OtkCheck`: FK `batch` (`ProductionBatch`), `accepted`, `rejected`, `check_status`, `reject_reason`, `comment`, `inspector`, `inspector_name`, `checked_date` (auto_now_add на модели; в `otk_accept` дополнительно `update(checked_date=…)`).

## B. Бизнес-логика

- Очередь: фильтр `otk_status=pending`, `lifecycle_status=otk`, `in_otk_queue=True` в `OtkPendingView`.
- Приёмка: `BatchViewSet.otk_accept` — проверка суммы **accepted + rejected == batch.pieces** (все как `Decimal`), при браке нужна причина; создание `OtkCheck`; обновление партии; при `accepted > 0` — создание **`WarehouseBatch`**; если был заказ — `Order.status=done`.

## C. API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/otk/pending/` | Список партий в очереди (`required_access_key='otk'`) |
| POST | `/api/batches/{id}/submit-for-otk/` | Перевод в очередь |
| POST | `/api/batches/{id}/otk_accept/` | Фиксация результата |

**Обязательные поля `otk_accept`:** `otk_accepted` или `accepted`; `otk_defect` или `rejected`. Алиасы причин/комментариев см. в `views.py`.

## D. Связь производство ↔ ОТК ↔ склад

- Производство: смена статусов и полей `ProductionBatch` в `submit_for_otk` / `otk_accept`.
- Склад: только создание `WarehouseBatch` в `otk_accept` при принятом количестве > 0.

## E. Контроль accepted + rejected = pieces

- Строго в `BatchViewSet.otk_accept` (`apps/production/views.py`): сравнение с `batch.pieces` с квантованием `Decimal('0.0001')`.

## F. Цепочка

Партия в очереди → проверка → запись `OtkCheck` → партия `lifecycle_done` → склад при приёмке.

## G.

- Отдельного CRUD для `OtkCheck` через роутер **не зарегистрировано**; сериализатор `apps/otk/serializers.py` есть, endpoint’ов на него в `config/api_urls.py` **нет**.

---

# Склад готовой продукции (`apps/warehouse`)

## A. Модели

- `WarehouseBatch`: `product` (строковый ключ), `quantity`, `length_per_piece`, `total_meters` (в `save`), `status` (`available`/`reserved`/`shipped`), `inventory_form` (`unpacked`/`packed`/`open_package`), параметры упаковки, снимок ОТК, FK `source_batch` → `ProductionBatch`, FK `profile` в модели (nullable).

## B. Бизнес-логика

- Создание строки «с нуля» через публичный create viewset **не предусмотрено** — `WarehouseBatchViewSet` **ReadOnlyModelViewSet**.
- Появление строки: из **`otk_accept`** и из **`package`** (консолидация unpacked FIFO по `id` в `plan_fifo_pack`).
- Резерв: смена статуса на `reserved` без уменьшения `quantity`.
- Продажа: списание через `apply_sale_to_warehouse_batch` (`apps/warehouse/stock_ops.py`).

## C. API

`required_access_key='warehouse'`.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/warehouse/batches/` | Список (фильтр `status`, `product`, формы; query `debug` включает тестовые названия) |
| GET | `/api/warehouse/batches/{id}/` | Карточка |
| POST | `/api/warehouse/batches/reserve/` | Резерв (`batch_id`/`batchId`, `quantity`, опц. `sale_id` для аудита) |
| POST | `/api/warehouse/batches/package/` | Упаковка (геометрия, `product_id`, `packages_count`, …) |

Алиасы без `/api/` в корне: `warehouse/pack-from-otk/`, `warehouse/pack/`, `batches/pack_from_otk/` → тот же `package` (`config/urls.py`).

**Ошибки reserve/package:** `400` валидация, `404` нет партии, `409` конфликтные сценарии упаковки/остатка (см. `views.py`).

## D. Сервисы

- `apply_sale_to_warehouse_batch`, `deduct_unpacked_quantity`, нормализация форм — `apps/warehouse/stock_ops.py`.
- Планирование упаковки — `apps/warehouse/packaging.py` (используется из `views.package`).

## E. FIFO

- Упаковка: порядок строк `inventory_form=unpacked`, `status=available`, `order_by('id')`, см. `WarehouseBatchViewSet.package`.

## F. Цепочка

ОТК → строка `unpacked` → резерв/упаковка/продажа.

## G.

- Резерв **не** уменьшает количество — доступность для других операций завязана на `status` в части кода; продажа проверяет `status == available` в `apply_sale_to_warehouse_batch`.

---

# Клиенты (`apps/sales`, модель `Client`)

## A. Модели

`Client`: контактные и реквизитные поля, `is_active`.

## B. Логика

- Удаление запрещено при наличии продаж (`ClientViewSet.destroy` → `409` `CLIENT_IN_USE`).

## C. API

`required_access_key='clients'`.

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/clients/` | Клиенты |
| GET | `/api/clients/{id}/history/` | История продаж клиента |

**Поля `ClientSerializer`:** см. `apps/sales/serializers.py` (алиасы `contact_person`, `whatsapp_telegram`, read `sales_count`, `sales_total` из аннотации queryset).

## D–G

Клиенты не связаны с производственным складом сырья напрямую; связь через `Sale`.

---

# Продажи (`apps/sales`, `Sale`, `Shipment`)

## A. Модели

- `Sale`: `order_number`, `client`, `warehouse_batch`, `product`, режимы `sale_mode` (`pieces`/`packages`), `sold_pieces`, `sold_packages`, `quantity` (= legacy sold_pieces), `quantity_input`, `price`, `revenue`, `cost`, `profit`, `stock_form`, `piece_pick`, дата и пр.
- `Shipment`: FK на `Sale`, отдельный жизненный цикл отгрузки. В `SaleViewSet.perform_destroy` сначала удаляются отгрузки из‑за PROTECT.

## B. Логика

- **Создание:** `_apply_finance` — выручка от цены и режима; себестоимость от `warehouse_batch.cost_per_piece × sold_pieces`; `profit = revenue - cost`. При первом подключении склада — `apply_sale_to_warehouse_batch` в той же транзакции.
- **Обновление:** при привязанном складе запрещены смена партии, изменение количества, `stock_form`/`piece_pick` (`perform_update`).

## C. API

`required_access_key='sales'`.

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/sales/` | Продажи |
| GET | `/api/sales/{id}/nakladnaya/`, `/waybill/`, `/invoice/` | HTML-накладная |

**Обязательные/логические поля:** см. `SaleSerializer.validate` — нужен `product` или склад для подстановки продукта; для упакованного склада при первом линке обязателен `piece_pick` (правила по форме).

## D. Сервисы

- `apply_sale_to_warehouse_batch` — `apps/warehouse/stock_ops.py` (вызывается из `SaleSerializer`).

## E.

- Прибыль пересчитывается в сериализаторе при create/update из актуальных полей.

## G.

- `Shipment` не имеет зарегистрированного viewset в `config/api_urls.py` — управление отгрузками через ORM/API **в маршрутах не найдено**.

---

# Аналитика (`apps/analytics`)

## A. Модели

Отдельных моделей аналитики **нет** (`apps/analytics/models.py` в дереве проекта не использован как доменный слой в данном обзоре маршрутов). Логика в `views.py` + `services.py`.

## B. Логика

- `AnalyticsSummaryView.list`: агрегаты по периоду и фильтрам `parse_analytics_scope` (`date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `status`).
- Списания сырья в финансах: сумма `MaterialStockDeduction.line_total` за период (`writeoff_q` по `created_at`).
- `profit` / `profit_simple` в ответе: выручка минус закупки приходов за период; отдельно `profit_recorded_in_sales` — сумма поля `profit` в `Sale`.
- `chemistry.tasks_marked_done_linked_to_writeoffs_qty`: агрегат по `ChemistryBatch` с `source_task_id` не null за период через **`scope.writeoff_q()`** (тот же фильтр дат, что и для списаний сырья) — см. `apps/analytics/views.py`.

## C. API

`required_access_key='analytics'`.

| Метод | Путь | Query | Назначение |
|-------|------|-------|------------|
| GET | `/api/analytics/summary/` | `year`, `month`, `day`, фильтры scope | Сводка |
| GET | `/api/analytics/revenue-details/` | **`year` обязателен** | Продажи |
| GET | `/api/analytics/expense-details/` | **`year` обязателен** | Приходы сырья |
| GET | `/api/analytics/writeoff-details/` | **`year` обязателен** | Списания сырья (строки с `fifo_line_total` и т.д.) |

## D. Сервисы

- `parse_period`, `parse_analytics_scope`, `Period`, `AnalyticsScope`, хелперы `*_scope_q` — `apps/analytics/services.py`.
- Функция `material_avg_unit_prices` в том же файле **нигде кроме определения не вызывается**.

## E–G

- Детализации требуют `year` явно; иначе `ValidationError` 400.

---

# Линии и смены (`apps/production`)

## A. Модели

- `Line`: `name`, `code`, `notes`, `is_active`. При удалении обновляются снимки на `Order`, `RecipeRun`, `LineHistory`, `Shift`.
- `LineHistory`: события `open`, `close`, `params_update`, `shift_pause`, `shift_resume` с размерами/комментариями.
- `Shift`: `line` (nullable — «личная» смена), `user`, `opened_at`, `closed_at`, `status` (`open`/`paused`/`closed`). Ограничения уникальности: одна открытая личная смена на пользователя; одна открытая смена на пару `(user, line)` при непустой линии (`Meta.constraints` в модели).
- `ShiftNote`, `ShiftComplaint` — заметки и жалобы.

## B. Логика

- **Смена на линии:** `POST /api/lines/{id}/open/` — запись `LineHistory` OPEN + создание `Shift` с привязкой к линии и пользователю; проверка что нет другой открытой смены пользователя на этой линии; проверка что на линии ещё нет открытой смены (`line_shift_is_open`).
- **Закрытие:** `close` — запись CLOSE в историю, закрытие `Shift` текущего пользователя на линии; размеры из тела или из последних параметров в истории.
- **Пауза/возобновление:** события в `LineHistory`, массовое `Shift.objects.filter(...).update(status=...)`.
- **Состояние «смена открыта» для линии:** определяется **по последней записи `LineHistory`**, не по наличию любого `Shift` с `closed_at is null`** — см. `line_shift_is_open` в `apps/production/shift_state.py` (возможное расхождение с записями `Shift` при ручных правках БД).
- **Глобальные смены:** `POST /api/shifts/open/` — без `line_id` создаётся только `Shift` без строки в `LineHistory`; с `line_id` — как открытие на линии с параметрами.

## C. API

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/lines/` | Линии (destroy запрещён при открытой смене — проверка `line_shift_is_open` и открытых `Shift` на линии) |
| POST | `/api/lines/{id}/open/`, `/close/`, `/shift-params/`, `/shift-pause/`, `/shift-resume/` | Сценарии смены |
| GET | `/api/lines/history/`, `/api/lines/{id}/history/`, `/api/lines/{id}/history/session/` | История |
| GET/POST | `/api/shifts/` (read-only list/retrieve для стандартных маршрутов роутера) | Список/деталка смен |
| POST | `/api/shifts/open/`, `/api/shifts/close/` | Открыть/закрыть |
| GET | `/api/shifts/my/` | Текущая открытая **личная** смена |
| GET/POST | `/api/shifts/notes/` | Заметки личной смены |
| GET | `/api/shifts/{id}/notes/` | Заметки конкретной смены |
| GET | `/api/shifts/history/` | История смен пользователя |
| GET/POST | `/api/shifts/complaints/` | Жалобы (`CanAccessShiftComplaints`) |

**Поля open линии:** `LineShiftOpenSerializer` — `height`, `width`, `angle_deg`, опц. `comment`, `session_title`.

## D. Сервисы

- `prefetch_line_histories_map`, `line_shift_is_open`, `line_shift_is_paused`, … — `apps/production/shift_state.py`.

## E.

- Замес требует открытую смену на линии при создании (`RecipeRunWriteSerializer`).

## G.

- Два способа открыть смену на линии (`/api/lines/.../open/` и `/api/shifts/open/` с `line_id`) — дублирование сценариев в коде.

---

# Пользователи / роли (доступ к разделам; не путать с «Профилями» ГП)

## API (фрагмент)

| Метод | Путь | Назначение |
|-------|------|------------|
| CRUD | `/api/users/` | Пользователи (`required_access_key='users'`) |
| PATCH | `/api/users/{id}/access/` | Замена списка `UserAccess`: тело `{ "access_keys": [...] }` (`UserAccessPatchSerializer`) |
| CRUD | `/api/roles/` | Роли (`RoleSerializer`; вложенные `RoleAccess` в сериализаторе **не найдены** в кратком просмотре — только `id`, `name` в `RoleSerializer`) |

Ключи вкладок: `settings.ACCESS_KEYS` в `config/settings.py`; эффективные ключи пользователя — `User.get_access_keys()` (`UserAccess` только, суперпользователь без строк получает полный список из настроек).

---

# Ключевые выводы по бэкенду

## Что реализовано согласованно с явной моделью данных

- Двухуровневый учёт сырья: партии прихода + дедукции с `reason`/`reference_id` и FIFO (`apps/materials/fifo.py`).
- Учёт химии: выпуск с переносом стоимости сырья в `ChemistryBatch`, списание химии FIFO при производстве (`apps/chemistry/fifo.py`, `batch_stock.py`).
- Производственная партия через **`POST /api/batches/`** связывает рецепт, метраж и списание в одной транзакции create сериализатора.
- ОТК создаёт **`OtkCheck`** и при принятии — строки **`WarehouseBatch`** с копией себестоимости с партии.
- Продажи уменьшают остаток склада ГП и считают `revenue`/`cost`/`profit` в одном месте (`SaleSerializer`).

## Что выглядит устаревшим или дублируется

- Поля `output_quantity` / `output_unit_kind` у рецепта помечены как устаревшие, но участвуют в расчёте объёма для замеса/партии ОТК.
- `ProductionBatch.cost_price` дублирует `material_cost_total` в `save`.
- Два входа открытия смены на линии (`lines` vs `shifts`).
- Несколько URL для `package` склада (`/api/...` и корневые алиасы).

## Что дублируется или не используется

- `apps/production/costing.py` — функции оценки **не вызываются** из views/serializers (по поиску по репозиторию).
- `material_avg_unit_prices` в `apps/analytics/services.py` — **не используется**.
- `Recipe.recipe_run_consumption_applied` — в модели указано как неиспользуемое.

## Потенциально опасно для бизнес-логики

- **Партия производства, созданная из RecipeRun (`submit_recipe_run_to_otk`), не вызывает `apply_production_batch_stock_and_cost`:** остатки сырья/химии и `material_cost_total` могут **не** отражать выпуск по этой партии, пока не пройден отдельный сценарий с `POST /api/batches/` (который для уже созданной партии из замеса ограничен связью `RecipeRun`).
- **Резерв склада** не уменьшает `quantity` — риск расхождения ожиданий «зарезервировано» vs «доступно к продаже» (продажа требует `status==available`).
- **accepted + rejected** проверяется к `pieces`, а не к дробным полям партии — при несогласованности данных в БД возможны ошибки приёмки.
- Состояние смены линии по **`LineHistory`** может расходиться с таблицей **`Shift`**, если данные менялись вне штатных endpoint’ов.

## Фактическая реализация vs ожидаемая архитектура (наблюдения без предложений)

- В модели `RecipeRun` заявлено, что FIFO на партии производства; фактически создание партии из замеса **не** запускает списание в том же потоке.
- `RecipeRunBatchComponent` хранит количества, но **не** является источником для `aggregate_consumption_for_recipe` (используются только нормы `RecipeComponent` и `total_meters` партии при `apply_production_batch_stock_and_cost`).

## Ответ на явный вопрос про RecipeRun

- **Существует ли RecipeRun?** Да: модели, `RecipeRunViewSet`, маршрут `/api/production/recipe-runs/`.
- **Влияет ли на остатки и себестоимость фактически?** Списание складов **не** выполняется при `submit_recipe_run_to_otk` / создании связанной партии. Строки замеса **не** подключены к FIFO в просмотренном коде. Себестоимость партии из замеса при создании остаётся **0** до иных действий (автоматического пересчёта в коде **не найдено**). При удалении замеса с pending-партией вызывается **`reverse_production_batch_stock`**, что откатывает дедукции **только если** они были созданы для этой партии (например, если когда-либо вызывался `apply_production_batch_stock_and_cost` для этого `batch_id`).

---

*Документ составлен по состоянию кода в репозитории (модели, views, serializers, services). Пути файлов — относительно корня проекта `DIas_ERP`.*
