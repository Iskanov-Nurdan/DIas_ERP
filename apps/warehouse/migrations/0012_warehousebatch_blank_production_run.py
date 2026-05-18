# Generated manually for GP stock consistency

from decimal import Decimal

from django.db import migrations, models


def link_gp_acceptance_batches_to_runs(apps, schema_editor):
    WarehouseBatch = apps.get_model('warehouse', 'WarehouseBatch')
    BlankProductionRun = apps.get_model('workshop', 'BlankProductionRun')
    for run in BlankProductionRun.objects.filter(gp_accepted_at__isnull=False).iterator():
        wb = (
            WarehouseBatch.objects.filter(
                blank_production_run__isnull=True,
                profile_id=run.product_id,
                inventory_form='unpacked',
                otk_checked_at=run.otk_recorded_at,
            )
            .order_by('id')
            .first()
        )
        if wb is None:
            continue
        if abs(Decimal(str(wb.quantity)) - Decimal(str(run.gp_accepted_pieces or 0))) > Decimal('0.0001'):
            continue
        WarehouseBatch.objects.filter(pk=wb.pk).update(blank_production_run_id=run.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0011_gp_pack_unit_warehouse_batch_and_sale_line'),
        ('workshop', '0001_workshop_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousebatch',
            name='blank_production_run',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='warehouse_gp_acceptance_batches',
                to='workshop.blankproductionrun',
                verbose_name='Приёмка ГП (строка неупакованного остатка)',
            ),
        ),
        migrations.RunPython(link_gp_acceptance_batches_to_runs, migrations.RunPython.noop),
    ]
