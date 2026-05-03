# Generated manually: link return line to rework warehouse receipt batch

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0008_warehousebatch_stock_bucket'),
        ('sales', '0026_sale_order_paid_amount_applied'),
    ]

    operations = [
        migrations.AddField(
            model_name='returnline',
            name='rework_receipt_batch',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='source_return_line',
                to='warehouse.warehousebatch',
                verbose_name='Партия оприходования (переделанные)',
            ),
        ),
    ]
