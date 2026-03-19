from django.db import models


class Recipe(models.Model):
    recipe = models.CharField('Наименование рецепта', max_length=255)
    product = models.CharField('Продукт', max_length=255)

    class Meta:
        db_table = 'recipes'
        verbose_name = 'Рецепт'
        verbose_name_plural = 'Рецепты'

    def __str__(self):
        return f'{self.recipe} → {self.product}'


class RecipeComponent(models.Model):
    TYPE_RAW = 'raw'
    TYPE_CHEM = 'chem'
    TYPE_CHOICES = [(TYPE_RAW, 'Сырьё'), (TYPE_CHEM, 'Хим. элемент')]

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='components')
    type = models.CharField('Тип', max_length=10, choices=TYPE_CHOICES)
    raw_material = models.ForeignKey('materials.RawMaterial', on_delete=models.CASCADE, null=True, blank=True, related_name='recipe_components')
    chemistry = models.ForeignKey('chemistry.ChemistryCatalog', on_delete=models.CASCADE, null=True, blank=True, related_name='recipe_components')
    quantity = models.DecimalField('Количество', max_digits=14, decimal_places=4)
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
