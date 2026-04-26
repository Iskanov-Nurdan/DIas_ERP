"""
Создание и синхронизация DefectRecord для партий склада quality=defect.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.packaging import q4

from .models import DefectRecord


def ensure_defect_record_for_defect_batch(batch: WarehouseBatch) -> DefectRecord | None:
    """
    Для WarehouseBatch с quality=defect гарантирует ровно одну DefectRecord (1:1).
    Не дублирует при повторных save().
    """
    if batch.quality != WarehouseBatch.QUALITY_DEFECT:
        return None

    with transaction.atomic():
        existing = (
            DefectRecord.objects.select_for_update()
            .filter(warehouse_batch_id=batch.pk)
            .first()
        )
        if existing:
            return existing

        q = q4(Decimal(str(batch.quantity or 0)))
        dr = DefectRecord.objects.create(
            source_type=DefectRecord.SOURCE_WAREHOUSE,
            source_id=batch.pk,
            warehouse_batch=batch,
            profile_id=batch.profile_id,
            product=batch.product,
            original_quantity_pcs=q,
            quantity_pcs=q,
            defect_reason=(batch.defect_reason or '')[:2000] if (batch.defect_reason or '').strip() else 'Брак со склада',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        return dr
