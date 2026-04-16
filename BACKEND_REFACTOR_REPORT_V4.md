# BACKEND_REFACTOR_REPORT_V4

Отчёт по фактическим правкам бэкенда после `BACKEND_AUDIT_DOC_V3.md`. Бизнес-цепочка (сырьё → химия → рецепт → `ProductionBatch` → ОТК → склад → упаковка → продажи), FIFO, отдельные warehouse batch для good/defect не менялись по смыслу.

---

## Что было исправлено

### По endpoint

| Область | Endpoint / действие | Изменение |
|---------|---------------------|-----------|
| Рецепты | `GET /api/recipes/{id}/availability/` | Структурированный ответ: `mode`, `total_meters`, `all_sufficient`, `components[]` с типом, именем, нормой на 1 м, потребностью, остатком, `shortage_kg`, `sufficient`. Режимы query: `mode=per_meter` (по умолчанию, 1 м) и `mode=for_production` с `total_meters` **или** `pieces` + `length_per_piece`. Числа — строки (`api_decimal_str`). |
| Партии | `GET/PATCH/POST /api/batches/` (list/detail) | Убраны дубли имён/алиасов в списке: нет `quantity`/`released`/`recipe_label`/`line_label`/`product_name` как дубль; метры и деньги через `coerce_to_string` / строки для ОТК-полей. |
| ОТК | pending-список | Стабильная сортировка: `-otk_submitted_at`, `-id`. |
| Производство | `RecipeRun` destroy | `perform_destroy`: безопасная проверка заказа до `.batches.exists()` (нет падения при `order=None`). |
| История смены | session history | Убран дубль ключа `pauseResume` (остаётся `pause_resume`). |
| Склад | warehouse list/detail, package | Единый `inventory_form` в ответе; убраны дубли `stock_form` / `packaging_status` и прочие алиасы упаковки/геометрии; количества/стоимости — предсказуемые строки где нужно. |
| Продажи | clients, sales | Клиент: запись `contact_person` / `whatsapp_telegram` только write-only; чтение — `contact` / `messenger`. У продаж убраны дубли `cost_total`/`sale_date`/`quantity_unit` из выдачи; суммы — строки. |
| Химия | chemistry batches / catalog list | Убраны дубли `unit_cost`/`total_cost` в API партии; балансы — строки. |
| Рецепты (CRUD) | recipe / components | Убраны лишние поля в выдаче (`yield_quantity`/`output_measure` и т.п. по сериализатору); компонент — одно имя `name`. |
| Аналитика | `GET` summary | Финансы без лишних дублей ключей; `material_flow` и тренды — строковые decimal; производство в сводке: **`production.total_meters`** = сумма **`total_meters`** партий (не legacy `quantity`); блок **`otk_batches_by_status`**: поле метров — `total_meters` (сумма `total_meters` партий по статусу ОТК). Убран отдельный верхнеуровневый дубль склада; склад: `snapshot` + `new_in_period`. |
| Аналитика | revenue / expense / writeoff details | Числа — `api_decimal_str`; закупки: убраны дубли `quantity_initial`, `price_per_unit`, `incoming_id` в строке; списания: убраны алиасы суммы строки и `note`, корень без `total_estimated_value`. |

### По моделям

- Поля legacy в БД (**`ProductionBatch.quantity`**, **`Sale.quantity`**, **`cost_price`** и т.д.) **не удалялись**; при необходимости заполняются/используются внутри `save()` и сервисов, но **не дублируются** в публичном JSON там, где это мешало фронту.

### По сериализаторам

- `apps/production/serializers.py` — список партий, линии/смены/история: меньше дублей, стабильные ключи, размеры смены строками.
- `apps/warehouse/serializers.py` — единый контракт склада/упаковки.
- `apps/sales/serializers.py` — клиенты и продажи.
- `apps/chemistry/serializers.py` — партии и каталог.
- `apps/recipes/serializers.py` — рецепт и компоненты.

### По сервисам / утилитам

- `config/api_numbers.py` — **`api_decimal_str()`** для стабильного вывода decimal в JSON.

---

## Какие дубли убраны

| Старые / дублирующие ключи в API (примеры) | Канон для фронта |
|---------------------------------------------|------------------|
| `quantity` / `released` vs факт метража в списке партий | **`total_meters`**, **`pieces`**, **`length_per_piece`** |
| `recipe_label` / дубль имени рецепта | **`recipe_name`** (и снимки по контексту) |
| `line_label` | **`line_name`** или FK `line` |
| `contact_person` + `contact` на чтение | чтение: **`contact`**; запись legacy-полей отдельно |
| `whatsapp_telegram` + `messenger` | чтение: **`messenger`** |
| `stock_form` / `packaging_status` vs форма учёта | **`inventory_form`** |
| `cost_total` / `unit_cost` (химия, продажи) | **`material_cost_total`** / обозначенные в сериализаторе поля без дубля имён |
| `pauseResume` | **`pause_resume`** |
| `warehouse_finished_goods` (дубль смысла со snapshot) | **`warehouse.snapshot`** + **`warehouse.new_in_period`** |
| `daily_purchases` / дубли трендов | **`daily_material_purchases`**, **`daily_production_meters`** |
| Закупки detail: `quantity_initial`, `price_per_unit`, `incoming_id` | **`quantity`**, **`unit_price`**, `id` |
| Списания detail: `raw_material_name`, `name`, `estimated_value`, `amount`, … | **`material_name`**, **`fifo_line_total`** |

---

## Что изменено в контрактах API (до / после, кратко)

- **Сводка аналитики:** числовые блоки отдаются **строками** (один стиль для UI). **`production.total_quantity`** заменено на **`production.total_meters`** (сумма `ProductionBatch.total_meters`). В **`otk_batches_by_status`** вместо `quantity` (legacy сумма) — **`total_meters`** по партиям.
- **`finances` / `material_flow` / `sales` / `trends` / `otk`:** убраны float там, где были; значения через **`api_decimal_str`**.
- **`GET …/recipes/{id}/availability/`:** вместо сырого вложенного JSON — плоский список **`components`** с человекочитаемыми полями (см. выше).
- **Детализация списаний сырья:** меньше полей в строке; **`total`** строка; без `note` и без набора алиасов суммы.

---

## Что исправлено в числах и decimal

- Дробные нормы, деньги, метры, остатки в затронутых ответах приводятся к **строкам** фиксированного plain-формата (`format_decimal_plain` через **`api_decimal_str`**), без скрытого `float` в JSON для этих величин.
- Внутренние расчёты сводки аналитики для ключевых сумм переведены на **`Decimal`** там, где раньше промежуточно использовался `float`.
- ОТК доля брака: **`defect_rate_pct`** как decimal с квантованием, затем строка.

---

## Что исправлено в recipe availability

- **URL:** `GET /api/recipes/{id}/availability/`
- **Query:** `mode` = `per_meter` | `for_production`; для расчёта объёма: `total_meters` или `pieces` + `length_per_piece`.
- **Ответ:** `mode`, `total_meters`, `all_sufficient`, массив **`components`**: `component_type`, `name`, `norm_per_meter_kg`, `required_total_kg`, `available_kg`, `shortage_kg`, `sufficient`, плюс идентификаторы для связи с формами.

---

## Что проверить вручную

1. Создание партии `POST /api/batches/` и чтение list/detail — поля без дублей, метры/стоимости строками.
2. `GET /api/recipes/{id}/availability/` без параметров (экв. 1 м) и с `mode=for_production&total_meters=100`.
3. То же с `pieces` + `length_per_piece` вместо `total_meters`.
4. ОТК: очередь pending — порядок по дате отправки.
5. ОТК accept — без регрессий по сумме штук/метрам.
6. Склад: list, reserve, package — ключ **`inventory_form`**, без старых алиасов.
7. Продажа create/list/detail — нет лишних полей, decimal стабилен (в т.ч. **0.80**, **0.001**, длинные **147.0000** где применимо).
8. Сводка аналитики и три detail-endpoint (выручка / закупки / списания).
9. Удаление `RecipeRun` при отсутствии заказа — ответ без 500.
10. Любой экран, завязанный на **`production.total_quantity`** или **`otk_batches_by_status[].quantity`** в analytics — обновить на новые ключи.

---

## Примечание по окружению

Локальный `manage.py check` в сессии агента упал из‑за отсутствия пакета `django-cors-headers` в используемом интерпретаторе; после `pip install -r requirements.txt` в вашем venv проверку стоит повторить.
