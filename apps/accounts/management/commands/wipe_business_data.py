"""
Полная очистка бизнес-данных и прикладных пользователей/ролей.

Сохраняются: записи с is_system=True (системный Admin и роль Администратор),
RoleAccess/UserAccess системной пары после ensure_system_admin_entities.

Не трогаются: схема БД, миграции, django.contrib.auth.Permission/Group, contenttypes.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError


# Список — просто «что вообще считаем бизнес-данными» (какие таблицы чистим).
# Порядок значения не имеет: handle() ниже сам разруливает on_delete=PROTECT/
# RESTRICT повторными проходами, а не жёсткой последовательностью — раньше
# список приходилось держать строго «дети перед родителями» вручную, и он
# дважды расходился с реальной схемой (сначала без Workshop/Foam/ОТК, потом
# без sales.Order/OrderLine — см. ProtectedError на PlasticProfile через
# OtkAccountLine). Совсем «на автомате» через apps.get_models() не берём
# специально: список — это ещё и explicit-документация, что именно считается
# бизнес-данными, а не служебными Django-таблицами.
_DELETE_MODEL_LABELS = [
    ('token_blacklist', 'BlacklistedToken'),
    ('token_blacklist', 'OutstandingToken'),
    ('admin', 'LogEntry'),
    ('sessions', 'Session'),
    ('activity', 'AuditOutbox'),
    ('activity', 'UserActivity'),

    # --- sales ---
    ('sales', 'Shipment'),
    ('sales', 'OrderReservation'),
    ('sales', 'ReworkRequest'),
    ('sales', 'DefectRecord'),
    ('sales', 'Return'),           # ReturnLine — каскадом
    ('sales', 'Payment'),
    ('sales', 'Sale'),             # SaleLine — каскадом
    ('sales', 'ClientPrice'),
    ('sales', 'ProductPrice'),
    ('sales', 'PriceList'),
    ('sales', 'OrderLine'),
    ('sales', 'Order'),

    ('warehouse', 'WarehouseBatch'),

    # --- workshop / ОТК (цех) ---
    ('workshop', 'OtkAccountBlankAllocation'),
    ('workshop', 'OtkAccountLine'),
    ('workshop', 'OtkAccountSession'),
    ('workshop', 'OtkBlankIntake'),
    ('workshop', 'OtkBlankPool'),
    ('workshop', 'BlankProductionRun'),
    ('workshop', 'WorkshopPreparedState'),
    ('workshop', 'WorkshopBlankCompositionLine'),
    ('workshop', 'WorkshopBlank'),
    ('otk', 'OtkCheck'),

    # --- production ---
    ('production', 'RecipeRunBatchComponent'),
    ('production', 'RecipeRunBatch'),
    ('production', 'RecipeRun'),
    ('production', 'ShiftComplaint'),
    ('production', 'ShiftNote'),
    ('production', 'ShiftPhotoReport'),   # ShiftPhotoReportImage — каскадом
    ('production', 'Shift'),
    ('production', 'ProductionBatch'),
    ('production', 'LineHistory'),
    ('production', 'Order'),              # legacy production.Order (не sales.Order)
    ('production', 'Line'),

    # --- foam (вторая линия — Пенополистирол) ---
    ('foam', 'FoamSaleLine'),
    ('foam', 'FoamSale'),
    ('foam', 'FoamGpOperation'),
    ('foam', 'FoamGpStock'),
    ('foam', 'FoamProductionRun'),
    ('foam', 'FoamRawLot'),
    ('foam', 'FoamDensityGrade'),

    # --- recipes / materials / chemistry ---
    ('recipes', 'RecipeComponent'),
    ('recipes', 'Recipe'),
    ('recipes', 'PlasticProfile'),
    ('materials', 'MaterialStockDeduction'),
    ('materials', 'MaterialBatch'),
    ('chemistry', 'ChemistryStockDeduction'),
    ('chemistry', 'ChemistryBatch'),
    ('chemistry', 'ChemistryTaskElement'),
    ('chemistry', 'ChemistryTask'),
    ('chemistry', 'ChemistryRecipe'),
    ('materials', 'RawMaterial'),
    ('chemistry', 'ChemistryCatalog'),

    ('sales', 'Client'),
]


class Command(BaseCommand):
    help = 'Полная очистка бизнес-данных; системный Admin и роль Администратор восстанавливаются через ensure_system_admin_entities.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes',
            action='store_true',
            dest='confirm',
            help='Подтвердить без интерактива (обязательно)',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('Добавьте флаг --yes для подтверждения полной очистки.')

        models = []
        for app_label, model_name in _DELETE_MODEL_LABELS:
            try:
                models.append(apps.get_model(app_label, model_name))
            except LookupError:
                self.stdout.write(self.style.WARNING(f'Пропуск (нет модели): {app_label}.{model_name}'))

        total = 0
        with transaction.atomic():
            # Реальный порядок в _DELETE_MODEL_LABELS не важен: пробуем удалить
            # всё по очереди, что не удалось из-за PROTECT/RESTRICT (ссылается
            # ещё не удалённая строка другой модели из этого же списка) —
            # откладываем и пробуем снова следующим проходом. Так порядок
            # моделей в списке никогда больше не «сломает» команду — важно
            # только чтобы модель вообще была в списке.
            pending = models
            while pending:
                blocked = []
                progressed = False
                for model in pending:
                    label = f'{model._meta.app_label}.{model.__name__}'
                    try:
                        with transaction.atomic():
                            deleted, details = model.objects.all().delete()
                    except (ProtectedError, RestrictedError):
                        blocked.append(model)
                        continue
                    progressed = True
                    total += deleted
                    if details:
                        self.stdout.write(f'{label}: {deleted} объектов — {details}')
                    else:
                        self.stdout.write(f'{label}: {deleted} объектов')
                if blocked and not progressed:
                    names = ', '.join(f'{m._meta.app_label}.{m.__name__}' for m in blocked)
                    raise CommandError(
                        f'Не удалось удалить (PROTECT на модель вне этого списка?): {names}'
                    )
                pending = blocked

        User = apps.get_model('accounts', 'User')
        Role = apps.get_model('accounts', 'Role')
        with transaction.atomic():
            u_del, u_det = User.objects.filter(is_system=False).delete()
            r_del, r_det = Role.objects.filter(is_system=False).delete()
            total += u_del + r_del
            self.stdout.write(f'accounts.User (не системные): {u_del} — {u_det}')
            self.stdout.write(f'accounts.Role (не системные): {r_del} — {r_det}')

        from apps.accounts.system_bootstrap import ensure_system_admin_entities

        ensure_system_admin_entities()

        users_left = User.objects.count()
        roles_left = Role.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f'Готово. Удалено записей (сумма каскадов): {total}. '
                f'Осталось пользователей: {users_left}, ролей: {roles_left}. '
                f'Системный Admin и доступы синхронизированы.'
            )
        )
