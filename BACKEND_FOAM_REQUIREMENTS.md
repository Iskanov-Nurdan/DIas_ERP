# DIAS — API Requirements: линия «Пенополистирол» (пеноплат)

**Статус на 2026-08-10: линия существует на фронте только как визуальный прототип на моках** (`src/features/foam/mockData.js`, `src/features/foam/store.js`) — состояние живёт в памяти вкладки браузера через `useSyncExternalStore`, **ни одного обращения к API нет**. Задача этого документа — описать API, который нужен бэкенду, чтобы фронт перестал быть локальным и начал реально сохранять данные на сервере.

Общие соглашения (авторизация, пагинация, формат дат, формат ошибок, именование полей) — **те же, что в `BACKEND_REQUIREMENTS.md` §1**, не дублируются здесь. Base URL — тот же `/api/`.

---

## 0. Контекст

- Вторая, физически не связанная с «Пластиковый профиль» продуктовая линия: **свой склад сырья** (`FOAM_WAREHOUSE_RAW`), **своё производство**, **свой склад ГП** (`FOAM_WAREHOUSE_GP`). Не смешивать с существующими `raw-materials/`, `incoming/`, `workshop/*`, `warehouse/gp-stock/` — своя другая гранулярность данных (партии/рецепты/хим. состав), здесь — биг-бэги гранул и всего два формата выхода.
- **Права доступа — те же, что уже есть**: `materials`, `production`, `warehouse`, `sales` (см. `BACKEND_REQUIREMENTS.md` §2.1). Отдельный `access_key` под линию заводить не нужно.
- **Переключатель «Пластиковый профиль» / «Пенополистирол»** (`ProductLineTabs`) — чисто фронтовый, состояние в `localStorage` (`src/shared/hooks/useProductLine.js`), бэку про него знать не нужно — это не влияет на выбор эндпоинта, эндпоинты просто отдельные (`foam/...`).
- **ОТК для этой линии не нужен вообще.** Куб/гранулы входят на склад ГП сразу при выпуске из производства, без отдельного этапа проверки/приёмки.
- «Лист» — не производственный формат. Он появляется только на складе: куб (всегда один физический размер) режут на листы нужной толщины.

---

## 1. Бизнес-константы (расчёты, которые должен воспроизводить бэк)

| Константа | Значение | Где используется |
|---|---|---|
| Технологические потери при обработке | **3.5%** | Выход при выпуске производства |
| Размер куба | высота 60 см × ширина 100 см × длина 200 см = **1.2 м³** | Вес куба, нарезка на листы |
| Толщина нарезки листа | 2 / 3 / 4 см (не жёсткий enum — форма фронта позволяет ввести любую разумную толщину, но в UI сейчас предлагаются три эти) | Нарезка куба на складе |
| Плотность (грейд) | своя фабричная шкала, **не ГОСТ-марки**: `6` (5–7 кг/м³), `12` (10–12 кг/м³), `14-15` (13–15.5 кг/м³) — это **справочник, редактируемый пользователем** (форма «Добавить плотность»), не жёстко зафиксированный enum | Вес куба/листа, доступна только для формата «Куб»/«Лист»; у гранул на продажу плотности нет |

**Формула расчёта выхода производства (должны считаться на бэке — не доверять числу с фронта):**

```
usable_kg = input_kg * (1 - 0.035)

# формат "куб":
mid_density_kg_m3 = (grade.min_kg_m3 + grade.max_kg_m3) / 2
cube_weight_kg = mid_density_kg_m3 * 1.2   # объём куба, м³
output_qty = round(usable_kg / cube_weight_kg, 1)   # дробное число кубов — это нормально, льёт по объёму

# формат "гранулы на продажу" (без деления на плотность, считается напрямую в кг):
output_qty = round(usable_kg, 1)
```

**Нарезка куба на листы (на складе):**
```
sheets_per_cube = floor(60 / thickness_cm)
sheets_qty = floor(sheets_per_cube * cubes_qty)
```

Вес листа/куба для отображения (`≈ вес` в таблицах остатков) фронт продолжает считать **сам**, на основе `qty` + справочника плотностей — бэку отдельное поле веса в ответе не нужно.

---

## 2. Сырьё — второй склад (`materials`)

### GET /api/foam/raw-lots/
Лоты сырья (биг-бэги гранул). Список.

**Auth:** Bearer, `access=materials`. **Query:** `page`, `page_size`, `search`, `ordering`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 1,
      "lot_number": "KG-2607-01",
      "material_name": "Гранула EPS Kingeps HS",
      "supplier": "Kingeps",
      "bag_weight_kg": "800.0",
      "received_kg": "800.0",
      "remaining_kg": "560.0",
      "received_at": "2026-07-24T09:10:00+06:00",
      "warehouse": "Склад сырья №2 — Пенополистирол"
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 4 }
}
```
`material_name`/`supplier` — свободный текст (в форме прихода нет справочника-каталога, просто текстовые поля) — не нужен отдельный каталог сырья, только текстовые поля на лоте. `status` (`in_stock`/`low`/`empty`) можно **не отдавать** — фронт вычисляет его сам на лету из `remaining_kg`/`bag_weight_kg` (`remaining_kg <= 0` — «нет остатка», `remaining_kg <= bag_weight_kg × 0.15` — «остаток мал», иначе «в наличии»).

### POST /api/foam/raw-lots/
Приход лота сырья.

**Request:**
```json
{ "material_name": "Гранула EPS Kingeps HS", "supplier": "Kingeps", "bag_weight_kg": "800" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| material_name | string | да |
| supplier | string | нет |
| bag_weight_kg | string/number decimal, > 0 | да |

**Response 201:** созданный лот. `lot_number` генерируется на бэке (сейчас на фронте — `KG-{timestamp}`, можно сохранить свою схему нумерации), `received_kg = remaining_kg = bag_weight_kg`, `received_at = now()`.

### GET /api/foam/density-grades/
Справочник плотностей.

**Response 200:** `{ "items": [ { "code": "12", "min_kg_m3": "10.0", "max_kg_m3": "12.0" } ] }` (без пагинации — справочник маленький, весь список сразу).

### POST /api/foam/density-grades/
Добавить плотность в справочник.

**Request:** `{ "code": "20", "min_kg_m3": "18", "max_kg_m3": "20" }`
| Поле | Тип | Обязательно |
|---|---|---|
| code | string, уникален | да |
| min_kg_m3 | decimal > 0 | да |
| max_kg_m3 | decimal ≥ min_kg_m3 | да |

**Response 201:** созданная плотность. **Errors:** `409` — код уже существует (фронт сейчас сам проверяет уникальность на клиенте, но это должно быть продублировано на бэке).

> Движения сырья («Приход»/«Списано в производство») отдельного эндпоинта не требуют — фронт строит журнал сам, объединяя `raw-lots[].received_at` (приходы) и `production-runs[].produced_at`+`input_kg` (списания). Заводить `GET /api/foam/materials/movements/` не обязательно.

---

## 3. Производство (`production`)

### GET /api/foam/production-runs/
Выпуски производства.

**Auth:** `access=production`. **Query:** `page`, `page_size`, `lot_id`, `date_from`, `date_to`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 2,
      "lot_id": 3,
      "lot_number": "KG-2607-03",
      "material_name": "Гранула EPS Kingeps HP",
      "grade_code": "14-15",
      "input_kg": "90.0",
      "output_format": "cube",
      "output_qty": "5.0",
      "produced_at": "2026-07-25T16:45:00+06:00",
      "operator": "Дюбол С."
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 4 }
}
```
`grade_code` присутствует только для `output_format: "cube"` — у гранул на продажу плотности нет, поле `null`/отсутствует.

### POST /api/foam/production-runs/
Запустить производство: списывает `input_kg` с лота, **бэк сам считает `output_qty`** по формулам из §1 (не доверять числу, присланному фронтом — фронт присылает его только для предпросмотра в форме, авторитетное значение должно быть с бэка), пополняет остаток склада ГП, создаёт запись движения склада (`kind: production_intake`).

**Request:**
```json
{ "lot_id": 3, "input_kg": "90", "output_format": "cube", "grade_code": "14-15" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| lot_id | number | да |
| input_kg | decimal > 0, ≤ остаток лота | да |
| output_format | string enum: `cube` \| `granule` | да |
| grade_code | string, код из справочника плотностей | да, если `output_format = cube`; игнорируется для `granule` |

`operator` в теле **не нужен** — бэк берёт текущего пользователя из токена и подставляет отображаемое имя сам (сейчас фронт подставляет `user.name`/`user.role_name`).

**Response 201:** созданный прогон производства (структура как в GET, с посчитанным `output_qty`).

**Errors:** `400` — `input_kg` больше остатка лота, `grade_code` не найден в справочнике (для `cube`), `lot_id` не существует или остаток 0.

**Побочный эффект (в одной транзакции):**
1. `lot.remaining_kg -= input_kg` (не уходит ниже 0).
2. Строка склада ГП с ключом `(output_format, grade_code)` — создать, если ещё нет, иначе `qty += output_qty`.
3. Запись в `foam/gp-operations/`: `kind: "production_intake"`, `output_format`, `grade_code` (если куб), `qty: output_qty`, `ref` = id созданного прогона.

---

## 4. Склад ГП (`warehouse`)

### GET /api/foam/gp-stock/
Остатки склада готовой продукции пенопласта, сгруппированные по вариантам товара.

**Response 200:**
```json
{
  "items": [
    { "id": 10, "output_format": "granule", "grade_code": null, "thickness_cm": null, "qty": "150.0", "warehouse": "Склад ГП — Пенопласт" },
    { "id": 11, "output_format": "cube", "grade_code": "14-15", "thickness_cm": null, "qty": "5.0", "warehouse": "Склад ГП — Пенопласт" },
    { "id": 12, "output_format": "sheet", "grade_code": "12", "thickness_cm": 3, "qty": "84", "warehouse": "Склад ГП — Пенопласт" }
  ]
}
```
**Важно:** `id` — реальный числовой первичный ключ строки остатка (уникальная комбинация `output_format` + `grade_code` + `thickness_cm`). На фронте сейчас вместо этого синтетический строковый `key` (`'granule'`, `'cube-14-15'`, `'sheet-12-3'`), собранный на клиенте — при переходе на реальный бэк это поле **должно замениться на `id` с бэка** (см. §6 «Что поменять на фронте»). Без пагинации — вариантов немного, отдаются целиком.

### GET /api/foam/gp-operations/
Лента движений склада ГП (для вкладки «История»).

**Query:** `page`, `page_size`, `date_from`, `date_to`, `kind`, `output_format`.
**Response 200:**
```json
{
  "items": [
    { "id": 1, "kind": "sale", "output_format": "granule", "grade_code": null, "thickness_cm": null, "qty": "-23.7", "created_at": "2026-07-25T19:45:00+06:00", "ref": "sale-2" }
  ],
  "meta": { "page": 1, "pages": 1, "total": 5 }
}
```
`qty` — со знаком: положительное увеличивает остаток (приход/нарезка-получено), отрицательное уменьшает (продажа/нарезка-списан куб/брак/возврат-минус). `kind` — см. enum в §5. `ref` — свободный текст/id связанного документа (id прогона производства, id продажи, текстовая пометка для ручных операций).

### POST /api/foam/gp-stock/cut/
Нарезать куб на складе на листы заданной толщины.

**Request:**
```json
{ "cube_stock_id": 11, "thickness_cm": 3, "cubes_qty": "1.5" }
```
| Поле | Тип | Обязательно |
|---|---|---|
| cube_stock_id | number (id строки остатка с `output_format="cube"`) | да |
| thickness_cm | number > 0 | да |
| cubes_qty | decimal > 0, ≤ остаток кубов на этой строке | да |

**Response 200/201:** обновлённые/созданные строки остатка (куб — уменьшенный `qty`, лист — новый/увеличенный `qty`).

**Errors:** `400` — `cubes_qty` больше остатка кубов, `cube_stock_id` не найден или не куб.

**Побочный эффект (одной транзакцией):**
1. `sheets_qty = floor(floor(60 / thickness_cm) * cubes_qty)`.
2. Строка куба: `qty -= cubes_qty`.
3. Строка листа `(sheet, grade_code куба, thickness_cm)` — создать или `qty += sheets_qty`.
4. Две записи в `gp-operations/`: `kind: "cut_in"` (+ `sheets_qty` листов) и `kind: "cut_out"` (− `cubes_qty` кубов), с одинаковым `created_at`.

> **Не реализовано в UI сейчас, но заложено в модель данных:** ручные операции брака/возврата по складу (`kind: "defect"` / `"return"`, произвольный `qty` со знаком на конкретной строке остатка) — в сторе-прототипе есть функция `recordWarehouseOperation`, но кнопки/формы для неё в `WarehouseFoamTab.jsx` нет. Отдельный эндпоинт под это заводить **не обязательно прямо сейчас** — оставляю как известный будущий сценарий, чтобы `kind` enum на бэке сразу учитывал эти два значения.

---

## 5. Продажи (`sales`)

### GET /api/foam/sales/
**Auth:** `access=sales`. **Query:** `page`, `page_size`, `search`, `client`, `payment_status`, `date_from`, `date_to`.

**Response 200:**
```json
{
  "items": [
    {
      "id": 1,
      "client": "ТОО СтройДом",
      "date": "2026-07-25T19:45:00+06:00",
      "lines": [
        { "stock_id": 10, "output_format": "granule", "grade_code": null, "thickness_cm": null, "qty": "23.7", "unit_price": "45.00" }
      ],
      "total_amount": "1066.50",
      "paid_amount": "1066.50",
      "debt_amount": "0.00",
      "payment_status": "paid"
    }
  ],
  "meta": { "page": 1, "pages": 1, "total": 1 }
}
```

### POST /api/foam/sales/
Продажа готовой продукции клиенту: списывает `qty` с указанных строк остатка склада ГП, создаёт запись о продаже и движения склада.

**Request:**
```json
{
  "client": "ТОО СтройДом",
  "sale_date": "2026-07-25",
  "lines": [ { "stock_id": 10, "qty": "23.7", "unit_price": "45" } ],
  "paid_amount": "1066.5"
}
```
| Поле | Тип | Обязательно |
|---|---|---|
| client | string (клиент вводится текстом, не выбирается из справочника `clients/`) | да |
| sale_date | string (ISO date) | да |
| lines | array<{stock_id: number, qty: decimal > 0 ≤ остаток строки, unit_price: decimal ≥ 0}>, минимум 1 | да |
| paid_amount | decimal, `0 ≤ paid_amount ≤ total_amount` | да |

`client` — именно свободный текст, **не** `client_id` из основного справочника клиентов (`clients/`) — форма продажи пенопласта сейчас не использует общий справочник клиентов, просто текстовое поле. Решите с продуктом, оставлять ли так или переиспользовать `clients/` — контракт ниже описывает то, что реально есть в форме сейчас (текст).

**Response 201:**
```json
{
  "id": 2, "client": "ТОО СтройДом", "date": "2026-07-25T00:00:00+06:00",
  "lines": [ { "stock_id": 10, "output_format": "granule", "qty": "23.7", "unit_price": "45.00" } ],
  "total_amount": "1066.50", "paid_amount": "1066.50", "debt_amount": "0.00", "payment_status": "paid"
}
```
`total_amount` = `Σ(lines.qty × lines.unit_price)`, посчитано на бэке (не доверять числу с фронта). `debt_amount = total_amount − paid_amount`. `payment_status`: `paid_amount <= 0` → `debt`; `0 < paid_amount < total_amount` → `partial`; `paid_amount >= total_amount` → `paid`.

**Errors:** `400` — `qty` по строке больше остатка на складе, `stock_id` не найден, `paid_amount` вне диапазона.

**Побочный эффект (одной транзакцией):** для каждой строки — `gp_stock[stock_id].qty -= line.qty` (не ниже 0) + запись в `gp-operations/` с `kind: "sale"`, `qty: -line.qty`, `ref` = id продажи.

---

## 6. Enum-справочник

`output_format`: `cube` (Куб) · `sheet` (Лист) · `granule` (Гранулы на продажу)

`gp-operations[].kind`: `production_intake` (Поступление с производства) · `sale` (Продажа) · `defect` (Брак, зарезервировано, UI пока нет) · `return` (Возврат, зарезервировано, UI пока нет) · `cut_in` (Нарезка листов — получено) · `cut_out` (Нарезка листов — списан куб)

`sales[].payment_status`: `paid` (Оплачено) · `partial` (Частично оплачено) · `debt` (Долг)

---

## 7. Что должно поменяться на фронте (для полноты картины, не для бэка)

Когда эндпоинты выше появятся, на фронте нужно будет:
1. Заменить импорты `useFoamStore`/`addRawLot`/`startProductionRun`/`cutCubeToSheets`/`createFoamSale`/`addDensityGrade` (`src/features/foam/store.js`) на реальные вызовы `apiClient` (`GET/POST foam/...`), по аналогии с остальными фичами (`src/features/*/api/*.js`).
2. Заменить синтетический строковый `row.key` (`'cube-14-15'` и т.п.) на числовой `id` строки остатка с бэка — везде, где сейчас сравнение идёт по `key` (`SalesFoamTab`, `WarehouseFoamTab`).
3. `output_qty` в форме запуска производства (`ProductionFoamTab`) оставить как **предпросмотр** (мгновенный расчёт в UI до отправки), но после ответа `POST foam/production-runs/` показывать/использовать именно значение с бэка, а не локально посчитанное.
4. `mockData.js` можно оставить только ради констант/утилит форматирования (`FOAM_CUBE_DIMS_CM`, `foamOutputFormatLabel`, `foamUnitWeightKg` и т.п.) — они остаются чисто фронтовыми хелперами отображения, серверу не нужны.

---

## 8. Чеклист приёмки

- [ ] `POST foam/raw-lots/` — лот появляется в `GET foam/raw-lots/`, `remaining_kg = bag_weight_kg`
- [ ] `POST foam/density-grades/` с существующим `code` — `409`
- [ ] `POST foam/production-runs/` (формат `cube`) — `output_qty` посчитан по формуле §1, `remaining_kg` лота уменьшился, в `foam/gp-stock/` появилась/выросла строка куба, в `foam/gp-operations/` — запись `production_intake`
- [ ] `POST foam/production-runs/` (формат `granule`) — без `grade_code`, `output_qty = round(usable_kg, 1)`
- [ ] `POST foam/gp-stock/cut/` на куб — строка куба уменьшилась, строка листа выросла/создалась, 2 записи в `gp-operations/`
- [ ] `POST foam/sales/` с недостаточным остатком по строке — `400`, склад не тронут
- [ ] `POST foam/sales/` успешно — `total_amount`/`debt_amount`/`payment_status` посчитаны на бэке, остаток списан, запись `sale` в `gp-operations/`
