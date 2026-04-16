from decimal import Decimal

from config.api_numbers import api_decimal_str
from datetime import datetime, timedelta
from calendar import monthrange

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess
from apps.sales.models import Sale, Shipment
from apps.warehouse.models import WarehouseBatch
from apps.materials.models import MaterialBatch, RawMaterial, MaterialStockDeduction
from apps.production.models import ProductionBatch, RecipeRun, Shift, ShiftComplaint
from apps.chemistry.models import ChemistryBatch, ChemistryCatalog, ChemistryTask
from apps.activity.models import UserActivity

from apps.otk.models import OtkCheck

from .services import (
    Period,
    parse_analytics_scope,
    parse_period,
    production_batch_scope_q,
    recipe_run_scope_q,
    sale_scope_q,
)

_ANALYTICS_PERIOD_PARAMS = [
    OpenApiParameter('year', int, required=False, description='Год периода (по умолчанию текущий).'),
    OpenApiParameter('month', int, required=False, description='Месяц 1–12 или пусто = весь год.'),
    OpenApiParameter('day', int, required=False, description='День или пусто = весь месяц/год.'),
]

_ANALYTICS_DETAILS_PERIOD_PARAMS = [
    OpenApiParameter('year', int, required=True, description='Год периода (обязателен для детализаций).'),
    OpenApiParameter('month', int, required=False, description='Месяц 1–12 или пусто = весь год.'),
    OpenApiParameter('day', int, required=False, description='День или пусто = весь месяц/год.'),
]


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Сводная аналитика за период',
        parameters=_ANALYTICS_PERIOD_PARAMS,
        responses={
            200: OpenApiTypes.OBJECT,
            401: DiasErrorSerializer,
            403: DiasErrorSerializer,
        },
        description=(
            'Сводка за период: финансы, движение сырья, производство, склад, тренды по дням. '
            'Числовые суммы и количества — строки с фиксированным десятичным представлением (без float). '
            '`material_flow.writeoffs.fifo_cost_total` — сумма `line_total` (FIFO) по списаниям.'
        ),
    ),
)
class AnalyticsSummaryView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        scope = parse_analytics_scope(request)
        p: Period = scope.period

        sq = sale_scope_q(scope)
        date_filter_batches = production_batch_scope_q(scope)
        date_filter_incoming = scope.incoming_date_q()
        date_filter_shipments = scope.shipment_by_sale_q()
        date_filter_writeoffs = scope.writeoff_q()
        date_filter_wh_batches = scope.warehouse_batch_q()
        if scope.profile_id:
            date_filter_wh_batches &= Q(profile_id=scope.profile_id)
        if scope.batch_id:
            date_filter_wh_batches &= Q(source_batch_id=scope.batch_id)
        date_filter_shifts = scope.shift_opened_q()
        if scope.line_id:
            date_filter_shifts &= Q(line_id=scope.line_id)
        date_filter_recipe_runs = recipe_run_scope_q(scope)
        date_filter_activity = scope.activity_q()
        date_filter_complaints = scope.complaint_q()

        # --- Финансы (продажи vs закупки сырья) ---
        sales_data = Sale.objects.filter(sq).aggregate(
            revenue=Sum('revenue'),
            count=Count('id'),
            profit_sum=Sum('profit'),
        )
        total_revenue = sales_data['revenue'] or Decimal('0')
        profit_recorded = sales_data['profit_sum'] or Decimal('0')

        expenses_data = MaterialBatch.objects.filter(date_filter_incoming).aggregate(
            expenses=Sum('total_price'),
            incoming_lines=Count('id'),
            incoming_qty=Sum('quantity_initial'),
        )
        total_expenses = expenses_data['expenses'] or Decimal('0')
        profit_simple = total_revenue - total_expenses

        cost_price_data = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            cost=Sum('material_cost_total'),
        )
        total_cost_price = cost_price_data['cost'] or Decimal('0')

        # --- Списания сырья за период ---
        wo_qs = MaterialStockDeduction.objects.filter(date_filter_writeoffs).select_related('batch__material')
        wo_agg = wo_qs.aggregate(
            n=Count('id'),
            qty=Sum('quantity'),
        )
        wo_by_reason = list(
            wo_qs.values('reason')
            .annotate(count=Count('id'), quantity=Sum('quantity'))
            .order_by('-count')[:20]
        )
        wo_top_materials = list(
            wo_qs.values('batch__material__name', 'batch__material__unit')
            .annotate(count=Count('id'), quantity=Sum('quantity'))
            .order_by('-quantity')[:15]
        )
        wo_value_est = wo_qs.aggregate(s=Sum('line_total'))['s'] or Decimal('0')
        wo_valued_lines = wo_qs.count()
        writeoff_finance_total = wo_value_est

        # --- ОТК / производственные партии ---
        otk_by_status = list(
            ProductionBatch.objects.filter(date_filter_batches)
            .values('otk_status')
            .annotate(n=Count('id'), total_meters=Sum('total_meters'))
            .order_by('-n')
        )

        # --- Склад ГП: появилось за период ---
        wh_new = WarehouseBatch.objects.filter(date_filter_wh_batches)
        wh_new_stats = wh_new.aggregate(
            n=Count('id'),
            qty=Sum('quantity'),
        )
        wh_by_status = list(
            wh_new.values('status')
            .annotate(n=Count('id'), quantity=Sum('quantity'))
            .order_by('-n')
        )

        # --- Замесы, смены, жалобы ---
        recipe_runs_n = RecipeRun.objects.filter(date_filter_recipe_runs).count()
        shifts_opened = Shift.objects.filter(date_filter_shifts).count()
        shifts_closed = Shift.objects.filter(date_filter_shifts).exclude(closed_at__isnull=True).count()
        complaints_n = ShiftComplaint.objects.filter(date_filter_complaints).count()

        # --- Химия: снимок остатка (все партии) и выпуск за период ---
        chemistry_remaining_kg_snapshot = ChemistryBatch.objects.aggregate(s=Sum('quantity_remaining'))['s'] or Decimal('0')

        date_filter_chem_batches = scope.writeoff_q()
        chem_done_qty = (
            ChemistryBatch.objects.filter(date_filter_chem_batches, source_task_id__isnull=False).aggregate(
                s=Sum('quantity_produced')
            )['s']
        )

        # --- Журнал: активность по разделам ---
        audit_by_section = list(
            UserActivity.objects.filter(date_filter_activity)
            .values('section')
            .annotate(n=Count('id'))
            .order_by('-n')[:25]
        )

        # --- Продажи: топы ---
        top_products_list = []
        top_products_keys = (
            Sale.objects.filter(sq)
            .values('product')
            .annotate(qty=Sum('quantity'))
            .order_by('-qty')[:5]
        )
        for row in top_products_keys:
            prod = row['product']
            agg = Sale.objects.filter(sq, product=prod).aggregate(
                qty=Sum('quantity'),
                rev=Sum('revenue'),
            )
            top_products_list.append({
                'product_name': prod,
                'quantity': api_decimal_str(agg['qty'] or Decimal('0')),
                'revenue': api_decimal_str(agg['rev'] or Decimal('0')),
            })

        top_clients_list = []
        top_clients_keys = (
            Sale.objects.filter(sq)
            .values('client__name')
            .annotate(qty=Sum('quantity'))
            .order_by('-qty')[:5]
        )
        for row in top_clients_keys:
            cname = row['client__name']
            agg = Sale.objects.filter(sq, client__name=cname).aggregate(
                qty=Sum('quantity'),
                rev=Sum('revenue'),
            )
            top_clients_list.append({
                'client_name': cname or '—',
                'quantity': api_decimal_str(agg['qty'] or Decimal('0')),
                'revenue': api_decimal_str(agg['rev'] or Decimal('0')),
            })

        sales_total_qty = Sale.objects.filter(sq).aggregate(s=Sum('quantity'))['s'] or Decimal('0')

        # --- Поставщики ---
        top_suppliers_list = []
        top_suppliers_data = (
            MaterialBatch.objects.filter(date_filter_incoming)
            .exclude(supplier_name='')
            .values('supplier_name')
            .annotate(quantity_sum=Sum('quantity_initial'))
            .order_by('-quantity_sum')[:5]
        )
        for s in top_suppliers_data:
            supplier_total = MaterialBatch.objects.filter(
                date_filter_incoming, supplier_name=s['supplier_name']
            ).aggregate(
                amount=Sum('total_price')
            )
            top_suppliers_list.append({
                'supplier': s['supplier_name'],
                'amount': api_decimal_str(supplier_total['amount'] or Decimal('0')),
            })

        # --- Производство по продуктам / линиям ---
        production_by_product_list = [
            {
                'product_name': x['product'],
                'batches': x['batches'],
                'total_meters': api_decimal_str(x['tm'] or Decimal('0')),
                'pieces': str(int(x['pc'] or 0)),
            }
            for x in ProductionBatch.objects.filter(date_filter_batches)
            .values('product')
            .annotate(batches=Count('id'), tm=Sum('total_meters'), pc=Sum('pieces'))
            .order_by('-tm')[:10]
        ]

        production_by_line_list = [
            {
                'line_name': (x['line__name'] or '') or '—',
                'batches': x['batches'],
                'total_meters': api_decimal_str(x['tm'] or Decimal('0')),
            }
            for x in ProductionBatch.objects.filter(date_filter_batches)
            .values('line__name')
            .annotate(batches=Count('id'), tm=Sum('total_meters'))
            .order_by('-tm')
        ]

        batches_stats = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            total_batches=Count('id'),
            total_meters=Sum('total_meters'),
        )

        # --- Склад ГП (срез остатков, не только период) ---
        wh_stat_qs = WarehouseBatch.objects.all()
        if scope.profile_id:
            wh_stat_qs = wh_stat_qs.filter(profile_id=scope.profile_id)
        warehouse_stats = wh_stat_qs.aggregate(
            available=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_AVAILABLE)),
            reserved=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_RESERVED)),
            shipped=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_SHIPPED)),
        )
        warehouse_by_product_list = [
            {
                'product_name': w['product'],
                'available': api_decimal_str(w['available'] or Decimal('0')),
                'reserved': api_decimal_str(w['reserved'] or Decimal('0')),
            }
            for w in wh_stat_qs.values('product').annotate(
                available=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_AVAILABLE)),
                reserved=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_RESERVED)),
            ).order_by('-available')[:10]
        ]

        shipments_stats = Shipment.objects.filter(date_filter_shipments).aggregate(
            total_count=Count('id'),
            pending=Count('id', filter=Q(status=Shipment.STATUS_PENDING)),
            shipped=Count('id', filter=Q(status=Shipment.STATUS_SHIPPED)),
            delivered=Count('id', filter=Q(status=Shipment.STATUS_DELIVERED)),
        )

        # --- Остатки сырья (текущие): low только при заданном min_balance ---
        raw_materials_rows = []
        for m in RawMaterial.objects.filter(is_active=True):
            balance = MaterialBatch.objects.filter(material=m).aggregate(s=Sum('quantity_remaining'))['s'] or Decimal('0')
            if balance <= 0:
                continue
            min_b = m.min_balance
            low = bool(min_b is not None and balance <= min_b)
            raw_materials_rows.append((balance, {
                'name': m.name,
                'balance_kg': api_decimal_str(balance),
                'unit': m.unit,
                'min_balance': api_decimal_str(min_b) if min_b is not None else None,
                'low_stock': low,
            }))
        raw_materials_rows.sort(key=lambda x: x[0])
        raw_materials_list = [r[1] for r in raw_materials_rows]

        chemistry_list = []
        for row in (
            ChemistryCatalog.objects.filter(is_active=True)
            .annotate(bal=Sum('batches__quantity_remaining'))
            .filter(bal__gt=0)
            .order_by('name')
        ):
            bal = row.bal or Decimal('0')
            mb = row.min_balance
            chemistry_list.append({
                'name': row.name,
                'balance_kg': api_decimal_str(bal),
                'unit': row.unit,
                'min_balance': api_decimal_str(mb) if mb is not None else None,
                'low_stock': bool(mb is not None and bal <= mb),
            })

        # --- Тренды по дням (окно внутри выбранного месяца/года) ---
        if p.day is not None and p.month is not None:
            end_date = datetime(p.year, p.month, p.day).date()
            start_date = end_date - timedelta(days=6)
        elif p.month is not None:
            start_date = datetime(p.year, p.month, 1).date()
            last_day = monthrange(p.year, p.month)[1]
            end_date = datetime(p.year, p.month, last_day).date()
        else:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=29)

        daily_revenue_list = []
        for d in (
            Sale.objects.filter(sq, date__gte=start_date, date__lte=end_date)
            .values('date')
            .annotate(quantity_sum=Sum('quantity'))
            .order_by('date')
        ):
            day_revenue = Sale.objects.filter(sq, date=d['date']).aggregate(
                revenue=Sum('revenue'),
            )
            daily_revenue_list.append({
                'date': d['date'].strftime('%Y-%m-%d'),
                'revenue': api_decimal_str(day_revenue['revenue'] or Decimal('0')),
            })

        daily_production_list = [
            {
                'date': d['date'].strftime('%Y-%m-%d'),
                'total_meters': api_decimal_str(d['tm'] or Decimal('0')),
            }
            for d in ProductionBatch.objects.filter(
                date_filter_batches,
                date__gte=start_date,
                date__lte=end_date,
            )
            .values('date')
            .annotate(tm=Sum('total_meters'))
            .order_by('date')
        ]

        daily_expenses_list = []
        for d in (
            MaterialBatch.objects.filter(received_at__date__gte=start_date, received_at__date__lte=end_date)
            .annotate(day=TruncDate('received_at'))
            .values('day')
            .annotate(quantity_sum=Sum('quantity_initial'))
            .order_by('day')
        ):
            day_val = d['day']
            day_expense = MaterialBatch.objects.filter(received_at__date=day_val).aggregate(
                expense=Sum('total_price')
            )
            daily_expenses_list.append({
                'date': day_val.strftime('%Y-%m-%d') if day_val else None,
                'expense': api_decimal_str(day_expense['expense'] or Decimal('0')),
            })

        daily_writeoffs_list = []
        for d in (
            MaterialStockDeduction.objects.filter(
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(quantity=Sum('quantity'), n=Count('id'))
            .order_by('day')
        ):
            day_val = d['day']
            daily_writeoffs_list.append({
                'date': day_val.strftime('%Y-%m-%d') if day_val else None,
                'quantity': api_decimal_str(d['quantity'] or Decimal('0')),
                'lines': d['n'],
            })

        otk_pb_ids = ProductionBatch.objects.filter(date_filter_batches).values_list('id', flat=True)
        otk_agg = OtkCheck.objects.filter(batch_id__in=otk_pb_ids).aggregate(
            acc=Sum('accepted'),
            rej=Sum('rejected'),
        )
        otk_accepted_total = otk_agg['acc'] or Decimal('0')
        otk_defect_total = otk_agg['rej'] or Decimal('0')
        otk_sum = otk_accepted_total + otk_defect_total
        otk_defect_rate_pct = (
            (otk_defect_total / otk_sum * Decimal('100')).quantize(Decimal('0.0001'))
            if otk_sum > 0
            else Decimal('0')
        )

        return Response({
            'period': scope.as_period_dict(),
            'finances': {
                'revenue': api_decimal_str(total_revenue),
                'material_purchases': api_decimal_str(total_expenses),
                'profit_revenue_minus_purchases': api_decimal_str(profit_simple),
                'profit_sum_in_sales': api_decimal_str(profit_recorded),
                'production_material_cost_sum': api_decimal_str(total_cost_price),
                'material_writeoffs_fifo_cost': api_decimal_str(writeoff_finance_total),
            },
            'material_flow': {
                'incoming': {
                    'documents': expenses_data['incoming_lines'] or 0,
                    'total_quantity': api_decimal_str(expenses_data['incoming_qty'] or Decimal('0')),
                    'total_value': api_decimal_str(total_expenses),
                },
                'writeoffs': {
                    'lines': wo_agg['n'] or 0,
                    'total_quantity': api_decimal_str(wo_agg['qty'] or Decimal('0')),
                    'by_reason': [
                        {
                            'reason': (x['reason'] or '—'),
                            'count': x['count'],
                            'quantity': api_decimal_str(x['quantity'] or Decimal('0')),
                        }
                        for x in wo_by_reason
                    ],
                    'top_materials': [
                        {
                            'material_name': x['batch__material__name'],
                            'unit': x['batch__material__unit'],
                            'count': x['count'],
                            'quantity': api_decimal_str(x['quantity'] or Decimal('0')),
                        }
                        for x in wo_top_materials
                    ],
                    'fifo_cost_total': api_decimal_str(wo_value_est),
                    'lines_with_known_price': wo_valued_lines,
                },
            },
            'chemistry': {
                'catalog_positions_with_stock': len(chemistry_list),
                'remaining_kg_snapshot': api_decimal_str(chemistry_remaining_kg_snapshot),
                'produced_kg_in_period': api_decimal_str(chem_done_qty or Decimal('0')),
            },
            'sales': {
                'total_count': sales_data['count'],
                'total_quantity': api_decimal_str(sales_total_qty or Decimal('0')),
                'total_revenue': api_decimal_str(total_revenue),
                'top_products': top_products_list,
                'top_clients': top_clients_list,
            },
            'expenses_breakdown': {
                'material_purchases_total': api_decimal_str(total_expenses),
                'by_supplier': top_suppliers_list,
            },
            'production': {
                'total_batches': batches_stats['total_batches'],
                'total_meters': api_decimal_str(batches_stats['total_meters'] or Decimal('0')),
                'by_product': production_by_product_list,
                'by_line': production_by_line_list,
                'otk_batches_by_status': [
                    {
                        'otk_status': x['otk_status'],
                        'count': x['n'],
                        'total_meters': api_decimal_str(x['total_meters'] or Decimal('0')),
                    }
                    for x in otk_by_status
                ],
            },
            'warehouse': {
                'snapshot': {
                    'total_available': api_decimal_str(warehouse_stats['available'] or Decimal('0')),
                    'total_reserved': api_decimal_str(warehouse_stats['reserved'] or Decimal('0')),
                    'total_shipped': api_decimal_str(warehouse_stats['shipped'] or Decimal('0')),
                    'by_product': warehouse_by_product_list,
                },
                'new_in_period': {
                    'batches': wh_new_stats['n'] or 0,
                    'quantity': api_decimal_str(wh_new_stats['qty'] or Decimal('0')),
                    'by_status': [
                        {
                            'status': x['status'],
                            'count': x['n'],
                            'quantity': api_decimal_str(x['quantity'] or Decimal('0')),
                        }
                        for x in wh_by_status
                    ],
                },
            },
            'shipments': {
                'total_count': shipments_stats['total_count'],
                'pending': shipments_stats['pending'],
                'shipped': shipments_stats['shipped'],
                'delivered': shipments_stats['delivered'],
            },
            'operations': {
                'recipe_runs_started': recipe_runs_n,
                'shifts_opened': shifts_opened,
                'shifts_closed_in_period': shifts_closed,
                'shift_complaints': complaints_n,
            },
            'audit': {
                'events_by_section': [
                    {'section': x['section'] or '—', 'count': x['n']}
                    for x in audit_by_section
                ],
            },
            'stock_balances': {
                'raw_materials': raw_materials_list,
                'chemistry': chemistry_list,
            },
            'otk': {
                'accepted_total': api_decimal_str(otk_accepted_total),
                'defect_total': api_decimal_str(otk_defect_total),
                'defect_rate_pct': api_decimal_str(otk_defect_rate_pct),
            },
            'trends': {
                'window': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
                'daily_revenue': daily_revenue_list,
                'daily_material_purchases': daily_expenses_list,
                'daily_production_meters': daily_production_list,
                'daily_raw_writeoffs': daily_writeoffs_list,
            },
        })


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация выручки (продажи)',
        parameters=_ANALYTICS_DETAILS_PERIOD_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsRevenueDetailsView(viewsets.ViewSet):
    """Детализация выручки (продажи)."""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        if request.query_params.get('year') in (None, ''):
            raise DRFValidationError({'year': ['Обязательный query-параметр']})
        p = parse_period(request)
        date_filter = p.sale_q()

        sales = Sale.objects.filter(date_filter).select_related('client').order_by('-date', '-id')

        items = []
        total = Decimal('0')
        for sale in sales:
            sale_total = sale.revenue or Decimal('0')
            total += sale_total
            items.append({
                'id': sale.id,
                'date': sale.date.strftime('%Y-%m-%d'),
                'client_name': sale.client.name if sale.client_id else '',
                'product_name': sale.product,
                'quantity': api_decimal_str(sale.quantity),
                'price_per_unit': api_decimal_str(sale.price or Decimal('0')),
                'total': api_decimal_str(sale_total),
                'revenue': api_decimal_str(sale.revenue or Decimal('0')),
                'cost': api_decimal_str(sale.cost or Decimal('0')),
                'profit': api_decimal_str(sale.profit or Decimal('0')),
                'warehouse_batch_id': sale.warehouse_batch_id,
            })

        return Response({'total': api_decimal_str(total), 'items': items})


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация закупок сырья',
        parameters=_ANALYTICS_DETAILS_PERIOD_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
    ),
)
class AnalyticsExpenseDetailsView(viewsets.ViewSet):
    """Детализация закупок сырья (приходы)."""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        if request.query_params.get('year') in (None, ''):
            raise DRFValidationError({'year': ['Обязательный query-параметр']})
        p = parse_period(request)
        date_filter = p.incoming_q()

        incomings = MaterialBatch.objects.filter(date_filter).select_related('material').order_by('-received_at', '-id')

        items = []
        total = Decimal('0')
        for incoming in incomings:
            incoming_total = incoming.total_price or Decimal('0')
            total += incoming_total
            items.append({
                'id': incoming.id,
                'date': incoming.received_at.date().strftime('%Y-%m-%d'),
                'received_at': incoming.received_at.isoformat(),
                'created_at': incoming.created_at.isoformat(),
                'material_name': incoming.material.name,
                'supplier_name': incoming.supplier_name or '',
                'quantity': api_decimal_str(incoming.quantity_initial),
                'quantity_remaining': api_decimal_str(incoming.quantity_remaining),
                'unit': incoming.unit,
                'unit_price': api_decimal_str(incoming.unit_price or Decimal('0')),
                'total': api_decimal_str(incoming_total),
                'supplier_batch_number': incoming.supplier_batch_number or '',
                'comment': incoming.comment or '',
            })

        return Response({'total': api_decimal_str(total), 'items': items})


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация списаний сырья',
        parameters=_ANALYTICS_DETAILS_PERIOD_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
        description=(
            'Корень: **`total`** (строка, FIFO `line_total`), **`items`**. '
            'Строка `items[]`: `date`, `created_at`, `material_name`, `quantity`, `unit`, `reason`, `reference_id`, `fifo_line_total`.'
        ),
    ),
)
class AnalyticsWriteoffDetailsView(viewsets.ViewSet):
    """Детализация списаний сырья за период (по created_at)."""
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
        total_s = api_decimal_str(total_est)
        return Response({
            'total': total_s,
            'items': items,
        })
