from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F
from django.utils import timezone


def copy_incoming_to_batches(apps, schema_editor):
    Incoming = apps.get_model('materials', 'Incoming')
    MaterialBatch = apps.get_model('materials', 'MaterialBatch')
    from datetime import datetime, time

    for row in Incoming.objects.all().iterator():
        dt = row.date
        if dt:
            created_at = timezone.make_aware(datetime.combine(dt, time.min))
        else:
            created_at = timezone.now()
        mb = MaterialBatch.objects.create(
            material_id=row.material_id,
            quantity_initial=row.quantity,
            quantity_remaining=row.quantity,
            unit=row.unit or 'кг',
            unit_price=row.price_per_unit or 0,
            total_price=row.total_price or 0,
            supplier=row.supplier or '',
            supplier_batch=row.batch or '',
            comment=row.comment or '',
        )
        MaterialBatch.objects.filter(pk=mb.pk).update(created_at=created_at)


def fifo_apply_writeoffs(apps, schema_editor):
    Writeoff = apps.get_model('materials', 'MaterialWriteoff')
    Deduction = apps.get_model('materials', 'MaterialStockDeduction')
    MaterialBatch = apps.get_model('materials', 'MaterialBatch')

    for wo in Writeoff.objects.all().order_by('created_at', 'id'):
        need = Decimal(str(wo.quantity or 0))
        if need <= 0:
            continue
        mid = wo.material_id
        while need > 0:
            b = (
                MaterialBatch.objects.filter(material_id=mid, quantity_remaining__gt=0)
                .order_by('created_at', 'id')
                .first()
            )
            if b is None:
                raise RuntimeError(
                    f'Migration FIFO: недостаточно остатка material_id={mid}, writeoff id={wo.pk}'
                )
            br = Decimal(str(b.quantity_remaining or 0))
            take = min(br, need)
            up = Decimal(str(b.unit_price or 0))
            line_total = (take * up).quantize(Decimal('0.01'))
            Deduction.objects.create(
                batch_id=b.pk,
                quantity=take,
                unit_price=up,
                line_total=line_total,
                reason=wo.reason or '',
                reference_id=wo.reference_id,
            )
            MaterialBatch.objects.filter(pk=b.pk).update(
                quantity_remaining=F('quantity_remaining') - take
            )
            need -= take


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0005_rawmaterial_min_stock_is_active'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaterialBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_initial', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Начальное количество')),
                ('quantity_remaining', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Остаток по партии')),
                ('unit', models.CharField(default='кг', max_length=50, verbose_name='Единица')),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='Цена за единицу')),
                ('total_price', models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Сумма партии')),
                ('supplier', models.CharField(blank=True, max_length=255, verbose_name='Поставщик')),
                ('supplier_batch', models.CharField(blank=True, max_length=100, verbose_name='Номер партии поставщика')),
                ('comment', models.TextField(blank=True, verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                (
                    'material',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='batches',
                        to='materials.rawmaterial',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Партия сырья',
                'verbose_name_plural': 'Партии прихода сырья',
                'db_table': 'material_batches',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MaterialStockDeduction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Количество')),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='Цена партии (снимок)')),
                ('line_total', models.DecimalField(decimal_places=2, max_digits=16, verbose_name='Сумма строки')),
                ('reason', models.CharField(blank=True, max_length=100, verbose_name='Причина')),
                ('reference_id', models.PositiveIntegerField(blank=True, null=True, verbose_name='ID связи')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'batch',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='deductions',
                        to='materials.materialbatch',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Списание из партии',
                'verbose_name_plural': 'Списания из партий',
                'db_table': 'material_stock_deductions',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.RunPython(copy_incoming_to_batches, noop_reverse),
        migrations.RunPython(fifo_apply_writeoffs, noop_reverse),
        migrations.DeleteModel(
            name='MaterialWriteoff',
        ),
        migrations.DeleteModel(
            name='Incoming',
        ),
    ]
