import logging
import traceback

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed, NotAuthenticated, PermissionDenied,
    NotFound, MethodNotAllowed, Throttled, ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class LineShiftPausedForRecipeRun(APIException):
    """Замес (recipe-run) недопустим, пока смена на линии в паузе."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        'Смена на выбранной линии остановлена (пауза). '
        'Возобновите смену или выберите другую линию.'
    )
    default_code = 'line_shift_paused'


_STATUS_TO_CODE = {
    400: 'bad_request',
    401: 'unauthorized',
    403: 'forbidden',
    404: 'not_found',
    409: 'conflict',
    410: 'bad_request',
    429: 'too_many_requests',
    500: 'internal_error',
}

_DEFAULT_MESSAGES = {
    401: 'Не аутентифицирован',
    403: 'Доступ запрещён',
    404: 'Ресурс не найден',
    409: 'Конфликт данных',
    429: 'Превышен лимит запросов',
    500: 'Внутренняя ошибка сервера',
}


def _make_error_response(
    code: str,
    message: str,
    errors: list = None,
    http_status: int = 400,
    *,
    wait: int | float | None = None,
) -> Response:
    """Строковые error и detail + code; без вложенного объекта в error (удобно для UI)."""
    payload = {
        'code': code,
        'error': message,
        'detail': message,
    }
    if errors:
        payload['errors'] = errors
    if wait is not None:
        payload['wait'] = int(wait)
    return Response(payload, status=http_status)


def _extract_validation_errors(detail) -> list[dict]:
    """Разворачивает DRF ValidationError detail в список {field, message}."""
    errors = []
    if isinstance(detail, dict):
        raw_errors = detail.get('errors')
        if isinstance(raw_errors, list) and raw_errors:
            for item in raw_errors:
                if isinstance(item, dict) and 'field' in item and 'message' in item:
                    errors.append({'field': item['field'], 'message': str(item['message'])})
                elif isinstance(item, str):
                    errors.append({'field': 'non_field_errors', 'message': item})
            if errors:
                return errors
        for field, messages in detail.items():
            if field in ('errors', 'code', 'detail', 'error', 'missing'):
                continue
            if isinstance(messages, list):
                for msg in messages:
                    errors.append({'field': field, 'message': str(msg)})
            else:
                errors.append({'field': field, 'message': str(messages)})
    elif isinstance(detail, list):
        for item in detail:
            if isinstance(item, dict):
                errors.extend(_extract_validation_errors(item))
            else:
                errors.append({'field': 'non_field_errors', 'message': str(item)})
    else:
        errors.append({'field': 'non_field_errors', 'message': str(detail)})
    return errors


def dias_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = ValidationError(detail=exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)

    response = exception_handler(exc, context)

    if response is None:
        logger.exception('Необработанное исключение: %s', exc, exc_info=True)
        return _make_error_response('internal_error', 'Внутренняя ошибка сервера', http_status=500)

    http_status = response.status_code
    detail = response.data

    if http_status >= 500:
        logger.exception(
            'Серверная ошибка %s: %s\n%s',
            http_status, exc,
            ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        message = str(exc.detail) if hasattr(exc, 'detail') else _DEFAULT_MESSAGES[401]
        return _make_error_response('unauthorized', message, http_status=401)

    if isinstance(exc, PermissionDenied):
        return _make_error_response('forbidden', 'Доступ запрещён', http_status=403)

    if isinstance(exc, NotFound):
        return _make_error_response('not_found', 'Ресурс не найден', http_status=404)

    if isinstance(exc, Throttled):
        wait = exc.wait
        msg = f'Превышен лимит запросов. Повторите через {int(wait)} с.' if wait else 'Превышен лимит запросов.'
        return _make_error_response('too_many_requests', msg, http_status=429, wait=wait)

    if isinstance(exc, ValidationError):
        errors = _extract_validation_errors(exc.detail)
        if isinstance(exc.detail, dict) and exc.detail.get('detail'):
            first_msg = str(exc.detail['detail'])
        elif isinstance(exc.detail, dict) and exc.detail.get('error'):
            first_msg = str(exc.detail['error'])
        else:
            first_msg = errors[0]['message'] if errors else 'Ошибка валидации'
        code = 'validation_error'
        if isinstance(exc.detail, dict) and exc.detail.get('code'):
            code = str(exc.detail['code'])
        return _make_error_response(code, first_msg, errors=errors or None, http_status=400)

    if isinstance(exc, MethodNotAllowed):
        return _make_error_response('bad_request', f'Метод {exc.args[0]} не поддерживается', http_status=405)

    code = _STATUS_TO_CODE.get(http_status, 'bad_request')
    if isinstance(detail, dict):
        err_val = detail.get('error')
        if isinstance(err_val, dict):
            message = str(err_val.get('message', '') or err_val.get('detail', ''))
            if not message:
                message = _DEFAULT_MESSAGES.get(http_status, 'Ошибка')
        else:
            message = str(detail.get('detail', '') or err_val or _DEFAULT_MESSAGES.get(http_status, 'Ошибка'))
    elif isinstance(detail, list) and detail:
        message = str(detail[0])
    else:
        message = str(detail) if detail else _DEFAULT_MESSAGES.get(http_status, 'Ошибка')

    if 400 <= http_status < 500:
        logger.warning('HTTP %s %s: %s', http_status, code, message)

    return _make_error_response(code, message, http_status=http_status)
