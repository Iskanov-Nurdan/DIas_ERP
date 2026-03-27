"""
Удаление всех прикладных данных, сохранение сотрудников (accounts.User, Role, RoleAccess).

Также сохраняются: django.contrib.auth (Permission, Group), contenttypes, миграции.
Сессии, JWT blacklist и admin LogEntry очищаются.
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
    ('materials', 'MaterialWriteoff'),
    ('materials', 'Incoming'),
    ('chemistry', 'ChemistryComposition'),
    ('chemistry', 'ChemistryTaskElement'),
    ('chemistry', 'ChemistryStock'),
    ('chemistry', 'ChemistryTask'),
    ('materials', 'RawMaterial'),
    ('chemistry', 'ChemistryCatalog'),
    ('production', 'Line'),
    ('sales', 'Client'),
]


class Command(BaseCommand):
    help = 'Удалить все данные приложения, кроме сотрудников (пользователи, роли, доступы ролей).'

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

        with transaction.atomic():
            total = 0
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

        users_left = apps.get_model('accounts', 'User').objects.count()
        roles_left = apps.get_model('accounts', 'Role').objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f'Готово. Удалено записей (сумма каскадов): {total}. '
                f'Осталось пользователей: {users_left}, ролей: {roles_left}.'
            )
        )
