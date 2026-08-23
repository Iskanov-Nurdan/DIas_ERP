"""Health-check для оркестратора (docker healthcheck / балансировщик).

Открытый эндпоинт без аутентификации: возвращает 200 только когда процесс жив
и БД отвечает, иначе 503 — контейнер будет помечен unhealthy.
"""
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def health(request):
    try:
        connections['default'].cursor().execute('SELECT 1')
    except OperationalError:
        return JsonResponse({'status': 'error', 'database': 'down'}, status=503)
    return JsonResponse({'status': 'ok', 'database': 'up'})
