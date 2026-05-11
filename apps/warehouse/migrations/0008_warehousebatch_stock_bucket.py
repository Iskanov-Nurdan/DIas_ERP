# Generated manually for reworked stock segment

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0007_remove_warehousebatch_is_defect'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='stock_bucket',
            field=models.CharField(
                db_index=True,
                default='standard',
                max_length=20,
                verbose_name='Сегмент склада',
            ),
        ),
    ]
