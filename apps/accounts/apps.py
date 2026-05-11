from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'
    verbose_name = 'Пользователи и роли'

    def ready(self):
        from django.db.utils import OperationalError, ProgrammingError

        from . import signals  # noqa: F401
        from . import system_bootstrap  # noqa: F401 — post_migrate
        from .system_bootstrap import ensure_system_admin_entities

        try:
            ensure_system_admin_entities()
        except (ProgrammingError, OperationalError):
            # Таблицы ещё не созданы (первая миграция и т.п.)
            pass
