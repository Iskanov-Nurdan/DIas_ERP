from django.db import models


class RawMaterial(models.Model):
    name = models.CharField('Название', max_length=255)
    unit = models.CharField('Единица', max_length=50, default='кг')
    min_balance = models.DecimalField(
        'Мин. остаток (порог)',
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = 'raw_materials'
        verbose_name = 'Сырьё'
        verbose_name_plural = 'Справочник сырья'

    def __str__(self):
        return self.name


class Incoming(models.Model):
    date = models.DateField('Дата')
    material = models.ForeignKey('materials.RawMaterial', on_delete=models.PROTECT, related_name='incomings')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица', max_length=50, default='кг')
    price_per_unit = models.DecimalField('Цена за единицу', max_digits=14, decimal_places=2, default=0)
    batch = models.CharField('Партия', max_length=100, blank=True)
    supplier = models.CharField('Поставщик', max_length=255, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        db_table = 'incoming'
        verbose_name = 'Приход'
        verbose_name_plural = 'Приходы сырья'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.material.name} — {self.quantity} ({self.date})'


class MaterialWriteoff(models.Model):
    """Списание сырья (химия, производство)."""
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='writeoffs')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица', max_length=50, default='кг')
    reason = models.CharField('Причина', max_length=100, blank=True)  # chemistry_task, production_batch
    reference_id = models.PositiveIntegerField('ID связи', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_writeoffs'
        verbose_name = 'Списание сырья'
        ordering = ['-created_at']
