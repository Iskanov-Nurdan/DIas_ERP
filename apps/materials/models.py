from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class RawMaterial(models.Model):
    """Справочник сырья (без цены, поставщика и остатка)."""

    name = models.CharField('Название', max_length=255)
    unit = models.CharField('Единица', max_length=50, default='kg')
    min_balance = models.DecimalField(
        'Мин. остаток (порог)',
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField('Активен', default=True)
    comment = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'raw_materials'
        verbose_name = 'Сырьё'
        verbose_name_plural = 'Справочник сырья'

    def __str__(self):
        return self.name


class MaterialBatch(models.Model):
    """Партия прихода (Incoming): остаток в quantity_remaining."""

    material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name='batches',
    )
    quantity_initial = models.DecimalField('Начальное количество', max_digits=14, decimal_places=4)
    quantity_remaining = models.DecimalField('Остаток по партии', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица хранения количества', max_length=50, default='kg')
    unit_price = models.DecimalField('Цена за единицу', max_digits=14, decimal_places=2, default=0)
    total_price = models.DecimalField('Сумма партии', max_digits=16, decimal_places=2, default=0)
    supplier_name = models.CharField('Поставщик', max_length=255, blank=True)
    supplier_batch_number = models.CharField('Номер партии поставщика', max_length=100, blank=True)
    comment = models.TextField('Комментарий', blank=True)
    received_at = models.DateTimeField('Дата прихода')
    created_at = models.DateTimeField('Запись создана', auto_now_add=True)

    class Meta:
        db_table = 'material_batches'
        verbose_name = 'Партия сырья'
        verbose_name_plural = 'Партии прихода сырья'
        ordering = ['-received_at', '-created_at', '-id']

    def clean(self):
        if self.quantity_initial is not None and self.quantity_remaining is not None:
            if self.quantity_remaining < 0:
                raise ValidationError({'quantity_remaining': 'Остаток не может быть < 0'})
            if self.quantity_remaining > self.quantity_initial:
                raise ValidationError({'quantity_remaining': 'Остаток не может превышать начальное количество'})

    def save(self, *args, **kwargs):
        q = self.quantity_initial or Decimal('0')
        p = self.unit_price or Decimal('0')
        self.total_price = (Decimal(str(q)) * Decimal(str(p))).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.material.name} — {self.quantity_initial} ({self.received_at})'


class MaterialStockDeduction(models.Model):
    """Списание сырья из партии (FIFO); строки откатываются по reason + reference_id."""

    batch = models.ForeignKey(
        MaterialBatch,
        on_delete=models.PROTECT,
        related_name='deductions',
    )
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit_price = models.DecimalField('Цена партии (снимок)', max_digits=14, decimal_places=2)
    line_total = models.DecimalField('Сумма строки', max_digits=16, decimal_places=2)
    reason = models.CharField('Причина', max_length=100, blank=True)
    reference_id = models.PositiveIntegerField('ID связи', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'material_stock_deductions'
        verbose_name = 'Списание из партии'
        verbose_name_plural = 'Списания из партий'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.batch_id}: −{self.quantity}'
