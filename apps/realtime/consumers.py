import json

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .broadcast import OPERATIONAL_GROUP


class OperationalConsumer(AsyncWebsocketConsumer):
    """
    Один канал для операционных разделов: только события «что-то изменилось».
    После reconnect фронт делает точечный REST refetch.
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or getattr(user, 'is_anonymous', True):
            await self.close(code=4001)
            return
        await self.channel_layer.group_add(OPERATIONAL_GROUP, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {
                    'protocol_version': 1,
                    'event': 'connected',
                    'resource': 'socket',
                    'action': 'open',
                    'payload': {
                        'group': OPERATIONAL_GROUP,
                        'hint': 'После переподключения обновите списки через REST (refetch).',
                        'debug': bool(getattr(settings, 'DEBUG', False)),
                    },
                },
                ensure_ascii=False,
            )
        )

    async def disconnect(self, code):
        await self.channel_layer.group_discard(OPERATIONAL_GROUP, self.channel_name)

    async def operational_push(self, event):
        await self.send(text_data=json.dumps(event['payload'], ensure_ascii=False))
