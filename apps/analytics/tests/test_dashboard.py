from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import UserAccess
from apps.analytics.models import AnalyticsExpenseCategory, AnalyticsOtherExpense
from apps.analytics.other_expenses import generate_recurring_occurrences
from apps.foam.models import FoamDensityGrade, FoamGpStock, FoamProductionRun, FoamRawLot, FoamSale, FoamSaleLine
from apps.materials.models import MaterialBatch, RawMaterial
from apps.recipes.models import PlasticProfile
from apps.sales.models import Client, Payment, Return, ReturnLine, Sale, SaleLine
from apps.warehouse.models import WarehouseBatch

URL = '/api/analytics/dashboard/?date_from=2026-04-01&date_to=2026-04-30'


def kpi(resp, key):
    return next(k for k in resp.data['kpis'] if k['key'] == key)


class DashboardTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(email='dash@example.com', password='pass12345', name='Dash')
        self.client.force_authenticate(self.user)
        self.profile = PlasticProfile.objects.create(name='Профиль 60')
        self.client_a = Client.objects.create(name='Клиент А', is_active=True)
        self.batch = WarehouseBatch.objects.create(
            profile=self.profile, product='Профиль 60', quantity=Decimal('100'),
            date=date(2026, 4, 1), status=WarehouseBatch.STATUS_AVAILABLE,
        )
        self.sale = Sale.objects.create(
            date=date(2026, 4, 15), client=self.client_a, product='Профиль 60', warehouse_batch=self.batch,
            quantity=Decimal('10'), sold_pieces=Decimal('10'), price=Decimal('100'),
            revenue=Decimal('1000'), cost=Decimal('0'), profit=Decimal('0'), sale_status=Sale.STATUS_SHIPPED,
        )
        self.line = SaleLine.objects.create(
            sale=self.sale, product='Профиль 60', warehouse_batch=self.batch,
            quantity=Decimal('10'), unit_price=Decimal('100'), line_total=Decimal('1000'), cost=Decimal('400'),
        )

    def test_purchases_not_subtracted_from_profit(self):
        rm = RawMaterial.objects.create(name='ПВХ', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=rm, quantity_initial=Decimal('100'), quantity_remaining=Decimal('100'), unit='kg',
            unit_price=Decimal('50'), total_price=Decimal('5000'),
            received_at=timezone.make_aware(timezone.datetime(2026, 4, 10, 12)),
        )
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(kpi(resp, 'revenue')['value'], '1000')
        self.assertEqual(kpi(resp, 'gross_margin')['value'], '600')
        self.assertEqual(kpi(resp, 'net_profit')['value'], '600')
        # Закупка видна в движении денег, но не в прибыли.
        self.assertEqual(resp.data['cash']['purchases'], '5000')

    def test_returns_reduce_revenue_and_cogs(self):
        ret = Return.objects.create(date=date(2026, 4, 20), status=Return.STATUS_COMPLETED, sale=self.sale)
        ReturnLine.objects.create(return_doc=ret, sale_line=self.line, product='Профиль 60', quantity=Decimal('2'))
        resp = self.client.get(URL)
        self.assertEqual(kpi(resp, 'revenue')['value'], '800')
        self.assertEqual(kpi(resp, 'gross_margin')['value'], '480')

    def test_foam_line_included_and_filterable(self):
        grade = FoamDensityGrade.objects.create(code='F15', min_kg_m3=Decimal('14'), max_kg_m3=Decimal('16'))
        lot = FoamRawLot.objects.create(
            lot_number='L1', material_name='ПСВ', supplier='S', bag_weight_kg=Decimal('25'),
            received_kg=Decimal('100'), remaining_kg=Decimal('0'), unit_price=Decimal('10'),
        )
        FoamProductionRun.objects.create(lot=lot, grade=grade, input_kg=Decimal('100'), output_format='cube', output_qty=Decimal('5'))
        stock = FoamGpStock.objects.create(output_format='cube', grade=grade, qty=Decimal('3'))
        fs = FoamSale.objects.create(client='Стройка', sale_date=date(2026, 4, 5), total_amount=Decimal('900'),
                                     paid_amount=Decimal('600'), payment_status='partial')
        FoamSaleLine.objects.create(sale=fs, stock=stock, qty=Decimal('2'), unit_price=Decimal('450'))

        resp = self.client.get(URL)
        self.assertEqual(kpi(resp, 'revenue')['value'], '1900')
        # Себестоимость куба = 100 кг × 10 / 5 = 200 → 2 куба = 400; маржа 600 + 500.
        self.assertEqual(kpi(resp, 'gross_margin')['value'], '1100')

        foam = self.client.get(URL + '&product_line=foam')
        self.assertEqual(kpi(foam, 'revenue')['value'], '900')
        self.assertEqual(kpi(foam, 'cash_in')['value'], '600')
        self.assertEqual(foam.data['debts']['total'], '300')

    def test_manual_entries_expense_and_income(self):
        rent = AnalyticsExpenseCategory.objects.get(kind='expense', name='Аренда')
        other = AnalyticsExpenseCategory.objects.get(kind='income', name='Прочий доход')
        AnalyticsOtherExpense.objects.create(name='Аренда', amount=Decimal('100'), date=date(2026, 4, 2),
                                             status='accepted', kind='expense', category=rent)
        AnalyticsOtherExpense.objects.create(name='Доход', amount=Decimal('50'), date=date(2026, 4, 3),
                                             status='accepted', kind='income', category=other)
        AnalyticsOtherExpense.objects.create(name='Ждёт', amount=Decimal('999'), date=date(2026, 4, 3), status='pending')
        resp = self.client.get(URL)
        self.assertEqual(kpi(resp, 'expenses')['value'], '100')
        self.assertEqual(kpi(resp, 'net_profit')['value'], '550')
        details = self.client.get(URL.replace('dashboard/', 'dashboard-details/') + '&metric=net_profit')
        self.assertEqual(details.status_code, status.HTTP_200_OK)
        self.assertEqual(details.data['formula'][-1]['amount'], '550')

    def test_payments_count_as_cash_in(self):
        Payment.objects.create(client=self.client_a, linked_sale=self.sale, date=date(2026, 4, 16),
                               payment_type=Payment.TYPE_PAYMENT, payment_method=Payment.METHOD_CARD,
                               amount=Decimal('300'), status=Payment.STATUS_ACTIVE)
        resp = self.client.get(URL)
        self.assertEqual(kpi(resp, 'cash_in')['value'], '300')
        self.assertEqual(resp.data['debts']['total'], '700')

    def test_finance_hidden_without_key(self):
        User = get_user_model()
        u = User.objects.create_user(email='op@example.com', password='pass12345', name='Op')
        UserAccess.objects.filter(user=u).delete()
        UserAccess.objects.create(user=u, access_key='analytics')
        self.client.force_authenticate(u)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(kpi(resp, 'revenue')['value'], '1000')
        self.assertIsNone(kpi(resp, 'gross_margin')['value'])
        self.assertTrue(kpi(resp, 'net_profit')['locked'])
        self.assertIsNone(resp.data['top_products'][0]['margin'])
        details = self.client.get('/api/analytics/dashboard-details/?metric=net_profit')
        self.assertEqual(details.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get('/api/analytics/other-expenses/?year=2026').status_code, status.HTTP_403_FORBIDDEN)


class ExpenseRegistryTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(email='reg@example.com', password='pass12345', name='Reg')
        self.client.force_authenticate(self.user)
        self.rent = AnalyticsExpenseCategory.objects.get(kind='expense', name='Аренда')
        self.income = AnalyticsExpenseCategory.objects.get(kind='income', name='Прочий доход')

    def post(self, **data):
        return self.client.post('/api/analytics/other-expenses/', data, format='json')

    def test_validation(self):
        future = (date.today() + timedelta(days=2)).isoformat()
        self.assertEqual(self.post(amount='0', date='2026-01-05', category_id=self.rent.pk).status_code, 400)
        self.assertEqual(self.post(amount='10', date=future, category_id=self.rent.pk).status_code, 400)
        self.assertEqual(self.post(amount='10', date='2026-01-05', kind='expense', category_id=self.income.pk).status_code, 400)
        ok = self.post(amount='10.5', date='2026-01-05', category_id=self.rent.pk, comment='январь')
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.data['name'], 'Аренда')
        self.assertEqual(ok.data['amount'], '10.5')
        self.assertEqual(ok.data['created_by_name'], 'Reg')

    def test_patch_sets_updated_by(self):
        oid = self.post(amount='10', date='2026-01-05', category_id=self.rent.pk).data['id']
        resp = self.client.patch(f'/api/analytics/other-expenses/{oid}/', {'amount': '25'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['amount'], '25')
        self.assertEqual(resp.data['updated_by_name'], 'Reg')

    def test_recurring_generates_once_per_month(self):
        today = date.today()
        start = date(today.year - 1 if today.month <= 2 else today.year, (today.month - 3) % 12 + 1, 31 if today.month != 5 else 28)
        start = start.replace(day=28)
        resp = self.post(amount='1000', date=start.isoformat(), category_id=self.rent.pk, recurring=True)
        self.assertEqual(resp.status_code, 201)
        tpl = AnalyticsOtherExpense.objects.get(pk=resp.data['id'])
        n = tpl.occurrences.count()
        self.assertGreaterEqual(n, 2)
        generate_recurring_occurrences()
        generate_recurring_occurrences()
        self.assertEqual(tpl.occurrences.count(), n)
        lst = self.client.get(f'/api/analytics/other-expenses/?date_from={start.isoformat()}&date_to={today.isoformat()}')
        self.assertEqual(len(lst.data['recurring_templates']), 1)
        self.assertNotIn(tpl.pk, [x['id'] for x in lst.data['items']])

    def test_categories_endpoint(self):
        resp = self.client.get('/api/analytics/expense-categories/?active=1')
        self.assertEqual(resp.status_code, 200)
        names = [c['name'] for c in resp.data['items']]
        self.assertIn('Зарплата', names)
        created = self.client.post('/api/analytics/expense-categories/', {'name': 'Реклама', 'kind': 'expense'}, format='json')
        self.assertEqual(created.status_code, 201)
        dup = self.client.post('/api/analytics/expense-categories/', {'name': 'реклама', 'kind': 'expense'}, format='json')
        self.assertEqual(dup.status_code, 400)
