# OTK account v2: shift_period, packers M2M, multi-blank allocations

from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0025_productionbatch_workshop_blank'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('workshop', '0008_migrate_otk_pool_from_runs'),
    ]

    operations = [
        migrations.AddField(
            model_name='otkaccountsession',
            name='defect_blank',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='otk_defect_account_sessions',
                to='workshop.workshopblank',
                verbose_name='Заготовка для брака',
            ),
        ),
        migrations.AddField(
            model_name='otkaccountsession',
            name='shift',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='otk_account_sessions',
                to='production.shift',
                verbose_name='Смена (привязка)',
            ),
        ),
        migrations.AddField(
            model_name='otkaccountsession',
            name='shift_period',
            field=models.CharField(
                blank=True,
                choices=[('day', 'День'), ('night', 'Ночь')],
                default='',
                max_length=8,
                verbose_name='Смена',
            ),
        ),
        migrations.AlterField(
            model_name='otkaccountsession',
            name='blank',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='otk_account_sessions',
                to='workshop.workshopblank',
                verbose_name='Заготовка (основная)',
            ),
        ),
        migrations.AlterField(
            model_name='otkaccountsession',
            name='packer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='otk_sessions_as_packer',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Упаковщик (legacy)',
            ),
        ),
        migrations.AddField(
            model_name='otkaccountsession',
            name='packers',
            field=models.ManyToManyField(
                blank=True,
                related_name='otk_packer_sessions',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Упаковщики',
            ),
        ),
        migrations.CreateModel(
            name='OtkAccountBlankAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'consumed_kg',
                    models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Списано, кг'),
                ),
                (
                    'remaining_kg_after',
                    models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Остаток пула после, кг'),
                ),
                (
                    'blank',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='otk_account_allocations',
                        to='workshop.workshopblank',
                        verbose_name='Заготовка',
                    ),
                ),
                (
                    'session',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='blank_allocations',
                        to='workshop.otkaccountsession',
                        verbose_name='Сессия учёта',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Списание ОТК по заготовке',
                'verbose_name_plural': 'Списания ОТК по заготовкам',
                'db_table': 'workshop_otk_account_blank_allocations',
                'ordering': ('blank__name', 'pk'),
            },
        ),
    ]
