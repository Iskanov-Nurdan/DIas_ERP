# Partial defect ops: original + cumulative counters; quantity_pcs = remaining.

from decimal import Decimal

from django.db import migrations, models


def forwards(apps, schema_editor):
    DefectRecord = apps.get_model('sales', 'DefectRecord')
    for d in DefectRecord.objects.all().iterator(chunk_size=200):
        qty = Decimal(str(d.quantity_pcs or 0))
        sold = Decimal('0')
        written = Decimal('0')
        sent = Decimal('0')
        new_qty = qty
        st = d.status
        if st == 'sold':
            sold = qty
            new_qty = Decimal('0')
        elif st == 'written_off':
            written = qty
            new_qty = Decimal('0')
        elif st == 'sent_to_rework':
            sent = qty
            new_qty = Decimal('0')
        DefectRecord.objects.filter(pk=d.pk).update(
            original_quantity_pcs=qty,
            quantity_pcs=new_qty,
            sold_quantity_pcs=sold,
            written_off_quantity_pcs=written,
            sent_to_rework_quantity_pcs=sent,
        )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0021_reworkrequest_quantity_pcs'),
    ]

    operations = [
        migrations.AddField(
            model_name='defectrecord',
            name='original_quantity_pcs',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=14,
                null=True,
                verbose_name='Исходно шт (неизменяемая база)',
            ),
        ),
        migrations.AddField(
            model_name='defectrecord',
            name='sold_quantity_pcs',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=14,
                verbose_name='Продано шт (накопительно)',
            ),
        ),
        migrations.AddField(
            model_name='defectrecord',
            name='written_off_quantity_pcs',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=14,
                verbose_name='Списано шт (накопительно)',
            ),
        ),
        migrations.AddField(
            model_name='defectrecord',
            name='sent_to_rework_quantity_pcs',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=14,
                verbose_name='Отправлено в переделку шт (накопительно)',
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name='defectrecord',
            name='original_quantity_pcs',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                max_digits=14,
                verbose_name='Исходно шт (неизменяемая база)',
            ),
        ),
    ]
