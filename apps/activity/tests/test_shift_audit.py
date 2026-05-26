from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.activity.models import UserActivity
from apps.activity.shift_audit import apply_shift_context_policy, is_shift_audited_entity_type
from apps.production.models import Shift


class ShiftAuditPolicyTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            email='shift-audit@example.com',
            password='pass12345',
            name='Shift Audit',
        )
        self.client.force_authenticate(self.user)
        self.shift = Shift.objects.create(
            line=None,
            user=self.user,
            status=Shift.STATUS_OPEN,
            opened_at=timezone.now() - timedelta(hours=1),
        )

    def test_whitelist_contains_operational_types(self):
        self.assertTrue(is_shift_audited_entity_type('sales.client'))
        self.assertFalse(is_shift_audited_entity_type('accounts.user'))

    def test_apply_shift_context_policy_strips_non_whitelist(self):
        sid, lid, ev = apply_shift_context_policy('accounts.user', 10, 2, 3)
        self.assertIsNone(sid)
        self.assertIsNone(lid)
        self.assertIsNone(ev)
        sid2, _, _ = apply_shift_context_policy('sales.sale', 10, 2, 3)
        self.assertEqual(sid2, 10)

    def test_activity_my_shift_id_excludes_non_whitelist(self):
        mid = self.shift.opened_at + timedelta(minutes=10)
        UserActivity.objects.create(
            user=self.user,
            action='update',
            section='Пользователи',
            description='user edit',
            entity_type='accounts.user',
            entity_id='1',
            shift=self.shift,
            created_at=mid,
        )
        UserActivity.objects.create(
            user=self.user,
            action='update',
            section='Клиенты',
            description='client edit',
            entity_type='sales.client',
            entity_id='2',
            shift=self.shift,
            created_at=mid,
        )
        resp = self.client.get(f'/api/activity/my/?shift_id={self.shift.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        types = {item['entity_type'] for item in resp.data['items']}
        self.assertIn('sales.client', types)
        self.assertNotIn('accounts.user', types)

    def test_activity_my_shift_id_includes_legacy_whitelist_in_interval(self):
        mid = self.shift.opened_at + timedelta(minutes=15)
        UserActivity.objects.create(
            user=self.user,
            action='update',
            section='Продажи',
            description='sale legacy',
            entity_type='sales.sale',
            entity_id='99',
            shift=None,
            created_at=mid,
        )
        resp = self.client.get(f'/api/activity/my/?shift_id={self.shift.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(r['entity_id'] == '99' for r in resp.data['items']))
