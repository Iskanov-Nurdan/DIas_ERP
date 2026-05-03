from django.db import migrations, models


def normalize_client_type(apps, schema_editor):
    Client = apps.get_model('sales', 'Client')
    Client.objects.filter(client_type__in=('', None)).update(client_type='individual')


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0027_returnline_rework_receipt_batch'),
    ]

    operations = [
        migrations.AlterField(
            model_name='client',
            name='phone_alt',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Доп. телефон'),
        ),
        migrations.AddField(
            model_name='client',
            name='settlement_account',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='Расчётный счёт'),
        ),
        migrations.RunPython(normalize_client_type, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='client',
            name='client_type',
            field=models.CharField(
                choices=[('individual', 'Физ лицо'), ('company', 'Компания')],
                default='individual',
                max_length=20,
                verbose_name='Тип клиента',
            ),
        ),
    ]
