from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.warehouse.models import WarehouseBatch

from .defect_service import ensure_defect_record_for_defect_batch


@receiver(post_save, sender=WarehouseBatch)
def warehouse_batch_sync_defect_record(sender, instance: WarehouseBatch, **kwargs):
    if instance.quality == WarehouseBatch.QUALITY_DEFECT:
        ensure_defect_record_for_defect_batch(instance)
