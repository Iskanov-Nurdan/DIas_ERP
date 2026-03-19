from django.db import models
from django.conf import settings


class Line(models.Model):
    name = models.CharField('Название', max_length=255)

    class Meta:
        db_table = 'lines'
        verbose_name = 'Линия'
        verbose_name_plural = 'Линии'

    def __str__(self):
        return self.name


class LineHistory(models.Model):
    ACTION_OPEN = 'open'
    ACTION_CLOSE = 'close'
    ACTION_CHOICES = [(ACTION_OPEN, 'Открыта'), (ACTION_CLOSE, 'Закрыта')]

    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name='history')
    action = models.CharField('Действие', max_length=10, choices=ACTION_CHOICES)
    date = models.DateField('Дата')
    time = models.TimeField('Время')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='line_actions')

    class Meta:
        db_table = 'line_history'
        verbose_name = 'История смены'
        verbose_name_plural = 'История смен'
        ordering = ['-date', '-time']

    def __str__(self):
        return f'{self.line.name} — {self.get_action_display()} ({self.date})'


class Order(models.Model):
    STATUS_CREATED = 'created'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'
    STATUS_CHOICES = [
        (STATUS_CREATED, 'Создан'),
        (STATUS_IN_PROGRESS, 'В работе'),
        (STATUS_DONE, 'Готово'),
    ]

    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default=STATUS_CREATED)
    recipe = models.ForeignKey('recipes.Recipe', on_delete=models.PROTECT, related_name='orders')
    line = models.ForeignKey(Line, on_delete=models.PROTECT, related_name='orders')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    product = models.CharField('Продукт', max_length=255)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    date = models.DateField('Дата')

    class Meta:
        db_table = 'orders'
        verbose_name = 'Заказ на производство'
        verbose_name_plural = 'Заказы'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.product} — {self.quantity} ({self.get_status_display()})'


class ProductionBatch(models.Model):
    OTK_PENDING = 'pending'
    OTK_ACCEPTED = 'accepted'
    OTK_REJECTED = 'rejected'
    OTK_STATUS_CHOICES = [
        (OTK_PENDING, 'Ожидает ОТК'),
        (OTK_ACCEPTED, 'Принято'),
        (OTK_REJECTED, 'Брак'),
    ]

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='batches')
    product = models.CharField('Продукт', max_length=255)
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='production_batches')
    date = models.DateField('Дата')
    otk_status = models.CharField('Статус ОТК', max_length=20, choices=OTK_STATUS_CHOICES, default=OTK_PENDING)
    cost_price = models.DecimalField('Себестоимость', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'production_batches'
        verbose_name = 'Партия производства'
        verbose_name_plural = 'Партии производства'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.product} — {self.quantity} ({self.get_otk_status_display()})'


class Shift(models.Model):
    STATUS_OPEN = 'open'
    STATUS_CLOSED = 'closed'

    line = models.ForeignKey(Line, on_delete=models.SET_NULL, null=True, blank=True, related_name='shifts', verbose_name='Линия')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='shifts',
        verbose_name='Сотрудник',
    )
    opened_at = models.DateTimeField('Начало смены')
    closed_at = models.DateTimeField('Конец смены', null=True, blank=True)
    comment = models.TextField('Итоговый комментарий', blank=True)

    class Meta:
        db_table = 'shifts'
        verbose_name = 'Смена'
        verbose_name_plural = 'Смены'
        ordering = ['-opened_at']
        indexes = [
            models.Index(fields=['user', '-opened_at']),
            models.Index(fields=['-opened_at']),
        ]

    @property
    def status(self):
        return self.STATUS_CLOSED if self.closed_at else self.STATUS_OPEN

    def __str__(self):
        user_name = self.user.name if self.user_id else '—'
        return f'{user_name} / {self.line.name} ({self.opened_at:%d.%m.%Y %H:%M})'


class ShiftNote(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='notes', verbose_name='Смена')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='shift_notes',
        verbose_name='Автор',
    )
    text = models.TextField('Заметка')
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        db_table = 'shift_notes'
        verbose_name = 'Заметка к смене'
        verbose_name_plural = 'Заметки к сменам'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заметка к смене #{self.shift_id}'
