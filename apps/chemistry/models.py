from django.db import models


class ChemistryCatalog(models.Model):
    name = models.CharField('Название', max_length=255)
    unit = models.CharField('Единица', max_length=50, default='кг')

    class Meta:
        db_table = 'chemistry_catalog'
        verbose_name = 'Хим. элемент'
        verbose_name_plural = 'Справочник хим. элементов'

    def __str__(self):
        return self.name


class ChemistryComposition(models.Model):
    chemistry = models.ForeignKey(ChemistryCatalog, on_delete=models.CASCADE, related_name='compositions')
    raw_material = models.ForeignKey('materials.RawMaterial', on_delete=models.CASCADE, related_name='chemistry_compositions')
    quantity_per_unit = models.DecimalField('Количество на единицу', max_digits=14, decimal_places=6)

    class Meta:
        db_table = 'chemistry_composition'
        unique_together = [('chemistry', 'raw_material')]
        verbose_name = 'Состав хим. элемента'
        verbose_name_plural = 'Составы'

    def __str__(self):
        return f'{self.chemistry.name} ← {self.raw_material.name}'


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
    unit = models.CharField('Единица', max_length=50, default='кг')
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
    unit = models.CharField('Единица', max_length=50, default='кг')

    class Meta:
        db_table = 'chemistry_task_elements'
        verbose_name = 'Элемент задания'
        verbose_name_plural = 'Элементы заданий'


class ChemistryStock(models.Model):
    chemistry = models.OneToOneField(ChemistryCatalog, on_delete=models.CASCADE, related_name='stock')
    quantity = models.DecimalField('Остаток', max_digits=14, decimal_places=4, default=0)
    unit = models.CharField('Единица', max_length=50, default='кг')
    updated_at = models.DateTimeField(auto_now=True)
    last_task = models.ForeignKey(
        'ChemistryTask', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    class Meta:
        db_table = 'chemistry_stock'
        verbose_name = 'Остаток хим. элемента'
        verbose_name_plural = 'Остатки хим. элементов'

    def __str__(self):
        return f'{self.chemistry.name}: {self.quantity} {self.unit}'
