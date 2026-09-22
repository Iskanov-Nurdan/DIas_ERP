"""POST /api/workshop/otk-account/ — multi-blank, shift, packers."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.recipes.models import PlasticProfile
from apps.workshop.models import OtkAccountSession, OtkBlankPool, WorkshopBlank, WorkshopPreparedState


class OtkAccountV2ApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='otk-v2@example.com',
            password='pass12345',
            name='OTK V2',
        )
        self.packer1 = user_model.objects.create_user(
            email='packer1@example.com',
            password='pass12345',
            name='Упаковщик 1',
        )
        self.packer2 = user_model.objects.create_user(
            email='packer2@example.com',
            password='pass12345',
            name='Упаковщик 2',
        )
        self.client.force_authenticate(self.user)

        self.blank_a = WorkshopBlank.objects.create(name='Заготовка A', recipe_kg_per_barrel=Decimal('50'))
        self.blank_b = WorkshopBlank.objects.create(name='Заготовка B', recipe_kg_per_barrel=Decimal('40'))
        for blank in (self.blank_a, self.blank_b):
            WorkshopPreparedState.objects.create(blank=blank, barrels=10, extra_kg=Decimal('0'))

        self.profile_a = PlasticProfile.objects.create(
            name='Профиль A',
            code='OTK-A',
            weight_kg_per_piece=Decimal('2.0'),
            blank=self.blank_a,
            is_active=True,
        )
        self.profile_b = PlasticProfile.objects.create(
            name='Профиль B',
            code='OTK-B',
            weight_kg_per_piece=Decimal('1.0'),
            blank=self.blank_b,
            is_active=True,
        )

    def _produce(self, blank_id, kg='100'):
        return self.client.post(
            '/api/workshop/blank-production-runs/',
            {
                'blank_id': blank_id,
                'blank_total_kg': kg,
                'blank_used_in_production_kg': kg,
                'vat_max_kg_demo': '200',
            },
            format='json',
        )

    def test_v2_account_single_blank(self):
        self._produce(self.blank_a.pk)
        resp = self.client.post(
            '/api/workshop/otk-account/',
            {
                'lines': [{'profile_id': self.profile_a.pk, 'pieces': 10}],
                'defect_kg': '5',
                'shift_period': 'day',
                'operator_id': self.user.pk,
                'packer_ids': [self.packer1.pk, self.packer2.pk],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['shift_period'], 'day')
        self.assertEqual(resp.data['packer_ids'], [self.packer1.pk, self.packer2.pk])
        self.assertEqual(len(resp.data['packer_names']), 2)
        self.assertEqual(len(resp.data['blank_breakdown']), 1)
        self.assertEqual(resp.data['blank_breakdown'][0]['blank_id'], self.blank_a.pk)

        pool = OtkBlankPool.objects.get(blank_id=self.blank_a.pk)
        self.assertLess(pool.remaining_kg, Decimal('100'))

    def test_v2_multi_blank(self):
        self._produce(self.blank_a.pk)
        self._produce(self.blank_b.pk, kg='50')
        resp = self.client.post(
            '/api/workshop/otk-account/',
            {
                'lines': [
                    {'profile_id': self.profile_a.pk, 'pieces': 5},
                    {'profile_id': self.profile_b.pk, 'pieces': 10},
                ],
                'shift_period': 'night',
                'defect_kg': '0',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['shift_period'], 'night')
        self.assertEqual(len(resp.data['blank_breakdown']), 2)
        blank_ids = {row['blank_id'] for row in resp.data['blank_breakdown']}
        self.assertEqual(blank_ids, {self.blank_a.pk, self.blank_b.pk})

    def test_v2_pool_overage_per_blank(self):
        self._produce(self.blank_b.pk, kg='5')
        resp = self.client.post(
            '/api/workshop/otk-account/',
            {
                'lines': [{'profile_id': self.profile_b.pk, 'pieces': 10}],
                'shift_period': 'day',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('error'), 'Недостаточно кг в пуле ОТК')
        self.assertEqual(resp.data.get('blank_id'), self.blank_b.pk)

    def test_v2_defect_blank_id(self):
        self._produce(self.blank_a.pk)
        self._produce(self.blank_b.pk, kg='30')
        resp = self.client.post(
            '/api/workshop/otk-account/',
            {
                'lines': [{'profile_id': self.profile_a.pk, 'pieces': 2}],
                'defect_kg': '10',
                'defect_blank_id': self.blank_b.pk,
                'shift_period': 'day',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        session = OtkAccountSession.objects.get(pk=resp.data['id'])
        self.assertEqual(session.defect_blank_id, self.blank_b.pk)
        self.assertEqual(len(resp.data['blank_breakdown']), 2)

    def test_accounting_list_has_shift_and_packers(self):
        self._produce(self.blank_a.pk)
        create = self.client.post(
            '/api/workshop/otk-account/',
            {
                'lines': [{'profile_id': self.profile_a.pk, 'pieces': 1}],
                'shift_period': 'day',
                'packer_ids': [self.packer1.pk],
            },
            format='json',
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        listed = self.client.get('/api/workshop/otk-accounting/')
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        items = listed.data.get('items') or listed.data.get('results') or []
        row = next(i for i in items if i['id'] == create.data['id'])
        self.assertEqual(row['shift_period'], 'day')
        self.assertEqual(row['packer_ids'], [self.packer1.pk])
