from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import UserAccess
from apps.realtime.access import user_may_receive_resource

User = get_user_model()


class ResourceAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='access@test.local',
            password='pass12345',
            name='Access Test',
        )
        UserAccess.objects.filter(user=self.user).delete()
        UserAccess.objects.create(user=self.user, access_key='materials')

    def test_materials_user_gets_raw_material(self):
        keys = set(self.user.get_access_keys())
        assert user_may_receive_resource(
            self.user, 'raw_material', user_keys=keys, is_superuser=False
        )

    def test_materials_user_blocked_from_sale(self):
        keys = set(self.user.get_access_keys())
        assert not user_may_receive_resource(
            self.user, 'sale', user_keys=keys, is_superuser=False
        )

    def test_superuser_gets_everything(self):
        assert user_may_receive_resource(
            self.user, 'sale', user_keys=set(), is_superuser=True
        )
