from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0029_gp_pack_unit_warehouse_batch_and_sale_line'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='checkout_payment_type',
            field=models.CharField(
                blank=True,
                default='',
                max_length=20,
                verbose_name='Тип оплаты при продаже (full/partial/debt)',
            ),
        ),
        migrations.AddField(
            model_name='sale',
            name='checkout_payment_method',
            field=models.CharField(
                blank=True,
                default='',
                max_length=20,
                verbose_name='Основной способ оплаты при продаже (cash/card)',
            ),
        ),
        migrations.AddField(
            model_name='sale',
            name='payment_reference',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Реквизит оплаты (карта/телефон)',
            ),
        ),
    ]
