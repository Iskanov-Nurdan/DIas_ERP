from django.db import models


class WarehouseBatch(models.Model):
    STATUS_AVAILABLE = 'available'
    STATUS_RESERVED = 'reserved'
    STATUS_SHIPPED = 'shipped'
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, 'Доступна'),
        (STATUS_RESERVED, 'Зарезервирована'),
        (STATUS_SHIPPED, 'Отгружена'),
    ]

    product = models.CharField('Продукт', max_length=255)
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    date = models.DateField('Дата')
    source_batch = models.ForeignKey('production.ProductionBatch', on_delete=models.SET_NULL, null=True, blank=True, related_name='warehouse_batches')

    class Meta:
        db_table = 'warehouse_batches'
        verbose_name = 'Партия ГП'
        verbose_name_plural = 'Партии ГП на складе'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.product} — {self.quantity} ({self.get_status_display()})'
