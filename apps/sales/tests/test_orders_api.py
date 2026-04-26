from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import (
    Client,
    Order,
    OrderLine,
    OrderReservation,
    Payment,
    Return,
    Sale,
    SaleLine,
)
from apps.warehouse.models import WarehouseBatch


class OrdersApiContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='orders-admin@example.com',
            password='pass12345',
            name='Orders Admin',
        )
        self.client.force_authenticate(self.user)
        self.active_client = Client.objects.create(name='Активный', is_active=True)
        self.inactive_client = Client.objects.create(name='Неактивный', is_active=False)

    def _order_payload(self, client_id=None):
        return {
            'date': '2026-04-26',
            'client': client_id if client_id is not None else self.active_client.pk,
            'source_type': 'manager',
            'comment': 'Срочная',
            'lines': [
                {
                    'product': '60 мм белый',
                    'ordered_quantity': '10',
                    'unit_price': '100',
                    'comment': '',
                }
            ],
        }

    def _create_order(self, status_value=Order.STATUS_NEW):
        o = Order.objects.create(
            order_number=f'ORD-T-{Order.objects.count()+1:04d}',
            date=date(2026, 4, 26),
            client=self.active_client,
            source_type=Order.SOURCE_MANAGER,
            status=status_value,
        )
        OrderLine.objects.create(
            order=o,
            product='Линия',
            ordered_quantity=Decimal('10'),
            unit_price=Decimal('50'),
        )
        return o

    def test_create_fails_without_client(self):
        payload = self._order_payload()
        payload.pop('client')
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_CLIENT')

    def test_create_fails_with_inactive_client(self):
        payload = self._order_payload(client_id=self.inactive_client.pk)
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'INACTIVE_CLIENT')

    def test_create_fails_without_lines(self):
        payload = self._order_payload()
        payload.pop('lines')
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINES')

    def test_create_fails_with_empty_lines(self):
        payload = self._order_payload()
        payload['lines'] = []
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINES')

    def test_create_fails_line_without_product_or_profile(self):
        payload = self._order_payload()
        payload['lines'][0]['product'] = ''
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'PRODUCT_OR_PROFILE_REQUIRED')

    def test_create_fails_ordered_quantity_non_positive(self):
        payload = self._order_payload()
        payload['lines'][0]['ordered_quantity'] = '0'
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'ORDERED_QUANTITY_INVALID')

    def test_create_fails_negative_unit_price(self):
        payload = self._order_payload()
        payload['lines'][0]['unit_price'] = '-1'
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'UNIT_PRICE_NEGATIVE')

    def test_create_allows_zero_unit_price(self):
        payload = self._order_payload()
        payload['lines'][0]['unit_price'] = '0'
        resp = self.client.post('/api/orders/', data=payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(resp.data['lines'][0]['unit_price']), '0.00')

    def test_create_success_with_one_line(self):
        resp = self.client.post('/api/orders/', data=self._order_payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['client'], self.active_client.pk)
        self.assertEqual(len(resp.data['lines']), 1)

    def test_update_status_forbidden_on_regular_patch(self):
        order = self._create_order()
        resp = self.client.patch(
            f'/api/orders/{order.pk}/',
            data={'status': Order.STATUS_CONFIRMED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'STATUS_UPDATE_FORBIDDEN')

    def test_status_changes_only_via_status_endpoint(self):
        order = self._create_order()
        resp = self.client.patch(
            f'/api/orders/{order.pk}/status/',
            data={'status': Order.STATUS_CONFIRMED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CONFIRMED)

    def test_update_closed_order_forbidden(self):
        order = self._create_order(status_value=Order.STATUS_CLOSED)
        resp = self.client.patch(f'/api/orders/{order.pk}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'ORDER_UPDATE_FORBIDDEN')

    def test_update_canceled_order_forbidden(self):
        order = self._create_order(status_value=Order.STATUS_CANCELED)
        resp = self.client.patch(f'/api/orders/{order.pk}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'ORDER_UPDATE_FORBIDDEN')

    def test_cannot_change_line_after_sale(self):
        order = self._create_order()
        line = order.lines.first()
        sale = Sale.objects.create(
            order_number='ORD-SALE-1',
            product='Продажа',
            quantity=Decimal('2'),
            date=date(2026, 4, 26),
            client=self.active_client,
            sale_status=Sale.STATUS_SHIPPED,
            linked_order=order,
            revenue=Decimal('200'),
            cost=Decimal('100'),
        )
        SaleLine.objects.create(
            sale=sale,
            order_line=line,
            product='Линия',
            quantity=Decimal('2'),
            unit_price=Decimal('100'),
            line_total=Decimal('200'),
        )
        resp = self.client.patch(
            f'/api/orders/{order.pk}/',
            data={'lines': [{'id': line.pk, 'ordered_quantity': '20'}]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'ORDER_LINES_UPDATE_FORBIDDEN')

    def test_status_transition_new_to_confirmed(self):
        order = self._create_order()
        resp = self.client.patch(
            f'/api/orders/{order.pk}/status/',
            data={'status': Order.STATUS_CONFIRMED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_status_transition_confirmed_to_in_progress(self):
        order = self._create_order(status_value=Order.STATUS_CONFIRMED)
        resp = self.client.patch(
            f'/api/orders/{order.pk}/status/',
            data={'status': Order.STATUS_IN_PROGRESS},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_status_invalid_transition_returns_422(self):
        order = self._create_order(status_value=Order.STATUS_NEW)
        resp = self.client.patch(
            f'/api/orders/{order.pk}/status/',
            data={'status': Order.STATUS_SHIPPED},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INVALID_STATUS_TRANSITION')

    def test_status_without_body_returns_missing_status(self):
        order = self._create_order()
        resp = self.client.patch(f'/api/orders/{order.pk}/status/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_STATUS')

    def test_cancel_new_order_works(self):
        order = self._create_order(status_value=Order.STATUS_NEW)
        resp = self.client.patch(f'/api/orders/{order.pk}/cancel/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], Order.STATUS_CANCELED)

    def test_cancel_releases_active_reservations(self):
        order = self._create_order(status_value=Order.STATUS_NEW)
        line = order.lines.first()
        wb = WarehouseBatch.objects.create(
            product='Профиль',
            quantity=Decimal('20'),
            date=date(2026, 4, 26),
            quality=WarehouseBatch.QUALITY_GOOD,
            status=WarehouseBatch.STATUS_AVAILABLE,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        )
        res = OrderReservation.objects.create(
            order_line=line,
            warehouse_batch=wb,
            quantity=Decimal('3'),
            status=OrderReservation.STATUS_ACTIVE,
        )
        line.reserved_quantity = Decimal('3')
        line.save(update_fields=['reserved_quantity'])
        resp = self.client.patch(f'/api/orders/{order.pk}/cancel/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        res.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(res.status, OrderReservation.STATUS_RELEASED)
        self.assertEqual(line.reserved_quantity, Decimal('0'))
        self.assertEqual(resp.data['reservations_released'], 1)

    def test_cancel_forbidden_with_active_sale(self):
        order = self._create_order(status_value=Order.STATUS_NEW)
        Sale.objects.create(
            order_number='ORD-SALE-ACT',
            product='Продажа',
            quantity=Decimal('1'),
            date=date(2026, 4, 26),
            client=self.active_client,
            sale_status=Sale.STATUS_CONFIRMED,
            linked_order=order,
            revenue=Decimal('10'),
            cost=Decimal('5'),
        )
        resp = self.client.patch(f'/api/orders/{order.pk}/cancel/', data={}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get('code'), 'INVALID_TRANSITION')

    def test_delete_order_disabled(self):
        order = self._create_order()
        resp = self.client.delete(f'/api/orders/{order.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'DELETE_DISABLED')

    def test_select_sources_returns_only_active_clients_and_profiles_shape(self):
        Client.objects.create(name='Неактивный 2', is_active=False)
        resp = self.client.get('/api/orders/select-sources/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('clients', resp.data)
        self.assertIn('profiles', resp.data)
        self.assertTrue(all('id' in c and 'label' in c for c in resp.data['clients']))
        self.assertTrue(all(c['id'] != self.inactive_client.pk for c in resp.data['clients']))

    def test_order_history_structure(self):
        order = self._create_order()
        Payment.objects.create(
            client=self.active_client,
            linked_order=order,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('30'),
            status=Payment.STATUS_ACTIVE,
        )
        Return.objects.create(
            sale=Sale.objects.create(
                order_number='ORD-SALE-HIST',
                product='Продажа',
                quantity=Decimal('1'),
                date=date(2026, 4, 26),
                client=self.active_client,
                sale_status=Sale.STATUS_SHIPPED,
                linked_order=order,
                revenue=Decimal('50'),
                cost=Decimal('20'),
            ),
            linked_order=order,
            date=date(2026, 4, 26),
        )
        resp = self.client.get(f'/api/orders/{order.pk}/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('order', resp.data)
        self.assertIn('sales', resp.data)
        self.assertIn('payments', resp.data)
        self.assertIn('returns', resp.data)

    def test_order_waybill_returns_html(self):
        order = self._create_order()
        resp = self.client.get(f'/api/orders/{order.pk}/waybill/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('text/html', resp['Content-Type'])
