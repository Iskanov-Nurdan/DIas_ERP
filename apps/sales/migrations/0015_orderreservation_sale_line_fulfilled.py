import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0014_business_logic_upgrade'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderreservation',
            name='fulfilled_quantity',
            field=models.DecimalField(
                decimal_places=4, default=0, max_digits=14,
                verbose_name='Исполнено',
            ),
        ),
        migrations.AddField(
            model_name='orderreservation',
            name='sale_line',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fulfilled_reservations',
                to='sales.saleline',
                verbose_name='Строка продажи (исполнила резерв)',
            ),
        ),
    ]
