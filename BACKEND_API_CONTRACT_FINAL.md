# Backend API — заявки на производство и клиенты (финальный контракт)

## Клиенты `Client`

**CRUD:** `GET/POST /api/clients/`, `GET/PATCH /api/clients/{id}/`

| Поле | Описание |
|------|----------|
| `name` | Название |
| `phone` | Телефон |
| `phone_extra` | Доп. телефон (в БД: `phone_alt`, то же что `phone_alt`) |
| `status` | `active` / `inactive` (в БД: `is_active`) |
| `comment` | Комментарий (в БД: `notes`, то же что `notes`) |

Прочие поля (`contact`, `inn`, `address`, `credit_limit`, …) сохранены для совместимости.

**Удаление:** отключено; деактивация через `PATCH` с `status: inactive` или `is_active: false`.

---

## Заявка клиента на производство (`client_orders`, модель `sales.Order`)

Таблица БД: `client_orders`. Для производственной ветки используются поля **в дополнение** к существующим (`order_number`, `date`, `status` отгрузочного контура и т.д.).

### Поля производства (в JSON)

| Поле | Read | Write | Примечание |
|------|------|-------|------------|
| `profile` | ✓ | ✓ только если `request_status` **draft**, **not_ready** (или `null`, пока нет ветки производства) | FK `PlasticProfile` |
| `length` | ✓ | ✓ — те же условия, что у `profile` | Длина одной штуки, м |
| `quantity` | ✓ | ✓ — те же условия, что у `profile` | Количество штук |
| `request_status` | ✓ | ✗ | Только сервер (`approve` / `reject` / логика ниже) |
| `recipe` | ✓ | ✗ | Снимок `resolved_recipe` после проверки |
| `total_meters` | ✓ | ✗ | `length * quantity`, дублируется в `request_total_meters` |
| `resource_check` | ✓ | ✗ | JSON-снимок проверки ресурсов |

Создание **производственной** заявки: в теле указать `client`, `date` (опционально), `profile`, `length`, `quantity` — **без** обязательного массива `lines` (старый сценарий с `lines` без этих трёх полей не меняется).

---

## Статусы производства `request_status`

| Значение | Смысл |
|----------|--------|
| `draft` | Черновик, можно править profile/length/quantity |
| `approved` | Заявка принята; дальше запускается проверка ресурсов (следующий шаг в БД — `checking`) |
| `rejected` | Отказ |
| `checking` | Идёт проверка остатков (сырьё/химия) по рецепту; после — `ready` или `not_ready` |
| `ready` | Ресурсов достаточно, заявка в очереди производства |
| `not_ready` | Не хватает сырья/химии; доступна **повторная проверка** (`recheck`) |
| `in_production` | Запущена партия (`POST /api/production/requests/{id}/start/`) |

**Цепочка после «Принять»:** `draft` → `approved` (фиксируется в БД) → `checking` (фиксируется) → `ready` **или** `not_ready` (фиксируется). Один запрос `POST /approve/`, три последовательных коммита транзакции, чтобы при опросе API были видны «приняли» и «идёт проверка».

**Повтор из `not_ready`:** `not_ready` → `checking` → `ready` | `not_ready` (см. `recheck`).

Статус отгрузки заявки (`status`: new, confirmed, …) **отдельный**, не смешивается с `request_status`.

---

## Новые endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/orders/{id}/approve/` | Из `draft`: последовательно `approved` → `checking` → `ready` / `not_ready` (три фиксации в БД) |
| `POST` | `/api/orders/{id}/recheck/` | Из `not_ready`: `checking` → `ready` / `not_ready` (повторный расчёт плановой проверки) |
| `POST` | `/api/orders/{id}/reject/` | Отказ → `rejected` (не из `in_production`) |
| `GET` | `/api/production/requests/` | Список заявок с `request_status=ready` |
| `POST` | `/api/production/requests/{id}/start/` | Старт партии; тело: `{ "line": <id линии> }` |

Фильтр заявок: `GET /api/orders/?request_status=...` (параметр `request_status`).

---

## Проверка ресурсов после `approve` и `recheck`

1. `total_meters = length * quantity` (как в `request_total_meters`).
2. Рецепт: первый **активный** `Recipe` для выбранного профиля (`order_by id`).
3. По `RecipeComponent`: для `raw` — остатки сырья (сумма `quantity_remaining` по `MaterialBatch` в **кг**); для `chem` — остатки химии (`ChemistryBatch`).
4. Потребность: `quantity_per_meter * total_meters` (как в `aggregate_consumption_for_recipe` / партии).
5. В `resource_check` / `resource_check_snapshot` сохраняется список позиций: тип, id, имя, `needed`, `available` (строки decimal API), `enough`, `unit`.
6. Итог: `ready` если **все** позиции с ненулевой потребностью имеют `enough=true`; иначе `not_ready` (в т.ч. нет рецепта / пустой рецепт).

---

## `POST /api/production/requests/{id}/start/`

- `{id}` — id **заявки клиента** (`sales.Order`).
- Условие: `request_status == ready`, заданы профиль, рецепт, длина, количество; по заявке ещё нет партии.
- Создаётся `ProductionBatch`: `profile`, `recipe` из заявки, `pieces=quantity`, `length_per_piece=length`, выбранная `line`, смена текущего пользователя (как при `POST /api/batches/`: открытая смена на линии, не на паузе).
- Списание: `apply_production_batch_stock_and_cost` — **FIFO** сырья и химии.
- К партии привязывается `client_order_id`; у заявки `request_status` → `in_production`.
- Далее существующий контур: ОТК, `WarehouseBatch` и т.д.

**Ограничения:** нельзя стартовать при `status != ready`; рецепт в заявку не передаётся с фронта; расчёты только на backend.

---

## Блокировка полей (PATCH заявки)

**Нельзя** менять `profile`, `length`, `quantity`, если `request_status` равен одному из:

- `approved`
- `checking`
- `ready`
- `in_production`

Редактирование **разрешено** в `draft` и `not_ready`. При **PATCH** `profile` / `length` / `quantity` в `not_ready` заявка сбрасывается в `draft` (нужен снова `POST /approve/`).

---

## Read-only с фронта

- `order_number`, `request_status`, `recipe`, `total_meters`, `resource_check`, `resolved_recipe` (как отдельное поле), `request_total_meters`, `resource_check_snapshot` в PATCH — **не** передавать; сервер отклонит `request_status`, `resolved_recipe`, `request_total_meters`, `resource_check_snapshot`.

---

## Пример тел для фронта

**Создать заявку на производство**

```json
{
  "client": 1,
  "date": "2026-04-27",
  "profile": 3,
  "length": "2.5",
  "quantity": 10
}
```

**Принять / отклонить / повторная проверка**

- `POST /api/orders/42/approve/` — пустое тело (или `{}`); из `draft`.
- `POST /api/orders/42/reject/` — пустое тело.
- `POST /api/orders/42/recheck/` — пустое тело; только из `not_ready`.

**Старт производства**

- `POST /api/production/requests/42/start/`  
  `{"line": 1}`

---

## Модель `ProductionBatch`

Новое поле: `client_order` → FK на `sales.Order` (заявка клиента), если партия создана из производственной заявки. В ответе списка партий присутствует поле `client_order` (id).
