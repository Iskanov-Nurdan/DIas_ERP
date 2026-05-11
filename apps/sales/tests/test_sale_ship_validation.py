"""Проверки validate_sale_ship и списания склада по строкам продажи."""
from decimal import Decimal

from django.test import TestCase

from apps.sales.models import Client, Sale, SaleLine
from apps.sales.sale_warehouse import apply_warehouse_for_sale
from apps.sales.state_machine import validate_sale_ship
from apps.warehouse.models import WarehouseBatch


def _good_batch(**kwargs):
    defaults = {
        'product': 'Prof',
        'quantity': Decimal('100'),
        'date': '2026-01-15',
        'quality': WarehouseBatch.QUALITY_GOOD,
        'status': WarehouseBatch.STATUS_AVAILABLE,
        'inventory_form': WarehouseBatch.INVENTORY_UNPACKED,
    }
    defaults.update(kwargs)
    return WarehouseBatch.objects.create(**defaults)


class ValidateSaleShipTests(TestCase):
    def test_regular_sale_without_batch_cannot_validate_ship(self):
        client = Client.objects.create(name='Клиент тест')
        sale = Sale.objects.create(
            order_number='ORD-SHIP-1',
            product='P',
            quantity=Decimal('1'),
            date='2026-01-15',
            sale_status=Sale.STATUS_CONFIRMED,
            client=client,
            price=Decimal('10'),
            revenue=Decimal('10'),
            is_defect_sale=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='P',
            quantity=Decimal('1'),
            unit_price=Decimal('10'),
            line_total=Decimal('10'),
        )
        with self.assertRaises(ValueError) as ctx:
            validate_sale_ship(sale)
        self.assertIn('партию', str(ctx.exception).lower())

    def test_multiline_one_line_without_batch_blocks_ship(self):
        client = Client.objects.create(name='Клиент тест 2')
        wb = _good_batch(quantity=Decimal('50'))
        sale = Sale.objects.create(
            order_number='ORD-SHIP-2',
            product='Mix',
            quantity=Decimal('3'),
            date='2026-01-15',
            sale_status=Sale.STATUS_CONFIRMED,
            client=client,
            price=Decimal('1'),
            revenue=Decimal('3'),
            is_defect_sale=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='A',
            quantity=Decimal('2'),
            warehouse_batch=wb,
            stock_form=WarehouseBatch.INVENTORY_UNPACKED,
            piece_pick='',
            unit_price=Decimal('1'),
            line_total=Decimal('2'),
        )
        SaleLine.objects.create(
            sale=sale,
            product='B',
            quantity=Decimal('1'),
            warehouse_batch=None,
            stock_form='',
            piece_pick='',
            unit_price=Decimal('1'),
            line_total=Decimal('1'),
        )
        with self.assertRaises(ValueError) as ctx:
            validate_sale_ship(sale)
        self.assertIn('warehouse_batch', str(ctx.exception).lower())

    def test_multiline_with_batches_deducts_stock(self):
        client = Client.objects.create(name='Клиент тест 3')
        wb1 = _good_batch(product='G1', quantity=Decimal('100'))
        wb2 = _good_batch(product='G2', quantity=Decimal('80'))
        sale = Sale.objects.create(
            order_number='ORD-SHIP-3',
            product='G1+G2',
            quantity=Decimal('7'),
            date='2026-01-15',
            sale_status=Sale.STATUS_SHIPPED,
            client=client,
            price=Decimal('1'),
            revenue=Decimal('7'),
            is_defect_sale=False,
        )
        SaleLine.objects.create(
            sale=sale,
            product='G1',
            quantity=Decimal('4'),
            warehouse_batch=wb1,
            stock_form=WarehouseBatch.INVENTORY_UNPACKED,
            piece_pick='',
            unit_price=Decimal('1'),
            line_total=Decimal('4'),
        )
        SaleLine.objects.create(
            sale=sale,
            product='G2',
            quantity=Decimal('3'),
            warehouse_batch=wb2,
            stock_form=WarehouseBatch.INVENTORY_UNPACKED,
            piece_pick='',
            unit_price=Decimal('1'),
            line_total=Decimal('3'),
        )
        validate_sale_ship(sale)
        self.assertTrue(apply_warehouse_for_sale(sale))
        wb1.refresh_from_db()
        wb2.refresh_from_db()
        self.assertEqual(wb1.quantity, Decimal('96'))
        self.assertEqual(wb2.quantity, Decimal('77'))
        sale.refresh_from_db()
        self.assertTrue(sale.warehouse_stock_applied)
