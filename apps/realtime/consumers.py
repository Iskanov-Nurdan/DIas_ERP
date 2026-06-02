import asyncio
import json
import time

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .access import user_may_receive_resource
from .broadcast import OPERATIONAL_GROUP, OPERATIONAL_WS_PROTOCOL_VERSION

_WS_PING_INTERVAL_SEC = 30
_WS_IDLE_TIMEOUT_SEC = 60


class OperationalConsumer(AsyncWebsocketConsumer):
    """
    Один канал для операционных разделов: connected + change (refetch на фронте).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ping_task: asyncio.Task | None = None
        self._last_activity_at = 0.0
        self._user_access_keys: set[str] | None = None
        self._user_is_superuser = False

    @staticmethod
    def _load_access_context(user):
        return getattr(user, 'is_superuser', False), set(user.get_access_keys())

    def _touch_activity(self) -> None:
        self._last_activity_at = time.monotonic()

    async def connect(self):
        user = self.scope.get('user')
        if not user or getattr(user, 'is_anonymous', True):
            await self.close(code=4001)
            return
        self._user_is_superuser, self._user_access_keys = await sync_to_async(
            self._load_access_context
        )(user)
        await self.channel_layer.group_add(OPERATIONAL_GROUP, self.channel_name)
        await self.accept()
        self._touch_activity()
        await self.send(
            text_data=json.dumps(
                {
                    'event': 'connected',
                    'protocol_version': OPERATIONAL_WS_PROTOCOL_VERSION,
                    'user_id': user.pk,
                },
                ensure_ascii=False,
            )
        )
        self._ping_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self, code):
        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None
        await self.channel_layer.group_discard(OPERATIONAL_GROUP, self.channel_name)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_WS_PING_INTERVAL_SEC)
                idle = time.monotonic() - self._last_activity_at
                if idle >= _WS_IDLE_TIMEOUT_SEC:
                    await self.close(code=4000)
                    return
                await self.send(
                    text_data=json.dumps(
                        {
                            'event': 'ping',
                            'protocol_version': OPERATIONAL_WS_PROTOCOL_VERSION,
                        },
                        ensure_ascii=False,
                    )
                )
        except asyncio.CancelledError:
            return

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            return
        ev = str(msg.get('event') or '').lower()
        if ev in ('ping', 'pong'):
            self._touch_activity()
        if ev == 'ping':
            await self.send(
                text_data=json.dumps(
                    {
                        'event': 'pong',
                        'protocol_version': OPERATIONAL_WS_PROTOCOL_VERSION,
                    },
                    ensure_ascii=False,
                )
            )
        elif ev == 'pong':
            pass

    async def operational_push(self, event):
        payload = event.get('payload') or {}
        resource = payload.get('resource')
        user = self.scope.get('user')
        if resource and not user_may_receive_resource(
            user,
            str(resource),
            user_keys=self._user_access_keys,
            is_superuser=self._user_is_superuser,
        ):
            return
        await self.send(text_data=json.dumps(payload, ensure_ascii=False))
