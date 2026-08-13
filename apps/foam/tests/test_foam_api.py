from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase


class FoamApiAcceptanceTests(APITestCase):
    """Чек-лист приёмки из BACKEND_FOAM_REQUIREMENTS.md §8."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='foam-admin@example.com', password='pass12345', name='Foam Admin'
        )
        self.client.force_authenticate(self.user)

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
                'client': 'ТОО СтройМир',
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
                'client': 'ТОО СтройМир',
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
