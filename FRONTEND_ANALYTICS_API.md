# Аналитика: API для фронтенда

Базовый префикс: `/api/`. Аутентификация: заголовок `Authorization: Bearer <token>` (права `analytics`).

Все перечисленные ниже эндпоинты — **GET**, ViewSet `list`, URL с **завершающим слэшем** (как у `DefaultRouter`).

---

## Общие query-параметры (период и фильтры)

Используются в: `summary`, `revenue-details`, `expense-details`, `profit-details`, `otk-details`.

| Параметр     | Тип    | Описание |
|-------------|--------|----------|
| `year`      | int    | Год; если не задан при отсутствии `date_from`/`date_to`, подставляется текущий год (см. бэкенд `parse_period`). |
| `month`     | int    | 1–12 или пусто = весь год. |
| `day`       | int    | День или пусто. |
| `date_from` | string | `YYYY-MM-DD`, фильтр по полю даты сущности (продажи — `Sale.date`, производство — `ProductionBatch.date`). |
| `date_to`   | string | `YYYY-MM-DD`, включительно. |
| `line_id`   | int    | Линия: производство — `ProductionBatch.line_id`; продажи — через `Sale.warehouse_batch.source_batch.line_id`; склад — `WarehouseBatch.source_batch.line_id`. |
| `client_id` | int    | Только продажи / прибыль / выручка (не склад). |
| `profile_id`| int    | Профиль: продажи через склад; производство `ProductionBatch.profile_id`; склад `WarehouseBatch.profile_id`. |
| `recipe_id` | int    | Рецепт на производстве и в цепочке склада от партии. |
| `batch_id`  | int    | **Партия производства** (`ProductionBatch.id`): производство, ОТК, продажи через склад, склад. |
| `otk_status`| string | Только `pending` \| `accepted` \| `rejected` — фильтр `ProductionBatch.otk_status`. Иное значение на бэкенде **игнорируется** (не передавайте сюда статусы склада). |
| `trend_group` | string | Только для `summary`: `day` \| `month`. |

При указании `date_from` / `date_to` границы периода для агрегатов берутся из них; иначе — из комбинации `year` / `month` / `day`.

---

## 1. Сводка

```http
GET /api/analytics/summary/?year=2026&month=4&trend_group=day
Authorization: Bearer <token>
```

Пример на TypeScript:

```ts
const params = new URLSearchParams({
  year: '2026',
  month: '4',
  trend_group: 'day',
});
const res = await fetch(`/api/analytics/summary/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
const data = await res.json();
```

Ключи ответа: `period`, `trend_group`, `cards`, `otk_summary`, `warehouse_summary`, `production_summary`, `trends`, `sales_by_profile`, `sales_by_client`, `production_by_line`.

---

## 2. Детализация выручки

```http
GET /api/analytics/revenue-details/?year=2026&client_id=5
Authorization: Bearer <token>
```

```ts
const params = new URLSearchParams({ year: '2026', client_id: '5' });
const res = await fetch(`/api/analytics/revenue-details/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
const { total, items } = await res.json();
```

Элемент `items[]`: `id`, `date`, `client_name`, `profile_id`, `profile_name`, `product_name`, `quantity`, `price_per_unit`, `revenue`.

---

## 3. Детализация расходов

```http
GET /api/analytics/expense-details/?year=2026&expense_type=all
```

`expense_type`:

- `all` — только строки **sales_cost** и **production_cost** (главный экран расходов).
- `sales_cost` | `production_cost` — один тип.
- `material_writeoff` | `purchase_cost` — внутренние / справочные типы.

```ts
const params = new URLSearchParams({
  year: '2026',
  expense_type: 'all',
});
const res = await fetch(`/api/analytics/expense-details/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
```

Ответ: `period`, `expense_type`, `totals_by_type`, `total`, `items` (поля строки см. OpenAPI / бэкенд).

---

## 4. Детализация прибыли

```http
GET /api/analytics/profit-details/?date_from=2026-04-01&date_to=2026-04-30
```

```ts
const params = new URLSearchParams({
  date_from: '2026-04-01',
  date_to: '2026-04-30',
});
const res = await fetch(`/api/analytics/profit-details/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
const { period, totals, items } = await res.json();
```

`totals`: `revenue`, `cost`, `profit`. `items[]`: `id`, `date`, `order_number`, `client_name`, `product_name`, `profile_id`, `profile_name`, `revenue`, `cost`, `profit`.

---

## 5. Детализация ОТК

```http
GET /api/analytics/otk-details/?year=2026&batch_id=12
```

Партии производства задают набор `OtkCheck`; фильтр `otk_status` сужает партии до нужного статуса.

```ts
const params = new URLSearchParams({ year: '2026', otk_status: 'accepted' });
const res = await fetch(`/api/analytics/otk-details/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
const { period, items } = await res.json();
```

`items[]`: `id`, `date`, `batch_id`, `profile_id`, `profile_name`, `accepted`, `defect`, `defect_percent`, `check_status`.

---

## 6. Списания сырья (внутренний учёт)

```http
GET /api/analytics/writeoff-details/?year=2026&month=3
```

**Обязателен** `year`. Параметры только календарные (`year`, `month`, `day`), без фильтров профиля/линии.

---

## Коды ответов

- `200` — успех.
- `401` / `403` — нет токена или нет права `analytics`.
- `400` — у `writeoff-details` без `year` вернётся ошибка валидации.

---

## Пример периода только по датам

```ts
const params = new URLSearchParams({
  date_from: '2026-01-01',
  date_to: '2026-01-31',
  line_id: '2',
});
await fetch(`/api/analytics/summary/?${params}`, {
  headers: { Authorization: `Bearer ${token}` },
});
```
