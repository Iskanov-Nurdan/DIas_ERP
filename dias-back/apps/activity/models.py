from django.conf import settings
from django.db import models


class UserActivity(models.Model):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_CHOICES = [
        (ACTION_CREATE, 'Создал'),
        (ACTION_UPDATE, 'Изменил'),
        (ACTION_DELETE, 'Удалил'),
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
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        db_table = 'user_activity'
        verbose_name = 'Действие'
        verbose_name_plural = 'Журнал действий'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        user_name = self.user.name if self.user_id else '—'
        return f'{user_name} — {self.get_action_display()} — {self.section}'
