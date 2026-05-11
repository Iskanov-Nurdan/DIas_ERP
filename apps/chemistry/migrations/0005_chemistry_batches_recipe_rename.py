import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def stock_to_batches(apps, schema_editor):
    ChemistryStock = apps.get_model('chemistry', 'ChemistryStock')
    ChemistryBatch = apps.get_model('chemistry', 'ChemistryBatch')
    for stock in ChemistryStock.objects.all():
        q = stock.quantity or 0
        if q <= 0:
            continue
        ChemistryBatch.objects.create(
            chemistry_id=stock.chemistry_id,
            quantity_produced=q,
            quantity_remaining=q,
            cost_total=0,
            cost_per_unit=0,
            comment='Миграция: перенос с единого остатка в партии',
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('chemistry', '0004_remove_chemistrywriteoff_chemistry_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='chemistrycatalog',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активен'),
        ),
        migrations.AddField(
            model_name='chemistrycatalog',
            name='min_balance',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=14,
                null=True,
                verbose_name='Мин. остаток (порог)',
            ),
        ),
        migrations.CreateModel(
            name='ChemistryBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_produced', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Выпущено')),
                ('quantity_remaining', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Остаток партии')),
                (
                    'cost_total',
                    models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Себестоимость партии'),
                ),
                (
                    'cost_per_unit',
                    models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Себестоимость за кг'),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('comment', models.TextField(blank=True, verbose_name='Комментарий')),
                (
                    'chemistry',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='batches',
                        to='chemistry.chemistrycatalog',
                    ),
                ),
                (
                    'produced_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='chemistry_batches_produced',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'source_task',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='produced_batches',
                        to='chemistry.chemistrytask',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Партия химии',
                'verbose_name_plural': 'Партии химии',
                'db_table': 'chemistry_batches',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ChemistryStockDeduction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Количество')),
                (
                    'unit_price',
                    models.DecimalField(decimal_places=4, max_digits=16, verbose_name='Себестоимость кг (снимок)'),
                ),
                ('line_total', models.DecimalField(decimal_places=2, max_digits=16, verbose_name='Сумма строки')),
                ('reason', models.CharField(blank=True, max_length=100, verbose_name='Причина')),
                ('reference_id', models.PositiveIntegerField(blank=True, null=True, verbose_name='ID связи')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'batch',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='deductions',
                        to='chemistry.chemistrybatch',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Списание из партии химии',
                'verbose_name_plural': 'Списания из партий химии',
                'db_table': 'chemistry_stock_deductions',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.RunPython(stock_to_batches, noop),
        migrations.DeleteModel(name='ChemistryStock'),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(old_name='ChemistryComposition', new_name='ChemistryRecipe'),
                migrations.AlterModelTable(name='ChemistryRecipe', table='chemistry_composition'),
                migrations.AlterField(
                    model_name='chemistryrecipe',
                    name='chemistry',
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='recipe_lines',
                        to='chemistry.chemistrycatalog',
                    ),
                ),
                migrations.AlterField(
                    model_name='chemistryrecipe',
                    name='raw_material',
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='chemistry_recipe_lines',
                        to='materials.rawmaterial',
                    ),
                ),
            ],
        ),
    ]
