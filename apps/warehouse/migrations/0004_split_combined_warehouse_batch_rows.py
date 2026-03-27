from decimal import Decimal

from django.db import migrations

Q4 = Decimal('0.0001')


def q4(d):
    return Decimal(str(d)).quantize(Q4)


def forwards(apps, schema_editor):
    """
    Разделение строк open_package, где одновременно были целые упаковки и хвост
    (старая модель). После миграции: отдельная packed-строка и open_package только с хвостом.
    """
    WB = apps.get_model('warehouse', 'WarehouseBatch')
    qs = WB.objects.filter(inventory_form='open_package').order_by('id')
    for row in qs.iterator():
        pc = row.packages_count
        if pc is None or Decimal(str(pc)) < 1:
            continue
        ppp = row.pieces_per_package
        if ppp is None or Decimal(str(ppp)) <= 0:
            continue
        qty_d = q4(Decimal(str(row.quantity)))
        pc_d = q4(Decimal(str(pc)))
        ppp_d = q4(Decimal(str(ppp)))
        sealed_qty = q4(pc_d * ppp_d)
        open_tail = q4(qty_d - sealed_qty)
        if open_tail <= 0:
            row.inventory_form = 'packed'
            row.quantity = sealed_qty
            row.save(update_fields=['inventory_form', 'quantity'])
            continue
        WB.objects.create(
            product=row.product,
            quantity=sealed_qty,
            status=row.status,
            date=row.date,
            source_batch_id=row.source_batch_id,
            inventory_form='packed',
            unit_meters=row.unit_meters,
            package_total_meters=row.package_total_meters,
            pieces_per_package=row.pieces_per_package,
            packages_count=pc_d,
            otk_accepted=row.otk_accepted,
            otk_defect=row.otk_defect,
            otk_defect_reason=row.otk_defect_reason or '',
            otk_comment=row.otk_comment or '',
            otk_inspector_name=row.otk_inspector_name or '',
            otk_checked_at=row.otk_checked_at,
            otk_status=getattr(row, 'otk_status', None) or '',
        )
        row.packages_count = q4(Decimal('0'))
        row.quantity = open_tail
        row.save(update_fields=['packages_count', 'quantity'])


class Migration(migrations.Migration):
    dependencies = [
        ('warehouse', '0003_warehousebatch_inventory_form'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
