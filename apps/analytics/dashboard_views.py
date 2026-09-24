"""API: GET /api/analytics/dashboard/ и /api/analytics/dashboard-details/?metric=."""
from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.response import Response

from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess

from .dashboard import build_dashboard, build_details, parse_dash_params


class AnalyticsDashboardView(viewsets.ViewSet):
    """Дашборд: KPI со сравнением, графики, долги, склад, производство.
    Финансовые поля (себестоимость, маржа, прибыль, расходы) — null без ключа analytics_finance."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    @extend_schema(
        tags=['analytics'],
        summary='Дашборд аналитики (date_from, date_to, product_line=all|profile|foam)',
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 403: DiasErrorSerializer},
    )
    def list(self, request):
        return Response(build_dashboard(parse_dash_params(request.query_params), request.user))


class AnalyticsDashboardDetailsView(viewsets.ViewSet):
    """Расшифровка KPI: формула + записи, из которых сложилась цифра."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    @extend_schema(
        tags=['analytics'],
        summary='Детализация KPI дашборда (metric=revenue|cash_in|gross_margin|expenses|net_profit)',
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 403: DiasErrorSerializer},
    )
    def list(self, request):
        qp = request.query_params
        return Response(build_details(parse_dash_params(qp), (qp.get('metric') or '').strip(), request.user))
