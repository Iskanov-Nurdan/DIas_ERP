from decimal import Decimal

from django.db import migrations, models


def forwards_total_price(apps, schema_editor):
    Incoming = apps.get_model('materials', 'Incoming')
    for row in Incoming.objects.all().iterator():
        q = Decimal(str(row.quantity or 0))
        p = Decimal(str(row.price_per_unit or 0))
        row.total_price = (q * p).quantize(Decimal('0.01'))
        row.save(update_fields=['total_price'])


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0003_rawmaterial_min_balance'),
    ]

    operations = [
        migrations.AddField(
            model_name='incoming',
            name='total_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Сумма строки'),
        ),
        migrations.RunPython(forwards_total_price, migrations.RunPython.noop),
    ]
