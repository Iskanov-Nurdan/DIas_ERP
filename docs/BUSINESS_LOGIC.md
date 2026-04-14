# Бизнес-логика DIas ERP (бэкенд)

Краткое описание доменных правил и потоков данных. Источник истины — код в `apps/*` и `config/*`. Префикс API: **`/api/`** (см. `config/urls.py` → `include('config.api_urls')`).

---

## 1. Доступ (RBAC)

- Пользователь получает **только** ключи из `UserAccess` (модель `accounts.UserAccess`).
- Суперпользователь **без** строк `UserAccess` получает полный список `ACCESS_KEYS` из `config.settings` (обход для API).
- Эндпоинты проверяют `required_access_key` / `HasAccessKey` / `IsAdminOrHasAccess` (`config/permissions.py`).
- Модуль химии: **`required_access_key = 'chemistry'`** (`apps/chemistry/views.py`).

---

## 2. Сырьё (`materials`)

- **Справочник:** `RawMaterial`.
- **Учёт остатков:** партии **`MaterialBatch`** (`quantity_remaining` в кг), списание **FIFO** по `created_at`, `id` — `apps/materials/fifo.py` (`fifo_deduct`, `material_stock_kg`).
- **Приход** (роутер `incoming`): фактически CRUD над **`MaterialBatch`** — см. `IncomingViewSet` в `apps/materials/views.py`.
- Остаток по материалу: **`SUM(quantity_remaining)`** по партиям, не отдельное поле-агрегат в справочнике.

---

## 3. Химия (`chemistry`) — полуфабрикат между сырьём и профилем

### 3.1 Модели

| Сущность | Файл | Назначение |
|----------|------|------------|
| `ChemistryCatalog` | `apps/chemistry/models.py` | Справочник: `name`, `unit` (нормализуется к `kg`/`g` в сериализаторе), `min_balance`, `is_active` |
| `ChemistryRecipe` | то же | Строка состава: **`quantity_per_unit` = кг сырья на 1 кг химии**; FK `raw_material`, `chemistry`. Таблица БД: `chemistry_composition` |
| `ChemistryBatch` | то же | Партия выпуска: `quantity_produced`, `quantity_remaining`, `cost_total`, `cost_per_unit` (пересчёт в `save()`), `produced_by`, `comment`, опционально `source_task` |
| `ChemistryStockDeduction` | то же | Факт списания химии из партии (FIFO при производстве профиля / замесе): `quantity`, `unit_price`, `line_total`, `reason`, `reference_id` |
| `ChemistryTask` | то же | Задание на выпуск: при **`POST .../confirm/`** вызывается тот же пайплайн, что и produce, с привязкой партии к заданию |

Остаток химии по позиции каталога: **`SUM(ChemistryBatch.quantity_remaining)`** — см. `chemistry_stock_kg()` в `apps/chemistry/fifo.py`. Отдельной таблицы «склад химии» нет.

### 3.2 Алгоритм «Произвести химию»

Реализация: **`produce_chemistry()`** в `apps/chemistry/produce.py`.

1. Проверка: активный `ChemistryCatalog`, **`quantity_kg > 0`**, состав **`ChemistryRecipe` не пустой** — иначе `ValidationError` (в т.ч. код `EMPTY_CHEMISTRY_RECIPE`).
2. Для каждой строки состава: потребность **`quantity_per_unit × quantity_kg`**; сверка с `material_stock_kg(material_id)`.
3. Если не хватает — **`INSUFFICIENT_STOCK`** + массив **`missing`** (как в ответе API).
4. Создаётся **`ChemistryBatch`** с нулевой себестоимостью, затем по строкам состава вызывается **`fifo_deduct(..., reason='chemistry_batch_produce', reference_id=batch.pk)`** (`apps/materials/fifo.py`).
5. **`cost_total`** = сумма фактических списаний сырья; **`cost_per_unit`** = `cost_total / quantity_produced` (в `save()` модели партии).

Прямой выпуск без задания: экшен **`POST /api/chemistry/elements/produce/`** (`ChemistryCatalogViewSet.produce`).

### 3.3 Использование химии в рецепте профиля

- Модель **`RecipeComponent`**: поле **`type`** — `'raw'` | `'chem'` (`TYPE_RAW` / `TYPE_CHEM`), не текст «component_type» — см. `apps/recipes/models.py`.
- Норма: **`quantity_per_meter`** (кг на 1 м профиля) + `unit`.

### 3.4 Списание химии при производстве партии профиля

- **`apply_production_batch_stock_and_cost`** — `apps/production/batch_stock.py`.
- Расход по рецепту: **`aggregate_consumption_for_recipe`** → для `type=chem` агрегируется **`chemistry_id` → кг**.
- Списание: **`fifo_deduct_chemistry(..., reason='production_batch', reference_id=batch.pk)`** — `apps/chemistry/fifo.py` (порядок партий: `created_at`, `id`).
- Себестоимость партии: **`material_cost_total = raw_cost + chem_cost`** (строки 120–134 в `batch_stock.py`).

### 3.5 Замес (`RecipeRun`) и единое списание

- **`RecipeRun`** / **`RecipeRunBatch`** / **`RecipeRunBatchComponent`** — учёт замеса (ёмкости, фактический расход по строкам для экрана/аналитики). **Остатки сырья и химии здесь не списываются.**
- Реальное списание и **`material_cost_total`** — только **`apply_production_batch_stock_and_cost`** в `apps/production/batch_stock.py`: норма рецепта (**`quantity_per_meter`**) × **`total_meters`** партии, `reason='production_batch'`, `reference_id=batch.pk`.
- Связь **`RecipeRun` → `ProductionBatch`** (1:1): при отправке в ОТК создаётся/обновляется партия; пересчёт FIFO выполняется по партии (откат старых движений `production_batch` при смене метража или удалении).

### 3.6 Валидации и ограничения

- Удаление **`ChemistryCatalog`**: если есть **`batches`** — **409** `CHEMISTRY_IN_USE` (`ChemistryCatalogViewSet.destroy`).
- Удаление выполненного **`ChemistryTask`** запрещено (`perform_destroy`).
- Партии химии в API — **только чтение** (`ChemistryBatchViewSet` — `ReadOnlyModelViewSet`).

---

## 4. HTTP API: модуль «Химия»

База: **`/api/`**. Роуты регистрируются в `config/api_urls.py`.

### 4.1 Справочник `ChemistryCatalog` — `chemistry/elements`

| Метод | Путь | Действие |
|-------|------|----------|
| GET | `/api/chemistry/elements/` | Список (фильтры: `unit`, `is_active`; поиск: `name`; сортировка: `id`, `name`) |
| POST | `/api/chemistry/elements/` | Создать элемент + опционально строки состава |
| GET | `/api/chemistry/elements/{id}/` | Один элемент с **`recipe_lines`** |
| PUT/PATCH | `/api/chemistry/elements/{id}/` | Обновить |
| DELETE | `/api/chemistry/elements/{id}/` | Удалить (409 если есть партии) |

**Тело POST (пример):**

```json
{
  "name": "Смесь А",
  "unit": "kg",
  "min_balance": "10.0000",
  "is_active": true,
  "recipe_lines": [
    { "raw_material_id": 1, "quantity_per_unit": "2.0" },
    { "raw_material_id": 2, "quantity_per_unit": "0.1" }
  ]
}
```

Алиас для строк состава при создании: **`compositions`** (см. `ChemistryCatalogSerializer.create` в `apps/chemistry/serializers.py`).

### 4.2 Выпуск химии (без задания)

| Метод | Путь | Тело |
|-------|------|------|
| POST | `/api/chemistry/elements/produce/` | `ChemistryProduceSerializer` |

**Тело:**

```json
{
  "chemistry_id": 1,
  "quantity": "100.0000",
  "comment": "Смена 1"
}
```

**Ответ 201:** сериализованная **`ChemistryBatch`** (`ChemistryBatchSerializer`).

**Ошибки 400:** например `INSUFFICIENT_STOCK`, `EMPTY_CHEMISTRY_RECIPE`, неактивная/несуществующая химия.

### 4.3 Задания `ChemistryTask` — `chemistry/tasks`

| Метод | Путь | Действие |
|-------|------|----------|
| GET | `/api/chemistry/tasks/` | Список |
| POST | `/api/chemistry/tasks/` | Создать задание |
| GET/PATCH/PUT/DELETE | `/api/chemistry/tasks/{id}/` | CRUD (удаление запрещено для `status=done`) |
| POST | `/api/chemistry/tasks/{id}/confirm/` | Выпуск по заданию → `produce_chemistry` + `source_task_id` + статус задания `done` |

**Ответ `confirm`:**

```json
{
  "task": { "...": "ChemistryTaskSerializer" },
  "batch": { "...": "ChemistryBatchSerializer" }
}
```

### 4.4 Остатки и партии

| Метод | Путь | Ответ |
|-------|------|--------|
| GET | `/api/chemistry/balances/` | `{ "items": [ { "element_name", "unit", "balance", "chemistry_id" } ] }` — только активные каталоги с **balance > 0** |
| GET | `/api/chemistry/batches/` | Список партий (фильтр `chemistry`, поиск по `comment`) |
| GET | `/api/chemistry/batches/{id}/` | Одна партия |

---

## 5. Рецепты (`recipes`)

- **`Recipe`**, **`RecipeComponent`** — см. `apps/recipes/models.py`.
- При **удалении рецепта** у связанных заказов и замесов сохраняются `recipe_name_snapshot` и `former_recipe_id`.

---

## 6. Производство (`production`)

### 6.1 Линии и история смены на линии

- **`Line`**, **`LineHistory`** — см. `shift_state.py` и модели.

### 6.2 Заказ на производство

- **`Order`**: статусы, рецепт, линия, снимки.

### 6.3 Партия производства и ОТК

- **`ProductionBatch`**, **`OtkCheck`**, снимки параметров смены для склада ГП.

### 6.4 Личные смены (`Shift`)

- Ограничения на одну открытую смену — в модели/валидации.

### 6.5 Замес по рецепту (`RecipeRun`)

- API: **`/api/production/recipe-runs/`** (не модуль химии). См. п. 3.5: план замеса отдельно, списание — только у **`ProductionBatch`**.

---

## 7. Склад ГП (`warehouse`), продажи (`sales`), аналитика, аудит, realtime

- Логика упаковки и FIFO отбора — `warehouse/packaging.py`, `stock_ops.py`.
- Аналитика — агрегаты в `apps/analytics/views.py` (без отдельных доменных таблиц под сводки).
- Аудит — `apps/activity/audit_service.py`.
- WebSocket — `apps/realtime` (в т.ч. события по партиям/списаниям химии при изменении моделей).

---

## Сводка цепочки «сырьё → химия → профиль»

1. Приход сырья → партии **`MaterialBatch`**.
2. Справочник химии + **`ChemistryRecipe`** (кг сырья на 1 кг химии).
3. **`POST .../elements/produce/`** или **`.../tasks/{id}/confirm/`** → списание сырья FIFO → **`ChemistryBatch`** с фактической себестоимостью.
4. Рецепт профиля: компоненты **`type=raw`** и **`type=chem`**.
5. Партия производства (**`ProductionBatch`**, в т.ч. после замеса и submit-to-otk) → списание **`MaterialBatch`** и **`ChemistryBatch`** FIFO (`reason=production_batch`) → себестоимость в **`ProductionBatch`**. Замес (**`RecipeRun`**) хранит состав по ёмкостям без дублирования FIFO.

---

*Обновляйте документ при изменении моделей и сервисов.*
