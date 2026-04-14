from decimal import Decimal
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
            'Крупный вложенный JSON: выручка, расходы, производство, смены, ОТК и т.д. '
            'В **finances** для карточки «Списания сырья» (сумма за период, **фактическая стоимость FIFO**): '
            'каноническое поле **`writeoff_total`**; дубли: `writeoffs`, `write_offs`, `material_writeoffs`. '
            '`material_flow.writeoffs.fifo_cost_total` и `estimated_cost_by_avg_purchase_price` — сумма `line_total` по списаниям.'
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
        total_revenue = float(sales_data['revenue'] or 0)
        profit_recorded = float(sales_data['profit_sum'] or 0)

        expenses_data = MaterialBatch.objects.filter(date_filter_incoming).aggregate(
            expenses=Sum('total_price'),
            incoming_lines=Count('id'),
            incoming_qty=Sum('quantity_initial'),
        )
        total_expenses = float(expenses_data['expenses'] or 0)
        profit_simple = total_revenue - total_expenses

        cost_price_data = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            cost=Sum('material_cost_total'),
        )
        total_cost_price = float(cost_price_data['cost'] or 0)

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
        writeoff_finance_total = float(wo_value_est)

        # --- ОТК / производственные партии ---
        otk_by_status = list(
            ProductionBatch.objects.filter(date_filter_batches)
            .values('otk_status')
            .annotate(n=Count('id'), quantity=Sum('quantity'))
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

        # --- Химия: остатки (сумма по партиям) и выпуск по заданиям за период ---
        chemistry_balance_total = float(
            ChemistryBatch.objects.aggregate(s=Sum('quantity_remaining'))['s'] or 0
        )

        date_filter_chem_batches = scope.writeoff_q()  # created_at — те же границы периода
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
                'quantity': float(agg['qty'] or 0),
                'revenue': float(agg['rev'] or 0),
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
                'quantity': float(agg['qty'] or 0),
                'revenue': float(agg['rev'] or 0),
            })

        sales_total_qty = Sale.objects.filter(sq).aggregate(s=Sum('quantity'))['s'] or 0

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
                'amount': float(supplier_total['amount'] or 0),
            })

        # --- Производство по продуктам / линиям ---
        production_by_product_list = [
            {
                'product_name': x['product'],
                'batches': x['batches'],
                'total_meters': float(x['tm'] or 0),
                'pieces': float(x['pc'] or 0),
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
                'total_meters': float(x['tm'] or 0),
            }
            for x in ProductionBatch.objects.filter(date_filter_batches)
            .values('line__name')
            .annotate(batches=Count('id'), tm=Sum('total_meters'))
            .order_by('-tm')
        ]

        batches_stats = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            total_batches=Count('id'),
            total_quantity=Sum('quantity'),
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
                'available': float(w['available'] or 0),
                'reserved': float(w['reserved'] or 0),
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

        # --- Остатки сырья (текущие) с порогом min_balance ---
        raw_materials_list = []
        for m in RawMaterial.objects.filter(is_active=True):
            balance = float(
                MaterialBatch.objects.filter(material=m).aggregate(s=Sum('quantity_remaining'))['s'] or 0
            )
            if balance <= 0:
                continue
            min_b = m.min_balance
            low = False
            if min_b is not None and balance <= float(min_b):
                low = True
            elif min_b is None and balance < 50:
                low = True
            raw_materials_list.append({
                'name': m.name,
                'balance': balance,
                'unit': m.unit,
                'min_balance': float(min_b) if min_b is not None else None,
                'low_stock': low,
            })
        raw_materials_list.sort(key=lambda x: x['balance'])

        chemistry_list = [
            {
                'name': row.name,
                'balance': float(row.bal or 0),
                'unit': row.unit,
                'low_stock': float(row.bal or 0) < 10,
            }
            for row in ChemistryCatalog.objects.filter(is_active=True)
            .annotate(bal=Sum('batches__quantity_remaining'))
            .filter(bal__gt=0)
            .order_by('name')
        ]

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
                'revenue': float(day_revenue['revenue'] or 0),
            })

        daily_production_list = [
            {
                'date': d['date'].strftime('%Y-%m-%d'),
                'total_meters': float(d['tm'] or 0),
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
                'expense': float(day_expense['expense'] or 0),
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
                'quantity': float(d['quantity'] or 0),
                'lines': d['n'],
            })

        otk_pb_ids = ProductionBatch.objects.filter(date_filter_batches).values_list('id', flat=True)
        otk_agg = OtkCheck.objects.filter(batch_id__in=otk_pb_ids).aggregate(
            acc=Sum('accepted'),
            rej=Sum('rejected'),
        )
        otk_accepted_total = float(otk_agg['acc'] or 0)
        otk_defect_total = float(otk_agg['rej'] or 0)
        otk_sum = otk_accepted_total + otk_defect_total
        otk_defect_rate_pct = (otk_defect_total / otk_sum * 100.0) if otk_sum > 0 else 0.0

        return Response({
            'period': scope.as_period_dict(),
            'finances': {
                'revenue': total_revenue,
                'expenses': total_expenses,
                'profit': profit_simple,
                'cost_price': total_cost_price,
                'expenses_purchases': total_expenses,
                'profit_simple': profit_simple,
                'profit_recorded_in_sales': profit_recorded,
                'production_cost_recorded': total_cost_price,
                # Карточка «Списания сырья»: канон writeoff_total; остальные — алиасы для старого фронта
                'writeoff_total': writeoff_finance_total,
                'writeoffs': writeoff_finance_total,
                'write_offs': writeoff_finance_total,
                'material_writeoffs': writeoff_finance_total,
                'note': (
                    'profit / profit_simple = выручка − закупки сырья за период. '
                    'profit_recorded_in_sales = сумма поля profit в продажах. '
                    'cost_price = сумма cost_price партий ОТК с датой в периоде. '
                    'writeoff_total = фактическая стоимость списаний (FIFO по партиям, сумма line_total).'
                ),
            },
            'material_flow': {
                'incoming': {
                    'documents': expenses_data['incoming_lines'] or 0,
                    'total_quantity': float(expenses_data['incoming_qty'] or 0),
                    'total_value': total_expenses,
                },
                'writeoffs': {
                    'lines': wo_agg['n'] or 0,
                    'total_quantity': float(wo_agg['qty'] or 0),
                    'by_reason': [
                        {
                            'reason': (x['reason'] or '—'),
                            'count': x['count'],
                            'quantity': float(x['quantity'] or 0),
                        }
                        for x in wo_by_reason
                    ],
                    'top_materials': [
                        {
                            'material_name': x['batch__material__name'],
                            'unit': x['batch__material__unit'],
                            'count': x['count'],
                            'quantity': float(x['quantity'] or 0),
                        }
                        for x in wo_top_materials
                    ],
                    'fifo_cost_total': float(wo_value_est),
                    'estimated_cost_by_avg_purchase_price': float(wo_value_est),
                    'lines_with_known_price': wo_valued_lines,
                },
            },
            'chemistry': {
                'stock_positions_positive': len(chemistry_list),
                'stock_quantity_sum': chemistry_balance_total,
                'tasks_marked_done_linked_to_writeoffs_qty': float(chem_done_qty or 0),
                'note': 'Выпуск химии — партии ChemistryBatch; сырьё списывается (FIFO) при produce/confirm.',
            },
            'sales': {
                'total_count': sales_data['count'],
                'total_quantity': float(sales_total_qty),
                'total_revenue': total_revenue,
                'top_products': top_products_list,
                'top_clients': top_clients_list,
            },
            'expenses_breakdown': {
                'raw_materials': total_expenses,
                'raw_materials_purchases': total_expenses,
                'by_supplier': top_suppliers_list,
            },
            'production': {
                'total_batches': batches_stats['total_batches'],
                'total_quantity': float(batches_stats['total_quantity'] or 0),
                'by_product': production_by_product_list,
                'by_line': production_by_line_list,
                'otk_batches_by_status': [
                    {
                        'otk_status': x['otk_status'],
                        'count': x['n'],
                        'quantity': float(x['quantity'] or 0),
                    }
                    for x in otk_by_status
                ],
            },
            'warehouse': {
                'total_available': float(warehouse_stats['available'] or 0),
                'total_reserved': float(warehouse_stats['reserved'] or 0),
                'total_shipped': float(warehouse_stats['shipped'] or 0),
                'by_product': warehouse_by_product_list,
            },
            'warehouse_finished_goods': {
                'snapshot': {
                    'total_available': float(warehouse_stats['available'] or 0),
                    'total_reserved': float(warehouse_stats['reserved'] or 0),
                    'total_shipped': float(warehouse_stats['shipped'] or 0),
                    'by_product': warehouse_by_product_list,
                },
                'new_in_period': {
                    'batches': wh_new_stats['n'] or 0,
                    'quantity': float(wh_new_stats['qty'] or 0),
                    'by_status': [
                        {
                            'status': x['status'],
                            'count': x['n'],
                            'quantity': float(x['quantity'] or 0),
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
                'accepted_total': otk_accepted_total,
                'defect_total': otk_defect_total,
                'defect_rate_pct': round(otk_defect_rate_pct, 4),
            },
            'trends': {
                'window': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
                'daily_revenue': daily_revenue_list,
                'daily_expenses': daily_expenses_list,
                'daily_purchases': daily_expenses_list,
                'daily_production': daily_production_list,
                'daily_production_otk_batches': daily_production_list,
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
                'quantity': float(sale.quantity),
                'price_per_unit': float(sale.price or 0),
                'total': float(sale_total),
                'revenue': float(sale.revenue or 0),
                'cost': float(sale.cost or 0),
                'profit': float(sale.profit or 0),
                'warehouse_batch_id': sale.warehouse_batch_id,
            })

        return Response({'total': float(total), 'items': items})


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
                'quantity': float(incoming.quantity_initial),
                'quantity_initial': float(incoming.quantity_initial),
                'quantity_remaining': float(incoming.quantity_remaining),
                'unit': incoming.unit,
                'unit_price': float(incoming.unit_price or 0),
                'price_per_unit': float(incoming.unit_price or 0),
                'total': float(incoming_total),
                'supplier_batch_number': incoming.supplier_batch_number or '',
                'comment': incoming.comment or '',
                'incoming_id': incoming.id,
            })

        return Response({'total': float(total), 'items': items})


@extend_schema_view(
    list=extend_schema(
        tags=['analytics'],
        summary='Детализация списаний сырья',
        parameters=_ANALYTICS_DETAILS_PERIOD_PARAMS,
        responses={200: OpenApiTypes.OBJECT, 400: DiasErrorSerializer, 401: DiasErrorSerializer, 403: DiasErrorSerializer},
        description=(
            'Корень: **`total`** (число, как у revenue/expense details), **`items`**, опционально **`note`**. '
            'Дубль суммы: **`total_estimated_value`** = `total`. '
            'Строка `items[]`: канон **`date`** (YYYY-MM-DD из created_at), **`material_name`**, **`quantity`**, **`unit`**, '
            '`reason`, `reference_id`, **`estimated_value`** (оценка или null); алиасы суммы строки: '
            '`amount`, `value`, `cost`, `total` (= estimated_value); подпись: `raw_material_name`, `name` (= material_name). '
            'ISO-время: `created_at`.'
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
            ev = float(line_est)
            material_name = w.batch.material.name
            items.append({
                'id': w.id,
                'batch_id': w.batch_id,
                'date': date_str,
                'created_at': created.isoformat() if created else None,
                'material_name': material_name,
                'raw_material_name': material_name,
                'name': material_name,
                'quantity': float(w.quantity),
                'unit': w.batch.material.unit,
                'reason': w.reason or '',
                'reference_id': w.reference_id,
                'estimated_value': ev,
                'fifo_line_total': ev,
                'amount': ev,
                'value': ev,
                'cost': ev,
                'total': ev,
            })
        total_float = float(total_est)
        return Response({
            'total': total_float,
            'total_estimated_value': total_float,
            'items': items,
            'note': 'Стоимость строки — фактическая (FIFO): line_total при списании с партии.',
        })
