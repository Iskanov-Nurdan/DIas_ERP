"""Откат эффектов проведённого возврата при cancel."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.packaging import q4

from .models import DefectRecord, Return, ReturnLine, ReworkRequest


def rollback_return_document(ret_doc: Return) -> None:
    if ret_doc.status != Return.STATUS_COMPLETED:
        return
    for line in ret_doc.lines.all():
        _rollback_line(line, ret_doc)


def _rollback_line(line: ReturnLine, ret_doc: Return) -> None:
    if line.return_target == ReturnLine.TARGET_WAREHOUSE:
        wb = None
        if line.sale_line_id and line.sale_line.warehouse_batch_id:
            wb = line.sale_line.warehouse_batch
        else:
            wb = ret_doc.sale.warehouse_batch
        if wb:
            with transaction.atomic():
                w = WarehouseBatch.objects.select_for_update().get(pk=wb.pk)
                w.quantity = q4(Decimal(str(w.quantity)) - Decimal(str(line.quantity)))
                w.save(update_fields=['quantity'])
    elif line.return_target == ReturnLine.TARGET_DEFECT:
        DefectRecord.objects.filter(
            source_type=DefectRecord.SOURCE_RETURN,
            source_id=line.id,
        ).delete()
    elif line.return_target == ReturnLine.TARGET_REWORK:
        bid = line.rework_receipt_batch_id
        if bid:
            WarehouseBatch.objects.filter(pk=bid).delete()
        else:
            drs = DefectRecord.objects.filter(
                source_type=DefectRecord.SOURCE_RETURN,
                source_id=line.id,
            )
            for d in drs:
                ReworkRequest.objects.filter(defect_record=d).delete()
            drs.delete()
