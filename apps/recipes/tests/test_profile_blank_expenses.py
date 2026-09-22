"""blank_id, extra expenses, sale_unit_price, OTK blank match."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from django.utils import timezone

from apps.materials.models import MaterialBatch, RawMaterial
from apps.recipes.models import PlasticProfile
from apps.workshop.models import (
    WorkshopBlank,
    WorkshopBlankCompositionLine,
    WorkshopPreparedState,
)


class ProfileBlankExpensesApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='blank-exp@example.com',
            password='pass12345',
            name='BlankExp',
        )
        self.client.force_authenticate(self.user)

        self.rm = RawMaterial.objects.create(name='ПВХ', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=self.rm,
            quantity_initial=Decimal('1000'),
            quantity_remaining=Decimal('1000'),
            unit_price=Decimal('10'),
            received_at=timezone.now(),
        )

        self.blank = WorkshopBlank.objects.create(
            name='ПВХ заготовка',
            recipe_kg_per_barrel=Decimal('50'),
        )
        self.other_blank = WorkshopBlank.objects.create(
            name='Другая заготовка',
            recipe_kg_per_barrel=Decimal('40'),
        )
        WorkshopBlankCompositionLine.objects.create(
            blank=self.blank,
            raw_material=self.rm,
            quantity_kg=Decimal('50'),
        )
        WorkshopPreparedState.objects.create(
            blank=self.blank, barrels=20, extra_kg=Decimal('0')
        )
        WorkshopPreparedState.objects.create(
            blank=self.other_blank, barrels=10, extra_kg=Decimal('0')
        )

    def test_list_profiles_with_ordering(self):
        PlasticProfile.objects.create(
            name='Z профиль',
            code='Z-1',
            weight_kg_per_piece=Decimal('1'),
            blank=self.blank,
            is_active=True,
        )
        resp = self.client.get('/api/plastic-profiles/?ordering=name')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        items = resp.data.get('items') or resp.data.get('results') or []
        self.assertTrue(len(items) >= 1)
        row = items[0]
        self.assertIn('blank_id', row)
        self.assertIn('other_expenses_total', row)
        self.assertIn('sale_unit_price', row)

    def test_post_requires_blank_id(self):
        resp = self.client.post(
            '/api/plastic-profiles/',
            {'name': 'Без заготовки', 'weight_kg_per_piece': '1.0'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            'blank_id' in resp.data
            or any(e.get('field') == 'blank_id' for e in (resp.data.get('errors') or []))
        )

    def test_create_with_expenses_and_sale_unit_price_null(self):
        resp = self.client.post(
            '/api/plastic-profiles/',
            {
                'name': 'Профиль с расходами',
                'weight_kg_per_piece': '1.0',
                'blank_id': self.blank.pk,
                'markup_amount': '30',
                'extra_rubber': '5',
                'extra_label': '2',
                'extra_labor': '15',
                'extra_electricity': '3',
                'extra_repair': '1',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['blank_id'], self.blank.pk)
        self.assertEqual(resp.data['blank_name'], self.blank.name)
        self.assertEqual(resp.data['other_expenses_total'], '26')
        self.assertIsNone(resp.data['cost_price'])
        self.assertIsNone(resp.data['sale_unit_price'])

    def test_otk_account_wrong_blank_rejected(self):
        profile = PlasticProfile.objects.create(
            name='Чужой профиль',
            code='WB-WRONG',
            weight_kg_per_piece=Decimal('1.0'),
            blank=self.other_blank,
            is_active=True,
        )
        self.client.post(
            '/api/workshop/blank-production-runs/',
            {
                'blank_id': self.blank.pk,
                'blank_total_kg': '50',
                'blank_used_in_production_kg': '50',
                'vat_max_kg_demo': '100',
            },
            format='json',
        )
        account = self.client.post(
            f'/api/workshop/otk-blanks/{self.blank.pk}/account/',
            {
                'lines': [{'profile_id': profile.pk, 'pieces': 2}],
                'defect': {'unit': 'kg', 'value': '0'},
            },
            format='json',
        )
        self.assertEqual(account.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(account.data.get('error'), 'Профиль не привязан к этой заготовке')
        self.assertEqual(account.data.get('profile_id'), profile.pk)
        self.assertEqual(account.data.get('expected_blank_id'), self.blank.pk)

    def test_otk_account_sets_sale_unit_price(self):
        profile = PlasticProfile.objects.create(
            name='Профиль ОТК цена',
            code='WB-PRICE',
            weight_kg_per_piece=Decimal('1.0'),
            blank=self.blank,
            markup_amount=Decimal('30'),
            extra_rubber=Decimal('5'),
            extra_label=Decimal('2'),
            extra_labor=Decimal('15'),
            extra_electricity=Decimal('3'),
            extra_repair=Decimal('1'),
            is_active=True,
        )
        self.client.post(
            '/api/workshop/blank-production-runs/',
            {
                'blank_id': self.blank.pk,
                'blank_total_kg': '50',
                'blank_used_in_production_kg': '50',
                'vat_max_kg_demo': '100',
            },
            format='json',
        )
        account = self.client.post(
            f'/api/workshop/otk-blanks/{self.blank.pk}/account/',
            {
                'lines': [{'profile_id': profile.pk, 'pieces': 5}],
                'defect': {'unit': 'kg', 'value': '0'},
            },
            format='json',
        )
        self.assertEqual(account.status_code, status.HTTP_201_CREATED, account.data)

        detail = self.client.get(f'/api/plastic-profiles/{profile.pk}/')
        self.assertIsNotNone(detail.data['cost_price'])
        self.assertEqual(detail.data['other_expenses_total'], '26')
        self.assertIsNotNone(detail.data['sale_unit_price'])
        sale = Decimal(str(detail.data['sale_unit_price']))
        cost = Decimal(str(detail.data['cost_price']))
        self.assertEqual(sale, (cost + Decimal('26') + Decimal('30')).quantize(Decimal('0.01')))

    def test_readonly_pricing_fields_ignored_on_patch(self):
        profile = PlasticProfile.objects.create(
            name='RO',
            code='WB-RO',
            weight_kg_per_piece=Decimal('1'),
            blank=self.blank,
            is_active=True,
        )
        resp = self.client.patch(
            f'/api/plastic-profiles/{profile.pk}/',
            {
                'cost_price': '999',
                'other_expenses_total': '888',
                'sale_unit_price': '777',
                'extra_rubber': '10',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['other_expenses_total'], '10')
        self.assertIsNone(resp.data['sale_unit_price'])
