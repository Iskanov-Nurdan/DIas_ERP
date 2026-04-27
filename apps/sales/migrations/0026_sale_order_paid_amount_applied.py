from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0025_order_production_request_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='order_paid_amount_applied',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Примененная предоплата из заявки'),
        ),
    ]
