from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def forwards_wh(apps, schema_editor):
    WB = apps.get_model('warehouse', 'WarehouseBatch')
    PB = apps.get_model('production', 'ProductionBatch')
    for row in WB.objects.all().iterator():
        pbatch = PB.objects.filter(pk=row.source_batch_id).first() if row.source_batch_id else None
        if pbatch:
            if getattr(pbatch, 'profile_id', None):
                row.profile_id = pbatch.profile_id
            row.length_per_piece = pbatch.length_per_piece
            row.cost_per_piece = getattr(pbatch, 'cost_per_piece', None) or Decimal('0')
            row.cost_per_meter = getattr(pbatch, 'cost_per_meter', None) or Decimal('0')
        q = Decimal(str(row.quantity or 0))
        lp = Decimal(str(row.length_per_piece or 0))
        if row.length_per_piece is not None:
            row.total_meters = (q * lp).quantize(Decimal('0.0001'))
        row.save(
            update_fields=['profile_id', 'length_per_piece', 'total_meters', 'cost_per_piece', 'cost_per_meter']
        )


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0004_split_combined_warehouse_batch_rows'),
        ('production', '0019_plastic_batch_and_shift_status'),
        ('recipes', '0003_plastic_profile_per_meter'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='cost_per_meter',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Себестоимость м'),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='cost_per_piece',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Себестоимость шт'),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='length_per_piece',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True, verbose_name='Длина штуки, м'),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='warehouse_batches',
                to='recipes.plasticprofile',
                verbose_name='Профиль',
            ),
        ),
        migrations.AddField(
            model_name='warehousebatch',
            name='total_meters',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=16, null=True, verbose_name='Всего м'),
        ),
        migrations.AlterField(
            model_name='warehousebatch',
            name='quantity',
            field=models.DecimalField(decimal_places=4, max_digits=14, verbose_name='Штук доступно'),
        ),
        migrations.RunPython(forwards_wh, migrations.RunPython.noop),
    ]
