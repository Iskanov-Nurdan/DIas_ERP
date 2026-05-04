from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.materials.models import MaterialBatch, RawMaterial
from apps.recipes.models import PlasticProfile, Recipe, RecipeComponent
from apps.warehouse.models import WarehouseBatch


class RecipeReworkStockComponentTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='recipe-rework-admin@example.com',
            password='pass12345',
            name='Recipes Admin',
        )
        self.client.force_authenticate(self.user)
        self.profile = PlasticProfile.objects.create(name='Профиль 60', code='PR-RW', is_active=True)
        self.rework_batch = WarehouseBatch.objects.create(
            product='Белый профиль 60 мм',
            quantity=Decimal('120'),
            length_per_piece=Decimal('6'),
            date=date(2026, 5, 3),
            status=WarehouseBatch.STATUS_AVAILABLE,
            quality=WarehouseBatch.QUALITY_GOOD,
            profile=self.profile,
            stock_bucket=WarehouseBatch.STOCK_BUCKET_REWORKED,
        )

    def test_patch_and_read_rework_stock_component_and_availability(self):
        created = self.client.post(
            '/api/recipes/',
            {
                'recipe': 'Рецепт с переделкой',
                'profile_id': self.profile.pk,
                'base_unit': 'per_meter',
                'components': [],
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        rid = created.data['id']

        patch = self.client.patch(
            f'/api/recipes/{rid}/',
            {
                'components': [
                    {
                        'type': 'rework_stock',
                        'rework_warehouse_batch_id': self.rework_batch.pk,
                        'quantity_per_meter': '0.5',
                        'unit': 'кг',
                    },
                ],
            },
            format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)

        detail = self.client.get(f'/api/recipes/{rid}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        comps = detail.data['components']
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]['type'], 'rework_stock')
        self.assertEqual(comps[0]['rework_warehouse_batch_id'], self.rework_batch.pk)
        self.assertEqual(comps[0]['warehouse_batch_id'], self.rework_batch.pk)

        rc = RecipeComponent.objects.get(recipe_id=rid)
        self.assertEqual(rc.type, RecipeComponent.TYPE_REWORK_STOCK)

        avail = self.client.get(f'/api/recipes/{rid}/availability/')
        self.assertEqual(avail.status_code, status.HTTP_200_OK)
        rows = avail.data['components']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'rework_stock')
        self.assertEqual(rows[0]['rework_warehouse_batch_id'], self.rework_batch.pk)
        self.assertIn('quantity_per_meter', rows[0])
        self.assertIn('available', rows[0])
        self.assertIn('sufficient', rows[0])

    def test_fractional_quantity_per_meter_is_preserved_and_calculated(self):
        material = RawMaterial.objects.create(name='ПВХ', unit='kg', is_active=True)
        MaterialBatch.objects.create(
            material=material,
            quantity_initial=Decimal('3.9000'),
            quantity_remaining=Decimal('3.9000'),
            unit='kg',
            unit_price=Decimal('10'),
            received_at=timezone.now(),
        )

        created = self.client.post(
            '/api/recipes/',
            {
                'recipe': 'Дробная норма',
                'profile_id': self.profile.pk,
                'base_unit': 'per_meter',
                'components': [
                    {
                        'type': 'raw_material',
                        'material_id': material.pk,
                        'quantity_per_meter': 0.04,
                        'unit': 'кг',
                    },
                ],
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        rid = created.data['id']

        component = RecipeComponent.objects.get(recipe_id=rid)
        self.assertEqual(component.quantity_per_meter, Decimal('0.040000'))

        detail = self.client.get(f'/api/recipes/{rid}/')
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['components'][0]['quantity_per_meter'], '0.04')

        availability = self.client.get(f'/api/recipes/{rid}/availability/?total_meters=100')
        self.assertEqual(availability.status_code, status.HTTP_200_OK)
        row = availability.data['components'][0]
        self.assertEqual(row['norm_per_meter_kg'], '0.04')
        self.assertEqual(row['required_total_kg'], '4')
        self.assertEqual(row['available_kg'], '3.9')
        self.assertEqual(row['shortage_kg'], '0.1')
        self.assertFalse(row['sufficient'])
        self.assertFalse(availability.data['all_sufficient'])
