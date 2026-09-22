"""Контракт: line_starts → отдельный BlankProductionRun на профиль (ОТК)."""
import unittest
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.production.models import ProductionBatch, Shift
from apps.recipes.models import PlasticProfile
from apps.sales.models import (
    Client,
    Order,
    OrderLine,
    REQUEST_STATUS_READY,
)
from apps.workshop.models import BlankProductionRun, WorkshopBlank, WorkshopPreparedState


@unittest.skip('POST /production/requests/{id}/start/ снят (410); поток через workshop/blank-production-runs/')
class ProductionRequestsApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='prod-req@example.com',
            password='pass12345',
            name='Prod Req',
        )
        self.client.force_authenticate(self.user)
        self.active_client = Client.objects.create(name='Клиент П', is_active=True)
        self.profile1 = PlasticProfile.objects.create(
            name='Профиль 5м',
            code='P5',
            is_active=True,
            weight_kg_per_piece=Decimal('2'),
        )
        self.profile2 = PlasticProfile.objects.create(
            name='Профиль 6м',
            code='P6',
            is_active=True,
            weight_kg_per_piece=Decimal('2'),
        )
        self.blank1 = WorkshopBlank.objects.create(
            name='Заготовка 5м',
            recipe_kg_per_barrel=Decimal('100'),
            plastic_profile=self.profile1,
            is_active=True,
        )
        self.blank2 = WorkshopBlank.objects.create(
            name='Заготовка 6м',
            recipe_kg_per_barrel=Decimal('100'),
            plastic_profile=self.profile2,
            is_active=True,
        )
        WorkshopPreparedState.objects.create(
            blank=self.blank1,
            barrels=2,
            extra_kg=Decimal('0'),
        )
        WorkshopPreparedState.objects.create(
            blank=self.blank2,
            barrels=2,
            extra_kg=Decimal('0'),
        )
        self.personal_shift = Shift.objects.create(
            line=None,
            user=self.user,
            status=Shift.STATUS_OPEN,
            opened_at=timezone.now(),
        )
        self.order = Order.objects.create(
            order_number='ORD-PR-0001',
            date=date(2026, 5, 21),
            client=self.active_client,
            request_status=REQUEST_STATUS_READY,
            production_profile=self.profile1,
            production_length=Decimal('6'),
            production_quantity=10,
        )
        self.line1 = OrderLine.objects.create(
            order=self.order,
            product=self.profile1.name,
            profile=self.profile1,
            ordered_quantity=Decimal('20'),
        )
        self.line2 = OrderLine.objects.create(
            order=self.order,
            product=self.profile2.name,
            profile=self.profile2,
            ordered_quantity=Decimal('10'),
        )

    def test_list_order_lines_have_per_line_allowed_blank_ids(self):
        resp = self.client.get('/api/production/requests/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.data.get('items') or resp.data
        row = next(x for x in items if x['id'] == self.order.pk)
        lines = row.get('order_lines') or []
        self.assertEqual(len(lines), 2)
        line5 = next(l for l in lines if l['profile_id'] == self.profile1.pk)
        line6 = next(l for l in lines if l['profile_id'] == self.profile2.pk)
        self.assertIn(self.blank1.pk, line5['allowed_blank_ids'])
        self.assertIn(self.blank2.pk, line6['allowed_blank_ids'])
        self.assertNotIn(self.blank2.pk, line5['allowed_blank_ids'])

    def test_line_starts_creates_two_otk_runs_with_correct_pairs(self):
        resp = self.client.post(
            f'/api/production/requests/{self.order.pk}/start/',
            {
                'line_starts': [
                    {'order_line_id': self.line1.pk, 'blank': self.blank1.pk},
                    {'order_line_id': self.line2.pk, 'blank': self.blank2.pk},
                ],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        runs = resp.data.get('runs') or []
        self.assertEqual(len(runs), 2)
        self.assertEqual(resp.data['client_request_id'], self.order.pk)

        db_runs = list(
            BlankProductionRun.objects.filter(client_request_id=self.order.pk).order_by('order_line_id'),
        )
        self.assertEqual(len(db_runs), 2)
        r5 = next(r for r in db_runs if r.order_line_id == self.line1.pk)
        r6 = next(r for r in db_runs if r.order_line_id == self.line2.pk)
        self.assertEqual(r5.product_id, self.profile1.pk)
        self.assertEqual(r5.blank_id, self.blank1.pk)
        self.assertEqual(r6.product_id, self.profile2.pk)
        self.assertEqual(r6.blank_id, self.blank2.pk)
        self.assertEqual(ProductionBatch.objects.filter(client_order_id=self.order.pk).count(), 2)

        otk = self.client.get('/api/workshop/blank-production-runs/')
        self.assertEqual(otk.status_code, status.HTTP_200_OK)
        otk_items = otk.data.get('items') or otk.data
        otk_for_order = [x for x in otk_items if x.get('client_request_id') == self.order.pk]
        self.assertEqual(len(otk_for_order), 2)

    def test_single_line_blank_and_order_line_id(self):
        order_single = Order.objects.create(
            order_number='ORD-PR-S1',
            date=date(2026, 5, 22),
            client=self.active_client,
            request_status=REQUEST_STATUS_READY,
            production_profile=self.profile1,
            production_length=Decimal('6'),
            production_quantity=5,
        )
        line = OrderLine.objects.create(
            order=order_single,
            product=self.profile1.name,
            profile=self.profile1,
            ordered_quantity=Decimal('5'),
        )
        resp = self.client.post(
            f'/api/production/requests/{order_single.pk}/start/',
            {'blank': self.blank1.pk, 'order_line_id': line.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        run = BlankProductionRun.objects.get(client_request_id=order_single.pk)
        self.assertEqual(run.order_line_id, line.pk)
        self.assertEqual(run.product_id, self.profile1.pk)

    def test_rejects_blanks_without_line_binding(self):
        resp = self.client.post(
            f'/api/production/requests/{self.order.pk}/start/',
            {'blanks': [self.blank1.pk, self.blank2.pk]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'LINE_STARTS_REQUIRED')

    def test_rejects_line_field(self):
        resp = self.client.post(
            f'/api/production/requests/{self.order.pk}/start/',
            {'line': 1},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'LINE_NOT_SUPPORTED')

    def test_blank_profile_mismatch(self):
        order3 = Order.objects.create(
            order_number='ORD-PR-0003',
            date=date(2026, 5, 23),
            client=self.active_client,
            request_status=REQUEST_STATUS_READY,
            production_profile=self.profile1,
            production_length=Decimal('6'),
            production_quantity=3,
        )
        ln = OrderLine.objects.create(
            order=order3,
            product=self.profile1.name,
            profile=self.profile1,
            ordered_quantity=Decimal('3'),
        )
        resp = self.client.post(
            f'/api/production/requests/{order3.pk}/start/',
            {'line_starts': [{'order_line_id': ln.pk, 'blank': self.blank2.pk}]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'BLANK_PROFILE_MISMATCH')

    def test_no_open_shift_without_line_wording(self):
        self.personal_shift.closed_at = timezone.now()
        self.personal_shift.status = Shift.STATUS_CLOSED
        self.personal_shift.save(update_fields=['closed_at', 'status'])
        order4 = Order.objects.create(
            order_number='ORD-PR-0004',
            date=date(2026, 5, 24),
            client=self.active_client,
            request_status=REQUEST_STATUS_READY,
            production_profile=self.profile1,
            production_length=Decimal('6'),
            production_quantity=3,
        )
        ln = OrderLine.objects.create(
            order=order4,
            product=self.profile1.name,
            profile=self.profile1,
            ordered_quantity=Decimal('3'),
        )
        resp = self.client.post(
            f'/api/production/requests/{order4.pk}/start/',
            {'line_starts': [{'order_line_id': ln.pk, 'blank': self.blank1.pk}]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get('code'), 'NO_OPEN_SHIFT')
        msg = (resp.data.get('message') or resp.data.get('detail') or '').lower()
        self.assertNotIn('на линии', msg)
