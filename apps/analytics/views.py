from decimal import Decimal
from datetime import datetime, timedelta
from calendar import monthrange

from django.db import models
from django.db.models import Sum, F, Count, Q
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
from apps.materials.models import Incoming as MaterialIncoming, RawMaterial, MaterialWriteoff
from apps.production.models import ProductionBatch, RecipeRun, Shift, ShiftComplaint
from apps.chemistry.models import ChemistryStock, ChemistryTask
from apps.activity.models import UserActivity

from .services import Period, parse_period, material_avg_unit_prices, estimate_writeoff_value

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
            'В **finances** для карточки «Списания сырья» (сумма за период, оценка по средней цене закупки): '
            'каноническое поле **`writeoff_total`**; дубли для совместимости: `writeoffs`, `write_offs`, `material_writeoffs` '
            '(все равны оценочной стоимости списаний, см. `material_flow.writeoffs.estimated_cost_by_avg_purchase_price`).'
        ),
    ),
)
class AnalyticsSummaryView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        p: Period = parse_period(request)

        date_filter_sales = p.sale_q()
        date_filter_incoming = p.incoming_q()
        date_filter_batches = p.batch_q()
        date_filter_shipments = p.shipment_by_sale_q()
        date_filter_writeoffs = p.writeoff_q()
        date_filter_wh_batches = p.warehouse_batch_q()
        date_filter_shifts = p.shift_opened_q()
        date_filter_recipe_runs = p.recipe_run_q()
        date_filter_activity = p.activity_q()
        date_filter_complaints = p.complaint_q()

        # --- Финансы (продажи vs закупки сырья) ---
        sales_data = Sale.objects.filter(date_filter_sales).aggregate(
            revenue=Sum(F('quantity') * F('price'), output_field=models.DecimalField()),
            count=Count('id'),
            profit_sum=Sum('profit'),
        )
        total_revenue = float(sales_data['revenue'] or 0)
        profit_recorded = float(sales_data['profit_sum'] or 0)

        expenses_data = MaterialIncoming.objects.filter(date_filter_incoming).aggregate(
            expenses=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField()),
            incoming_lines=Count('id'),
            incoming_qty=Sum('quantity'),
        )
        total_expenses = float(expenses_data['expenses'] or 0)
        profit_simple = total_revenue - total_expenses

        cost_price_data = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            cost=Sum('cost_price'),
        )
        total_cost_price = float(cost_price_data['cost'] or 0)

        # --- Списания сырья за период ---
        wo_qs = MaterialWriteoff.objects.filter(date_filter_writeoffs).select_related('material')
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
            wo_qs.values('material__name', 'material__unit')
            .annotate(count=Count('id'), quantity=Sum('quantity'))
            .order_by('-quantity')[:15]
        )
        prices = material_avg_unit_prices()
        wo_value_est, wo_valued_lines = estimate_writeoff_value(wo_qs, prices)
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

        # --- Химия: остатки и задания (выполненные за период по связи со списаниями) ---
        chem_stock_rows = ChemistryStock.objects.filter(quantity__gt=0).select_related('chemistry')
        chemistry_balance_total = sum(float(c.quantity or 0) for c in chem_stock_rows)

        chem_task_ids = (
            MaterialWriteoff.objects.filter(date_filter_writeoffs, reason='chemistry_task')
            .exclude(reference_id__isnull=True)
            .values_list('reference_id', flat=True)
            .distinct()
        )
        chem_done_qty = (
            ChemistryTask.objects.filter(id__in=chem_task_ids, status='done').aggregate(s=Sum('quantity'))['s']
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
            Sale.objects.filter(date_filter_sales)
            .values('product')
            .annotate(qty=Sum('quantity'))
            .order_by('-qty')[:5]
        )
        for row in top_products_keys:
            prod = row['product']
            agg = Sale.objects.filter(date_filter_sales, product=prod).aggregate(
                qty=Sum('quantity'),
                rev=Sum(F('quantity') * F('price'), output_field=models.DecimalField()),
            )
            top_products_list.append({
                'product_name': prod,
                'quantity': float(agg['qty'] or 0),
                'revenue': float(agg['rev'] or 0),
            })

        top_clients_list = []
        top_clients_keys = (
            Sale.objects.filter(date_filter_sales)
            .values('client__name')
            .annotate(qty=Sum('quantity'))
            .order_by('-qty')[:5]
        )
        for row in top_clients_keys:
            cname = row['client__name']
            agg = Sale.objects.filter(date_filter_sales, client__name=cname).aggregate(
                qty=Sum('quantity'),
                rev=Sum(F('quantity') * F('price'), output_field=models.DecimalField()),
            )
            top_clients_list.append({
                'client_name': cname or '—',
                'quantity': float(agg['qty'] or 0),
                'revenue': float(agg['rev'] or 0),
            })

        sales_total_qty = Sale.objects.filter(date_filter_sales).aggregate(s=Sum('quantity'))['s'] or 0

        # --- Поставщики ---
        top_suppliers_list = []
        top_suppliers_data = (
            MaterialIncoming.objects.filter(date_filter_incoming)
            .exclude(supplier='')
            .values('supplier')
            .annotate(quantity_sum=Sum('quantity'))
            .order_by('-quantity_sum')[:5]
        )
        for s in top_suppliers_data:
            supplier_total = MaterialIncoming.objects.filter(
                date_filter_incoming, supplier=s['supplier']
            ).aggregate(
                amount=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField())
            )
            top_suppliers_list.append({
                'supplier': s['supplier'],
                'amount': float(supplier_total['amount'] or 0),
            })

        # --- Производство по продуктам / линиям ---
        production_by_product_list = [
            {
                'product_name': x['product'],
                'batches': x['batches'],
                'quantity': float(x['quantity'] or 0),
            }
            for x in ProductionBatch.objects.filter(date_filter_batches)
            .values('product')
            .annotate(batches=Count('id'), quantity=Sum('quantity'))
            .order_by('-quantity')[:10]
        ]

        production_by_line_list = [
            {
                'line_name': x['order__line__name'],
                'batches': x['batches'],
                'quantity': float(x['quantity'] or 0),
            }
            for x in ProductionBatch.objects.filter(date_filter_batches)
            .values('order__line__name')
            .annotate(batches=Count('id'), quantity=Sum('quantity'))
            .order_by('-quantity')
        ]

        batches_stats = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            total_batches=Count('id'),
            total_quantity=Sum('quantity'),
        )

        # --- Склад ГП (срез остатков, не только период) ---
        warehouse_stats = WarehouseBatch.objects.aggregate(
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
            for w in WarehouseBatch.objects.values('product').annotate(
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
        for m in RawMaterial.objects.all():
            total_in = MaterialIncoming.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            total_out = MaterialWriteoff.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            balance = float(Decimal(str(total_in)) - Decimal(str(total_out)))
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
                'name': c.chemistry.name,
                'balance': float(c.quantity),
                'unit': c.unit,
                'low_stock': float(c.quantity) < 10,
            }
            for c in ChemistryStock.objects.filter(quantity__gt=0).select_related('chemistry')
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
            Sale.objects.filter(date__gte=start_date, date__lte=end_date)
            .values('date')
            .annotate(quantity_sum=Sum('quantity'))
            .order_by('date')
        ):
            day_revenue = Sale.objects.filter(date=d['date']).aggregate(
                revenue=Sum(F('quantity') * F('price'), output_field=models.DecimalField())
            )
            daily_revenue_list.append({
                'date': d['date'].strftime('%Y-%m-%d'),
                'revenue': float(day_revenue['revenue'] or 0),
            })

        daily_production_list = [
            {
                'date': d['date'].strftime('%Y-%m-%d'),
                'quantity': float(d['quantity'] or 0),
            }
            for d in ProductionBatch.objects.filter(date__gte=start_date, date__lte=end_date)
            .values('date')
            .annotate(quantity=Sum('quantity'))
            .order_by('date')
        ]

        daily_expenses_list = []
        for d in (
            MaterialIncoming.objects.filter(date__gte=start_date, date__lte=end_date)
            .values('date')
            .annotate(quantity_sum=Sum('quantity'))
            .order_by('date')
        ):
            day_expense = MaterialIncoming.objects.filter(date=d['date']).aggregate(
                expense=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField())
            )
            daily_expenses_list.append({
                'date': d['date'].strftime('%Y-%m-%d'),
                'expense': float(day_expense['expense'] or 0),
            })

        daily_writeoffs_list = []
        for d in (
            MaterialWriteoff.objects.filter(
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

        return Response({
            'period': p.as_dict(),
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
                    'writeoff_total = оценка стоимости списаний сырья за период (quantity × средневзвешенная цена закупки).'
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
                            'material_name': x['material__name'],
                            'unit': x['material__unit'],
                            'count': x['count'],
                            'quantity': float(x['quantity'] or 0),
                        }
                        for x in wo_top_materials
                    ],
                    'estimated_cost_by_avg_purchase_price': float(wo_value_est),
                    'lines_with_known_price': wo_valued_lines,
                },
            },
            'chemistry': {
                'stock_positions_positive': len(chemistry_list),
                'stock_quantity_sum': chemistry_balance_total,
                'tasks_marked_done_linked_to_writeoffs_qty': float(chem_done_qty or 0),
                'note': 'Списание сырья под химию — MaterialWriteoff с reason=chemistry_task; выпуск в остатки — confirm задания.',
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
            sale_total = (sale.quantity or 0) * (sale.price or 0)
            total += sale_total
            items.append({
                'id': sale.id,
                'date': sale.date.strftime('%Y-%m-%d'),
                'client_name': sale.client.name if sale.client_id else '',
                'product_name': sale.product,
                'quantity': float(sale.quantity),
                'price_per_unit': float(sale.price or 0),
                'total': float(sale_total),
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

        incomings = MaterialIncoming.objects.filter(date_filter).select_related('material').order_by('-date', '-id')

        items = []
        total = Decimal('0')
        for incoming in incomings:
            incoming_total = (incoming.quantity or 0) * (incoming.price_per_unit or 0)
            total += incoming_total
            items.append({
                'id': incoming.id,
                'date': incoming.date.strftime('%Y-%m-%d'),
                'material_name': incoming.material.name,
                'supplier': incoming.supplier or '',
                'quantity': float(incoming.quantity),
                'unit': incoming.unit,
                'price_per_unit': float(incoming.price_per_unit or 0),
                'total': float(incoming_total),
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
            MaterialWriteoff.objects.filter(p.writeoff_q())
            .select_related('material')
            .order_by('-created_at', '-id')
        )
        prices = material_avg_unit_prices()
        items = []
        total_est = Decimal('0')
        for w in qs:
            unit_price = prices.get(w.material_id)
            line_est = (w.quantity or Decimal('0')) * unit_price if unit_price is not None else None
            if line_est is not None:
                total_est += line_est
            created = w.created_at
            date_str = created.date().isoformat() if created else None
            ev = float(line_est) if line_est is not None else None
            material_name = w.material.name
            items.append({
                'id': w.id,
                'date': date_str,
                'created_at': created.isoformat() if created else None,
                'material_name': material_name,
                'raw_material_name': material_name,
                'name': material_name,
                'quantity': float(w.quantity),
                'unit': w.unit,
                'reason': w.reason or '',
                'reference_id': w.reference_id,
                'estimated_value': ev,
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
            'note': 'Стоимость оценочная: quantity × средневзвешенная цена закупки по всем приходам данного сырья.',
        })
