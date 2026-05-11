# Generated manually: безопасные default для Sale и Return

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0019_sale_warehouse_mutation_saleline_piece_pick'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sale',
            name='sale_status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'),
                    ('confirmed', 'Подтверждена'),
                    ('partially_shipped', 'Частично отгружена'),
                    ('shipped', 'Отгружена'),
                    ('closed', 'Закрыта'),
                    ('canceled', 'Отменена'),
                ],
                db_index=True,
                default='draft',
                max_length=25,
                verbose_name='Статус продажи',
            ),
        ),
        migrations.AlterField(
            model_name='return',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Черновик'),
                    ('completed', 'Проведён'),
                    ('canceled', 'Отменён'),
                ],
                db_index=True,
                default='draft',
                max_length=20,
                verbose_name='Статус',
            ),
        ),
    ]
