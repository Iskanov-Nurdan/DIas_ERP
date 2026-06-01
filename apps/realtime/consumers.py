import asyncio
import json
import time

from channels.generic.websocket import AsyncWebsocketConsumer

from .broadcast import OPERATIONAL_GROUP, OPERATIONAL_WS_PROTOCOL_VERSION

_WS_PING_INTERVAL_SEC = 30


class OperationalConsumer(AsyncWebsocketConsumer):
    """
    Один канал для операционных разделов: connected + change (refetch на фронте).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ping_task: asyncio.Task | None = None
        self._last_activity_at = 0.0

    def _touch_activity(self) -> None:
        self._last_activity_at = time.monotonic()

    async def connect(self):
        user = self.scope.get('user')
        if not user or getattr(user, 'is_anonymous', True):
            await self.close(code=4001)
            return
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
        self._touch_activity()
        try:
            msg = json.loads(text_data)
        except json.JSONDecodeError:
            return
        ev = str(msg.get('event') or '').lower()
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

    async def operational_push(self, event):
        await self.send(text_data=json.dumps(event['payload'], ensure_ascii=False))
