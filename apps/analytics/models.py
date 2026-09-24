from django.conf import settings
from django.db import models

PRODUCT_LINE_GENERAL = 'general'
PRODUCT_LINE_PROFILE = 'profile'
PRODUCT_LINE_FOAM = 'foam'
PRODUCT_LINE_CHOICES = [
    (PRODUCT_LINE_GENERAL, 'Общий'),
    (PRODUCT_LINE_PROFILE, 'Пластиковый профиль'),
    (PRODUCT_LINE_FOAM, 'Пенополистирол'),
]

KIND_EXPENSE = 'expense'
KIND_INCOME = 'income'
KIND_CHOICES = [
    (KIND_EXPENSE, 'Расход'),
    (KIND_INCOME, 'Приход'),
]


class AnalyticsExpenseCategory(models.Model):
    """Настраиваемая категория ручного расхода/прихода (аренда, коммуналка, прочий доход…)."""

    name = models.CharField('Название', max_length=120)
    kind = models.CharField('Тип', max_length=10, choices=KIND_CHOICES, default=KIND_EXPENSE)
    # Зарплата — отдельно считается в блоке «Люди» (зарплатный фонд).
    is_payroll = models.BooleanField('Это зарплата', default=False)
    is_active = models.BooleanField('Активна', default=True)
    sort_order = models.PositiveSmallIntegerField('Порядок', default=100)
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'analytics_expense_categories'
        ordering = ['kind', 'sort_order', 'name']
        verbose_name = 'Категория расхода'
        verbose_name_plural = 'Категории расходов'
        constraints = [
            models.UniqueConstraint(fields=['kind', 'name'], name='uniq_expense_category_kind_name'),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.get_kind_display()})'


class AnalyticsOtherExpense(models.Model):
    """
    Единый реестр ручных расходов и приходов (то, чего нет в основной системе:
    аренда, коммуналка, зарплата, налоги, прочий доход…).

    pending → accepted (в P&L по date) или reject (удаление). Регулярная запись
    (recurring=True) — шаблон: каждый месяц от неё создаётся pending-копия
    (recurring_source + period_key, уникально — не задваивается).
    """

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_ACCEPTED, 'Принят'),
    ]

    name = models.CharField('Наименование', max_length=255)
    amount = models.DecimalField('Сумма', max_digits=16, decimal_places=2)
    date = models.DateField('Дата расхода', db_index=True)
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    kind = models.CharField('Тип', max_length=10, choices=KIND_CHOICES, default=KIND_EXPENSE, db_index=True)
    category = models.ForeignKey(
        AnalyticsExpenseCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='entries',
        verbose_name='Категория',
    )
    product_line = models.CharField(
        'Товарная линия', max_length=10, choices=PRODUCT_LINE_CHOICES, default=PRODUCT_LINE_GENERAL,
    )
    comment = models.TextField('Комментарий', blank=True, default='')
    recurring = models.BooleanField('Повторять ежемесячно', default=False)
    recurring_source = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='occurrences',
        verbose_name='Шаблон регулярного расхода',
    )
    period_key = models.CharField('Месяц регулярной записи (YYYY-MM)', max_length=7, blank=True, default='')
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_other_expenses',
    )
    updated_at = models.DateTimeField('Изменено', auto_now=True, null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='analytics_other_expenses_updated',
    )

    class Meta:
        db_table = 'analytics_other_expenses'
        ordering = ['-date', '-id']
        verbose_name = 'Прочий расход'
        verbose_name_plural = 'Прочие расходы'
        indexes = [
            models.Index(fields=['date', 'status']),
            models.Index(fields=['kind', 'status', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['recurring_source', 'period_key'],
                condition=models.Q(recurring_source__isnull=False),
                name='uniq_recurring_occurrence_per_month',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.name} — {self.amount} ({self.date}, {self.status})'
