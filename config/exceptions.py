from rest_framework.views import exception_handler
from rest_framework.response import Response


def dias_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        details = response.data
        if not isinstance(details, dict):
            details = {'detail': details}
        payload = {
            'error': str(exc) if str(exc) else (
                'Не авторизован' if response.status_code == 401 else
                'Доступ запрещён' if response.status_code == 403 else
                'Не найдено' if response.status_code == 404 else 'Ошибка валидации'
            ),
            'code': getattr(exc, 'default_code', 'ERROR'),
            'details': details,
        }
        return Response(payload, status=response.status_code)
    return response
