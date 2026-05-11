"""
Восстановление системной роли и системного пользователя (идемпотентно).

Вызывается при старте приложения и после миграций accounts.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_migrate

from .system_constants import (
    SYSTEM_ADMIN_EMAIL,
    SYSTEM_ADMIN_PASSWORD,
    SYSTEM_ADMIN_ROLE_NAME,
    SYSTEM_ADMIN_USERNAME,
)

logger = logging.getLogger(__name__)


def _sync_role_accesses(role, access_keys):
    from .models import RoleAccess

    desired = set(access_keys)
    existing = set(role.accesses.values_list('access_key', flat=True))
    for key in desired - existing:
        RoleAccess.objects.get_or_create(role=role, access_key=key)
    if existing - desired:
        role.accesses.exclude(access_key__in=desired).delete()


def _sync_user_accesses(user, access_keys):
    from .models import UserAccess

    desired_set = set(access_keys)
    desired = sorted(desired_set)
    existing = set(user.user_accesses.values_list('access_key', flat=True))
    if desired_set == existing:
        return
    user.user_accesses.exclude(access_key__in=desired_set).delete()
    for key in desired:
        UserAccess.objects.get_or_create(user=user, access_key=key)


def ensure_system_admin_entities():
    """
    Гарантирует наличие системной роли «Администратор» и пользователя Admin
    с полным набором RoleAccess/UserAccess и корректными флагами.
    """
    from django.contrib.auth import get_user_model

    from .models import Role

    User = get_user_model()
    access_keys = list(getattr(settings, 'ACCESS_KEYS', ()) or [])
    if not access_keys:
        logger.warning('ensure_system_admin_entities: ACCESS_KEYS пуст — пропуск синхронизации доступов')

    with transaction.atomic():
        role = (
            Role.objects.select_for_update()
            .filter(is_system=True)
            .first()
        )
        if role is None:
            role = Role.objects.create(
                name=SYSTEM_ADMIN_ROLE_NAME,
                description='Системная роль. Не изменяется и не удаляется через API.',
                is_system=True,
            )
        else:
            changed = False
            if role.name != SYSTEM_ADMIN_ROLE_NAME:
                role.name = SYSTEM_ADMIN_ROLE_NAME
                changed = True
            if not role.is_system:
                role.is_system = True
                changed = True
            if changed:
                role.save(update_fields=['name', 'is_system'])

        if access_keys:
            _sync_role_accesses(role, access_keys)

        for legacy in User.objects.select_for_update().filter(
            name=SYSTEM_ADMIN_USERNAME,
            is_system=False,
        ):
            legacy.name = f'renamed_from_admin_{legacy.pk}'
            legacy.save(update_fields=['name'])

        user = (
            User.objects.select_for_update()
            .filter(is_system=True)
            .first()
        )
        if user is None:
            user = User(
                email=SYSTEM_ADMIN_EMAIL,
                name=SYSTEM_ADMIN_USERNAME,
                role=role,
                is_system=True,
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
            user.set_password(SYSTEM_ADMIN_PASSWORD)
            user.save()
        else:
            user.set_password(SYSTEM_ADMIN_PASSWORD)
            user.email = SYSTEM_ADMIN_EMAIL
            user.name = SYSTEM_ADMIN_USERNAME
            user.role = role
            user.is_system = True
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.save(
                update_fields=[
                    'password',
                    'email',
                    'name',
                    'role',
                    'is_system',
                    'is_staff',
                    'is_superuser',
                    'is_active',
                ],
            )

        if access_keys:
            _sync_user_accesses(user, access_keys)


def _accounts_post_migrate_receiver(sender, **kwargs):
    # sender — AppConfig приложения после миграции
    if getattr(sender, 'label', None) != 'accounts':
        return
    try:
        ensure_system_admin_entities()
    except Exception:
        logger.exception('ensure_system_admin_entities после миграции accounts')


post_migrate.connect(_accounts_post_migrate_receiver)
