from django.db import models


class Client(models.Model):
    name = models.CharField('Название', max_length=255)
    contact = models.CharField('Контакт', max_length=255, blank=True)
    phone = models.CharField('Телефон', max_length=50, blank=True)
    phone_alt = models.CharField('Доп. телефон', max_length=50, blank=True, default='')
    inn = models.CharField('ИНН', max_length=20, blank=True)
    address = models.TextField('Адрес', blank=True)
    client_type = models.CharField('Тип клиента', max_length=100, blank=True, default='')
    notes = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'clients'
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'

    def __str__(self):
        return self.name


class Sale(models.Model):
    MODE_PIECES = 'pieces'
    MODE_PACKAGES = 'packages'
    SALE_MODE_CHOICES = [
        (MODE_PIECES, 'По штукам'),
        (MODE_PACKAGES, 'По упаковкам'),
    ]

    order_number = models.CharField('Номер заказа', max_length=100)
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name='sales', null=True, blank=True,
    )
    warehouse_batch = models.ForeignKey(
        'warehouse.WarehouseBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales',
        verbose_name='Партия склада ГП',
    )
    product = models.CharField('Продукт', max_length=255)
    sale_mode = models.CharField(
        'Режим продажи',
        max_length=12,
        choices=SALE_MODE_CHOICES,
        default=MODE_PIECES,
    )
    sold_pieces = models.DecimalField('Продано шт', max_digits=14, decimal_places=4, default=0)
    sold_packages = models.DecimalField('Продано упаковок', max_digits=14, decimal_places=4, default=0)
    length_per_piece = models.DecimalField('Длина штуки, м', max_digits=14, decimal_places=4, null=True, blank=True)
    total_meters = models.DecimalField('Всего м', max_digits=16, decimal_places=4, default=0)
    quantity = models.DecimalField('Количество (legacy = sold_pieces)', max_digits=14, decimal_places=4)
    quantity_input = models.DecimalField(
        'Ввод количества (упаковки при продаже в упаковках)',
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )
    price = models.DecimalField('Цена за единицу сделки', max_digits=14, decimal_places=2, null=True, blank=True)
    revenue = models.DecimalField('Выручка', max_digits=16, decimal_places=2, default=0)
    cost = models.DecimalField('Себестоимость', max_digits=16, decimal_places=2, default=0)
    date = models.DateField('Дата')
    comment = models.TextField('Комментарий', blank=True)
    profit = models.DecimalField('Прибыль', max_digits=14, decimal_places=2, default=0)
    sale_unit = models.CharField('Единица продажи', max_length=50, blank=True)
    packaging = models.CharField('Упаковка (packed/unpacked и т.п.)', max_length=50, blank=True)
    stock_form = models.CharField(
        'Форма учёта склада на момент продажи',
        max_length=20,
        blank=True,
    )
    piece_pick = models.CharField(
        'Источник штук при продаже',
        max_length=40,
        blank=True,
    )

    class Meta:
        db_table = 'sales'
        verbose_name = 'Продажа'
        verbose_name_plural = 'Продажи'
        ordering = ['-date', '-id']

    def __str__(self):
        c = self.client.name if self.client_id else '—'
        return f'{self.order_number} — {c}'


class Shipment(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SHIPPED = 'shipped'
    STATUS_DELIVERED = 'delivered'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'К отгрузке'),
        (STATUS_SHIPPED, 'Отгружено'),
        (STATUS_DELIVERED, 'Доставлено'),
    ]

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name='shipments')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    shipment_date = models.DateField('Дата отгрузки', null=True, blank=True)
    delivery_date = models.DateField('Дата доставки', null=True, blank=True)
    address = models.TextField('Адрес доставки', blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        db_table = 'shipments'
        verbose_name = 'Отгрузка'
        verbose_name_plural = 'Отгрузки'
        ordering = ['-id']

    def __str__(self):
        return f'Отгрузка #{self.id} — {self.get_status_display()}'
