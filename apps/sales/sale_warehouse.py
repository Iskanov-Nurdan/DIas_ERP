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


def _schedule_sale_warehouse_ws_push(sale_pk: int, *, gp_pack_unit_ids: list[int] | None = None) -> None:
    """После коммита: refetch продажи и затронутых партий на других экранах."""
    from django.db import transaction as db_txn

    from apps.realtime.broadcast import schedule_push

    gp_ids = list(gp_pack_unit_ids or [])

    def _push():
        sale_fresh = Sale.objects.filter(pk=sale_pk).first()
        if not sale_fresh:
            return
        schedule_push(resource='sale', action='changed', entity_id=sale_pk)
        schedule_push(resource='sale', action='changed')
        wb_ids = list(
            SaleLine.objects.filter(sale_id=sale_pk, warehouse_batch_id__isnull=False)
            .values_list('warehouse_batch_id', flat=True)
            .distinct()
        )
        for wid in wb_ids:
            schedule_push(resource='warehouse_batch', action='changed', entity_id=wid)
        schedule_push(resource='warehouse_batch', action='changed')
        for gid in gp_ids:
            schedule_push(resource='warehouse_package', action='changed', entity_id=gid)
        if gp_ids:
            schedule_push(resource='warehouse_package', action='changed')

    db_txn.on_commit(_push)


def _is_shipping_status(status: str) -> bool:
    return status in (
        Sale.STATUS_PARTIALLY_SHIPPED,
        Sale.STATUS_SHIPPED,
        Sale.STATUS_CLOSED,
    )


def apply_warehouse_for_sale(sale: Sale) -> bool:
    if sale.warehouse_stock_applied:
        return False
    if sale.sale_status == Sale.STATUS_CANCELED:
        return False

    lines = list(
        sale.sale_lines.select_related('warehouse_batch', 'gp_pack_unit').order_by('id')
    )
    any_line_batch = bool(lines) and any(ln.warehouse_batch_id for ln in lines)

    if lines and any_line_batch:
        mutations: list = []
        gp_pack_ids: list[int] = []
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
                if line.gp_pack_unit_id:
                    gp_pack_ids.append(line.gp_pack_unit_id)
            if not mutations:
                return False
            sale_locked.warehouse_stock_applied = True
            sale_locked.warehouse_mutation = mutations
            sale_locked.save(update_fields=['warehouse_stock_applied', 'warehouse_mutation'])
            sale_pk = sale_locked.pk
        _schedule_sale_warehouse_ws_push(sale_pk, gp_pack_unit_ids=gp_pack_ids)
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
            sale_pk = s2.pk
        _schedule_sale_warehouse_ws_push(sale_pk, gp_pack_unit_ids=[])
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
            sale_pk = s2.pk
        _schedule_sale_warehouse_ws_push(sale_pk, gp_pack_unit_ids=[])
        return True
    return False


def sale_requires_warehouse_apply(sale: Sale) -> bool:
    """Нужно ли списание склада по этой продаже (есть строки/шапка с партией)."""
    if sale.warehouse_batch_id:
        return True
    return SaleLine.objects.filter(sale_id=sale.pk, warehouse_batch_id__isnull=False).exists()


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
        sale_pk = s2.pk
    gp_ids = list(
        SaleLine.objects.filter(sale_id=sale_pk, gp_pack_unit_id__isnull=False)
        .values_list('gp_pack_unit_id', flat=True)
        .distinct()
    )
    _schedule_sale_warehouse_ws_push(sale_pk, gp_pack_unit_ids=gp_ids)
    return True
