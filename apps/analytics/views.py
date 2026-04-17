from decimal import Decimal

from config.api_numbers import api_decimal_str
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess
from apps.materials.models import MaterialStockDeduction

from .services import parse_analytics_scope, parse_period
from .reporting import (
    build_analytics_summary,
    build_otk_details,
    build_production_cost_details,
    build_profit_details,
    build_purchase_details,
    build_revenue_details_items,
    build_sales_cost_details,
)

_ANALYTICS_SCOPE_PARAMS = [
    OpenApiParameter('year', int, required=False, description='Год (если нет date_from/date_to — по умолчанию текущий).'),
    OpenApiParameter('month', int, required=False, description='Месяц 1–12 или пусто = весь год.'),
    OpenApiParameter('day', int, required=False, description='День или пусто = весь месяц/год.'),
    OpenApiParameter('date_from', str, required=False, description='Начало периода YYYY-MM-DD.'),
    OpenApiParameter('date_to', str, required=False, description='Конец периода YYYY-MM-DD.'),
    OpenApiParameter('line_id', int, required=False, description='Линия производства (партия / продажа через склад).'),
    OpenApiParameter('client_id', int, required=False),
    OpenApiParameter('profile_id', int, required=False),
    OpenApiParameter('recipe_id', int, required=False),
    OpenApiParameter('batch_id', int, required=False, description='Партия производства (production batch id).'),
    OpenApiParameter(
        'otk_status',
        str,
        required=False,
        description='Статус ОТК партии производства: pending | accepted | rejected.',
    ),
    OpenApiParameter(
        'trend_group',
        str,
        required=False,
        description='Группировка трендов: day | month (по умолчанию — авто от длины периода).',
    ),
]

@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Сводная аналитика (KPI, ОТК, склад, тренды, разрезы)',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
        description=(
            'Ответ: `period`, `trend_group`, `cards` (в т.ч. `purchase_total` — сумма закупок сырья за период), '
            '`otk_summary`, `warehouse_summary`, `production_summary`, `trends` '
            '(revenue, sales_cost, profit, production_cost, purchase_total), '
            '`sales_by_profile`, `sales_by_client`, `production_by_line`.'
        ),
    ),
)
class AnalyticsSummaryView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        tg = request.query_params.get('trend_group')
        return Response(build_analytics_summary(scope, trend_group=tg))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация выручки (продажи)',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsRevenueDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_revenue_details_items(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация себестоимости продаж (Sale.cost)',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsSalesCostDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_sales_cost_details(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация себестоимости производства (ProductionBatch.material_cost_total)',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsProductionCostDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_production_cost_details(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация закупок сырья (партии прихода)',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsPurchaseDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_purchase_details(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация прибыли по продажам',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsProfitDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_profit_details(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация ОТК по партиям периода',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsOtkDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        return Response(build_otk_details(scope))


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация списаний сырья (FIFO по строкам, внутренний учёт)',
        parameters=[
            OpenApiParameter('year', int, required=True, description='Год.'),
            OpenApiParameter('month', int, required=False, description='Месяц 1–12 или пусто = весь год.'),
            OpenApiParameter('day', int, required=False, description='День или пусто = весь месяц/год.'),
        ],
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsWriteoffDetailsView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        if request.query_params.get('year') in (None, ''):
            raise DRFValidationError({'year': ['Обязательный query-параметр']})
        p = parse_period(request)
        qs = (
            MaterialStockDeduction.objects.filter(p.writeoff_q())
            .select_related('batch__material')
            .order_by('-created_at', '-id')
        )
        items = []
        total_est = Decimal('0')
        for w in qs:
            line_est = w.line_total or Decimal('0')
            total_est += line_est
            created = w.created_at
            date_str = created.date().isoformat() if created else None
            ev = api_decimal_str(line_est)
            material_name = w.batch.material.name
            items.append({
                'id': w.id,
                'batch_id': w.batch_id,
                'date': date_str,
                'created_at': created.isoformat() if created else None,
                'material_name': material_name,
                'quantity': api_decimal_str(w.quantity),
                'unit': w.batch.material.unit,
                'reason': w.reason or '',
                'reference_id': w.reference_id,
                'fifo_line_total': ev,
            })
        return Response({
            'total': api_decimal_str(total_est),
            'items': items,
        })
