from decimal import Decimal

from django.db import migrations, models


def forwards_sales(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    for s in Sale.objects.all().iterator():
        q = Decimal(str(s.quantity or 0))
        p = Decimal(str(s.price or 0))
        s.sold_pieces = q
        s.sale_mode = 'pieces'
        s.revenue = (q * p).quantize(Decimal('0.01'))
        s.cost = Decimal('0')
        s.profit = (s.revenue - s.cost).quantize(Decimal('0.01'))
        s.total_meters = Decimal('0')
        s.save(
            update_fields=[
                'sold_pieces', 'sale_mode', 'revenue', 'cost', 'profit', 'total_meters',
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0009_sale_quantity_input'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='length_per_piece',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True, verbose_name='Длина штуки, м'),
        ),
        migrations.AddField(
            model_name='sale',
            name='revenue',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Выручка'),
        ),
        migrations.AddField(
            model_name='sale',
            name='cost',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Себестоимость'),
        ),
        migrations.AddField(
            model_name='sale',
            name='sale_mode',
            field=models.CharField(
                choices=[('pieces', 'По штукам'), ('packages', 'По упаковкам')],
                default='pieces',
                max_length=12,
                verbose_name='Режим продажи',
            ),
        ),
        migrations.AddField(
            model_name='sale',
            name='sold_packages',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Продано упаковок'),
        ),
        migrations.AddField(
            model_name='sale',
            name='sold_pieces',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Продано шт'),
        ),
        migrations.AddField(
            model_name='sale',
            name='total_meters',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Всего м'),
        ),
        migrations.AlterField(
            model_name='sale',
            name='price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name='Цена за единицу сделки'),
        ),
        migrations.AlterField(
            model_name='sale',
            name='quantity',
            field=models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Количество (legacy = sold_pieces)'),
        ),
        migrations.RunPython(forwards_sales, migrations.RunPython.noop),
    ]
