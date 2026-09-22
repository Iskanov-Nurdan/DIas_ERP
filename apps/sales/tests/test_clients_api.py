from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client, Order, Payment, Return, Sale, SaleLine


class ClientsApiContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='admin@example.com',
            password='pass12345',
            name='Admin',
        )
        self.client.force_authenticate(self.user)

    def _create_client(self, **kwargs) -> Client:
        data = {
            'name': 'ОсОО Альфа',
            'is_active': True,
            'credit_limit_mode': Client.CREDIT_MODE_SOFT,
        }
        data.update(kwargs)
        return Client.objects.create(**data)

    def _create_sale(self, c: Client, status_value: str, revenue='100', cost='60') -> Sale:
        return Sale.objects.create(
            order_number=f'ORD-{status_value}-{Sale.objects.count()+1}',
            product='Профиль',
            quantity=Decimal('1'),
            date=date(2026, 4, 26),
            client=c,
            sale_status=status_value,
            revenue=Decimal(str(revenue)),
            cost=Decimal(str(cost)),
        )

    def test_post_clients_creates_client(self):
        resp = self.client.post('/api/clients/', data={'name': 'ОсОО Бета'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'ОсОО Бета')
        self.assertEqual(resp.data['client_type'], Client.TYPE_INDIVIDUAL)
        self.assertEqual(resp.data['status'], 'active')
        self.assertNotIn('comment', resp.data)
        self.assertNotIn('notes', resp.data)

    def test_post_clients_creates_individual_contract(self):
        resp = self.client.post(
            '/api/clients/',
            data={
                'client_type': 'individual',
                'name': 'Иванов Иван Иванович',
                'phone': '+996700000000',
                'phone_extra': '+996500000000',
                'inn': '123',
                'address': 'Адрес не нужен физлицу',
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['client_type'], 'individual')
        self.assertEqual(resp.data['name'], 'Иванов Иван Иванович')
        self.assertEqual(resp.data['phone_extra'], '+996500000000')
        self.assertEqual(resp.data['inn'], '')
        self.assertEqual(resp.data['address'], '')
        self.assertEqual(resp.data['status'], 'active')

    def test_post_clients_creates_company_contract(self):
        resp = self.client.post(
            '/api/clients/',
            data={
                'client_type': 'company',
                'name': 'ОсОО Ромашка',
                'settlement_account': '1234567890123456',
                'phone': '+996700000000',
                'phone_extra': '+996500000000, +996550000000',
                'inn': '12345678901234',
                'address': 'г. Бишкек',
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['client_type'], 'company')
        self.assertEqual(resp.data['settlement_account'], '1234567890123456')
        self.assertEqual(resp.data['phone_extra'], '+996500000000, +996550000000')
        self.assertEqual(resp.data['inn'], '12345678901234')
        self.assertEqual(resp.data['address'], 'г. Бишкек')

    def test_patch_clients_updates_client(self):
        c = self._create_client(name='Старое')
        resp = self.client.patch(
            f'/api/clients/{c.pk}/',
            data={
                'client_type': 'company',
                'name': 'ОсОО Ромашка',
                'settlement_account': '1234567890123456',
                'phone': '+996700000000',
                'phone_extra': '+996500000000',
                'inn': '12345678901234',
                'address': 'Адрес',
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        c.refresh_from_db()
        self.assertEqual(c.client_type, 'company')
        self.assertEqual(c.name, 'ОсОО Ромашка')
        self.assertEqual(c.settlement_account, '1234567890123456')
        self.assertEqual(c.phone_alt, '+996500000000')
        self.assertEqual(c.inn, '12345678901234')
        self.assertEqual(c.address, 'Адрес')

    def test_patch_inactive_and_active_back(self):
        c = self._create_client()
        resp_off = self.client.patch(f'/api/clients/{c.pk}/', data={'is_active': False}, format='json')
        self.assertEqual(resp_off.status_code, status.HTTP_200_OK)
        c.refresh_from_db()
        self.assertFalse(c.is_active)
        resp_on = self.client.patch(f'/api/clients/{c.pk}/', data={'is_active': True}, format='json')
        self.assertEqual(resp_on.status_code, status.HTTP_200_OK)
        c.refresh_from_db()
        self.assertTrue(c.is_active)

    def test_delete_client_disabled(self):
        c = self._create_client()
        resp = self.client.delete(f'/api/clients/{c.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(resp.data['code'], 'DELETE_DISABLED')

    def test_history_has_expected_structure(self):
        c = self._create_client()
        sale_shipped = self._create_sale(c, Sale.STATUS_SHIPPED, revenue='200', cost='100')
        self._create_sale(c, Sale.STATUS_CANCELED, revenue='300', cost='120')
        self._create_sale(c, Sale.STATUS_DRAFT, revenue='400', cost='130')
        Payment.objects.create(
            client=c,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('50'),
            status=Payment.STATUS_ACTIVE,
        )
        Payment.objects.create(
            client=c,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('999'),
            status=Payment.STATUS_CANCELED,
        )
        resp = self.client.get(f'/api/clients/{c.pk}/history/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for key in (
            'client_id', 'client_name', 'orders', 'sales', 'payments',
            'total_revenue', 'total_paid', 'client_debt_money', 'credit_limit_mode',
        ):
            self.assertIn(key, resp.data)
        self.assertNotIn('returns', resp.data)
        # Агрегаты считаются только по реальным продажам (без draft/canceled).
        self.assertEqual(resp.data['total_revenue'], '200')
        self.assertEqual(resp.data['total_profit'], '100')
        # В массиве payments только active.
        self.assertEqual(len(resp.data['payments']), 1)
        # История может содержать sales разных статусов.
        self.assertGreaterEqual(len(resp.data['sales']), 3)
        self.assertEqual(sale_shipped.sale_status, Sale.STATUS_SHIPPED)

    def test_fin_summary_requires_client_id(self):
        resp = self.client.get('/api/client-financial-summary/')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['code'], 'MISSING_PARAM')

    def test_fin_summary_404_for_unknown_client(self):
        resp = self.client.get('/api/client-financial-summary/?client_id=999999')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.data['code'], 'NOT_FOUND')

    def test_fin_summary_success(self):
        c = self._create_client(credit_limit=Decimal('1000'))
        self._create_sale(c, Sale.STATUS_SHIPPED, revenue='300', cost='100')
        self._create_sale(c, Sale.STATUS_CANCELED, revenue='900', cost='100')
        self._create_sale(c, Sale.STATUS_DRAFT, revenue='700', cost='100')
        Payment.objects.create(
            client=c,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('100'),
            status=Payment.STATUS_ACTIVE,
        )
        resp = self.client.get(f'/api/client-financial-summary/?client_id={c.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['client_id'], c.pk)
        self.assertEqual(resp.data['total_revenue'], '300')

    def test_inactive_client_blocked_for_new_order(self):
        c = self._create_client(is_active=False)
        resp = self.client.post(
            '/api/orders/',
            data={'client': c.pk, 'date': '2026-04-26'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(resp.data.get('code'), ('validation_error', 'INACTIVE_CLIENT'))
        self.assertTrue(any(e.get('field') == 'client' for e in resp.data.get('errors', [])))

    def test_inactive_client_blocked_for_new_sale(self):
        c = self._create_client(is_active=False)
        resp = self.client.post(
            '/api/sales/',
            data={'client': c.pk, 'product': 'Профиль', 'quantity': '1'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(resp.data.get('code'), ('validation_error', 'INACTIVE_CLIENT'))
        self.assertTrue(any(e.get('field') == 'client' for e in resp.data.get('errors', [])))

    def test_inactive_client_blocked_for_new_payment(self):
        c = self._create_client(is_active=False)
        resp = self.client.post(
            '/api/payments/',
            data={'client': c.pk, 'amount': '10', 'date': '2026-04-26'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(resp.data.get('code'), ('validation_error', 'INACTIVE_CLIENT'))
        self.assertTrue(any(e.get('field') == 'client' for e in resp.data.get('errors', [])))

    def test_inactive_client_can_have_return_for_old_sale(self):
        c = self._create_client(is_active=True)
        sale = Sale.objects.create(
            order_number='ORD-RET-1',
            product='Профиль',
            quantity=Decimal('2'),
            date=date(2026, 4, 26),
            client=c,
            sale_status=Sale.STATUS_SHIPPED,
            revenue=Decimal('200'),
            cost=Decimal('80'),
        )
        line = SaleLine.objects.create(
            sale=sale,
            product='Профиль',
            quantity=Decimal('2'),
            unit_price=Decimal('100'),
            line_total=Decimal('200'),
        )
        c.is_active = False
        c.save(update_fields=['is_active'])
        resp = self.client.post(
            '/api/returns/',
            data={
                'sale': sale.pk,
                'date': '2026-04-26',
                'lines': [
                    {
                        'sale_line': line.pk,
                        'quantity': '1',
                        'return_target': 'warehouse',
                        'condition_type': 'good',
                    }
                ],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Return.objects.filter(sale=sale).count(), 1)

    def test_clients_list_sales_metrics_exclude_draft_and_canceled(self):
        c = self._create_client()
        self._create_sale(c, Sale.STATUS_SHIPPED, revenue='100')
        self._create_sale(c, Sale.STATUS_CANCELED, revenue='200')
        self._create_sale(c, Sale.STATUS_DRAFT, revenue='300')
        resp = self.client.get('/api/clients/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get('items', [])
        row = next(item for item in rows if item['id'] == c.pk)
        self.assertEqual(row['sales_count'], 1)
        self.assertEqual(row['sales_total'], '100')

    def test_profile_summary_and_debts_sync_after_partial_payment(self):
        c = self._create_client(
            client_type=Client.TYPE_COMPANY,
            settlement_account='1234567890123456',
            phone='+996700000000',
            phone_alt='+996500000000',
            inn='12345678901234',
            address='Адрес',
        )
        sale = Sale.objects.create(
            order_number='ORD-DEBT-1',
            product='Профиль',
            quantity=Decimal('1'),
            date=date(2026, 4, 26),
            client=c,
            sale_status=Sale.STATUS_DRAFT,
            revenue=Decimal('400'),
            cost=Decimal('100'),
        )
        Payment.objects.create(
            client=c,
            linked_sale=sale,
            date=date(2026, 4, 26),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('200'),
            status=Payment.STATUS_ACTIVE,
        )

        resp = self.client.get(f'/api/clients/{c.pk}/profile/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['client']['client_type'], Client.TYPE_COMPANY)
        self.assertEqual(resp.data['client']['settlement_account'], '1234567890123456')
        self.assertEqual(resp.data['client']['phone_extra'], '+996500000000')
        self.assertEqual(resp.data['client']['inn'], '12345678901234')
        self.assertEqual(resp.data['client']['address'], 'Адрес')
        self.assertNotIn('comment', resp.data['client'])
        self.assertEqual(resp.data['summary']['total_sales_amount'], '400')
        self.assertEqual(resp.data['summary']['total_paid_amount'], '200')
        self.assertEqual(resp.data['summary']['total_debt'], '200')
        self.assertTrue(any(d['id'] == sale.pk and d['debt_amount'] == '200' for d in resp.data['debts']))

    def test_client_profile_json_has_no_returns_block(self):
        c = self._create_client()
        resp = self.client.get(f'/api/clients/{c.pk}/profile/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('returns', resp.data)
        self.assertNotIn('total_returns', resp.data['summary'])
        self.assertIn('client_type_label', resp.data['client'])

    def test_client_profile_html_renders_without_returns(self):
        c = self._create_client(name='Тест HTML')
        resp = self.client.get(f'/api/clients/{c.pk}/profile/?format=html')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('text/html', resp['Content-Type'])
        body = resp.content.decode('utf-8')
        self.assertIn('Тест HTML', body)
        self.assertNotIn('Возврат', body)
        self.assertNotIn('total_returns', body)
