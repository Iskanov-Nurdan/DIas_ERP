# Generated manually for rework input from defect (шт / кг).

from decimal import Decimal

from django.db import migrations, models


def backfill_rework_quantities(apps, schema_editor):
    ReworkRequest = apps.get_model('sales', 'ReworkRequest')
    DefectRecord = apps.get_model('sales', 'DefectRecord')
    for rw in ReworkRequest.objects.filter(defect_record_id__isnull=False).iterator(chunk_size=200):
        try:
            d = DefectRecord.objects.get(pk=rw.defect_record_id)
        except DefectRecord.DoesNotExist:
            continue
        pcs = Decimal(str(d.quantity_pcs or 0))
        kg_raw = d.quantity_kg
        kg = Decimal(str(kg_raw)) if kg_raw is not None else Decimal('0')
        upd = {}
        if pcs > 0:
            if rw.quantity_pcs is None or Decimal(str(rw.quantity_pcs or 0)) == 0:
                upd['quantity_pcs'] = pcs
            if (rw.quantity_kg is None or Decimal(str(rw.quantity_kg or 0)) == 0) and kg > 0:
                upd['quantity_kg'] = kg
        elif kg > 0:
            if rw.quantity_kg is None or Decimal(str(rw.quantity_kg or 0)) == 0:
                upd['quantity_kg'] = kg
        if upd:
            for k, v in upd.items():
                setattr(rw, k, v)
            ReworkRequest.objects.filter(pk=rw.pk).update(**upd)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0020_sale_draft_default_return_draft_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='reworkrequest',
            name='quantity_pcs',
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=14,
                null=True,
                verbose_name='Количество шт (вход с брака)',
            ),
        ),
        migrations.RunPython(backfill_rework_quantities, noop_reverse),
    ]
