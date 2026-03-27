"""
ASGI: HTTP (Django) + WebSocket (Channels) для операционных push-событий.
Запуск: python manage.py runserver (Channels подменяет dev-сервер) или daphne config.asgi:application

WebSocket: проверка Origin через OriginValidator со списком как у CORS (страница фронта), а не AllowedHostsOriginValidator
(тот сравнивал Origin с ALLOWED_HOSTS — хост API — и ломал handshake при фронте на другом origin).
"""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_asgi_app = get_asgi_application()

from apps.realtime.middleware import JwtWsAuthMiddleware  # noqa: E402
from apps.realtime.routing import websocket_urlpatterns  # noqa: E402


def _websocket_allowed_origins():
    if getattr(settings, 'CHANNELS_WS_ALLOW_ALL_ORIGINS', False):
        return ['*']
    if getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False):
        return ['*']
    explicit = getattr(settings, 'CHANNELS_WS_ALLOWED_ORIGINS', None) or []
    if explicit:
        return list(explicit)
    cors = list(getattr(settings, 'CORS_ALLOWED_ORIGINS', []))
    if cors:
        return cors
    return ['http://localhost:3000', 'http://127.0.0.1:3000']


application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': OriginValidator(
        JwtWsAuthMiddleware(
            URLRouter(websocket_urlpatterns),
        ),
        allowed_origins=_websocket_allowed_origins(),
    ),
})
