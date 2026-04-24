from django.conf import settings
from django.db import models


class Client(models.Model):
    CREDIT_MODE_SOFT = 'soft'
    CREDIT_MODE_HARD = 'hard'
    CREDIT_MODE_CHOICES = [
        (CREDIT_MODE_SOFT, 'Мягкое предупреждение'),
        (CREDIT_MODE_HARD, 'Жёсткая блокировка'),
    ]

    name = models.CharField('Название', max_length=255)
    contact = models.CharField('Контакт', max_length=255, blank=True)
    phone = models.CharField('Телефон', max_length=50, blank=True)
    phone_alt = models.CharField('Доп. телефон', max_length=50, blank=True, default='')
    inn = models.CharField('ИНН', max_length=20, blank=True)
    address = models.TextField('Адрес', blank=True)
    client_type = models.CharField('Тип клиента', max_length=100, blank=True, default='')
    notes = models.TextField('Комментарий', blank=True, default='')
    email = models.EmailField('Email', blank=True, default='')
    messenger = models.CharField(
        'Мессенджер / WhatsApp / Telegram',
        max_length=255,
        blank=True,
        default='',
    )
    is_active = models.BooleanField('Активен', default=True)
    credit_limit = models.DecimalField(
        'Кредитный лимит', max_digits=16, decimal_places=2, null=True, blank=True,
    )
    credit_limit_mode = models.CharField(
        'Режим кредитного лимита',
        max_length=10,
        choices=CREDIT_MODE_CHOICES,
        default=CREDIT_MODE_SOFT,
    )

    class Meta:
        db_table = 'clients'
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────────────────────────────────────
# ЗАЯВКА (Order)
# ─────────────────────────────────────────────────────────────────────────────

class Order(models.Model):
    """Заявка клиента — намерение, не перемещение склада."""

    SOURCE_CASHIER = 'cashier'
    SOURCE_MANAGER = 'manager'
    SOURCE_BOSS = 'boss'
    SOURCE_OTHER = 'other'
    SOURCE_CHOICES = [
        (SOURCE_CASHIER, 'Кассир'),
        (SOURCE_MANAGER, 'Менеджер'),
        (SOURCE_BOSS, 'Руководитель'),
        (SOURCE_OTHER, 'Другое'),
    ]

    STATUS_NEW = 'new'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_PARTIALLY_SHIPPED = 'partially_shipped'
    STATUS_SHIPPED = 'shipped'
    STATUS_CLOSED = 'closed'
    STATUS_CANCELED = 'canceled'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новая'),
        (STATUS_CONFIRMED, 'Подтверждена'),
        (STATUS_IN_PROGRESS, 'В работе'),
        (STATUS_PARTIALLY_SHIPPED, 'Частично отгружена'),
        (STATUS_SHIPPED, 'Отгружена'),
        (STATUS_CLOSED, 'Закрыта'),
        (STATUS_CANCELED, 'Отменена'),
    ]

    order_number = models.CharField('Номер заявки', max_length=100, unique=True)
    date = models.DateField('Дата')
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name='orders', null=True, blank=True,
    )
    source_type = models.CharField(
        'Тип источника заявки', max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANAGER,
    )
    comment = models.TextField('Комментарий', blank=True, default='')
    status = models.CharField(
        'Статус', max_length=25, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_orders',
        verbose_name='Создал',
    )
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='responsible_orders',
        verbose_name='Ответственный',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        db_table = 'client_orders'
        verbose_name = 'Заявка'
        verbose_name_plural = 'Заявки'
        ordering = ['-date', '-id']

    def __str__(self):
        c = self.client.name if self.client_id else '—'
        return f'{self.order_number} — {c}'

    @property
    def total_amount(self):
        from decimal import Decimal
        return sum((line.line_total or Decimal('0')) for line in self.lines.all())

    @property
    def shipped_amount(self):
        from decimal import Decimal
        return sum((line.shipped_quantity or Decimal('0')) * (line.unit_price or Decimal('0'))
                   for line in self.lines.all())

    @property
    def remaining_amount(self):
        from decimal import Decimal
        return sum((line.remaining_quantity or Decimal('0')) * (line.unit_price or Decimal('0'))
                   for line in self.lines.all())

    @property
    def has_company_debt_by_goods(self):
        return any(
            (line.remaining_quantity or 0) > 0
            for line in self.lines.all()
        )


class OrderLine(models.Model):
    """Строка заявки."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    product = models.CharField('Товар / профиль / наименование', max_length=255)
    product_type = models.CharField('Тип товара', max_length=100, blank=True, default='')
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='order_lines',
        verbose_name='Профиль',
    )
    ordered_quantity = models.DecimalField(
        'Заказано', max_digits=14, decimal_places=4, default=0,
    )
    shipped_quantity = models.DecimalField(
        'Отгружено', max_digits=14, decimal_places=4, default=0,
    )
    reserved_quantity = models.DecimalField(
        'Зарезервировано', max_digits=14, decimal_places=4, default=0,
    )
    unit_price = models.DecimalField(
        'Цена за ед.', max_digits=14, decimal_places=2, null=True, blank=True,
    )
    comment = models.TextField('Комментарий строки', blank=True, default='')

    class Meta:
        db_table = 'order_lines'
        verbose_name = 'Строка заявки'
        verbose_name_plural = 'Строки заявок'
        ordering = ['id']

    @property
    def remaining_quantity(self):
        from decimal import Decimal
        return max(Decimal('0'), (self.ordered_quantity or Decimal('0')) - (self.shipped_quantity or Decimal('0')))

    @property
    def available_to_ship(self):
        """Можно отгрузить = заказано - отгружено - зарезервировано (сверх отгруженного)."""
        from decimal import Decimal
        remaining = self.remaining_quantity
        reserved = max(Decimal('0'), (self.reserved_quantity or Decimal('0')) - (self.shipped_quantity or Decimal('0')))
        return max(Decimal('0'), remaining - reserved)

    @property
    def remaining_to_reserve(self):
        """Ещё можно зарезервировать = заказано - зарезервировано."""
        from decimal import Decimal
        return max(
            Decimal('0'),
            (self.ordered_quantity or Decimal('0')) - (self.reserved_quantity or Decimal('0')),
        )

    @property
    def line_total(self):
        from decimal import Decimal
        qty = self.ordered_quantity or Decimal('0')
        price = self.unit_price or Decimal('0')
        return (qty * price).quantize(Decimal('0.01'))

    def __str__(self):
        return f'{self.product} × {self.ordered_quantity}'


# ─────────────────────────────────────────────────────────────────────────────
# ПРОДАЖА (Sale) — расширенная, обратно совместимая
# ─────────────────────────────────────────────────────────────────────────────

class Sale(models.Model):
    MODE_PIECES = 'pieces'
    MODE_PACKAGES = 'packages'
    SALE_MODE_CHOICES = [
        (MODE_PIECES, 'По штукам'),
        (MODE_PACKAGES, 'По упаковкам'),
    ]

    # Новые статусы
    STATUS_DRAFT = 'draft'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_PARTIALLY_SHIPPED = 'partially_shipped'
    STATUS_SHIPPED = 'shipped'
    STATUS_CLOSED = 'closed'
    STATUS_CANCELED = 'canceled'
    SALE_STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_CONFIRMED, 'Подтверждена'),
        (STATUS_PARTIALLY_SHIPPED, 'Частично отгружена'),
        (STATUS_SHIPPED, 'Отгружена'),
        (STATUS_CLOSED, 'Закрыта'),
        (STATUS_CANCELED, 'Отменена'),
    ]

    order_number = models.CharField('Номер заказа', max_length=100)
    sale_number = models.CharField('Номер продажи', max_length=100, blank=True, default='')
    invoice_number = models.CharField('Номер накладной', max_length=100, blank=True, default='')
    receipt_number = models.CharField('Номер квитанции', max_length=100, blank=True, default='')
    sale_status = models.CharField(
        'Статус продажи',
        max_length=25,
        choices=SALE_STATUS_CHOICES,
        default=STATUS_CLOSED,
        db_index=True,
    )
    linked_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sales',
        verbose_name='Заявка',
    )
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
    stock_quality = models.CharField(
        'Качество склада на момент продажи',
        max_length=10,
        blank=True,
        default='',
    )
    is_defect_sale = models.BooleanField('Продажа брака', default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_sales',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True, null=True, blank=True)

    class Meta:
        db_table = 'sales'
        verbose_name = 'Продажа'
        verbose_name_plural = 'Продажи'
        ordering = ['-date', '-id']

    def __str__(self):
        c = self.client.name if self.client_id else '—'
        return f'{self.order_number} — {c}'


class SaleLine(models.Model):
    """Строка многострочной продажи (новый формат)."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='sale_lines')
    order_line = models.ForeignKey(
        OrderLine,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sale_lines',
        verbose_name='Строка заявки',
    )
    product = models.CharField('Товар / наименование', max_length=255)
    warehouse_batch = models.ForeignKey(
        'warehouse.WarehouseBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sale_lines',
        verbose_name='Партия склада ГП',
    )
    stock_form = models.CharField('Форма учёта', max_length=20, blank=True, default='')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4, default=0)
    unit_price = models.DecimalField('Цена за ед.', max_digits=14, decimal_places=2, null=True, blank=True)
    line_total = models.DecimalField('Сумма строки', max_digits=16, decimal_places=2, default=0)
    cost = models.DecimalField('Себестоимость строки', max_digits=16, decimal_places=2, default=0)
    profit = models.DecimalField('Прибыль строки', max_digits=16, decimal_places=2, default=0)
    defect_flag = models.BooleanField('Строка брака', default=False)
    comment = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'sale_lines'
        verbose_name = 'Строка продажи'
        verbose_name_plural = 'Строки продаж'
        ordering = ['id']

    def __str__(self):
        return f'{self.product} × {self.quantity}'


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


# ─────────────────────────────────────────────────────────────────────────────
# ОПЛАТА (Payment)
# ─────────────────────────────────────────────────────────────────────────────

class Payment(models.Model):
    """Денежное движение по клиенту. Деньги и товар живут отдельно."""

    TYPE_PREPAYMENT = 'prepayment'
    TYPE_PAYMENT = 'payment'
    TYPE_SURCHARGE = 'surcharge'
    TYPE_REFUND = 'refund'
    TYPE_CHOICES = [
        (TYPE_PREPAYMENT, 'Предоплата'),
        (TYPE_PAYMENT, 'Оплата'),
        (TYPE_SURCHARGE, 'Доплата'),
        (TYPE_REFUND, 'Возврат денег'),
    ]

    METHOD_CASH = 'cash'
    METHOD_TRANSFER = 'transfer'
    METHOD_CARD = 'card'
    METHOD_OTHER = 'other'
    METHOD_CHOICES = [
        (METHOD_CASH, 'Наличные'),
        (METHOD_TRANSFER, 'Перевод'),
        (METHOD_CARD, 'Карта'),
        (METHOD_OTHER, 'Другое'),
    ]

    payment_number = models.CharField('Номер оплаты / квитанции', max_length=100, blank=True, default='')
    date = models.DateField('Дата')
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name='payments', null=True, blank=True,
    )
    linked_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='payments',
        verbose_name='Заявка',
    )
    linked_sale = models.ForeignKey(
        Sale,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='payments',
        verbose_name='Продажа',
    )
    payment_type = models.CharField(
        'Тип оплаты', max_length=20, choices=TYPE_CHOICES, default=TYPE_PAYMENT,
    )
    amount = models.DecimalField('Сумма', max_digits=16, decimal_places=2, default=0)
    payment_method = models.CharField(
        'Способ оплаты', max_length=20, choices=METHOD_CHOICES, default=METHOD_CASH,
    )
    comment = models.TextField('Комментарий', blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_payments',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'payments'
        verbose_name = 'Оплата'
        verbose_name_plural = 'Оплаты'
        ordering = ['-date', '-id']

    def __str__(self):
        c = self.client.name if self.client_id else '—'
        return f'{self.get_payment_type_display()} {self.amount} — {c}'


# ─────────────────────────────────────────────────────────────────────────────
# ВОЗВРАТ (Return)
# ─────────────────────────────────────────────────────────────────────────────

class Return(models.Model):
    """Возврат товара от клиента. Всегда привязан к продаже."""

    return_number = models.CharField('Номер возврата', max_length=100, blank=True, default='')
    date = models.DateField('Дата')
    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, related_name='returns', verbose_name='Продажа',
    )
    linked_order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='returns',
        verbose_name='Заявка',
    )
    invoice_number = models.CharField('Накладная', max_length=100, blank=True, default='')
    return_reason = models.TextField('Причина возврата', blank=True, default='')
    comment = models.TextField('Комментарий', blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_returns',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'returns'
        verbose_name = 'Возврат'
        verbose_name_plural = 'Возвраты'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'Возврат #{self.return_number or self.id} к продаже #{self.sale_id}'


class ReturnLine(models.Model):
    """Строка возврата."""

    TARGET_WAREHOUSE = 'warehouse'
    TARGET_DEFECT = 'defect'
    TARGET_REWORK = 'rework'
    TARGET_CHOICES = [
        (TARGET_WAREHOUSE, 'На склад ГП'),
        (TARGET_DEFECT, 'В брак'),
        (TARGET_REWORK, 'На переделку'),
    ]

    CONDITION_GOOD = 'good'
    CONDITION_DAMAGED = 'damaged'
    CONDITION_DEFECT = 'defect'
    CONDITION_CHOICES = [
        (CONDITION_GOOD, 'Хорошее'),
        (CONDITION_DAMAGED, 'Повреждено'),
        (CONDITION_DEFECT, 'Брак'),
    ]

    return_doc = models.ForeignKey(Return, on_delete=models.CASCADE, related_name='lines')
    sale_line = models.ForeignKey(
        SaleLine,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='return_lines',
        verbose_name='Строка продажи',
    )
    product = models.CharField('Товар', max_length=255, blank=True, default='')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4, default=0)
    return_target = models.CharField(
        'Назначение возврата', max_length=20, choices=TARGET_CHOICES, default=TARGET_WAREHOUSE,
    )
    condition_type = models.CharField(
        'Состояние', max_length=20, choices=CONDITION_CHOICES, default=CONDITION_GOOD,
    )
    comment = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'return_lines'
        verbose_name = 'Строка возврата'
        verbose_name_plural = 'Строки возвратов'
        ordering = ['id']

    def __str__(self):
        return f'{self.product} × {self.quantity} → {self.get_return_target_display()}'


# ─────────────────────────────────────────────────────────────────────────────
# БРАК (DefectRecord)
# ─────────────────────────────────────────────────────────────────────────────

class DefectRecord(models.Model):
    """Учётная единица брака. Источник — ОТК или возврат клиента."""

    SOURCE_OTK = 'otk'
    SOURCE_RETURN = 'return'
    SOURCE_CHOICES = [
        (SOURCE_OTK, 'ОТК'),
        (SOURCE_RETURN, 'Возврат клиента'),
    ]

    STATUS_NEW = 'new'
    STATUS_ON_STOCK = 'on_stock'
    STATUS_SENT_TO_REWORK = 'sent_to_rework'
    STATUS_REWORKED = 'reworked'
    STATUS_SOLD = 'sold'
    STATUS_WRITTEN_OFF = 'written_off'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новый'),
        (STATUS_ON_STOCK, 'На складе брака'),
        (STATUS_SENT_TO_REWORK, 'Передан на переработку'),
        (STATUS_REWORKED, 'Переработан'),
        (STATUS_SOLD, 'Продан'),
        (STATUS_WRITTEN_OFF, 'Списан'),
    ]

    source_type = models.CharField(
        'Источник', max_length=20, choices=SOURCE_CHOICES, default=SOURCE_OTK,
    )
    source_id = models.IntegerField('ID источника (otk_check или return_line)', null=True, blank=True)
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='defect_records',
        verbose_name='Профиль',
    )
    product = models.CharField('Продукт / наименование', max_length=255, blank=True, default='')
    quantity_pcs = models.DecimalField(
        'Количество шт/м', max_digits=14, decimal_places=4, default=0,
    )
    quantity_kg = models.DecimalField(
        'Количество кг', max_digits=14, decimal_places=4, null=True, blank=True,
    )
    kg_coefficient = models.DecimalField(
        'Коэффициент кг/ед.', max_digits=14, decimal_places=6, null=True, blank=True,
    )
    defect_reason = models.TextField('Причина брака', blank=True, default='')
    status = models.CharField(
        'Статус', max_length=25, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True,
    )
    writeoff_reason = models.TextField('Причина списания', blank=True, default='')
    comment = models.TextField('Комментарий', blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_defects',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        db_table = 'defect_records'
        verbose_name = 'Запись брака'
        verbose_name_plural = 'Записи брака'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Брак #{self.id} — {self.product} ({self.get_status_display()})'


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕДЕЛКА (ReworkRequest)
# ─────────────────────────────────────────────────────────────────────────────

class ReworkRequest(models.Model):
    """Запрос на переделку/перевыпуск по возврату клиента."""

    STATUS_PENDING = 'pending'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELED = 'canceled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_IN_PROGRESS, 'В работе'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_CANCELED, 'Отменено'),
    ]

    rework_number = models.CharField('Номер переделки', max_length=100, blank=True, default='')
    return_doc = models.ForeignKey(
        Return,
        on_delete=models.PROTECT,
        related_name='rework_requests',
        verbose_name='Возврат',
    )
    defect_record = models.ForeignKey(
        DefectRecord,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rework_requests',
        verbose_name='Запись брака',
    )
    original_sale = models.ForeignKey(
        Sale,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rework_requests',
        verbose_name='Исходная продажа',
    )
    product = models.CharField('Продукт', max_length=255, blank=True, default='')
    quantity_kg = models.DecimalField('Масса входа кг (сырьё)', max_digits=14, decimal_places=4, default=0)
    output_quantity_kg = models.DecimalField(
        'Масса выхода кг (ГП)', max_digits=14, decimal_places=4, null=True, blank=True,
    )
    loss_kg = models.DecimalField(
        'Потери кг', max_digits=14, decimal_places=4, null=True, blank=True,
    )
    conversion_rate = models.DecimalField(
        'Коэффициент переработки (выход/вход)', max_digits=10, decimal_places=6, null=True, blank=True,
    )
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True,
    )
    result_warehouse_batch = models.ForeignKey(
        'warehouse.WarehouseBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rework_requests',
        verbose_name='Партия ГП после переделки',
    )
    comment = models.TextField('Комментарий', blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_rework_requests',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        db_table = 'rework_requests'
        verbose_name = 'Переделка'
        verbose_name_plural = 'Переделки'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Переделка #{self.rework_number or self.id} — {self.product}'

    @property
    def rework_loss_kg(self):
        from decimal import Decimal
        if self.loss_kg is not None:
            return self.loss_kg
        if self.quantity_kg and self.output_quantity_kg is not None:
            return max(Decimal('0'), Decimal(str(self.quantity_kg)) - Decimal(str(self.output_quantity_kg)))
        return None

    @property
    def recovered_output(self):
        return self.output_quantity_kg


# ─────────────────────────────────────────────────────────────────────────────
# ПРАЙС-ЛИСТ (PriceList / ProductPrice / ClientPrice)
# ─────────────────────────────────────────────────────────────────────────────

class PriceList(models.Model):
    """Базовый прайс-лист для товаров/профилей."""
    name = models.CharField('Название прайса', max_length=255)
    is_active = models.BooleanField('Активен', default=True)
    valid_from = models.DateField('Действует с', null=True, blank=True)
    valid_to = models.DateField('Действует по', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True, default='')
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'price_lists'
        verbose_name = 'Прайс-лист'
        verbose_name_plural = 'Прайс-листы'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class ProductPrice(models.Model):
    """Цена по прайс-листу для товара/профиля."""
    price_list = models.ForeignKey(
        PriceList, on_delete=models.CASCADE, related_name='product_prices', verbose_name='Прайс',
    )
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='product_prices',
        verbose_name='Профиль',
    )
    product = models.CharField('Товар (текст)', max_length=255, blank=True, default='')
    price = models.DecimalField('Цена', max_digits=14, decimal_places=2)
    unit = models.CharField('Единица', max_length=20, default='piece')

    class Meta:
        db_table = 'product_prices'
        verbose_name = 'Цена по прайсу'
        verbose_name_plural = 'Цены по прайсу'
        ordering = ['id']

    def __str__(self):
        label = self.profile.name if self.profile_id else self.product
        return f'{label} — {self.price}'


class ClientPrice(models.Model):
    """Индивидуальная цена клиента (приоритет выше базового прайса)."""
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name='client_prices', verbose_name='Клиент',
    )
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='client_prices',
        verbose_name='Профиль',
    )
    product = models.CharField('Товар (текст)', max_length=255, blank=True, default='')
    price = models.DecimalField('Цена', max_digits=14, decimal_places=2)
    unit = models.CharField('Единица', max_length=20, default='piece')
    valid_from = models.DateField('Действует с', null=True, blank=True)
    valid_to = models.DateField('Действует по', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True, default='')
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'client_prices'
        verbose_name = 'Индивидуальная цена клиента'
        verbose_name_plural = 'Индивидуальные цены клиентов'
        ordering = ['-created_at']

    def __str__(self):
        label = self.profile.name if self.profile_id else self.product
        return f'{self.client.name} — {label} — {self.price}'


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗЕРВ ПО ЗАЯВКЕ (OrderReservation)
# ─────────────────────────────────────────────────────────────────────────────

class OrderReservation(models.Model):
    """Резерв конкретной партии склада под строку заявки."""

    STATUS_ACTIVE = 'active'
    STATUS_RELEASED = 'released'
    STATUS_FULFILLED = 'fulfilled'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Активен'),
        (STATUS_RELEASED, 'Снят'),
        (STATUS_FULFILLED, 'Исполнен (отгружен)'),
    ]

    order_line = models.ForeignKey(
        OrderLine,
        on_delete=models.CASCADE,
        related_name='reservations',
        verbose_name='Строка заявки',
    )
    warehouse_batch = models.ForeignKey(
        'warehouse.WarehouseBatch',
        on_delete=models.CASCADE,
        related_name='order_reservations',
        verbose_name='Партия склада',
    )
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4, default=0)
    status = models.CharField(
        'Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_reservations',
        verbose_name='Создал',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    comment = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'order_reservations'
        verbose_name = 'Резерв по заявке'
        verbose_name_plural = 'Резервы по заявкам'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_line', 'status']),
            models.Index(fields=['warehouse_batch', 'status']),
        ]

    def __str__(self):
        return f'Резерв #{self.id}: строка {self.order_line_id} ← партия {self.warehouse_batch_id} × {self.quantity}'
