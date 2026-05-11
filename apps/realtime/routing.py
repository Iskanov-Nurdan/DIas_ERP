from django.urls import re_path

from .consumers import OperationalConsumer

websocket_urlpatterns = [
    re_path(r'^ws/operational/?$', OperationalConsumer.as_asgi()),
]
