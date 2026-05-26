# Промпт для фронта: журнал смены и заголовок `X-Audit-Shift-Id`

Скопируй этот файл целиком в чат агента фронтенда (React/Next и т.д.). Бэкенд DIas_ERP уже обновлён; задача — довести клиент до согласованного контракта.

---

## Цель

1. Пока у пользователя **открыта смена** — все операционные мутации API должны уходить с заголовком **`X-Audit-Shift-Id: <id>`** (не только на странице «Моя смена»).
2. Экран **«Действия за смену»** читает `GET /api/activity/my/?shift_id=<id>` (или админский `GET /api/activity/?shift_id=&user_id=`) — бэкенд отдаёт **только whitelist** типов; мусор (Сотрудники, Справочники) не попадёт даже если старые строки были в интервале смены.

---

## Контракт бэкенда (актуально)

### Заголовки (CORS разрешены)

| Заголовок | Назначение |
|-----------|------------|
| `Authorization: Bearer <access>` | JWT |
| `X-Audit-Shift-Id` | ID **личной** открытой смены текущего пользователя (приоритет над авто-привязкой на бэке) |
| `X-Shift-Id` | Legacy-алиас, бэкенд читает так же |
| `X-Request-Id` | Опционально, correlation |

### Разрешение смены на бэке

`apps/activity/shift_context.py`:

- Если передан `X-Audit-Shift-Id` — смена должна принадлежать **текущему пользователю** (`Shift.user_id = me`).
- Если заголовка нет — бэкенд сам берёт **самую поздно открытую** смену без `closed_at`.
- Для `entity_type` **вне whitelist** поля `shift_id` / `line_id` / `session_open_event_id` в `UserActivity` **не заполняются** (запись в общий журнал остаётся).

### Whitelist `entity_type` (формат `app_label.model_name`)

Источник: `apps/activity/shift_audit.py` → `DEFAULT_AUDIT_SHIFT_ENTITY_TYPES`.

**В смене (привязка + отчёт):**

```
materials.rawmaterial
materials.materialbatch
chemistry.chemistrycatalog
chemistry.chemistrytask
chemistry.chemistrybatch
workshop.workshopblank
workshop.blankproductionrun
production.line
production.linehistory
production.order
production.productionbatch
production.reciperun
warehouse.warehousebatch
sales.client
sales.order
sales.orderline
sales.orderreservation
sales.sale
sales.saleline
sales.payment
sales.return
sales.defectrecord
sales.reworkrequest
production.shift
production.shiftnote
production.shiftcomplaint
```

**Вне смены** (в отчёт `?shift_id=` не попадают; `shift_id` в БД не ставится):

```
accounts.user
accounts.role
recipes.plasticprofile
recipes.recipe
sales.pricelist
sales.clientprice
analytics — мутаций в UserActivity нет
```

Отгрузки: отдельного `sales.shipment` в аудите нет; смотреть `sales.sale` и `payload.meta` / `description`.

Переопределение списка на сервере: env `AUDIT_SHIFT_ENTITY_TYPES=materials.rawmaterial,sales.sale,...`

### API журнала

| Метод | URL | Права |
|-------|-----|-------|
| GET | `/api/activity/my/?shift_id=<id>&page=&page_size=` | свой пользователь |
| GET | `/api/activity/my/<pk>/` | деталь события |
| GET | `/api/activity/?shift_id=<id>&user_id=<id>` | ключ доступа `shifts` |
| GET | `/api/activity/<pk>/` | админ деталь |

**Фильтр `shift_id`:**

- Только `entity_type` из whitelist.
- Строка попадает, если `shift_id = смена` **или** (legacy) `shift_id` пустой, но `created_at` в `[opened_at … closed_at]` и тип в whitelist.
- `date_from` / `date_to` при `shift_id` **игнорируются**.

**Ответ списка (пагинация проекта):**

```json
{
  "items": [ { "id", "entity_type", "entity_id", "action", "section", "description", "summary", "shift_id", "occurred_at", "payload", ... } ],
  "meta": { "total", "page", "perPage", "totalPages" },
  "links": { "next", "previous" }
}
```

Не `results` — **`items`**.

### Смена отдельно от API-аудита

Эти сущности тоже в whitelist и в UI «История смены», но часть UI может грузить отдельные эндпоинты:

| Что | API |
|-----|-----|
| Открытие/закрытие | `POST /api/shifts/open/`, `POST /api/shifts/close/` |
| Заметки | `GET/POST /api/shifts/notes/` |
| Жалобы | `GET/POST /api/shifts/complaints/` |
| Текущая смена | `GET /api/shifts/my/` (или как у вас назван `getMyShift`) |

---

## Что сделать на фронте

### 1. Глобальный store смены для аудита

```ts
// Пример: zustand / context
type AuditShiftState = {
  auditShiftId: number | null;
  setAuditShiftId: (id: number | null) => void;
};
```

**Правила:**

- После успешного `getMyShift()` / `GET /api/shifts/my/`: если смена открыта (`closed_at == null`) → `setAuditShiftId(shift.id)`.
- После `close` смены → `setAuditShiftId(null)`.
- При logout → `null`.
- **Не сбрасывать** при уходе с роута «Моя смена» — только при закрытии смены или logout.

При старте приложения (если уже есть токен): один раз подтянуть `my shift` и выставить id.

### 2. HTTP interceptor

На **все** мутации (`POST`, `PUT`, `PATCH`, `DELETE`):

```ts
if (auditShiftId != null && isAuditShiftRoute(config.url, config.method)) {
  config.headers['X-Audit-Shift-Id'] = String(auditShiftId);
}
```

`isAuditShiftRoute` — см. §3. Для `GET` заголовок **не обязателен** (бэкенд сам привяжет по открытой смене), но можно слать и на GET — без вреда.

Не дублировать, если заголовок уже задан вручную.

### 3. Whitelist маршрутов (синхрон с ACCESS_KEYS / вкладками)

Сопоставление **префиксов API** (axios `baseURL` + path):

| Вкладка / зона | Префиксы path | Шлём `X-Audit-Shift-Id` на мутации |
|----------------|---------------|-------------------------------------|
| materials | `/api/raw-materials`, `/api/incoming`, `/api/material-batches` | да |
| chemistry | `/api/chemistry/` | да |
| workshop (заготовка) | `/api/workshop/` | да |
| lines | `/api/lines/` (open, close, shift-*, pause, resume) | да |
| production | `/api/batches`, `/api/production/`, `/api/orders` (цех) | да |
| otk | `/api/batches/{id}/otk_accept` | да |
| warehouse | `/api/warehouse/` | да |
| clients | `/api/clients/` | да |
| client_orders | `/api/orders/` (sales orders) | да |
| sales | `/api/sales/` | да |
| payments | `/api/payments/` | да |
| returns | `/api/returns/` | да |
| defects | `/api/defects/` | да |
| rework | `/api/rework/` | да |
| my_shift / shifts | `/api/shifts/` (open, close, notes, complaints) | да |
| users | `/api/users/`, `/api/roles/` | **нет** |
| recipes | `/api/plastic-profiles/`, `/api/recipes/` | **нет** (справочник) |
| analytics | `/api/analytics/` | **нет** (только GET) |
| pricelist | `/api/price-lists/`, client prices | **нет** |

Уточни точные path по своему `api client` / OpenAPI (`/api/schema/`).

Пример проверки:

```ts
const AUDIT_SHIFT_PATH_PREFIXES = [
  '/api/raw-materials', '/api/incoming', '/api/material',
  '/api/chemistry/', '/api/workshop/',
  '/api/lines/', '/api/batches', '/api/production/',
  '/api/warehouse/',
  '/api/clients/', '/api/orders/', '/api/sales/',
  '/api/payments/', '/api/returns/', '/api/defects/', '/api/rework/',
  '/api/shifts/',
];

const MUTATION_METHODS = new Set(['post', 'put', 'patch', 'delete']);

function isAuditShiftRoute(url: string, method?: string): boolean {
  if (!method || !MUTATION_METHODS.has(method.toLowerCase())) return false;
  const path = url.split('?')[0];
  return AUDIT_SHIFT_PATH_PREFIXES.some((p) => path.includes(p));
}
```

### 4. UI «Действия за смену»

- Источник: `GET /api/activity/my/?shift_id={closedShiftId}&page_size=50` (пагинация `page`, `page_size`).
- Показывать `section` / `summary` / `action_display`; деталь — `GET /api/activity/my/{id}/` при `has_detail`.
- **Не фильтровать** на клиенте по `entity_type` для скрытия users — бэкенд уже режет; клиентский фильтр только для UX-группировки по желанию.
- В разделе **Смены** (чужой сотрудник): `GET /api/activity/?shift_id=&user_id=` при праве `shifts`.

### 5. Старт производства (уже было)

Кратко слать `X-Audit-Shift-Id` при `POST` старта заявки в производство — оставить; теперь это часть общего interceptor.

### 6. Проверка (чеклист)

- [ ] Открыть смену на «Моя смена», перейти в Клиенты, изменить клиента → в Network есть `X-Audit-Shift-Id`.
- [ ] Тот же сценарий в Сотрудники → заголовка **нет**.
- [ ] Закрыть смену → заголовок пропал на следующих мутациях.
- [ ] «Действия за смену» после закрытия: нет строк `accounts.user`, есть `sales.client` / производство.
- [ ] Reload приложения при открытой смене → заголовок снова есть без захода на «Моя смена».

---

## Ошибки / краевые случаи

- Неверный `X-Audit-Shift-Id` (чужая смена) — бэкенд игнорирует override и fallback на свою открытую смену.
- Несколько открытых смен (линия + личная) без заголовка — бэкенд берёт **последнюю по `opened_at`**. Фронт должен слать явный id личной смены с `my shift`.
- `activity` с `shift_id` закрытой смены: только whitelist; старые записи users в интервале смены **не отображаются**.

---

## Файлы бэкенда для сверки

| Файл | Содержание |
|------|------------|
| `apps/activity/shift_audit.py` | Whitelist, фильтр GET, policy записи |
| `apps/activity/audit_service.py` | `schedule_user_activity` + policy |
| `apps/activity/views.py` | `ActivityMyView`, `ActivityAdminView` |
| `apps/activity/shift_context.py` | Разбор заголовка |
| `config/settings.py` | CORS headers, `AUDIT_SHIFT_ENTITY_TYPES` |

---

## Итог для агента

Реализуй **глобальный `auditShiftId` + axios/fetch interceptor** по whitelist путей. Приведи экран «Действия за смену» к `items` + `shift_id`. Не полагайся на фильтр только в UI. После этого журнал смены на бэке и фронте совпадают.
