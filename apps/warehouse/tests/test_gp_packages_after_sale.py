from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.recipes.models import PlasticProfile
from apps.sales.models import Client, Sale, SaleLine
from apps.warehouse.models import GpPackOperation, GpPackUnit, WarehouseBatch
from apps.workshop.models import WorkshopBlank


class GpPackagesAfterSaleTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='gp-wh@example.com',
            password='pass12345',
            name='GP WH',
        )
        self.client.force_authenticate(self.user)
        self.client_obj = Client.objects.create(name='C', is_active=True)
        self.profile = PlasticProfile.objects.create(name='Профиль', code='GP-T', is_active=True)
        self.blank = WorkshopBlank.objects.create(
            name='Заготовка GP-T',
            recipe_kg_per_barrel=Decimal('1'),
            plastic_profile=self.profile,
        )
        self.wb = WarehouseBatch.objects.create(
            product='Prof',
            quantity=Decimal('6'),
            date=date(2026, 5, 19),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_PACKED,
            pieces_per_package=Decimal('6'),
            packages_count=Decimal('1'),
        )
        self.op = GpPackOperation.objects.create(
            product=self.profile,
            blank=self.blank,
            kind=GpPackOperation.KIND_BOX,
            label='',
            split_mode=GpPackOperation.SPLIT_SINGLE,
            total_pieces=6,
            created_by=self.user,
        )
        self.unit = GpPackUnit.objects.create(
            operation=self.op,
            sequence=1,
            pieces=6,
            warehouse_batch=self.wb,
        )

    def test_gp_packages_list_hides_sold_unit(self):
        resp = self.client.get('/api/warehouse/gp-packages/?page_size=500')
        self.assertEqual(resp.status_code, 200)
        items = resp.data.get('items') or resp.data.get('results') or []
        self.assertIn(self.unit.pk, {x['id'] for x in items})

        sale = Sale.objects.create(
            order_number='ORD-GP-1',
            product='Prof',
            quantity=Decimal('6'),
            date=date(2026, 5, 19),
            sale_status=Sale.STATUS_DRAFT,
            client=self.client_obj,
            price=Decimal('10'),
            revenue=Decimal('60'),
            warehouse_stock_applied=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='Prof',
            warehouse_batch=self.wb,
            gp_pack_unit=self.unit,
            quantity=Decimal('6'),
            piece_pick='from_sealed_package',
            unit_price=Decimal('10'),
            line_total=Decimal('60'),
        )
        from apps.sales.sale_warehouse import apply_warehouse_for_sale

        self.assertTrue(apply_warehouse_for_sale(sale))
        self.wb.refresh_from_db()
        self.assertEqual(self.wb.status, WarehouseBatch.STATUS_SHIPPED)

        resp2 = self.client.get('/api/warehouse/gp-packages/?page_size=500')
        self.assertEqual(resp2.status_code, 200)
        items2 = resp2.data.get('items') or resp2.data.get('results') or []
        self.assertNotIn(self.unit.pk, {x['id'] for x in items2})
