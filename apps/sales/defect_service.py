"""
Создание и синхронизация DefectRecord для партий склада quality=defect.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.packaging import q4

from .models import DefectRecord


def create_defect_split_from_good_batch(
    *,
    source_batch: WarehouseBatch,
    quantity_pcs: Decimal,
    defect_reason: str,
) -> DefectRecord:
    """
    Списывает quantity_pcs с партии quality=good и создаёт новую строку склада quality=defect.
    DefectRecord создаётся сигналом post_save на WarehouseBatch.
    """
    qp = q4(Decimal(str(quantity_pcs)))
    if qp <= 0:
        raise ValueError('quantity_pcs должно быть > 0')

    reason = (defect_reason or '').strip()
    if not reason:
        raise ValueError('Укажите defect_reason')

    with transaction.atomic():
        src = WarehouseBatch.objects.select_for_update().get(pk=source_batch.pk)
        if src.quality != WarehouseBatch.QUALITY_GOOD:
            raise ValueError('Создание брака из этой формы доступно только для партии с качеством good')
        avail = q4(Decimal(str(src.quantity or 0)))
        if qp > avail:
            raise ValueError(f'Недостаточно остатка на партии (доступно {avail})')

        src.quantity = q4(avail - qp)
        src.save(update_fields=['quantity'])

        new_wb = WarehouseBatch.objects.create(
            profile_id=src.profile_id,
            product=src.product,
            length_per_piece=src.length_per_piece,
            cost_per_piece=src.cost_per_piece,
            cost_per_meter=src.cost_per_meter,
            quantity=qp,
            status=WarehouseBatch.STATUS_AVAILABLE,
            date=timezone.now().date(),
            source_batch=src.source_batch,
            inventory_form=src.inventory_form,
            unit_meters=src.unit_meters,
            package_total_meters=src.package_total_meters,
            pieces_per_package=src.pieces_per_package,
            packages_count=src.packages_count,
            quality=WarehouseBatch.QUALITY_DEFECT,
            defect_reason=reason[:2000],
            stock_bucket=src.stock_bucket,
            otk_accepted=src.otk_accepted,
            otk_defect=src.otk_defect,
            otk_defect_reason=src.otk_defect_reason or '',
            otk_comment=src.otk_comment or '',
            otk_inspector_name=src.otk_inspector_name or '',
            otk_checked_at=src.otk_checked_at,
            otk_status=(src.otk_status or '')[:20],
        )

    dr = DefectRecord.objects.filter(warehouse_batch_id=new_wb.pk).first()
    if dr is None:
        dr = ensure_defect_record_for_defect_batch(new_wb)
    if dr is None:
        raise RuntimeError('Не удалось создать DefectRecord для партии брака')
    return dr


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
