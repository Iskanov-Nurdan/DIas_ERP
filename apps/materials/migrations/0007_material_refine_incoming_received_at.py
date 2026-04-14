from django.db import migrations, models
from django.utils import timezone


def fill_received_at(apps, schema_editor):
    MaterialBatch = apps.get_model('materials', 'MaterialBatch')
    for row in MaterialBatch.objects.all():
        row.received_at = row.created_at or timezone.now()
        row.save(update_fields=['received_at'])


def normalize_units(apps, schema_editor):
    RawMaterial = apps.get_model('materials', 'RawMaterial')
    MaterialBatch = apps.get_model('materials', 'MaterialBatch')
    for m in RawMaterial.objects.all():
        u = (m.unit or '').strip().lower()
        if u in ('кг', 'kg'):
            nu = 'kg'
        elif u in ('г', 'g', 'гр'):
            nu = 'g'
        else:
            nu = 'kg'
        if m.unit != nu:
            m.unit = nu
            m.save(update_fields=['unit'])
    for b in MaterialBatch.objects.all():
        u = (b.unit or '').strip().lower()
        if u in ('кг', 'kg'):
            nu = 'kg'
        elif u in ('г', 'g', 'гр'):
            nu = 'g'
        else:
            nu = 'kg'
        if b.unit != nu:
            b.unit = nu
            b.save(update_fields=['unit'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('materials', '0006_material_batch_fifo'),
    ]

    operations = [
        migrations.RenameField(
            model_name='rawmaterial',
            old_name='min_stock',
            new_name='min_balance',
        ),
        migrations.RenameField(
            model_name='materialbatch',
            old_name='supplier',
            new_name='supplier_name',
        ),
        migrations.RenameField(
            model_name='materialbatch',
            old_name='supplier_batch',
            new_name='supplier_batch_number',
        ),
        migrations.AddField(
            model_name='materialbatch',
            name='received_at',
            field=models.DateTimeField(null=True, verbose_name='Дата прихода'),
        ),
        migrations.RunPython(fill_received_at, noop_reverse),
        migrations.AlterField(
            model_name='materialbatch',
            name='received_at',
            field=models.DateTimeField(verbose_name='Дата прихода'),
        ),
        migrations.AlterField(
            model_name='materialbatch',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, verbose_name='Запись создана'),
        ),
        migrations.RunPython(normalize_units, noop_reverse),
    ]
