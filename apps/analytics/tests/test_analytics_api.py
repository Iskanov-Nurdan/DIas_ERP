from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.analytics.models import AnalyticsOtherExpense
from apps.materials.models import MaterialBatch, RawMaterial
from apps.production.models import Line, ProductionBatch
from apps.recipes.models import PlasticProfile
from apps.sales.models import Client, Payment, Sale
from apps.warehouse.models import WarehouseBatch


class AnalyticsApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='analytics@example.com',
            password='pass12345',
            name='Analytics Admin',
        )
        self.client.force_authenticate(self.user)
        self.profile = PlasticProfile.objects.create(name='Профиль 60')
        self.client_a = Client.objects.create(name='Клиент А', is_active=True)
        self.batch = WarehouseBatch.objects.create(
            profile=self.profile,
            product='Профиль 60',
            quantity=Decimal('100'),
            date=date(2026, 4, 1),
            status=WarehouseBatch.STATUS_AVAILABLE,
        )
        self.sale = Sale.objects.create(
            date=date(2026, 4, 15),
            client=self.client_a,
            product='Профиль 60',
            warehouse_batch=self.batch,
            quantity=Decimal('10'),
            sold_pieces=Decimal('10'),
            price=Decimal('100'),
            revenue=Decimal('1000'),
            cost=Decimal('400'),
            profit=Decimal('600'),
            sale_status=Sale.STATUS_SHIPPED,
        )

    def test_summary_empty_period_returns_zeros(self):
        resp = self.client.get('/api/analytics/summary/?year=2099&month=1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cards = resp.data['cards']
        self.assertEqual(cards['revenue_total'], '0')
        self.assertEqual(cards['sales_cost_total'], '0')
        self.assertEqual(cards['product_other_expenses_total'], '0')
        self.assertEqual(cards['sold_goods_cost_total'], '0')
        self.assertEqual(cards['period_expenses_total'], '0')
        self.assertEqual(cards['profit_total'], '0')
        self.assertEqual(cards['sales_count'], 0)
        self.assertEqual(cards['client_debt_total'], '0')
        self.assertEqual(cards['purchase_total'], '0')
        self.assertEqual(cards['production_cost_total'], '0')
        self.assertEqual(resp.data['trends'], [])
        self.assertEqual(resp.data['sales_by_profile'], [])
        self.assertIsInstance(resp.data['warehouse_summary']['available'], (int, float))
        self.assertIsInstance(resp.data['warehouse_summary']['reserved'], (int, float))
        self.assertNotIn('production_summary', resp.data)
        trend = resp.data['trends']
        self.assertEqual(trend, [])

    def test_summary_includes_sale_and_simplified_shape(self):
        resp = self.client.get('/api/analytics/summary/?year=2026&month=4')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cards = resp.data['cards']
        self.assertEqual(cards['revenue_total'], '1000')
        self.assertEqual(cards['sales_cost_total'], '400')
        self.assertEqual(cards['product_other_expenses_total'], '0')
        self.assertEqual(cards['sold_goods_cost_total'], '400')
        self.assertEqual(cards['period_expenses_total'], '400')
        self.assertEqual(cards['profit_total'], '600')
        self.assertEqual(cards['sales_count'], 1)
        self.assertGreater(Decimal(cards['sold_units_total']), 0)
        self.assertIn('client_debt_total', cards)
        self.assertEqual(cards['purchase_total'], '0')
        self.assertEqual(cards['production_cost_total'], '0')
        self.assertIn('expense_total', cards)
        if resp.data['trends']:
            row = resp.data['trends'][0]
            self.assertIn('period', row)
            self.assertIn('revenue', row)
            self.assertIn('purchase_total', row)
            self.assertIn('other_expenses_total', row)
            self.assertNotIn('profit', row)
            self.assertIsInstance(row['revenue'], (int, float))
            self.assertIsInstance(row['purchase_total'], (int, float))
        if resp.data['sales_by_profile']:
            row = resp.data['sales_by_profile'][0]
            self.assertIn('profile_id', row)
            self.assertIn('sold_units', row)
            self.assertIn('revenue', row)

    def test_debt_details_matches_card(self):
        resp_sum = self.client.get('/api/analytics/summary/?year=2026&month=4')
        resp_debt = self.client.get('/api/analytics/debt-details/?year=2026&month=4')
        self.assertEqual(resp_debt.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp_debt.data['total_debt'],
            resp_sum.data['cards']['client_debt_total'],
        )
        self.assertIn('items', resp_debt.data)
        if Decimal(resp_debt.data['total_debt']) > 0:
            self.assertTrue(len(resp_debt.data['items']) >= 1)
            item = resp_debt.data['items'][0]
            self.assertIn('client_id', item)
            self.assertIn('oldest_debt_date', item)

    def test_debt_details_with_partial_payment(self):
        Payment.objects.create(
            client=self.client_a,
            linked_sale=self.sale,
            date=date(2026, 4, 16),
            payment_type=Payment.TYPE_PAYMENT,
            payment_method=Payment.METHOD_CASH,
            amount=Decimal('300'),
            status=Payment.STATUS_ACTIVE,
        )
        resp = self.client.get('/api/analytics/debt-details/?year=2026&month=4')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_debt'], '700')
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['debt_amount'], '700')
        self.assertEqual(resp.data['items'][0]['sales_count'], 1)

    def test_summary_includes_draft_with_warehouse_applied(self):
        Sale.objects.create(
            date=date(2026, 5, 19),
            client=self.client_a,
            product='Профиль 60',
            quantity=Decimal('14'),
            sold_pieces=Decimal('14'),
            price=Decimal('1000'),
            revenue=Decimal('14000'),
            cost=Decimal('0'),
            profit=Decimal('14000'),
            sale_status=Sale.STATUS_DRAFT,
            warehouse_stock_applied=True,
        )
        resp = self.client.get('/api/analytics/summary/?year=2026&month=5')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['cards']['sales_count'], 1)
        self.assertGreater(Decimal(resp.data['cards']['revenue_total']), 0)

    def test_profit_details_totals(self):
        resp = self.client.get('/api/analytics/profit-details/?year=2026&month=4')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['totals']['revenue'], '1000')
        self.assertEqual(resp.data['totals']['profit'], '600')

    def test_product_unit_costs_empty_catalog(self):
        WarehouseBatch.objects.all().delete()
        PlasticProfile.objects.all().delete()
        resp = self.client.get('/api/analytics/product-unit-costs/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['items'], [])

    def test_product_unit_costs_two_profiles_different_costs(self):
        p2 = PlasticProfile.objects.create(name='Профиль 80', code='P-80', is_active=True)
        line = Line.objects.create(name='Л1', is_active=True)
        ProductionBatch.objects.create(
            profile=self.profile,
            line=line,
            product='Профиль 60',
            pieces=10,
            date=date(2026, 3, 1),
            material_cost_total=Decimal('1255'),
            cost_per_piece=Decimal('125.50'),
        )
        ProductionBatch.objects.create(
            profile=p2,
            line=line,
            product='Профиль 80',
            pieces=5,
            date=date(2026, 4, 1),
            material_cost_total=Decimal('1000'),
            cost_per_piece=Decimal('200'),
        )
        resp = self.client.get('/api/analytics/product-unit-costs/?year=2026')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_id = {row['profile_id']: row for row in resp.data['items']}
        self.assertEqual(by_id[self.profile.pk]['unit_cost_per_piece'], '125.5')
        self.assertEqual(by_id[p2.pk]['unit_cost_per_piece'], '200')
        self.assertEqual(by_id[self.profile.pk]['profile_name'], 'Профиль 60')
        self.assertTrue(by_id[p2.pk]['is_active'])

    def test_summary_period_expenses_equals_purchase_only(self):
        rm = RawMaterial.objects.create(name='ПВХ', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=rm,
            quantity_initial=Decimal('500'),
            quantity_remaining=Decimal('500'),
            unit_price=Decimal('10'),
            received_at=timezone.make_aware(datetime(2026, 4, 5, 12, 0)),
        )
        line = Line.objects.create(name='Л1', is_active=True)
        ProductionBatch.objects.create(
            profile=self.profile,
            line=line,
            product='Профиль 60',
            pieces=10,
            date=date(2026, 4, 10),
            material_cost_total=Decimal('9999'),
            cost_per_piece=Decimal('999'),
        )
        resp = self.client.get('/api/analytics/summary/?year=2026&month=4')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cards = resp.data['cards']
        self.assertEqual(cards['purchase_total'], '5000')
        self.assertEqual(cards['period_expenses_total'], '5400')
        self.assertEqual(cards['operating_expenses_total'], '5400')
        self.assertEqual(cards['production_cost_total'], '9999')
        self.assertEqual(cards['sales_cost_total'], '400')

    def test_trends_purchase_and_revenue_by_incoming_date(self):
        rm = RawMaterial.objects.create(name='Сырьё PnL', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=rm,
            quantity_initial=Decimal('262.91'),
            quantity_remaining=Decimal('262.91'),
            unit_price=Decimal('100'),
            received_at=timezone.make_aware(datetime(2026, 3, 15, 10, 0)),
        )
        Sale.objects.create(
            date=date(2026, 4, 20),
            client=self.client_a,
            product='Профиль 60',
            quantity=Decimal('1'),
            sold_pieces=Decimal('1'),
            price=Decimal('65051'),
            revenue=Decimal('65051'),
            cost=Decimal('0'),
            profit=Decimal('65051'),
            sale_status=Sale.STATUS_SHIPPED,
        )
        resp_mar = self.client.get('/api/analytics/summary/?year=2026&month=3&trend_group=month')
        resp_apr = self.client.get('/api/analytics/summary/?year=2026&month=4&trend_group=month')
        self.assertEqual(resp_mar.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_apr.status_code, status.HTTP_200_OK)
        mar = resp_mar.data['trends'][0]
        apr = resp_apr.data['trends'][0]
        self.assertEqual(mar['period'], '2026-03')
        self.assertEqual(float(mar['purchase_total']), 26291.0)
        self.assertEqual(float(mar['revenue']), 0.0)
        self.assertEqual(apr['period'], '2026-04')
        self.assertGreater(float(apr['revenue']), 0)
        self.assertEqual(float(apr.get('purchase_total', 0)), 0.0)
        self.assertEqual(resp_mar.data['cards']['purchase_total'], '26291')
        trend_sum = sum(float(t['purchase_total']) for t in resp_mar.data['trends'])
        self.assertAlmostEqual(trend_sum, float(resp_mar.data['cards']['purchase_total']), places=2)

    def test_other_expenses_pending_not_in_summary(self):
        create = self.client.post(
            '/api/analytics/other-expenses/',
            {'name': 'Аренда', 'amount': '2200', 'date': '2026-05-15'},
            format='json',
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create.data['status'], 'pending')
        resp = self.client.get('/api/analytics/summary/?year=2026&month=5')
        self.assertEqual(resp.data['cards']['other_expenses_total'], '0')
        self.assertEqual(resp.data['cards']['period_expenses_total'], '0')

    def test_other_expenses_accept_in_summary_and_trends(self):
        create = self.client.post(
            '/api/analytics/other-expenses/',
            {'name': 'Доставка', 'amount': '1200', 'date': '2026-05-10'},
            format='json',
        )
        oid = create.data['id']
        accept = self.client.post(f'/api/analytics/other-expenses/{oid}/accept/')
        self.assertEqual(accept.status_code, status.HTTP_200_OK)
        self.assertEqual(accept.data['status'], 'accepted')
        resp = self.client.get('/api/analytics/summary/?year=2026&month=5&trend_group=month')
        cards = resp.data['cards']
        self.assertEqual(cards['other_expenses_total'], '1200')
        self.assertEqual(cards['period_expenses_total'], '1200')
        trend = resp.data['trends'][0]
        self.assertEqual(trend['period'], '2026-05')
        self.assertEqual(float(trend['other_expenses_total']), 1200.0)
        self.assertEqual(float(trend['period_expenses_total']), 1200.0)

    def test_other_expenses_accept_may_not_count_in_april(self):
        create = self.client.post(
            '/api/analytics/other-expenses/',
            {'name': 'Май', 'amount': '500', 'date': '2026-05-01'},
            format='json',
        )
        self.client.post(f'/api/analytics/other-expenses/{create.data["id"]}/accept/')
        resp_may = self.client.get('/api/analytics/summary/?year=2026&month=5')
        resp_apr = self.client.get('/api/analytics/summary/?year=2026&month=4')
        self.assertEqual(resp_may.data['cards']['other_expenses_total'], '500')
        self.assertEqual(resp_apr.data['cards']['other_expenses_total'], '0')

    def test_other_expenses_reject_removed(self):
        create = self.client.post(
            '/api/analytics/other-expenses/',
            {'name': 'Отмена', 'amount': '99', 'date': '2026-05-20'},
            format='json',
        )
        oid = create.data['id']
        reject = self.client.post(f'/api/analytics/other-expenses/{oid}/reject/')
        self.assertEqual(reject.status_code, status.HTTP_204_NO_CONTENT)
        lst = self.client.get('/api/analytics/other-expenses/?year=2026&month=5')
        ids = [x['id'] for x in lst.data['items']]
        self.assertNotIn(oid, ids)
        self.assertFalse(AnalyticsOtherExpense.objects.filter(pk=oid).exists())

    def test_other_expenses_period_expenses_purchase_plus_other(self):
        rm = RawMaterial.objects.create(name='Сырьё mix', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=rm,
            quantity_initial=Decimal('100'),
            quantity_remaining=Decimal('100'),
            unit_price=Decimal('10'),
            received_at=timezone.make_aware(datetime(2026, 5, 5, 12, 0)),
        )
        c1 = self.client.post(
            '/api/analytics/other-expenses/',
            {'name': 'Прочее', 'amount': '200', 'date': '2026-05-12'},
            format='json',
        )
        self.client.post(f'/api/analytics/other-expenses/{c1.data["id"]}/accept/')
        resp = self.client.get('/api/analytics/summary/?year=2026&month=5')
        self.assertEqual(resp.data['cards']['purchase_total'], '1000')
        self.assertEqual(resp.data['cards']['other_expenses_total'], '200')
        self.assertEqual(resp.data['cards']['period_expenses_total'], '1200')

    def test_product_other_expenses_in_summary_and_details(self):
        self.profile.extra_rubber = Decimal('5')
        self.profile.extra_label = Decimal('2')
        self.profile.extra_labor = Decimal('3')
        self.profile.save()
        resp = self.client.get('/api/analytics/summary/?year=2026&month=4')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        cards = resp.data['cards']
        self.assertEqual(cards['sales_cost_total'], '400')
        self.assertEqual(cards['product_other_expenses_total'], '100')
        self.assertEqual(cards['sold_goods_cost_total'], '500')
        self.assertEqual(cards['period_expenses_total'], '500')
        self.assertEqual(cards['profit_total'], '500')

        detail = self.client.get('/api/analytics/product-other-expenses-details/?year=2026&month=4')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['total_product_other_expenses'], '100')
        self.assertEqual(len(detail.data['items']), 1)
        item = detail.data['items'][0]
        self.assertEqual(item['sale_id'], self.sale.pk)
        self.assertEqual(item['unit_other_expenses'], '10')
        self.assertEqual(item['total_other_expenses'], '100')
        self.assertIn('breakdown', item)
