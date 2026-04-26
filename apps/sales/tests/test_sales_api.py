from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client, Order, OrderLine, Payment, Return, Sale
from apps.warehouse.models import WarehouseBatch


class SalesApiContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='sales-admin@example.com',
            password='pass12345',
            name='Sales Admin',
        )
        self.client.force_authenticate(self.user)
        self.client_active = Client.objects.create(name='Активный', is_active=True, credit_limit=Decimal('1000'))
        self.client_inactive = Client.objects.create(name='Неактивный', is_active=False)
        self.order = Order.objects.create(
            order_number='ORD-2026-001',
            date=date(2026, 4, 26),
            client=self.client_active,
            status=Order.STATUS_CONFIRMED,
        )
        self.order_line = OrderLine.objects.create(
            order=self.order,
            product='60 мм белый',
            ordered_quantity=Decimal('10'),
            shipped_quantity=Decimal('0'),
            unit_price=Decimal('100'),
        )
        self.good_batch = WarehouseBatch.objects.create(
            product='60 мм белый',
            quantity=Decimal('20'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_PACKED,
            pieces_per_package=Decimal('10'),
            packages_count=Decimal('2'),
        )
        self.defect_batch = WarehouseBatch.objects.create(
            product='Брак профиль',
            quantity=Decimal('10'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_DEFECT,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )

    def _payload(self, sale_status=Sale.STATUS_DRAFT, client_id=None, with_batch=True, qty='5', unit_price='100'):
        return {
            'date': '2026-04-26',
            'client': client_id if client_id is not None else self.client_active.pk,
            'linked_order': self.order.pk,
            'sale_status': sale_status,
            'comment': 'test',
            'sale_lines': [
                {
                    'order_line': self.order_line.pk,
                    'product': '60 мм белый',
                    'warehouse_batch': self.good_batch.pk if with_batch else None,
                    'stock_form': 'packed',
                    'piece_pick': 'from_sealed_package' if with_batch else '',
                    'quantity': qty,
                    'unit_price': unit_price,
                    'defect_flag': False,
                    'comment': '',
                }
            ],
        }

    def _create_sale(self, sale_status=Sale.STATUS_DRAFT):
        resp = self.client.post('/api/sales/', data=self._payload(sale_status=sale_status), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        return Sale.objects.get(pk=resp.data['id'])

    def test_create_requires_client(self):
        payload = self._payload()
        payload.pop('client')
        resp = self.client.post('/api/sales/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_CLIENT')

    def test_create_rejects_inactive_client(self):
        resp = self.client.post('/api/sales/', data=self._payload(client_id=self.client_inactive.pk), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'INACTIVE_CLIENT')

    def test_create_requires_sale_lines(self):
        payload = self._payload()
        payload.pop('sale_lines')
        resp = self.client.post('/api/sales/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_SALE_LINES')

    def test_create_rejects_header_only(self):
        payload = {'date': '2026-04-26', 'client': self.client_active.pk, 'product': 'X', 'quantity': '1', 'price': '1'}
        resp = self.client.post('/api/sales/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_SALE_LINES')

    def test_create_rejects_quantity_non_positive(self):
        resp = self.client.post('/api/sales/', data=self._payload(qty='0'), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'SALE_QUANTITY_INVALID')

    def test_create_rejects_negative_unit_price(self):
        resp = self.client.post('/api/sales/', data=self._payload(unit_price='-1'), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'UNIT_PRICE_NEGATIVE')

    def test_create_rejects_closed_status(self):
        resp = self.client.post('/api/sales/', data=self._payload(sale_status=Sale.STATUS_CLOSED), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'CLOSED_CREATE_FORBIDDEN')

    def test_create_draft_success(self):
        resp = self.client.post('/api/sales/', data=self._payload(sale_status=Sale.STATUS_DRAFT), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_create_shipped_success_with_batch(self):
        resp = self.client.post('/api/sales/', data=self._payload(sale_status=Sale.STATUS_SHIPPED), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sale = Sale.objects.get(pk=resp.data['id'])
        self.assertTrue(sale.warehouse_stock_applied)

    def test_create_shipped_requires_batch_on_line(self):
        resp = self.client.post(
            '/api/sales/',
            data=self._payload(sale_status=Sale.STATUS_SHIPPED, with_batch=False),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_WAREHOUSE_BATCH')

    def test_create_rejects_defect_batch_for_regular_sale(self):
        payload = self._payload(sale_status=Sale.STATUS_SHIPPED)
        payload['sale_lines'][0]['warehouse_batch'] = self.defect_batch.pk
        resp = self.client.post('/api/sales/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'DEFECT_BATCH_FORBIDDEN')

    def test_create_rejects_insufficient_stock(self):
        payload = self._payload(sale_status=Sale.STATUS_SHIPPED, qty='25')
        payload['sale_lines'][0]['order_line'] = None
        resp = self.client.post('/api/sales/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'INSUFFICIENT_STOCK')

    def test_create_rejects_order_line_exceeded(self):
        resp = self.client.post('/api/sales/', data=self._payload(qty='11'), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'ORDER_LINE_QUANTITY_EXCEEDED')

    def test_update_rejects_status_via_patch(self):
        sale = self._create_sale()
        resp = self.client.patch(f'/api/sales/{sale.pk}/', data={'sale_status': Sale.STATUS_CONFIRMED}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'SALE_STATUS_UPDATE_FORBIDDEN')

    def test_update_rejects_shipped_sale(self):
        sale = self._create_sale(sale_status=Sale.STATUS_SHIPPED)
        resp = self.client.patch(f'/api/sales/{sale.pk}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'SALE_UPDATE_FORBIDDEN')

    def test_update_rejects_when_active_payment(self):
        sale = self._create_sale()
        Payment.objects.create(
            client=self.client_active,
            linked_sale=sale,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('10'),
            status=Payment.STATUS_ACTIVE,
        )
        resp = self.client.patch(f'/api/sales/{sale.pk}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'SALE_LOCKED_BY_PAYMENT')

    def test_update_rejects_when_return_exists(self):
        sale = self._create_sale()
        Return.objects.create(sale=sale, linked_order=self.order, date=date(2026, 4, 26))
        resp = self.client.patch(f'/api/sales/{sale.pk}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'SALE_LOCKED_BY_RETURN')

    def test_update_safe_fields_allowed(self):
        sale = self._create_sale()
        resp = self.client.patch(
            f'/api/sales/{sale.pk}/',
            data={'comment': 'ok', 'invoice_number': 'INV-1', 'receipt_number': 'RCPT-1'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_status_transition_and_missing_status(self):
        sale = self._create_sale()
        bad = self.client.patch(f'/api/sales/{sale.pk}/status/', data={}, format='json')
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad.data.get('code'), 'MISSING_STATUS')
        ok = self.client.patch(
            f'/api/sales/{sale.pk}/status/',
            data={'status': Sale.STATUS_CONFIRMED},
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)

    def test_status_invalid_transition(self):
        sale = self._create_sale()
        resp = self.client.patch(
            f'/api/sales/{sale.pk}/status/',
            data={'status': Sale.STATUS_CLOSED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INVALID_STATUS_TRANSITION')

    def test_cancel_sale_guards_and_rollback(self):
        sale = self._create_sale(sale_status=Sale.STATUS_PARTIALLY_SHIPPED)
        Payment.objects.create(
            client=self.client_active,
            linked_sale=sale,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('10'),
            status=Payment.STATUS_ACTIVE,
        )
        blocked = self.client.post(f'/api/sales/{sale.pk}/cancel/', data={}, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(blocked.data.get('code'), 'HAS_PAYMENTS')
        Payment.objects.filter(linked_sale=sale).update(status=Payment.STATUS_CANCELED)
        ok = self.client.post(f'/api/sales/{sale.pk}/cancel/', data={}, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        sale.refresh_from_db()
        self.assertEqual(sale.sale_status, Sale.STATUS_CANCELED)

    def test_select_sources_filters_and_shapes(self):
        other_client = Client.objects.create(name='Другой', is_active=True)
        other_order = Order.objects.create(
            order_number='ORD-2026-999',
            date=date(2026, 4, 26),
            client=other_client,
            status=Order.STATUS_CONFIRMED,
        )
        OrderLine.objects.create(order=other_order, product='X', ordered_quantity=Decimal('1'))
        resp = self.client.get(f'/api/sales/select-sources/?client_id={self.client_active.pk}&order_id={self.order.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('order_lines', resp.data)
        self.assertTrue(all(row['id'] == self.order.pk for row in resp.data['orders']))
        self.assertTrue(all(b['quality'] == WarehouseBatch.QUALITY_GOOD for b in resp.data['warehouse_batches']))

    def test_waybill_and_receipt_are_html(self):
        sale = self._create_sale()
        w = self.client.get(f'/api/sales/{sale.pk}/waybill/')
        r = self.client.get(f'/api/sales/{sale.pk}/receipt/')
        self.assertEqual(w.status_code, status.HTTP_200_OK)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('text/html', w['Content-Type'])
        self.assertIn('text/html', r['Content-Type'])

    def test_credit_check_hard_block_and_override_access(self):
        self.client_active.credit_limit_mode = 'hard'
        self.client_active.credit_limit = Decimal('1')
        self.client_active.save(update_fields=['credit_limit_mode', 'credit_limit'])
        sale = self._create_sale(sale_status=Sale.STATUS_CONFIRMED)
        resp = self.client.patch(
            f'/api/sales/{sale.pk}/status/',
            data={'status': Sale.STATUS_SHIPPED, 'force_credit_override': True},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get('credit_limit_bypassed'))

