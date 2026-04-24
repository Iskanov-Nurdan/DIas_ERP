# DIAS ERP — Полная backend-документация (единый источник истины)

Документ собран по текущему коду backend (`apps/*`, `config/*`) и актуальным миграциям.  
При расхождениях со старыми текстами приоритет у кода и миграций.

---

## 1) Общая структура проекта

### Backend-модули (Django apps)

- `apps/accounts` — пользователи, роли, `UserAccess`, аутентификация JWT, `/api/me`.
- `apps/materials` — справочник сырья, приходы сырья, FIFO-списания сырья, остатки/движения.
- `apps/chemistry` — справочник химии, состав химии из сырья, задачи на выпуск химии, партии химии, FIFO-списания химии.
- `apps/recipes` — профили ГП, рецепты и компоненты рецептов (сырье/химия на 1 метр).
- `apps/production` — линии, история смен по линиям, смены (личные и линейные), партии производства, замесы (`RecipeRun`), жалобы и заметки смен.
- `apps/otk` — очередь партий в ОТК (read API), приемка ОТК выполняется через `production`.
- `apps/warehouse` — склад ГП: строки склада, резервы, упаковка, формы учета, качество (годный/брак).
- `apps/sales` — клиенты, заявки, продажи, оплаты, возвраты, брак, переделки.
- `apps/analytics` — агрегированная аналитика и детализации (выручка, себестоимость, ОТК, списания).
- `apps/activity` — аудит действий (`UserActivity`), outbox для надежной записи аудита.
- `apps/realtime` — WebSocket `/ws/operational`, сигналы и рассылка событий.

### Основные сущности

- Пользователи/RBAC: `User`, `Role`, `RoleAccess`, `UserAccess`.
- Производственный контур: `Line`, `LineHistory`, `Shift`, `ProductionBatch`, `RecipeRun`, `RecipeRunBatch`, `RecipeRunBatchComponent`.
- Материалы/химия: `RawMaterial`, `MaterialBatch`, `MaterialStockDeduction`, `ChemistryCatalog`, `ChemistryRecipe`, `ChemistryTask`, `ChemistryBatch`, `ChemistryStockDeduction`.
- Рецептуры: `PlasticProfile`, `Recipe`, `RecipeComponent`.
- Качество/склад: `OtkCheck`, `WarehouseBatch`.
- Коммерция: `Client`, `Order`, `OrderLine`, `Sale`, `SaleLine`, `Payment`, `Return`, `ReturnLine`, `DefectRecord`, `ReworkRequest`, `Shipment(legacy)`.
- Аудит: `UserActivity`, `AuditOutbox`.

---

## 2) Полная бизнес-логика по разделам

### Пользователи и роли

- Вход: `POST /api/auth/login` по `name + password` (JWT access+refresh).
- Меню/доступы берутся из `UserAccess` (`User.get_access_keys()`).
- `RoleAccess` хранит шаблонные ключи ролей; фактический контроль API идет по ключам пользователя.
- Системные пользователь/роль (`is_system=True`) защищены от изменения/удаления через API.

### Моя смена

- `POST /api/shifts/open/` без `line_id` открывает **личную** смену.
- `POST /api/shifts/close/` без `line_id` закрывает личную смену.
- `GET /api/shifts/my/` возвращает только текущую личную смену.
- `GET/POST /api/shifts/notes/` — заметки текущей личной смены.

### Сотрудники

- Сотрудники — это `accounts.User` (плюс привязка к `Role` и `UserAccess`).
- Участие в процессах: операторы партий, авторы жалоб/заметок, инспекторы ОТК, создатели коммерческих документов.

### Журнал смен

- `LineHistory` фиксирует: `open`, `close`, `params_update`, `shift_pause`, `shift_resume`.
- API:
  - `GET /api/lines/history/` (общая лента),
  - `GET /api/lines/{id}/history/`,
  - `GET /api/lines/{id}/history/session/?open_event_id=...`.

### Линии

- Линию нельзя удалить, если есть открытая смена на линии.
- Открытие/закрытие линейной смены ведет к созданию `Shift` и `LineHistory`.
- Пауза/возобновление смены фиксируются в истории и ставят статус `Shift` (`paused/open`).

### Сырьё

- `RawMaterial` — справочник.
- Приход создает `MaterialBatch`.
- Производство химии/партии ГП создает `MaterialStockDeduction` (FIFO).
- Остатки считаются по партиям (`quantity_remaining`), с конвертацией `kg/g`.

### Химия

- `ChemistryCatalog` — справочник химии.
- `ChemistryRecipe` — состав химии (расход сырья на 1 кг химии).
- Выпуск химии (`/chemistry/elements/produce` или подтверждение задания) списывает сырье FIFO и создает `ChemistryBatch`.

### Рецепты

- Рецепт задает нормы на 1 метр (`quantity_per_meter`) по сырью и/или химии.
- Сам рецепт не делает движений склада.
- Движения делает выпуск партии производства (`ProductionBatch`) по рецепту.

### Профили

- `PlasticProfile` — номенклатура профилей ГП.
- Рецепт всегда привязан к профилю; партия производства валидирует соответствие `recipe.profile == profile`.

### Производство

- Партия ГП (`ProductionBatch`) создается через `POST /api/batches/`.
- При создании:
  - проверяется открытая и не-паузная смена на линии у текущего пользователя,
  - рассчитываются `total_meters`, себестоимость (`material_cost_total`, `cost_per_meter`, `cost_per_piece`),
  - выполняется списание сырья/химии FIFO через `apply_production_batch_stock_and_cost`.
- После отправки в ОТК редактировать можно только `comment`.

### Замесы

- `RecipeRun` и вложенные `RecipeRunBatch*` — план/факт замеса (структура емкостей и компонентов).
- Важно: фактическое складское списание — по `ProductionBatch`; `RecipeRun` не является отдельным источником движения.
- `POST /api/production/recipe-runs/{id}/submit-to-otk/` привязывает замес к партии ОТК.

### ОТК

- Партия переводится в очередь: `POST /api/batches/{id}/submit-for-otk/`.
- Приемка: `POST /api/batches/{id}/otk_accept/` с `otk_accepted` + `otk_defect`.
- Обязательная инварианта: `otk_accepted + otk_defect == pieces`.
- При браке обязательна `otk_defect_reason`.
- Результат создает `OtkCheck`, обновляет статус партии, и формирует складские строки ГП/брака.

### Склад готовой продукции

- `WarehouseBatch` создается из приемки ОТК.
- Есть качество `good/defect`, статус `available/reserved/shipped`, форма учета `unpacked/packed/open_package`.
- Резерв — только на полный остаток строки.
- Упаковка — split строки `unpacked` в строку `packed` с расчетом упаковочных полей.

### Клиенты

- `Client` с расширенными полями контактов.
- Удаление запрещено при наличии продаж (`CLIENT_IN_USE`).

### Продажи

- `Sale` поддерживает piece/package режимы, связь с `WarehouseBatch` и `Order`.
- При продаже со складом:
  - вызывается `apply_sale_to_warehouse_batch`,
  - уменьшается доступный остаток, может измениться статус/форма упаковки строки склада.
- Есть признак `is_defect_sale` для продаж брака.

### Заявки

- `Order` + `OrderLine` — коммерческая заявка, сама по себе склад не двигает.
- Имеет отдельную статусную машину (new → ... → closed/canceled).

### Оплаты

- `Payment` — денежное движение (отдельно от товарного движения).
- Типы: `prepayment`, `payment`, `surcharge`, `refund`.
- Используется для расчета долга/аванса клиента.

### Возвраты

- `Return` обязателен к `Sale`.
- По строкам возврата:
  - `warehouse` — вернуть в склад ГП,
  - `defect` — создать `DefectRecord`,
  - `rework` — создать `DefectRecord` + `ReworkRequest`.

### Брак / переработка / переделка

- `DefectRecord` — учет брака (источник ОТК или возврат).
- Операции: передать в переделку, завершить переделку, продать брак, списать брак.
- `ReworkRequest` связывает возврат/брак с результатной партией ГП после переделки.

### Аналитика

- Все отчеты read-only через `/api/analytics/*`.
- Параметры области (`year/month/day/date_from/date_to`) и разрезы (`line_id/client_id/profile_id/...`).
- Отдельная детализация списаний сырья (`writeoff-details`) по `MaterialStockDeduction`.

### Activity / аудит

- Любые ключевые операции пишутся в `UserActivity` через `schedule_entity_audit`.
- Есть админская и личная ленты, фильтры по сущности/request_id/action/смене/дате.

### WebSocket realtime

- Единый сокет `/ws/operational`.
- События легковесные: resource/action/id(+payload с ключевыми id).
- Фронт после события делает REST refetch нужных ресурсов.

---

## 3) Сущности: модели, таблицы, поля, связи, ограничения, движения

> Ниже перечислены основные поля, связи и логика движения. Для decimal-полей в API используются строковые/точные значения без потери точности.

### RBAC и пользователи

- `Role` (`roles`): `name`, `description`, `is_system`; ограничение: только одна системная роль (`role_single_system_flag`).
- `RoleAccess` (`role_access`): `role`, `access_key`; уникальность `(role, access_key)`.
- `User` (`users`): `name`, `email(unique)`, `role`, `is_system`, `is_active`, `is_staff`; только один системный пользователь (`user_single_system_flag`).
- `UserAccess` (`user_access`): `user`, `access_key`; уникальность `(user, access_key)`.
- Движения склада/денег: не создают.

### Материалы

- `RawMaterial` (`raw_materials`): справочник, `name`, `unit(kg/g)`, `min_balance`, `is_active`, `comment`.
  - Не создает движения.
  - Ограничения: удаление запрещено при использовании в приходах/движениях/рецептах/производстве.
- `MaterialBatch` (`material_batches`): приход партии, `quantity_initial`, `quantity_remaining`, `unit_price`, `total_price`, `received_at`, поставщик.
  - Создает движение `incoming`.
  - Валидации: `0 <= quantity_remaining <= quantity_initial`.
- `MaterialStockDeduction` (`material_stock_deductions`): списание FIFO, `batch`, `quantity`, `unit_price`, `line_total`, `reason`, `reference_id`.
  - Создает движение `writeoff_*`.

### Химия

- `ChemistryCatalog` (`chemistry_catalog`): справочник химии.
- `ChemistryRecipe` (`chemistry_composition`): компонент сырья на 1 кг химии, уникальность `(chemistry, raw_material)`.
- `ChemistryTask` (`chemistry_tasks`): задание на выпуск (`pending/in_progress/done`).
- `ChemistryBatch` (`chemistry_batches`): партия химии (выпуск, остаток, себестоимость).
- `ChemistryStockDeduction` (`chemistry_stock_deductions`): списание химии FIFO.
- Движения:
  - `ChemistryBatch` — приход химии,
  - `ChemistryStockDeduction` — расход химии в производстве.
- Не создают движения: редактирование карточки химии и состава.

### Рецептуры

- `PlasticProfile` (`plastic_profiles`): `name`, `code(unique)`, `is_active`, `comment`.
- `Recipe` (`recipes`): `recipe` (имя), `profile`, `base_unit=per_meter`, legacy-поля `output_quantity/output_unit_kind`, `is_active`.
- `RecipeComponent` (`recipe_components`): тип `raw/chem`, ссылка на `raw_material` или `chemistry`, `quantity_per_meter`.
- Движения: не создают.
- Ограничения:
  - удаление `PlasticProfile` запрещено при наличии рецептов/партий;
  - удаление `Recipe` запрещено при наличии production batches/orders/recipe runs.

### Производство и смены

- `Line` (`lines`): линия.
- `LineHistory` (`line_history`): события смены по линии.
- `Shift` (`shifts`): смена (личная или по линии).
  - Ограничения уникальности открытых смен:
    - 1 открытая личная смена на пользователя,
    - 1 открытая смена на пользователя+линию.
- `ProductionBatch` (`production_batches`): партия ГП, включает статусы ОТК и lifecycle, себестоимость, снапшоты параметров смены.
- `RecipeRun` (`recipe_runs`) + `RecipeRunBatch` + `RecipeRunBatchComponent` — структура замеса.
- `ShiftComplaint` (`shift_complaints`) / `ShiftNote` (`shift_notes`) — коммуникация по сменам.
- Движения:
  - `ProductionBatch` create/update(пересчет) — делает FIFO списания сырья/химии;
  - `submit-for-otk`/`otk_accept` — двигает lifecycle/ОТК и создает складские записи.
- Не создают движения:
  - `LineHistory`, `Shift`, `ShiftNote`, `ShiftComplaint`, `RecipeRun*` сами по себе.

### ОТК

- `OtkCheck` (`otk_checks`): результат проверки партии, accepted/rejected, причина, инспектор.
- Складские последствия приемки управляются в `production` (а не в отдельном API `otk`).

### Склад ГП

- `WarehouseBatch` (`warehouse_batches`):
  - статус: `available/reserved/shipped`,
  - форма учета: `unpacked/packed/open_package`,
  - качество: `good/defect`,
  - упаковочные поля (`pieces_per_package`, `packages_count`, ...),
  - снимки ОТК.
- Движения:
  - создается после ОТК,
  - уменьшается при продаже,
  - увеличивается при возврате на склад,
  - сплитится при упаковке.

### Коммерческий контур

- `Client` (`clients`) — клиент.
- `Order` (`client_orders`) + `OrderLine` (`order_lines`) — заявка.
- `Sale` (`sales`) + `SaleLine` (`sale_lines`) — продажа.
- `Payment` (`payments`) — деньги.
- `Return` (`returns`) + `ReturnLine` (`return_lines`) — возвраты.
- `DefectRecord` (`defect_records`) — учет брака.
- `ReworkRequest` (`rework_requests`) — переделка.
- `Shipment` (`shipments`) — legacy-сущность отгрузки.

### Аудит

- `UserActivity` (`user_activity`) — аудит действий + payload изменений.
- `AuditOutbox` (`audit_outbox`) — очередь ретраев аудита.

---

## 4) API: endpoint’ы, методы, access key, фильтры, payload, ответы, ошибки

## Базовые правила

- Base: `/api/*` (дополнительно есть alias-маршруты без `/api` для совместимости).
- Auth: Bearer JWT.
- Пагинация списков: `items + meta + links`.
- Поиск/фильтрация: DRF `search`, `ordering`, `django-filter`.

### Auth / Users / Roles

- `POST /api/auth/login` — login (`name`, `password`), без access key.
- `POST /api/auth/logout` — logout (`refresh?`).
- `GET /api/me` — текущий пользователь + `accesses`.
- `GET/POST /api/users/`, `GET/PATCH/PUT/DELETE /api/users/{id}/` — key `users`.
- `PATCH /api/users/{id}/access/` — полная замена `access_keys`.
- `GET/POST /api/roles/`, `GET/PATCH/PUT/DELETE /api/roles/{id}/` — key `users`.

Пример login request:

```json
{
  "name": "operator1",
  "password": "secret"
}
```

Пример login response:

```json
{
  "token": "<jwt-access>",
  "refresh": "<jwt-refresh>",
  "user": {
    "id": 5,
    "name": "operator1",
    "role": 2,
    "accesses": ["materials", "production"]
  }
}
```

### Materials (key: `materials`)

- `GET/POST /api/raw-materials/`, `GET/PATCH/PUT/DELETE /api/raw-materials/{id}/`
  - filters: `unit`, `is_active`.
  - errors: `MATERIAL_IN_USE`, 409 при попытке удаления используемого сырья.
- `GET/POST /api/incoming/` (update/delete отключены)
  - filters: `material_id/material`, `received_at` range.
  - create payload: `material_id`, `quantity`, `unit_price`, `received_at`, optional supplier/comment.
- `GET /api/materials/balances/`
- `GET /api/materials/movements/`

### Chemistry (key: `chemistry`)

- `GET/POST /api/chemistry/elements/`, `GET/PATCH/PUT/DELETE /api/chemistry/elements/{id}/`
- `POST /api/chemistry/elements/produce/` — выпуск химии.
- `GET/POST /api/chemistry/tasks/`, `GET/PATCH/PUT/DELETE /api/chemistry/tasks/{id}/`
- `POST /api/chemistry/tasks/{id}/confirm/` — выполнить задание и выпустить партию.
- `GET /api/chemistry/balances/`
- `GET /api/chemistry/batches/`, `GET /api/chemistry/batches/{id}/`

Типовые ошибки:
- `INSUFFICIENT_STOCK` (409),
- `EMPTY_CHEMISTRY_RECIPE` (409),
- `CHEMISTRY_IN_USE` (409).

### Recipes / Profiles (key: `recipes`)

- `GET/POST /api/plastic-profiles/`, `GET/PATCH/PUT/DELETE /api/plastic-profiles/{id}/`
- `GET/POST /api/recipes/`, `GET/PATCH/PUT/DELETE /api/recipes/{id}/`
- `GET /api/recipes/{id}/availability/?mode=per_meter|for_production&...`

Ошибки:
- `PROFILE_IN_USE` (409),
- `RECIPE_IN_USE` (409),
- validation errors по несоответствию profile/recipe и компонентам.

### Production / Lines / Shifts / Batches / Recipe-runs

- Lines (key `lines`):
  - CRUD `/api/lines/`
  - `POST /api/lines/{id}/open/`
  - `POST /api/lines/{id}/close/`
  - `PATCH /api/lines/{id}/shift-params/`
  - `POST /api/lines/{id}/shift-pause/`
  - `POST /api/lines/{id}/shift-resume/`
  - `GET /api/lines/history/`
  - `GET /api/lines/{id}/history/`
  - `GET /api/lines/{id}/history/session/?open_event_id=...`

- Shifts (key `my_shift`):
  - `GET /api/shifts/` (+date_from/date_to/line/user),
  - `POST /api/shifts/open/`,
  - `POST /api/shifts/close/`,
  - `GET /api/shifts/{id}/`,
  - `GET /api/shifts/{id}/notes/`,
  - `GET /api/shifts/my/`,
  - `GET/POST /api/shifts/notes/`.

- Shift complaints:
  - `GET/POST /api/shifts/complaints/`
  - Доступ: `CanAccessShiftComplaints` (`my_shift` или `shifts`).

- Shift history:
  - `GET /api/shifts/history/` (история смен текущего пользователя).

- Batches:
  - `GET/POST /api/batches/`, `GET/PATCH/PUT/DELETE /api/batches/{id}/`
  - `POST /api/batches/{id}/submit-for-otk/`
  - `POST /api/batches/{id}/otk_accept/`

- Recipe runs:
  - `GET/POST /api/production/recipe-runs/`
  - `GET/PATCH/PUT/DELETE /api/production/recipe-runs/{id}/`
  - `POST /api/production/recipe-runs/{id}/submit-to-otk/`

### OTK (key `otk`)

- `GET /api/otk/pending/` — очередь партий в ОТК.
- Приемка партии: `POST /api/batches/{id}/otk_accept/`.

### Warehouse (key `warehouse`)

- `GET /api/warehouse/batches/`, `GET /api/warehouse/batches/{id}/`
  - filters: `status`, `product`, `quality`, aliases `inventory_form/stock_form/packaging_status`.
- `POST /api/warehouse/batches/reserve/`
  - payload: `batch_id`, `quantity` (должно равняться полному остатку строки), optional `sale_id`.
- `POST /api/warehouse/batches/package/`
  - payload: `warehouse_batch_id`, `packages_count`, `pieces_per_package`, `comment?`.

### Clients / Orders / Sales / Payments / Returns / Defects / Rework

- Clients (key `clients`)
  - CRUD `/api/clients/`
  - `GET /api/clients/{id}/history/` (агрегированная карточка).

- Orders (key `client_orders`)
  - CRUD `/api/orders/`
  - `PATCH /api/orders/{id}/status/`
  - `GET /api/orders/{id}/nakladnaya/`
  - `GET /api/orders/{id}/history/`

- Sales (key `sales`)
  - CRUD `/api/sales/`
  - `GET /api/sales/{id}/nakladnaya/|waybill/|invoice/|receipt/`.

- Payments (key `payments`)
  - CRUD `/api/payments/`
  - `GET /api/payments/summary/?client_id=...`.

- Returns (key `returns`)
  - CRUD `/api/returns/`
  - `GET /api/returns/{id}/nakladnaya/`.

- Defects (key `defects`)
  - CRUD `/api/defects/`
  - `POST /api/defects/{id}/send-to-rework/`
  - `POST /api/defects/{id}/complete-rework/`
  - `POST /api/defects/{id}/writeoff/`
  - `POST /api/defects/{id}/sell/`.

- Rework (key `defects`)
  - CRUD `/api/rework-requests/`
  - `POST /api/rework-requests/{id}/complete/`.

### Analytics (key `analytics`)

- `GET /api/analytics/summary/`
- `GET /api/analytics/revenue-details/`
- `GET /api/analytics/sales-cost-details/`
- `GET /api/analytics/production-cost-details/`
- `GET /api/analytics/purchase-details/`
- `GET /api/analytics/profit-details/`
- `GET /api/analytics/otk-details/`
- `GET /api/analytics/writeoff-details/` (`year` обязателен).

### Activity / Audit

- `GET /api/activity/my/`, `GET /api/activity/my/{id}/`
- `GET /api/activity/`, `GET /api/activity/{id}/` (key `shifts`).

---

## 5) Полный список статусов и переходов

### `production.Shift.status`

- `open` (Открыта) → `paused`, `closed`
- `paused` (На паузе) → `open`, `closed`
- `closed` (Закрыта) → terminal

### `production.ProductionBatch.otk_status`

- `pending` (Ожидает ОТК) → `accepted` | `rejected`
- `accepted` / `rejected` → terminal

### `production.ProductionBatch.lifecycle_status`

- `pending` (Производство) → `otk`
- `otk` (Очередь ОТК) → `done`
- `done` → terminal

### `sales.Order.status`

- `new` → `confirmed` | `canceled`
- `confirmed` → `in_progress` | `canceled`
- `in_progress` → `partially_shipped` | `shipped` | `canceled`
- `partially_shipped` → `shipped` | `closed` | `canceled`
- `shipped` → `closed`
- `closed` / `canceled` → terminal

### `sales.Sale.sale_status`

- `draft`, `confirmed`, `partially_shipped`, `shipped`, `closed`, `canceled`
- В коде нет отдельного endpoint transition-machine, статус обычно задается через create/update.

### `sales.DefectRecord.status`

- `new` → `on_stock`/`sent_to_rework` (в зависимости от сценария)
- `on_stock` → `sent_to_rework` | `sold` | `written_off`
- `sent_to_rework` → `reworked`
- `reworked` → `sold` | `written_off`
- `sold` / `written_off` → terminal

### `sales.ReworkRequest.status`

- `pending` → `in_progress` → `completed`
- `canceled` — terminal
- `complete` endpoint ставит `completed`.

### `chemistry.ChemistryTask.status`

- `pending` → `in_progress`/`done`
- `done` — terminal (удаление done запрещено).

### `warehouse.WarehouseBatch.status`

- `available` ↔ `reserved` (через reserve/release логики продаж/возвратов)
- `available|reserved` → `shipped` (отгрузка)
- `shipped` может вернуться в `available` при возврате товара.

### `warehouse.WarehouseBatch.inventory_form`

- `unpacked` → `packed` (упаковка)
- `packed` ↔ `open_package` (расход из открытой упаковки)

### `otk.OtkCheck.check_status`

- `pending` → `accepted` | `rejected`

---

## 6) Полные пошаговые сценарии

### Приход сырья

1. `POST /api/incoming/` создает `MaterialBatch`.
2. `quantity_initial = quantity_remaining`, `total_price = quantity * unit_price`.
3. Баланс сырья увеличивается.

### Выпуск химии

1. `POST /api/chemistry/elements/produce/` (или confirm task).
2. По `ChemistryRecipe` рассчитывается потребность сырья.
3. Сырье списывается FIFO (`MaterialStockDeduction`).
4. Создается `ChemistryBatch` с себестоимостью.

### Создание рецепта

1. `POST /api/recipes/` с `profile_id` и `components`.
2. Сохраняются `Recipe` + `RecipeComponent`.
3. Движений склада нет.

### Создание замеса

1. `POST /api/production/recipe-runs/` с `recipe_id`, `line_id`, `batches[*].components`.
2. Проверяется, что линия с открытой и не-паузной сменой.
3. Сохраняется структура замеса.

### Выпуск партии

1. `POST /api/batches/` с `profile`, `recipe`, `line`, `pieces`, `length_per_piece`.
2. Проверяется активная смена текущего пользователя.
3. Считаются метры и себестоимость.
4. Выполняются FIFO списания сырья/химии.

### Отправка в ОТК

1. `POST /api/batches/{id}/submit-for-otk/`.
2. Партия переводится `lifecycle_status=pending -> otk`, `in_otk_queue=true`.

### Приемка ОТК

1. `POST /api/batches/{id}/otk_accept/` с `otk_accepted`, `otk_defect`.
2. Проверка суммы = `pieces`.
3. Создается `OtkCheck`.
4. Партия получает `otk_status=accepted/rejected`, lifecycle `done`.
5. Формируются складские строки (`WarehouseBatch`) по годной и бракованной части.

### Попадание на склад

- После `otk_accept` создаются строки `warehouse_batches` c качеством и OTK-снапшотами.

### Упаковка

1. `POST /api/warehouse/batches/package/`.
2. Исходная `unpacked` строка уменьшается/удаляется.
3. Создается новая `packed` строка с `pieces_per_package`, `packages_count`.

### Резерв

1. `POST /api/warehouse/batches/reserve/`.
2. Разрешен только полный резерв строки (`quantity == текущему остатку`).
3. Статус строки: `available -> reserved`.

### Продажа

1. `POST /api/sales/` (опционально с `warehouse_batch`).
2. Рассчитываются revenue/cost/profit.
3. При продаже со склада вызывается `apply_sale_to_warehouse_batch` (изменение остатков и форм упаковки).

### Заявка → продажа → оплата

1. Создается `Order` + `OrderLine`.
2. Создается `Sale` с `linked_order`.
3. Создаются `Payment` по этой заявке/продаже.
4. Долг/аванс клиента считаются как `revenue` против net оплат.

### Частичная отгрузка

- Поддерживается статусами заявки (`partially_shipped`) и фактическими `shipped_quantity` в `OrderLine`.

### Возврат на склад

1. `POST /api/returns/` со строкой `return_target=warehouse`.
2. Количество возвращается в `WarehouseBatch.quantity`.
3. Если batch был `shipped`, статус возвращается в `available`.

### Возврат в брак

1. `POST /api/returns/` со строкой `return_target=defect`.
2. Создается `DefectRecord(status=on_stock)`.

### Возврат на переделку

1. `POST /api/returns/` со строкой `return_target=rework`.
2. Создается `DefectRecord(status=sent_to_rework)`.
3. Создается `ReworkRequest(status=pending)`.

### Продажа брака

1. `POST /api/defects/{id}/sell/`.
2. Проверка статуса брака (`on_stock|reworked`).
3. Создается `Sale(is_defect_sale=true, sale_status=shipped)`.
4. `DefectRecord.status -> sold`.

### Списание брака

1. `POST /api/defects/{id}/writeoff/` с `writeoff_reason`.
2. `DefectRecord.status -> written_off`.

### Завершение переделки

1. `POST /api/rework-requests/{id}/complete/` с `result_warehouse_batch_id`.
2. `ReworkRequest.status -> completed`.
3. Связанный `DefectRecord.status -> reworked`.

---

## 7) Складская логика

- FIFO сырья:
  - списывают `produce_chemistry` и `apply_production_batch_stock_and_cost`.
  - фиксируется в `MaterialStockDeduction`.
- FIFO химии:
  - списывает производство партий (`ProductionBatch`) из `ChemistryBatch`.
  - фиксируется в `ChemistryStockDeduction`.
- Создание склада ГП:
  - только через приемку ОТК (`otk_accept`).
- Что меняет `WarehouseBatch`:
  - продажи, возвраты, reserve, package, приемка ОТК.
- Нельзя редактировать после проведения:
  - у `ProductionBatch` после отправки в ОТК — только `comment`;
  - у продажи с привязанной складской партией нельзя менять batch/количество/stock_form/piece_pick.
- Упаковка:
  - только для `inventory_form=unpacked`, `status=available`;
  - не смешивает разные партии/качества.
- Резерв:
  - только full-line reserve.
- Возврат:
  - склад/брак/переделка через `ReturnLine.return_target`.

---

## 8) Финансовая логика

- Выручка:
  - `Sale.revenue` = `price * sold_quantity_or_packages` (в зависимости от `sale_mode`).
- Себестоимость продажи:
  - `Sale.cost` = `sold_pieces * warehouse_batch.cost_per_piece` (для складских продаж).
- Прибыль:
  - `Sale.profit = revenue - cost`.
- Предоплаты/доплаты:
  - `Payment.type in (prepayment, payment, surcharge)` учитываются как входящие деньги.
- Возврат денег:
  - `Payment.type = refund` уменьшает net оплату.
- Долг клиента:
  - `max(0, total_revenue - net_paid)`.
- Аванс клиента:
  - `max(0, net_paid - total_revenue)`.
- Отличие обычных продаж от продаж брака:
  - `Sale.is_defect_sale=true` — обязательно использовать в аналитике/отчетах и UI.

---

## 9) RBAC (access keys)

Список из `settings.ACCESS_KEYS`:

- `users`, `lines`, `materials`, `chemistry`, `recipes`, `orders`, `production`, `otk`, `warehouse`, `clients`, `sales`, `shipments`, `analytics`, `shifts`, `my_shift`, `client_orders`, `payments`, `returns`, `defects`.

Привязка разделов:

- Пользователи/роли: `users`
- Линии/история линий: `lines`
- Сырье/приходы/движения: `materials`
- Химия: `chemistry`
- Профили/рецепты: `recipes`
- Производственные партии/замесы: права `production` (write), `otk|production` (read batch list)
- ОТК очередь: `otk`
- Склад ГП: `warehouse`
- Клиенты: `clients`
- Продажи: `sales`
- Заявки: `client_orders`
- Оплаты: `payments`
- Возвраты: `returns`
- Брак/переделки: `defects`
- Аналитика: `analytics`
- Журнал/админ activity и общие жалобы смен: `shifts`
- Моя смена: `my_shift`

Новые ключи коммерческого контура:

- `client_orders`, `payments`, `returns`, `defects`.

---

## 10) Realtime / WebSocket

- Endpoint: `ws://<host>/ws/operational` (или `wss://`).
- Протокол:
  - при connect: событие `connected` с `protocol_version=1`.
  - рабочие события: `event=change`, `resource`, `action`, `id?`, `payload?`, `ts`.
- `action`: `created|updated|deleted|changed`.
- Основные `resource`:
  - `shift`, `shift_note`, `shift_complaint`, `line`, `line_history`,
  - `recipe_run`, `production_batch`, `batch`,
  - `incoming`, `material_balance`, `material_movement`, `material_writeoff`, `raw_material`,
  - `chemistry_element`, `chemistry`, `chemistry_task`, `chemistry_batch`, `chemistry_balance`,
  - `plastic_profile`, `recipe`, `recipes`,
  - `warehouse_batch`,
  - `sale`, `order`, `payment`, `return`, `defect_record`, `rework_request`,
  - `activity`.
- Что фронт должен refetch:
  - по `resource` точечно обновлять соответствующий список/карточку REST.

---

## 11) Ограничения и защита от поломки

- Нельзя удалить:
  - `RawMaterial` при использовании,
  - `ChemistryCatalog` при использовании,
  - `PlasticProfile`/`Recipe` при использовании,
  - `Client` при наличии продаж.
- Нельзя смешивать:
  - упаковку разных партий/форм/качеств через один API-запрос.
- Нельзя менять после проведения:
  - критичные поля `ProductionBatch` после отправки в ОТК,
  - складскую партию и количество у проведенной складской продажи.
- Нельзя открыть конфликтующие смены (DB constraints + runtime checks).
- ОТК проверяет арифметическую целостность (`accepted + defect = pieces`).
- Системные user/role защищены от API-изменений/удаления.

---

## 12) Совместимость и миграции

### Legacy/compatibility в моделях

- `Recipe.output_quantity`, `output_unit_kind` — legacy поля (сохранены для старых клиентов/миграций).
- `ProductionBatch.quantity` — legacy alias (`= total_meters`).
- `ProductionBatch.cost_price` — legacy alias (`= material_cost_total`).
- `RecipeRun.recipe_run_consumption_applied` — устаревший флаг.
- `Shipment` — legacy сущность.
- alias-пути в `config/urls.py` для совместимости старого фронта без `/api/`.

### Критичные миграции

- `sales.0013_commercial_flow` — добавляет коммерческий контур: `Order*`, `Payment`, `Return*`, `DefectRecord`, `ReworkRequest`, новые поля `Sale`.
- `production.0022_production_batch_lifecycle`, `0020_recipe_run_stock_single_source` — фиксация lifecycle и единого источника списаний.
- `warehouse.0004_split_combined_warehouse_batch_rows`, `0006_warehousebatch_quality_defect` — детализация строк склада и качества.
- `materials.0006_material_batch_fifo` — FIFO по партиям сырья.
- `chemistry.0005_chemistry_batches_recipe_rename` и последующие — текущая модель партий химии.

### Что важно не сломать

- Связку `ProductionBatch <-> OTK <-> WarehouseBatch`.
- Инварианты FIFO/себестоимости.
- Разделение товарного и денежного контуров.
- Маркировку брака (`is_defect_sale`, `DefectRecord.status`).
- WebSocket события и refetch-контракт фронта.

---

## 13) Что важно для фронтенда

- Использовать новые ресурсы:
  - `client_orders`, `order_lines`, `payments`, `returns`, `defect_records`, `rework_requests`, `sale_lines`.
- Главные endpoint’ы:
  - Производство: `/api/batches/*`, `/api/production/recipe-runs/*`, `/api/lines/*`, `/api/shifts/*`.
  - Коммерция: `/api/orders/*`, `/api/sales/*`, `/api/payments/*`, `/api/returns/*`, `/api/defects/*`, `/api/rework-requests/*`.
  - Склад: `/api/warehouse/batches/*`.
- Поля, которые фронт обязан передавать:
  - `batches/otk_accept`: `otk_accepted`, `otk_defect` (+ `otk_defect_reason` при браке).
  - `warehouse/batches/reserve`: `batch_id`, `quantity` (полный остаток).
  - `warehouse/batches/package`: `warehouse_batch_id`, `packages_count`, `pieces_per_package`.
  - `returns`: корректные `return_target` по каждой строке.
  - `defects/writeoff`: `writeoff_reason`.
- Статусы, которые фронт обязан показывать:
  - `Order.status`, `Sale.sale_status`,
  - `ProductionBatch.otk_status` и `lifecycle_status`,
  - `WarehouseBatch.status`, `WarehouseBatch.inventory_form`, `WarehouseBatch.quality`,
  - `DefectRecord.status`, `ReworkRequest.status`, `Shift.status`.
- Где нельзя использовать старую логику:
  - нельзя считать, что продажа = одна строка: теперь есть `SaleLine`.
  - нельзя смешивать денежные и товарные статусы.
  - нельзя обходить lifecycle ОТК напрямую.
  - нельзя игнорировать `is_defect_sale` в отчетах/таблицах продаж.

---

## 14) Минимальные примеры ошибок API (реальные коды)

- `validation_error` — невалидные обязательные поля/формат.
- `conflict` — конфликт статуса/состояния (`409`).
- `MATERIAL_IN_USE`, `CHEMISTRY_IN_USE`, `PROFILE_IN_USE`, `RECIPE_IN_USE`, `CLIENT_IN_USE`.
- `INVALID_STATUS_TRANSITION`, `INVALID_STATUS`, `MISSING_STATUS`, `MISSING_REASON`, `MISSING_BATCH`, `NOT_FOUND`.

---

## 15) Короткая матрица «что создает движения»

- Создает движения:
  - `POST /api/incoming/` (приход сырья),
  - `POST /api/chemistry/elements/produce/`, `POST /api/chemistry/tasks/{id}/confirm/`,
  - `POST /api/batches/` (списание FIFO сырья/химии),
  - `POST /api/batches/{id}/otk_accept/` (склад ГП/брак),
  - `POST /api/sales/` с `warehouse_batch`,
  - `POST /api/returns/` (в зависимости от target).

- Не создает движения:
  - CRUD справочников (`raw-materials`, `chemistry/elements`, `plastic-profiles`, `recipes`),
  - CRUD `Order`/`Payment` сами по себе не двигают склад,
  - `RecipeRun*` (без связанного производства/ОТК).

---

## 16) Endpoint Web/API совместимости

- Основной REST префикс: `/api/`.
- Alias без `/api` (для совместимости): `/users/{id}/`, `/users/{id}/access/`, `/warehouse/pack-from-otk/`, `/warehouse/pack/`, `/batches/pack_from_otk/`.
- Swagger/OpenAPI: `/api/docs/`, `/api/openapi.json`, `/api/redoc/`.

