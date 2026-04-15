# Сброс базы данных

## Что очищено

По команде `python manage.py wipe_business_data --yes` удаляются все строки моделей (в указанном порядке, с каскадами Django):

| Приложение | Сущности |
|------------|----------|
| token_blacklist | BlacklistedToken, OutstandingToken |
| admin | LogEntry |
| sessions | Session |
| activity | AuditOutbox, UserActivity |
| sales | Shipment, Sale, Client |
| warehouse | WarehouseBatch |
| production | RecipeRunBatchComponent, RecipeRunBatch, RecipeRun, ShiftComplaint, ShiftNote, Shift, ProductionBatch, LineHistory, Order, Line |
| otk | OtkCheck |
| recipes | RecipeComponent, Recipe, PlasticProfile |
| materials | MaterialStockDeduction, MaterialBatch, RawMaterial |
| chemistry | ChemistryStockDeduction, ChemistryBatch, ChemistryTaskElement, ChemistryTask, ChemistryRecipe, ChemistryCatalog |

Дополнительно в той же команде:

- `accounts.User` с **`is_system=False`** (все прикладные сотрудники).
- `accounts.Role` с **`is_system=False`** (все роли кроме системной).

## Что сохранено

- Структура таблиц, миграции, `django_migrations`.
- `django.contrib.auth` Permission / Group (если используются стандартные таблицы).
- `django_content_type` и прочие системные таблицы Django без явного удаления в команде.
- После очистки заново выравниваются **системная роль** (`Role.is_system=True`) и **системный пользователь** (`User.is_system=True`) через **`ensure_system_admin_entities()`** (пароль и email берутся из `apps/accounts/system_constants.py`, доступы — из `settings.ACCESS_KEYS`).

## Как выполнена очистка

- Файл: `apps/accounts/management/commands/wipe_business_data.py`.
- Запуск: **`python manage.py wipe_business_data --yes`** (флаг `--yes` обязателен).
- Две фазы в транзакциях: (1) удаление по списку моделей; (2) удаление не-системных пользователей и ролей.
- Затем вне транзакции команды вызывается **`ensure_system_admin_entities()`** из `apps/accounts/system_bootstrap.py`.

## Результат

- Рабочие данные (сырьё, химия, рецепты, производство, ОТК, склад, продажи, линии, смены, аудит и т.д.) отсутствуют.
- Проект запускается; миграции не откатываются.
- Вход под системным Admin — по учётным данным из `system_constants` после выполнения команды (**пароль сбрасывается** на значение из констант — рискованно для продакшена, нормально для локального сброса).

## Риски

- Команда **необратима** для бизнес-данных и всех пользователей с `is_system=False`.
- Пароль системного администратора **всегда перезаписывается** при вызове `ensure_system_admin_entities()`.
