"""
Middleware:
1. request_id_middleware — генерирует уникальный X-Request-Id для каждого запроса,
   сохраняет в thread-local и добавляет в заголовок ответа.
2. utf8_json_content_type — принудительно выставляет charset=utf-8 для JSON-ответов,
   чтобы фронт корректно декодировал кириллицу.
"""

import threading
import uuid

_local = threading.local()


def get_current_request_id() -> str:
    return getattr(_local, 'request_id', '-')


def request_id_middleware(get_response):
    def middleware(request):
        incoming = request.META.get('HTTP_X_REQUEST_ID') or request.META.get('HTTP_X_CORRELATION_ID')
        if incoming:
            request_id = str(incoming).strip()[:64]
        else:
            request_id = str(uuid.uuid4())
        _local.request_id = request_id
        request.request_id = request_id

        response = get_response(request)
        response['X-Request-Id'] = request_id
        return response

    return middleware


def utf8_json_content_type(get_response):
    def middleware(request):
        response = get_response(request)
        content_type = (response.get('Content-Type') or '').lower()
        if 'application/json' in content_type:
            response['Content-Type'] = 'application/json; charset=utf-8'
        return response

    return middleware
