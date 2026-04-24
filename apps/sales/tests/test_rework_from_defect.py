"""ReworkRequest: количество с DefectRecord и поля API."""
from decimal import Decimal

from django.test import TestCase

from apps.sales.models import DefectRecord, ReworkRequest
from apps.sales.serializers import ReworkRequestSerializer, rework_quantities_from_defect_record


class ReworkQuantitiesFromDefectTests(TestCase):
    def test_pcs_priority(self):
        d = DefectRecord(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='60 мм белый',
            original_quantity_pcs=Decimal('2'),
            quantity_pcs=Decimal('2'),
            quantity_kg=None,
            defect_reason='трещина',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        d.save()
        m = rework_quantities_from_defect_record(d)
        self.assertEqual(m['quantity_pcs'], Decimal('2'))
        self.assertEqual(m['quantity_kg'], Decimal('0'))

    def test_kg_only(self):
        d = DefectRecord(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='X',
            original_quantity_pcs=Decimal('0'),
            quantity_pcs=Decimal('0'),
            quantity_kg=Decimal('1.5'),
            defect_reason='',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        d.save()
        m = rework_quantities_from_defect_record(d)
        self.assertIsNone(m['quantity_pcs'])
        self.assertEqual(m['quantity_kg'], Decimal('1.5'))

    def test_zero_raises(self):
        d = DefectRecord(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='X',
            original_quantity_pcs=Decimal('0'),
            quantity_pcs=Decimal('0'),
            quantity_kg=None,
            defect_reason='r',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        d.save()
        with self.assertRaises(ValueError):
            rework_quantities_from_defect_record(d)


class ReworkRequestSerializerCreateTests(TestCase):
    def test_create_sets_quantities_from_defect(self):
        d = DefectRecord.objects.create(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='60 мм белый',
            original_quantity_pcs=Decimal('2'),
            quantity_pcs=Decimal('2'),
            defect_reason='трещина',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        ser = ReworkRequestSerializer(data={'defect_record': d.pk})
        self.assertTrue(ser.is_valid(), ser.errors)
        rw = ser.save()
        rw.refresh_from_db()
        self.assertEqual(rw.quantity_pcs, Decimal('2'))
        self.assertEqual(rw.quantity_kg, Decimal('0'))

    def test_representation_has_display_fields(self):
        d = DefectRecord.objects.create(
            source_type=DefectRecord.SOURCE_WAREHOUSE,
            product='60 мм белый',
            original_quantity_pcs=Decimal('2'),
            quantity_pcs=Decimal('2'),
            defect_reason='трещина',
            status=DefectRecord.STATUS_ON_STOCK,
        )
        rw = ReworkRequest.objects.create(
            defect_record=d,
            product=d.product,
            quantity_pcs=Decimal('2'),
            quantity_kg=Decimal('0'),
            rework_number='RWK-2099-0001',
            status=ReworkRequest.STATUS_PENDING,
        )
        data = ReworkRequestSerializer(rw).data
        self.assertEqual(data['defect_record_id'], d.pk)
        self.assertEqual(data['defect_product_name'], '60 мм белый')
        self.assertEqual(data['defect_quantity_pcs'], '2')
        self.assertEqual(data['display_quantity'], '2')
        self.assertEqual(data['display_quantity_label'], '2 шт')
        self.assertEqual(data['defect_reason'], 'трещина')
        self.assertEqual(data['defect_source_type'], 'warehouse')
