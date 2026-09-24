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
    update_other_expense,
)
from .models import (
    KIND_EXPENSE,
    PRODUCT_LINE_GENERAL,
    AnalyticsExpenseCategory,
    AnalyticsOtherExpense,
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
    # Ручные расходы/приходы — финансовые данные.
    required_access_key = 'analytics_finance'
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def list(self, request):
        qp = request.query_params
        date_from = _parse_iso_date(qp.get('date_from'))
        date_to = _parse_iso_date(qp.get('date_to'))
        if date_from or date_to:
            return Response(list_other_expenses(None, date_from=date_from, date_to=date_to))
        if qp.get('year') in (None, ''):
            raise DRFValidationError({'year': ['Обязательный query-параметр (или date_from/date_to)']})
        period = parse_period(request)
        return Response(list_other_expenses(period))

    def partial_update(self, request, pk=None):
        row = AnalyticsOtherExpense.objects.filter(pk=pk).first()
        if row is None:
            return Response({'detail': 'Запись не найдена.'}, status=status.HTTP_404_NOT_FOUND)
        data = dict(request.data or {})
        if 'amount' in data:
            try:
                data['amount'] = Decimal(str(data['amount']))
            except Exception:
                raise DRFValidationError({'amount': ['Некорректная сумма.']})
        if 'date' in data and _parse_iso_date(data['date']) is None:
            raise DRFValidationError({'date': ['Укажите дату в формате YYYY-MM-DD.']})
        row = update_other_expense(row, data, request.user)
        _schedule_other_expense_push(row.pk)
        return Response(serialize_other_expense(row))

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
            kind=str(data.get('kind') or KIND_EXPENSE),
            category_id=data.get('category_id'),
            product_line=str(data.get('product_line') or PRODUCT_LINE_GENERAL),
            comment=str(data.get('comment') or ''),
            recurring=bool(data.get('recurring')),
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


class AnalyticsExpenseCategoryViewSet(viewsets.ViewSet):
    """GET/POST /api/analytics/expense-categories/ — настраиваемые категории."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics_finance'
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    @staticmethod
    def _ser(c):
        return {
            'id': c.pk, 'name': c.name, 'kind': c.kind, 'is_payroll': c.is_payroll,
            'is_active': c.is_active, 'sort_order': c.sort_order,
        }

    @extend_schema(tags=['analytics'], summary='Категории ручных расходов/приходов', responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        qs = AnalyticsExpenseCategory.objects.all()
        if request.query_params.get('active') in ('1', 'true'):
            qs = qs.filter(is_active=True)
        return Response({'items': [self._ser(c) for c in qs]})

    @extend_schema(tags=['analytics'], summary='Создать категорию', responses={201: OpenApiTypes.OBJECT})
    def create(self, request):
        data = request.data or {}
        name = str(data.get('name') or '').strip()
        kind = str(data.get('kind') or KIND_EXPENSE)
        if not name:
            raise DRFValidationError({'name': ['Обязательное поле.']})
        if kind not in ('expense', 'income'):
            raise DRFValidationError({'kind': ['kind: expense | income.']})
        # iexact в SQLite не сравнивает кириллицу без учёта регистра — сверяем в Python.
        existing = AnalyticsExpenseCategory.objects.filter(kind=kind).values_list('name', flat=True)
        if name.casefold() in {n.casefold() for n in existing}:
            raise DRFValidationError({'name': ['Такая категория уже есть.']})
        c = AnalyticsExpenseCategory.objects.create(
            name=name[:120], kind=kind, is_payroll=bool(data.get('is_payroll')) and kind == 'expense',
        )
        return Response(self._ser(c), status=status.HTTP_201_CREATED)

    @extend_schema(tags=['analytics'], summary='Изменить категорию', responses={200: OpenApiTypes.OBJECT})
    def partial_update(self, request, pk=None):
        c = AnalyticsExpenseCategory.objects.filter(pk=pk).first()
        if c is None:
            return Response({'detail': 'Категория не найдена.'}, status=status.HTTP_404_NOT_FOUND)
        data = request.data or {}
        if 'name' in data:
            name = str(data.get('name') or '').strip()
            if not name:
                raise DRFValidationError({'name': ['Обязательное поле.']})
            c.name = name[:120]
        if 'is_active' in data:
            c.is_active = bool(data.get('is_active'))
        if 'is_payroll' in data:
            c.is_payroll = bool(data.get('is_payroll')) and c.kind == 'expense'
        c.save()
        return Response(self._ser(c))
