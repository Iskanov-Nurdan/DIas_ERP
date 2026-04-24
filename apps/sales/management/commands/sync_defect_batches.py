from django.core.management.base import BaseCommand

from apps.warehouse.models import WarehouseBatch
from apps.sales.defect_service import ensure_defect_record_for_defect_batch
from apps.sales.models import DefectRecord


class Command(BaseCommand):
    help = 'Создать DefectRecord для существующих WarehouseBatch quality=defect без записи брака.'

    def handle(self, *args, **options):
        n = 0
        for wb in WarehouseBatch.objects.filter(quality=WarehouseBatch.QUALITY_DEFECT).iterator():
            if not DefectRecord.objects.filter(warehouse_batch=wb).exists():
                ensure_defect_record_for_defect_batch(wb)
                n += 1
        self.stdout.write(self.style.SUCCESS(f'Создано записей: {n}'))
