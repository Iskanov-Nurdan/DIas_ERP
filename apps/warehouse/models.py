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

    INVENTORY_UNPACKED = 'unpacked'
    INVENTORY_PACKED = 'packed'
    INVENTORY_OPEN_PACKAGE = 'open_package'
    INVENTORY_FORM_CHOICES = [
        (INVENTORY_UNPACKED, 'Не упаковано'),
        (INVENTORY_PACKED, 'Упаковано'),
        (INVENTORY_OPEN_PACKAGE, 'Открытая упаковка'),
    ]

    product = models.CharField('Продукт', max_length=255)
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    date = models.DateField('Дата')
    source_batch = models.ForeignKey(
        'production.ProductionBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_batches',
    )
    inventory_form = models.CharField(
        'Форма учёта на складе ГП',
        max_length=20,
        choices=INVENTORY_FORM_CHOICES,
        default=INVENTORY_UNPACKED,
    )

    # Параметры упаковки (снимок на момент приёмки на склад ГП)
    unit_meters = models.DecimalField('М/ед.', max_digits=14, decimal_places=4, null=True, blank=True)
    package_total_meters = models.DecimalField('М в упаковке', max_digits=14, decimal_places=4, null=True, blank=True)
    pieces_per_package = models.DecimalField('Штук в упаковке', max_digits=14, decimal_places=4, null=True, blank=True)
    packages_count = models.DecimalField('Число упаковок', max_digits=14, decimal_places=4, null=True, blank=True)

    # Снимок ОТК по исходной партии (для карточки ГП / «Подробнее»)
    otk_accepted = models.DecimalField('ОТК принято', max_digits=14, decimal_places=4, null=True, blank=True)
    otk_defect = models.DecimalField('ОТК брак', max_digits=14, decimal_places=4, null=True, blank=True)
    otk_defect_reason = models.TextField('Причина брака', blank=True)
    otk_comment = models.TextField('Комментарий ОТК', blank=True)
    otk_inspector_name = models.CharField('Контролёр ОТК', max_length=255, blank=True)
    otk_checked_at = models.DateTimeField('Дата проверки ОТК', null=True, blank=True)
    otk_status = models.CharField('Статус ОТК (снимок)', max_length=20, blank=True)

    class Meta:
        db_table = 'warehouse_batches'
        verbose_name = 'Партия ГП'
        verbose_name_plural = 'Партии ГП на складе'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.product} — {self.quantity} ({self.get_status_display()})'
