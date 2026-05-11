# Generated manually

from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def forwards_shift_status(apps, schema_editor):
    Shift = apps.get_model('production', 'Shift')
    for s in Shift.objects.all().iterator():
        if s.closed_at is not None:
            s.status = 'closed'
            s.save(update_fields=['status'])


def forwards_production_batches(apps, schema_editor):
    PB = apps.get_model('production', 'ProductionBatch')
    Order = apps.get_model('production', 'Order')
    Recipe = apps.get_model('recipes', 'Recipe')
    for pb in PB.objects.all().iterator():
        q = Decimal(str(pb.quantity or 0))
        pb.pieces = 1
        pb.length_per_piece = q if q > 0 else Decimal('1')
        pb.total_meters = q if q > 0 else Decimal('0')
        pb.quantity = pb.total_meters
        if pb.order_id:
            o = Order.objects.filter(pk=pb.order_id).first()
            if o and getattr(o, 'recipe_id', None):
                r = Recipe.objects.filter(pk=o.recipe_id).first()
                if r:
                    if getattr(r, 'profile_id', None):
                        pb.profile_id = r.profile_id
                    pb.recipe_id = r.pk
                if getattr(o, 'line_id', None):
                    pb.line_id = o.line_id
        mc = getattr(pb, 'cost_price', None) or Decimal('0')
        pb.material_cost_total = mc
        pb.cost_per_meter = Decimal('0')
        pb.cost_per_piece = Decimal('0')
        if pb.total_meters and pb.total_meters > 0 and mc:
            pb.cost_per_meter = (mc / pb.total_meters).quantize(Decimal('0.0001'))
        if pb.pieces and int(pb.pieces) > 0 and mc:
            pb.cost_per_piece = (mc / Decimal(int(pb.pieces))).quantize(Decimal('0.0001'))
        pb.save()


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0018_shift_personal_vs_line_opens'),
        ('recipes', '0003_plastic_profile_per_meter'),
    ]

    operations = [
        migrations.AddField(
            model_name='shift',
            name='status',
            field=models.CharField(
                choices=[('open', 'Открыта'), ('paused', 'На паузе'), ('closed', 'Закрыта')],
                db_index=True,
                default='open',
                max_length=10,
                verbose_name='Статус',
            ),
        ),
        migrations.RunPython(forwards_shift_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='productionbatch',
            name='order',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='batches',
                to='production.order',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='comment',
            field=models.TextField(blank=True, verbose_name='Комментарий'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='cost_per_meter',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Себестоимость за м'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='cost_per_piece',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Себестоимость за шт'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='length_per_piece',
            field=models.DecimalField(decimal_places=4, default=1, max_digits=14, verbose_name='Длина штуки, м'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='line',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='production_batches',
                to='production.line',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='material_cost_total',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16, verbose_name='Материальная себестоимость'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='pieces',
            field=models.PositiveIntegerField(default=1, verbose_name='Штук'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='produced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Произведено'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='production_batches',
                to='recipes.plasticprofile',
                verbose_name='Профиль',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='recipe',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='production_batches',
                to='recipes.recipe',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='shift',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='production_batches',
                to='production.shift',
                verbose_name='Смена',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='total_meters',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=16, verbose_name='Всего метров'),
        ),
        migrations.AlterField(
            model_name='productionbatch',
            name='cost_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='Себестоимость (legacy)'),
        ),
        migrations.AlterField(
            model_name='productionbatch',
            name='product',
            field=models.CharField(max_length=255, verbose_name='Продукт (наименование)'),
        ),
        migrations.AlterField(
            model_name='productionbatch',
            name='quantity',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14, verbose_name='Количество (legacy = total_meters)'),
        ),
        migrations.RunPython(forwards_production_batches, migrations.RunPython.noop),
    ]
