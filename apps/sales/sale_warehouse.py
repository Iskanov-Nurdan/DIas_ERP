"""
Списание/откат склада по продаже: idempotency через Sale.warehouse_stock_applied
и JSON warehouse_mutation (полный reverse через snapshot).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import (
    apply_sale_to_warehouse_batch,
    reverse_apply_sale_to_warehouse_batch,
    reverse_warehouse_mutation,
)

from .models import Sale, SaleLine


def _is_shipping_status(status: str) -> bool:
    return status in (
        Sale.STATUS_PARTIALLY_SHIPPED,
        Sale.STATUS_SHIPPED,
        Sale.STATUS_CLOSED,
    )


def apply_warehouse_for_sale(sale: Sale) -> bool:
    if sale.warehouse_stock_applied or not _is_shipping_status(sale.sale_status):
        return False
    if sale.sale_status == Sale.STATUS_CANCELED:
        return False

    lines = list(sale.sale_lines.all())
    any_line_batch = bool(lines) and any(ln.warehouse_batch_id for ln in lines)

    if lines and any_line_batch:
        mutations: list = []
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
                qty_ln = Decimal(str(line.quantity or 0))
                if qty_ln <= 0:
                    continue
                sf = (line.stock_form or '').strip() or (wb.inventory_form or '')
                pp = (line.piece_pick or '').strip() or None
                mut = apply_sale_to_warehouse_batch(
                    line.warehouse_batch_id,
                    qty_ln,
                    sf,
                    pp,
                )
                mutations.append(mut)
            if not mutations:
                return False
            sale_locked.warehouse_stock_applied = True
            sale_locked.warehouse_mutation = mutations
            sale_locked.save(update_fields=['warehouse_stock_applied', 'warehouse_mutation'])
        return True

    if lines and (not any_line_batch) and sale.warehouse_batch_id:
        wb = sale.warehouse_batch
        if wb.quality == WarehouseBatch.QUALITY_DEFECT and not sale.is_defect_sale:
            raise ValueError('Обычная продажа не может списывать партию брака')
        mut = apply_sale_to_warehouse_batch(
            sale.warehouse_batch_id,
            Decimal(str(sale.quantity)),
            sale.stock_form or '',
            sale.piece_pick or None,
        )
        with transaction.atomic():
            s2 = Sale.objects.select_for_update().get(pk=sale.pk)
            if s2.warehouse_stock_applied:
                return False
            s2.warehouse_stock_applied = True
            s2.warehouse_mutation = [mut]
            s2.save(update_fields=['warehouse_stock_applied', 'warehouse_mutation'])
        return True

    if (not lines) and sale.warehouse_batch_id:
        wb = sale.warehouse_batch
        if wb.quality == WarehouseBatch.QUALITY_DEFECT and not sale.is_defect_sale:
            raise ValueError('Обычная продажа не может списывать партию брака')
        mut = apply_sale_to_warehouse_batch(
            sale.warehouse_batch_id,
            Decimal(str(sale.quantity)),
            sale.stock_form or '',
            sale.piece_pick or None,
        )
        with transaction.atomic():
            s2 = Sale.objects.select_for_update().get(pk=sale.pk)
            s2.warehouse_stock_applied = True
            s2.warehouse_mutation = [mut]
            s2.save(update_fields=['warehouse_stock_applied', 'warehouse_mutation'])
        return True
    return False


def reverse_warehouse_for_sale(sale: Sale) -> bool:
    if not sale.warehouse_stock_applied:
        return False
    with transaction.atomic():
        if sale.warehouse_mutation:
            for m in reversed(sale.warehouse_mutation):
                reverse_warehouse_mutation(m)
        else:
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
        s2 = Sale.objects.select_for_update().get(pk=sale.pk)
        s2.warehouse_stock_applied = False
        s2.warehouse_mutation = None
        s2.save(update_fields=['warehouse_stock_applied', 'warehouse_mutation'])
    return True
