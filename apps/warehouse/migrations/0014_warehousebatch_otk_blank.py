import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0007_otk_simplification'),
        ('warehouse', '0013_alter_warehousebatch_blank_production_run'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='workshop_blank',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='warehouse_batches',
                to='workshop.workshopblank',
                verbose_name='Заготовка (цех)',
            ),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='otk_account_session',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='warehouse_batches',
                to='workshop.otkaccountsession',
                verbose_name='Учёт ОТК',
            ),
        ),
    ]
