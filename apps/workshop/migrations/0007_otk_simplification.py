import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0010_plastic_profile_cost_markup'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('workshop', '0006_blankproductionrun_order_line_client_request'),
    ]

    operations = [
        migrations.AlterField(
            model_name='blankproductionrun',
            name='product',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='workshop_production_runs',
                to='recipes.plasticprofile',
                verbose_name='Готовая продукция (SKU)',
            ),
        ),
        migrations.AlterField(
            model_name='blankproductionrun',
            name='product_name_snapshot',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Наименование ГП (снимок)',
            ),
        ),
        migrations.AlterField(
            model_name='blankproductionrun',
            name='weight_kg_per_piece',
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=14,
                null=True,
                verbose_name='Вес одной шт, кг',
            ),
        ),
        migrations.CreateModel(
            name='OtkBlankPool',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('remaining_kg', models.DecimalField(decimal_places=6, default=0, max_digits=14, verbose_name='Доступно для учёта, кг')),
                ('total_intake_kg', models.DecimalField(decimal_places=6, default=0, max_digits=14, verbose_name='Сумма приходов, кг')),
                ('version', models.PositiveIntegerField(default=0, verbose_name='Версия (optimistic lock)')),
                (
                    'blank',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='otk_pool',
                        to='workshop.workshopblank',
                        verbose_name='Заготовка',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Пул ОТК по заготовке',
                'verbose_name_plural': 'Пулы ОТК по заготовкам',
                'db_table': 'workshop_otk_blank_pools',
            },
        ),
        migrations.CreateModel(
            name='OtkAccountSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('consumed_kg', models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Списано с пула, кг')),
                ('defect_kg', models.DecimalField(decimal_places=6, default=0, max_digits=14, verbose_name='Брак, кг')),
                ('remaining_kg_after', models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Остаток пула после учёта, кг')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                (
                    'blank',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='otk_account_sessions',
                        to='workshop.workshopblank',
                        verbose_name='Заготовка',
                    ),
                ),
                (
                    'chemist',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='otk_sessions_as_chemist',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Химик',
                    ),
                ),
                (
                    'operator',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='otk_sessions_as_operator',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Оператор',
                    ),
                ),
                (
                    'packer',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='otk_sessions_as_packer',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Упаковщик',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Учёт ОТК',
                'verbose_name_plural': 'Учёты ОТК',
                'db_table': 'workshop_otk_account_sessions',
                'ordering': ('-created_at', '-pk'),
            },
        ),
        migrations.CreateModel(
            name='OtkBlankIntake',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kg', models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Кг')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                (
                    'blank',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='otk_intakes',
                        to='workshop.workshopblank',
                        verbose_name='Заготовка',
                    ),
                ),
                (
                    'run',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='otk_intakes',
                        to='workshop.blankproductionrun',
                        verbose_name='Партия производства',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Приход на ОТК',
                'verbose_name_plural': 'Приходы на ОТК',
                'db_table': 'workshop_otk_blank_intakes',
                'ordering': ('-created_at', '-pk'),
            },
        ),
        migrations.CreateModel(
            name='OtkAccountLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('profile_name_snapshot', models.CharField(max_length=255, verbose_name='Наименование профиля (снимок)')),
                ('pieces', models.PositiveIntegerField(verbose_name='Штук')),
                ('kg', models.DecimalField(decimal_places=6, max_digits=14, verbose_name='Кг')),
                (
                    'profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='otk_account_lines',
                        to='recipes.plasticprofile',
                        verbose_name='Профиль',
                    ),
                ),
                (
                    'session',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='lines',
                        to='workshop.otkaccountsession',
                        verbose_name='Сессия учёта',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Строка учёта ОТК',
                'verbose_name_plural': 'Строки учёта ОТК',
                'db_table': 'workshop_otk_account_lines',
            },
        ),
    ]
