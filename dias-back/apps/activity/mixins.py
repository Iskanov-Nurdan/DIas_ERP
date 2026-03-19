"""
ActivityLoggingMixin — добавить к ViewSet для автоматической записи действий пользователя.

Использование:
    class MyViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
        activity_section = 'Заказы'
        activity_label = 'заказ'   # используется в описании: "Создал заказ №123"
"""

import logging

logger = logging.getLogger(__name__)


class ActivityLoggingMixin:
    activity_section = ''
    activity_label = ''

    def _activity_description(self, instance, action: str) -> str:
        action_map = {
            'create': 'Создал',
            'update': 'Изменил',
            'delete': 'Удалил',
        }
        verb = action_map.get(action, action)
        label = self.activity_label or self.activity_section.lower()
        try:
            obj_str = str(instance)
        except Exception:
            obj_str = f'#{getattr(instance, "pk", "?")}'
        return f'{verb} {label}: {obj_str}'

    def _log_activity(self, action: str, instance) -> None:
        from apps.activity.models import UserActivity
        request = getattr(self, 'request', None)
        if request is None or not request.user.is_authenticated:
            return
        try:
            UserActivity.objects.create(
                user=request.user,
                action=action,
                section=self.activity_section,
                description=self._activity_description(instance, action),
            )
        except Exception as exc:
            logger.exception('Не удалось записать действие в журнал: %s', exc)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._log_activity('create', serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._log_activity('update', serializer.instance)

    def perform_destroy(self, instance):
        description = self._activity_description(instance, 'delete')
        request = getattr(self, 'request', None)
        super().perform_destroy(instance)
        from apps.activity.models import UserActivity
        if request and request.user.is_authenticated:
            try:
                UserActivity.objects.create(
                    user=request.user,
                    action='delete',
                    section=self.activity_section,
                    description=description,
                )
            except Exception as exc:
                logger.exception('Не удалось записать удаление в журнал: %s', exc)
