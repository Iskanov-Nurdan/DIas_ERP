"""
JWT для WebSocket: токен в query (?token= или ?access=).

Парсинг query вручную + urllib.parse.unquote (не parse_qs): в application/x-www-form-urlencoded
символ «+» в значении декодируется как пробел и может испортить JWT, если клиент не использовал %2B.
"""
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication


class _BearerRequest:
    __slots__ = ('META',)

    def __init__(self, raw_token: str):
        self.META = {'HTTP_AUTHORIZATION': f'Bearer {raw_token}'}


def _token_from_query_string(raw_qs: bytes) -> str | None:
    if not raw_qs:
        return None
    text = raw_qs.decode('utf-8', errors='replace')
    for segment in text.split('&'):
        if not segment or '=' not in segment:
            continue
        key, _, val = segment.partition('=')
        key_decoded = unquote(key.replace('+', ' '))
        if key_decoded not in ('token', 'access'):
            continue
        # unquote, не unquote_plus: «+» в токене остаётся «+»
        return unquote(val)
    return None


def _user_from_scope_query(scope: dict):
    raw_qs = scope.get('query_string') or b''
    token = _token_from_query_string(raw_qs)
    if not token:
        return AnonymousUser()
    try:
        auth = JWTAuthentication()
        pair = auth.authenticate(_BearerRequest(token))
        if pair:
            return pair[0]
    except Exception:
        pass
    return AnonymousUser()


class JwtWsAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            scope['user'] = await sync_to_async(_user_from_scope_query)(scope)
        return await super().__call__(scope, receive, send)
