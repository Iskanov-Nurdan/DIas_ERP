from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0008_client_extended_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='quantity_input',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=14,
                null=True,
                verbose_name='Ввод количества (упаковки при продаже в упаковках)',
            ),
        ),
    ]
