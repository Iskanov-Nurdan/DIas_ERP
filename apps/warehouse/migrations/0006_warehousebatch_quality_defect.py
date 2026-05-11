from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0005_warehouse_profile_piece_costs'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='quality',
            field=models.CharField(
                choices=[('good', 'Годный'), ('defect', 'Брак')],
                db_index=True,
                default='good',
                max_length=10,
                verbose_name='Качество партии',
            ),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='is_defect',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Брак'),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='defect_reason',
            field=models.TextField(blank=True, default='', verbose_name='Причина брака (строка партии)'),
        ),
    ]
