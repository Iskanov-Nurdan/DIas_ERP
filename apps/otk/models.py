from django.db import models
from django.conf import settings


class OtkCheck(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_ACCEPTED, 'Принято'),
        (STATUS_REJECTED, 'Брак'),
    ]

    batch = models.ForeignKey('production.ProductionBatch', on_delete=models.CASCADE, related_name='otk_checks')
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='otk_checks',
    )
    pieces = models.PositiveIntegerField('Штук', default=0)
    length_per_piece = models.DecimalField('Длина штуки, м', max_digits=14, decimal_places=4, default=0)
    total_meters = models.DecimalField('Всего м', max_digits=16, decimal_places=4, default=0)
    accepted = models.DecimalField('Принято (legacy)', max_digits=14, decimal_places=4, default=0)
    rejected = models.DecimalField('Брак (legacy)', max_digits=14, decimal_places=4, default=0)
    check_status = models.CharField(
        'Результат',
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    reject_reason = models.TextField('Причина брака', blank=True)
    comment = models.TextField('Комментарий', blank=True)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='otk_checks')
    inspector_name = models.CharField('Контролёр (строка)', max_length=255, blank=True, default='')
    checked_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otk_checks'
        verbose_name = 'Проверка ОТК'
        verbose_name_plural = 'Проверки ОТК'
        ordering = ['-checked_date']

    def __str__(self):
        return f'Партия #{self.batch_id} — {self.get_check_status_display()}'
