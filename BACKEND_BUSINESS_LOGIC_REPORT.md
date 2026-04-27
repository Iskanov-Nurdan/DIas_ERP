# Технический отчёт: backend ERP (DIas)

Источник: только анализ кода в `c:\Users\abyto\Documents\GitHub ADAM\DIas_ERP`.  
Базовый префикс API: `/api/` (см. `config/urls.py`, `config/api_urls.py`).

---

## 1. Django apps

| App | Путь | Назначение | Главные модели |
|-----|------|------------|----------------|
| `apps.accounts` | `apps/accounts/` | Пользователи, роли, RBAC (вкладки UI через `UserAccess`) | `User`, `Role`, `RoleAccess`, `UserAccess` |
| `apps.materials` | `apps/materials/` | Справочник сырья, приходы, FIFO-списания | `RawMaterial`, `MaterialBatch`, `MaterialStockDeduction` |
| `apps.chemistry` | `apps/chemistry/` | Справочник химии, состав, задания, партии выпуска, списания | `ChemistryCatalog`, `ChemistryRecipe`, `ChemistryBatch`, `ChemistryStockDeduction`, `ChemistryTask`, `ChemistryTaskElement` |
| `apps.recipes` | `apps/recipes/` | Профили и рецептуры (нормы на 1 м) | `PlasticProfile`, `Recipe`, `RecipeComponent` |
| `apps.production` | `apps/production/` | Линии, смены, **заказ на производство** (внутренний), партии, замесы | `Line`, `LineHistory`, `Order`, `ProductionBatch`, `Shift`, `ShiftNote`, `ShiftComplaint`, `RecipeRun`, `RecipeRunBatch`, `RecipeRunBatchComponent` |
| `apps.warehouse` | `apps/warehouse/` | Склад готовой продукции (после ОТК) | `WarehouseBatch` |
| `apps.sales` | `apps/sales/` | Клиенты, **заявки клиентов**, продажи, оплаты, возвраты, брак, переделка, прайсы, резервы | `Client`, `Order` (клиентский, таблица `client_orders`), `OrderLine`, `OrderReservation`, `Sale`, `SaleLine`, `Payment`, `Return`, `ReturnLine`, `DefectRecord`, `ReworkRequest`, `PriceList`, `ProductPrice`, `ClientPrice`, `Shipment` |
| `apps.otk` | `apps/otk/` | Сущность проверки ОТК (связь с партией) | `OtkCheck` |
| `apps.analytics` | `apps/analytics/` | Агрегаты и отчёты (моделей нет) | — (`apps/analytics/models.py` пустой) |
| `apps.activity` | `apps/activity/` | Журнал аудита | `UserActivity`, `AuditOutbox` |
| `apps.realtime` | `apps/realtime/` | WebSocket/push (не ORM в отчёте) | — |

---

## 2. Модели (сводно)

**Обозначения:** FK — `ForeignKey`, O2O — `OneToOne`, M2M — `ManyToMany`.

### 2.1 `apps/accounts/models.py`

| Модель | Поля (основные) | Связи | Кто создаёт / где | Статусы | Методы / правила |
|--------|------------------|------|-------------------|---------|------------------|
| `Role` | `name`, `description`, `is_system` | — | Сид/админ | — | `UniqueConstraint` на один `is_system=True` |
| `RoleAccess` | `access_key` | FK → `Role` | Назначение роли | — | `unique_together (role, access_key)` |
| `UserAccess` | `access_key` | FK → `User` | API `UserAccessPatchSerializer`, админ | — | `unique_together (user, access_key)`; меню: `User.get_access_keys()` в `users/models.py` |
| `User` | `name`, `email`, `role`, `is_system`, `is_active`, `is_staff` | FK → `Role` | `UserSerializer.create`, суперпользователь | — | `get_access_keys()`: только `UserAccess`, иначе супер — полный `ACCESS_KEYS` из `config/settings.py` |

### 2.2 `apps/materials/models.py`

| Модель | Поля | Связи | Где | Статусы | Методы |
|--------|------|------|-----|---------|--------|
| `RawMaterial` | `name`, `unit`, `min_balance`, `is_active`, `comment` | — | CRUD `RawMaterialViewSet` | — | — |
| `MaterialBatch` (приход) | `quantity_initial`, `quantity_remaining`, `unit_price`, `total_price`, `received_at`, … | FK → `RawMaterial` | `IncomingViewSet` (POST) | — | `clean`: остаток 0…initial; `save`: `total_price = q * unit_price` |
| `MaterialStockDeduction` | `quantity`, `unit_price`, `line_total`, `reason`, `reference_id` | FK → `MaterialBatch` | `apps/materials/fifo.py` → `fifo_deduct` | — | Списание FIFO; откат `reverse_stock_deductions(reason, reference_id)` |

### 2.3 `apps/chemistry/models.py`

| Модель | Поля | Связи | Где | Статусы | Методы |
|--------|------|------|-----|---------|--------|
| `ChemistryCatalog` | `name`, `unit`, `min_balance`, `is_active`, `comment` | — | CRUD каталога | — | — |
| `ChemistryRecipe` | `quantity_per_unit` | FK `chemistry` → `ChemistryCatalog`, FK `raw_material` | Сериализатор каталога / админ | — | `unique_together (chemistry, raw_material)` |
| `ChemistryBatch` | `quantity_produced`, `quantity_remaining`, `cost_total`, `cost_per_unit` | FK `chemistry`, FK `produced_by`, FK `source_task` | `produce_chemistry` (`apps/chemistry/produce.py`), confirm task | — | `clean` остатка; `save`: `cost_per_unit` от `cost_total/quantity_produced` |
| `ChemistryStockDeduction` | как у сырья | FK → `ChemistryBatch` | `apps/chemistry/fifo.py` `fifo_deduct_chemistry` | — | FIFO по партиям химии |
| `ChemistryTask` | `name`, `status`, `deadline`, `quantity`, `unit` | FK → `ChemistryCatalog` | CRUD | `pending`, `in_progress`, `done` | — |
| `ChemistryTaskElement` | `quantity`, `unit` | FK `task`, FK `chemistry` | **Только БД/админ**; в публичных сериализаторах заданий не используется | — | — |

### 2.4 `apps/recipes/models.py`

| Модель | Поля | Связи | Где | Методы |
|--------|------|------|-----|--------|
| `PlasticProfile` | `name`, `code`, `comment`, `is_active` | — | `PlasticProfileViewSet` | `UniqueConstraint` на `code` |
| `Recipe` | `recipe`, `product`, `base_unit`, `output_quantity` (legacy), `output_unit_kind` (legacy), `is_active` | FK → `PlasticProfile` | `RecipeViewSet` | `save`: автозаполнение `product` из профиля; `delete`: снимки на `production.Order` и `RecipeRun` |
| `RecipeComponent` | `type` (raw/chem), `quantity_per_meter`, `unit` | FK `recipe`, опционально `raw_material` / `chemistry` | Создаётся в `RecipeViewSet.perform_create/update` | — |

### 2.5 `apps/production/models.py`

| Модель | Поля / статусы | Связи | Кто создаёт |
|--------|----------------|------|-------------|
| `Line` | `name`, `code`, `is_active` | — | `LineViewSet` |
| `LineHistory` | `action` (open/close/params/pause/resume), `date`, `time`, геометрия, `comment` | FK `line`, `user` | `LineViewSet` open/close/params/pause/resume, `ShiftViewSet` при закрытии с линией |
| `Order` (**производство**, табл. `orders`) | `status`: created / in_progress / done | FK `recipe`, `line`, `operator` | `submit_recipe_run_to_otk` в `apps/production/views.py` — `Order.objects.create(...)`; **не** клиентская заявка |
| `ProductionBatch` | `otk_status`, `lifecycle_status`, `pieces`, `length_per_piece`, `total_meters`, `material_cost_total`, снимки смены | FK `order`, `profile`, `recipe`, `line`, `shift`, `operator` | `POST /api/batches/`, `RecipeRunViewSet` + `submit_recipe_run_to_otk` |
| `Shift` | `status` open/paused/closed | FK `line`, `user` | `LineViewSet` open, `POST /api/shifts/open/` |
| `ShiftComplaint` | `body` | FK `author`, `shift`, M2M `mentioned_users` | `ShiftComplaintViewSet` |
| `ShiftNote` | `text` | FK `shift`, `user` | `POST /api/shifts/notes/` |
| `RecipeRun` | снимки рецепта/линии | FK `recipe`, `line`, O2O `production_batch` | `POST /api/production/recipe-runs/` |
| `RecipeRunBatch` | `index`, `label`, `quantity` | FK `run` | Вложенно в `RecipeRunWriteSerializer` |
| `RecipeRunBatchComponent` | `quantity`, снимки имён | FK `batch` (run batch), FK `recipe_component`, raw/chem | Запись из UI замеса |

`ProductionBatch.save`: `recompute_totals`, `cost_per_meter` / `cost_per_piece` от `material_cost_total`.  
`Line.delete`: снимки на заказы/замесы/смены.

### 2.6 `apps/warehouse/models.py`

`WarehouseBatch`: `status` (available/reserved/shipped), `inventory_form`, `quality` (good/defect), количество шт, длина, остатки, снимок ОТК, FK `profile`, FK `source_batch` → `ProductionBatch`.  
`save`: пересчёт `total_meters` из `quantity * length_per_piece` при `quality=good` очищает `defect_reason`.

Создание: `create_warehouse_batches_from_otk` в `apps/warehouse/receipt.py` после `POST /api/batches/{id}/otk_accept/`.

### 2.7 `apps/sales/models.py` (клиентский контур)

Ключевые: `Client`; `Order` (табл. `client_orders`) + `OrderLine` + `OrderReservation`; `Sale` + `SaleLine`; `Payment`; `Return` + `ReturnLine`; `DefectRecord`; `ReworkRequest`; `PriceList`, `ProductPrice`, `ClientPrice`; `Shipment` — **нет** ViewSet в `config/api_urls.py` (только админка `apps/sales/admin.py`).

Свойства: `OrderLine.remaining_quantity`, `line_total`, `Order.total_amount` и т.д. — в модели, без `clean` на уровне ORM для всего контура (валидация в сериализаторах/сервисах).

### 2.8 `apps/otk/models.py`

`OtkCheck` — FK → `production.ProductionBatch`, инспектор, accepted/rejected, `check_status`. Создаётся в `BatchViewSet.otk_accept` (`apps/production/views.py`).

### 2.9 `apps/activity/models.py`

`UserActivity` — аудит; `AuditOutbox` — повтор при сбое.

---

## 3. API endpoints (сводка)

Глобально: `REST_FRAMEWORK` в `config/settings.py` — JWT, пагинация `StandardResultsSetPagination`, `filterset_fields` + `search` + `ordering` где задано.

### 3.1 Auth (без `/api/` в части путей дубли в `config/urls.py`)

| URL | Метод | View | Права |
|-----|-------|------|--------|
| `/api/auth/login` | POST | `LoginView` | `LoginRateThrottle`, без JWT |
| `/api/me` | GET | `MeView` | `IsAuthenticated` (по умолчанию) |
| `/api/auth/logout` | POST | `LogoutView` | с JWT (refresh в теле) |

### 3.2 Роутер `config/api_urls.py` (все с префиксом `/api/`)

**accounts:** `UserViewSet` — CRUD; `PATCH /api/users/{id}/access/` — `UserAccessPatchSerializer`. `RoleViewSet` — CRUD.  
`Permission`: `IsAdminOrHasAccess`, ключи `users` / по view.

**materials:** `raw-materials` CRUD; `incoming` GET/POST; `materials/balances` GET list; `materials/movements` GET list. Ключ `materials`. Фильтры: см. `RawMaterialViewSet` (`filterset_fields`), `MaterialBatchFilter` на incoming.

**chemistry:** `chemistry/elements` CRUD + **`POST /api/chemistry/elements/produce/`**; `chemistry/tasks` CRUD + **`POST .../confirm/`**; `chemistry/balances` list; `chemistry/batches` read-only. Ключ `chemistry`.

**recipes:** `plastic-profiles` CRUD; `recipes` CRUD + **`GET /api/recipes/{id}/availability/?mode=...&total_meters=...`**. Ключ `recipes`.

**production:** `lines` CRUD + actions: `open`, `close`, `shift-params`, `shift-pause`, `shift-resume`, `GET history`, `GET {id}/history`, `GET {id}/history/session?open_event_id=`; `batches` CRUD + **`submit-for-otk`**, **`otk_accept`**; `production/recipe-runs` create/patch/delete + **`submit-to-otk`**; `shifts` read + `open`/`close` (detail=False), `GET my`, `GET notes`/`POST notes`, `GET {id}/notes`. Отдельно: `GET /api/shifts/history/`, `GET|POST /api/shifts/complaints/`.  
Права: `IsAdminOrHasAccess` (lines — `lines`; shifts — `my_shift`); партии/замес — `IsAdminOrHasProductionOrOtk` (`apps/accounts/views.py` не используется для батчей — см. `config/permissions.py`).

**warehouse:** `warehouse/batches` read + **`POST reserve`**, **`POST package`**, **`GET {id}/trace`**. Ключ `warehouse`. Query: `debug=1` на list — не скрывать test-продукты.

**sales:** `clients`, `orders`, `sales`, `payments`, `returns`, `defects`, `rework-requests`, `price-lists`, `client-prices`, `order-reservations` (read-only), `client-financial-summary` (read). Ключи: `clients`, `client_orders`, `sales`, `payments`, `returns` и т.д. — см. каждый `required_access_key` в `apps/sales/views.py`. Много кастомных `action` (см. grep `@action` в файле): cancel, waybill, status, reserve, select-sources, complete, writeoff, sell, start, …

**otk:** `GET /api/otk/pending/` — `OtkPendingView`. Ключ `otk`. Реальная приёмка: **`POST /api/batches/{id}/otk_accept/`** (другой app).

**analytics:** `GET` list на viewsets `Analytics*` — без отдельных моделей, ключ `analytics` (см. `apps/analytics/views.py`).

**activity:** `GET /api/activity/my/`, `GET /api/activity/my/{pk}/`, `GET /api/activity/`, `GET /api/activity/{pk}/`.

**Алиасы без `/api/`** (`config/urls.py`): `users/.../`, `warehouse/pack.../`, `batches/pack_from_otk/` — те же `UserViewSet` / `package`.

### 3.3 Типовые query params (списки)

- Пагинация: `page`, `page_size` (см. `config/pagination.py`).
- Поиск: `search` (где `search_fields` заданы).
- Сортировка: `ordering`.
- Фильтры: DRF + `django_filters` — поля из `filterset_fields` / `FilterSet` у каждого ViewSet.
- `lines` list: `eligible_for_recipe_run`, `eligible_for_production_batch` — true/1/yes.
- `shifts` list: `date_from`, `date_to`, `line`, `user`.

### 3.4 Что создаёт / меняет / возвращает (коротко по цепочке)

| Endpoint | Создаёт / меняет | Возвращает |
|----------|------------------|------------|
| `POST /api/batches/` | `ProductionBatch` + FIFO списание `MaterialStockDeduction` / `ChemistryStockDeduction` (`batch_stock.apply_production_batch_stock_and_cost`) | Партия с расчётными метрами/себестоимостью |
| `POST /api/batches/{id}/submit-for-otk` | `lifecycle_status` → otk, флаги очереди, снимок смены | Партия |
| `POST /api/batches/{id}/otk_accept` | `OtkCheck`, обновление партии, `WarehouseBatch`×1–2, при `order_id` — `production.Order.status=done` | Партия |
| `POST /api/chemistry/elements/produce/` | `ChemistryBatch` + списание сырья | Партия химии |
| `POST /api/orders/` (клиент) | `client_orders` + строки | Заявка |
| `PATCH /api/orders/{id}/status/` | Статус заявки (state machine) | Заявка |
| `POST /api/orders/{id}/reserve/` | `OrderReservation`, обновление `reserved_quantity` на строке | Резерв |
| `POST /api/sales/` | `Sale` (+ склад при мультилинейной логике в сериализаторе) | Продажа |

---

## 4. Serializers (обзор)

Файлы: `apps/*/serializers.py`, `config/*` — нет.

| Файл | Классы | read-only / write-only / особое |
|------|--------|-----------------------------------|
| `accounts/serializers.py` | `UserSerializer` — `password` write_only; `UserAccessPatchSerializer` — `access_keys`; `MeSerializer` | `validate_name`, `create` генерирует email; системный пользователь защищён |
| `materials/serializers.py` | `RawMaterialSerializer`, `MaterialBatchSerializer` | Остаток при приходе задаётся равным количеству; единицы g/kg |
| `chemistry/serializers.py` | `ChemistryCatalogSerializer` — `recipe_lines` в create/update из тела; `ChemistryTaskSerializer`, `ChemistryProduceSerializer`, `ChemistryBatchSerializer` | `produce` / `confirm` ведут в `produce_chemistry` |
| `recipes/serializers.py` | `PlasticProfileSerializer`, `RecipeSerializer` / list-варианты | Вложенные `components` на чтение; запись компонентов в `RecipeViewSet`, не в сериализаторе |
| `production/serializers.py` | `ProductionBatchCreateUpdateValidator` путь: **`ProductionBatchCreateUpdateSerializer`**: `create` вызывает `apply_production_batch_stock_and_cost`; `update` — `resync_...` при смене полей; **`BatchListSerializer`** — ОТК/список; **`RecipeRunWriteSerializer`**, list/detail | Строгий whitelist полей create; нельзя менять ключевые поля после создания |
| `warehouse/serializers.py` | `WarehouseBatchSerializer` | — |
| `sales/serializers.py` | `OrderSerializer`, `SaleSerializer`, `PaymentSerializer`, `ReturnSerializer`, `DefectRecordSerializer`, `ReworkRequestSerializer` и др. | Много `validate` / `create` / `update` (списание склада, кредитный лимит) — смотреть по классу в файле (объём большой) |
| `otk/serializers.py` | при наличии | В проекте основной поток ОТК без отдельного create-сериализатора — тело в `BatchViewSet.otk_accept` |
| `activity/serializers.py` | для activity views | read для журналов |

**Полный перечень классов** (имя → файл; детали полей — `Meta.fields` / `fields =` в исходнике):

| Класс | Файл | read-only / write-only / логика |
|-------|------|----------------------------------|
| `RoleAccessSerializer`, `RoleSerializer` | `apps/accounts/serializers.py` | `RoleSerializer`: `is_system` read_only; `validate_name` — зарезервированное имя |
| `UserSerializer` | `apps/accounts/serializers.py` | `password` write_only; `create` — email автогенерация; `update` — блок системного пользователя |
| `UserAccessPatchSerializer` | `apps/accounts/serializers.py` | `access_keys` — полная замена `UserAccess`; `validate_access_keys` против `ACCESS_KEYS` |
| `MeSerializer` | `apps/accounts/serializers.py` | Профиль текущего пользователя |
| `RawMaterialSerializer`, `MaterialBatchSerializer` | `apps/materials/serializers.py` | `MaterialBatchSerializer`: `quantity_remaining` read_only на выходе; `create` ставит остаток = приход; `validate`, `to_representation` — единицы g/kg |
| `ChemistryRecipeLineSerializer`, `ChemistryCatalogListSerializer`, `ChemistryCatalogSerializer` | `apps/chemistry/serializers.py` | Каталог: `validate_unit`, `validate_min_balance`, CRUD `recipe_lines` в `create`/`update` |
| `ChemistryProduceSerializer` |同上| Поля `chemistry_id`, `quantity`, `comment` — для `POST .../produce/` |
| `ChemistryTaskSerializer` |同上| CRUD заданий |
| `ChemistryBatchSerializer` |同上| Партии выпуска, read поля остатка |
| `PlasticProfile*`, `Recipe*`, `RecipeComponentSerializer` | `apps/recipes/serializers.py` | `RecipeSerializer` — вложенные `components` read; nested для профиля |
| `WarehouseBatchSerializer` | `apps/warehouse/serializers.py` | Карточка склада ГП + вычисления в `to_representation` при необходимости |
| `LineSerializer`, `ShiftSerializer`, `ShiftDetailSerializer`, `LineHistorySerializer`, `ShiftComplaint*`, `LineShift*` | `apps/production/serializers.py` | Много `SerializerMethodField` / смена; `LineShiftOpenSerializer` — геометрия открытия |
| `ProductionBatchCreateUpdateSerializer`, `BatchListSerializer`, `ProductionBatchSerializer` |同上| См. §2.5 и §8; `BatchListSerializer` — список/ОТК |
| `RecipeRunWriteSerializer`, `RecipeRun*Serializer` |同上| `RecipeRunWriteSerializer` — compose: `batches` + `line` + `recipe`; вложенные input-сериализаторы |
| `ClientSerializer`, `OrderLineSerializer`, `OrderSerializer`, `PaymentSerializer`, `SaleLineSerializer`, `SaleSerializer`, `Return*`, `DefectRecordSerializer`, `ReworkRequestSerializer`, `PriceListSerializer`, `ProductPriceSerializer`, `ClientPriceSerializer`, `OrderReservationSerializer`, `ClientHistorySerializer` | `apps/sales/serializers.py` | Крупный файл: много `validate_*`, `create`, `update` — списание склада, многострочные продажи, возвраты (см. строки классов в файле) |
| `OtkCheckSerializer` | `apps/otk/serializers.py` | Под схему/админ; основной поток ОТК — тело в `BatchViewSet` |
| `UserActivitySerializer` | `apps/activity/serializers.py` | Журнал |

**Детализация `validate` / `create` / `update` по каждому классу:** см. исходный файл; отчёт фиксирует: критичные — `ProductionBatchCreateUpdateSerializer`, `UserAccessPatchSerializer`, `RecipeViewSet` + тело `components` на уровне view, `SaleSerializer` (списание ГП, многострочные продажи).

### 4.1 Стандартные маршруты роутера (`DefaultRouter`)

Для каждого `router.register(...)` доступны (если не переопределено `http_method_names`):  
`GET/POST` list, `GET/PUT/PATCH/DELETE` detail `{pk}/`. Исключения: `IncomingViewSet` — только `get`+`post`; `RecipeRunViewSet` — без `put`; `WarehouseBatchViewSet` — read-only; `OrderReservationViewSet` — read-only; `ShiftComplaintViewSet` — get+post.

---

## 5. Бизнес-поток ERP (как в коде)

> Две разных сущности «заказ/заявка»: `sales.Order` (клиент) и `production.Order` (внутренняя). Связи между ними **не** делаются автоматически в `POST /api/batches/`.

| Этап | Модель | Endpoint | Что происходит |
|------|--------|----------|----------------|
| Клиентская заявка | `sales.Order`, `OrderLine` | `POST/GET/PATCH /api/orders/`, `PATCH /api/orders/{id}/status/` | Намерение, статусы `state_machine.ORDER_TRANSITIONS` |
| «Проверка сырья/химии» (доступность) | агрегаты `material_stock_kg` / `chemistry_stock_kg` | `GET /api/materials/balances/`, `GET /api/chemistry/balances/`, `GET /api/recipes/{id}/availability/` | Только чтение / симуляция |
| Производство партии (прямой путь) | `ProductionBatch` | `POST /api/batches/` | FIFO списание по `RecipeComponent` × `total_meters` |
| Производство через замес | `RecipeRun` → `ProductionBatch` + `production.Order` | `POST /api/production/recipe-runs/` / `submit-to-otk` | Создаётся внутренний `Order`, партия, списание |
| В очередь ОТК | `ProductionBatch` | `POST .../submit-for-otk` | `lifecycle_status=otk` |
| ОТК | `OtkCheck` | `GET /api/otk/pending/`, `POST /api/batches/{id}/otk_accept` | Склад ГП, статус партии, при `batch.order_id` — `production.Order` → done |
| Склад ГП | `WarehouseBatch` | `GET /api/warehouse/batches/`, `POST .../package`, `POST .../reserve` | Остатки в поле `quantity` строки |
| Продажа | `Sale`, `SaleLine` | `POST /api/sales/` | Списание ГП, себестоимость; синх с заявкой через `order_sync` при `linked_order` |
| Оплата | `Payment` | `POST /api/payments/` | Денежные движения |
| Возврат | `Return`, `ReturnLine` | `POST /api/returns/`, `complete` | Может писать `DefectRecord` |
| Брак / переделка | `DefectRecord`, `ReworkRequest` | `defects`, `rework-requests` + actions | См. `apps/sales/views.py` actions |

**Куда движется объект:** `ProductionBatch`: pending → (submit) otk → (otk_accept) done + строки `WarehouseBatch`; клиентская заявка: статусы до `closed` / `canceled` независимо от завода, пока `Sale` / резервы не увязаны.

---

## 6. Остатки и списания

| Вопрос | Реализация |
|--------|------------|
| Остаток сырья | `Sum(quantity_remaining)` по `MaterialBatch` для `material_id`; хелпер `material_stock_kg` в `apps/materials/fifo.py`. UI-список: `GET /api/materials/balances/`. |
| Остаток химии | `ChemistryBatch.quantity_remaining` по каталогу; `chemistry_stock_kg` в `apps/chemistry/fifo.py`. |
| Готовая продукция | Суммарно по строкам `WarehouseBatch` (шт/метры в модели); нет отдельной агрегирующей таблицы — только выборки. |
| FIFO | **Да** для сырья (`order_by received_at, created_at, id`) и химии (`ChemistryBatch` — порядок в `chemistry/fifo.py`). |
| Когда списание | **Сырьё/химия в производство:** в момент `apply_production_batch_stock_and_cost` при `POST /api/batches/` и при пересчёте `resync_...` / `RecipeRun` + создании партии. **Сырьё в выпуск химии:** `produce_chemistry`. **Списание не через RecipeRun дублирование** — `recipe_run_consumption_applied` помечен устаревшим в модели. |
| Списать больше остатка | `fifo_deduct` / `apply_production_batch_stock_and_cost` — проверка остатка до цикла; `ValidationError` / `INSUFFICIENT_STOCK`. |

**Склад ГП:** списание при продаже — логика в `apps/sales/sale_warehouse.py` (подключается из `SaleViewSet` / сериализатора; детализация в том файле). Резерв: `order-reservations` + поля `reserved_quantity` на `OrderLine`.

---

## 7. Заявки (клиентские, `sales.Order`)

- **Создание:** `POST /api/orders/` + `OrderSerializer` — `order_number`, `date`, `client`, строки, `source_type`, и т.д.
- **Подтверждение:** смена статуса `PATCH /api/orders/{id}/status/` с `validate_order_transition` — первый «жёсткий» шаг `new` → `confirmed` (и далее по таблице в `state_machine.py`).
- **Проверка сырья/химии по заявке:** **нет** прямой автопроверки в модели заявки; планировщик смотрит остатки/availability отдельными GET.
- **Попадание в производство:** **не** автоматизировано: производственные `ProductionBatch` не получают FK на `sales.Order` в текущем `ProductionBatchCreateUpdateSerializer` (только `profile`, `recipe`, `line`, …). Связь коммерция↔завод — вручную/процесс вне схемы или через продажи/резервы.
- **Автозаполнение vs ручной выбор:** в заявке — поля в сериализаторе; `retrieve` отдаёт `available_status_transitions`, `available_actions`. Резерв: `POST /api/orders/{id}/reserve/` с выбором `warehouse_batch_id` и `order_line_id`.
- **Статусы заявки:** `ORDER_TRANSITIONS` + `validate_order_for_new_status` при переходах в отгружено/закрыто (`order_sync.py`).

---

## 8. Производство (детально)

| Вопрос | Ответ в коде |
|--------|--------------|
| Старт | Открытая **не**на паузе смена на линии + `Shift` текущего пользователя на эту линию (`ProductionBatchCreateUpdateSerializer.validate`). |
| Профиль / рецепт | Передаётся в теле: `profile`, `recipe`; проверка `recipe.profile_id == profile.pk`. |
| Длина / количество | `pieces` × `length_per_piece` → `recompute_totals` → `total_meters` (и норма рецепта **на 1 м**). |
| Линия | Поле `line` в create; должна совпадать с открытой сменой пользователя. |
| Списание компонентов | При `save` новой партии: `apply_production_batch_stock_and_cost`. |
| После завершения (ОТК) | `create_warehouse_batches_from_otk`, опционально обновление `production.Order` если партия с `order_id` (только путь с замесом, где order создан). |

**Замес `RecipeRun`:** план ёмкостей (`RecipeRunBatch*`) + при создании/сабмите — та же `ProductionBatch` и то же FIFO, что и `POST /batches/`.

---

## 9. Проблемы и риски (по коду)

**Работает согласованно**

- Единая точка FIFO для производства: `batch_stock.py` + `reverse_production_batch_stock` при откате/удалении замеса.
- Раздельные таблицы клиентского заказа и производственного заказа; снимки имён при удалении рецепта/линии.
- Кредит / резерв / синхронизация отгрузок — централизованы в `sales` (сериализаторы + `order_sync`).

**Риски / несоответствия**

- Два разных `Order` — путаница в названиях и интеграциях; **нет** автосвязи клиентская заявка → партия.  
- `POST /api/batches/` не создаёт `production.Order` (в отличие от замеса) — разные сценарии «готовности к ОТК».
- `Shipment` в БД, но **нет** REST в `config/api_urls.py` — доставка вне основного API.
- `ChemistryTaskElement` не используется в публичном API.
- `Recipe.output_quantity` — для замеса/ОТК ветки используется в `submit_recipe_run_to_otk`; прямой `POST /batches/` опирается на `pieces`/`length`, не на `output_quantity`.
- Сильная валидация в сериализаторах/вью: изменение бизнес-правил в одном месте без зеркалирования в `clean()` моделей везде.
- `warehouse` reserve на строку: полный остаток строки за раз (см. `WarehouseBatchViewSet.reserve`).

**Возможные «лишние» с точки зрения API**

- `Shipment` (если фронт не ведёт доставки через БД).  
- Legacy поля `Recipe.output_unit_kind` / `Sale` legacy поля — помечены в моделях как устаревшие/совместимость.

**Опасно для данных**

- Прямое изменение `quantity_remaining` в админке обходит FIFO-историю.  
- `DELETE` замеса с откатом списаний — конкурентные правки других партий.  
- Обход кредитного лимита флагом `credit_limit_bypassed` в `Sale` — бизнес-риск.  
- `UserAccess` пустой у не-суперпользователя = нет вкладок (ожидаемое, но критично для UX).

**Недостающие endpoint’ы (если ожидать полный «ERP» в одном REST)**

- CRUD/REST для `Shipment`.  
- Явный мост `sales.Order` → `production` (создать заказ на цех по строке заявки) — отсутствует.  
- Отдельный read-only агрегат «остаток ГП по номенклатуре» (сейчас — клиентский расчёт из `warehouse/batches`).

---

## 10. Frontend contract (вкладки = `ACCESS_KEYS`)

Ключи меню: `config/settings.py` — `ACCESS_KEYS` и `User.get_access_keys()`.

| Вкладка (логич. ключ) | Данные (GET) | Действия (POST/PATCH) | API | Read-only с сервера | Вручную |
|----------------------|--------------|------------------------|-----|----------------------|---------|
| users | `/api/users/`, `/api/roles/` | CRUD user, `PATCH .../access/` | `users`, `users`+роли | `accesses` как выдача | Пароль, набор `access_keys` |
| lines | `/api/lines/`, history | open/close/shift-* | `lines` | `shift_snapshot`, вычисл. поля | Параметры смены при open |
| materials | `raw-materials`, `incoming`, `balances`, `movements` | приход, правка сырья | `materials` | Остатки, FIFO-флаги deletable | Количество прихода, поставщик |
| recipes | `plastic-profiles`, `recipes`, `availability` | CRUD + components через тело | `recipes` | `components` с нормами | Компоненты, норма на м |
| chemistry | `chemistry/elements`, `tasks`, `batches`, `balances` | produce, task confirm | `chemistry` | Баланс в list | Состав химии ( recipe_lines ) |
| orders (цех) | нет отдельного list для `production.Order` в api_urls | — | `orders` в URL — **клиентский** `sales` | — | — |
| production | `batches`, `production/recipe-runs`, `lines` | create batch, submit OTK, recipe-run | `production` + `lines` + `batches` | `total_meters`, cost fields | `pieces`, `length`, выбор line/recipe |
| otk | `otk/pending`, `batches` | `otk_accept` | `otk` + `production` | accepted/defect sum = pieces | OTK количественные поля |
| warehouse | `warehouse/batches` | `reserve`, `package`, `trace` | `warehouse` | cost from batch | Параметры упаковки |
| client_orders | `orders` (sales) | status, reserve, cancel | `client_orders` | `available_actions` в retrieve | Статусы, резервы |
| sales / payments / returns / defects / rework | соответствующие ресурсы | много `action` в `SaleViewSet` / `Defect` / `Rework` | каждый свой ключ | расчёты revenue/cost | Выбор партии склада, оплаты |
| analytics | `/api/analytics/...` | GET | `analytics` | агрегаты | фильтры дат |
| my_shift / shifts | `shifts`, `shifts/complaints`, `shifts/notes` | open/close, complaints | `my_shift` / `shifts` | — | Текст жалоб, заметок |
| activity | `activity/`, `activity/my/` | — | требует договорённости (админ) | события | — |

**Итог:** фронт обязан хранить JWT, слать `Authorization: Bearer`. Пагинация: `page`, `page_size`; ответы списков в формате проекта (см. `REST_FRAMEWORK` + кастомный `StandardResultsSetPagination`).

---

*Конец отчёта. Файлы кода не изменялись.*
