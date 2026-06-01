import asyncio

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.realtime.broadcast import OPERATIONAL_GROUP, push_operational_event
from config.asgi import application

_WS_HEADERS = [(b'origin', b'http://localhost:3000')]


@override_settings(
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)
class OperationalWsTests(TransactionTestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='ws@test.local',
            password='pass12345',
            name='WS Test',
        )
        self.token = str(AccessToken.for_user(self.user))

    def test_invalid_token_close_4001(self):
        async def _run():
            comm = WebsocketCommunicator(
                application, '/ws/operational/?token=not-a-jwt', headers=_WS_HEADERS
            )
            connected, code = await comm.connect()
            assert not connected
            assert code == 4001
            await comm.disconnect()

        asyncio.run(_run())

    def test_connected_frame(self):
        async def _run():
            comm = WebsocketCommunicator(
                application, f'/ws/operational/?token={self.token}', headers=_WS_HEADERS
            )
            connected, _ = await comm.connect()
            assert connected
            msg = await comm.receive_json_from()
            assert msg == {
                'event': 'connected',
                'protocol_version': 1,
                'user_id': self.user.pk,
            }
            await comm.disconnect()

        asyncio.run(_run())

    def test_push_change_delivered(self):
        async def _run():
            comm = WebsocketCommunicator(
                application, f'/ws/operational/?token={self.token}', headers=_WS_HEADERS
            )
            connected, _ = await comm.connect()
            assert connected
            await comm.receive_json_from()

            await sync_to_async(push_operational_event)(
                resource='prepared_blank',
                action='updated',
                entity_id=7,
            )
            msg = await comm.receive_json_from()
            assert msg['event'] == 'change'
            assert msg['protocol_version'] == 1
            assert msg['resource'] == 'prepared_blank'
            assert msg['action'] == 'updated'
            assert msg['id'] == 7
            assert 'at' in msg
            await comm.disconnect()

        asyncio.run(_run())

    def test_group_name(self):
        assert OPERATIONAL_GROUP == 'operational'
        assert get_channel_layer() is not None
