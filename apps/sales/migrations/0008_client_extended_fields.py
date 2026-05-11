from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0007_sale_stock_form_piece_pick'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='phone_alt',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='Доп. телефон'),
        ),
        migrations.AddField(
            model_name='client',
            name='client_type',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Тип клиента'),
        ),
        migrations.AddField(
            model_name='client',
            name='notes',
            field=models.TextField(blank=True, default='', verbose_name='Комментарий'),
        ),
    ]
