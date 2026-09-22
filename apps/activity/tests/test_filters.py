from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.activity.models import UserActivity


class ActivityFilterTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            email='activity-filters-admin@example.com',
            password='pass12345',
            name='Admin Filters',
        )
        self.other_user = user_model.objects.create_user(
            email='activity-filters-other@example.com',
            password='pass12345',
            name='Other Person',
        )
        self.client.force_authenticate(self.admin)

        UserActivity.objects.create(
            user=self.admin,
            action='create',
            section='Клиенты',
            description='Создан клиент Иванов',
            summary='Создан клиент Иванов',
            entity_type='sales.client',
            entity_id='1',
        )
        UserActivity.objects.create(
            user=self.admin,
            action='update',
            section='Склад',
            description='Резерв партии склада',
            summary='Резерв партии',
            entity_type='warehouse.warehousebatch',
            entity_id='2',
        )
        UserActivity.objects.create(
            user=self.other_user,
            action='delete',
            section='Клиенты',
            description='Удалён клиент Петров',
            summary='Удалён клиент Петров',
            entity_type='sales.client',
            entity_id='3',
        )

    def test_section_filter_narrows_results(self):
        resp = self.client.get('/api/activity/?section=Клиенты')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 2)
        for item in resp.data['items']:
            self.assertEqual(item['section'], 'Клиенты')

    def test_section_filter_no_match(self):
        resp = self.client.get('/api/activity/?section=Несуществующий')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 0)

    def test_search_matches_description(self):
        resp = self.client.get('/api/activity/?search=Иванов')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 1)
        self.assertIn('Иванов', resp.data['items'][0]['description'])

    def test_search_matches_user_name(self):
        resp = self.client.get('/api/activity/?search=Other Person')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['user_name'], 'Other Person')

    def test_search_case_insensitive_partial(self):
        # ASCII case-fold check: sqlite's default `icontains` only case-folds
        # ASCII, so this uses the Latin user name rather than Cyrillic text
        # (Cyrillic case-insensitivity is exercised for real on Postgres/ILIKE
        # in production, but is not portable to the sqlite test backend).
        resp = self.client.get('/api/activity/?search=OTHER PERSON')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['user_name'], 'Other Person')

    def test_section_and_search_and_action_combine(self):
        resp = self.client.get('/api/activity/?section=Клиенты&search=Петров&action=delete')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['entity_id'], '3')

    def test_section_and_action_no_match_when_combined_wrong(self):
        resp = self.client.get('/api/activity/?section=Клиенты&action=update')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 0)

    def test_empty_params_return_everything(self):
        resp = self.client.get('/api/activity/?section=&search=')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 3)

    def test_no_params_return_everything(self):
        resp = self.client.get('/api/activity/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 3)

    def test_my_view_supports_section_and_search(self):
        self.client.force_authenticate(self.other_user)
        resp = self.client.get('/api/activity/my/?section=Клиенты&search=Петров')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['entity_id'], '3')
