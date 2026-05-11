from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0012_fill_component_snapshots_from_recipe_component'),
    ]

    operations = [
        migrations.AddField(
            model_name='productionbatch',
            name='shift_height',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Смена: высота',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='shift_width',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Смена: ширина',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='shift_angle_deg',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=8, null=True, verbose_name='Смена: угол °',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='shift_opener_name',
            field=models.CharField(
                blank=True, default='', max_length=255, verbose_name='Смена: кто открыл',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='shift_opened_at',
            field=models.DateTimeField(
                blank=True, null=True, verbose_name='Смена: время открытия',
            ),
        ),
    ]
