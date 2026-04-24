"""
Списание/откат склада по продаже: только по отгрузке (shipped+), idempotency через Sale.warehouse_stock_applied.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import apply_sale_to_warehouse_batch, reverse_apply_sale_to_warehouse_batch

from .models import Sale, SaleLine


def _is_shipping_status(status: str) -> bool:
    return status in (
        Sale.STATUS_PARTIALLY_SHIPPED,
        Sale.STATUS_SHIPPED,
        Sale.STATUS_CLOSED,
    )


def apply_warehouse_for_sale(sale: Sale) -> bool:
    """
    Списывает со склада при отгрузке. Возвращает True, если применяли операцию.
    """
    if sale.warehouse_stock_applied or not _is_shipping_status(sale.sale_status):
        return False
    if sale.sale_status == Sale.STATUS_CANCELED:
        return False

    lines = list(sale.sale_lines.all())
    if lines:
        return _apply_multiline(sale, lines)
    if sale.warehouse_batch_id:
        wb = sale.warehouse_batch
        if wb.quality == WarehouseBatch.QUALITY_DEFECT and not sale.is_defect_sale:
            raise ValueError('Обычная продажа не может списывать партию брака')
        apply_sale_to_warehouse_batch(
            sale.warehouse_batch_id,
            Decimal(str(sale.quantity)),
            sale.stock_form or '',
            sale.piece_pick or None,
        )
        with transaction.atomic():
            s2 = Sale.objects.select_for_update().get(pk=sale.pk)
            s2.warehouse_stock_applied = True
            s2.save(update_fields=['warehouse_stock_applied'])
        return True
    return False


def _apply_multiline(sale: Sale, lines: list[SaleLine]) -> bool:
    from django.db import transaction

    with transaction.atomic():
        sale_locked = Sale.objects.select_for_update().get(pk=sale.pk)
        if sale_locked.warehouse_stock_applied:
            return False
        for line in lines:
            if not line.warehouse_batch_id:
                continue
            wb = line.warehouse_batch
            if wb.quality == WarehouseBatch.QUALITY_DEFECT and not (sale.is_defect_sale or line.defect_flag):
                raise ValueError('Строка продажи ссылается на партию брака при обычной продаже')
            sf = (line.stock_form or '').strip() or (wb.inventory_form or '')
            apply_sale_to_warehouse_batch(
                line.warehouse_batch_id,
                Decimal(str(line.quantity)),
                sf,
                None,
            )
        sale_locked.warehouse_stock_applied = True
        sale_locked.save(update_fields=['warehouse_stock_applied'])
    return True


def reverse_warehouse_for_sale(sale: Sale) -> bool:
    if not sale.warehouse_stock_applied:
        return False
    lines = list(sale.sale_lines.all())
    if lines:
        for line in lines:
            if line.warehouse_batch_id:
                reverse_apply_sale_to_warehouse_batch(
                    line.warehouse_batch_id,
                    Decimal(str(line.quantity)),
                )
    elif sale.warehouse_batch_id:
        reverse_apply_sale_to_warehouse_batch(
            sale.warehouse_batch_id,
            Decimal(str(sale.quantity)),
        )
    with transaction.atomic():
        s2 = Sale.objects.select_for_update().get(pk=sale.pk)
        s2.warehouse_stock_applied = False
        s2.save(update_fields=['warehouse_stock_applied'])
    return True
