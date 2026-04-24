# Backend Flow: от «Склад» до финала

Источник: текущий backend-код (`config/api_urls.py`, `apps/warehouse/*`, `apps/sales/*`, `apps/analytics/*`, `apps/realtime/*`).
Ниже только разделы от «Склад» и дальше.

---

## 1) Склад

### 1. Назначение
- Учёт партий ГП на складе (`warehouse_batches`), включая форму учёта, качество, упаковку, резервы и трассировку цепочки до продажи/возврата/брака/переделки.

### 2. Сущности
- `WarehouseBatch`:
  - ключевые поля: `id`, `product`, `quantity`, `status`, `quality`, `inventory_form`, `source_batch`, `date`.
  - упаковка: `unit_meters`, `package_total_meters`, `pieces_per_package`, `packages_count`.
  - качество: `quality` (`good|defect`), `defect_reason`.
  - вычисляемое (model): `total_meters = quantity * length_per_piece`.
- `OrderReservation` (используется для `reserved_quantity`/`available_quantity` в сериализаторе склада).
- Вычисляемые поля API (`WarehouseBatchSerializer`):
  - `reserved_quantity` = сумма активных резервов по партии.
  - `available_quantity` = `quantity - reserved_quantity`.
  - `sealed_packages_count`, `open_package_pieces`, `sealed_pieces`, `packaging_quantity_consistent`.

Показывать фронту:
- бизнес-поля остатков и упаковки: `product`, `quantity`, `available_quantity`, `reserved_quantity`, `status`, `quality`, `inventory_form`, упаковочные поля.

Не показывать пользователю как «бизнес-колонки»:
- внутренние ссылки/технические поля: `source_batch`, OTK snapshot-поля (`otk_*`) при обычном списке (можно в «подробно»).

### 3. Endpoint
- `GET /api/warehouse/batches/`
  - access key: `warehouse`
  - query: фильтры `status`, `product`, `quality`, `inventory_form` (+ алиасы `stock_form`, `packaging_status`; `not_packed/opened` маппятся в канон).
  - response: список `WarehouseBatchSerializer`.
  - ошибки: `401/403`.

- `GET /api/warehouse/batches/{id}/`
  - access key: `warehouse`
  - response: карточка партии.
  - ошибки: `404`.

- `POST /api/warehouse/batches/reserve/`
  - access key: `warehouse`
  - body: `batch_id` (или legacy `batchId`), `quantity`, `sale_id?`.
  - правило: резерв только на **полный остаток строки** (`quantity` обязан быть равен `batch.quantity`).
  - response: обновлённая партия.
  - ошибки: `validation_error`, `not_found`, `bad_request`.

- `POST /api/warehouse/batches/package/`
  - access key: `warehouse`
  - body: `warehouse_batch_id` (или `batchId`), `packages_count`, `pieces_per_package`, `comment?`.
  - правила:
    - только `inventory_form=unpacked`;
    - только `status=available`;
    - нужна длина штуки (`unit_meters/length_per_piece`);
    - нельзя упаковать больше доступного количества.
  - response: `201`, `{ "items": [...] }` (созданные упакованные строки).
  - ошибки: `validation_error`, `not_found`, `bad_request`, `conflict`.

- `GET /api/warehouse/batches/{id}/trace/`
  - access key: `warehouse`
  - response: трассировка `production_batch -> otk_checks -> sale_lines -> return_lines -> defect_records -> rework_requests -> reservations`.

### 4. Статусы
- `WarehouseBatch.status`: `available -> reserved -> shipped` (обратного автомата как отдельного endpoint нет; изменения через бизнес-операции).
- `WarehouseBatch.quality`: `good|defect`.
- Ограничения:
  - резерв под клиентскую заявку запрещён для `quality=defect`.
  - продажа со склада разрешена только при `status=available`.

### 5. Что пользователь делает по шагам
- Открывает склад -> фильтрует партии.
- Для упаковки: выбирает строку `unpacked` -> вводит `packages_count` и `pieces_per_package` -> backend создаёт упакованные строки.
- Для резерва: выбирает строку и резервирует под заявку.
- Для расследования: открывает `trace` партии и видит сквозную цепочку.

### 6. Поля форм
- Резерв партии:
  - `batch_id` (select id партии, обяз.)
  - `quantity` (input decimal, обяз., ровно весь остаток строки)
  - `sale_id` (опц., служебный контекст).
- Упаковка:
  - `warehouse_batch_id` (select, обяз.)
  - `packages_count` (input int >=1, обяз.)
  - `pieces_per_package` (input int >=1, обяз.)
  - `comment` (textarea, опц.)

### 7. Автологика backend
- `total_meters` считается автоматически.
- При упаковке:
  - остаток исходной строки уменьшается/строка удаляется при нуле;
  - новые строки наследуют `quality` и OTK snapshot;
  - смешивание качества через API не допускается.
- `reserved_quantity`/`available_quantity` считаются только backend.

### 8. Что фронт должен скрыть
- `id`/FK как внутренние идентификаторы в обычной таблице.
- OTK snapshot-поля (`otk_*`) вне карточки «Подробнее».

---

## 2) Клиенты

### 1. Назначение
- Справочник клиентов + их финансовое состояние и история операций.

### 2. Сущности
- `Client`: контактные и финансовые поля (`credit_limit`, `credit_limit_mode`, `is_active`).
- Агрегаты:
  - история: `orders`, `sales`, `payments`, `returns`.
  - финансы: `client_debt_money`, `client_advance_amount`, `credit_available`, `is_over_limit`.

Показывать фронту:
- карточку клиента, финсводку, историю.

Скрывать:
- технические `created_by`/внутренние id связанных сущностей в списковых строках UI.

### 3. Endpoint
- `GET/POST /api/clients/`, `GET/PATCH/PUT/DELETE /api/clients/{id}/`
  - access key: `clients`
  - query list: `is_active`, поиск по `name/inn/contact/email/messenger`.
  - ошибки delete: `409 CLIENT_IN_USE` при связанных продажах.

- `GET /api/clients/{id}/history/`
  - access key: `clients`
  - response: агрегированная история + денежные итоги + кредитные показатели.

- `GET /api/client-financial-summary/?client_id=...`
  - access key: `clients`
  - response: полная финсводка.
  - ошибки: `MISSING_PARAM`, `NOT_FOUND`.

### 4. Статусы
- `Client.is_active` (в API дополнительно `status=active|inactive` как вычисляемое поле сериализатора).

### 5. Сценарий пользователя
- Открыть клиента -> посмотреть историю.
- Проверить долг/аванс и лимит.
- Обновить лимит/режим (`soft|hard`) для кредитной политики.

### 6. Поля форм
- `name` обяз.
- `contact/phone/phone_alt/inn/address/email/messenger` опц.
- `credit_limit` опц. decimal.
- `credit_limit_mode` select: `soft|hard`.
- `is_active` bool.

### 7. Автологика backend
- `sales_count`, `sales_total`, `has_sales`, `status` вычисляются backend.
- `history` и `financial-summary` считают долг/аванс/лимиты backend.

### 8. Что фронт должен скрыть
- внутренние служебные поля (`created_by`, raw id из вложенных сущностей) в обычном UX.

---

## 3) Заявки

### 1. Назначение
- Коммерческое намерение клиента (до/в процессе отгрузки), с построчным резервом и контролем закрытия.

### 2. Сущности
- `Order`, `OrderLine`, `OrderReservation`.
- Важные вычисляемые поля строки:
  - `remaining_quantity`, `available_to_ship`, `remaining_to_reserve`, `line_total`.
- Для заявки: `total_amount`, `shipped_amount`, `remaining_amount`, `paid_amount`, `has_company_debt_by_goods`.

### 3. Endpoint
- CRUD:
  - `GET/POST /api/orders/`
  - `GET/PATCH/PUT/DELETE /api/orders/{id}/`
  - access key: `client_orders`
  - query: `client_id`, `status`, `source_type`, `date_from`, `date_to`.

- `PATCH /api/orders/{id}/status/`
  - body: `status`
  - переходы валидируются state machine.
  - закрытие проверяет: нет активных резервов + все строки полностью отгружены.
  - ошибки: `MISSING_STATUS`, `INVALID_STATUS_TRANSITION`, `ORDER_CLOSE_BLOCKED`.

- `PATCH /api/orders/{id}/cancel/`
  - снимает все активные резервы и переводит в `canceled`.
  - ошибки: `INVALID_TRANSITION`.

- `POST /api/orders/{id}/reserve/`
  - body: `order_line_id`, `warehouse_batch_id`, `quantity`, `comment?`
  - ошибки: `MISSING_FIELD`, `NOT_FOUND`, `RESERVATION_ERROR`.

- `POST /api/orders/{id}/release-reserve/`
  - body: `reservation_id`
  - ошибки: `MISSING_FIELD`, `NOT_FOUND`, `RESERVATION_ERROR`.

- `GET /api/orders/{id}/reservations/`
  - список резервов заявки.

- `GET /api/orders/{id}/history/`
  - цепочка `order -> sales -> payments -> returns`.

- `GET /api/orders/{id}/nakladnaya/`
  - HTML документ заявки (`text/html`, inline).

- `GET /api/order-reservations/`, `GET /api/order-reservations/{id}/`
  - readonly список резервов.
  - access key: `client_orders`.

### 4. Статусы
- `Order`: `new -> confirmed -> in_progress -> partially_shipped -> shipped -> closed`.
- Дополнительно отмена: `new/confirmed/in_progress/partially_shipped -> canceled`.
- Backend запрещает:
  - недопустимые переходы;
  - закрытие при активных резервах;
  - закрытие при недоотгруженных строках;
  - отмену заявки с активными продажами (не `draft/canceled`).

### 5. Сценарий
- Создать заявку с линиями.
- Зарезервировать склад под строки.
- Создавать продажи по заявке.
- После полной отгрузки и снятия активных резервов закрыть заявку.

### 6. Поля форм
- Заявка: `date?`, `client?`, `source_type`, `comment`, `responsible_user?`, `lines[]`.
- Строка: `product`, `product_type`, `profile?`, `ordered_quantity`, `unit_price?`, `comment?`.
- Резерв: см. endpoint выше.

### 7. Автологика backend
- `order_number` автогенерируется: `ORD-{year}-{####}`.
- `created_by` подставляется из текущего пользователя.
- суммы/остатки по строкам и заявке считает backend.

### 8. Что фронт должен скрыть
- внутренние поля резерва (`sale_line`, служебные id) вне режима отладки.

---

## 4) Продажи

### 1. Назначение
- Фиксация отгрузки/продажи, выручки/себестоимости/прибыли, статуса сделки и печатных документов.

### 2. Сущности
- `Sale`, `SaleLine` (+ связь с `Order`, `WarehouseBatch`, `Client`).
- Ключевые поля:
  - `sale_status`, `linked_order`, `warehouse_batch`, `quantity`, `price`, `revenue`, `cost`, `profit`.
  - режим: `sale_mode` (`pieces|packages`), `quantity_input`.
  - складовой снимок: `stock_form`, `piece_pick`, `stock_quality`.
- sale lines для документооборота и аналитики.

### 3. Endpoint
- CRUD:
  - `GET/POST /api/sales/`
  - `GET/PATCH/PUT/DELETE /api/sales/{id}/`
  - access key: `sales`
  - query: `client_id`, `sale_status`, `is_defect_sale`, `linked_order`, `date_from`, `date_to`.

- `PATCH /api/sales/{id}/status/`
  - body: `status`, `force_credit_override?`
  - при `shipped|closed`: проверка склада + hard credit limit.
  - override разрешён только пользователю с `credit_limit_override` (или staff), если передан `force_credit_override=true`.
  - ошибки: `MISSING_STATUS`, `INVALID_STATUS_TRANSITION`, `SHIP_BLOCKED`, `CREDIT_LIMIT_BLOCKED`.

- `GET /api/sales/{id}/credit-check/`
  - проверка лимита перед оплатой/отгрузкой.

- `GET /api/sales/{id}/nakladnaya/` (`/waybill/`, `/invoice/` — тот же HTML)
- `GET /api/sales/{id}/receipt/`
  - HTML документы.

### 4. Статусы
- `Sale`: `draft -> confirmed -> partially_shipped -> shipped -> closed`.
- Отмена: `draft/confirmed/partially_shipped -> canceled`.
- Backend запрещает:
  - невалидные переходы;
  - отгрузку при нехватке/недоступности склада;
  - hard credit limit без override.

### 5. Сценарий
- Создать продажу (вручную или из контекста заявки).
- При привязке к складу backend списывает остаток.
- При привязке к заявке + партии backend автоисполняет резервы.
- Перевести статус до `shipped/closed` после проверок.
- Печатать `waybill/receipt`.

### 6. Поля форм
- Основные: `client?`, `linked_order?`, `warehouse_batch?`, `product`, `quantity`, `price`, `date?`, `comment?`.
- Режим: `sale_mode`, `sale_unit`, `quantity_input?`.
- Для упакованной партии обязателен корректный `piece_pick`.
- Для `force_credit_override`: bool в запросе смены статуса.

### 7. Автологика backend
- `order_number` автогенерируется (`ORD-{year}-{###}`) при отсутствии.
- `revenue/cost/profit/total_meters` считаются backend.
- `stock_quality` берётся из партии склада автоматически.
- при создании с `warehouse_batch` происходит списание со склада.
- при создании с `linked_order+warehouse_batch` автоисполняются резервы и обновляется `OrderLine.shipped_quantity`.
- обновление существующей продажи блокирует смену `warehouse_batch` и изменение `quantity` для уже складской продажи.

### 8. Что фронт должен скрыть
- технические поля совместимости (`packaging`, часть legacy-полей) в обычной форме, если не используются сценарием.

---

## 5) Оплаты

### 1. Назначение
- Учёт денежных движений отдельно от товарного движения.

### 2. Сущности
- `Payment`:
  - `payment_type`: `prepayment|payment|surcharge|refund`
  - `payment_method`: `cash|transfer|card|other`
  - связи: `client`, `linked_order`, `linked_sale`.

### 3. Endpoint
- CRUD:
  - `GET/POST /api/payments/`
  - `GET/PATCH/PUT/DELETE /api/payments/{id}/`
  - access key: `payments`
  - query: `client_id`, `payment_type`, `payment_method`, `date_from`, `date_to`, `linked_order`, `linked_sale`.

- `GET /api/payments/summary/?client_id=...`
  - сводка по клиенту (`total_paid_gross`, `total_refunded`, `total_paid_net`, `client_debt_money`, `client_advance_amount`).
  - ошибки: `MISSING_CLIENT`, `NOT_FOUND`.

### 4. Статусы
- Отдельного статусного автомата нет; бизнес-смысл задаётся `payment_type`.

### 5. Сценарий
- Открыть оплаты клиента.
- Внести предоплату/оплату/доплату/возврат денег.
- Смотреть summary по клиенту.

### 6. Поля форм
- `date?`, `client?`, `linked_order?`, `linked_sale?`,
- `payment_type` (select),
- `amount` (обяз., >=0),
- `payment_method` (select),
- `comment?`.

### 7. Автологика backend
- `payment_number` автогенерируется: `PAY-{year}-{####}`.
- `created_by` проставляется автоматически.

### 8. Что фронт должен скрыть
- служебные `created_by`, внутренние id привязок вне карточки/дебага.

---

## 6) Возвраты

### 1. Назначение
- Возврат от клиента, связанный с продажей, с маршрутизацией строк: на склад, в брак, на переделку.

### 2. Сущности
- `Return`, `ReturnLine`.
- `ReturnLine.return_target`: `warehouse|defect|rework`.
- `ReturnLine.condition_type`: `good|damaged|defect`.

### 3. Endpoint
- CRUD:
  - `GET/POST /api/returns/`
  - `GET/PATCH/PUT/DELETE /api/returns/{id}/`
  - access key: `returns`
  - query: `sale_id`, `client_id`, `date_from`, `date_to`.

- `GET /api/returns/{id}/nakladnaya/`
  - HTML акт возврата.

### 4. Статусы
- У `Return` нет автомата статусов; статусность в целевом маршруте строки (`return_target`).

### 5. Сценарий
- Создать возврат к продаже с линиями.
- Для каждой линии выбрать `return_target`.
- Backend:
  - `warehouse`: увеличит остаток партии продажи;
  - `defect`: создаст `DefectRecord` (`on_stock`);
  - `rework`: создаст `DefectRecord` (`sent_to_rework`) + `ReworkRequest` (`pending`).

### 6. Поля форм
- Документ: `sale` (обяз.), `date?`, `linked_order?`, `invoice_number?`, `return_reason?`, `comment?`.
- Строка: `sale_line?`, `product?`, `quantity`, `return_target`, `condition_type`, `comment?`.

### 7. Автологика backend
- `return_number` автогенерируется: `RET-{year}-{####}`.
- Валидация количества: нельзя вернуть больше, чем отгружено по `sale_line` с учётом уже оформленных возвратов.
- Автосоздание брака/переделки по `return_target`.

### 8. Что фронт должен скрыть
- внутренние поля `source_id` из созданных downstream-сущностей в общем списке возвратов.

---

## 7) Брак / переделка

### 1. Назначение
- Управление жизненным циклом брака и переработки.

### 2. Сущности
- `DefectRecord`, `ReworkRequest`.
- Важные поля:
  - брак: `source_type`, `source_id`, `quantity_pcs`, `quantity_kg`, `status`, `writeoff_reason`.
  - переделка: `status`, `quantity_kg`, `output_quantity_kg`, `loss_kg`, `conversion_rate`, `result_warehouse_batch`.

### 3. Endpoint
- Брак CRUD:
  - `GET/POST /api/defects/`
  - `GET/PATCH/PUT/DELETE /api/defects/{id}/`
  - access key: `defects`
  - query: `source_type`, `status`, `profile_id`.

- Действия брака:
  - `POST /api/defects/{id}/send-to-rework/`
  - `POST /api/defects/{id}/complete-rework/`
  - `POST /api/defects/{id}/writeoff/` (body: `writeoff_reason` обяз.)
  - `POST /api/defects/{id}/sell/` (body: `client_id?`, `price?`, `quantity?`, `comment?`, `date?`)

- Переделка CRUD:
  - `GET/POST /api/rework-requests/`
  - `GET/PATCH/PUT/DELETE /api/rework-requests/{id}/`
  - access key: `defects`
  - filter: `status`, `original_sale`.

- Действия переделки:
  - `POST /api/rework-requests/{id}/start/`
  - `POST /api/rework-requests/{id}/complete/` (обяз. `result_warehouse_batch_id`, опц. `output_quantity_kg`, `loss_kg`)
  - `POST /api/rework-requests/{id}/cancel/`

### 4. Статусы
- Брак:
  - `new -> on_stock|sent_to_rework|written_off`
  - `on_stock -> sent_to_rework|sold|written_off`
  - `sent_to_rework -> reworked|on_stock`
  - `reworked -> sold|written_off`
  - `sold`, `written_off` терминальные.
- Переделка:
  - `pending -> in_progress|canceled`
  - `in_progress -> completed|canceled`
  - `completed`, `canceled` терминальные.
- Backend запрещает любые переходы вне state machine.

### 5. Сценарий
- Из возврата создаётся брак/переделка автоматически (в зависимости от target).
- По браку можно:
  - списать (`writeoff` с обязательной причиной),
  - передать в переделку,
  - продать брак (`sell`) из допустимых статусов.
- По переделке:
  - `start` -> `complete` с результирующей партией склада.

### 6. Поля форм
- Брак: `product`, `quantity_pcs`, `quantity_kg?`, `defect_reason?`, `status`.
- Списание: `writeoff_reason` обяз.
- Продажа брака: `price`, `quantity?`, `client_id?`, `date?`, `comment?`.
- Переделка:
  - создание: `return_doc`, `defect_record?`, `original_sale?`, `product`, `quantity_kg`.
  - завершение: `result_warehouse_batch_id` обяз., `output_quantity_kg?`, `loss_kg?`.

### 7. Автологика backend
- `sell defect` создаёт `Sale` с `is_defect_sale=True`, `sale_status=shipped`, авто-номером продажи.
- `complete rework`:
  - считает `loss_kg` (если не передан и есть вход/выход),
  - считает `conversion_rate`,
  - обновляет статус связанного `DefectRecord` на `reworked`.
- `rework_number` автогенерируется: `RWK-{year}-{####}`.

### 8. Что фронт должен скрыть
- внутренние `source_id`, служебные связи, если нет экрана трассировки.

---

## 8) Аналитика

### 1. Назначение
- KPI и детализация по продажам/себестоимости/прибыли/браку/переделке/дебиторке.

### 2. Сущности
- Используются агрегаты из `Sale`, `Payment`, `DefectRecord`, `ReworkRequest`, `MaterialStockDeduction` и др.
- Показатели считаются backend (через `apps/analytics/reporting.py` + view-агрегации).

### 3. Endpoint (все access key: `analytics`)
- `GET /api/analytics/summary/`
- `GET /api/analytics/revenue-details/`
- `GET /api/analytics/sales-cost-details/`
- `GET /api/analytics/production-cost-details/`
- `GET /api/analytics/purchase-details/`
- `GET /api/analytics/profit-details/`
- `GET /api/analytics/otk-details/`
- `GET /api/analytics/writeoff-details/` (требует `year`)
- `GET /api/analytics/defect-analytics/`
- `GET /api/analytics/rework-analytics/`
- `GET /api/analytics/client-profitability/`
- `GET /api/analytics/receivables/`

Общие query params (где применимо):
- `year`, `month`, `day`, `date_from`, `date_to`, `line_id`, `client_id`, `profile_id`, `recipe_id`, `batch_id`, `otk_status`, `trend_group`.

### 4. Статусы
- Отдельного статусного автомата нет; аналитика использует статусы предметных сущностей.

### 5. Сценарий
- Выбрать период/фильтры -> получить сводку -> провалиться в детализацию нужного KPI.

### 6. Поля форм
- Период (`year/month/day` или `date_from/date_to`) + доменные фильтры.

### 7. Автологика backend
- Все метрики и тренды считает backend.
- Фронт не должен вычислять прибыль/долг/дефектные агрегаты самостоятельно.

### 8. Что фронт должен скрыть
- внутренние технические идентификаторы строк детализации, если они не нужны для drill-down UX.

---

## 9) Документы / печать / preview / receipt / waybill

### Что есть в коде сейчас (факт)
- Только HTML-документы (`content-type: text/html; charset=utf-8`, `Content-Disposition: inline`).
- Отдельных endpoint для `pdf` или `preview` **нет**.

Endpoint:
- Заявка: `GET /api/orders/{id}/nakladnaya/`
- Продажа: `GET /api/sales/{id}/nakladnaya/`, `GET /api/sales/{id}/waybill/`, `GET /api/sales/{id}/invoice/`
- Квитанция продажи: `GET /api/sales/{id}/receipt/`
- Возврат: `GET /api/returns/{id}/nakladnaya/`

Логика preview-first:
- backend отдаёт сразу inline HTML; отдельного режима «preview API -> pdf API» нет.

---

## 10) WebSocket / realtime для этих разделов

### Подключение
- WS endpoint: `ws/operational` (ASGI route `^ws/operational/?$`).
- Требуется авторизованный пользователь (иначе close `4001`).

### Формат событий
- Базовый payload: `protocol_version`, `event=change`, `resource`, `action`, `ts`, `id?`, `payload?`.
- Подход: лёгкий event, после него фронт делает REST refetch.

### Ресурсы по нужным вкладкам
- `warehouse_batch` -> склад.
- `order` -> заявки.
- `sale` -> продажи.
- `payment` -> оплаты.
- `return` -> возвраты.
- `defect_record` -> брак.
- `rework_request` -> переделка.

### Что фронт должен refetch
- При `warehouse_batch`: список склада + карточка партии (если открыта).
- При `order`: список заявок + карточка/резервы конкретной заявки.
- При `sale`: список продаж + карточка/документы продажи.
- При `payment`: список оплат + summary/финсводки клиента.
- При `return`: список возвратов + связанные карточки.
- При `defect_record`, `rework_request`: соответствующие списки и карточки.

---

## Финал

### 1. Что фронт обязан учитывать
- Все статусные переходы проверяются backend; UI должен предлагать только допустимые действия.
- Денежные и складские расчёты (`revenue/cost/profit`, `reserved/available`, debt/advance/credit) — источник истины backend.
- Для продаж из `packed` партий обязателен корректный `piece_pick`.
- Документы сейчас только HTML inline.

### 2. Что фронту запрещено делать
- Самостоятельно считать и «подменять» backend-значения остатков, резервов, прибыли, долгов и кредитного статуса.
- Пытаться закрывать заявку с активными резервами или недоотгруженными строками.
- Резервировать брак под клиентские заявки.
- Рассчитывать, что есть `pdf/preview` endpoint (их нет в текущем коде).

### 3. Спорные / неидеальные места, которые реально есть
- Нумерация документов/сделок генерируется в разных местах (форматы похожи, но логика разнесена по сериализаторам/действиям).
- Есть исторические/legacy алиасы полей (`batchId`, `quantity_unit`, `not_packed/opened`) — это повышает риск неоднозначности на фронте.
- Документы только HTML; если нужен PDF, это отдельная backend-реализация (сейчас отсутствует).

