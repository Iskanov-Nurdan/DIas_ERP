# Backend API Contract Review — Клиенты

Документ составлен строго по фактическому backend-коду.
Код не менялся. Фиксы не вносились.

---

## 1. ОБЩАЯ ИНФОРМАЦИЯ

### 1.1 Назначение вкладки
Вкладка "Клиенты" управляет карточками клиентов, их активностью (`is_active`), кредитными параметрами (`credit_limit`, `credit_limit_mode`) и предоставляет агрегированную историю и финансовую сводку по связанным документам.

### 1.2 Backend-модели
- `Client` (`apps/sales/models.py`)
- Связанные:
  - `Order` (FK `client`, related_name `orders`)
  - `Sale` (FK `client`, related_name `sales`)
  - `Payment` (FK `client`, related_name `payments`)
  - `Return` (не имеет прямого FK на `Client`, связь через `Return.sale.client`)

### 1.3 Serializers
- `ClientSerializer`
- В `history` используются:
  - `OrderSerializer`
  - `SaleSerializer`
  - `PaymentSerializer`
  - `ReturnSerializer`

### 1.4 Viewsets / actions
- `ClientViewSet`:
  - list/create/retrieve/update/partial_update/destroy
  - action `GET /api/clients/{id}/history/`
- `ClientFinancialSummaryView`:
  - list endpoint `GET /api/client-financial-summary/?client_id=...`

### 1.5 Permissions / access
- `permission_classes = [IsAdminOrHasAccess]`
- `required_access_key = "clients"`
- Требование доступа действует на:
  - `ClientViewSet`
  - `ClientFinancialSummaryView`

### 1.6 Связанные сущности
- Заявки: `Order.objects.filter(client=client)`
- Продажи: `Sale.objects.filter(client=client)`
- Оплаты: `Payment.objects.filter(client=client, status=active)` (в history)
- Возвраты: `Return.objects.filter(sale__client=client)`
- История: endpoint `GET /api/clients/{id}/history/`
- Финсводка: endpoint `GET /api/client-financial-summary/?client_id=...`

---

## 2. ВСЕ ENDPOINTS ВКЛАДКИ "КЛИЕНТЫ"

## 2.1 GET /api/clients/

1) Method + URL  
`GET /api/clients/`

2) Назначение  
Получение списка клиентов с вычисляемыми полями продаж.

3) Когда frontend вызывает  
Экран списка клиентов, поиск, фильтрация, сортировка.

4) Query params
- `is_active` / boolean / optional / пример: `true`
- `search` / string / optional / пример: `alpha`
- `ordering` / string / optional / пример: `name`
- `page` / int / optional / пример: `1`
- `page_size` / int / optional / пример: `20`

5) Request JSON  
Не используется.

6) Response JSON (пример элемента)
```json
{
  "id": 1,
  "name": "ОсОО Альфа",
  "contact": "Иван",
  "phone": "+996700000000",
  "phone_alt": "+996555000000",
  "inn": "12345678901234",
  "address": "Бишкек",
  "email": "client@example.com",
  "messenger": "Telegram @alpha",
  "client_type": "B2B",
  "notes": "Постоянный клиент",
  "is_active": true,
  "status": "active",
  "sales_count": 12,
  "sales_total": "150000.00",
  "has_sales": true,
  "credit_limit": "100000.00",
  "credit_limit_mode": "soft"
}
```

Поля response (что показывать/скрывать):
- Показывать:
  - `name`, `contact`, `phone`, `inn`, `is_active`, `status`, `sales_count`, `sales_total`, `credit_limit`, `credit_limit_mode`
- Можно показывать в карточке:
  - `address`, `email`, `messenger`, `client_type`, `notes`, `phone_alt`
- Не показывать пользователю как отдельный UI-атрибут:
  - `id` (использовать как технический id)
  - `has_sales` (внутренний флаг для логики кнопок/иконок)

7) Errors
- `401` (не авторизован)
- `403` (нет access_key `clients`)

8) Business rules
- `sales_count` считает только реальные продажи:
  - исключает `sale_status=draft`
  - исключает `sale_status=canceled`
- `sales_total` считает только выручку реальных продаж:
  - исключает `sale_status=draft`
  - исключает `sale_status=canceled`
- Фильтр `is_active` поддерживается.

9) Frontend submit
- Отправляется только query.
- Body не отправляется.

10) UI contract
- Label "Клиент" -> value `name`
- Label "Статус" -> value `status` (`active|inactive`)
- Label "Продаж" -> value `sales_count`
- Label "Сумма продаж" -> value `sales_total`
- В backend для действий отправлять `id`.

---

## 2.2 POST /api/clients/

1) Method + URL  
`POST /api/clients/`

2) Назначение  
Создание клиента.

3) Когда frontend вызывает  
Форма "Создать клиента".

4) Query params  
Не используется.

5) Request JSON (полный пример)
```json
{
  "name": "ОсОО Альфа",
  "contact": "Иван",
  "phone": "+996700000000",
  "phone_alt": "+996555000000",
  "inn": "12345678901234",
  "address": "Бишкек",
  "email": "client@example.com",
  "messenger": "Telegram @alpha",
  "client_type": "B2B",
  "notes": "Постоянный клиент",
  "is_active": true,
  "credit_limit": "100000",
  "credit_limit_mode": "soft"
}
```

Поля request:
- `name` / string / required / writable / null: нет / источник: форма / пример: `"ОсОО Альфа"`
- `contact` / string / optional / writable / null: нет / форма / `"Иван"`
- `phone` / string / optional / writable / null: нет / форма / `"+996700000000"`
- `phone_alt` / string / optional / writable / null: нет / форма / `"+996555000000"`
- `inn` / string / optional / writable / null: нет / форма / `"12345678901234"`
- `address` / string / optional / writable / null: нет / форма / `"Бишкек"`
- `email` / string(email) / optional / writable / null: нет / форма / `"client@example.com"`
- `messenger` / string / optional / writable / null: нет / форма / `"Telegram @alpha"`
- `client_type` / string / optional / writable / null: нет / форма / `"B2B"`
- `notes` / string / optional / writable / null: нет / форма / `"Постоянный клиент"`
- `is_active` / boolean / optional / writable / null: нет / форма / `true`
- `credit_limit` / decimal string / optional / writable / null: да / форма / `"100000"`
- `credit_limit_mode` / enum / optional / writable / null: нет / форма / `"soft"` или `"hard"`

Поля, которые frontend не должен отправлять:
- `id`
- `status` (read-only computed)
- `sales_count` (read-only)
- `sales_total` (read-only)
- `has_sales` (read-only)

6) Response JSON (пример)
```json
{
  "id": 1,
  "name": "ОсОО Альфа",
  "contact": "Иван",
  "phone": "+996700000000",
  "phone_alt": "+996555000000",
  "inn": "12345678901234",
  "address": "Бишкек",
  "email": "client@example.com",
  "messenger": "Telegram @alpha",
  "client_type": "B2B",
  "notes": "Постоянный клиент",
  "is_active": true,
  "status": "active",
  "sales_count": 0,
  "sales_total": "0",
  "has_sales": false,
  "credit_limit": "100000.00",
  "credit_limit_mode": "soft"
}
```

7) Errors
- `400` validation error:
  - `name` отсутствует/пустой
  - `email` невалидный формат
  - `credit_limit_mode` не из `soft|hard`
- `401`
- `403`

8) Business rules
- Клиент создается даже если нет телефона/ИНН/email.
- Дубликаты клиента по `name/phone/inn/email` не запрещены моделью (unique нет).

9) Frontend submit
- Отправлять только writable поля.
- Пример axios:
```js
await axios.post("/api/clients/", {
  name: "ОсОО Альфа",
  contact: "Иван",
  phone: "+996700000000",
  is_active: true,
  credit_limit: "100000",
  credit_limit_mode: "soft"
});
```

10) UI contract
- Пользователь вводит human-readable поля.
- В backend не отправлять вычисляемые поля.

---

## 2.3 GET /api/clients/{id}/

1) Method + URL  
`GET /api/clients/{id}/`

2) Назначение  
Получение детальной карточки клиента.

3) Когда frontend вызывает  
Открытие карточки/редактирования клиента.

4) Query params  
Не реализовано.

5) Request JSON  
Не используется.

6) Response JSON  
Структура как у `GET /api/clients/` для одного объекта.

7) Errors
- `404` если `id` не найден
- `401`
- `403`

8) Business rules  
Read-only поля приходят в ответе, но не обновляются напрямую.

9) Frontend submit  
Не используется.

10) UI contract  
Отображать карточку; идентификатор для update/deactivate — `id`.

---

## 2.4 PATCH /api/clients/{id}/

1) Method + URL  
`PATCH /api/clients/{id}/`

2) Назначение  
Частичное обновление клиента.

3) Когда frontend вызывает  
Редактирование отдельных полей, деактивация/активация, изменение кредита.

4) Query params  
Не используется.

5) Request JSON примеры

Деактивация:
```json
{"is_active": false}
```

Изменение кредита:
```json
{"credit_limit": "50000", "credit_limit_mode": "hard"}
```

6) Response JSON  
Полная карточка клиента (как в GET).

7) Errors
- `400` validation error:
  - `name: ""` -> ошибка по обязательному непустому имени
  - `email` невалидный
  - `credit_limit_mode` невалидный enum
- `404`, `401`, `403`

8) Business rules
- Можно менять:
  - `name`
  - `phone`
  - `is_active`
  - `credit_limit`
  - `credit_limit_mode`
- Read-only поля (`status`, `sales_count`, `sales_total`, `has_sales`) обновлять нельзя.

9) Frontend submit
- Отправлять только изменяемые поля.
- Не отправлять read-only поля.

10) UI contract
- Кнопки:
  - "Сохранить"
  - "Деактивировать/Активировать"
- Для деактивации отправлять `is_active=false`.

---

## 2.5 PUT /api/clients/{id}/

1) Method + URL  
`PUT /api/clients/{id}/`

2) Назначение  
Полное обновление клиента.

3) Когда frontend вызывает  
Если форма отправляет полный объект.

4) Query params  
Не используется.

5) Request JSON  
Полный объект клиента (как при create).

6) Response JSON  
Полная карточка клиента.

7) Errors  
Аналогично PATCH + обязательность `name`.

8) Business rules
- Поведение полей как у PATCH.

9) Frontend submit
- Предпочтительно PATCH для частичных изменений.

10) UI contract
- Если используется PUT, форма должна содержать `name`.

---

## 2.6 DELETE /api/clients/{id}/

1) Method + URL  
`DELETE /api/clients/{id}/`

2) Назначение  
Физическое удаление клиента.

3) Когда frontend вызывает  
Не должен вызывать.

4) Query params  
Не используется.

5) Request JSON  
Не используется.

6) Response JSON
```json
{
  "code": "DELETE_DISABLED",
  "error": "Физическое удаление клиентов отключено. Используйте is_active=false.",
  "detail": "Патч клиента: {\"is_active\": false}."
}
```

7) Errors
- HTTP `405`
- code: `DELETE_DISABLED`

8) Business rules
- DELETE запрещен всегда.

9) Frontend submit
- Вместо delete делать:
```json
{"is_active": false}
```

10) UI contract
- Кнопку "Удалить" не показывать.
- Показывать "Деактивировать".

---

## 2.7 GET /api/clients/{id}/history/

1) Method + URL  
`GET /api/clients/{id}/history/`

2) Назначение  
Агрегированная история клиента: документы + финансовые итоги + кредит.

3) Когда frontend вызывает  
Вкладка "История клиента".

4) Query params  
Не реализовано.

5) Request JSON  
Не используется.

6) Response JSON (пример)
```json
{
  "client_id": 1,
  "client_name": "ОсОО Альфа",
  "orders": [],
  "sales": [],
  "payments": [],
  "returns": [],
  "total_revenue": "100000",
  "total_ordered": "100000",
  "total_paid": "60000",
  "total_paid_gross": "60000",
  "total_refunded": "0",
  "client_debt_money": "40000",
  "client_advance_amount": "0",
  "has_unshipped_goods": false,
  "overdue_orders_count": 0,
  "total_profit": "15000",
  "defect_revenue": "0",
  "credit_limit": "100000",
  "credit_limit_mode": "soft",
  "credit_available": "60000",
  "credit_is_over_limit": false,
  "credit_warning": null
}
```

Массивы:
- `orders[]`: формат `OrderSerializer` (включает `lines[]`).
- `sales[]`: формат `SaleSerializer` (включает `sale_lines[]`).
- `payments[]`: формат `PaymentSerializer`.
- `returns[]`: формат `ReturnSerializer` (включает `lines[]`).

Факты фильтрации:
- `payments` в history: только `status=active`.
- `returns` в history: без фильтра по status (включая canceled).
- `sales` в history: без фильтра по status (включая canceled).

7) Errors
- `404`, `401`, `403`

8) Business rules
- `total_paid` = `incoming - refund` по активным платежам.
- `client_debt_money` и `client_advance_amount` считаются от `sales revenue` и `net_paid`.
- Для финансовых агрегатов history учитываются только реальные продажи:
  - исключаются `sale_status=draft`
  - исключаются `sale_status=canceled`
- `credit_limit_mode` возвращается как raw `soft|hard`.

9) Frontend submit  
Не используется.

10) UI contract
- Показывать:
  - финансовые агрегаты
  - списки документов
- Скрывать:
  - технические id внутренних связей без UX-контекста.
- Перевод status labels в UI:
  - `active/inactive`, `new/confirmed/...`, `draft/completed/canceled`, `paid/...`.

---

## 2.8 GET /api/client-financial-summary/?client_id=

1) Method + URL  
`GET /api/client-financial-summary/?client_id=`

2) Назначение  
Краткая финансовая сводка клиента.

3) Когда frontend вызывает  
Кнопка/виджет "Финсводка".

4) Query params
- `client_id` / int / required / пример: `1`

5) Request JSON  
Не используется.

6) Response JSON (пример)
```json
{
  "client_id": 1,
  "client_name": "ОсОО Альфа",
  "payment_status": "partially_paid",
  "total_revenue": "100000",
  "total_cost": "85000",
  "total_profit": "15000",
  "defect_revenue": "0",
  "total_paid_gross": "60000",
  "total_refunded": "0",
  "total_paid_net": "60000",
  "client_debt_money": "40000",
  "client_advance_amount": "0",
  "credit_limit": "100000",
  "credit_limit_mode": "soft",
  "credit_available": "60000",
  "is_over_limit": false,
  "credit_warning": null
}
```

7) Errors
- `400` code `MISSING_PARAM` если нет `client_id`
- `404` code `NOT_FOUND` если client не найден
- `401`, `403`

8) Business rules
- Реальный URL для frontend: `/api/client-financial-summary/?client_id=...`
- В docstring и swagger зафиксирован канонический URL `/api/client-financial-summary/?client_id=...`.
- `sales` в этом расчете исключают:
  - `sale_status=draft`
  - `sale_status=canceled`
- `payments` только active.

9) Frontend submit
- Отправлять `client_id` в query.

10) UI contract
- `payment_status` labels:
  - `unpaid` -> `Не оплачено`
  - `partially_paid` -> `Частично оплачено`
  - `paid` -> `Оплачено`
  - `overpaid` -> `Переплата`
  - `refunded` -> `Возврат денег`
- `credit_limit_mode` labels:
  - `soft` -> `Мягкий лимит`
  - `hard` -> `Жёсткий лимит`

---

## 2.9 Есть ли ещё связанные endpoints по клиентам

Связанные select-источники клиентов:
- `GET /api/orders/select-sources/` -> возвращает `clients[{id,label}]`
- `GET /api/sales/select-sources/` -> возвращает `clients[{id,label}]`

Отдельный `GET /api/clients/select-sources/`:
- **не реализовано**

---

## 3. ПОЛЯ CLIENT

Поля `ClientSerializer`:

1) `id`
- Тип: int
- Required: no (response only)
- Writable: read-only
- Default: auto pk
- Null/blank: null нет
- Значение: идентификатор
- Показывать: нет как отдельный label, использовать технически
- Отправлять: нет
- НЕ отправлять: всегда
- Пример: `1`

2) `name`
- Тип: string (max 255)
- Required: yes
- Writable: writable
- Default: нет
- Null: нельзя, blank: нельзя
- Значение: название клиента
- Показывать: да
- Отправлять: да
- Пример: `"ОсОО Альфа"`

3) `contact`
- string, optional, writable, default `""`, null нельзя, blank можно.

4) `phone`
- string, optional, writable, default `""`, null нельзя, blank можно.

5) `phone_alt`
- string, optional, writable, default `""`, null нельзя, blank можно.

6) `inn`
- string, optional, writable, default `""`(через blank), null нельзя.

7) `address`
- string(text), optional, writable, default `""`, null нельзя.

8) `email`
- string(email), optional, writable, default `""`, null нельзя.

9) `messenger`
- string, optional, writable, default `""`, null нельзя.

10) `client_type`
- string, optional, writable, default `""`, null нельзя.

11) `notes`
- string(text), optional, writable, default `""`, null нельзя.

12) `is_active`
- boolean, optional, writable, default `true`, null нельзя.

13) `status`
- string, optional, read-only, computed: `active|inactive`.
- frontend не отправляет.

14) `sales_count`
- int, read-only, computed annotation.
- frontend не отправляет.

15) `sales_total`
- decimal-string, read-only, computed annotation/sum.
- frontend не отправляет.

16) `has_sales`
- boolean, read-only, computed.
- frontend не отправляет.

17) `credit_limit`
- decimal / optional / writable / default `null` / null можно.
- frontend показывает и отправляет при редактировании кредита.

18) `credit_limit_mode`
- enum `soft|hard` / optional / writable / default `soft` / null нельзя.

---

## 4. CREATE CLIENT

Endpoint: `POST /api/clients/`

Обязательные поля реально:
- `name` обязательно.

Можно не отправлять:
- `contact,phone,phone_alt,inn,address,email,messenger,client_type,notes,is_active,credit_limit,credit_limit_mode`

Нельзя отправлять (как writable input):
- `status`, `sales_count`, `sales_total`, `has_sales`, `id`

Что backend вернет:
- Полный объект клиента с вычисляемыми read-only полями.

Какие ошибки:
- 400 validation:
  - `name` отсутствует/пустой
  - `email` невалидный
  - `credit_limit_mode` невалидный
- 401/403 доступ.

---

## 5. UPDATE CLIENT

Endpoints:
- `PATCH /api/clients/{id}/`
- `PUT /api/clients/{id}/`

Проверки:
1. Можно менять `name`: да.
2. Можно менять `phone`: да.
3. Можно менять `is_active`: да.
4. Можно менять `credit_limit`: да.
5. Можно менять `credit_limit_mode`: да.
6. Read-only:
   - `status`, `sales_count`, `sales_total`, `has_sales`, `id`
7. Если отправить read-only поля:
   - DRF не применяет изменения в read-only поля (в БД не меняются).
8. Если `name=""`:
   - validation error 400.
9. Если `is_active=false`:
   - клиент деактивируется.

---

## 6. DELETE CLIENT

`DELETE /api/clients/{id}/`

1. Разрешен DELETE: нет.
2. HTTP status: `405`.
3. code: `DELETE_DISABLED`.
4. response JSON: см. раздел 2.6.
5. Что делать frontend: `PATCH {"is_active": false}`.
6. Кнопка "Удалить": не показывать.

---

## 7. ACTIVE / INACTIVE CLIENT

1. Активный клиент: `is_active=true`, `status=active`.
2. Неактивный клиент: `is_active=false`, `status=inactive`.
3. `status` вычисляется serializer-методом.

4. Создать заявку на inactive: нельзя (OrderSerializer.create проверяет).
5. Создать продажу на inactive: нельзя (SaleSerializer.validate/create проверяет).
6. Создать оплату на inactive: нельзя (PaymentSerializer.validate проверяет).
7. Создать возврат на inactive: да, по старой продаже (возврат закрывает старую операцию).
8. Ошибки backend для запрета:
   - validation error с текстом "Клиент неактивен. Создание ... запрещено."

Точный вердикт:
- inactive клиент НЕ может создавать новые:
  - заявки
  - продажи
  - обычные оплаты
- inactive клиент МОЖЕТ иметь возврат по старой продаже, потому что возврат закрывает старую операцию.

---

## 8. CREDIT LIMIT

Поля:
- `credit_limit` (Decimal, nullable)
- `credit_limit_mode` (`soft|hard`)

Режимы:
- `soft`: мягкое предупреждение, не блокирует.
- `hard`: блокировка отгрузки/продажи без override.

Где используется:
- `credit_check.py` (`check_credit_limit`, `enforce_credit_limit`)
- продажи (create/status shipping flow).

Как влияет на продажу:
- при shipping/close sale выполняется проверка.
- `hard` + превышение -> ошибка `CREDIT_LIMIT_BLOCKED` (action status) или serializer validation по кредиту.

Frontend labels:
- `soft` -> "Мягкий лимит"
- `hard` -> "Жёсткий лимит"

Дополнительно:
- `credit_limit` может быть `null`: да.
- `credit_limit` может быть `0`: да (валидно как Decimal).
- если `credit_limit` пустой -> `null`, лимит фактически не ограничивает.
- если `credit_limit_mode` не передан -> default `soft`.

---

## 9. CLIENT HISTORY

Endpoint: `GET /api/clients/{id}/history/`

Request: без body.

Response верхний уровень:
- `client_id`
- `client_name`
- `orders[]`
- `sales[]`
- `payments[]`
- `returns[]`
- `total_revenue`
- `total_ordered`
- `total_paid`
- `total_paid_gross`
- `total_refunded`
- `client_debt_money`
- `client_advance_amount`
- `has_unshipped_goods`
- `overdue_orders_count`
- `total_profit`
- `defect_revenue`
- `credit_limit`
- `credit_limit_mode`
- `credit_available`
- `credit_is_over_limit`
- `credit_warning`

Массивы:
- `orders[]`: поля `OrderSerializer` (включая `lines[]`, payment metrics).
- `sales[]`: поля `SaleSerializer` (включая `sale_lines[]`, payment metrics).
- `payments[]`: поля `PaymentSerializer`.
- `returns[]`: поля `ReturnSerializer` (включая `lines[]`).

Показывать в UI:
- агрегаты долгов/авансов/оплат
- списки документов со статусами

Скрывать:
- технические fk без label-контекста
- служебные creator поля, если не нужен аудит

Проверки учета:
- payments: только active.
- canceled payments: не учитываются.
- returns canceled: в массив `returns` попадают (фильтра нет).
- sales canceled: в массив `sales` попадают (фильтра нет).

---

## 10. CLIENT FINANCIAL SUMMARY

Endpoint: `GET /api/client-financial-summary/?client_id=...`

1) Реальный URL: `/api/client-financial-summary/?client_id=...`
2) Другой docstring URL: не используется (исправлено на канонический URL)
3) Для frontend правильный: `/api/client-financial-summary/?client_id=...` (единственный канонический)
4) `client_id`: required, int, если нет -> `400 MISSING_PARAM`.

Response поля:
- `client_id` int
- `client_name` string
- `payment_status` enum
- `total_revenue` decimal-string
- `total_cost` decimal-string
- `total_profit` decimal-string
- `defect_revenue` decimal-string
- `total_paid_gross` decimal-string
- `total_refunded` decimal-string
- `total_paid_net` decimal-string
- `client_debt_money` decimal-string
- `client_advance_amount` decimal-string
- `credit_limit` decimal-string|null
- `credit_limit_mode` string (`soft|hard`)
- `credit_available` decimal-string|null
- `is_over_limit` boolean
- `credit_warning` string|null

payment_status labels:
- `unpaid` -> Не оплачено
- `partially_paid` -> Частично оплачено
- `paid` -> Оплачено
- `overpaid` -> Переплата
- `refunded` -> Возврат денег

---

## 11. SELECT-SOURCES

1. `/api/clients/select-sources/`:
- **не реализовано**

2. Где брать clients для select:
- `/api/orders/select-sources/`
- `/api/sales/select-sources/`
- `/api/clients/` (для общего списка)

3. label:
- использовать `name` как label

4. id:
- отправлять `id` клиента

---

## 12. ОШИБКИ И HTTP CODES

По клиентским endpoint:

### 400
- `MISSING_PARAM` (financial-summary без client_id)
- DRF validation errors (create/update invalid fields)

### 401
- неавторизованный запрос

### 403
- нет access key `clients`

### 404
- client не найден (`GET/PATCH/PUT/DELETE /clients/{id}`)
- `NOT_FOUND` в financial-summary при несуществующем client_id

### 405
- `DELETE /api/clients/{id}/` -> `DELETE_DISABLED`

### Validation errors
- пустой `name`
- невалидный `email`
- невалидный `credit_limit_mode`

Что frontend должен показывать:
- `DELETE_DISABLED`: "Удаление отключено. Используйте деактивацию."
- `MISSING_PARAM`: "Укажите client_id."
- `NOT_FOUND`: "Клиент не найден."
- validation: поле + текст ошибки.

---

## 13. BUSINESS LOGIC CHECK

1. Можно создать клиента без name: нет.
2. Можно создать дубль клиента: да (unique ограничений нет).
3. Unique по phone/inn/email: нет.
4. Можно удалить клиента: нет.
5. Можно отключить клиента: да (`is_active=false`).
6. Можно вернуть inactive в active: да (`is_active=true`).
7. Inactive влияет на старые документы: нет, существующие документы остаются.
8. Inactive влияет на новые документы:
   - order/sale/payment: да, блокируются.
   - return по старой продаже: разрешен.
9. `sales_count` считается через annotation `Count('sales')` с фильтром по реальным продажам (без draft/canceled).
10. `sales_total` считается через annotation `Sum('sales__revenue')` с фильтром по реальным продажам (без draft/canceled).
11. debt в history/summary: считается от revenue и net_paid.
12. `credit_available`: считается сервисом credit_check.
13. `payment_status`: считается `payment_status.py`.

---

## 14. FRONTEND CONTRACT

### Список клиентов
- endpoint: `GET /api/clients/`
- query: `is_active, search, ordering, page, page_size`
- показывать: `name, phone, status, sales_count, sales_total, credit_limit, credit_limit_mode`
- actions: `Редактировать`, `История`, `Финсводка`, `Деактивировать/Активировать`

### Создание клиента
- endpoint: `POST /api/clients/`
- required fields: `name`
- validation: `name` not blank, valid email, valid credit_limit_mode

### Редактирование
- endpoint: `PATCH /api/clients/{id}/` (предпочтительно)
- forbidden for submit: `status,sales_count,sales_total,has_sales,id`

### Деактивация
- endpoint: `PATCH /api/clients/{id}/`
- body:
```json
{"is_active": false}
```

### История
- endpoint: `GET /api/clients/{id}/history/`
- отображать массивы документов + агрегаты.

### Финсводка
- endpoint: `GET /api/client-financial-summary/?client_id={id}`
- отображать payment_status/debt/credit блок.

### Кнопки UI
- Показывать:
  - Создать
  - Редактировать
  - История
  - Финсводка
  - Деактивировать / Активировать
- Не показывать:
  - Удалить

---

## 15. PROBLEMS

### Critical
- Не найдено критических ошибок в базовом CRUD клиента.

### Medium
- Не найдено.

### Minor
- Нет отдельного `clients/select-sources`.

### API contract mismatch
- Не найдено.

### Legacy
- Legacy пометок по самим клиентским endpoint нет.

### Frontend must not use
- `DELETE /api/clients/{id}/` для удаления бизнес-сущности.

### Missing tests
- Добавлены API тесты в `apps/sales/tests/test_clients_api.py`:
  - create/update/deactivate/reactivate client
  - `DELETE_DISABLED`
  - history structure + агрегаты
  - financial-summary success + ошибки `MISSING_PARAM/NOT_FOUND`
  - блокировка order/sale/payment для inactive client
  - разрешение return по старой продаже для inactive client
  - проверка `sales_count/sales_total` без draft/canceled

---

## 16. FINAL VERDICT

Клиенты backend contract:
- **OK**

Клиенты backend contract закрыт.
