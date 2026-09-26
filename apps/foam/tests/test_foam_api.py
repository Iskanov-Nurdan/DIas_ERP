from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.sales.models import Client


class FoamApiAcceptanceTests(APITestCase):
    """Чек-лист приёмки из BACKEND_FOAM_REQUIREMENTS.md §8."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='foam-admin@example.com', password='pass12345', name='Foam Admin'
        )
        self.client.force_authenticate(self.user)
        self.crm_client = Client.objects.create(name='ТОО СтройМир', is_active=True)

    def _create_lot(self, bag_weight_kg='800'):
        resp = self.client.post(
            '/api/foam/raw-lots/',
            {'material_name': 'Гранула EPS Kingeps HS', 'supplier': 'Kingeps', 'bag_weight_kg': bag_weight_kg},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp.data

    def _create_grade(self, code='14-15', min_kg_m3='13', max_kg_m3='15.5'):
        resp = self.client.post(
            '/api/foam/density-grades/',
            {'code': code, 'min_kg_m3': min_kg_m3, 'max_kg_m3': max_kg_m3},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp.data

    def test_raw_lot_created_and_listed(self):
        lot = self._create_lot(bag_weight_kg='800')
        self.assertEqual(Decimal(lot['remaining_kg']), Decimal('800'))
        self.assertEqual(Decimal(lot['received_kg']), Decimal('800'))
        self.assertTrue(lot['lot_number'])

        resp = self.client.get('/api/foam/raw-lots/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('items', resp.data)
        self.assertIn('pages', resp.data['meta'])
        self.assertEqual(resp.data['meta']['total'], 1)

    def test_density_grade_duplicate_code_conflict(self):
        self._create_grade(code='20')
        resp = self.client.post(
            '/api/foam/density-grades/', {'code': '20', 'min_kg_m3': '18', 'max_kg_m3': '20'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

        resp = self.client.get('/api/foam/density-grades/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('meta', resp.data)
        self.assertEqual(len(resp.data['items']), 1)

    def test_production_run_cube_formula_and_side_effects(self):
        lot = self._create_lot(bag_weight_kg='800')
        self._create_grade(code='14-15', min_kg_m3='13', max_kg_m3='15.5')

        resp = self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '90', 'output_format': 'cube', 'grade_code': '14-15'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        run = resp.data
        # usable = 90*0.965 = 86.85; mid_density=14.25; cube_weight=14.25*1.2=17.1; qty=86.85/17.1=5.0789...->5.1
        self.assertEqual(Decimal(run['output_qty']), Decimal('5.1'))
        self.assertEqual(run['grade_code'], '14-15')

        lot_after = self.client.get(f"/api/foam/raw-lots/{lot['id']}/").data
        self.assertEqual(Decimal(lot_after['remaining_kg']), Decimal('710'))

        stock = self.client.get('/api/foam/gp-stock/').data['items']
        cube_row = next(r for r in stock if r['output_format'] == 'cube')
        self.assertEqual(Decimal(cube_row['qty']), Decimal('5.1'))

        ops = self.client.get('/api/foam/gp-operations/').data['items']
        self.assertTrue(any(o['kind'] == 'production_intake' for o in ops))

    def test_production_run_granule_no_grade_code(self):
        lot = self._create_lot(bag_weight_kg='100')
        resp = self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '10', 'output_format': 'granule'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data['grade_code'])
        # usable = 10*0.965 = 9.65 -> round half up -> 9.7
        self.assertEqual(Decimal(resp.data['output_qty']), Decimal('9.7'))

    def test_production_run_input_exceeds_remaining_returns_400(self):
        lot = self._create_lot(bag_weight_kg='10')
        resp = self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '999', 'output_format': 'granule'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cut_cube_into_sheets(self):
        lot = self._create_lot(bag_weight_kg='800')
        self._create_grade(code='14-15', min_kg_m3='13', max_kg_m3='15.5')
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '90', 'output_format': 'cube', 'grade_code': '14-15'},
            format='json',
        )
        cube_row = next(
            r for r in self.client.get('/api/foam/gp-stock/').data['items'] if r['output_format'] == 'cube'
        )

        resp = self.client.post(
            '/api/foam/gp-stock/cut/',
            {'cube_stock_id': cube_row['id'], 'thickness_cm': 3, 'cubes_qty': '1.5'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        # sheets_per_cube = floor(60/3)=20; sheets_qty = floor(20*1.5)=30
        self.assertEqual(Decimal(resp.data['sheet_stock']['qty']), Decimal('30'))
        self.assertEqual(Decimal(resp.data['cube_stock']['qty']), Decimal('3.6'))

        ops = self.client.get('/api/foam/gp-operations/').data['items']
        kinds = [o['kind'] for o in ops]
        self.assertIn('cut_in', kinds)
        self.assertIn('cut_out', kinds)

    def test_sale_insufficient_stock_returns_400_and_leaves_stock_untouched(self):
        lot = self._create_lot(bag_weight_kg='100')
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '10', 'output_format': 'granule'},
            format='json',
        )
        stock_row = self.client.get('/api/foam/gp-stock/').data['items'][0]

        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '999', 'unit_price': '10'}],
                'paid_amount': '0',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        stock_after = self.client.get('/api/foam/gp-stock/').data['items'][0]
        self.assertEqual(stock_row['qty'], stock_after['qty'])

    def test_sale_success_computes_totals_on_backend(self):
        lot = self._create_lot(bag_weight_kg='100')
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '10', 'output_format': 'granule'},
            format='json',
        )
        stock_row = self.client.get('/api/foam/gp-stock/').data['items'][0]
        qty_before = Decimal(stock_row['qty'])

        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '5', 'unit_price': '45'}],
                'paid_amount': '100',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        sale = resp.data
        self.assertEqual(Decimal(sale['total_amount']), Decimal('225.00'))
        self.assertEqual(Decimal(sale['debt_amount']), Decimal('125.00'))
        self.assertEqual(sale['payment_status'], 'partial')
        self.assertTrue(sale['date'].startswith('2026-07-25'))

        stock_after = self.client.get('/api/foam/gp-stock/').data['items'][0]
        self.assertEqual(Decimal(stock_after['qty']), qty_before - Decimal('5'))

        ops = self.client.get('/api/foam/gp-operations/?kind=sale').data['items']
        self.assertTrue(any(Decimal(o['qty']) == Decimal('-5') for o in ops))

        listed = self.client.get('/api/foam/sales/').data
        self.assertGreaterEqual(listed['meta']['total'], 1)

    def _stock_row(self, kg='100'):
        lot = self._create_lot(bag_weight_kg=kg)
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': kg, 'output_format': 'granule'},
            format='json',
        )
        return self.client.get('/api/foam/gp-stock/').data['items'][0]

    def test_sale_discount_reduces_total_and_debt(self):
        stock_row = self._stock_row()
        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '5', 'unit_price': '45'}],
                'discount_amount': '25',
                'paid_amount': '200',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Decimal(resp.data['total_amount']), Decimal('200.00'))
        self.assertEqual(Decimal(resp.data['discount_amount']), Decimal('25.00'))
        self.assertEqual(resp.data['payment_status'], 'paid')

    def test_sale_discount_over_subtotal_rejected(self):
        stock_row = self._stock_row()
        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '5', 'unit_price': '45'}],
                'discount_amount': '999',
                'paid_amount': '0',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sale_requires_known_client_id(self):
        stock_row = self._stock_row()
        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': 999999,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '1', 'unit_price': '10'}],
                'paid_amount': '0',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sale_debt_counts_toward_shared_credit_limit(self):
        """
        Лимит долга общий на клиента — не отдельный по товарной линии.
        Кассир — обычный пользователь без права обхода лимита: суперпользователь
        (self.user) хард-блок обходит неявно (can_override_credit_limit), это
        отдельная, уже проверенная в apps.sales логика, не то, что тестируем тут.
        """
        from apps.accounts.models import UserAccess

        user_model = get_user_model()
        cashier = user_model.objects.create_user(email='foam-cashier@example.com', password='pass12345', name='Кассир')
        for key in ('sales', 'materials', 'production'):
            UserAccess.objects.get_or_create(user=cashier, access_key=key)
        self.client.force_authenticate(cashier)

        self.crm_client.credit_limit = Decimal('100')
        self.crm_client.credit_limit_mode = 'hard'
        self.crm_client.save(update_fields=['credit_limit', 'credit_limit_mode'])

        stock_row = self._stock_row()
        resp = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '5', 'unit_price': '45'}],
                'paid_amount': '0',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY, resp.data)
        self.assertEqual(resp.data['code'], 'CREDIT_LIMIT_EXCEEDED')

        debt_resp = self.client.get(f'/api/foam/sales/client-debt/?client_id={self.crm_client.pk}')
        self.assertEqual(debt_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(debt_resp.data['current_debt']), Decimal('0'))
        self.assertEqual(debt_resp.data['blocked'], False)  # additional_amount=0 в справочном вызове

        # Полностью оплаченная продажа (unpaid=0) лимит не задевает.
        ok = self.client.post(
            '/api/foam/sales/',
            {
                'client_id': self.crm_client.pk,
                'sale_date': '2026-07-25',
                'lines': [{'stock_id': stock_row['id'], 'qty': '5', 'unit_price': '45'}],
                'paid_amount': '225',
            },
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED, ok.data)

        debt_resp2 = self.client.get(f'/api/foam/sales/client-debt/?client_id={self.crm_client.pk}')
        self.assertEqual(Decimal(debt_resp2.data['current_debt']), Decimal('0'))

    def test_raw_lot_update_allows_only_name_and_supplier(self):
        lot = self._create_lot(bag_weight_kg='800')
        resp = self.client.patch(
            f"/api/foam/raw-lots/{lot['id']}/",
            {'material_name': 'Гранула EPS Kingeps HS (испр.)', 'supplier': 'Новый поставщик', 'bag_weight_kg': '999'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['material_name'], 'Гранула EPS Kingeps HS (испр.)')
        self.assertEqual(resp.data['supplier'], 'Новый поставщик')

        # bag_weight_kg/remaining_kg проигнорированы — количество руками не меняется
        lot_after = self.client.get(f"/api/foam/raw-lots/{lot['id']}/").data
        self.assertEqual(Decimal(lot_after['bag_weight_kg']), Decimal('800'))
        self.assertEqual(Decimal(lot_after['remaining_kg']), Decimal('800'))

    def test_raw_lot_delete_untouched_lot_succeeds(self):
        lot = self._create_lot(bag_weight_kg='800')
        resp = self.client.delete(f"/api/foam/raw-lots/{lot['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        resp = self.client.get('/api/foam/raw-lots/')
        self.assertEqual(resp.data['meta']['total'], 0)

    def test_raw_lot_delete_used_lot_conflicts(self):
        lot = self._create_lot(bag_weight_kg='100')
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '10', 'output_format': 'granule'},
            format='json',
        )
        resp = self.client.delete(f"/api/foam/raw-lots/{lot['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['code'], 'LOT_IN_USE')

    def test_density_grade_update_and_duplicate_conflict(self):
        grade_a = self._create_grade(code='A-1', min_kg_m3='10', max_kg_m3='10')
        self._create_grade(code='B-1', min_kg_m3='12', max_kg_m3='12')

        resp = self.client.patch(
            f"/api/foam/density-grades/{grade_a['id']}/",
            {'min_kg_m3': '11', 'max_kg_m3': '11'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(Decimal(resp.data['min_kg_m3']), Decimal('11'))

        # Переименование в уже занятый код — конфликт, не тихая перезапись.
        resp = self.client.patch(
            f"/api/foam/density-grades/{grade_a['id']}/", {'code': 'B-1'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

    def test_density_grade_delete_unused_grade_succeeds(self):
        grade = self._create_grade(code='UNUSED-1', min_kg_m3='10', max_kg_m3='10')
        resp = self.client.delete(f"/api/foam/density-grades/{grade['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_raw_lot_accepts_unit_price_and_received_at(self):
        resp = self.client.post(
            '/api/foam/raw-lots/',
            {
                'material_name': 'Гранула EPS Kingeps HS',
                'supplier': 'Kingeps',
                'bag_weight_kg': '500',
                'unit_price': '85.50',
                'received_at': '2026-01-15T00:00:00Z',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Decimal(resp.data['unit_price']), Decimal('85.50'))
        self.assertTrue(resp.data['received_at'].startswith('2026-01-15'))

        # Цену/дату можно поправить и позже — только это и material_name/supplier.
        resp = self.client.patch(
            f"/api/foam/raw-lots/{resp.data['id']}/",
            {'unit_price': '90.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(Decimal(resp.data['unit_price']), Decimal('90.00'))

    def test_density_grade_delete_used_grade_conflicts(self):
        lot = self._create_lot(bag_weight_kg='800')
        grade = self._create_grade(code='14-15', min_kg_m3='13', max_kg_m3='15.5')
        self.client.post(
            '/api/foam/production-runs/',
            {'lot_id': lot['id'], 'input_kg': '90', 'output_format': 'cube', 'grade_code': '14-15'},
            format='json',
        )
        resp = self.client.delete(f"/api/foam/density-grades/{grade['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['code'], 'DENSITY_GRADE_IN_USE')
