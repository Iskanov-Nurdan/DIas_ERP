"""ОТК: produce → pool → account → gp-stock."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.recipes.models import PlasticProfile
from apps.workshop.models import OtkBlankPool, WorkshopBlank, WorkshopPreparedState
class OtkApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='otk@example.com',
            password='pass12345',
            name='OTK',
        )
        self.client.force_authenticate(self.user)

        self.profile = PlasticProfile.objects.create(
            name='Профиль белый',
            code='OTK-P1',
            weight_kg_per_piece=Decimal('1.7'),
            is_active=True,
        )
        self.blank = WorkshopBlank.objects.create(
            name='ПВХ белая',
            recipe_kg_per_barrel=Decimal('50'),
        )
        WorkshopPreparedState.objects.create(
            blank=self.blank, barrels=10, extra_kg=Decimal('0')
        )

    def test_produce_account_gp_stock(self):
        produce = self.client.post(
            '/api/workshop/blank-production-runs/',
            {
                'blank_id': self.blank.pk,
                'blank_total_kg': '100',
                'blank_used_in_production_kg': '100',
                'vat_max_kg_demo': '180',
            },
            format='json',
        )
        self.assertEqual(produce.status_code, status.HTTP_201_CREATED)

        pools = self.client.get('/api/workshop/otk-blanks/')
        self.assertEqual(pools.status_code, status.HTTP_200_OK)
        items = pools.data.get('items') or pools.data.get('results') or []
        self.assertTrue(any(i['blank_id'] == self.blank.pk for i in items))

        account = self.client.post(
            f'/api/workshop/otk-blanks/{self.blank.pk}/account/',
            {
                'lines': [{'profile_id': self.profile.pk, 'pieces': 10}],
                'defect': {'unit': 'kg', 'value': '5'},
                'operator_id': self.user.pk,
            },
            format='json',
        )
        self.assertEqual(account.status_code, status.HTTP_201_CREATED)

        pool = OtkBlankPool.objects.get(blank_id=self.blank.pk)
        self.assertLess(pool.remaining_kg, Decimal('100'))

        stock = self.client.get('/api/warehouse/gp-stock/')
        self.assertEqual(stock.status_code, status.HTTP_200_OK)
        row = next((x for x in stock.data['items'] if x['product_id'] == self.profile.pk), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['pieces'], 10)
