from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ChemistryCatalog(models.Model):
    """Справочник химии (полуфабрикат)."""

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
        db_table = 'chemistry_catalog'
        verbose_name = 'Хим. элемент'
        verbose_name_plural = 'Справочник хим. элементов'

    def __str__(self):
        return self.name


class ChemistryRecipe(models.Model):
    """Состав химии: расход сырья на 1 кг готовой химии."""

    chemistry = models.ForeignKey(
        ChemistryCatalog,
        on_delete=models.CASCADE,
        related_name='recipe_lines',
    )
    raw_material = models.ForeignKey(
        'materials.RawMaterial',
        on_delete=models.CASCADE,
        related_name='chemistry_recipe_lines',
    )
    quantity_per_unit = models.DecimalField('Количество на 1 кг химии', max_digits=14, decimal_places=6)

    class Meta:
        db_table = 'chemistry_composition'
        unique_together = [('chemistry', 'raw_material')]
        verbose_name = 'Строка состава химии'
        verbose_name_plural = 'Состав химии (рецепт)'

    def __str__(self):
        return f'{self.chemistry.name} ← {self.raw_material.name}'


class ChemistryBatch(models.Model):
    """Партия произведённой химии (остаток в quantity_remaining)."""

    chemistry = models.ForeignKey(
        ChemistryCatalog,
        on_delete=models.PROTECT,
        related_name='batches',
    )
    quantity_produced = models.DecimalField('Выпущено', max_digits=14, decimal_places=4)
    quantity_remaining = models.DecimalField('Остаток партии', max_digits=14, decimal_places=4)
    cost_total = models.DecimalField('Себестоимость партии', max_digits=16, decimal_places=2, default=0)
    cost_per_unit = models.DecimalField('Себестоимость за кг', max_digits=16, decimal_places=4, default=0)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    produced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chemistry_batches_produced',
    )
    comment = models.TextField('Комментарий', blank=True)
    source_task = models.ForeignKey(
        'ChemistryTask',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='produced_batches',
    )

    class Meta:
        db_table = 'chemistry_batches'
        verbose_name = 'Партия химии'
        verbose_name_plural = 'Партии химии'
        ordering = ['-created_at', '-id']

    def clean(self):
        if self.quantity_produced is not None and self.quantity_remaining is not None:
            if self.quantity_remaining < 0:
                raise ValidationError({'quantity_remaining': 'Остаток не может быть < 0'})
            if self.quantity_remaining > self.quantity_produced:
                raise ValidationError({'quantity_remaining': 'Остаток не может превышать выпуск'})

    def save(self, *args, **kwargs):
        qp = self.quantity_produced or Decimal('0')
        if qp > 0 and self.cost_total is not None:
            self.cost_per_unit = (Decimal(str(self.cost_total)) / Decimal(str(qp))).quantize(Decimal('0.0001'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.chemistry.name} — {self.quantity_produced} кг'


class ChemistryStockDeduction(models.Model):
    """Списание химии из партии (FIFO при производстве профиля / замесе)."""

    batch = models.ForeignKey(
        ChemistryBatch,
        on_delete=models.PROTECT,
        related_name='deductions',
    )
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit_price = models.DecimalField('Себестоимость кг (снимок)', max_digits=16, decimal_places=4)
    line_total = models.DecimalField('Сумма строки', max_digits=16, decimal_places=2)
    reason = models.CharField('Причина', max_length=100, blank=True)
    reference_id = models.PositiveIntegerField('ID связи', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chemistry_stock_deductions'
        verbose_name = 'Списание из партии химии'
        verbose_name_plural = 'Списания из партий химии'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.batch_id}: −{self.quantity}'


class ChemistryTask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'К выполнению'),
        ('in_progress', 'В работе'),
        ('done', 'Выполнено'),
    ]
    name = models.CharField('Название', max_length=255)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='pending')
    deadline = models.DateField('Срок', null=True, blank=True)
    chemistry = models.ForeignKey(ChemistryCatalog, on_delete=models.PROTECT, related_name='tasks')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица', max_length=50, default='kg')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chemistry_tasks'
        verbose_name = 'Задание по химии'
        verbose_name_plural = 'Задания по химии'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} — {self.chemistry.name}'


class ChemistryTaskElement(models.Model):
    task = models.ForeignKey(ChemistryTask, on_delete=models.CASCADE, related_name='elements')
    chemistry = models.ForeignKey(ChemistryCatalog, on_delete=models.CASCADE, related_name='task_elements')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица', max_length=50, default='kg')

    class Meta:
        db_table = 'chemistry_task_elements'
        verbose_name = 'Элемент задания'
        verbose_name_plural = 'Элементы заданий'
