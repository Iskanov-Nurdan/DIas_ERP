from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.accounts.models import Role, RoleAccess

User = get_user_model()


class Command(BaseCommand):
    help = 'Создать начальные роли и админ-пользователя'

    def handle(self, *args, **options):
        admin_role, _ = Role.objects.get_or_create(
            name='Админ',
            defaults={'description': 'Полный доступ'}
        )
        for key in settings.ACCESS_KEYS:
            RoleAccess.objects.get_or_create(role=admin_role, access_key=key)

        planner_role, _ = Role.objects.get_or_create(
            name='Планировщик',
            defaults={'description': 'Линии, заказы, рецепты'}
        )
        for key in settings.USERS_PLANNER_ACCESS_KEYS:
            RoleAccess.objects.get_or_create(role=planner_role, access_key=key)

        admin_user = User.objects.filter(name='admin', is_superuser=True).first()
        if admin_user is None:
            admin_user = User.objects.filter(email='admin@dias.local').first()
        if admin_user:
            admin_user.name = 'admin'
            admin_user.set_password('admin')
            admin_user.is_staff = True
            admin_user.is_superuser = True
            admin_user.is_active = True
            admin_user.save(update_fields=['name', 'password', 'is_staff', 'is_superuser', 'is_active'])
            self.stdout.write(self.style.SUCCESS('Суперпользователь admin обновлён: admin / admin'))
        else:
            User.objects.create_superuser(
                email='admin@dias.local',
                password='admin',
                name='admin',
            )
            self.stdout.write(self.style.SUCCESS('Создан суперпользователь admin / admin'))
        self.stdout.write(self.style.SUCCESS('Сиды применены'))
