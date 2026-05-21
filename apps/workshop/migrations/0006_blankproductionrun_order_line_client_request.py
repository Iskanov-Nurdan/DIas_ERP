import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0029_gp_pack_unit_warehouse_batch_and_sale_line'),
        ('workshop', '0005_alter_workshopblank_plastic_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='blankproductionrun',
            name='client_request',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='blank_production_runs',
                to='sales.order',
                verbose_name='Заявка клиента',
            ),
        ),
        migrations.AddField(
            model_name='blankproductionrun',
            name='order_line',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='blank_production_runs',
                to='sales.orderline',
                verbose_name='Строка заявки',
            ),
        ),
    ]
