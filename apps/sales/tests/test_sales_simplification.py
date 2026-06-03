"""BACKEND_SALES_SIMPLIFICATION: pieces, auto price, sale_date."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.materials.models import MaterialBatch, RawMaterial
from apps.recipes.models import PlasticProfile
from apps.warehouse.models import WarehouseBatch
from apps.workshop.models import WorkshopBlank


class SalesSimplificationTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='sales-s@example.com',
            password='pass12345',
            name='Sales',
        )
        self.client.force_authenticate(self.user)

        self.profile = PlasticProfile.objects.create(
            name='Профиль 6м',
            code='S6',
            weight_kg_per_piece=Decimal('1.5'),
            cost_price=Decimal('19'),
            markup_amount=Decimal('110'),
            is_active=True,
        )
        self.blank = WorkshopBlank.objects.create(name='Заготовка', recipe_kg_per_barrel=Decimal('50'))
        self.wb = WarehouseBatch.objects.create(
            profile=self.profile,
            product=self.profile.name,
            quantity=Decimal('20'),
            cost_per_piece=Decimal('19'),
            date=timezone.now().date(),
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
            workshop_blank=self.blank,
        )

    def test_select_sources_rejects_packages(self):
        resp = self.client.get('/api/sales/select-sources/?unit_type=packages')
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)

    def test_select_sources_has_pricing(self):
        resp = self.client.get('/api/sales/select-sources/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['available_gp_packages'], [])
        batch = next(
            (b for b in resp.data['available_warehouse_batches'] if b['id'] == self.wb.pk),
            None,
        )
        self.assertIsNotNone(batch)
        self.assertEqual(batch['unit_sale_price'], '129')
        self.assertEqual(batch['available_pieces'], 20)

    def test_preview_without_unit_price(self):
        resp = self.client.post(
            '/api/sales/preview/',
            {
                'client': None,
                'sale_lines': [{'warehouse_batch': self.wb.pk, 'quantity': '2'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        from apps.sales.models import Client

        cl = Client.objects.create(name='Покупатель', is_active=True)
        resp2 = self.client.post(
            '/api/sales/preview/',
            {
                'client': cl.pk,
                'sale_date': str(date.today()),
                'payment_type': 'debt',
                'paid_amount': '0',
                'sale_lines': [{'warehouse_batch': self.wb.pk, 'quantity': '2'}],
            },
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data['normalized_lines'][0]['unit_price'], '129')
        self.assertEqual(resp2.data['total_amount'], '258')

    def test_preview_mixed_payment_splits(self):
        from apps.sales.models import Client

        cl = Client.objects.create(name='Смешанная', is_active=True)
        resp = self.client.post(
            '/api/sales/preview/',
            {
                'client': cl.pk,
                'sale_date': '2026-06-03',
                'sale_lines': [{'warehouse_batch': self.wb.pk, 'quantity': '2'}],
                'payment_type': 'full',
                'payment_method': 'card',
                'paid_amount': '258',
                'payment_splits': [
                    {'payment_method': 'cash', 'amount': '100'},
                    {'payment_method': 'card', 'amount': '158'},
                ],
                'payment_reference': '+996701111544',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['payment_splits']), 2)
        self.assertEqual(resp.data['payment_reference'], '+996701111544')

    def test_create_with_sale_date_and_splits(self):
        from apps.sales.models import Client, Payment

        cl = Client.objects.create(name='Покупатель 2', is_active=True)
        resp = self.client.post(
            '/api/sales/',
            {
                'client': cl.pk,
                'sale_date': '2026-06-01',
                'sale_lines': [
                    {'warehouse_batch': self.wb.pk, 'quantity': '1'},
                ],
                'payment_type': 'full',
                'payment_method': 'cash',
                'paid_amount': '129',
                'payment_splits': [
                    {'payment_method': 'cash', 'amount': '50'},
                    {'payment_method': 'card', 'amount': '79'},
                ],
                'payment_reference': '4111111111111111',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['date'], '2026-06-01')
        self.assertEqual(resp.data['payment_type'], 'full')
        self.assertEqual(resp.data['payment_reference'], '4111111111111111')
        sale_id = resp.data['id']
        pays = Payment.objects.filter(linked_sale_id=sale_id, status=Payment.STATUS_ACTIVE)
        self.assertEqual(pays.count(), 2)
