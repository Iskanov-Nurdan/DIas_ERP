from django.conf import settings
from django.db import models


class WarehouseBatch(models.Model):
    STOCK_BUCKET_STANDARD = 'standard'
    STOCK_BUCKET_REWORKED = 'reworked'
    STOCK_BUCKET_CHOICES = [
        (STOCK_BUCKET_STANDARD, 'Основной склад ГП'),
        (STOCK_BUCKET_REWORKED, 'Переделанные'),
    ]

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

    QUALITY_GOOD = 'good'
    QUALITY_DEFECT = 'defect'
    QUALITY_CHOICES = [
        (QUALITY_GOOD, 'Годный'),
        (QUALITY_DEFECT, 'Брак'),
    ]

    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.PROTECT,
        related_name='warehouse_batches',
        null=True,
        blank=True,
        verbose_name='Профиль',
    )
    product = models.CharField('Продукт', max_length=255)
    length_per_piece = models.DecimalField('Длина штуки, м', max_digits=14, decimal_places=4, null=True, blank=True)
    total_meters = models.DecimalField('Всего м', max_digits=16, decimal_places=4, null=True, blank=True)
    quantity = models.DecimalField('Штук доступно', max_digits=14, decimal_places=4)
    cost_per_piece = models.DecimalField('Себестоимость шт', max_digits=16, decimal_places=4, default=0)
    cost_per_meter = models.DecimalField('Себестоимость м', max_digits=16, decimal_places=4, default=0)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    date = models.DateField('Дата')
    source_batch = models.ForeignKey(
        'production.ProductionBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_batches',
    )
    blank_production_run = models.ForeignKey(
        'workshop.BlankProductionRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_gp_acceptance_batches',
        verbose_name='Приёмка ГП (неупакованная строка по приёмке)',
    )
    workshop_blank = models.ForeignKey(
        'workshop.WorkshopBlank',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_batches',
        verbose_name='Заготовка (цех)',
    )
    otk_account_session = models.ForeignKey(
        'workshop.OtkAccountSession',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='warehouse_batches',
        verbose_name='Учёт ОТК',
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

    quality = models.CharField(
        'Качество партии',
        max_length=10,
        choices=QUALITY_CHOICES,
        default=QUALITY_GOOD,
        db_index=True,
    )
    stock_bucket = models.CharField(
        'Сегмент склада',
        max_length=20,
        choices=STOCK_BUCKET_CHOICES,
        default=STOCK_BUCKET_STANDARD,
        db_index=True,
    )
    defect_reason = models.TextField('Причина брака (строка партии)', blank=True, default='')

    class Meta:
        db_table = 'warehouse_batches'
        verbose_name = 'Партия ГП'
        verbose_name_plural = 'Партии ГП на складе'
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        from decimal import Decimal

        if self.quality == self.QUALITY_GOOD and (self.defect_reason or '').strip():
            self.defect_reason = ''

        if self.length_per_piece is not None and self.quantity is not None:
            self.total_meters = (Decimal(str(self.quantity)) * Decimal(str(self.length_per_piece))).quantize(
                Decimal('0.0001')
            )
        elif self.length_per_piece is None:
            self.total_meters = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product} — {self.quantity} шт ({self.get_status_display()})'


class GpPackOperation(models.Model):
    """Одна операция упаковки неупакованного ГП (по группе product + blank), с дочерними единицами и FIFO по приёмкам."""

    KIND_PALLET = 'pallet'
    KIND_BOX = 'box'
    KIND_OTHER = 'other'
    KIND_CHOICES = [
        (KIND_PALLET, 'Поддон'),
        (KIND_BOX, 'Короб'),
        (KIND_OTHER, 'Иное'),
    ]

    SPLIT_SINGLE = 'single'
    SPLIT_UNIFORM = 'uniform'
    SPLIT_CUSTOM = 'custom'
    SPLIT_CHOICES = [
        (SPLIT_SINGLE, 'Одна упаковка'),
        (SPLIT_UNIFORM, 'Одинаковые упаковки'),
        (SPLIT_CUSTOM, 'Разный состав'),
    ]

    product = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.PROTECT,
        related_name='gp_pack_operations',
        verbose_name='ГП (SKU)',
    )
    blank = models.ForeignKey(
        'workshop.WorkshopBlank',
        on_delete=models.PROTECT,
        related_name='gp_pack_operations',
        verbose_name='Заготовка',
    )
    kind = models.CharField('Тип', max_length=16, choices=KIND_CHOICES)
    label = models.CharField('Метка', max_length=255, blank=True, default='')
    split_mode = models.CharField('Режим разбиения', max_length=16, choices=SPLIT_CHOICES)
    total_pieces = models.PositiveIntegerField('Всего штук в операции')
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gp_pack_operations',
        verbose_name='Кто создал',
    )
    client_request_id = models.CharField(
        'Идемпотентность (клиент)',
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        default=None,
    )

    class Meta:
        db_table = 'warehouse_gp_pack_operations'
        verbose_name = 'Операция упаковки ГП'
        verbose_name_plural = 'Операции упаковки ГП'
        ordering = ('-created_at', '-pk')

    def __str__(self):
        return f'#{self.pk} {self.get_kind_display()} {self.total_pieces} шт'


class GpPackUnit(models.Model):
    """Одна физическая упаковка (короб / поддон / …)."""

    operation = models.ForeignKey(
        GpPackOperation,
        on_delete=models.CASCADE,
        related_name='units',
        verbose_name='Операция',
    )
    sequence = models.PositiveIntegerField('Порядок')
    pieces = models.PositiveIntegerField('Штук в упаковке')
    warehouse_batch = models.ForeignKey(
        'WarehouseBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gp_pack_units',
        verbose_name='Партия склада ГП (1 упаковка = 1 строка)',
    )

    class Meta:
        db_table = 'warehouse_gp_pack_units'
        verbose_name = 'Единица упаковки ГП'
        verbose_name_plural = 'Единицы упаковки ГП'
        ordering = ('operation_id', 'sequence', 'pk')
        constraints = [
            models.UniqueConstraint(fields=('operation', 'sequence'), name='uniq_gp_pack_unit_op_seq'),
        ]

    def __str__(self):
        return f'op={self.operation_id} seq={self.sequence} {self.pieces} шт'


class GpPackRunAllocation(models.Model):
    """Списание штук с конкретной строки приёмки (BlankProductionRun), FIFO."""

    operation = models.ForeignKey(
        GpPackOperation,
        on_delete=models.CASCADE,
        related_name='run_allocations',
        verbose_name='Операция',
    )
    blank_production_run = models.ForeignKey(
        'workshop.BlankProductionRun',
        on_delete=models.PROTECT,
        related_name='gp_pack_allocations',
        verbose_name='Партия (приёмка ГП)',
    )
    pieces = models.PositiveIntegerField('Штук')
    kg = models.DecimalField('Кг (снимок)', max_digits=14, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = 'warehouse_gp_pack_run_allocations'
        verbose_name = 'Списание упаковки с приёмки'
        verbose_name_plural = 'Списания упаковки с приёмок'
        indexes = [
            models.Index(fields=['blank_production_run']),
        ]

    def __str__(self):
        return f'run={self.blank_production_run_id} {self.pieces} шт'
