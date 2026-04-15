# Отчёт: складской брак и продажи (реализация)

## Что изменено

### Модели

- **`apps/warehouse/models.py` — `WarehouseBatch`**
  - `quality`: `good` | `defect`, индекс, по умолчанию `good` (единственный признак качества в модели).
  - `defect_reason`: текст; для `quality=good` при сохранении очищается, если был заполнен (защита от смешения).
  - Константы: `QUALITY_GOOD`, `QUALITY_DEFECT`.
  - Миграция **`0007`**: удалено поле `is_defect`.

- **`apps/sales/models.py` — `Sale`**
  - `stock_quality`: строка, снимок `warehouse_batch.quality` на момент продажи (не меняется при смене цены без смены партии вручную в том же PATCH — см. логику сериализатора).

- **Миграции**
  - `apps/warehouse/migrations/0006_warehousebatch_quality_defect.py`
  - `apps/warehouse/migrations/0007_remove_warehousebatch_is_defect.py`
  - `apps/sales/migrations/0012_sale_stock_quality.py`

### Сервисы / модули

- **`apps/warehouse/receipt.py`** (новый)
  - `create_warehouse_batches_from_otk(batch, accepted, rejected, defect_reason, comment, inspector_name, checked_at, otk_status_snapshot)` — создаёт одну или две строки склада; годный и брак **всегда разные объекты**.

- **`apps/warehouse/stock_ops.py`**
  - `loose_quantity_for_packaging(pb, *, quality)`: для `quality=good` cap = `OtkCheck.accepted`, для `defect` cap = `OtkCheck.rejected`; unpacked и остаток по складу только этого `quality`.
  - `_duplicate_warehouse_batch`: копирует `profile_id`, `length_per_piece`, `cost_per_piece`, `cost_per_meter`, `quality`, `defect_reason` и прежние поля ОТК.
  - `deduct_unpacked_quantity(pb, qty, *, quality)`: FIFO unpacked только для переданного `quality`.

- **`apps/production/views.py`**
  - `otk_accept`: вместо одного `WarehouseBatch.objects.create` при `accepted > 0` — вызов **`create_warehouse_batches_from_otk`** (создание good и/или defect).

### Views

- **`apps/warehouse/views.py` — `package`**
  - Обязательно: **`warehouse_batch_id`** (алиас `batchId`), **`pieces_per_package`**, **`packages_count`**; опционально **`comment`**.
  - Упаковка **только одной** исходной строки; `quality` и прочие параметры берутся с неё, без выбора качества в API.

### Serializers

- **`apps/warehouse/serializers.py` — `WarehouseBatchSerializer`**
  - В ответ API: `quality`, `defect_reason`.

- **`apps/sales/serializers.py` — `SaleSerializer`**
  - Поле `stock_quality` (read-only в API): при создании и при первичной привязке `warehouse_batch` в `update` заполняется из **`warehouse_batch.quality`**.

### Filters / admin

- **`apps/warehouse/filters.py`**: фильтр **`quality`**.
- **`apps/warehouse/admin.py`**: в списке и фильтрах `quality`; `quality`, `defect_reason` — **readonly** в админке.

---

## Как теперь работает ОТК

После успешной валидации `POST /api/batches/{id}/otk_accept/`:

1. **`accepted > 0`** — создаётся **`WarehouseBatch`** с `quality=good`, `quantity=accepted`, та же себестоимость и геометрия, что у **`ProductionBatch`** (`cost_per_piece`, `cost_per_meter`, `length_per_piece`, `profile_id`, `unit_meters`), снимок ОТК в полях `otk_*`.

2. **`rejected > 0`** — создаётся **отдельная** строка с `quality=defect`, `quantity=rejected`, те же `cost_per_piece` / `cost_per_meter` / `length_per_piece` / `profile` / `source_batch`, `defect_reason` = текст причины из ОТК, те же `otk_*` снимки.

3. **Оба > 0** — создаются **две** строки.

4. **`accepted = 0`, `rejected > 0`** — создаётся **только** defect-строка.

Факт производства и расчёт себестоимости по-прежнему только в **`ProductionBatch`**; ОТК только делит количество на складские строки.

---

## Как теперь работает склад

- Разделение: **одна строка = одно качество** (`good` или `defect`).
- Резерв: **`POST /api/warehouse/batches/reserve/`** — без изменений по смыслу; работает для любой строки в статусе `available`, в том числе defect.
- Упаковка: одна строка по **`warehouse_batch_id`** (good или defect — как у исходной строки).
- Дубли при упаковке/сплите (`_duplicate_warehouse_batch`, частичное вскрытие в `apply_sale_to_warehouse_batch`) сохраняют **`quality`** и **`defect_reason`**.

---

## Как теперь работает продажа брака

- Выбор партии: тот же **`warehouse_batch_id`**, что и для годного; в queryset для выбора доступны все партии со статусом **`available`** (ограничения по `quality` нет).
- Списание: **`apply_sale_to_warehouse_batch`** — без проверки на `quality`; логика по `inventory_form` / `piece_pick` не менялась.
- **`revenue` / `cost` / `profit`**: как раньше, `cost = sold_pieces * cost_per_piece` партии; для defect **`cost_per_piece`** совпадает с производственной партией.
- В **`Sale`** сохраняется **`stock_quality`** для фиксации, что продана именно defect/good строка.

---

## Что проверить вручную

1. ОТК: только годный (`rejected=0`) — одна строка склада, `quality=good`.
2. ОТК: только брак (`accepted=0`) — одна строка, `quality=defect`, `quantity=rejected`.
3. ОТК: смешанный результат — две строки с одинаковым `source_batch`, разные `quality` и `quantity`.
4. Продажа defect-партии: корректное списание остатка, `profit = revenue - cost`, в ответе продажи есть `stock_quality=defect`.
5. Резерв defect-строки на полный остаток — статус `reserved`.
6. Упаковка defect: **`warehouse_batch_id`** строки с `quality=defect` — новая packed-строка с тем же `quality` и `defect_reason`.
7. Упаковка good: **`warehouse_batch_id`** строки с `quality=good`.

---

## Примечание по миграциям

Миграции добавлены в репозиторий; применение: `python manage.py migrate` в окружении проекта.
