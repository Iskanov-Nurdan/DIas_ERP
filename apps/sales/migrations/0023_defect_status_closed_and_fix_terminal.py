# Пересчёт остатка и финальный статус (в т.ч. closed при смешанных операциях).

from django.db import migrations


def forwards(apps, schema_editor):
    from apps.sales.models import DefectRecord

    for d in DefectRecord.objects.all().iterator():
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save(update_fields=['quantity_pcs', 'status', 'updated_at'])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0022_defect_partial_quantities'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
