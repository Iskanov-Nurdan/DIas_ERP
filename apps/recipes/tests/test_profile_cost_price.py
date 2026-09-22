"""cost_price: read-only, расчёт после OTK account."""
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


class ProfileCostPriceApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='cost@example.com',
            password='pass12345',
            name='Cost',
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
        WorkshopBlankCompositionLine.objects.create(
            blank=self.blank,
            raw_material=self.rm,
            quantity_kg=Decimal('50'),
        )
        WorkshopPreparedState.objects.create(
            blank=self.blank, barrels=20, extra_kg=Decimal('0')
        )

    def test_create_profile_cost_price_null(self):
        resp = self.client.post(
            '/api/plastic-profiles/',
            {
                'name': 'Новый профиль',
                'weight_kg_per_piece': '2.0',
                'markup_amount': '30',
                'blank_id': self.blank.pk,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data.get('cost_price'))

        detail = self.client.get(f'/api/plastic-profiles/{resp.data["id"]}/')
        self.assertIsNone(detail.data.get('cost_price'))

    def test_patch_cost_price_ignored(self):
        profile = PlasticProfile.objects.create(
            name='Профиль',
            code='CP-1',
            weight_kg_per_piece=Decimal('1.7'),
            blank=self.blank,
            is_active=True,
        )
        resp = self.client.patch(
            f'/api/plastic-profiles/{profile.pk}/',
            {'cost_price': '999', 'markup_amount': '40'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        profile.refresh_from_db()
        self.assertIsNone(profile.cost_price)
        self.assertEqual(profile.markup_amount, Decimal('40'))

    def test_otk_account_sets_cost_price(self):
        profile = PlasticProfile.objects.create(
            name='Профиль ОТК',
            code='CP-OTK',
            weight_kg_per_piece=Decimal('1.0'),
            blank=self.blank,
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
        self.assertEqual(account.status_code, status.HTTP_201_CREATED)

        profile.refresh_from_db()
        self.assertIsNotNone(profile.cost_price)
        self.assertGreater(profile.cost_price, Decimal('0'))

        detail = self.client.get(f'/api/plastic-profiles/{profile.pk}/')
        self.assertIsNotNone(detail.data['cost_price'])
        self.assertGreater(Decimal(str(detail.data['cost_price'])), Decimal('0'))
