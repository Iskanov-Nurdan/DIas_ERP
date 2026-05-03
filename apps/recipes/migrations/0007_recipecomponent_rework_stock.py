# Generated manually: rework_stock recipe components

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0008_warehousebatch_stock_bucket'),
        ('recipes', '0006_alter_recipe_output_quantity_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recipecomponent',
            name='type',
            field=models.CharField(max_length=30, verbose_name='Тип'),
        ),
        migrations.AddField(
            model_name='recipecomponent',
            name='rework_warehouse_batch',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recipe_components_as_rework_input',
                to='warehouse.warehousebatch',
                verbose_name='Партия переделанных',
            ),
        ),
    ]
