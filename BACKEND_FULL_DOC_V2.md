# Бэкенд DIas_ERP — фактическая реализация (V2)

Документ описывает только то, что есть в коде на момент составления. Модульные пути: `apps/…`.

---

## 1. Модели (структура данных)

В коде справочник «химии» называется **`ChemistryCatalog`** (в ТЗ часто «Chemistry»).

### `RawMaterial` (`apps/materials/models.py`, `db_table`: `raw_materials`)

| Поле | Тип | Связи / флаги |
|------|-----|----------------|
| `name` | CharField(255) | — |
| `unit` | CharField(50), default `'kg'` | В API нормализуется до `kg` \| `g` |
| `min_balance` | Decimal(14,4), null | Порог |
| `is_active` | Boolean, default True | |
| `comment` | TextField | |

Связи: обратная `MaterialBatch.batches` (FK `material`), `ChemistryRecipe`, `RecipeComponent`, `RecipeRunBatchComponent` — по коду внешних ключей в других приложениях.

---

### `MaterialBatch` (`material_batches`)

| Поле | Тип | Связи |
|------|-----|--------|
| `material` | FK → `RawMaterial`, PROTECT, `related_name='batches'` | |
| `quantity_initial` | Decimal(14,4) | Приход |
| `quantity_remaining` | Decimal(14,4) | Остаток для FIFO |
| `unit` | CharField — при создании прихода в сериализаторе принудительно **`'kg'`** (хранение в кг) | |
| `unit_price`, `total_price` | Decimal | `total_price` пересчитывается в `save()` как `quantity_initial * unit_price` |
| `supplier_name`, `supplier_batch_number`, `comment` | | |
| `received_at` | DateTimeField | Порядок FIFO |
| `created_at` | auto_now_add | Порядок FIFO |

`clean()`: `0 ≤ quantity_remaining ≤ quantity_initial`.

---

### `MaterialStockDeduction` (`material_stock_deductions`)

| Поле | Тип |
|------|-----|
| `batch` | FK → `MaterialBatch`, PROTECT |
| `quantity` | Decimal(14,4) |
| `unit_price` | Decimal(14,2) — снимок с партии |
| `line_total` | Decimal(16,2) |
| `reason` | CharField(100) — ключ отката вместе с `reference_id` |
| `reference_id` | PositiveIntegerField, null |
| `created_at` | auto_now_add |

---

### `ChemistryCatalog` (`chemistry_catalog`) — каталог «химии»

Аналогично сырью: `name`, `unit`, `min_balance`, `is_active`, `comment`.

Связи: `ChemistryRecipe`, `ChemistryBatch`, `ChemistryTask`, `RecipeComponent`, и т.д.

---

### `ChemistryRecipe` (`chemistry_composition`)

| Поле | Тип |
|------|-----|
| `chemistry` | FK → `ChemistryCatalog`, CASCADE, `related_name='recipe_lines'` |
| `raw_material` | FK → `materials.RawMaterial`, CASCADE |
| `quantity_per_unit` | Decimal(14,6) — **кг сырья на 1 кг готовой химии** |

`Meta.unique_together`: `(chemistry, raw_material)`.

---

### `ChemistryBatch` (`chemistry_batches`)

| Поле | Тип |
|------|-----|
| `chemistry` | FK → `ChemistryCatalog`, PROTECT |
| `quantity_produced`, `quantity_remaining` | Decimal(14,4) — **кг** |
| `cost_total`, `cost_per_unit` | Decimal — `save()` считает `cost_per_unit = cost_total / quantity_produced` при `qp > 0` |
| `created_at` | Порядок FIFO химии |
| `produced_by` | FK User, SET_NULL |
| `comment` | |
| `source_task` | FK `ChemistryTask`, SET_NULL |

---

### `ChemistryStockDeduction` (`chemistry_stock_deductions`)

Как у сырья: `batch` → `ChemistryBatch`, `quantity`, `unit_price` (снимок `cost_per_unit` партии), `line_total`, `reason`, `reference_id`, `created_at`.

---

### `Recipe` (`recipes`)

| Поле | Примечание |
|------|------------|
| `recipe` | Наименование рецепта |
| `profile` | FK → `PlasticProfile`, PROTECT |
| `product` | Денормализация; при `save()` подставляется из профиля, если пусто |
| `base_unit` | choices, фактически используется норма **на 1 м** (`BASE_UNIT_PER_METER`) |
| `output_quantity`, `output_unit_kind` | Помечены в модели как **устаревшие**; для объёма замеса/партии используются в `_recipe_run_otk_quantity` |
| `comment`, `is_active` | |

`delete()`: обновляет `Order` и `RecipeRun` — `recipe_name_snapshot`, `former_recipe_id` (история после удаления рецепта).

---

### `RecipeComponent` (`recipe_components`)

| Поле | Тип |
|------|-----|
| `recipe` | FK → `Recipe`, CASCADE |
| `type` | `'raw'` \| `'chem'` (`TYPE_RAW` / `TYPE_CHEM`) |
| `raw_material` | FK `RawMaterial`, CASCADE, null если химия |
| `chemistry` | FK `ChemistryCatalog`, CASCADE, null если сырьё |
| `quantity_per_meter` | Decimal(14,6) — **на 1 м профиля** |
| `unit` | CharField, default `'кг'` |

---

### `ProductionBatch` (`production_batches`)

Ключевые поля:

- Связи: `order` (FK `Order`, null), `profile`, `recipe`, `line`, `shift` — часть nullable в схеме, но бизнес-валидация требует линию/рецепт/профиль при создании через API.
- Выпуск: `pieces`, `length_per_piece`, **`total_meters`**, `quantity` (**legacy = total_meters**, синхронизируется в `save()`).
- ОТК: `otk_status` (`pending` / `accepted` / `rejected`), `sent_to_otk`, `in_otk_queue`, `otk_submitted_at`.
- Жизненный цикл: `lifecycle_status` — `pending` → `otk` → `done`.
- Себестоимость: `material_cost_total`, `cost_per_meter`, `cost_per_piece`, `cost_price` (**= material_cost_total** в `save()`).

`recompute_totals()`: `total_meters = pieces * length_per_piece` (quantize 0.0001), затем `quantity = total_meters`.

`save()`: всегда вызывает `recompute_totals()`, пересчитывает `cost_per_meter` / `cost_per_piece` от `material_cost_total`.

---

### `MaterialStockDeduction` / `ChemistryStockDeduction` — см. выше.

---

### `WarehouseBatch` (`warehouse_batches`)

| Поле | Примечание |
|------|------------|
| `profile` | FK `PlasticProfile`, null |
| `product` | Строковый ключ продукта на складе |
| `length_per_piece`, `total_meters` | `total_meters` пересчитывается в `save()` как `quantity * length_per_piece`, если оба заданы |
| `quantity` | **Штук доступно** (остаток строки) |
| `cost_per_piece`, `cost_per_meter` | Копия с `ProductionBatch` при приёмке ОТК |
| `status` | `available` \| `reserved` \| `shipped` |
| `date` | |
| `source_batch` | FK `ProductionBatch`, SET_NULL |
| `inventory_form` | `unpacked` \| `packed` \| `open_package` |
| Поля упаковки | `unit_meters`, `package_total_meters`, `pieces_per_package`, `packages_count` |
| Снимок ОТК | `otk_accepted`, `otk_defect`, `otk_defect_reason`, `otk_comment`, `otk_inspector_name`, `otk_checked_at`, `otk_status` |

---

### `Sale` (`sales`)

| Поле | Примечание |
|------|------------|
| `order_number` | Обязателен; при отсутствии в `create` генерируется `ORD-{year}-{n}` |
| `client` | FK, null |
| `warehouse_batch` | FK `WarehouseBatch`, SET_NULL |
| `product` | |
| `sale_mode` | `pieces` \| `packages` |
| `sold_pieces`, `sold_packages`, `quantity` | `quantity` = legacy, выравнивается под `sold_pieces` в `_apply_finance` |
| `length_per_piece`, `total_meters` | |
| `quantity_input` | Для режима упаковок |
| `price`, `revenue`, `cost`, `profit` | `cost` = `sold_pieces * warehouse_batch.cost_per_piece` при наличии партии |
| `date`, `comment` | |
| `stock_form`, `piece_pick` | Канал списания со склада (см. `stock_ops`) |

---

## 2. Потоки

### 2.1. Сырьё

**Создание справочника**  
`POST` через `RawMaterialViewSet` (`apps/materials/views.py`) → `RawMaterialSerializer`: только поля справочника, без цены/остатка.

**Пополнение (партия прихода)**  
`IncomingViewSet` — URL **`/api/incoming/`** (только GET/POST).  
`MaterialBatchSerializer.create` (`apps/materials/serializers.py`):

1. Снимает `quantity` из validated_data.
2. Переводит в кг: `quantity_to_storage_kg(qty_in, material.unit)`.
3. Ставит `quantity_initial` = `quantity_remaining` = `q_kg`, `unit` = `'kg'`.
4. `total_price` на модели в `save()`.

**Списание**  
Только через **`apps/materials/fifo.py` → `fifo_deduct`** (и откат `reverse_stock_deductions`).  
Вызовы из продукции:

- `apps/chemistry/produce.py` — `reason='chemistry_batch_produce'`, `reference_id=ChemistryBatch.pk`.
- `apps/production/batch_stock.py` — `reason='production_batch'`, `reference_id=ProductionBatch.pk`.

Других вызовов `fifo_deduct` в репозитории нет.

---

### 2.2. Химия

**Справочник**  
CRUD `ChemistryCatalogViewSet` (`apps/chemistry/views.py`).

**Состав (рецепт химии)**  
Модель `ChemistryRecipe` — строки расхода сырья на 1 кг химии. Редактирование через API химии/админки (не дублируется здесь).

**Выпуск партии**  
`produce_chemistry()` — `apps/chemistry/produce.py`. Вызывается из:

- `POST …/chemistry/catalog/produce/` (`ChemistryCatalogViewSet.produce`);
- `POST …/chemistry/tasks/{id}/confirm/` (`ChemistryTaskViewSet.confirm`) с `source_task_id`.

Шаги `produce_chemistry`:

1. Проверка `quantity > 0`, активный `ChemistryCatalog`.
2. Перевод количества в кг: `quantity_to_storage_kg` по `cat.unit`.
3. Загрузка строк `ChemistryRecipe`; если пусто — ошибка `EMPTY_CHEMISTRY_RECIPE`.
4. Проверка остатков сырья: для каждой строки `need = quantity_per_unit * qty_kg`, сравнение с `material_stock_kg` — без блокировки партий, только чтение.
5. Создание `ChemistryBatch` с нулевой себестоимостью.
6. Цикл по строкам: `fifo_deduct(raw_material_id, need, reason=RAW_REASON='chemistry_batch_produce', reference_id=batch.pk)`.
7. `batch.cost_total` = сумма FIFO, `save(update_fields=[...])` — пересчёт `cost_per_unit` в `ChemistryBatch.save()`.

**FIFO химии (как полуфабрикат на профиль)**  
Вызывается только из **`apply_production_batch_stock_and_cost`** → `fifo_deduct_chemistry` (`apps/chemistry/fifo.py`).

**Себестоимость химии (факт)**  
- На выпуске: сумма `line_total` списаний сырья (FIFO).  
- На производстве профиля: сумма `line_total` по `ChemistryStockDeduction` при списании партий `ChemistryBatch` (цена = `cost_per_unit` партии на момент списания).

Плановые оценки (без списания): `apps/production/costing.py` — `simulate_fifo_cost_kg`, `simulate_chemistry_fifo_cost_kg`, `chemistry_estimated_kg_price`, `estimate_recipe_material_cost`.

---

### 2.3. Рецепт профиля (`Recipe` + `RecipeComponent`)

**Создание / изменение**  
`RecipeViewSet` (`apps/recipes/views.py`). Класс докстринга: *«Рецепт — справочник норм на 1 м; сохранение не списывает склад.»*

**Хранение состава**  
Таблица `recipe_components`: FK на рецепт, тип строки, FK на сырьё или каталог химии, `quantity_per_meter`.

**Что рецепт НЕ делает в коде**

- Не создаёт `ProductionBatch` и не списывает склады.
- Не вызывает `fifo_deduct` / `fifo_deduct_chemistry`.
- Изменение рецепта не трогает уже созданные `MaterialStockDeduction` / `ChemistryStockDeduction` задним числом — пересчёт только при **`resync_production_batch_consumption`** для существующей партии в статусе pending (см. ниже).

---

### 2.4. Производство (`ProductionBatch`)

**Создание партии**  
`BatchViewSet` + `ProductionBatchCreateUpdateSerializer.create` (`apps/production/serializers.py`):

1. Валидация: линия с открытой непаузной сменой у текущего пользователя; профиль; рецепт с тем же профилем; у рецепта есть компоненты; `pieces > 0`, `length_per_piece > 0`.
2. `ProductionBatch.objects.create(...)` с привязкой `shift`, `operator`, датами, `otk_status=pending`, `lifecycle_status=pending`.
3. В той же транзакции: **`apply_production_batch_stock_and_cost(batch)`**.

**`total_meters`**  
Не принимается с клиента (read-only в сериализаторе). Считается в **`ProductionBatch.save()`** → `recompute_totals()` → `pieces * length_per_piece`.

**Списание**  
Единственная функция для партии профиля: **`apply_production_batch_stock_and_cost`** (`batch_stock.py`):

- `aggregate_consumption_for_recipe(recipe, total_meters)` — суммарно по каждому `raw_material_id` / `chemistry_id`: `quantity_per_meter * total_meters`.
- Проверка остатков (`material_stock_kg`, `chemistry_stock_kg`).
- `fifo_deduct` / `fifo_deduct_chemistry` с **`reason='production_batch'`**, **`reference_id=batch.pk`**.
- Запись **`batch.material_cost_total`**, `batch.save(update_fields=[...])` — триггер пересчёта `cost_per_meter`, `cost_per_piece`, `cost_price` на модели.

**Обновление партии (только `lifecycle_status=pending`)**  
`update`: при смене полей пересчёта — `resync_production_batch_consumption(instance, previous_recipe=..., previous_total_meters=...)`:

1. `reverse_production_batch_stock` — удаляет все `MaterialStockDeduction` и `ChemistryStockDeduction` с `reason='production_batch'` и `reference_id=batch_id`, возвращает остатки партий.
2. `apply_production_batch_stock_and_cost(batch)` заново.

Если партия связана с `RecipeRun` (`RecipeRun.objects.filter(production_batch=instance).exists()`), PATCH партии запрещён.

---

### 2.5. Recipe Run (замес)

**Что это**  
Модели: `RecipeRun`, `RecipeRunBatch`, `RecipeRunBatchComponent` (`apps/production/models.py`). Докстринг `RecipeRun`: учёт ёмкостей и **фактического расхода по строкам для интерфейса**; реальное FIFO указано в модели: только у связанной **`ProductionBatch`** (`batch_stock`).

**Где используется**

- CRUD: `RecipeRunViewSet` — **`/api/production/recipe-runs/`** (`config/api_urls.py`).
- При `POST` create и при `PATCH` (если уже есть партия в `otk_pending`): после сохранения замеса вызывается **`submit_recipe_run_to_otk`** (`apps/production/views.py`).
- Отдельно: `POST …/recipe-runs/{id}/submit-to-otk/`.

**Логика `submit_recipe_run_to_otk`**

- Если у замеса уже есть `production_batch` и у партии `otk_status == pending`: пересчитывает метры из **`_recipe_run_otk_quantity`** (приоритет: явный `quantity` в запросе → иначе `recipe.output_quantity * output_scale` → иначе fallback текущего `batch.quantity`), выставляет **`pieces=1`**, **`length_per_piece=qty`**, `save`, затем **`resync_production_batch_consumption`**.
- Если партии нет: создаётся **`Order`**, затем **`ProductionBatch`** с теми же правилами метража, связь `RecipeRun.production_batch`, сразу **`apply_production_batch_stock_and_cost(batch)`**.

Объём для FIFO **не** берётся из суммы `RecipeRunBatch.quantity` и **не** из строк `RecipeRunBatchComponent` — только из нормы рецепта × `total_meters` партии, где `total_meters` для замеса = вычисленное `qty` как выше.

**Что замес НЕ делает**

- Не пишет `MaterialStockDeduction` / `ChemistryStockDeduction` сам по себе.
- Поле **`recipe_run_consumption_applied`** помечено в модели как устаревшее, не используется для списания.

**Удаление замеса**  
`perform_destroy`: если партия есть и `otk_pending` — **`reverse_production_batch_stock`**, обнуление связи, удаление `ProductionBatch` и заказа при отсутствии других партий.

---

### 2.6. ОТК

**Очередь**  
`GET /api/otk/pending/` (`apps/otk/views.py`, `OtkPendingView.list`): фильтр **`otk_status=pending`**, **`lifecycle_status=otk`**, **`in_otk_queue=True`**.

**Отправка партии в очередь ОТК**  
`POST /api/batches/{id}/submit-for-otk/` (`BatchViewSet.submit_for_otk`):

- Условия: `lifecycle_status == pending`, `otk_status == pending`, не `done`.
- **`assert_production_batch_ready_for_otk_pipeline(batch)`** — проверки готовности (рецепт, метры > 0, компоненты; при ненулевой норме расхода — наличие списаний или положительной `material_cost_total`).
- Снимок смены на партию: `_apply_shift_snapshot_to_batch`.
- Поля: `lifecycle_status=otk`, `sent_to_otk=True`, `in_otk_queue=True`, `otk_submitted_at=now`.

**Приёмка**  
`POST /api/batches/{id}/otk_accept/` (`BatchViewSet.otk_accept`):

1. Снова **`assert_production_batch_ready_for_otk_pipeline`**.
2. Обязательны числа **`otk_accepted`** (или `accepted`) и **`otk_defect`** (или `rejected`).
3. **`otk_accepted + otk_defect == batch.pieces`** (в штуках, шаг 0.0001 для суммы).
4. Если `rejected > 0` — обязателен `otk_defect_reason` / `rejectReason`.
5. В транзакции: создаётся **`OtkCheck`** с полями партии, принято/брак, статус строки проверки (`accepted` если есть принятые штуки, иначе при полном браке `rejected`).
6. Обновление партии: `otk_status`, `lifecycle_status=done`, `in_otk_queue=False`.
7. Если **`accepted > 0`**: создаётся **`WarehouseBatch`** (`status=available`, `inventory_form=unpacked`, `quantity=accepted`, себестоимость и снимок ОТК с партии).
8. Если у партии был `order_id` — **`Order.status = done`**.

Склад **не** создаётся при `accepted == 0` (полный брак по штукам).

---

### 2.7. Склад ГП

**Создание `WarehouseBatch`**  
Только из **`otk_accept`** при `accepted > 0` (см. выше), либо из логики упаковки/дробления в **`apps/warehouse/stock_ops.py`** и **`apps/warehouse/views.py`** (`package` и связанные пути — дублирование строк при вскрытии упаковки и т.д.).

**Резерв**  
`POST /api/warehouse/batches/reserve/` (`WarehouseBatchViewSet.reserve`):

- Партия должна быть в статусе **`available`**.
- **`quantity` обязано равняться полному остатку строки `batch.quantity`** — иначе 400. Резерв = перевод всей строки в **`reserved`** (частичного резерва нет).

**Списание при продаже**  
`SaleSerializer.create` / `update` при привязке `warehouse_batch` вызывает **`apply_sale_to_warehouse_batch`** (`stock_ops.py`): уменьшение `quantity`, смена статуса на `shipped` при нуле, ветвление по `inventory_form` и `piece_pick` (в т.ч. вскрытие `packed` → новая строка `open_package`).

---

### 2.8. Продажи

**Источник списания**  
Только остаток **`WarehouseBatch`**, не сырьё и не химия напрямую.

**Проверки**  
`SaleSerializer.validate`: продукт или партия; для первичной привязки партии — нормализация `stock_form` / обязательный `piece_pick` для `packed` (см. `validate` и `apply_sale_to_warehouse_batch`).

**Себестоимость продажи**  
`_apply_finance`: `cost = sold_pieces * warehouse_batch.cost_per_piece` (из карточки партии склада).

**Статус партии**  
`apply_sale_to_warehouse_batch` требует **`status == available`**; зарезервированная партия без перевода обратно не продаётся этим путём.

---

## 3. FIFO

| Ресурс | Файл | Порядок отбора партий |
|--------|------|------------------------|
| Сырьё | `apps/materials/fifo.py` → **`fifo_deduct`** | `MaterialBatch`: `order_by('received_at', 'created_at', 'id')` при `quantity_remaining > 0`. Докстринг функции упоминает `created_at` — фактический порядок в запросе: **`received_at`, `created_at`, `id`**. |
| Химия | `apps/chemistry/fifo.py` → **`fifo_deduct_chemistry`** | `ChemistryBatch`: **`order_by('created_at', 'id')`**. |

Вспомогательно:

- `material_stock_kg` / `chemistry_stock_kg` — сумма `quantity_remaining`.
- `simulate_fifo_cost_kg` / `simulate_chemistry_fifo_cost_kg` — без записи в БД.
- **`reverse_stock_deductions(reason, reference_id)`** / **`reverse_chemistry_deductions`** — откат: прибавить количество к партиям, удалить строки списания с данной парой reason/reference_id.

**Упаковка склада ГП** (не сырьё/химия): `apps/warehouse/packaging.py` — **`plan_fifo_pack`** (FIFO строк склада по условиям отбора); вызывается из `WarehouseBatchViewSet.package`.

---

## 4. Защиты (как в коде)

**Двойное списание по производству**  
Один контур списания с привязкой **`reason='production_batch'`** и **`reference_id=id партии`**. Перед пересчётом вызывается **`reverse_production_batch_stock`** (удаление всех таких строк). Новое создание партии вызывает `apply` один раз.

**Идемпотентность создания партии**  
`BatchViewSet.create`: заголовки **`X-Request-Id`** или **`Idempotency-Key`** — при повторе возвращается та же партия из кеша (24 ч), повторного `apply` нет.

**Партия без списания при ненулевой норме**  
**`assert_production_batch_ready_for_otk_pipeline`**: если по рецепту и `total_meters` есть положительная потребность (`production_batch_has_positive_material_requirement`), то при **`material_cost_total <= 0`** и отсутствии строк `MaterialStockDeduction` и `ChemistryStockDeduction` с `production_batch` и `reference_id` партии — **`INCOMPLETE_PRODUCTION_COST`**.

**Провал до списания**  
`apply_production_batch_stock_and_cost` сначала проверяет остатки; при недостатке — `ValidationError`, **записи списаний не создаются** (партия могла уже быть создана в той же транзакции — для create это одна транзакция: при ошибке откат create).

**Нулевая себестоимость при нулевых нормах**  
Если по рецепту при данных метрах **нет** положительных требований к сырью/химии, блок про себестоимость/движения **не** выполняется — партия теоретически может иметь `material_cost_total = 0` и идти в ОТК (риск логики рецепта).

**Сырьё для химии**  
В `produce_chemistry` проверка остатков **до** транзакции без `select_for_update` по партиям сырья; затем атомарная транзакция `fifo_deduct` — классическое окно между проверкой и списанием при параллельных запросах (минимизируется только полной атомарностью второй фазы; первая фаза не в одной транзакции с FIFO).

---

## 5. Ключевые API (что делает → какие функции)

Маршруты из `config/api_urls.py`.

### ProductionBatch create  
**`POST /api/batches/`**  
`ProductionBatchCreateUpdateSerializer.create` → **`apply_production_batch_stock_and_cost`**.

### RecipeRun «submit» (создание/обновление партии и списание)

- **`POST /api/production/recipe-runs/`** — после `RecipeRunWriteSerializer.save()` → **`submit_recipe_run_to_otk`** → при новой партии **`apply_production_batch_stock_and_cost`** или при существующей pending — **`resync_production_batch_consumption`**.
- **`POST /api/production/recipe-runs/{id}/submit-to-otk/`** — то же **`submit_recipe_run_to_otk`**.

### OTK accept  
**`POST /api/batches/{id}/otk_accept/`**  
`assert_production_batch_ready_for_otk_pipeline` → `OtkCheck.objects.create` → обновление `ProductionBatch` → при `accepted > 0` **`WarehouseBatch.objects.create`**.

### Warehouse reserve  
**`POST /api/warehouse/batches/reserve/`**  
Проверки количества и статуса → **`batch.status = reserved`**, `save(update_fields=['status'])`**; аудит `schedule_entity_audit`. Бизнес-функций кроме сохранения нет.

### Sale create  
**`POST /api/sales/`**  
`SaleSerializer.create` → **`apply_sale_to_warehouse_batch(wb_pk, quantity, stock_form, piece_pick)`** внутри `transaction.atomic` после `super().create`.

---

## 6. Важные правила и риски

| Правило | Где в коде |
|---------|------------|
| Единственная точка FIFO-списания сырья/химии под **партию профиля** | **`apply_production_batch_stock_and_cost`** (`batch_stock.py`) |
| Факт производства профиля с деньгами по материалам | Строки **`MaterialStockDeduction` / `ChemistryStockDeduction`** с **`reason='production_batch'`** и **`reference_id`** партии + **`material_cost_total`** на `ProductionBatch` |
| Факт выпуска химии | **`ChemistryBatch`** + списания сырья **`reason='chemistry_batch_produce'`**, **`reference_id`** = id партии химии |
| Факт готовой продукции на складе | **`WarehouseBatch`** после ОТК с **`accepted > 0`** |
| Рецепт профиля | Только нормы; списание только при расчёте партии | `RecipeViewSet` + `batch_stock` |

**Спорные / неочевидные места**

1. **`RecipeRunBatchComponent`** не участвует в `aggregate_consumption_for_recipe` — расхождение плана замеса и фактического FIFO, если UI вводит другие количества.
2. Объём замеса для партии завязан на **`output_quantity`** рецепта (устаревшее поле) или явный **`quantity`** в теле — не на `pieces`/`length_per_piece` в привычном смысле (для замеса принудительно `pieces=1`).
3. Докстринг `fifo_deduct` vs фактический `order_by` (см. раздел 3).
4. Резерв склада только на **100%** строки — иначе ошибка валидации.
5. Продажа с зарезервированной партией: отказ в **`apply_sale_to_warehouse_batch`** если статус не `available`.
6. Рецепт с нулевыми нормами на все компоненты: ОТК возможен без движений и с нулевой себестоимостью.

---

*Конец документа V2.*
