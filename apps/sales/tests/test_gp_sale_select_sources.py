"""Smoke: GP-упаковки в продаже без дубля с warehouse_batch."""
import unittest
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.recipes.models import PlasticProfile
from apps.sales.models import Client, Order, OrderLine, Sale
from apps.warehouse.models import GpPackOperation, GpPackUnit, WarehouseBatch
from apps.workshop.models import WorkshopBlank


@unittest.skip('Продажа в упаковках снята (BACKEND_SALES_SIMPLIFICATION).')
class GpSaleSelectSourcesTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='gp-sale@example.com',
            password='pass12345',
            name='GP Sale',
        )
        self.client.force_authenticate(self.user)
        self.client_obj = Client.objects.create(name='Клиент GP', is_active=True)
        self.profile = PlasticProfile.objects.create(name='Пластиковый профиль 6 м премиум', code='P6', is_active=True)
        self.blank = WorkshopBlank.objects.create(
            name='Заготовка P6',
            recipe_kg_per_barrel=Decimal('1'),
            plastic_profile=self.profile,
        )
        self.wb = WarehouseBatch.objects.create(
            product='Пластиковый профиль 6 м премиум',
            quantity=Decimal('10'),
            date=date(2026, 5, 29),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_PACKED,
            pieces_per_package=Decimal('10'),
            packages_count=Decimal('1'),
        )
        self.op = GpPackOperation.objects.create(
            product=self.profile,
            blank=self.blank,
            kind=GpPackOperation.KIND_BOX,
            label='метка',
            split_mode=GpPackOperation.SPLIT_SINGLE,
            total_pieces=10,
            created_by=self.user,
        )
        self.unit = GpPackUnit.objects.create(
            operation=self.op,
            sequence=1,
            pieces=10,
            warehouse_batch=self.wb,
        )
        self.order = Order.objects.create(
            order_number='ORD-GP-SALE',
            date=date(2026, 5, 29),
            client=self.client_obj,
            status='confirmed',
        )
        OrderLine.objects.create(
            order=self.order,
            product='Пластиковый профиль 6 м премиум',
            ordered_quantity=Decimal('10'),
            unit_price=Decimal('100'),
        )

    def test_select_sources_packages_no_duplicate_batch(self):
        resp = self.client.get(
            f'/api/sales/select-sources/?client_id={self.client_obj.pk}&unit_type=packages'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        batch_ids = {b['id'] for b in resp.data['available_warehouse_batches']}
        self.assertNotIn(self.wb.pk, batch_ids)
        gp_rows = resp.data.get('available_gp_packages') or []
        self.assertEqual(len(gp_rows), 1)
        self.assertEqual(gp_rows[0]['id'], self.unit.pk)
        self.assertEqual(gp_rows[0]['kind'], 'box')
        self.assertEqual(gp_rows[0]['label'], 'метка')
        self.assertEqual(gp_rows[0]['total_pieces'], 10)

    def test_gp_packages_available_filter(self):
        resp = self.client.get('/api/warehouse/gp-packages/?status=available&page_size=500')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get('items') or []
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], self.unit.pk)
        summary = resp.data.get('meta', {}).get('summary') or {}
        self.assertEqual(summary.get('packages_count'), 1)
        self.assertEqual(summary.get('pieces_total'), 10)

    def test_warehouse_packed_summary(self):
        resp = self.client.get(
            '/api/warehouse/batches/?inventory_form=packed&status=available&page_size=500'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        summary = resp.data.get('meta', {}).get('summary') or {}
        self.assertEqual(summary.get('packages_count'), 1)
        self.assertEqual(summary.get('rows_count'), 1)
        self.assertEqual(summary.get('pieces_total'), '10.0000')

    def test_sale_with_gp_package_id_and_block_resale(self):
        payload = {
            'date': '2026-05-29',
            'client': self.client_obj.pk,
            'unit_type': 'packages',
            'sale_status': Sale.STATUS_DRAFT,
            'sale_lines': [
                {
                    'product': 'Пластиковый профиль 6 м премиум',
                    'gp_package_id': self.unit.pk,
                    'quantity': '1',
                    'unit_price': '100',
                }
            ],
        }
        resp = self.client.post('/api/sales/', payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        resp2 = self.client.post('/api/sales/', payload, format='json')
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)

        resp3 = self.client.get('/api/warehouse/gp-packages/?status=available')
        items = resp3.data.get('items') or []
        self.assertEqual(len(items), 0)

    def test_preview_accepts_gp_package_id(self):
        payload = {
            'client': self.client_obj.pk,
            'unit_type': 'packages',
            'sale_lines': [
                {
                    'gp_package_id': self.unit.pk,
                    'quantity': '1',
                    'unit_price': '100',
                }
            ],
        }
        resp = self.client.post('/api/sales/preview/', payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        line = resp.data.get('sale_lines') or resp.data.get('lines') or []
        if isinstance(line, list) and line:
            self.assertEqual(line[0].get('gp_package_id'), self.unit.pk)
