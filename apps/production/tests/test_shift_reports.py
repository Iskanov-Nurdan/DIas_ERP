"""
Закрытие смены (касса дня) + фотоотчёты по смене — отдельная фича от ShiftViewSet
(учёт рабочего времени, open/close/my/history). Гейтится ключом доступа 'shifts'.
"""
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import UserAccess
from apps.production.models import ShiftClosing, ShiftPhotoReport, ShiftPhotoReportImage

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:  # pragma: no cover
    _HAS_PIL = False


def _make_image_file(name='photo.jpg'):
    if _HAS_PIL:
        buf = BytesIO()
        Image.new('RGB', (10, 10), color='red').save(buf, format='JPEG')
        buf.seek(0)
        return SimpleUploadedFile(name, buf.read(), content_type='image/jpeg')
    # Фоллбек, если Pillow недоступен в окружении: ImageField-валидация всё равно
    # требует читаемого изображения, поэтому в норме PIL в venv уже есть.
    return SimpleUploadedFile(name, b'not-a-real-image', content_type='image/jpeg')


class ShiftClosingApiTests(APITestCase):
    def setUp(self):
        # Примечание: apps.accounts.signals.assign_default_role_for_new_user уже сажает
        # новому пользователю ПОЛНЫЙ settings.ACCESS_KEYS (включая 'shifts') автоматически —
        # явно создавать UserAccess(access_key='shifts') не нужно (и приведёт к UNIQUE-конфликту).
        # Для сценария «нет доступа» ключи у пользователя удаляются явно.
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            email='sc-admin@example.com', password='pass12345', name='Admin',
        )
        self.author = user_model.objects.create_user(
            email='sc-author@example.com', password='pass12345', name='Автор',
        )
        self.other = user_model.objects.create_user(
            email='sc-other@example.com', password='pass12345', name='Другой',
        )
        self.no_access = user_model.objects.create_user(
            email='sc-noacc@example.com', password='pass12345', name='Без доступа',
        )
        UserAccess.objects.filter(user=self.no_access).delete()

    def test_create_recomputes_total_ignoring_client_value(self):
        self.client.force_authenticate(self.author)
        resp = self.client.post('/api/shift-closings/', {
            'cash': '1000.00', 'card': '500.00', 'expense': '100', 'advance': '0',
            'total': '999999', 'description': 'смена 1',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['total'], '1500.00')
        self.assertEqual(resp.data['isEdited'], False)
        self.assertIsNone(resp.data['previous'])
        self.assertEqual(resp.data['employeeName'], 'Автор')

    def test_edit_once_then_blocked(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post('/api/shift-closings/', {
            'cash': '100', 'card': '50',
        }, format='json')
        closing_id = create_resp.data['id']

        edit_resp = self.client.patch(f'/api/shift-closings/{closing_id}/', {
            'cash': '200', 'card': '50',
        }, format='json')
        self.assertEqual(edit_resp.status_code, status.HTTP_200_OK, edit_resp.data)
        self.assertEqual(edit_resp.data['total'], '250.00')
        self.assertTrue(edit_resp.data['isEdited'])
        self.assertIsNotNone(edit_resp.data['previous'])
        self.assertEqual(edit_resp.data['previous']['cash'], '100.00')
        self.assertEqual(edit_resp.data['previous']['total'], '150.00')

        edit_again = self.client.patch(f'/api/shift-closings/{closing_id}/', {
            'cash': '300',
        }, format='json')
        self.assertEqual(edit_again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_blocked_for_non_admin(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post('/api/shift-closings/', {'cash': '10', 'card': '10'}, format='json')
        closing_id = create_resp.data['id']

        self.client.force_authenticate(self.other)
        resp = self.client.delete(f'/api/shift-closings/{closing_id}/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_same_day_as_admin_ok(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post('/api/shift-closings/', {'cash': '10', 'card': '10'}, format='json')
        closing_id = create_resp.data['id']

        self.client.force_authenticate(self.admin)
        resp = self.client.delete(f'/api/shift-closings/{closing_id}/')
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT))
        self.assertFalse(ShiftClosing.objects.filter(pk=closing_id).exists())

    def test_delete_backdated_as_admin_blocked(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post('/api/shift-closings/', {'cash': '10', 'card': '10'}, format='json')
        closing_id = create_resp.data['id']
        backdated = timezone.now() - timezone.timedelta(days=2)
        ShiftClosing.objects.filter(pk=closing_id).update(created_at=backdated)

        self.client.force_authenticate(self.admin)
        resp = self.client.delete(f'/api/shift-closings/{closing_id}/')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(ShiftClosing.objects.filter(pk=closing_id).exists())

    def test_no_access_key_forbidden(self):
        self.client.force_authenticate(self.no_access)
        list_resp = self.client.get('/api/shift-closings/')
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)
        create_resp = self.client.post('/api/shift-closings/', {'cash': '1', 'card': '1'}, format='json')
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_summary_totals_by_employee_and_coverage(self):
        # 2026-09 — сентябрь 2026: 22е (вторник). Создадим записи на дни 1 (вт), 6 (вс, должен
        # исключаться из missingDays), 10 (чт) для двух сотрудников; день 15 (вт) пропущен.
        self.client.force_authenticate(self.author)
        for day, user in [(1, self.author), (10, self.other)]:
            ShiftClosing.objects.create(
                user=user, cash=Decimal('100'), card=Decimal('50'), expense=Decimal('10'),
                advance=Decimal('5'), total=Decimal('150'),
            )
        # Бэкдейтим created_at на конкретные дни сентября 2026.
        import datetime as dt
        tz = timezone.get_current_timezone()
        c1 = ShiftClosing.objects.filter(user=self.author).order_by('id').first()
        ShiftClosing.objects.filter(pk=c1.pk).update(
            created_at=timezone.make_aware(dt.datetime(2026, 9, 1, 10, 0), tz)
        )
        c2 = ShiftClosing.objects.filter(user=self.other).order_by('id').first()
        ShiftClosing.objects.filter(pk=c2.pk).update(
            created_at=timezone.make_aware(dt.datetime(2026, 9, 10, 10, 0), tz)
        )
        # день 6 сентября 2026 — воскресенье, специально НЕ создаём запись (должен быть исключён
        # из missingDays независимо от отсутствия данных).

        resp = self.client.get('/api/shift-closings/summary/', {'year': 2026, 'month': 9})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        data = resp.data
        self.assertEqual(data['totals']['count'], 2)
        self.assertEqual(data['totals']['total'], '300.00')
        by_emp = {row['employeeId']: row for row in data['byEmployee']}
        self.assertEqual(by_emp[self.author.id]['total'], '150.00')
        self.assertEqual(by_emp[self.other.id]['total'], '150.00')

        # employeeId сужает только totals.
        resp_emp = self.client.get(
            '/api/shift-closings/summary/', {'year': 2026, 'month': 9, 'employeeId': self.author.id}
        )
        self.assertEqual(resp_emp.data['totals']['count'], 1)
        self.assertEqual(resp_emp.data['totals']['total'], '150.00')
        by_emp_narrowed = {row['employeeId']: row for row in resp_emp.data['byEmployee']}
        self.assertIn(self.other.id, by_emp_narrowed)  # byEmployee не сужается

        self.assertIsNotNone(data['coverage'])
        self.assertNotIn(6, data['coverage']['missingDays'])  # воскресенье исключено
        self.assertNotIn(1, data['coverage']['missingDays'])  # есть запись
        self.assertNotIn(10, data['coverage']['missingDays'])  # есть запись


class ShiftPhotoReportApiTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            email='spr-admin@example.com', password='pass12345', name='Admin',
        )
        self.author = user_model.objects.create_user(
            email='spr-author@example.com', password='pass12345', name='Автор',
        )
        self.no_access = user_model.objects.create_user(
            email='spr-noacc@example.com', password='pass12345', name='Без доступа',
        )
        UserAccess.objects.filter(user=self.no_access).delete()

    def test_create_with_two_photos(self):
        self.client.force_authenticate(self.author)
        resp = self.client.post('/api/shift-photo-reports/', {
            'photos': [_make_image_file('a.jpg'), _make_image_file('b.jpg')],
            'description': 'отчёт',
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(len(resp.data['photos']), 2)
        for p in resp.data['photos']:
            self.assertTrue(p['url'])

    def test_create_with_eleven_photos_rejected(self):
        self.client.force_authenticate(self.author)
        files = [_make_image_file(f'{i}.jpg') for i in range(11)]
        resp = self.client.post('/api/shift-photo-reports/', {
            'photos': files,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_blocked_for_non_admin_then_ok_for_admin(self):
        self.client.force_authenticate(self.author)
        create_resp = self.client.post('/api/shift-photo-reports/', {
            'photos': [_make_image_file('a.jpg')],
        }, format='multipart')
        report_id = create_resp.data['id']
        image_path = ShiftPhotoReportImage.objects.get(report_id=report_id).image.path

        resp = self.client.delete(f'/api/shift-photo-reports/{report_id}/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin)
        resp = self.client.delete(f'/api/shift-photo-reports/{report_id}/')
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT))
        self.assertFalse(ShiftPhotoReport.objects.filter(pk=report_id).exists())
        import os
        self.assertFalse(os.path.exists(image_path))

    def test_no_access_key_forbidden(self):
        self.client.force_authenticate(self.no_access)
        list_resp = self.client.get('/api/shift-photo-reports/')
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)
        create_resp = self.client.post('/api/shift-photo-reports/', {
            'photos': [_make_image_file('a.jpg')],
        }, format='multipart')
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)


class MyShiftAccessMigrationTests(APITestCase):
    """
    Юнит-тест логики data-миграции 0027 (вызывает её forwards-функцию напрямую на реальных
    моделях — таблицы уже существуют в тестовой БД, поведение идентично реальной миграции).
    Дополнительно проверено вручную: `python manage.py migrate` + `User.objects.all()` в
    Django shell на девелоперской БД — все ранее существовавшие пользователи получили 'my_shift'.
    """

    def test_grants_to_missing_and_is_idempotent_and_preserves_existing(self):
        import importlib

        migration_module = importlib.import_module(
            'apps.production.migrations.0027_grant_my_shift_access_to_all_users'
        )

        user_model = get_user_model()
        u1 = user_model.objects.create_user(email='mig-u1@example.com', password='pass12345', name='U1')
        u2 = user_model.objects.create_user(email='mig-u2@example.com', password='pass12345', name='U2')
        # Оба юзера при создании уже получили полный ACCESS_KEYS автосидом (см. accounts.signals) —
        # чтобы честно смоделировать «допроизводственное» состояние (до этой миграции), убираем
        # у u1 все ключи вообще, а у u2 оставляем только my_shift (уже был выдан ранее вручную).
        UserAccess.objects.filter(user=u1).delete()
        UserAccess.objects.filter(user=u2).exclude(access_key='my_shift').delete()
        pre_existing_row_id = UserAccess.objects.get(user=u2, access_key='my_shift').id

        class _RealAppsRegistry:
            @staticmethod
            def get_model(app_label, model_name):
                from django.apps import apps as real_apps
                return real_apps.get_model(app_label, model_name)

        migration_module.grant_my_shift_to_all_users(_RealAppsRegistry(), None)

        self.assertIn('my_shift', u1.get_access_keys())
        self.assertIn('my_shift', u2.get_access_keys())
        self.assertEqual(UserAccess.objects.filter(user=u2, access_key='my_shift').count(), 1)
        self.assertEqual(UserAccess.objects.get(user=u2, access_key='my_shift').id, pre_existing_row_id)

        # Повторный запуск — идемпотентен, дублей не создаёт.
        migration_module.grant_my_shift_to_all_users(_RealAppsRegistry(), None)
        self.assertEqual(UserAccess.objects.filter(user=u1, access_key='my_shift').count(), 1)
