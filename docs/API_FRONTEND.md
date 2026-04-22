# DIAS API — полная документация (эндпоинты + JSON контракты)

Базовый префикс REST: **`/api/`**  
Swagger UI: **`/api/docs/`**  
OpenAPI JSON: **`/api/openapi.json`** (источник истины для схем/типов)

---

## 1) Аутентификация (JWT)

### 1.1 Вход

`POST /api/auth/login`

Тело (JSON):

```json
{
  "name": "admin",
  "password": "admin"
}
```

Ответ `200`:

```json
{
  "token": "<access_jwt>",
  "refresh": "<refresh_jwt>",
  "user": {
    "id": 1,
    "name": "admin",
    "role": 1,
    "accesses": ["lines", "recipes", "orders"]
  }
}
```

Ошибки:
- `400 validation_error`: нет `name`/`password`
- `401 unauthorized`: неверные данные / пользователь деактивирован
- `409 conflict`: найдено несколько пользователей с таким `name`
- `429 too_many_requests`: throttle `login` (`10/min`)

### 1.2 Заголовок авторизации для всех остальных запросов

```
Authorization: Bearer <token>
```

### 1.3 Текущий пользователь + права

`GET /api/me`

Ответ `200`:

```json
{
  "user": {
    "id": 1,
    "name": "admin",
    "email": "admin@local.dias",
    "role": 1,
    "role_name": "Админ",
    "accesses": ["users", "lines", "materials"]
  },
  "accesses": ["users", "lines", "materials"]
}
```

### 1.4 Выход (blacklist refresh)

`POST /api/auth/logout`

Тело (JSON, опционально):

```json
{ "refresh": "<refresh_jwt>" }
```

Ответ `200` всегда:

```json
{ "detail": "OK" }
```

---

## 2) Общие правила API

### 2.1 Content-Type

Отправляйте JSON:

```
Content-Type: application/json
```

### 2.2 Формат ошибок (единый)

Ответ ошибки — объект:

```json
{
  "code": "validation_error",
  "error": "Сообщение для UI",
  "detail": "То же сообщение",
  "errors": [
    { "field": "name", "message": "Обязательное поле" }
  ]
}
```

Дополнительно при `429`:

```json
{ "code": "too_many_requests", "detail": "...", "wait": 12 }
```

### 2.3 Пагинация (списки)

По умолчанию: query `page`, `page_size` (макс. 100; для рецептов — до 500).

Ответ списков:

```json
{
  "items": [/* ... */],
  "meta": {
    "total": 123,
    "total_count": 123,
    "page": 1,
    "perPage": 20,
    "page_size": 20,
    "totalPages": 7,
    "total_pages": 7
  },
  "links": {
    "next": "http://host/api/.../?page=2",
    "previous": null
  }
}
```

### 2.4 Фильтры / поиск / сортировка

Во многих ViewSet доступны:
- `search=...` (по `search_fields`)
- `ordering=field` или `ordering=-field` (по `ordering_fields`)
- фильтры по полям (см. `filterset_fields` / `filterset_class` в OpenAPI)

### 2.5 RBAC (доступ по ключам)

Почти все эндпоинты требуют, чтобы у пользователя в `accesses[]` был нужный ключ раздела:
- `users`, `lines`, `materials`, `chemistry`, `recipes`, `production`, `otk`, `warehouse`,
  `clients`, `sales`, `analytics`, `shifts`, `my_shift`

Точный ключ указан ниже в описании эндпоинта (и в OpenAPI).

---

## 3) Эндпоинты (REST)

Ниже указаны **каноничные** пути. В отдельных местах есть алиасы без `/api/` (см. раздел 3.0).

### 3.0 Алиасы маршрутов без `/api/` (совместимость)

Определены в `config/urls.py`:
- `GET/PATCH/PUT/DELETE /users/{id}/` → как `/api/users/{id}/`
- `PATCH /users/{id}/access/` → как `/api/users/{id}/access/`
- `POST /warehouse/pack-from-otk/` и `POST /warehouse/pack/` и `POST /batches/pack_from_otk/` → как `/api/warehouse/batches/package/`

---

## 3.1 Accounts (пользователи/роли) — ключ доступа `users`

### Пользователи
- `GET /api/users/` — список (фильтры: `role`, `is_active`; поиск: `name`, `email`; ordering: `id,name,email,date_joined`)
- `POST /api/users/` — создать

Тело `POST` (JSON):

```json
{
  "name": "operator1",
  "role": 2,
  "password": "secret"
}
```

Ответ пользователя (пример):

```json
{
  "id": 10,
  "name": "operator1",
  "role": 2,
  "accesses": ["production", "otk"]
}
```

- `GET /api/users/{id}/` — карточка
- `PATCH /api/users/{id}/` — правка (в т.ч. `password`)
- `PUT /api/users/{id}/` — как `PATCH`
- `DELETE /api/users/{id}/` — удалить

### Права пользователя (полная замена вкладок)

`PATCH /api/users/{id}/access/`

Тело:

```json
{
  "access_keys": ["lines", "recipes", "production"]
}
```

Ответ `200`: объект пользователя (`UserSerializer`).

### Роли
- `GET /api/roles/`
- `POST /api/roles/` тело:

```json
{ "name": "Оператор" }
```

- `GET /api/roles/{id}/`
- `PATCH /api/roles/{id}/`
- `DELETE /api/roles/{id}/` (системные роли удалить нельзя)

---

## 3.2 Lines & Shifts (производственные линии/смены)

### Линии — ключ `lines`

- `GET /api/lines/`
  - query: `eligible_for_recipe_run=true` и/или `eligible_for_production_batch=true` — вернёт только линии, где смена **открыта** и **не в паузе**
- `POST /api/lines/`
- `GET /api/lines/{id}/`
- `PATCH /api/lines/{id}/`
- `DELETE /api/lines/{id}/` (нельзя, если смена открыта → `409 LINE_SHIFT_OPEN`)

#### Открыть смену на линии

`POST /api/lines/{id}/open/`

```json
{
  "height": 10.0,
  "width": 20.0,
  "angle_deg": 30.0,
  "comment": "опц.",
  "session_title": "опц."
}
```

Ответ:

```json
{
  "detail": "Смена открыта",
  "line": { /* LineSerializer, включая shift_snapshot */ }
}
```

#### Закрыть смену на линии

`POST /api/lines/{id}/close/`

Тело:
- либо передать все размеры `height,width,angle_deg` (опц. `comment`)
- либо **не передавать размеры** — тогда возьмутся последние параметры смены из истории; если их нет → `400`

#### Зафиксировать параметры смены

`PATCH /api/lines/{id}/shift-params/`

```json
{ "height": 10.0, "width": 20.0, "angle_deg": 30.0, "comment": "опц." }
```

#### Пауза/возобновление

- `POST /api/lines/{id}/shift-pause/`

```json
{ "reason": "текст причины" }
```

- `POST /api/lines/{id}/shift-resume/` (тело пустое)

#### История линии

- `GET /api/lines/{id}/history/` → `{ "items": [...] }`
- `GET /api/lines/history/` → пагинация стандартная
- `GET /api/lines/{id}/history/session/?open_event_id=123` → таймлайн одной смены

### Смены (личные и на линии) — ключ `my_shift`

Read-only список/деталка:
- `GET /api/shifts/` (фильтры: `date_from`, `date_to`, `line`, `user`)
- `GET /api/shifts/{id}/`

Открыть/закрыть смену:
- `POST /api/shifts/open/`
  - **без `line_id`** → открывает **личную** смену (line=null)
  - **с `line_id`** → открывает смену на линии (требует `height,width,angle_deg`)

```json
{ "line_id": 1, "height": 10, "width": 20, "angle_deg": 30, "comment": "", "session_title": "" }
```

- `POST /api/shifts/close/`
  - без `line_id` → закрывает личную
  - с `line_id` → закрывает смену пользователя на линии (размеры как у close линии)

Доп. endpoints:
- `GET /api/shifts/my/` → `{ "shift": ShiftSerializer | null }` (только личная открытая)
- `GET /api/shifts/history/` (пагинация)
- `GET /api/shifts/{id}/notes/`
- `GET /api/shifts/notes/` (заметки к текущей личной открытой смене)
- `POST /api/shifts/notes/`

```json
{ "note": "текст" }
```

Жалобы по сменам (доступ: `my_shift` или `shifts`):
- `GET /api/shifts/complaints/`
- `POST /api/shifts/complaints/`

```json
{
  "body": "Текст жалобы",
  "mentioned_user_ids": [2, 3],
  "shift_id": 15
}
```

---

## 3.3 Materials (сырьё/приход/остатки/движения) — ключ `materials`

### Справочник сырья
- `GET /api/raw-materials/`
- `POST /api/raw-materials/`

```json
{ "name": "ПВД", "unit": "kg", "min_balance": 10, "is_active": true, "comment": "" }
```

- `GET /api/raw-materials/{id}/`
- `PATCH /api/raw-materials/{id}/`
  - смена `unit` может быть запрещена (конфликт) → `409`
- `DELETE /api/raw-materials/{id}/`
  - если сырьё используется → `409 MATERIAL_IN_USE`

### Приход сырья (партии)

`/api/incoming/` — **только GET и POST**.

`POST /api/incoming/`:

```json
{
  "material_id": 1,
  "quantity": 25.5,
  "unit_price": 120.0,
  "received_at": "2026-04-22",
  "supplier_name": "Поставщик",
  "document_number": "DOC-123",
  "comment": ""
}
```

Ответ: строки прихода с пересчитанными полями (`quantity`/`quantity_remaining` в единицах карточки сырья).

### Остатки сырья

`GET /api/materials/balances/` → пагинированный список карточек с балансом (в единице карточки).

### Журнал движений сырья

`GET /api/materials/movements/` → входящие партии + списания (FIFO) одним списком (пагинация).

---

## 3.4 Chemistry (элементы/задания/остатки/партии) — ключ `chemistry`

### Справочник химии
- `GET /api/chemistry/elements/`
- `POST /api/chemistry/elements/` (состав можно передать как `recipe_lines` или `compositions`)

```json
{
  "name": "Добавка A",
  "unit": "kg",
  "min_balance": 1.0,
  "is_active": true,
  "comment": "",
  "recipe_lines": [
    { "raw_material_id": 1, "quantity_per_unit": 0.2 },
    { "material_id": 2, "quantity_per_unit": 0.8 }
  ]
}
```

- `GET /api/chemistry/elements/{id}/`
- `PATCH /api/chemistry/elements/{id}/` (карточка без изменения состава)
- `PATCH /api/chemistry/elements/{id}/` **только** с `{ "recipe_lines": [...] }` → обновляет состав; смешивать с другими полями нельзя
- `DELETE /api/chemistry/elements/{id}/` (если используется → `409 CHEMISTRY_IN_USE`)

### Выпуск химии (списание сырья FIFO + партия)

`POST /api/chemistry/elements/produce/`

```json
{ "chemistry_id": 1, "quantity": 5.0, "comment": "партия" }
```

Ответ `201`: `ChemistryBatchSerializer`.

Ошибки:
- `409` при `INSUFFICIENT_STOCK` / `EMPTY_CHEMISTRY_RECIPE`

### Задания химии
- `GET /api/chemistry/tasks/`
- `POST /api/chemistry/tasks/`
- `GET /api/chemistry/tasks/{id}/`
- `PATCH /api/chemistry/tasks/{id}/`
- `DELETE /api/chemistry/tasks/{id}/` (нельзя, если status=done)

Подтверждение задания (выпуск по заданию):

`POST /api/chemistry/tasks/{id}/confirm/`

Ответ:

```json
{
  "task": { /* ChemistryTask */ },
  "batch": { /* ChemistryBatch */ }
}
```

### Остатки химии

`GET /api/chemistry/balances/` → `items[]` (balance в единице карточки).

### История партий химии

`GET /api/chemistry/batches/` (read-only)

---

## 3.5 Recipes (профили/рецепты) — ключ `recipes`

### Профили пластика
- `GET /api/plastic-profiles/`
- `POST /api/plastic-profiles/`
- `GET /api/plastic-profiles/{id}/`
- `PATCH /api/plastic-profiles/{id}/`
- `DELETE /api/plastic-profiles/{id}/` (если используется → `409 PROFILE_IN_USE`)

### Рецепты

Рецепт — справочник норм **на 1 метр** (сохранение не списывает склад).

- `GET /api/recipes/` (пагинация, max `page_size` 500)
- `POST /api/recipes/`

Тело (важно: компоненты пишутся **массивом** `components` в запросе создания/обновления):

```json
{
  "recipe": "Рецепт 1",
  "product": "Изделие 1",
  "profile_id": 1,
  "base_unit": "per_meter",
  "output_quantity": 100.0,
  "output_unit_kind": "amount",
  "comment": "",
  "is_active": true,
  "components": [
    { "type": "raw_material", "material_id": 1, "quantity_per_meter": 0.12 },
    { "type": "chemistry", "chemistry_id": 2, "quantity_per_meter": 0.005 }
  ]
}
```

- `GET /api/recipes/{id}/` — карточка (включает `components[]`)
- `PATCH /api/recipes/{id}/` / `PUT /api/recipes/{id}/` — если передан `components`, старые компоненты удаляются и создаются заново
- `DELETE /api/recipes/{id}/` (если используется → `409 RECIPE_IN_USE`)

Проверка доступности по остаткам (сырьё/химия):

`GET /api/recipes/{id}/availability/?mode=per_meter`

или для выпуска:

`GET /api/recipes/{id}/availability/?mode=for_production&total_meters=250`

Ответ:

```json
{
  "mode": "for_production",
  "total_meters": "250",
  "all_sufficient": true,
  "components": [
    {
      "id": 10,
      "component_type": "raw_material",
      "material_id": 1,
      "chemistry_id": null,
      "name": "ПВД",
      "unit": "kg",
      "norm_per_meter_kg": "0.12",
      "required_total_kg": "30",
      "available_kg": "100",
      "shortage_kg": "0",
      "sufficient": true
    }
  ]
}
```

---

## 3.6 Production (партии, замесы) — ключи `production` / `otk`

### Партии производства (ProductionBatch) — доступ: `production` или `otk`

- `GET /api/batches/` (фильтры: `otk_status`, `order`, `line`, `profile`, `lifecycle_status`)
- `GET /api/batches/{id}/`
- `POST /api/batches/` (только при ключе `production`)

Создание партии:

```json
{
  "profile": 1,
  "recipe": 5,
  "line": 2,
  "pieces": 50,
  "length_per_piece": 6.0,
  "comment": "",
  "date": "2026-04-22",
  "produced_at": "2026-04-22T10:00:00+03:00",
  "product": "опц."
}
```

Важно:
- линия должна иметь **открытую и не остановленную** смену
- у текущего пользователя должна быть **открытая смена на этой линии**
- FIFO/себестоимость пересчитываются сервером (`apply_production_batch_stock_and_cost`)

Отправка партии в ОТК:

`POST /api/batches/{id}/submit-for-otk/` (тело пустое)

Приёмка ОТК:

`POST /api/batches/{id}/otk_accept/`

```json
{
  "otk_accepted": 48,
  "otk_defect": 2,
  "otk_defect_reason": "царапины",
  "otk_comment": "опц.",
  "otk_inspector": 3,
  "otk_inspector_name": "Иван",
  "otk_checked_at": "2026-04-22T12:00:00+03:00"
}
```

Правила:
- `otk_accepted + otk_defect == pieces партии`
- если `otk_defect > 0` → причина обязательна
- после приёмки создаются строки склада ГП (`create_warehouse_batches_from_otk`)

### Замесы (RecipeRun) — `/api/production/recipe-runs/`

- `GET /api/production/recipe-runs/`
- `POST /api/production/recipe-runs/` — создаёт замес + **создаёт/обновляет** связанную ProductionBatch pending + списание (через общую FIFO логику)

Тело:

```json
{
  "recipe_id": 5,
  "line_id": 2,
  "quantity": 120.0,
  "output_scale": 1.0,
  "batches": [
    {
      "index": 0,
      "label": "Ёмкость 1",
      "quantity": 60.0,
      "components": [
        { "material_id": 1, "quantity": 7.2, "unit": "кг", "recipe_component_id": 10 },
        { "chemistry_id": 2, "quantity": 0.3, "unit": "кг", "recipe_component_id": 11 }
      ]
    }
  ]
}
```

Примечания:
- `quantity` — выпуск для партии/ОТК (если не указан — берётся `recipe.output_quantity`, умноженный на `output_scale`)
- `batches[]` — план по ёмкостям и строкам расхода для интерфейса

Отправить/обновить связанную партию pending:

`POST /api/production/recipe-runs/{id}/submit-to-otk/`

```json
{ "quantity": 130.0, "output_scale": 1.0 }
```

Удаление замеса:
- `DELETE /api/production/recipe-runs/{id}/` — возможно только пока связанная партия **pending**; иначе `409`

---

## 3.7 Warehouse (склад ГП) — ключ `warehouse`

Read-only:
- `GET /api/warehouse/batches/`
- `GET /api/warehouse/batches/{id}/`

Резерв:

`POST /api/warehouse/batches/reserve/`

```json
{ "batch_id": 100, "quantity": 48, "sale_id": 10 }
```

Правило: резерв только **на полный остаток строки** (`quantity` должно равняться `batch.quantity`), иначе `400`.

Упаковка (создание упакованной строки склада из неупакованной):

`POST /api/warehouse/batches/package/`

```json
{
  "warehouse_batch_id": 100,
  "pieces_per_package": 10,
  "packages_count": 4,
  "comment": "опц."
}
```

Ответ `201`:

```json
{ "items": [ /* новые WarehouseBatch строки */ ] }
```

---

## 3.8 Sales (клиенты/продажи) — ключи `clients`, `sales`

### Клиенты — ключ `clients`
- `GET /api/clients/`
- `POST /api/clients/`
- `GET /api/clients/{id}/`
- `PATCH /api/clients/{id}/`
- `DELETE /api/clients/{id}/` (если есть продажи → `409 CLIENT_IN_USE`)

История продаж клиента:

`GET /api/clients/{id}/history/` → `{ "items": [...] }`

### Продажи — ключ `sales`

- `GET /api/sales/`
- `POST /api/sales/`
- `GET /api/sales/{id}/`
- `PATCH /api/sales/{id}/`
- `DELETE /api/sales/{id}/` (Shipment’ы удаляются транзакционно)

Создание продажи без склада:

```json
{
  "client": 1,
  "product": "Изделие X",
  "quantity": 50,
  "price": 200,
  "date": "2026-04-22",
  "comment": ""
}
```

Создание продажи со складом (спишет/зарезервирует склад по `apply_sale_to_warehouse_batch`):

```json
{
  "client": 1,
  "warehouse_batch_id": 100,
  "quantity": 48,
  "price": 200,
  "sale_unit": "piece",
  "stock_form": "unpacked",
  "piece_pick": "loose"
}
```

Документы (HTML):
- `GET /api/sales/{id}/nakladnaya/`
- `GET /api/sales/{id}/waybill/`
- `GET /api/sales/{id}/invoice/`

---

## 3.9 OTK — ключ `otk`

Очередь ОТК:

`GET /api/otk/pending/`

Ответ:

```json
{ "items": [ /* элементы как у GET /api/batches/ */ ] }
```

Приёмка партии: `POST /api/batches/{id}/otk_accept/` (см. выше).

---

## 3.10 Analytics — ключ `analytics` (все эндпоинты GET)

Подробная документация по аналитике: `FRONTEND_ANALYTICS_API.md` (в корне репозитория).

Эндпоинты:
- `GET /api/analytics/summary/`
- `GET /api/analytics/revenue-details/`
- `GET /api/analytics/sales-cost-details/`
- `GET /api/analytics/production-cost-details/`
- `GET /api/analytics/purchase-details/`
- `GET /api/analytics/profit-details/`
- `GET /api/analytics/otk-details/`
- `GET /api/analytics/writeoff-details/` (требует `year`)

---

## 3.11 Activity (аудит/журнал действий)

Личный журнал:
- `GET /api/activity/my/`
- `GET /api/activity/my/{id}/`

Админ журнал (ключ `shifts`):
- `GET /api/activity/`
- `GET /api/activity/{id}/`

---

## 4) WebSocket (операционные события)

См. отдельный файл: `docs/WEBSOCKET_API.md`.

