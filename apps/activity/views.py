import logging
from datetime import datetime

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.openapi_common import DiasErrorSerializer, paginated_inline
from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess

from .models import UserActivity
from .serializers import UserActivitySerializer

logger = logging.getLogger(__name__)


def _activity_list_queryset_base():
    return UserActivity.objects.select_related('user', 'shift', 'line')


def _apply_activity_filters(qs, query_params):
    entity_type = query_params.get('entity_type')
    entity_id = query_params.get('entity_id')
    action = query_params.get('action')
    request_id = query_params.get('request_id')
    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if entity_id is not None and str(entity_id).strip() != '':
        qs = qs.filter(entity_id=str(entity_id))
    if action:
        qs = qs.filter(action=action)
    if request_id:
        qs = qs.filter(request_id=request_id)
    return qs


_ACTIVITY_LIST_PARAMS = [
    OpenApiParameter('page', int, required=False),
    OpenApiParameter('page_size', int, required=False),
    OpenApiParameter('shift_id', int, required=False),
    OpenApiParameter('entity_type', str, required=False),
    OpenApiParameter('entity_id', str, required=False),
    OpenApiParameter('action', str, required=False, description='create | update | delete | restore'),
    OpenApiParameter('request_id', str, required=False),
    OpenApiParameter('date_from', str, required=False, description='YYYY-MM-DD'),
    OpenApiParameter('date_to', str, required=False, description='YYYY-MM-DD'),
]


@extend_schema_view(
    list=extend_schema(
        tags=['activity'],
        summary='Журнал действий (текущий пользователь)',
        parameters=_ACTIVITY_LIST_PARAMS,
        responses={
            200: paginated_inline('ActivityMyList', UserActivitySerializer),
            400: DiasErrorSerializer,
            404: DiasErrorSerializer,
            401: DiasErrorSerializer,
        },
    ),
)
class ActivityMyView(viewsets.ViewSet):
    """
    GET /api/activity/my/ — личный журнал действий текущего пользователя.
    Параметры: page, page_size, shift_id,
    entity_type, entity_id, action, request_id,
    date_from, date_to (YYYY-MM-DD).
    shift_id — действия этой смены: по полю shift_id ИЛИ по времени opened_at … closed_at
    (чтобы попадали записи без shift_id, пока смена была открыта).
    При переданном shift_id фильтры date_from / date_to не применяются — окно смены уже задаёт интервал;
    иначе комбинация shift_id + даты часто давала пустой список (часовой пояс / другой календарный день).
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        qs = _activity_list_queryset_base().filter(user=request.user)

        shift_id = request.query_params.get('shift_id')
        shift_applied = False
        if shift_id:
            from apps.production.models import Shift
            from django.utils import timezone

            shift = Shift.objects.filter(pk=shift_id, user=request.user).first()
            if not shift:
                msg = 'Смена не найдена'
                return Response(
                    {'code': 'not_found', 'error': msg, 'detail': msg},
                    status=404,
                )
            end = shift.closed_at or timezone.now()
            # Явная привязка к смене ИЛИ попадание во временной интервал смены (старые строки без shift_id)
            qs = qs.filter(
                Q(shift_id=shift.pk)
                | Q(created_at__gte=shift.opened_at, created_at__lte=end)
            )
            shift_applied = True

        qs = _apply_activity_filters(qs, request.query_params)

        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        if shift_applied:
            date_from = None
            date_to = None
        if date_from:
            try:
                dt_from = datetime.strptime(date_from, '%Y-%m-%d')
                qs = qs.filter(created_at__date__gte=dt_from.date())
            except ValueError:
                msg = 'Некорректный date_from (ожидается YYYY-MM-DD)'
                return Response(
                    {
                        'code': 'validation_error',
                        'error': msg,
                        'detail': msg,
                        'errors': [{'field': 'date_from', 'message': 'Формат: YYYY-MM-DD'}],
                    },
                    status=400,
                )

        if date_to:
            try:
                dt_to = datetime.strptime(date_to, '%Y-%m-%d')
                qs = qs.filter(created_at__date__lte=dt_to.date())
            except ValueError:
                msg = 'Некорректный date_to (ожидается YYYY-MM-DD)'
                return Response(
                    {
                        'code': 'validation_error',
                        'error': msg,
                        'detail': msg,
                        'errors': [{'field': 'date_to', 'message': 'Формат: YYYY-MM-DD'}],
                    },
                    status=400,
                )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = UserActivitySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ActivityMyRetrieveView(generics.RetrieveAPIView):
    """GET /api/activity/my/<id>/ — детальная карточка события (только свои записи)."""

    serializer_class = UserActivitySerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return get_object_or_404(
            _activity_list_queryset_base().filter(user=self.request.user),
            pk=self.kwargs['pk'],
        )


@extend_schema_view(
    list=extend_schema(
        tags=['activity'],
        summary='Журнал действий (админ / ключ shifts)',
        parameters=[
            *_ACTIVITY_LIST_PARAMS,
            OpenApiParameter('user_id', int, required=False),
        ],
        responses={
            200: paginated_inline('ActivityAdminList', UserActivitySerializer),
            400: DiasErrorSerializer,
            403: DiasErrorSerializer,
            404: DiasErrorSerializer,
            401: DiasErrorSerializer,
        },
    ),
)
class ActivityAdminView(viewsets.ViewSet):
    """
    GET /api/activity/ — журнал действий для администратора.
    Доступ: ключ «shifts».
    Параметры: user_id, date_from, date_to, shift_id,
    entity_type, entity_id, action, request_id, page, page_size.
    """

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'shifts'

    def list(self, request):
        qs = _activity_list_queryset_base().all()

        user_id = request.query_params.get('user_id')
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        shift_id = request.query_params.get('shift_id')

        if user_id:
            qs = qs.filter(user_id=user_id)

        shift_applied = False
        if shift_id:
            from apps.production.models import Shift
            from django.utils import timezone

            shift = Shift.objects.filter(pk=shift_id).first()
            if not shift:
                msg = 'Смена не найдена'
                return Response(
                    {'code': 'not_found', 'error': msg, 'detail': msg},
                    status=404,
                )
            end = shift.closed_at or timezone.now()
            qs = qs.filter(
                Q(shift_id=shift.pk)
                | Q(created_at__gte=shift.opened_at, created_at__lte=end)
            )
            shift_applied = True

        qs = _apply_activity_filters(qs, request.query_params)

        if shift_applied:
            date_from = None
            date_to = None
        if date_from:
            try:
                dt_from = datetime.strptime(date_from, '%Y-%m-%d')
                qs = qs.filter(created_at__date__gte=dt_from.date())
            except ValueError:
                msg = 'Некорректный date_from (ожидается YYYY-MM-DD)'
                return Response(
                    {
                        'code': 'validation_error',
                        'error': msg,
                        'detail': msg,
                        'errors': [{'field': 'date_from', 'message': 'Формат: YYYY-MM-DD'}],
                    },
                    status=400,
                )

        if date_to:
            try:
                dt_to = datetime.strptime(date_to, '%Y-%m-%d')
                qs = qs.filter(created_at__date__lte=dt_to.date())
            except ValueError:
                msg = 'Некорректный date_to (ожидается YYYY-MM-DD)'
                return Response(
                    {
                        'code': 'validation_error',
                        'error': msg,
                        'detail': msg,
                        'errors': [{'field': 'date_to', 'message': 'Формат: YYYY-MM-DD'}],
                    },
                    status=400,
                )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = UserActivitySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ActivityAdminRetrieveView(generics.RetrieveAPIView):
    """GET /api/activity/<id>/ — детальная карточка (доступ shifts / superuser)."""

    serializer_class = UserActivitySerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'shifts'

    def get_object(self):
        return get_object_or_404(_activity_list_queryset_base(), pk=self.kwargs['pk'])
