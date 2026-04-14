from decimal import Decimal

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

    def delete(self, *args, **kwargs):
        """Перед удалением — снимок названия и id для исторических связей (FK станут NULL)."""
        pk = self.pk
        name = self.name
        Order.objects.filter(line_id=pk).update(line_name_snapshot=name, former_line_id=pk)
        RecipeRun.objects.filter(line_id=pk).update(line_name_snapshot=name, former_line_id=pk)
        LineHistory.objects.filter(line_id=pk).update(line_name_snapshot=name, former_line_id=pk)
        Shift.objects.filter(line_id=pk).update(line_name_snapshot=name, former_line_id=pk)
        super().delete(*args, **kwargs)


class LineHistory(models.Model):
    ACTION_OPEN = 'open'
    ACTION_CLOSE = 'close'
    ACTION_PARAMS_UPDATE = 'params_update'
    ACTION_SHIFT_PAUSE = 'shift_pause'
    ACTION_SHIFT_RESUME = 'shift_resume'
    ACTION_CHOICES = [
        (ACTION_OPEN, 'Открыта'),
        (ACTION_CLOSE, 'Закрыта'),
        (ACTION_PARAMS_UPDATE, 'Параметры'),
        (ACTION_SHIFT_PAUSE, 'Остановка смены'),
        (ACTION_SHIFT_RESUME, 'Возобновление смены'),
    ]

    line = models.ForeignKey(
        Line,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='history',
    )
    line_name_snapshot = models.CharField(
        'Название линии (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_line_id = models.PositiveIntegerField('Бывший id линии', null=True, blank=True)
    action = models.CharField('Действие', max_length=20, choices=ACTION_CHOICES)
    date = models.DateField('Дата')
    time = models.TimeField('Время')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='line_actions')
    height = models.DecimalField('Высота', max_digits=10, decimal_places=2, null=True, blank=True)
    width = models.DecimalField('Ширина', max_digits=10, decimal_places=2, null=True, blank=True)
    angle_deg = models.DecimalField('Угол, °', max_digits=8, decimal_places=2, null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True)
    session_title = models.CharField('Название смены', max_length=255, blank=True)

    class Meta:
        db_table = 'line_history'
        verbose_name = 'История смены'
        verbose_name_plural = 'История смен'
        ordering = ['-date', '-time']

    def save(self, *args, **kwargs):
        if self.line_id:
            try:
                ln = Line.objects.get(pk=self.line_id)
                self.line_name_snapshot = ln.name
                self.former_line_id = None
            except Line.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.line.name if self.line_id else (self.line_name_snapshot or '—')
        return f'{label} — {self.get_action_display()} ({self.date})'


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
    recipe = models.ForeignKey(
        'recipes.Recipe',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
    )
    recipe_name_snapshot = models.CharField(
        'Наименование рецепта (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_recipe_id = models.PositiveIntegerField('Бывший id рецепта', null=True, blank=True)
    line = models.ForeignKey(
        Line,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
    )
    line_name_snapshot = models.CharField(
        'Название линии (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_line_id = models.PositiveIntegerField('Бывший id линии', null=True, blank=True)
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    product = models.CharField('Продукт', max_length=255)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    date = models.DateField('Дата')

    class Meta:
        db_table = 'orders'
        verbose_name = 'Заказ на производство'
        verbose_name_plural = 'Заказы'
        ordering = ['-date', '-id']

    def save(self, *args, **kwargs):
        if self.line_id:
            try:
                ln = Line.objects.get(pk=self.line_id)
                self.line_name_snapshot = ln.name
                self.former_line_id = None
            except Line.DoesNotExist:
                pass
        if self.recipe_id:
            from apps.recipes.models import Recipe as RecipeModel

            try:
                r = RecipeModel.objects.get(pk=self.recipe_id)
                self.recipe_name_snapshot = r._snapshot_name()
                self.former_recipe_id = None
            except RecipeModel.DoesNotExist:
                pass
        super().save(*args, **kwargs)

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

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name='batches',
        null=True,
        blank=True,
    )
    profile = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.PROTECT,
        related_name='production_batches',
        null=True,
        blank=True,
        verbose_name='Профиль',
    )
    recipe = models.ForeignKey(
        'recipes.Recipe',
        on_delete=models.PROTECT,
        related_name='production_batches',
        null=True,
        blank=True,
    )
    line = models.ForeignKey(
        Line,
        on_delete=models.PROTECT,
        related_name='production_batches',
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        'production.Shift',
        on_delete=models.PROTECT,
        related_name='production_batches',
        null=True,
        blank=True,
        verbose_name='Смена',
    )
    product = models.CharField('Продукт (наименование)', max_length=255)
    pieces = models.PositiveIntegerField('Штук', default=1)
    length_per_piece = models.DecimalField('Длина штуки, м', max_digits=14, decimal_places=4, default=1)
    total_meters = models.DecimalField('Всего метров', max_digits=16, decimal_places=4, default=0)
    quantity = models.DecimalField(
        'Количество (legacy = total_meters)',
        max_digits=14,
        decimal_places=4,
        default=0,
    )
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='production_batches')
    date = models.DateField('Дата')
    produced_at = models.DateTimeField('Произведено', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True)
    otk_status = models.CharField('Статус ОТК', max_length=20, choices=OTK_STATUS_CHOICES, default=OTK_PENDING)
    cost_price = models.DecimalField('Себестоимость (legacy)', max_digits=14, decimal_places=2, default=0)
    material_cost_total = models.DecimalField('Материальная себестоимость', max_digits=16, decimal_places=2, default=0)
    cost_per_meter = models.DecimalField('Себестоимость за м', max_digits=16, decimal_places=4, default=0)
    cost_per_piece = models.DecimalField('Себестоимость за шт', max_digits=16, decimal_places=4, default=0)
    shift_height = models.DecimalField('Смена: высота', max_digits=10, decimal_places=2, null=True, blank=True)
    shift_width = models.DecimalField('Смена: ширина', max_digits=10, decimal_places=2, null=True, blank=True)
    shift_angle_deg = models.DecimalField('Смена: угол °', max_digits=8, decimal_places=2, null=True, blank=True)
    shift_opener_name = models.CharField('Смена: кто открыл', max_length=255, blank=True, default='')
    shift_opened_at = models.DateTimeField('Смена: время открытия', null=True, blank=True)

    class Meta:
        db_table = 'production_batches'
        verbose_name = 'Партия производства'
        verbose_name_plural = 'Партии производства'
        ordering = ['-date', '-id']

    def recompute_totals(self):
        p = int(self.pieces or 0)
        l = Decimal(str(self.length_per_piece or 0))
        self.total_meters = (Decimal(p) * l).quantize(Decimal('0.0001'))
        self.quantity = self.total_meters

    def save(self, *args, **kwargs):
        self.recompute_totals()
        if self.material_cost_total is not None and self.total_meters and self.total_meters > 0:
            tm = Decimal(str(self.total_meters))
            self.cost_per_meter = (Decimal(str(self.material_cost_total)) / tm).quantize(Decimal('0.0001'))
        else:
            self.cost_per_meter = Decimal('0')
        if self.material_cost_total is not None and self.pieces and self.pieces > 0:
            self.cost_per_piece = (Decimal(str(self.material_cost_total)) / Decimal(int(self.pieces))).quantize(Decimal('0.0001'))
        else:
            self.cost_per_piece = Decimal('0')
        self.cost_price = self.material_cost_total
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product} — {self.pieces}×{self.length_per_piece} м ({self.get_otk_status_display()})'


class Shift(models.Model):
    STATUS_OPEN = 'open'
    STATUS_PAUSED = 'paused'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Открыта'),
        (STATUS_PAUSED, 'На паузе'),
        (STATUS_CLOSED, 'Закрыта'),
    ]

    line = models.ForeignKey(
        Line,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='shifts',
        verbose_name='Линия',
    )
    line_name_snapshot = models.CharField(
        'Название линии (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_line_id = models.PositiveIntegerField('Бывший id линии', null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='shifts',
        verbose_name='Сотрудник',
    )
    opened_at = models.DateTimeField('Начало смены')
    closed_at = models.DateTimeField('Конец смены', null=True, blank=True)
    status = models.CharField(
        'Статус',
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )
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
        constraints = [
            # Не больше одной открытой личной смены (без линии) на пользователя.
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(closed_at__isnull=True) & models.Q(line_id__isnull=True),
                name='uniq_shift_personal_open_per_user',
            ),
            # Не больше одной открытой смены на одну и ту же линию у одного пользователя.
            models.UniqueConstraint(
                fields=['user', 'line_id'],
                condition=models.Q(closed_at__isnull=True) & models.Q(line_id__isnull=False),
                name='uniq_shift_user_line_open_per_user_line',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.closed_at is not None and self.status != self.STATUS_CLOSED:
            self.status = self.STATUS_CLOSED
        if self.line_id:
            try:
                ln = Line.objects.get(pk=self.line_id)
                self.line_name_snapshot = ln.name
                self.former_line_id = None
            except Line.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        user_name = self.user.name if self.user_id else '—'
        line_label = self.line.name if self.line_id else (self.line_name_snapshot or '—')
        return f'{user_name} / {line_label} ({self.opened_at:%d.%m.%Y %H:%M})'


class ShiftComplaint(models.Model):
    """Жалоба/замечание в контексте смен (упоминания @сотрудников)."""

    body = models.TextField('Текст')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='authored_shift_complaints',
        verbose_name='Автор',
    )
    shift = models.ForeignKey(
        Shift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='complaints',
        verbose_name='Смена',
    )
    mentioned_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='mentioned_in_shift_complaints',
        verbose_name='Упомянутые',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)

    class Meta:
        db_table = 'shift_complaints'
        verbose_name = 'Жалоба (смена)'
        verbose_name_plural = 'Жалобы (смены)'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['author', '-created_at']),
        ]

    def __str__(self):
        return f'#{self.pk} — {self.author_id}'


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


class RecipeRun(models.Model):
    """
    Подготовка замеса до партии ОТК: ёмкости и фактический расход по строкам (для учёта/экрана).

    Реальное FIFO-списание и себестоимость — только у связанной ProductionBatch (см. batch_stock).
    """

    recipe = models.ForeignKey(
        'recipes.Recipe',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recipe_runs',
    )
    recipe_name_snapshot = models.CharField(
        'Наименование рецепта (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_recipe_id = models.PositiveIntegerField('Бывший id рецепта', null=True, blank=True)
    line = models.ForeignKey(
        Line,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recipe_runs',
    )
    line_name_snapshot = models.CharField(
        'Название линии (снимок)',
        max_length=255,
        blank=True,
        default='',
    )
    former_line_id = models.PositiveIntegerField('Бывший id линии', null=True, blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    production_batch = models.OneToOneField(
        'ProductionBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_recipe_run',
        verbose_name='Партия ОТК',
    )
    recipe_run_consumption_applied = models.BooleanField(
        'Устарело: раньше помечало списание по замесу (не используется)',
        default=False,
        db_index=True,
    )

    class Meta:
        db_table = 'recipe_runs'
        verbose_name = 'Запуск по рецепту'
        verbose_name_plural = 'Запуски по рецептам'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.line_id:
            try:
                ln = Line.objects.get(pk=self.line_id)
                self.line_name_snapshot = ln.name
                self.former_line_id = None
            except Line.DoesNotExist:
                pass
        if self.recipe_id:
            from apps.recipes.models import Recipe as RecipeModel

            try:
                r = RecipeModel.objects.get(pk=self.recipe_id)
                self.recipe_name_snapshot = r._snapshot_name()
                self.former_recipe_id = None
            except RecipeModel.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        line_label = self.line.name if self.line_id else (self.line_name_snapshot or '—')
        recipe_label = self.recipe.recipe if self.recipe_id else (self.recipe_name_snapshot or '—')
        return f'{recipe_label} @ {line_label} ({self.created_at:%Y-%m-%d %H:%M})'


class RecipeRunBatch(models.Model):
    """
    Партия внутри запуска (ёмкость) — в первую очередь для расхода сырья по components.
    Поле quantity не участвует в расчёте выпуска для ОТК (выпуск — корневой quantity замеса / норма рецепта).
    """

    run = models.ForeignKey(RecipeRun, on_delete=models.CASCADE, related_name='batches')
    index = models.PositiveSmallIntegerField('Порядок', default=0)
    label = models.CharField('Название', max_length=255, blank=True)
    quantity = models.DecimalField(
        'Сводное количество (опц.)',
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = 'recipe_run_batches'
        verbose_name = 'Партия запуска'
        verbose_name_plural = 'Партии запуска'
        ordering = ['index', 'id']

    def __str__(self):
        q = self.quantity if self.quantity is not None else '—'
        return f'{self.label or self.index} — {q}'


class RecipeRunBatchComponent(models.Model):
    """Фактический расход по строке рецепта в рамках одной партии запуска."""

    batch = models.ForeignKey(
        RecipeRunBatch,
        on_delete=models.CASCADE,
        related_name='components',
    )
    recipe_component = models.ForeignKey(
        'recipes.RecipeComponent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recipe_run_batch_lines',
        verbose_name='Строка рецепта',
    )
    raw_material = models.ForeignKey(
        'materials.RawMaterial',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='recipe_run_batch_usages',
    )
    chemistry = models.ForeignKey(
        'chemistry.ChemistryCatalog',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='recipe_run_batch_usages',
    )
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
    unit = models.CharField('Единица', max_length=50, default='кг')
    material_name_snapshot = models.CharField(
        'Сырьё (снимок наименования)',
        max_length=255,
        blank=True,
        default='',
    )
    chemistry_name_snapshot = models.CharField(
        'Хим. элемент (снимок наименования)',
        max_length=255,
        blank=True,
        default='',
    )

    class Meta:
        db_table = 'recipe_run_batch_components'
        verbose_name = 'Расход по партии'
        verbose_name_plural = 'Расходы по партиям'
        ordering = ['id']

    def save(self, *args, **kwargs):
        if self.raw_material_id:
            from apps.materials.models import RawMaterial

            try:
                m = RawMaterial.objects.get(pk=self.raw_material_id)
                self.material_name_snapshot = (m.name or '')[:255]
            except RawMaterial.DoesNotExist:
                pass
        if self.chemistry_id:
            from apps.chemistry.models import ChemistryCatalog

            try:
                ch = ChemistryCatalog.objects.get(pk=self.chemistry_id)
                self.chemistry_name_snapshot = (ch.name or '')[:255]
            except ChemistryCatalog.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.batch_id}: {self.quantity} {self.unit}'
