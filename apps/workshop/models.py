from django.db import models


class WorkshopBlank(models.Model):
    """Справочник цеховой заготовки: норма кг одной «бочки» задаётся в каталоге (заготовка продукции)."""

    name = models.CharField('Наименование', max_length=255)
    recipe_kg_per_barrel = models.DecimalField('Кг в одной бочке по рецепту', max_digits=14, decimal_places=6)
    plastic_profile = models.ForeignKey(
        'recipes.PlasticProfile',
        verbose_name='Профиль ГП (если задан — заготовка только для этого профиля)',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='workshop_blanks',
    )
    is_active = models.BooleanField('Активна', default=True)
    chemistry = models.ForeignKey(
        'chemistry.ChemistryCatalog',
        verbose_name='Связь с хим. полуфабрикатом',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='workshop_blanks',
    )
    comment = models.TextField('Комментарий', blank=True, default='')

    class Meta:
        db_table = 'workshop_blanks'
        verbose_name = 'Заготовка (цех)'
        verbose_name_plural = 'Заготовки (цех)'

    def __str__(self):
        return self.name or f'#{self.pk}'


class WorkshopBlankCompositionLine(models.Model):
    """Состав заготовки: сырьё и количество (кг) на указанную норму (например, на партию/бочку — по договорённости с производством)."""

    blank = models.ForeignKey(
        WorkshopBlank,
        on_delete=models.CASCADE,
        related_name='composition_lines',
        verbose_name='Заготовка',
    )
    raw_material = models.ForeignKey(
        'materials.RawMaterial',
        on_delete=models.PROTECT,
        related_name='workshop_blank_composition_lines',
        verbose_name='Сырьё',
    )
    quantity_kg = models.DecimalField('Количество, кг', max_digits=14, decimal_places=6)

    class Meta:
        db_table = 'workshop_blank_composition_lines'
        verbose_name = 'Строка состава заготовки'
        verbose_name_plural = 'Состав заготовок (цех)'
        constraints = [
            models.UniqueConstraint(fields=('blank', 'raw_material'), name='workshop_blank_comp_unique_rm'),
        ]

    def __str__(self):
        return f'{self.blank_id} ← {self.raw_material_id} {self.quantity_kg} кг'


class WorkshopPreparedState(models.Model):
    """Бочки + дробный остаток на цехе по заготовке (одна строка на заготовку)."""

    blank = models.OneToOneField(
        WorkshopBlank,
        on_delete=models.CASCADE,
        related_name='prepared_state',
        verbose_name='Заготовка',
    )
    barrels = models.PositiveIntegerField('Бочек', default=0)
    extra_kg = models.DecimalField(
        'Дробный остаток, кг', max_digits=14, decimal_places=6, default=0
    )

    class Meta:
        db_table = 'workshop_prepared_state'
        verbose_name = 'Остаток заготовки на цеху'
        verbose_name_plural = 'Остатки заготовок на цеху'

    def __str__(self):
        return f'{self.blank}: {self.barrels} боч. + {self.extra_kg} кг'


class BlankProductionRun(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_IN_PRODUCTION = 'in_production'
    STATUS_OTK_DONE = 'otk_done'
    STATUS_GP_ACCEPTED = 'gp_accepted'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_IN_PRODUCTION, 'В производстве'),
        (STATUS_OTK_DONE, 'ОТК выполнен'),
        (STATUS_GP_ACCEPTED, 'Принято на склад ГП'),
    ]

    created_at = models.DateTimeField('Создано', auto_now_add=True)

    blank = models.ForeignKey(
        WorkshopBlank,
        on_delete=models.PROTECT,
        related_name='production_runs',
        verbose_name='Заготовка',
    )
    blank_name_snapshot = models.CharField('Наименование заготовки (снимок)', max_length=255)

    product = models.ForeignKey(
        'recipes.PlasticProfile',
        on_delete=models.PROTECT,
        related_name='workshop_production_runs',
        verbose_name='Готовая продукция (SKU)',
    )
    product_name_snapshot = models.CharField('Наименование ГП (снимок)', max_length=255)

    blank_total_kg = models.DecimalField('Общая масса заготовки по данным оператора', max_digits=14, decimal_places=6)
    blank_used_in_production_kg = models.DecimalField('Запуск партии с цеха, кг', max_digits=14, decimal_places=6)
    vat_max_kg_demo = models.DecimalField('Для демо: лимит веса VAT, кг', max_digits=14, decimal_places=6)

    weight_kg_per_piece = models.DecimalField('Вес одной шт, кг', max_digits=14, decimal_places=6)

    defect_kg = models.DecimalField('Брак ОТК, кг', max_digits=14, decimal_places=6, null=True, blank=True)
    good_kg = models.DecimalField('Годный после ОТК, кг', max_digits=14, decimal_places=6, null=True, blank=True)
    good_pieces = models.IntegerField('Шт после ОТК (расчёт)', null=True, blank=True)
    otk_recorded_at = models.DateTimeField('Дата записи ОТК', null=True, blank=True)

    gp_accepted_at = models.DateTimeField('Дата приёмки ГП', null=True, blank=True)
    gp_accepted_pieces = models.IntegerField('Принято штук ГП', null=True, blank=True)
    gp_accepted_kg = models.DecimalField('Принято кг на ГП', max_digits=14, decimal_places=6, null=True, blank=True)
    gp_machine_remainder_kg = models.DecimalField(
        'Остаток на машине, кг (возврат на цех)', max_digits=14, decimal_places=6, null=True, blank=True
    )

    production_batch = models.ForeignKey(
        'production.ProductionBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='blank_production_runs',
        verbose_name='Партия производства (линия)',
    )
    client_request = models.ForeignKey(
        'sales.Order',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='blank_production_runs',
        verbose_name='Заявка клиента',
    )
    order_line = models.ForeignKey(
        'sales.OrderLine',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='blank_production_runs',
        verbose_name='Строка заявки',
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PRODUCTION)

    class Meta:
        db_table = 'workshop_blank_production_runs'
        ordering = ('-created_at', '-pk')
        verbose_name = 'Партия производства по заготовке'
        verbose_name_plural = 'Партии производства по заготовкам'
