from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('otk', '0003_otk_check_dimensions_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='otkcheck',
            name='inspector_name',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Контролёр (строка)',
            ),
        ),
    ]
