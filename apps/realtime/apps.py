from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.realtime'
    label = 'realtime'
    verbose_name = 'Realtime (WebSocket)'

    def ready(self):
        import apps.realtime.signals  # noqa: F401
