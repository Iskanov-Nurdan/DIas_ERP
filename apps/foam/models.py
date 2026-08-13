from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .constants import (
    OPERATION_KIND_CHOICES,
    OUTPUT_FORMAT_CHOICES,
    PAYMENT_STATUS_CHOICES,
)


class FoamDensityGrade(models.Model):
    """Справочник плотностей (грейдов) — редактируемый пользователем, не жёсткий enum."""

    code = models.CharField('Код', max_length=20, unique=True)
    min_kg_m3 = models.DecimalField('Плотность мин, кг/м³', max_digits=8, decimal_places=2)
    max_kg_m3 = models.DecimalField('Плотность макс, кг/м³', max_digits=8, decimal_places=2)

    class Meta:
        db_table = 'foam_density_grades'
        verbose_name = 'Плотность (грейд)'
        verbose_name_plural = 'Справочник плотностей'
        ordering = ['code']

    def clean(self):
        if self.min_kg_m3 is not None and self.max_kg_m3 is not None and self.max_kg_m3 < self.min_kg_m3:
            raise ValidationError({'max_kg_m3': 'Должно быть ≥ min_kg_m3'})

    def __str__(self):
        return self.code


class FoamRawLot(models.Model):
    """Лот сырья (биг-бэг гранул) — свой склад, без общего каталога сырья."""

    lot_number = models.CharField('Номер лота', max_length=50, unique=True)
    material_name = models.CharField('Материал', max_length=255)
    supplier = models.CharField('Поставщик', max_length=255, blank=True, default='')
    bag_weight_kg = models.DecimalField('Вес мешка, кг', max_digits=12, decimal_places=1)
    received_kg = models.DecimalField('Приход, кг', max_digits=12, decimal_places=1)
    remaining_kg = models.DecimalField('Остаток, кг', max_digits=12, decimal_places=1)
    received_at = models.DateTimeField('Дата прихода', auto_now_add=True)

    class Meta:
        db_table = 'foam_raw_lots'
        verbose_name = 'Лот сырья (пенопласт)'
        verbose_name_plural = 'Лоты сырья (пенопласт)'
        ordering = ['-received_at', '-id']

    def clean(self):
        if self.remaining_kg is not None and self.remaining_kg < 0:
            raise ValidationError({'remaining_kg': 'Остаток не может быть < 0'})

    def __str__(self):
        return f'{self.lot_number} — {self.material_name}'


class FoamProductionRun(models.Model):
    """Выпуск производства: списывает input_kg с лота, пополняет склад ГП."""

    lot = models.ForeignKey(FoamRawLot, on_delete=models.PROTECT, related_name='production_runs')
    grade = models.ForeignKey(
        FoamDensityGrade, on_delete=models.PROTECT, related_name='production_runs', null=True, blank=True
    )
    input_kg = models.DecimalField('Расход сырья, кг', max_digits=12, decimal_places=1)
    output_format = models.CharField('Формат выхода', max_length=20, choices=OUTPUT_FORMAT_CHOICES)
    output_qty = models.DecimalField('Выход', max_digits=12, decimal_places=1)
    produced_at = models.DateTimeField('Дата выпуска', auto_now_add=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='foam_production_runs'
    )

    class Meta:
        db_table = 'foam_production_runs'
        verbose_name = 'Выпуск производства (пенопласт)'
        verbose_name_plural = 'Выпуски производства (пенопласт)'
        ordering = ['-produced_at', '-id']

    def __str__(self):
        return f'#{self.pk} — {self.lot.lot_number} → {self.output_qty} ({self.output_format})'


class FoamGpStock(models.Model):
    """Остаток склада ГП по варианту товара (output_format + grade + thickness_cm)."""

    output_format = models.CharField('Формат', max_length=20, choices=OUTPUT_FORMAT_CHOICES)
    grade = models.ForeignKey(
        FoamDensityGrade, on_delete=models.PROTECT, related_name='gp_stock_rows', null=True, blank=True
    )
    thickness_cm = models.PositiveSmallIntegerField('Толщина, см', null=True, blank=True)
    qty = models.DecimalField('Остаток', max_digits=12, decimal_places=1, default=0)

    class Meta:
        db_table = 'foam_gp_stock'
        verbose_name = 'Остаток склада ГП (пенопласт)'
        verbose_name_plural = 'Остатки склада ГП (пенопласт)'
        ordering = ['output_format', 'grade_id', 'thickness_cm']

    def clean(self):
        if self.qty is not None and self.qty < 0:
            raise ValidationError({'qty': 'Остаток не может быть < 0'})

    def __str__(self):
        return f'{self.output_format} / {self.grade_id} / {self.thickness_cm} = {self.qty}'


class FoamGpOperation(models.Model):
    """Движение склада ГП (журнал, со знаком в qty)."""

    kind = models.CharField('Тип операции', max_length=20, choices=OPERATION_KIND_CHOICES)
    output_format = models.CharField('Формат', max_length=20, choices=OUTPUT_FORMAT_CHOICES)
    grade = models.ForeignKey(
        FoamDensityGrade, on_delete=models.SET_NULL, related_name='gp_operations', null=True, blank=True
    )
    thickness_cm = models.PositiveSmallIntegerField('Толщина, см', null=True, blank=True)
    qty = models.DecimalField('Кол-во (со знаком)', max_digits=12, decimal_places=1)
    created_at = models.DateTimeField('Когда', auto_now_add=True)
    ref = models.CharField('Ссылка на документ', max_length=100, blank=True, default='')

    class Meta:
        db_table = 'foam_gp_operations'
        verbose_name = 'Движение склада ГП (пенопласт)'
        verbose_name_plural = 'Движения склада ГП (пенопласт)'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.kind}: {self.qty} ({self.output_format})'


class FoamSale(models.Model):
    """Продажа готовой продукции (клиент — свободный текст, без общего справочника clients/)."""

    client = models.CharField('Клиент', max_length=255)
    sale_date = models.DateField('Дата продажи')
    total_amount = models.DecimalField('Сумма', max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField('Оплачено', max_digits=14, decimal_places=2, default=0)
    payment_status = models.CharField('Статус оплаты', max_length=20, choices=PAYMENT_STATUS_CHOICES)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'foam_sales'
        verbose_name = 'Продажа (пенопласт)'
        verbose_name_plural = 'Продажи (пенопласт)'
        ordering = ['-sale_date', '-id']

    @property
    def debt_amount(self):
        return self.total_amount - self.paid_amount

    def __str__(self):
        return f'#{self.pk} — {self.client} ({self.total_amount})'


class FoamSaleLine(models.Model):
    sale = models.ForeignKey(FoamSale, on_delete=models.CASCADE, related_name='lines')
    stock = models.ForeignKey(FoamGpStock, on_delete=models.PROTECT, related_name='sale_lines')
    qty = models.DecimalField('Кол-во', max_digits=12, decimal_places=1)
    unit_price = models.DecimalField('Цена за ед.', max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'foam_sale_lines'
        verbose_name = 'Строка продажи (пенопласт)'
        verbose_name_plural = 'Строки продаж (пенопласт)'

    @property
    def line_total(self):
        return self.qty * self.unit_price

    def __str__(self):
        return f'{self.sale_id}: {self.stock_id} × {self.qty}'
