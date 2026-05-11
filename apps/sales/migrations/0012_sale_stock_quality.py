from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0011_client_email_messenger_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='stock_quality',
            field=models.CharField(
                blank=True,
                default='',
                max_length=10,
                verbose_name='Качество склада на момент продажи',
            ),
        ),
    ]
