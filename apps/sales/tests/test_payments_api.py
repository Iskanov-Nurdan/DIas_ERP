from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client, Order, OrderLine, Payment, Return, ReturnLine, Sale, SaleLine


class PaymentsApiContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='payments-admin@example.com',
            password='pass12345',
            name='Payments Admin',
        )
        self.client.force_authenticate(self.user)

        self.client_active = Client.objects.create(name='Активный клиент', is_active=True)
        self.client_other = Client.objects.create(name='Другой клиент', is_active=True)
        self.client_inactive = Client.objects.create(name='Неактивный клиент', is_active=False)

        self.order = Order.objects.create(
            order_number='ORD-2026-001',
            date=date(2026, 4, 26),
            client=self.client_active,
            status=Order.STATUS_IN_PROGRESS,
        )
        self.order_canceled = Order.objects.create(
            order_number='ORD-2026-002',
            date=date(2026, 4, 26),
            client=self.client_active,
            status=Order.STATUS_CANCELED,
        )
        self.order_closed = Order.objects.create(
            order_number='ORD-2026-003',
            date=date(2026, 4, 26),
            client=self.client_active,
            status=Order.STATUS_CLOSED,
        )
        self.order_other = Order.objects.create(
            order_number='ORD-2026-004',
            date=date(2026, 4, 26),
            client=self.client_other,
            status=Order.STATUS_IN_PROGRESS,
        )
        OrderLine.objects.create(
            order=self.order,
            product='Труба 1',
            ordered_quantity=Decimal('10'),
            unit_price=Decimal('100'),
        )

        self.sale = Sale.objects.create(
            order_number='SALE-2026-001',
            sale_number='SALE-2026-001',
            sale_status=Sale.STATUS_SHIPPED,
            linked_order=self.order,
            client=self.client_active,
            product='Труба 1',
            quantity=Decimal('10'),
            price=Decimal('100'),
            revenue=Decimal('1000'),
            date=date(2026, 4, 26),
        )
        self.sale_canceled = Sale.objects.create(
            order_number='SALE-2026-002',
            sale_number='SALE-2026-002',
            sale_status=Sale.STATUS_CANCELED,
            linked_order=self.order,
            client=self.client_active,
            product='Труба 2',
            quantity=Decimal('5'),
            price=Decimal('200'),
            revenue=Decimal('1000'),
            date=date(2026, 4, 26),
        )
        self.sale_draft = Sale.objects.create(
            order_number='SALE-2026-003',
            sale_number='SALE-2026-003',
            sale_status=Sale.STATUS_DRAFT,
            linked_order=self.order,
            client=self.client_active,
            product='Труба 3',
            quantity=Decimal('2'),
            price=Decimal('300'),
            revenue=Decimal('600'),
            date=date(2026, 4, 26),
        )
        self.sale_other = Sale.objects.create(
            order_number='SALE-2026-004',
            sale_number='SALE-2026-004',
            sale_status=Sale.STATUS_SHIPPED,
            linked_order=self.order_other,
            client=self.client_other,
            product='Труба 4',
            quantity=Decimal('10'),
            price=Decimal('100'),
            revenue=Decimal('1000'),
            date=date(2026, 4, 26),
        )

        self.sale_line = SaleLine.objects.create(
            sale=self.sale,
            product='Труба 1',
            quantity=Decimal('5'),
            unit_price=Decimal('100'),
            line_total=Decimal('500'),
        )
        self.return_completed = Return.objects.create(
            return_number='RET-2026-001',
            date=date(2026, 4, 26),
            status=Return.STATUS_COMPLETED,
            sale=self.sale,
            linked_order=self.order,
        )
        ReturnLine.objects.create(
            return_doc=self.return_completed,
            sale_line=self.sale_line,
            product='Труба 1',
            quantity=Decimal('2'),
        )
        self.return_draft = Return.objects.create(
            return_number='RET-2026-002',
            date=date(2026, 4, 26),
            status=Return.STATUS_DRAFT,
            sale=self.sale,
            linked_order=self.order,
        )
        self.return_canceled = Return.objects.create(
            return_number='RET-2026-003',
            date=date(2026, 4, 26),
            status=Return.STATUS_CANCELED,
            sale=self.sale,
            linked_order=self.order,
        )
        self.return_other_client = Return.objects.create(
            return_number='RET-2026-004',
            date=date(2026, 4, 26),
            status=Return.STATUS_COMPLETED,
            sale=self.sale_other,
            linked_order=self.order_other,
        )

    def _create_payment(self, **overrides):
        payload = {
            'date': '2026-04-26',
            'client': self.client_active.pk,
            'linked_sale': self.sale.pk,
            'payment_type': Payment.TYPE_PAYMENT,
            'amount': '100',
            'payment_method': Payment.METHOD_CASH,
            'comment': 'Оплата',
        }
        payload.update(overrides)
        return self.client.post('/api/payments/', data=payload, format='json')

    def test_create_requires_client(self):
        resp = self._create_payment(client=None)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_CLIENT')

    def test_create_rejects_inactive_client(self):
        resp = self._create_payment(client=self.client_inactive.pk)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'INACTIVE_CLIENT')

    def test_create_rejects_amount_non_positive(self):
        zero = self._create_payment(amount='0')
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero.data.get('code'), 'INVALID_AMOUNT')
        neg = self._create_payment(amount='-1')
        self.assertEqual(neg.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(neg.data.get('code'), 'INVALID_AMOUNT')

    def test_create_rejects_invalid_payment_type_and_method(self):
        bad_type = self._create_payment(payment_type='bad')
        self.assertEqual(bad_type.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_type.data.get('code'), 'INVALID_PAYMENT_TYPE')
        bad_method = self._create_payment(payment_method='bad')
        self.assertEqual(bad_method.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_method.data.get('code'), 'INVALID_PAYMENT_METHOD')

    def test_payment_requires_linked_sale_or_order(self):
        resp = self._create_payment(linked_sale=None, linked_order=None)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINKED_ENTITY')

    def test_prepayment_requires_linked_order(self):
        resp = self._create_payment(payment_type=Payment.TYPE_PREPAYMENT, linked_sale=None, linked_order=None)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINKED_ENTITY')

    def test_surcharge_requires_linked_sale_or_order(self):
        resp = self._create_payment(payment_type=Payment.TYPE_SURCHARGE, linked_sale=None, linked_order=None)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINKED_ENTITY')

    def test_payment_rejects_canceled_sale_order(self):
        sale_resp = self._create_payment(linked_sale=self.sale_canceled.pk, linked_order=None)
        self.assertEqual(sale_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(sale_resp.data.get('code'), 'MISSING_LINKED_ENTITY')
        order_resp = self._create_payment(linked_sale=None, linked_order=self.order_canceled.pk)
        self.assertEqual(order_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(order_resp.data.get('code'), 'MISSING_LINKED_ENTITY')

    def test_prepayment_rejects_closed_order(self):
        resp = self._create_payment(
            payment_type=Payment.TYPE_PREPAYMENT,
            linked_sale=None,
            linked_order=self.order_closed.pk,
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'MISSING_LINKED_ENTITY')

    def test_refund_rules_and_limits(self):
        no_links = self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=None, manual_refund_reason='')
        self.assertEqual(no_links.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(no_links.data.get('code'), ('REFUND_RETURN_REQUIRED', 'REFUND_REASON_REQUIRED'))

        draft_ret = self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=self.return_draft.pk)
        self.assertEqual(draft_ret.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(draft_ret.data.get('code'), 'REFUND_RETURN_NOT_COMPLETED')

        canceled_ret = self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=self.return_canceled.pk)
        self.assertEqual(canceled_ret.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(canceled_ret.data.get('code'), 'REFUND_RETURN_NOT_COMPLETED')

        ok = self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=self.return_completed.pk, amount='100')
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)

        exceeded = self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=self.return_completed.pk, amount='1000')
        self.assertEqual(exceeded.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(exceeded.data.get('code'), 'REFUND_AMOUNT_EXCEEDED')

    def test_create_client_mismatch_errors(self):
        sale_mismatch = self._create_payment(client=self.client_other.pk, linked_sale=self.sale.pk, linked_order=None)
        self.assertEqual(sale_mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(sale_mismatch.data.get('code'), 'CLIENT_MISMATCH')

        order_mismatch = self._create_payment(client=self.client_other.pk, linked_sale=None, linked_order=self.order.pk)
        self.assertEqual(order_mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(order_mismatch.data.get('code'), 'CLIENT_MISMATCH')

        return_mismatch = self._create_payment(
            client=self.client_other.pk,
            payment_type=Payment.TYPE_REFUND,
            linked_sale=None,
            linked_order=None,
            linked_return=self.return_completed.pk,
            amount='10',
        )
        self.assertEqual(return_mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(return_mismatch.data.get('code'), 'CLIENT_MISMATCH')

    def test_update_frozen_and_status_forbidden(self):
        created = self._create_payment()
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        pid = created.data['id']

        for field, value in (
            ('amount', '999'),
            ('client', self.client_other.pk),
            ('linked_sale', self.sale_other.pk),
            ('linked_order', self.order_other.pk),
            ('linked_return', self.return_other_client.pk),
            ('payment_type', Payment.TYPE_REFUND),
            ('status', Payment.STATUS_CANCELED),
        ):
            resp = self.client.patch(f'/api/payments/{pid}/', data={field: value}, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp.data.get('code'), 'PAYMENT_STATUS_UPDATE_FORBIDDEN')

    def test_update_allows_safe_fields(self):
        created = self._create_payment()
        pid = created.data['id']
        resp = self.client.patch(
            f'/api/payments/{pid}/',
            data={'date': '2026-04-27', 'payment_method': Payment.METHOD_CARD, 'comment': 'upd'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_canceled_payment_cannot_be_edited(self):
        created = self._create_payment()
        pid = created.data['id']
        canceled = self.client.post(f'/api/payments/{pid}/cancel/', data={}, format='json')
        self.assertEqual(canceled.status_code, status.HTTP_200_OK)
        resp = self.client.patch(f'/api/payments/{pid}/', data={'comment': 'x'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'PAYMENT_ALREADY_CANCELED')

    def test_cancel_flow_and_metrics(self):
        created = self._create_payment(amount='300')
        pid = created.data['id']
        cancel = self.client.post(f'/api/payments/{pid}/cancel/', data={}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel.data.get('status'), Payment.STATUS_CANCELED)
        repeat = self.client.patch(f'/api/payments/{pid}/cancel/', data={}, format='json')
        self.assertEqual(repeat.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(repeat.data.get('code'), 'PAYMENT_ALREADY_CANCELED')

        summary = self.client.get(f'/api/payments/summary/?client_id={self.client_active.pk}')
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data['total_paid_gross'], '0')

    def test_summary_validation_and_filters(self):
        missing = self.client.get('/api/payments/summary/')
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing.data.get('code'), 'MISSING_CLIENT')

        not_found = self.client.get('/api/payments/summary/?client_id=999999')
        self.assertEqual(not_found.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(not_found.data.get('code'), 'NOT_FOUND')

        self._create_payment(amount='100')
        self._create_payment(payment_type=Payment.TYPE_REFUND, linked_sale=None, linked_order=None, linked_return=self.return_completed.pk, amount='20')
        summary = self.client.get(f'/api/payments/summary/?client_id={self.client_active.pk}')
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        # revenue: only shipped sale(1000), draft/canceled should be excluded
        self.assertEqual(summary.data['total_revenue'], '1000')
        self.assertEqual(summary.data['total_paid_gross'], '100')
        self.assertEqual(summary.data['total_refunded'], '20')
        self.assertEqual(summary.data['total_paid_net'], '80')

    def test_select_sources_contract(self):
        resp = self.client.get(f'/api/payments/select-sources/?client_id={self.client_active.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(c['id'] == self.client_active.pk for c in resp.data['clients']))
        self.assertTrue(all(o['client'] == self.client_active.pk for o in resp.data['orders']))
        self.assertTrue(all(s['client'] == self.client_active.pk for s in resp.data['sales']))
        self.assertTrue(all(r['status'] == Return.STATUS_COMPLETED for r in resp.data['returns']))
        for bucket in ('orders', 'sales', 'returns'):
            if resp.data[bucket]:
                self.assertIn('id', resp.data[bucket][0])
                self.assertIn('label', resp.data[bucket][0])

    def test_delete_disabled(self):
        created = self._create_payment()
        resp = self.client.delete(f'/api/payments/{created.data["id"]}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data.get('code'), 'DELETE_DISABLED')
