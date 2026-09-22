"""API: GET/POST /api/analytics/other-expenses/, accept/, reject/."""
from __future__ import annotations

from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.realtime.broadcast import schedule_push
from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess

from .other_expenses import (
    accept_other_expense,
    create_other_expense,
    list_other_expenses,
    reject_other_expense,
    serialize_other_expense,
)
from .services import _parse_iso_date, parse_period


def _schedule_other_expense_push(entity_id: int | None = None) -> None:
    schedule_push(resource='other_expense', action='changed', entity_id=entity_id)
    schedule_push(resource='other_expense', action='changed')


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Прочие расходы за период (pending + accepted)',
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
    create=extend_schema(
        tags=['analytics'],
        summary='Создать прочий расход (pending)',
        responses={201: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsOtherExpenseViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'
    http_method_names = ['get', 'post', 'head', 'options']

    def list(self, request):
        qp = request.query_params
        if qp.get('year') in (None, ''):
            raise DRFValidationError({'year': ['Обязательный query-параметр']})
        period = parse_period(request)
        return Response(list_other_expenses(period))

    def create(self, request):
        data = request.data or {}
        name = data.get('name')
        amount_raw = data.get('amount')
        date_raw = data.get('date')
        if amount_raw in (None, ''):
            raise DRFValidationError({'amount': ['Обязательное поле.']})
        try:
            amount = Decimal(str(amount_raw))
        except Exception:
            raise DRFValidationError({'amount': ['Некорректная сумма.']})
        expense_date = _parse_iso_date(date_raw)
        if expense_date is None:
            raise DRFValidationError({'date': ['Укажите дату в формате YYYY-MM-DD.']})
        row = create_other_expense(
            name=str(name or ''),
            amount=amount,
            expense_date=expense_date,
            user=request.user,
        )
        _schedule_other_expense_push(row.pk)
        return Response(serialize_other_expense(row), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='accept')
    def accept(self, request, pk=None):
        row, err = accept_other_expense(int(pk))
        if err == 'not_found':
            return Response({'detail': 'Запись не найдена.'}, status=status.HTTP_404_NOT_FOUND)
        if err == 'already_accepted':
            return Response(
                {'code': 'ALREADY_ACCEPTED', 'detail': 'Расход уже принят.'},
                status=status.HTTP_409_CONFLICT,
            )
        _schedule_other_expense_push(row.pk)
        return Response(serialize_other_expense(row))

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        if not reject_other_expense(int(pk)):
            return Response({'detail': 'Запись не найдена.'}, status=status.HTTP_404_NOT_FOUND)
        _schedule_other_expense_push()
        return Response(status=status.HTTP_204_NO_CONTENT)
