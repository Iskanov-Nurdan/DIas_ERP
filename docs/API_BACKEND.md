# DIas ERP — HTTP API (спецификация по коду бэкенда)

**Базовый префикс:** `/api/` (см. `config/urls.py`).

**Авторизация:** JWT (`rest_framework_simplejwt`). В заголовке:  
`Authorization: Bearer <access_token>`  
(см. `config/settings.py` → `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']`).

**Формат JSON:** UTF-8; Decimal в JSON как числа (`COERCE_DECIMAL_TO_STRING: false`).

**Источник схемы для IDE:** `GET /api/openapi.json`, UI: `/api/docs/`, `/api/redoc/`.

---

## Ошибки

Общий вид (см. `config/openapi_common.py`, `config/exceptions.py`):

```json
{
  "code": "validation_error",
  "error": "Краткий текст",
  "detail": "Дублирует error",
  "errors": [{ "field": "...", "message": "..." }]
}
```

Списание без остатка (партия производства) может вернуть вложенный объект с полями `code`, `missing` (массив компонентов) — см. `apps/production/batch_stock.py`.

---

## Пагинация (списки)

Класс: `config.pagination.StandardResultsSetPagination`.

**Query:** `page`, `page_size` (макс. **100**, дефолт **20**).

**Ответ:**

```json
{
  "items": [],
  "meta": {
    "total": 0,
    "page": 1,
    "perPage": 20,
    "totalPages": 1,
    "total_pages": 1
  },
  "links": { "next": null, "previous": null }
}
```

Дополнительно: `search`, `ordering`, фильтры по полям (`filterset_fields` / FilterSet у конкретного ViewSet).

---

## RBAC

Права — **строковые ключи** в `UserAccess` у пользователя. Суперпользователь обходит проверку.

Типичные ключи: `users`, `lines`, `materials`, `chemistry`, `recipes`, `production`, `otk`, `warehouse`, `clients`, `sales`, `analytics`, `shifts`, `my_shift`, …

Ниже у каждого ресурса указан **`required_access_key`** из соответствующего `ViewSet` (класс `config.permissions.IsAdminOrHasAccess`), если не оговорено иначе.

**Особый случай — партии производства** (`BatchViewSet`) и **замесы** (`RecipeRunViewSet`): класс `IsAdminOrHasProductionOrOtk` (`config/permissions.py`):

- **list / retrieve** — ключ `otk` **или** `production`;
- **create / update / partial_update / destroy** — только **`production`** (или суперпользователь).

---

## Auth (без префикса `/api/` в путях ниже — они в `config/urls.py`)

| Метод | Путь | Тело запроса | Ответ (суть) |
|-------|------|--------------|--------------|
| POST | `/api/auth/login` | `{ "name": "<логин>", "password": "<пароль>" }` | `{ "token", "refresh", "user": { ... } }` — см. `apps/accounts/views.py` `LoginView` |
| GET | `/api/me` | — | `{ "user": { ... }, "accesses": [ "<ключи>" ] }` |
| POST | `/api/auth/logout` | `{ "refresh": "<опционально>" }` | `{ "detail": "OK" }` — blacklist refresh |

Лимит логина: throttle `login` **10/min** (`LoginRateThrottle`).

---

## Пользователи и роли

**Ключ доступа:** `users`.

| Метод | Путь | Примечание |
|-------|------|------------|
| GET/POST | `/api/users/` | CRUD пользователей (`UserSerializer`) |
| GET/PATCH/PUT/DELETE | `/api/users/{id}/` | |
| PATCH | `/api/users/{id}/access/` | Тело: `{ "access_keys": ["lines", "sales", ...] }` — полная замена (`UserAccessPatchSerializer`) |

| Метод | Путь | Примечание |
|-------|------|------------|
| GET/POST | `/api/roles/` | Роли (`RoleSerializer`) |
| GET/PATCH/PUT/DELETE | `/api/roles/{id}/` | |

**Алиасы без `/api/`** (для совместимости): `users/<id>/`, `users/<id>/access/` — см. `config/urls.py`.

---

## Сырьё и приход

**Ключ:** `materials`.

| Метод | Путь | Примечание |
|-------|------|------------|
| GET/POST | `/api/raw-materials/` | Поля: `name`, `unit` (валидация **кг/г**), `min_balance` — см. `apps/materials/serializers.py` |
| GET/PATCH/PUT/DELETE | `/api/raw-materials/{id}/` | |
| GET/POST | `/api/incoming/` | `material_id` или `name` при создании; `quantity`, `unit`, `price_per_unit`, `total_price` (read-only в ответе, считается в `Incoming.save`) |
| GET/PATCH/PUT/DELETE | `/api/incoming/{id}/` | |

| Метод | Путь | Ответ |
|-------|------|--------|
| GET | `/api/materials/balances/` | **Без пагинации:** `{ "items": [ { "id", "name", "balance", "unit", "min_balance" } ] }` |

---

## Химия

**Ключ:** `chemistry`.

| Метод | Путь | Примечание |
|-------|------|------------|
| GET/POST | `/api/chemistry/elements/` | Справочник химии |
| GET/PATCH/PUT/DELETE | `/api/chemistry/elements/{id}/` | |
| GET/POST | `/api/chemistry/tasks/` | Задания |
| GET/PATCH/PUT/DELETE | `/api/chemistry/tasks/{id}/` | |
| POST | `/api/chemistry/tasks/{id}/confirm/` | **Тело пустое.** Списание сырья + начисление на `ChemistryStock`, статус `done` |
| GET | `/api/chemistry/balances/` | Остатки (read-only) |

---

## Профили и рецепты

**Ключ:** `recipes`.

| Метод | Путь | Примечание |
|-------|------|------------|
| GET/POST | `/api/plastic-profiles/` | `{ "name", "code" }` — `PlasticProfileSerializer` |
| GET/PATCH/PUT/DELETE | `/api/plastic-profiles/{id}/` | |
| GET/POST | `/api/recipes/` | Рецепт: `recipe`, `product`, `profile_id`, `base_unit`, `output_quantity`, `output_unit_kind`; вложенные `components` обрабатываются в `perform_create`/`perform_update` — см. `apps/recipes/views.py` |
| GET/PATCH/PUT/DELETE | `/api/recipes/{id}/` | При PUT/PATCH можно передать массив `components`: `type` (`raw_material`/`chemistry`/`raw`/`chem`), `material_id`/`chemistry_id`, **`quantity_per_meter`** (или алиас `quantity`), `unit` |
| GET | `/api/recipes/{id}/availability/` | Проверка остатков на 1 м нормы |

---

## Производство: линии, партии, смены, замесы

### Линии

**Ключ:** `lines`.

| Метод | Путь | Тело / query |
|-------|------|----------------|
| CRUD | `/api/lines/` | Модель `Line` |
| POST | `/api/lines/{id}/open/` | `LineShiftOpenSerializer`: `height`, `width`, `angle_deg`, опц. `comment`, `session_title` |
| POST | `/api/lines/{id}/close/` | `height`, `width`, `angle_deg` **все три** или берутся из истории — см. `_body_for_line_shift_close` в `apps/production/views.py` |
| PATCH | `/api/lines/{id}/shift-params/` | `LineShiftSnapshotSerializer` |
| POST | `/api/lines/{id}/shift-pause/` | `{ "reason": "..." }` |
| POST | `/api/lines/{id}/shift-resume/` | пустое или без обязательных полей |
| GET | `/api/lines/history/` | история всех линий |
| GET | `/api/lines/{id}/history/` | `{ "items": [...] }` |
| GET | `/api/lines/{id}/history/session/?open_event_id=<id>` | таймлайн смены |

### Партии производства (`ProductionBatch`)

**См. раздел RBAC выше (production vs otk).**

| Метод | Путь | Тело |
|-------|------|------|
| GET | `/api/batches/` | Список (`BatchListSerializer`), фильтры: `otk_status`, `order`, `line`, `profile` |
| GET | `/api/batches/{id}/` | Деталка |
| POST | `/api/batches/` | **`ProductionBatchCreateUpdateSerializer`:** обязательно `profile`, `recipe`, `line`, `pieces`, `length_per_piece`; опц. `product`, `date`, `produced_at`, `comment`. **`total_meters` только read-only в ответе.** Нужна открытая смена пользователя на этой линии (`status=open`). |
| PATCH/PUT | `/api/batches/{id}/` | Только пока `otk_status=pending` и нет связанного замеса `RecipeRun` с этой партией. После PATCH — пересчёт списания. |
| POST | `/api/batches/{id}/otk_accept/` | Принято/брак **в штуках**: `otk_accepted` или `accepted`, `otk_defect` или `rejected`, опц. `otk_defect_reason`, `otk_comment`, `otk_inspector` / `otk_inspector_id`, `otk_checked_at`. **Сумма accepted+rejected = `batch.pieces`.** |

### Смены пользователя

**Ключ:** `my_shift`.

| Метод | Путь | Тело |
|-------|------|------|
| GET | `/api/shifts/` | Query: `date_from`, `date_to`, `line`, `user` |
| GET | `/api/shifts/{id}/` | |
| POST | `/api/shifts/open/` | С `line_id`: как у открытия смены на линии + геометрия; без — личная смена |
| POST | `/api/shifts/close/` | Опц. `line_id`, `comment`, размеры для линии |
| GET | `/api/shifts/my/` | Текущая открытая **личная** смена |
| GET | `/api/shifts/{id}/notes/` | |
| GET/POST | `/api/shifts/notes/` | Заметки (см. `ShiftViewSet`) |

**Отдельные пути (см. `config/api_urls.py`):**

- `GET/POST /api/shifts/complaints/` — жалобы (`CanAccessShiftComplaints`: `my_shift` или `shifts`).
- `GET /api/shifts/history/` — `ShiftHistoryView`.

### Замес (recipe-run)

**Права:** как у партий — см. раздел RBAC выше (`IsAdminOrHasProductionOrOtk`). Замес — этап **до** очереди ОТК; строки расхода по ёмкостям **не** двигают остатки. FIFO и себестоимость — только у связанной **`ProductionBatch`** (`batch_stock.py`).

| Метод | Путь | Тело |
|-------|------|------|
| GET | `/api/production/recipe-runs/` | |
| POST | `/api/production/recipe-runs/` | `recipe_id`, `line_id`, `batches`: массив партий с `components` (в каждой строке ровно одно из `material_id`, `chemistry_id`), опц. `recipe_component_id`. Опц. корень `quantity`, `output_scale`/`scale` — создаётся `ProductionBatch` (pending) и списание по норме рецепта × метры |
| PATCH | `/api/production/recipe-runs/{id}/` | Полный `batches` при изменении плана замеса (остатки не пересчитываются по этим строкам) |
| DELETE | `/api/production/recipe-runs/{id}/` | При pending-партии: откат списания по партии, удаление партии/заказа |
| POST | `/api/production/recipe-runs/{id}/submit-to-otk/` | Опц. `quantity`, `output_scale` — создание/обновление `ProductionBatch` для ОТК и пересчёт списания по партии |

---

## ОТК (очередь)

**Ключ:** `otk`.

| Метод | Путь | Ответ |
|-------|------|--------|
| GET | `/api/otk/pending/` | `{ "items": [ ... ] }` — сериализатор как у списка партий |

---

## Склад ГП

**Ключ:** `warehouse`.

| Метод | Путь | Примечание |
|-------|------|------------|
| GET | `/api/warehouse/batches/` | Фильтры: `WarehouseBatchFilter` |
| GET | `/api/warehouse/batches/{id}/` | `WarehouseBatchSerializer` |
| POST | `/api/warehouse/batches/reserve/` | `{ "batch_id", "quantity", опц. "sale_id" }` (алиас `batchId`) |
| POST | `/api/warehouse/batches/package/` | Упаковка: обяз. `product_id`, геометрия `shift_height` или `unit_meters`, `shift_width` или `width_meters`, `angle_deg`, `packages_count`, опц. `pieces_per_package`, `package_total_meters` — полная логика в `apps/warehouse/views.py` `package()` |

**Алиасы без `/api/`:** `warehouse/pack/`, `warehouse/pack-from-otk/` → тот же `package` action.

---

## Клиенты и продажи

**Ключи:** `clients` (клиенты), `sales` (продажи).

| Метод | Путь | Примечание |
|-------|------|------------|
| CRUD | `/api/clients/` | |
| GET | `/api/clients/{id}/history/` | История по клиенту |

| Метод | Путь | Тело (создание/обновление) |
|-------|------|----------------------------|
| CRUD | `/api/sales/` | `SaleSerializer`: `sale_mode` (`pieces` \| `packages`), `sold_pieces`, `sold_packages`, `length_per_piece`, `price`, связь `warehouse_batch`, `quantity` (legacy = штуки), расчёт `revenue`, `cost`, `profit` на сервере (`_apply_finance`) |

Доп. GET: `/api/sales/{id}/nakladnaya/`, `/waybill/`, `/invoice/` — см. `apps/sales/views.py`.

---

## Аналитика

**Ключ:** `analytics`.

**Query для периода** (см. `apps/analytics/services.py` `parse_period`): `year`, `month`, `day`.

| Метод | Путь |
|-------|------|
| GET | `/api/analytics/summary/` |
| GET | `/api/analytics/revenue-details/` | `year` обязателен |
| GET | `/api/analytics/expense-details/` | |
| GET | `/api/analytics/writeoff-details/` | |

---

## Активность (аудит)

| Метод | Путь | Доступ |
|-------|------|--------|
| GET | `/api/activity/my/` | Авторизован любой; query: `page`, `page_size`, `shift_id`, `entity_type`, `entity_id`, `action`, `request_id`, `date_from`, `date_to` |
| GET | `/api/activity/my/{id}/` | Деталка своей записи |
| GET | `/api/activity/` | Ключ **`shifts`**; query: `user_id`, `date_from`, `date_to`, `shift_id`, … |
| GET | `/api/activity/{id}/` | Ключ **`shifts`** |

---

## Где смотреть код при изменениях

| Тема | Файлы |
|------|--------|
| Маршруты | `config/urls.py`, `config/api_urls.py` |
| Права | `config/permissions.py` |
| JWT / login | `config/settings.py`, `apps/accounts/views.py` |
| Пагинация | `config/pagination.py` |
| Партия производства (создание) | `apps/production/serializers.py` → `ProductionBatchCreateUpdateSerializer`; списание `apps/production/batch_stock.py` |
| ОТК приёмка | `apps/production/views.py` → `otk_accept` |
| Продажи (финансы) | `apps/sales/serializers.py` → `_apply_finance` |
| Склад reserve/package | `apps/warehouse/views.py` |

*Документ сгенерирован по состоянию репозитория; при изменении ViewSet/сериализаторов обновляйте этот файл или полагайтесь на `/api/openapi.json`.*
