from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0006_sale_packaging_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='piece_pick',
            field=models.CharField(blank=True, max_length=40, verbose_name='Источник штук при продаже'),
        ),
        migrations.AddField(
            model_name='sale',
            name='stock_form',
            field=models.CharField(blank=True, max_length=20, verbose_name='Форма учёта склада на момент продажи'),
        ),
    ]
