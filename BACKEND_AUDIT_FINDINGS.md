# Аудит бэкенда vs BACKEND_REQUIREMENTS.md

Дата: 2026-08-10. Сверка сделана постранично (10 параллельных аудитов + ручная проверка роутинга и §21) без изменения бизнес-логики — единственная внесённая правка: добавлен ключ `meta.pages` в пагинацию (см. «Уже исправлено» ниже), т.к. это чисто аддитивно и безопасно.

**Итог по п.6 задания (сверка сводной таблицы §21):** ни один эндпоинт из таблицы не отсутствует физически в роутинге — все замаплены в `config/api_urls.py` / `config/urls.py`. НО два из них **всегда возвращают 410 Gone** намеренно отключённым кодом (см. «КРИТИЧНО» ниже) — по факту недоступны, хотя путь существует.

---

## Уже исправлено

- `config/pagination.py` — добавлен ключ `meta.pages` (алиас к уже существующим `totalPages`/`total_pages`), т.к. док во всех примерах использует именно `meta.pages`. Аддитивно, ничего не ломает.

---

## КРИТИЧНО — требует решения, не стал чинить молча

### 1. `POST /api/orders/` всегда отвечает `410 Gone`
`apps/sales/views.py` (`OrderViewSet.create`, ~строка 900) безусловно возвращает `{"detail": "Устарело: модуль заявок снят с фронта."}, 410`. Вся логика создания заявки (`OrderSerializer.create()`, `_normalize_cart_order_lines`) при этом полностью реализована и рабочая — просто недостижима из ViewSet.

**Вопрос:** это осознанный отказ от создания заявок через API (фронт создаёт их как-то иначе), или регресс? Документ (снятый с реального фронта) говорит, что фронт этот эндпоинт вызывает.

### 2. `POST /api/production/requests/{id}/start/` всегда отвечает `410 Gone`
`apps/production/views.py` (~строка 1872) — та же картина: безусловный `return` с 410 перед всей логикой `line_starts`/`order_line_id` → `{"runs":[...]}`. Предлагаемая по коду замена — `POST /api/workshop/blank-production-runs/`, но у неё **другой контракт** (`blank_id`, `blank_total_kg`, `blank_used_in_production_kg` вместо `line_starts`/`order_line_id`) — не полная замена по факту.

**Вопрос:** то же самое — фронт полностью перешёл на `workshop/blank-production-runs/`, и документ устарел, либо это незавершённая миграция.

### 3. `PATCH /api/orders/{id}/status/` меняет не то поле
В доке `request_status` — единый enum (`draft/not_ready/ready/in_production/closed/approved/checking/rejected`), которым управляет `PATCH .../status/`. В коде это **два разных поля**:
- `request_status` (production-жизненный цикл, без `closed`) — models.py:63-77
- `status` (shipping-жизненный цикл: `new/confirmed/in_progress/partially_shipped/shipped/closed/canceled`) — models.py:108-122

`PATCH /orders/{id}/status/` реально дёргает `status` (shipping), не `request_status`. Пример из дока (`{"status":"in_production"}`) в реальности упадёт — `in_production` в `ORDER_TRANSITIONS` для `status` не существует.

**Вопрос:** нужно ли на фронте править вызов, или бэку нужно поддержать оба поля/алиас.

---

## ВЫСОКИЙ приоритет — ломает контракт, не просто косметика

| # | Место | Доку говорит | Код делает |
|---|---|---|---|
| 4 | `POST /api/chemistry/elements/produce/` | тело `{element_id, quantity, components:[{raw_material_id, quantity_kg}]}` | сериализатор принимает только `{chemistry_id, quantity, comment}` — `element_id`/`components` не распознаются вообще; состав берётся из фиксированного `ChemistryRecipe`, не из тела запроса (apps/chemistry/serializers.py:185-195, produce.py:51-53) |
| 5 | Ответ того же endpoint | `{id, element_id, quantity_remaining, cost_per_kg}` | `{id, chemistry_id, quantity_remaining, cost_per_unit}` (serializers.py:216-245) |
| 6 | `GET /chemistry/balances/`, `GET /chemistry/batches/` | ключи `element_id`/`element_name` | ключи `chemistry_id`/`chemistry_name` |
| 7 | `GET /api/materials/movements/` | фильтры `material_id`, `date_from`, `date_to`, `movement_type` | ни один фильтр не подключён (`MaterialsMovementsView` без `filterset_class`) — параметры молча игнорируются |
| 8 | `GET /api/incoming/` фильтр по дате | `date_from`/`date_to` | реальные параметры `received_at_after`/`received_at_before` (django-filter `DateFromToRangeFilter` дефолт) |
| 9 | `GET /api/incoming/` тело | включает `quantity_initial` | `quantity_initial` удаляется из ответа (`serializers.py:146`) |
| 10 | `GET /api/clients/{id}/history/` | `{"items":[{id,type,date,total_amount}]}` | совсем другой агрегат: `{client_id, client_name, orders, sales, payments, returns, total_revenue, ...}`, без `items` вообще |
| 11 | `GET /api/client-financial-summary/`, `GET /api/payments/summary/` | `{total_debt, total_paid, total_sales_amount, total_orders}` | `{client_debt_money, total_paid_net, total_paid_gross, total_revenue, ...}` — нет пересечения по именам полей |
| 12 | `GET /api/payments/select-sources/` | `{"sales":[{id,debt_amount,total_amount,sale_lines}]}` | другие поля (`label`,`client`,`payment_status`), плюс лишние `clients/orders/returns` |
| 13 | `GET /api/warehouse/gp-packages/` (GET), `GET /api/warehouse/gp-unpacked-balance/` | должны отдавать `410 Gone` | оба живые, отдают 200 с реальными данными (только `POST gp-packages/` реально 410) |
| 14 | `POST /api/lines/{id}/open/` | `{line_id, shift_id, opened_at}` | `{detail, line: {...LineSerializer}}` |
| 15 | `POST /api/lines/{id}/close/` | `{line_id, closed_at}` | `{detail, line}` |
| 16 | `POST /api/lines/{id}/shift-pause/`, `/shift-resume/` | `{line_id, is_paused, pause_reason}` / `{line_id, is_paused}` | `{detail, line}` (поля внутри вложенного объекта) |
| 17 | `GET /api/recipes/{id}/availability/` | `{"items":[{material_id,required,available,unit,sufficient}]}` | `{"mode","total_meters","all_sufficient","components":[{...}]}` — другой верхнеуровневый ключ и имена полей |
| 18 | `GET /api/recipes/` (список) | элементы содержат `components[]` | список отдаёт только `components_count`, нужен отдельный GET по id |
| 19 | `GET /api/batches/` (список) | содержит `comment`, `can_edit` | оба поля отсутствуют в `BatchListSerializer` |

---

## СРЕДНИЙ приоритет — расхождения имён/enum, коды ошибок

- **User/Me сериализаторы** не отдают/не принимают `username`, `role_name`, `is_active` в тех местах, где док их требует (`UserSerializer` — только `id,name,role,password,accesses`; `MeSerializer` — без `username`/`is_active`). У модели `User` вообще нет поля `username` — `name` используется и как логин, и как отображаемое имя.
- `DELETE /api/users/{id}/`, `DELETE /api/roles/{id}/` — док требует `409` при наличии зависимых записей; в коде FK `on_delete=SET_NULL`, удаление всегда проходит (`204`), проверки нет.
- Access-enum (`§2.1`) — в коде (`config/settings.py: ACCESS_KEYS`) есть лишние ключи `lines`, `recipes`, `returns`, `defects`, которых нет в доке. `LineViewSet.required_access_key = 'lines'`, а док требует `access=production` для `/api/lines/*`.
- `Shift.status` — в модели есть третье значение `paused`, которого нет в `§2.16` enum (`open/opened/active/closed`).
- `GET /api/shifts/`, `GET /api/shifts/{id}/` — требуют только `my_shift`, но **не** скоуплены на `request.user`: любой обладатель `my_shift` может читать чужие смены/заметки. Возможно, это осознанно (для админ-функций тоже используется `my_shift`?), но не соответствует «мои данные» духу `my_shift`, и стоит проверить с точки зрения безопасности.
- Коды ошибок в коде — `UPPER_SNAKE` (`INACTIVE_CLIENT`, `MISSING_SALE_LINES`), в доке — `lower_snake` (`inactive_client`). Доп. неожиданность: часть ошибок (`invalid_status_transition`, `payment_status_update_forbidden`-подобные) реально возвращаются с HTTP `422`, которого нет в таблице кодов §1.9 (там 400/401/403/404/409/410/429/500).
- `apps/workshop/serializers.py` (WorkshopBlank): `chemistry_id` не сериализуется вообще (модель поле `chemistry` есть, в API его нет); `recipe_kg_per_barrel`, присланный клиентом, молча перезаписывается пересчитанной суммой `composition[].quantity_kg`.
- `GET /api/workshop/blank-production-runs/` не отдаёт `source_type`, `otk_fully_accounted`, `remaining_kg_in_pool` — фронт не может понять «доучтён» ли прогон без похода в `workshop/otk-blanks/`.
- `POST /api/batches/{id}/otk_accept/` — код требует **точного равенства** `accepted + rejected == pieces`, доку читается как «не больше pieces» (мягче).
- `POST /api/warehouse/batches/package/` — ошибка нехватки остатка возвращает `409`, доку — `400`; успешный ответ обёрнут в `{"items":[...]}`, а не единичный объект.
- `GET /api/warehouse/operations/` игнорирует параметр `ordering` (жёстко сортирует по `at` убыв.).
- `GET /api/warehouse/gp-stock/` — нет `available_pieces` (есть только `pieces` + доп. `kg`/`unit_sale_price`), и весь список отдаётся без пагинации (`page`/`page_size` игнорируются).
- `GET /api/warehouse/batches/` — нет `product_id`/`batch` (код-строка партии); id профиля спрятан в `linked_entities.profile.id`.
- `GET /api/raw-materials/` — нет полей `balance`/`deletable` в списке/карточке (они есть только в `/materials/balances/`); у химии (`ChemistryCatalogListSerializer`) эти поля есть — асимметрия.
- `GET /api/shifts/{id}/` — нет поля `line_label` (только `line_name`).
- `POST /api/shifts/open/` (личная смена, без `line_id`) — присланный `comment` нигде не сохраняется.

---

## НИЗКИЙ приоритет / косметика

- `GET /api/lines/{id}/history/session/` отдаёт лишний недокументированный ключ `pause_resume[]` (аддитивно, не ломает).
- Enum `Activity.action` — значения `view`/`restore` не реализованы в коде (`ACTION_CHOICES` только create/update/delete), хотя заявлены в §2.17.
- `RoleSerializer.validate_name` сравнивает `'Администратор'` регистрозависимо; сама защита от удаления/правки работает через отдельный флаг `is_system`, так что по факту защищена — но текстовая валидация имени при переименовании других ролей не поймает `АДМИНИСТРАТОР`.
- Login-ответ дополнительно содержит `accesses` в объекте `user` — не в доке, но безвредно.

---

## Лишние (недокументированные) эндпоинты в коде — не факт что проблема, но не описаны в доке

`GET/POST /api/returns/`, `/api/defects/`, `/api/rework-requests/`, `/api/price-lists/`, `/api/client-prices/`, `/api/order-reservations/`, `/api/chemistry/tasks/`, `/api/production/recipe-runs/`, плюс алиасы без `/api/` префикса в `config/urls.py` (`/users/{id}/`, `/warehouse/pack-from-otk/`, `/warehouse/pack/`, `/batches/pack_from_otk/`) — целиком отсутствуют в документе. Возможно, старее/новее функциональность, которую фронт ещё не описал или уже не использует.

---

## Открытые вопросы для согласования (сводно)

1. Осознанно ли отключены `POST /api/orders/` и `POST /api/production/requests/{id}/start/` (оба — 410) — или это баг/недоделанная миграция? Если осознанно — чем фронт их реально заменяет?
2. `PATCH /api/orders/{id}/status/` — должен управлять `request_status` (как в доке) или `status` (как сейчас в коде)? Нужен ли единый enum или два раздельных эндпоинта?
3. `POST /api/chemistry/elements/produce/` — должен ли принимать произвольный `components[]` в теле (как в доке), или фиксированный `ChemistryRecipe` на справочнике — осознанный дизайн, и доку нужно поправить?
4. Chemistry-эндпоинты — стандартизировать на `element_id`/`element_name` (как в доке) или оставить `chemistry_id`/`chemistry_name` (как в коде)?
5. `username` — нужен как отдельное поле логина (отдельно от `name`), или `name` и есть логин, и доку надо поправить? Нужно ли `is_active` быть редактируемым через `PATCH /api/users/{id}/`?
6. Удаление пользователя/роли с зависимыми записями — должно блокироваться `409` (как в доке), или каскад `SET_NULL` — осознанное поведение?
7. `GET /api/warehouse/gp-packages/` и `gp-unpacked-balance/` — реально снять (410, как требует доку), или ещё нужны легаси-потребителям?
8. `GET /api/shifts/`, `/api/shifts/{id}/` — сузить видимость до собственных смен пользователя при доступе только `my_shift`, или широкий доступ осознан?
9. `'lines'` — самостоятельный access-key (отдельно от `'production'`), или баг в `LineViewSet.required_access_key`?

---

## Побочное наблюдение (не входит в задачу, но заметно)

`git status` на момент аудита показывает набор старых контрактных доков как удалённые из рабочего дерева (не закоммичено): `BACKEND_API_CONTRACT_*.md`, `docs/API_CONTRACT_*.md`, `docs/WEBSOCKET_API.md` и др. Не трогал их — если это часть уборки перед переходом на единый `BACKEND_REQUIREMENTS.md`, всё ок; если удаление случайное, дайте знать — их легко восстановить (`git checkout -- <path>`), они ещё в HEAD.
