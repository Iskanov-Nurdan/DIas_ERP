from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0010_sale_revenue_cost_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254, verbose_name='Email'),
        ),
        migrations.AddField(
            model_name='client',
            name='messenger',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Мессенджер / WhatsApp / Telegram',
            ),
        ),
        migrations.AddField(
            model_name='client',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='Активен'),
        ),
    ]
