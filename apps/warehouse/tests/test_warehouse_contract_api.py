"""Контракт: gp-packages status, operations ledger, unpacked balance."""
from datetime import date, datetime

from django.utils import timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.recipes.models import PlasticProfile
from apps.sales.models import Client, Sale, SaleLine
from apps.sales.sale_warehouse import apply_warehouse_for_sale
from apps.warehouse.gp_packaging_service import balance_group_detail
from apps.warehouse.models import GpPackOperation, GpPackUnit, WarehouseBatch
from apps.workshop.models import BlankProductionRun, WorkshopBlank


class WarehouseContractApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='wh-contract@example.com',
            password='pass12345',
            name='WH Contract',
        )
        self.client.force_authenticate(self.user)
        self.profile = PlasticProfile.objects.create(name='Пластиковый профиль 6м', code='P6', is_active=True)
        self.blank = WorkshopBlank.objects.create(
            name='Заготовка',
            recipe_kg_per_barrel=Decimal('1'),
            plastic_profile=self.profile,
        )
        self.run = BlankProductionRun.objects.create(
            product=self.profile,
            blank=self.blank,
            blank_name_snapshot=self.blank.name,
            product_name_snapshot=self.profile.name,
            blank_total_kg=Decimal('100'),
            blank_used_in_production_kg=Decimal('90'),
            vat_max_kg_demo=Decimal('100'),
            weight_kg_per_piece=Decimal('2.45'),
            status=BlankProductionRun.STATUS_GP_ACCEPTED,
            gp_accepted_at=timezone.make_aware(datetime(2026, 5, 19, 10, 0)),
            gp_accepted_pieces=38,
            gp_accepted_kg=Decimal('90'),
        )
        self.unpacked_wb = WarehouseBatch.objects.create(
            profile=self.profile,
            product='Профиль',
            quantity=Decimal('38'),
            date=date(2026, 5, 19),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
            blank_production_run=self.run,
        )
        self.client_obj = Client.objects.create(name='C', is_active=True)

    def _pack_unit(self):
        wb = WarehouseBatch.objects.create(
            profile=self.profile,
            product='Профиль',
            quantity=Decimal('6'),
            date=date(2026, 5, 19),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_PACKED,
            pieces_per_package=Decimal('6'),
            packages_count=Decimal('1'),
        )
        op = GpPackOperation.objects.create(
            product=self.profile,
            blank=self.blank,
            kind=GpPackOperation.KIND_BOX,
            label='042',
            split_mode=GpPackOperation.SPLIT_SINGLE,
            total_pieces=6,
            created_by=self.user,
        )
        return GpPackUnit.objects.create(operation=op, sequence=1, pieces=6, warehouse_batch=wb)

    def test_gp_packages_default_hides_sold(self):
        unit = self._pack_unit()
        resp = self.client.get('/api/warehouse/gp-packages/?page_size=500')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get('items') or resp.data.get('results') or []
        self.assertIn(unit.pk, {x['id'] for x in items})
        self.assertEqual(items[0]['status'], 'available')
        self.assertFalse(items[0]['is_sold'])

        sale = Sale.objects.create(
            order_number='S-PKG',
            product='P',
            quantity=Decimal('6'),
            date=date(2026, 5, 19),
            sale_status=Sale.STATUS_DRAFT,
            client=self.client_obj,
            price=Decimal('1'),
            revenue=Decimal('6'),
            warehouse_stock_applied=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='P',
            warehouse_batch=unit.warehouse_batch,
            gp_pack_unit=unit,
            quantity=Decimal('6'),
            piece_pick='from_sealed_package',
            unit_price=Decimal('1'),
            line_total=Decimal('6'),
        )
        apply_warehouse_for_sale(sale)

        resp2 = self.client.get('/api/warehouse/gp-packages/')
        items2 = resp2.data.get('items') or resp2.data.get('results') or []
        self.assertNotIn(unit.pk, {x['id'] for x in items2})

        resp_sold = self.client.get('/api/warehouse/gp-packages/?status=sold')
        sold_items = resp_sold.data.get('items') or resp_sold.data.get('results') or []
        self.assertIn(unit.pk, {x['id'] for x in sold_items})
        row = next(x for x in sold_items if x['id'] == unit.pk)
        self.assertEqual(row['status'], 'sold')
        self.assertTrue(row['is_sold'])
        self.assertEqual(row['sold_sale_id'], sale.pk)

    def test_unpacked_balance_after_piece_sale(self):
        sale = Sale.objects.create(
            order_number='S-PCS',
            product='P',
            quantity=Decimal('8'),
            date=date(2026, 5, 19),
            sale_status=Sale.STATUS_DRAFT,
            client=self.client_obj,
            price=Decimal('1'),
            revenue=Decimal('8'),
            warehouse_stock_applied=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='P',
            warehouse_batch=self.unpacked_wb,
            quantity=Decimal('8'),
            stock_form=WarehouseBatch.INVENTORY_UNPACKED,
            piece_pick='loose_remainder',
            unit_price=Decimal('1'),
            line_total=Decimal('8'),
        )
        apply_warehouse_for_sale(sale)
        g = balance_group_detail(product_id=self.profile.pk, blank_id=self.blank.pk)
        self.assertEqual(g.total_unpacked_pieces, 30)
        line = g.lines[0]
        self.assertEqual(line.sold_pieces, 8)

    def test_operations_sale_lines(self):
        unit = self._pack_unit()
        sale = Sale.objects.create(
            order_number='S-OP',
            product='P',
            quantity=Decimal('14'),
            date=date(2026, 5, 19),
            sale_status=Sale.STATUS_DRAFT,
            client=self.client_obj,
            price=Decimal('1'),
            revenue=Decimal('14'),
            warehouse_stock_applied=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='P',
            warehouse_batch=unit.warehouse_batch,
            gp_pack_unit=unit,
            quantity=Decimal('6'),
            piece_pick='from_sealed_package',
            unit_price=Decimal('1'),
            line_total=Decimal('6'),
        )
        SaleLine.objects.create(
            sale=sale,
            product='P',
            warehouse_batch=self.unpacked_wb,
            quantity=Decimal('8'),
            stock_form=WarehouseBatch.INVENTORY_UNPACKED,
            piece_pick='loose_remainder',
            unit_price=Decimal('1'),
            line_total=Decimal('8'),
        )
        apply_warehouse_for_sale(sale)
        resp = self.client.get('/api/warehouse/operations/?kind=sale')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data['items']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(x['kind'] == 'sale' and x['direction'] == 'out' for x in items))
