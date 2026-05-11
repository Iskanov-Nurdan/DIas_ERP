from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.warehouse.models import WarehouseBatch


class WarehouseStockBucketApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='wh-bucket-admin@example.com',
            password='pass12345',
            name='Warehouse Admin',
        )
        self.client.force_authenticate(self.user)
        self.standard = WarehouseBatch.objects.create(
            product='ГП стандарт',
            quantity=Decimal('10'),
            date=date(2026, 5, 3),
            status=WarehouseBatch.STATUS_AVAILABLE,
            quality=WarehouseBatch.QUALITY_GOOD,
            stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
        )
        self.reworked = WarehouseBatch.objects.create(
            product='Переделка А',
            quantity=Decimal('4'),
            date=date(2026, 5, 3),
            status=WarehouseBatch.STATUS_AVAILABLE,
            quality=WarehouseBatch.QUALITY_GOOD,
            stock_bucket=WarehouseBatch.STOCK_BUCKET_REWORKED,
        )

    def test_list_default_is_standard_only(self):
        resp = self.client.get('/api/warehouse/batches/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {row['id'] for row in resp.data['items']}
        self.assertIn(self.standard.pk, ids)
        self.assertNotIn(self.reworked.pk, ids)
        row = next(x for x in resp.data['items'] if x['id'] == self.standard.pk)
        self.assertEqual(row.get('stock_bucket'), WarehouseBatch.STOCK_BUCKET_STANDARD)
        self.assertIn('linked_entities', row)

    def test_list_reworked_bucket(self):
        resp = self.client.get('/api/warehouse/batches/', {'stock_bucket': 'reworked'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {row['id'] for row in resp.data['items']}
        self.assertIn(self.reworked.pk, ids)
        self.assertNotIn(self.standard.pk, ids)
        row = next(x for x in resp.data['items'] if x['id'] == self.reworked.pk)
        self.assertEqual(row.get('stock_bucket'), WarehouseBatch.STOCK_BUCKET_REWORKED)
        self.assertEqual(row.get('product_name'), 'Переделка А')
        self.assertEqual(row.get('quantity'), '4')
        self.assertEqual(row.get('available_quantity'), '4')
        self.assertIsNone(row.get('result_rework_request'))
