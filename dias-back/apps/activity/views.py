import logging
from datetime import datetime

from rest_framework import viewsets
from rest_framework.response import Response

from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess
from .models import UserActivity
from .serializers import UserActivitySerializer

logger = logging.getLogger(__name__)


class ActivityMyView(viewsets.ViewSet):
    """
    GET /api/activity/my/ — личный журнал действий текущего пользователя.
    Параметры: page, page_size, shift_id.
    shift_id — фильтрует действия за время конкретной смены (opened_at … closed_at).
    """

    def list(self, request):
        qs = UserActivity.objects.filter(user=request.user).select_related('user')

        shift_id = request.query_params.get('shift_id')
        if shift_id:
            from apps.production.models import Shift
            from django.utils import timezone
            shift = Shift.objects.filter(pk=shift_id, user=request.user).first()
            if not shift:
                return Response({
                    'error': {'code': 'not_found', 'message': 'Смена не найдена'},
                }, status=404)
            end = shift.closed_at or timezone.now()
            qs = qs.filter(created_at__gte=shift.opened_at, created_at__lte=end)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = UserActivitySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ActivityAdminView(viewsets.ViewSet):
    """
    GET /api/activity/ — журнал действий для администратора.
    Доступ: ключ «shifts».
    Параметры: user_id, date_from (YYYY-MM-DD), date_to (YYYY-MM-DD), page, page_size.
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'shifts'

    def list(self, request):
        qs = UserActivity.objects.select_related('user').all()

        user_id = request.query_params.get('user_id')
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        shift_id = request.query_params.get('shift_id')

        if user_id:
            qs = qs.filter(user_id=user_id)

        if shift_id:
            from apps.production.models import Shift
            from django.utils import timezone
            shift = Shift.objects.filter(pk=shift_id).first()
            if not shift:
                return Response({
                    'error': {'code': 'not_found', 'message': 'Смена не найдена'},
                }, status=404)
            end = shift.closed_at or timezone.now()
            qs = qs.filter(created_at__gte=shift.opened_at, created_at__lte=end)

        if date_from:
            try:
                dt_from = datetime.strptime(date_from, '%Y-%m-%d')
                qs = qs.filter(created_at__date__gte=dt_from.date())
            except ValueError:
                return Response({
                    'error': {'code': 'validation_error', 'message': 'Некорректный date_from (ожидается YYYY-MM-DD)'},
                    'errors': [{'field': 'date_from', 'message': 'Формат: YYYY-MM-DD'}],
                }, status=400)

        if date_to:
            try:
                dt_to = datetime.strptime(date_to, '%Y-%m-%d')
                qs = qs.filter(created_at__date__lte=dt_to.date())
            except ValueError:
                return Response({
                    'error': {'code': 'validation_error', 'message': 'Некорректный date_to (ожидается YYYY-MM-DD)'},
                    'errors': [{'field': 'date_to', 'message': 'Формат: YYYY-MM-DD'}],
                }, status=400)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = UserActivitySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
