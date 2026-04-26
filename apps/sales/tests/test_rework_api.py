from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import DefectRecord, ReworkRequest
from apps.warehouse.models import WarehouseBatch


class ReworkApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='rework-admin@example.com',
            password='pass12345',
            name='Rework Admin',
        )
        self.client.force_authenticate(self.user)
        self.defect = DefectRecord.objects.create(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='60 мм белый',
            original_quantity_pcs=Decimal('5'),
            quantity_pcs=Decimal('5'),
            defect_reason='тест',
            status=DefectRecord.STATUS_ON_STOCK,
        )

    def _create_rework(self):
        resp = self.client.post('/api/rework-requests/', data={'defect_record': self.defect.pk, 'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        return ReworkRequest.objects.get(pk=resp.data['id'])

    def test_create_requires_defect_record(self):
        resp = self.client.post('/api/rework-requests/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_quantity_from_defect_and_manual_qty_ignored(self):
        resp = self.client.post(
            '/api/rework-requests/',
            data={'defect_record': self.defect.pk, 'quantity_pcs': '999'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        rw = ReworkRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(rw.quantity_pcs, Decimal('5'))

    def test_start_pending_to_in_progress(self):
        rw = self._create_rework()
        resp = self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rw.refresh_from_db()
        self.assertEqual(rw.status, ReworkRequest.STATUS_IN_PROGRESS)

    def test_start_completed_or_canceled_forbidden(self):
        rw = self._create_rework()
        rw.status = ReworkRequest.STATUS_COMPLETED
        rw.save(update_fields=['status'])
        resp = self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_complete_requires_fields_and_non_negative_values(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        missing = self.client.post(f'/api/rework-requests/{rw.pk}/complete/', data={'quality': 'good'}, format='json')
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing.data.get('code'), 'MISSING_FIELDS')
        neg = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '-1', 'loss_quantity': '0', 'quality': 'good'},
            format='json',
        )
        self.assertEqual(neg.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(neg.data.get('code'), 'NEGATIVE_QUANTITY')

    def test_complete_rejects_bounds_and_quality(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        bounds = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '4', 'loss_quantity': '2', 'quality': 'good'},
            format='json',
        )
        self.assertEqual(bounds.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(bounds.data.get('code'), 'QTY_BOUNDS')
        bad_quality = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '2', 'loss_quantity': '1', 'quality': 'bad'},
            format='json',
        )
        self.assertEqual(bad_quality.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(bad_quality.data.get('code'), 'INVALID_QUALITY')

    def test_complete_good_creates_good_batch(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        resp = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '2', 'loss_quantity': '1', 'quality': 'good'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rw.refresh_from_db()
        self.assertEqual(rw.status, ReworkRequest.STATUS_COMPLETED)
        self.assertIsNotNone(rw.result_warehouse_batch_id)
        self.assertEqual(rw.result_warehouse_batch.quality, WarehouseBatch.QUALITY_GOOD)

    def test_complete_defect_creates_defect_batch_and_defect_record(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        before = DefectRecord.objects.count()
        resp = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '2', 'loss_quantity': '0', 'quality': 'defect'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rw.refresh_from_db()
        self.assertEqual(rw.result_warehouse_batch.quality, WarehouseBatch.QUALITY_DEFECT)
        self.assertGreater(DefectRecord.objects.count(), before)

    def test_repeated_complete_forbidden(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '2', 'loss_quantity': '1', 'quality': 'good'},
            format='json',
        )
        repeat = self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '1', 'loss_quantity': '0', 'quality': 'good'},
            format='json',
        )
        self.assertEqual(repeat.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(repeat.data.get('code'), 'REWORK_ALREADY_COMPLETED')

    def test_cancel_pending_and_in_progress_success(self):
        rw1 = self._create_rework()
        c1 = self.client.post(f'/api/rework-requests/{rw1.pk}/cancel/', data={}, format='json')
        self.assertEqual(c1.status_code, status.HTTP_200_OK)
        rw2 = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw2.pk}/start/', data={}, format='json')
        c2 = self.client.post(f'/api/rework-requests/{rw2.pk}/cancel/', data={}, format='json')
        self.assertEqual(c2.status_code, status.HTTP_200_OK)

    def test_cancel_completed_and_repeated_cancel_forbidden(self):
        rw = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw.pk}/start/', data={}, format='json')
        self.client.post(
            f'/api/rework-requests/{rw.pk}/complete/',
            data={'output_quantity': '2', 'loss_quantity': '1', 'quality': 'good'},
            format='json',
        )
        bad = self.client.post(f'/api/rework-requests/{rw.pk}/cancel/', data={}, format='json')
        self.assertEqual(bad.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(bad.data.get('code'), 'REWORK_ALREADY_COMPLETED')

        rw2 = self._create_rework()
        self.client.post(f'/api/rework-requests/{rw2.pk}/cancel/', data={}, format='json')
        repeat = self.client.post(f'/api/rework-requests/{rw2.pk}/cancel/', data={}, format='json')
        self.assertEqual(repeat.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(repeat.data.get('code'), 'REWORK_ALREADY_CANCELED')

    def test_cancel_returns_quantity_to_defect(self):
        self.defect.sent_to_rework_quantity_pcs = Decimal('2')
        self.defect.recompute_remaining_pcs()
        self.defect.status = DefectRecord.STATUS_SENT_TO_REWORK
        self.defect.save(update_fields=['sent_to_rework_quantity_pcs', 'quantity_pcs', 'status', 'updated_at'])
        rw = ReworkRequest.objects.create(
            defect_record=self.defect,
            product=self.defect.product,
            quantity_pcs=Decimal('2'),
            quantity_kg=Decimal('0'),
            rework_number='RWK-2026-0001',
            status=ReworkRequest.STATUS_IN_PROGRESS,
        )
        resp = self.client.post(f'/api/rework-requests/{rw.pk}/cancel/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.defect.refresh_from_db()
        self.assertEqual(self.defect.sent_to_rework_quantity_pcs, Decimal('0'))
        self.assertEqual(self.defect.quantity_pcs, Decimal('5'))
        self.assertEqual(self.defect.status, DefectRecord.STATUS_ON_STOCK)

    def test_delete_disabled(self):
        rw = self._create_rework()
        resp = self.client.delete(f'/api/rework-requests/{rw.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'DELETE_DISABLED')

    def test_select_sources_only_available_defects_with_display_and_available(self):
        DefectRecord.objects.create(
            source_type=DefectRecord.SOURCE_MANUAL,
            product='Closed defect',
            original_quantity_pcs=Decimal('1'),
            quantity_pcs=Decimal('0'),
            sold_quantity_pcs=Decimal('1'),
            status=DefectRecord.STATUS_SOLD,
        )
        resp = self.client.get('/api/rework-requests/select-sources/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data['defect_records']
        ids = {x['id'] for x in rows}
        self.assertIn(self.defect.pk, ids)
        for row in rows:
            self.assertIn('display_quantity_label', row)
            self.assertIn('available_quantity_pcs', row)

    def test_patch_rework_update_forbidden(self):
        rw = self._create_rework()
        resp = self.client.patch(
            f'/api/rework-requests/{rw.pk}/',
            data={'status': ReworkRequest.STATUS_COMPLETED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'REWORK_UPDATE_FORBIDDEN')
