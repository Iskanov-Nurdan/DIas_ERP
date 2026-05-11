"""
RBAC: меню UI — только UserAccess (см. User.get_access_keys).

При создании пользователя вкладки не зависят от роли: в UserAccess попадает
полный список settings.ACCESS_KEYS (seed_full_user_access_if_empty).
RoleAccess остаётся для сидов / справочника ролей в админке.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _expected_access_keys_for_role(role):
    """Список ключей для известных сидовых ролей; для остальных — None (не трогаем)."""
    if role is None:
        return None
    if getattr(role, 'is_system', False):
        return list(getattr(settings, 'ACCESS_KEYS', ()))
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

    if getattr(user, 'is_system', False):
        return False

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


def seed_full_user_access_if_empty(user) -> None:
    """
    Для нового пользователя: все ключи вкладок из settings.ACCESS_KEYS.
    Роль не учитывается. Не вызывать при обновлении существующей учётки с уже
    настроенными UserAccess (есть строки — пропуск).
    """
    from .models import UserAccess

    if user.pk and user.user_accesses.exists():
        return
    keys = list(getattr(settings, 'ACCESS_KEYS', ()))
    if not keys:
        return
    UserAccess.objects.bulk_create(
        [UserAccess(user=user, access_key=k) for k in keys],
        ignore_conflicts=True,
    )
