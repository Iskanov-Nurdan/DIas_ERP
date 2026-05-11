"""
Полная очистка бизнес-данных и прикладных пользователей/ролей.

Сохраняются: записи с is_system=True (системный Admin и роль Администратор),
RoleAccess/UserAccess системной пары после ensure_system_admin_entities.

Не трогаются: схема БД, миграции, django.contrib.auth.Permission/Group, contenttypes.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


# Порядок: сначала зависимые (дочерние) строки.
_DELETE_MODEL_LABELS = [
    ('token_blacklist', 'BlacklistedToken'),
    ('token_blacklist', 'OutstandingToken'),
    ('admin', 'LogEntry'),
    ('sessions', 'Session'),
    ('activity', 'AuditOutbox'),
    ('activity', 'UserActivity'),
    ('sales', 'Shipment'),
    ('sales', 'Sale'),
    ('warehouse', 'WarehouseBatch'),
    ('production', 'RecipeRunBatchComponent'),
    ('production', 'RecipeRunBatch'),
    ('production', 'RecipeRun'),
    ('production', 'ShiftComplaint'),
    ('production', 'ShiftNote'),
    ('production', 'Shift'),
    ('otk', 'OtkCheck'),
    ('production', 'ProductionBatch'),
    ('production', 'LineHistory'),
    ('production', 'Order'),
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
    ('production', 'Line'),
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

        total = 0
        with transaction.atomic():
            for app_label, model_name in _DELETE_MODEL_LABELS:
                try:
                    model = apps.get_model(app_label, model_name)
                except LookupError:
                    self.stdout.write(self.style.WARNING(f'Пропуск (нет модели): {app_label}.{model_name}'))
                    continue
                deleted, details = model.objects.all().delete()
                total += deleted
                if details:
                    self.stdout.write(f'{app_label}.{model_name}: {deleted} объектов — {details}')
                else:
                    self.stdout.write(f'{app_label}.{model_name}: {deleted} объектов')

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
