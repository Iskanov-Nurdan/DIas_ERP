from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.activity.audit_messages import (
    build_audit_summary,
    material_batch_incoming_text,
    shift_audit_text,
)
from apps.activity.models import UserActivity
from apps.materials.models import MaterialBatch, RawMaterial
from apps.production.models import Shift


class AuditMessagesTests(APITestCase):
    def test_shift_open_personal_text(self):
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            email='audit-msg@example.com',
            password='pass12345',
            name='Op',
        )
        shift = Shift.objects.create(
            line=None,
            user=user,
            status=Shift.STATUS_OPEN,
            opened_at=timezone.now(),
        )
        text = shift_audit_text(
            endpoint='POST /api/shifts/open/',
            shift=shift,
            action='create',
        )
        self.assertEqual(text, 'Открыта смена')
        self.assertNotIn('POST', text)
        self.assertNotIn('#', text)

    def test_material_batch_incoming_text_no_iso(self):
        mat = RawMaterial.objects.create(name='Дыма', unit='kg')
        batch = MaterialBatch.objects.create(
            material=mat,
            quantity_initial=1,
            quantity_remaining=1,
            unit='kg',
            received_at=timezone.now(),
        )
        text = material_batch_incoming_text(batch)
        self.assertIn('Дыма', text)
        self.assertIn('Приход:', text)
        self.assertNotIn('T', text)  # без ISO

    def test_build_audit_summary_client_create(self):
        from apps.sales.models import Client

        c = Client.objects.create(name='Иванов')
        summary = build_audit_summary(
            description='Создал клиенты: Иванов',
            action='create',
            model_cls=Client,
            after_instance=c,
        )
        self.assertEqual(summary, 'Создан клиент: Иванов')

    def test_activity_list_shift_summary_human(self):
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            email='audit-list@example.com',
            password='pass12345',
            name='List',
        )
        self.client.force_authenticate(user)
        shift = Shift.objects.create(
            line=None,
            user=user,
            status=Shift.STATUS_CLOSED,
            opened_at=timezone.now(),
            closed_at=timezone.now(),
        )
        UserActivity.objects.create(
            user=user,
            action='update',
            section='Смены',
            description='Смена закрыта',
            summary='Смена закрыта',
            entity_type='production.shift',
            entity_id=str(shift.pk),
            shift=shift,
        )
        UserActivity.objects.create(
            user=user,
            action='update',
            section='Пользователи',
            description='Смена #1: POST /api/shifts/close/',
            summary='Смена #1: POST /api/shifts/close/',
            entity_type='accounts.user',
            entity_id='1',
            shift=shift,
        )
        resp = self.client.get(f'/api/activity/my/?shift_id={shift.pk}')
        self.assertEqual(resp.status_code, 200)
        summaries = [i['summary'] for i in resp.data['items']]
        self.assertIn('Смена закрыта', summaries)
        self.assertNotIn('Смена #1: POST /api/shifts/close/', summaries)
