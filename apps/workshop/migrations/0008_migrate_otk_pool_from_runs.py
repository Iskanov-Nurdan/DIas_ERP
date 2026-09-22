"""Перенос активных партий без учёта ОТК/приёмки в пул OtkBlankPool."""
from decimal import Decimal

from django.db import migrations


def migrate_active_runs_to_pool(apps, schema_editor):
    BlankProductionRun = apps.get_model('workshop', 'BlankProductionRun')
    OtkBlankPool = apps.get_model('workshop', 'OtkBlankPool')
    OtkBlankIntake = apps.get_model('workshop', 'OtkBlankIntake')

    runs = BlankProductionRun.objects.filter(
        otk_recorded_at__isnull=True,
        gp_accepted_at__isnull=True,
    ).exclude(blank_used_in_production_kg__lte=0)

    for run in runs.iterator():
        if OtkBlankIntake.objects.filter(run_id=run.pk).exists():
            continue
        kg = Decimal(str(run.blank_used_in_production_kg))
        pool, created = OtkBlankPool.objects.get_or_create(
            blank_id=run.blank_id,
            defaults={'remaining_kg': Decimal('0'), 'total_intake_kg': Decimal('0'), 'version': 0},
        )
        pool.remaining_kg = Decimal(str(pool.remaining_kg)) + kg
        pool.total_intake_kg = Decimal(str(pool.total_intake_kg)) + kg
        pool.save(update_fields=['remaining_kg', 'total_intake_kg'])
        OtkBlankIntake.objects.create(blank_id=run.blank_id, run_id=run.pk, kg=kg)


class Migration(migrations.Migration):

    dependencies = [
        ('workshop', '0007_otk_simplification'),
    ]

    operations = [
        migrations.RunPython(migrate_active_runs_to_pool, migrations.RunPython.noop),
    ]
