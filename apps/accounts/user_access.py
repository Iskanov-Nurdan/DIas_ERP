"""
RBAC: меню UI строится только по UserAccess (см. User.get_access_keys).

RoleAccess — шаблон для сидов и формы роли в админке; при создании пользователя
ключи один раз копируются в UserAccess (copy_role_template_to_user_if_empty).
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _expected_access_keys_for_role(role):
    """Список ключей для известных сидовых ролей; для остальных — None (не трогаем)."""
    if role is None:
        return None
    admin_name = getattr(settings, 'USERS_SUPERUSER_ROLE_NAME', 'Админ')
    planner_name = getattr(settings, 'USERS_DEFAULT_ROLE_NAME', 'Планировщик')
    if role.name == admin_name:
        return list(getattr(settings, 'ACCESS_KEYS', ()))
    if role.name == planner_name:
        return list(getattr(settings, 'USERS_PLANNER_ACCESS_KEYS', ()))
    return None


def ensure_role_tab_access_keys(role) -> bool:
    """
    Для ролей «Админ» и «Планировщик» гарантирует наличие записей RoleAccess
    (полный набор из settings — как после seed_roles).

    Идемпотентно: только get_or_create, ничего не удаляет (кастомные ключи у роли сохраняются).
    Возвращает True, если создана хотя бы одна новая запись.
    """
    from .models import RoleAccess

    keys = _expected_access_keys_for_role(role)
    if not keys:
        return False

    created_any = False
    for key in keys:
        _, created = RoleAccess.objects.get_or_create(role=role, access_key=key)
        created_any = created_any or created
    return created_any


def ensure_user_role_for_tab_accesses(user) -> bool:
    """
    Если у пользователя нет роли — назначает роль по умолчанию (как у сидов seed_roles).

    Суперпользователь без роли получает роль «Админ», остальные — «Планировщик»
    (имена настраиваются в settings.USERS_SUPERUSER_ROLE_NAME / USERS_DEFAULT_ROLE_NAME).

    Возвращает True, если поле role было выставлено и нужен save/update в БД.
    """
    from .models import Role

    if user.role_id:
        return False

    super_name = getattr(settings, 'USERS_SUPERUSER_ROLE_NAME', 'Админ')
    default_name = getattr(settings, 'USERS_DEFAULT_ROLE_NAME', 'Планировщик')
    target_name = super_name if getattr(user, 'is_superuser', False) else default_name

    role = Role.objects.filter(name=target_name).first()
    if role is None:
        logger.warning(
            'ensure_user_role_for_tab_accesses: роль %r не найдена, пользователь id=%s остаётся без роли',
            target_name,
            getattr(user, 'pk', None),
        )
        return False

    user.role = role
    return True


def copy_role_template_to_user_if_empty(user) -> None:
    """
    Если у пользователя ещё нет UserAccess — копируем ключи из RoleAccess роли (шаблон).
    Не вызывать при обновлении существующей учётки (чтобы не затирать индивидуальные права).
    """
    from .models import UserAccess

    if user.pk and user.user_accesses.exists():
        return
    if not user.role_id:
        return
    keys = list(user.role.accesses.values_list('access_key', flat=True))
    if not keys:
        return
    UserAccess.objects.bulk_create(
        [UserAccess(user=user, access_key=k) for k in keys],
        ignore_conflicts=True,
    )
