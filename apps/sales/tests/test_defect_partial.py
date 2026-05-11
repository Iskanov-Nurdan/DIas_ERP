"""Частичные операции по DefectRecord (остаток и счётчики)."""
from decimal import Decimal

from django.test import TestCase

from apps.sales.models import DefectRecord, ReworkRequest
from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import apply_sale_to_warehouse_batch


def _mk_defect(pcs=Decimal('5')):
    return DefectRecord.objects.create(
        source_type=DefectRecord.SOURCE_MANUAL,
        product='Профиль',
        original_quantity_pcs=pcs,
        quantity_pcs=pcs,
        defect_reason='тест',
        status=DefectRecord.STATUS_ON_STOCK,
    )


class DefectPartialSellTests(TestCase):
    def test_sell_two_of_five_stays_on_stock(self):
        d = _mk_defect(Decimal('5'))
        d.sold_quantity_pcs += Decimal('2')
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('3'))
        self.assertEqual(d.sold_quantity_pcs, Decimal('2'))
        self.assertEqual(d.status, DefectRecord.STATUS_ON_STOCK)

    def test_sell_all_five_becomes_sold(self):
        d = _mk_defect(Decimal('5'))
        d.sold_quantity_pcs += Decimal('5')
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('0'))
        self.assertEqual(d.status, DefectRecord.STATUS_SOLD)


class DefectPartialWriteoffTests(TestCase):
    def test_writeoff_one_of_five(self):
        d = _mk_defect(Decimal('5'))
        d.written_off_quantity_pcs += Decimal('1')
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('4'))
        self.assertEqual(d.written_off_quantity_pcs, Decimal('1'))
        self.assertEqual(d.status, DefectRecord.STATUS_ON_STOCK)


class DefectPartialReworkTests(TestCase):
    def test_send_three_of_five_rework_and_remainder(self):
        d = _mk_defect(Decimal('5'))
        send_qty = Decimal('3')
        d.sent_to_rework_quantity_pcs += send_qty
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('2'))
        self.assertEqual(d.sent_to_rework_quantity_pcs, Decimal('3'))
        self.assertEqual(d.status, DefectRecord.STATUS_ON_STOCK)
        rw = ReworkRequest.objects.create(
            defect_record=d,
            product=d.product,
            quantity_pcs=send_qty,
            quantity_kg=Decimal('0'),
            rework_number='RWK-2099-0999',
            status=ReworkRequest.STATUS_PENDING,
        )
        self.assertEqual(rw.quantity_pcs, send_qty)


class DefectQtyBoundsViewLogicTests(TestCase):
    """Проверка условия qty > avail (как во view)."""

    def test_sell_exceeds_available(self):
        d = _mk_defect(Decimal('5'))
        avail = Decimal(str(d.quantity_pcs or 0))
        qty_d = Decimal('6')
        self.assertTrue(qty_d > avail + Decimal('0.0001'))


class DefectMixedDispositionTests(TestCase):
    def test_sell_two_writeoff_three_status_closed_not_on_stock(self):
        d = _mk_defect(Decimal('5'))
        d.sold_quantity_pcs = Decimal('2')
        d.written_off_quantity_pcs = Decimal('3')
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        d.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('0'))
        self.assertNotEqual(d.status, DefectRecord.STATUS_ON_STOCK)
        self.assertEqual(d.status, DefectRecord.STATUS_CLOSED)


class DefectWarehouseBatchSyncTests(TestCase):
    """Продажа + списание брака уменьшают quantity связанной партии склада."""

    def test_mixed_sell_writeoff_reduces_warehouse_batch_to_zero(self):
        wb = WarehouseBatch.objects.create(
            product='Профиль',
            quantity=Decimal('5'),
            date='2026-01-15',
            quality=WarehouseBatch.QUALITY_DEFECT,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )
        d = DefectRecord.objects.get(warehouse_batch=wb)
        self.assertEqual(d.original_quantity_pcs, Decimal('5'))
        self.assertEqual(d.quantity_pcs, Decimal('5'))
        sell_qty = Decimal('2')
        d.sold_quantity_pcs += sell_qty
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        apply_sale_to_warehouse_batch(wb.pk, sell_qty, wb.inventory_form, None)
        wb.refresh_from_db()
        self.assertEqual(wb.quantity, Decimal('3'))

        wo_qty = Decimal('3')
        rem = Decimal(str(d.quantity_pcs or 0))
        d.written_off_quantity_pcs += wo_qty
        d.recompute_remaining_pcs()
        d.apply_terminal_status_from_counters()
        d.save()
        apply_sale_to_warehouse_batch(wb.pk, wo_qty, wb.inventory_form, None)

        d.refresh_from_db()
        wb.refresh_from_db()
        self.assertEqual(d.quantity_pcs, Decimal('0'))
        self.assertEqual(d.status, DefectRecord.STATUS_CLOSED)
        self.assertEqual(wb.quantity, Decimal('0'))
