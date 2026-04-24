"""Backend ERP fixes — базовые проверки (расширяйте сценариями)."""
from decimal import Decimal

from django.test import TestCase

from apps.warehouse.models import WarehouseBatch
from apps.sales.models import DefectRecord, Return
from apps.sales.defect_service import ensure_defect_record_for_defect_batch
from apps.sales.sale_warehouse import _is_shipping_status
from apps.sales.models import Sale


class DefectAndSaleHelpersTests(TestCase):
    def test_ensure_defect_creates_for_defect_batch(self):
        wb = WarehouseBatch.objects.create(
            product='Test',
            quantity=Decimal('10'),
            date='2026-01-01',
            quality=WarehouseBatch.QUALITY_DEFECT,
        )
        dr = ensure_defect_record_for_defect_batch(wb)
        self.assertIsNotNone(dr)
        self.assertEqual(dr.warehouse_batch_id, wb.pk)
        self.assertEqual(dr.status, DefectRecord.STATUS_ON_STOCK)
        dr2 = ensure_defect_record_for_defect_batch(wb)
        self.assertEqual(dr.pk, dr2.pk)

    def test_shipping_status_helper(self):
        self.assertTrue(_is_shipping_status(Sale.STATUS_SHIPPED))
        self.assertFalse(_is_shipping_status(Sale.STATUS_DRAFT))


class ReturnModelTests(TestCase):
    def test_return_status_choices(self):
        self.assertEqual(Return.STATUS_COMPLETED, 'completed')
