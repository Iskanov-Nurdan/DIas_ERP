from django.db import models
from django.conf import settings


class OtkCheck(models.Model):
    batch = models.ForeignKey('production.ProductionBatch', on_delete=models.CASCADE, related_name='otk_checks')
    accepted = models.DecimalField('Принято', max_digits=14, decimal_places=4, default=0)
    rejected = models.DecimalField('Брак', max_digits=14, decimal_places=4, default=0)
    reject_reason = models.TextField('Причина брака', blank=True)
    comment = models.TextField('Комментарий', blank=True)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='otk_checks')
    checked_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otk_checks'
        verbose_name = 'Проверка ОТК'
        verbose_name_plural = 'Проверки ОТК'
        ordering = ['-checked_date']

    def __str__(self):
        return f'Партия #{self.batch_id} — принято {self.accepted}, брак {self.rejected}'
