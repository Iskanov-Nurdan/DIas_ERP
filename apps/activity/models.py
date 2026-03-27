from django.conf import settings
from django.db import models


class UserActivity(models.Model):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_RESTORE = 'restore'
    ACTION_CHOICES = [
        (ACTION_CREATE, 'Создал'),
        (ACTION_UPDATE, 'Изменил'),
        (ACTION_DELETE, 'Удалил'),
        (ACTION_RESTORE, 'Восстановил'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='activities',
        verbose_name='Пользователь',
    )
    action = models.CharField('Действие', max_length=10, choices=ACTION_CHOICES)
    section = models.CharField('Раздел', max_length=100)
    description = models.TextField('Описание')
    summary = models.CharField(
        'Краткое описание (список)',
        max_length=500,
        blank=True,
        default='',
    )
    created_at = models.DateTimeField('Время', auto_now_add=True)

    shift = models.ForeignKey(
        'production.Shift',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_activities',
        verbose_name='Смена',
    )
    line = models.ForeignKey(
        'production.Line',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_activities',
        verbose_name='Линия',
    )
    session_open_event_id = models.PositiveIntegerField(
        'ID события открытия сессии (LineHistory)',
        null=True,
        blank=True,
    )

    request_id = models.CharField('Request / correlation id', max_length=64, blank=True, default='', db_index=True)
    entity_type = models.CharField('Тип сущности', max_length=120, blank=True, default='', db_index=True)
    entity_id = models.CharField('ID сущности', max_length=64, blank=True, default='', db_index=True)

    payload_version = models.PositiveSmallIntegerField('Версия схемы payload', default=0)
    payload = models.JSONField('Детализация (changes, snapshot)', default=dict, blank=True)

    actor_role_snapshot = models.CharField('Роль (снимок)', max_length=200, blank=True, default='')
    client_ip = models.CharField('IP клиента', max_length=45, blank=True, default='')
    user_agent = models.TextField('User-Agent', blank=True, default='')

    class Meta:
        db_table = 'user_activity'
        verbose_name = 'Действие'
        verbose_name_plural = 'Журнал действий'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at'], name='user_activi_user_id_e0c66f_idx'),
            models.Index(fields=['-created_at'], name='user_activi_created_309e54_idx'),
            models.Index(fields=['shift', '-created_at'], name='user_activi_shift_i_idx'),
            models.Index(
                fields=['entity_type', 'entity_id', '-created_at'],
                name='user_activi_entity_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['request_id', 'entity_type', 'entity_id', 'action'],
                name='uniq_user_activity_idempotent_v1',
                condition=models.Q(request_id__gt='', entity_type__gt='', entity_id__gt=''),
            ),
        ]

    def __str__(self):
        user_name = self.user.name if self.user_id else '—'
        return f'{user_name} — {self.get_action_display()} — {self.section}'


class AuditOutbox(models.Model):
    """Очередь повторной записи аудита при сбое после commit."""

    payload = models.JSONField(default=dict)
    last_error = models.TextField(blank=True, default='')
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'audit_outbox'
        verbose_name = 'Очередь аудита'
        verbose_name_plural = 'Очередь аудита'
        ordering = ['created_at']

    def __str__(self):
        return f'AuditOutbox #{self.pk} attempts={self.attempts}'
