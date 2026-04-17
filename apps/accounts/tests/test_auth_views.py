from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class AuthViewsTests(APITestCase):
    def test_logout_allows_anonymous_request_without_refresh(self):
        response = self.client.post('/api/auth/logout', data={}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {'detail': 'OK'})

    def test_me_rejects_anonymous_request_with_json_error(self):
        response = self.client.get('/api/me')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
