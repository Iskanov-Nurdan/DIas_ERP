import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recipes', '0006_alter_recipe_output_quantity_and_more'),
        ('sales', '0013_commercial_flow'),
        ('warehouse', '0007_remove_warehousebatch_is_defect'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Client: кредитный лимит ────────────────────────────────────────
        migrations.AddField(
            model_name='client',
            name='credit_limit',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=16, null=True,
                verbose_name='Кредитный лимит',
            ),
        ),
        migrations.AddField(
            model_name='client',
            name='credit_limit_mode',
            field=models.CharField(
                choices=[('soft', 'Мягкое предупреждение'), ('hard', 'Жёсткая блокировка')],
                default='soft', max_length=10,
                verbose_name='Режим кредитного лимита',
            ),
        ),

        # ── OrderLine: резерв ──────────────────────────────────────────────
        migrations.AddField(
            model_name='orderline',
            name='reserved_quantity',
            field=models.DecimalField(
                decimal_places=4, default=0, max_digits=14,
                verbose_name='Зарезервировано',
            ),
        ),

        # ── SaleLine: прибыль строки ───────────────────────────────────────
        migrations.AddField(
            model_name='saleline',
            name='profit',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=16,
                verbose_name='Прибыль строки',
            ),
        ),

        # ── ReworkRequest: масса выхода, потери, коэффициент ──────────────
        migrations.AddField(
            model_name='reworkrequest',
            name='output_quantity_kg',
            field=models.DecimalField(
                blank=True, decimal_places=4, max_digits=14, null=True,
                verbose_name='Масса выхода кг (ГП)',
            ),
        ),
        migrations.AddField(
            model_name='reworkrequest',
            name='loss_kg',
            field=models.DecimalField(
                blank=True, decimal_places=4, max_digits=14, null=True,
                verbose_name='Потери кг',
            ),
        ),
        migrations.AddField(
            model_name='reworkrequest',
            name='conversion_rate',
            field=models.DecimalField(
                blank=True, decimal_places=6, max_digits=10, null=True,
                verbose_name='Коэффициент переработки (выход/вход)',
            ),
        ),

        # ── PriceList ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name='PriceList',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название прайса')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('valid_from', models.DateField(blank=True, null=True, verbose_name='Действует с')),
                ('valid_to', models.DateField(blank=True, null=True, verbose_name='Действует по')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
            ],
            options={
                'verbose_name': 'Прайс-лист',
                'verbose_name_plural': 'Прайс-листы',
                'db_table': 'price_lists',
                'ordering': ['-created_at'],
            },
        ),

        # ── ProductPrice ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='ProductPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product', models.CharField(blank=True, default='', max_length=255, verbose_name='Товар (текст)')),
                ('price', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='Цена')),
                ('unit', models.CharField(default='piece', max_length=20, verbose_name='Единица')),
                ('price_list', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='product_prices',
                    to='sales.pricelist',
                    verbose_name='Прайс',
                )),
                ('profile', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='product_prices',
                    to='recipes.plasticprofile',
                    verbose_name='Профиль',
                )),
            ],
            options={
                'verbose_name': 'Цена по прайсу',
                'verbose_name_plural': 'Цены по прайсу',
                'db_table': 'product_prices',
                'ordering': ['id'],
            },
        ),

        # ── ClientPrice ────────────────────────────────────────────────────
        migrations.CreateModel(
            name='ClientPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product', models.CharField(blank=True, default='', max_length=255, verbose_name='Товар (текст)')),
                ('price', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='Цена')),
                ('unit', models.CharField(default='piece', max_length=20, verbose_name='Единица')),
                ('valid_from', models.DateField(blank=True, null=True, verbose_name='Действует с')),
                ('valid_to', models.DateField(blank=True, null=True, verbose_name='Действует по')),
                ('comment', models.TextField(blank=True, default='', verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('client', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='client_prices',
                    to='sales.client',
                    verbose_name='Клиент',
                )),
                ('profile', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='client_prices',
                    to='recipes.plasticprofile',
                    verbose_name='Профиль',
                )),
            ],
            options={
                'verbose_name': 'Индивидуальная цена клиента',
                'verbose_name_plural': 'Индивидуальные цены клиентов',
                'db_table': 'client_prices',
                'ordering': ['-created_at'],
            },
        ),

        # ── OrderReservation ───────────────────────────────────────────────
        migrations.CreateModel(
            name='OrderReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Количество')),
                ('status', models.CharField(
                    choices=[('active', 'Активен'), ('released', 'Снят'), ('fulfilled', 'Исполнен (отгружен)')],
                    db_index=True, default='active', max_length=20, verbose_name='Статус',
                )),
                ('comment', models.TextField(blank=True, default='', verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_reservations',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Создал',
                )),
                ('order_line', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reservations',
                    to='sales.orderline',
                    verbose_name='Строка заявки',
                )),
                ('warehouse_batch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='order_reservations',
                    to='warehouse.warehousebatch',
                    verbose_name='Партия склада',
                )),
            ],
            options={
                'verbose_name': 'Резерв по заявке',
                'verbose_name_plural': 'Резервы по заявкам',
                'db_table': 'order_reservations',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='orderreservation',
            index=models.Index(fields=['order_line', 'status'], name='order_res_line_status_idx'),
        ),
        migrations.AddIndex(
            model_name='orderreservation',
            index=models.Index(fields=['warehouse_batch', 'status'], name='order_res_batch_status_idx'),
        ),
    ]
