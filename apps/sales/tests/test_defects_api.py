from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client, DefectRecord, ReworkRequest, Return, ReturnLine, Sale, SaleLine
from apps.warehouse.models import WarehouseBatch


class DefectsApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='defects-admin@example.com',
            password='pass12345',
            name='Defects Admin',
        )
        self.client.force_authenticate(self.user)
        self.active_client = Client.objects.create(name='Active', is_active=True)
        self.inactive_client = Client.objects.create(name='Inactive', is_active=False)
        self.wb_good = WarehouseBatch.objects.create(
            product='60 мм годный',
            quantity=Decimal('10'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )
        self.wb = WarehouseBatch.objects.create(
            product='60 мм белый',
            quantity=Decimal('10'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_DEFECT,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )
        self.defect = DefectRecord.objects.get(warehouse_batch=self.wb)

    def test_create_from_good_warehouse_batch_splits_stock(self):
        resp = self.client.post(
            '/api/defects/',
            data={
                'source_type': DefectRecord.SOURCE_WAREHOUSE,
                'warehouse_batch': self.wb_good.pk,
                'quantity_pcs': '3',
                'defect_reason': 'Повреждение',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.wb_good.refresh_from_db()
        self.assertEqual(self.wb_good.quantity, Decimal('7'))

    def test_create_manual_defect_rejected(self):
        resp = self.client.post(
            '/api/defects/',
            data={
                'source_type': DefectRecord.SOURCE_MANUAL,
                'product': 'Ручной профиль',
                'quantity_pcs': '5',
                'defect_reason': 'Ручное добавление',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'INVALID_SOURCE_TYPE')

    def test_create_manual_defect_without_quantity_error(self):
        resp = self.client.post(
            '/api/defects/',
            data={
                'source_type': DefectRecord.SOURCE_MANUAL,
                'product': 'Ручной профиль',
                'defect_reason': 'Ручное добавление',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_manual_defect_non_positive_quantity_error(self):
        resp = self.client.post(
            '/api/defects/',
            data={
                'source_type': DefectRecord.SOURCE_MANUAL,
                'product': 'Ручной профиль',
                'quantity_pcs': '0',
                'defect_reason': 'Ручное добавление',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_warehouse_defect_batch_rejected(self):
        resp = self.client.post(
            '/api/defects/',
            data={
                'source_type': DefectRecord.SOURCE_WAREHOUSE,
                'warehouse_batch': self.wb.pk,
                'quantity_pcs': '1',
                'defect_reason': 'duplicate',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_select_sources_excludes_already_linked_batch_and_has_available_quantity(self):
        wb2 = WarehouseBatch.objects.create(
            product='80 мм',
            quantity=Decimal('7'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_DEFECT,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )
        DefectRecord.objects.filter(warehouse_batch=wb2).delete()
        resp = self.client.get('/api/defects/select-sources/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {x['id'] for x in resp.data['warehouse_defect_batches']}
        self.assertIn(wb2.pk, ids)
        self.assertNotIn(self.wb.pk, ids)
        row = next(x for x in resp.data['warehouse_defect_batches'] if x['id'] == wb2.pk)
        self.assertIn('available_quantity_pcs', row)

    def test_sell_requires_client(self):
        resp = self.client.post(f'/api/defects/{self.defect.pk}/sell/', data={'quantity': '1', 'price': '10'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'MISSING_CLIENT')

    def test_sell_rejects_inactive_client(self):
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.inactive_client.pk, 'quantity': '1', 'price': '10'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INACTIVE_CLIENT')

    def test_sell_rejects_invalid_quantity(self):
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.active_client.pk, 'quantity': '0', 'price': '10'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INVALID_QUANTITY')

    def test_sell_rejects_quantity_exceeded(self):
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.active_client.pk, 'quantity': '100', 'price': '10'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'QUANTITY_EXCEEDED')

    def test_sell_rejects_invalid_price(self):
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.active_client.pk, 'quantity': '1', 'price': '0'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INVALID_PRICE')

    def test_sell_success_creates_sale_and_sale_line_and_updates_counters(self):
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.active_client.pk, 'quantity': '2', 'price': '100'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sale = Sale.objects.get(pk=resp.data['sale_id'])
        self.assertTrue(sale.is_defect_sale)
        self.assertTrue(SaleLine.objects.filter(sale=sale).exists())
        self.wb.refresh_from_db()
        self.defect.refresh_from_db()
        self.assertEqual(self.wb.quantity, Decimal('8'))
        self.assertEqual(self.defect.sold_quantity_pcs, Decimal('2'))

    def test_mixed_sell_and_writeoff_becomes_closed(self):
        self.client.post(
            f'/api/defects/{self.defect.pk}/sell/',
            data={'client_id': self.active_client.pk, 'quantity': '2', 'price': '100'},
            format='json',
        )
        resp = self.client.post(
            f'/api/defects/{self.defect.pk}/writeoff/',
            data={'quantity': '8', 'writeoff_reason': 'Невозможно восстановить'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.defect.refresh_from_db()
        self.assertEqual(self.defect.status, DefectRecord.STATUS_CLOSED)

    def test_writeoff_rules(self):
        missing_reason = self.client.post(f'/api/defects/{self.defect.pk}/writeoff/', data={'quantity': '1'}, format='json')
        self.assertEqual(missing_reason.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing_reason.data.get('code'), 'MISSING_REASON')
        bad_qty = self.client.post(
            f'/api/defects/{self.defect.pk}/writeoff/',
            data={'quantity': '0', 'writeoff_reason': 'bad'},
            format='json',
        )
        self.assertEqual(bad_qty.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(bad_qty.data.get('code'), 'INVALID_QUANTITY')
        full = self.client.post(
            f'/api/defects/{self.defect.pk}/writeoff/',
            data={'writeoff_reason': 'full'},
            format='json',
        )
        self.assertEqual(full.status_code, status.HTTP_200_OK)

    def test_send_to_rework_rules(self):
        bad = self.client.post(f'/api/defects/{self.defect.pk}/send-to-rework/', data={'quantity': '0'}, format='json')
        self.assertEqual(bad.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(bad.data.get('code'), 'INVALID_QUANTITY')
        over = self.client.post(f'/api/defects/{self.defect.pk}/send-to-rework/', data={'quantity': '99'}, format='json')
        self.assertEqual(over.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(over.data.get('code'), 'QTY_TOO_HIGH')
        ok = self.client.post(f'/api/defects/{self.defect.pk}/send-to-rework/', data={'quantity': '3'}, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.defect.refresh_from_db()
        self.assertEqual(self.defect.sent_to_rework_quantity_pcs, Decimal('3'))
        blocked = self.client.post(f'/api/defects/{self.defect.pk}/send-to-rework/', data={'quantity': '1'}, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(blocked.data.get('code'), 'REWORK_ACTIVE')
        self.assertTrue(ReworkRequest.objects.filter(defect_record=self.defect).exists())

    def test_complete_rework_on_defect_disabled(self):
        resp = self.client.post(f'/api/defects/{self.defect.pk}/complete-rework/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'USE_REWORK_COMPLETE')

    def test_delete_disabled(self):
        resp = self.client.delete(f'/api/defects/{self.defect.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'DELETE_DISABLED')

    def test_patch_forbids_status_and_counters(self):
        resp = self.client.patch(
            f'/api/defects/{self.defect.pk}/',
            data={'status': DefectRecord.STATUS_SOLD, 'sold_quantity_pcs': '10'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'DEFECT_UPDATE_FORBIDDEN')

    def test_return_lines_select_sources_excludes_existing_defect_source(self):
        sale = Sale.objects.create(
            order_number='ORD-2026-999',
            date=date(2026, 4, 26),
            client=self.active_client,
            sale_status=Sale.STATUS_SHIPPED,
            product='x',
            quantity=Decimal('1'),
            sold_pieces=Decimal('1'),
            price=Decimal('1'),
            revenue=Decimal('1'),
            cost=Decimal('0'),
            profit=Decimal('1'),
        )
        line = SaleLine.objects.create(sale=sale, product='x', quantity=Decimal('1'), unit_price=Decimal('1'), line_total=Decimal('1'))
        ret = Return.objects.create(sale=sale, date=date(2026, 4, 26))
        rl1 = ReturnLine.objects.create(return_doc=ret, sale_line=line, product='x', quantity=Decimal('1'), return_target=ReturnLine.TARGET_DEFECT)
        rl2 = ReturnLine.objects.create(return_doc=ret, sale_line=line, product='x', quantity=Decimal('1'), return_target=ReturnLine.TARGET_DEFECT)
        DefectRecord.objects.create(source_type=DefectRecord.SOURCE_RETURN, source_id=rl1.pk, product='x', quantity_pcs=Decimal('1'))
        resp = self.client.get('/api/defects/select-sources/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {x['id'] for x in resp.data['return_lines']}
        self.assertIn(rl2.pk, ids)
        self.assertNotIn(rl1.pk, ids)
