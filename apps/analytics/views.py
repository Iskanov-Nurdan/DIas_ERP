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

from .services import parse_analytics_scope, parse_period, _parse_iso_date, _parse_int_param, _qp_first
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


# ─────────────────────────────────────────────────────────────────────────────
# АНАЛИТИКА БРАКА
# ─────────────────────────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Аналитика брака: потери, статусы, источники',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsDefectView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        from apps.sales.models import DefectRecord
        from django.db.models import Count, Sum
        from django.db.models.functions import Coalesce

        scope = parse_analytics_scope(request)

        qs = DefectRecord.objects.all()
        # Фильтр по дате
        date_from = scope.date_from or (scope.period._date_field_q('created_at') and None)
        if scope.date_from:
            qs = qs.filter(created_at__date__gte=scope.date_from)
        if scope.date_to:
            qs = qs.filter(created_at__date__lte=scope.date_to)
        if not scope.date_from and not scope.date_to:
            qs = qs.filter(scope.period.writeoff_q())

        if scope.profile_id:
            qs = qs.filter(profile_id=scope.profile_id)

        total_pcs = qs.aggregate(t=Coalesce(Sum('quantity_pcs'), Decimal('0')))['t'] or Decimal('0')
        total_kg = qs.aggregate(t=Coalesce(Sum('quantity_kg'), Decimal('0')))['t'] or Decimal('0')

        by_status = list(
            qs.values('status').annotate(
                count=Count('id'),
                pcs=Coalesce(Sum('quantity_pcs'), Decimal('0')),
                kg=Coalesce(Sum('quantity_kg'), Decimal('0')),
            ).order_by('status')
        )
        by_source = list(
            qs.values('source_type').annotate(
                count=Count('id'),
                pcs=Coalesce(Sum('quantity_pcs'), Decimal('0')),
            ).order_by('source_type')
        )

        # Продажи брака
        from apps.sales.models import Sale
        defect_sales = Sale.objects.filter(is_defect_sale=True)
        if scope.date_from:
            defect_sales = defect_sales.filter(date__gte=scope.date_from)
        if scope.date_to:
            defect_sales = defect_sales.filter(date__lte=scope.date_to)
        if not scope.date_from and not scope.date_to:
            defect_sales = defect_sales.filter(scope.period.sale_q())
        defect_revenue = defect_sales.aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t'] or Decimal('0')
        defect_cost = defect_sales.aggregate(t=Coalesce(Sum('cost'), Decimal('0')))['t'] or Decimal('0')

        return Response({
            'period': scope.as_period_dict(),
            'total_defect_pcs': api_decimal_str(total_pcs),
            'total_defect_kg': api_decimal_str(total_kg),
            'defect_sale_revenue': api_decimal_str(defect_revenue),
            'defect_sale_cost': api_decimal_str(defect_cost),
            'defect_sale_profit': api_decimal_str(defect_revenue - defect_cost),
            'by_status': [
                {
                    'status': r['status'],
                    'count': r['count'],
                    'pcs': api_decimal_str(r['pcs']),
                    'kg': api_decimal_str(r['kg']),
                }
                for r in by_status
            ],
            'by_source': [
                {
                    'source_type': r['source_type'],
                    'count': r['count'],
                    'pcs': api_decimal_str(r['pcs']),
                }
                for r in by_source
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# АНАЛИТИКА ПЕРЕДЕЛКИ
# ─────────────────────────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Аналитика переделки: вход, выход, потери',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsReworkView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        from apps.sales.models import ReworkRequest
        from django.db.models import Count, Sum
        from django.db.models.functions import Coalesce

        scope = parse_analytics_scope(request)
        qs = ReworkRequest.objects.all()
        if scope.date_from:
            qs = qs.filter(created_at__date__gte=scope.date_from)
        if scope.date_to:
            qs = qs.filter(created_at__date__lte=scope.date_to)
        if not scope.date_from and not scope.date_to:
            qs = qs.filter(scope.period.writeoff_q())

        agg = qs.aggregate(
            total_input=Coalesce(Sum('quantity_kg'), Decimal('0')),
            total_output=Coalesce(Sum('output_quantity_kg'), Decimal('0')),
            total_loss=Coalesce(Sum('loss_kg'), Decimal('0')),
            count=Count('id'),
        )

        by_status = list(
            qs.values('status').annotate(
                count=Count('id'),
                input_kg=Coalesce(Sum('quantity_kg'), Decimal('0')),
                output_kg=Coalesce(Sum('output_quantity_kg'), Decimal('0')),
                loss_kg=Coalesce(Sum('loss_kg'), Decimal('0')),
            ).order_by('status')
        )

        return Response({
            'period': scope.as_period_dict(),
            'total_reworks': agg['count'],
            'total_input_kg': api_decimal_str(agg['total_input']),
            'total_output_kg': api_decimal_str(agg['total_output']),
            'total_loss_kg': api_decimal_str(agg['total_loss']),
            'by_status': [
                {
                    'status': r['status'],
                    'count': r['count'],
                    'input_kg': api_decimal_str(r['input_kg']),
                    'output_kg': api_decimal_str(r['output_kg']),
                    'loss_kg': api_decimal_str(r['loss_kg']),
                }
                for r in by_status
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# ПРИБЫЛЬНОСТЬ ПО КЛИЕНТАМ
# ─────────────────────────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Прибыльность по клиентам за период',
        parameters=_ANALYTICS_SCOPE_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsClientProfitabilityView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        from apps.sales.models import Sale, Client
        from django.db.models import Sum, Count
        from django.db.models.functions import Coalesce

        scope = parse_analytics_scope(request)
        qs = Sale.objects.exclude(sale_status=Sale.STATUS_CANCELED)
        qs = qs.filter(scope.sale_date_q())
        if scope.client_id:
            qs = qs.filter(client_id=scope.client_id)

        rows = (
            qs.values('client_id', 'client__name')
            .annotate(
                revenue=Coalesce(Sum('revenue'), Decimal('0')),
                cost=Coalesce(Sum('cost'), Decimal('0')),
                profit=Coalesce(Sum('profit'), Decimal('0')),
                sales_count=Count('id'),
            )
            .order_by('-revenue')
        )

        items = []
        for r in rows:
            items.append({
                'client_id': r['client_id'],
                'client_name': r['client__name'] or '—',
                'revenue': api_decimal_str(r['revenue']),
                'cost': api_decimal_str(r['cost']),
                'profit': api_decimal_str(r['profit']),
                'sales_count': r['sales_count'],
            })

        return Response({
            'period': scope.as_period_dict(),
            'items': items,
            'total_revenue': api_decimal_str(sum(Decimal(i['revenue']) for i in items)),
            'total_profit': api_decimal_str(sum(Decimal(i['profit']) for i in items)),
        })


# ─────────────────────────────────────────────────────────────────────────────
# ДЕБИТОРКА И АВАНСЫ
# ─────────────────────────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Дебиторская задолженность и авансы по клиентам',
        responses={200: OpenApiTypes.OBJECT, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsReceivablesView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        from apps.sales.models import Client, Payment, Sale
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        clients = Client.objects.filter(is_active=True)
        items = []
        total_debt = Decimal('0')
        total_advance = Decimal('0')

        for c in clients:
            revenue = (
                Sale.objects.filter(client=c)
                .exclude(sale_status__in=[Sale.STATUS_CANCELED, Sale.STATUS_DRAFT])
                .aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t']
            ) or Decimal('0')

            incoming = (
                Payment.objects.filter(
                    client=c,
                    payment_type__in=[Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE],
                ).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
            ) or Decimal('0')
            refunded = (
                Payment.objects.filter(client=c, payment_type=Payment.TYPE_REFUND)
                .aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
            ) or Decimal('0')
            net_paid = incoming - refunded

            debt = max(Decimal('0'), revenue - net_paid)
            advance = max(Decimal('0'), net_paid - revenue)

            if debt > 0 or advance > 0:
                total_debt += debt
                total_advance += advance
                items.append({
                    'client_id': c.pk,
                    'client_name': c.name,
                    'total_revenue': api_decimal_str(revenue),
                    'total_paid': api_decimal_str(net_paid),
                    'debt': api_decimal_str(debt),
                    'advance': api_decimal_str(advance),
                    'credit_limit': api_decimal_str(c.credit_limit) if c.credit_limit else None,
                    'credit_limit_mode': c.credit_limit_mode,
                    'is_over_limit': (
                        c.credit_limit is not None and debt > c.credit_limit
                    ),
                })

        items.sort(key=lambda x: Decimal(x['debt']), reverse=True)

        return Response({
            'total_debt': api_decimal_str(total_debt),
            'total_advance': api_decimal_str(total_advance),
            'items': items,
        })
