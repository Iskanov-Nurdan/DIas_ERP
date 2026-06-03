from decimal import Decimal

from django.db import migrations, models


def zero_cost_to_null(apps, schema_editor):
    PlasticProfile = apps.get_model('recipes', 'PlasticProfile')
    PlasticProfile.objects.filter(cost_price=Decimal('0')).update(cost_price=None)


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0010_plastic_profile_cost_markup'),
    ]

    operations = [
        migrations.AlterField(
            model_name='plasticprofile',
            name='cost_price',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                default=None,
                max_digits=16,
                null=True,
                verbose_name='Себестоимость за 1 шт, сом (только расчёт ОТК)',
            ),
        ),
        migrations.RunPython(zero_cost_to_null, migrations.RunPython.noop),
    ]
