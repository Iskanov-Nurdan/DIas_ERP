from django.db import models


class PlasticProfile(models.Model):
    """Пластиковый профиль (готовая продукция): 1 рецепт привязан к одному профилю."""

    name = models.CharField('Наименование', max_length=255)
    code = models.CharField('Код', max_length=100, blank=True, default='')

    class Meta:
        db_table = 'plastic_profiles'
        verbose_name = 'Профиль'
        verbose_name_plural = 'Профили'

    def __str__(self):
        return self.name or f'#{self.pk}'


class Recipe(models.Model):
    BASE_UNIT_PER_METER = 'per_meter'
    BASE_UNIT_CHOICES = [
        (BASE_UNIT_PER_METER, 'На 1 метр'),
    ]

    recipe = models.CharField('Наименование рецепта', max_length=255)
    profile = models.ForeignKey(
        PlasticProfile,
        on_delete=models.PROTECT,
        related_name='recipes',
        null=True,
        blank=True,
        verbose_name='Профиль',
    )
    product = models.CharField('Продукт (денормализация)', max_length=255, blank=True, default='')
    base_unit = models.CharField(
        'База норм',
        max_length=20,
        choices=BASE_UNIT_CHOICES,
        default=BASE_UNIT_PER_METER,
    )
    # Устарело: нормы на фиксированный «выпуск»; оставлено для миграций и старых клиентов.
    output_quantity = models.DecimalField(
        'Выпуск (количество), устар.',
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )
    output_unit_kind = models.CharField(
        'Тип учёта выпуска, устар.',
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('naming', 'Наименование'),
            ('pieces', 'Штуки'),
            ('amount', 'Количество'),
        ],
    )
    comment = models.TextField('Комментарий', blank=True, default='')
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        db_table = 'recipes'
        verbose_name = 'Рецепт'
        verbose_name_plural = 'Рецепты'

    def _snapshot_name(self) -> str:
        return (self.recipe or '').strip()

    def save(self, *args, **kwargs):
        if self.profile_id:
            try:
                p = PlasticProfile.objects.get(pk=self.profile_id)
                if not (self.product or '').strip():
                    self.product = (p.name or '')[:255]
            except PlasticProfile.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from apps.production.models import Order, RecipeRun

        pk = self.pk
        snap = self._snapshot_name()
        Order.objects.filter(recipe_id=pk).update(recipe_name_snapshot=snap, former_recipe_id=pk)
        RecipeRun.objects.filter(recipe_id=pk).update(recipe_name_snapshot=snap, former_recipe_id=pk)
        super().delete(*args, **kwargs)

    def __str__(self):
        label = (self.product or '').strip() or (self.profile.name if self.profile_id else '')
        return f'{self.recipe} → {label or "—"}'


class RecipeComponent(models.Model):
    TYPE_RAW = 'raw'
    TYPE_CHEM = 'chem'
    TYPE_CHOICES = [(TYPE_RAW, 'Сырьё'), (TYPE_CHEM, 'Хим. элемент')]

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='components')
    type = models.CharField('Тип', max_length=10, choices=TYPE_CHOICES)
    raw_material = models.ForeignKey(
        'materials.RawMaterial',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='recipe_components',
    )
    chemistry = models.ForeignKey(
        'chemistry.ChemistryCatalog',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='recipe_components',
    )
    quantity_per_meter = models.DecimalField('На 1 м профиля', max_digits=14, decimal_places=6)
    unit = models.CharField('Единица', max_length=50, default='кг')

    class Meta:
        db_table = 'recipe_components'
        verbose_name = 'Компонент рецепта'
        verbose_name_plural = 'Компоненты рецептов'

    def __str__(self):
        if self.type == self.TYPE_RAW and self.raw_material_id:
            return f'{self.recipe.product} — {self.raw_material.name}'
        if self.type == self.TYPE_CHEM and self.chemistry_id:
            return f'{self.recipe.product} — {self.chemistry.name}'
        return f'Component #{self.id}'
