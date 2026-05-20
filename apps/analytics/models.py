from django.conf import settings
from django.db import models


class AnalyticsOtherExpense(models.Model):
    """Прочие расходы аналитики: pending → accepted (в P&L по date) или reject (удаление)."""

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_ACCEPTED, 'Принят'),
    ]

    name = models.CharField('Наименование', max_length=255)
    amount = models.DecimalField('Сумма', max_digits=16, decimal_places=2)
    date = models.DateField('Дата расхода', db_index=True)
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_other_expenses',
    )

    class Meta:
        db_table = 'analytics_other_expenses'
        ordering = ['-date', '-id']
        verbose_name = 'Прочий расход'
        verbose_name_plural = 'Прочие расходы'
        indexes = [
            models.Index(fields=['date', 'status']),
        ]

    def __str__(self) -> str:
        return f'{self.name} — {self.amount} ({self.date}, {self.status})'
