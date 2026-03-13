from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import models
from django.db.models import Sum, F, Count, Q
from django.db.models.functions import TruncDate
from decimal import Decimal
from datetime import datetime, timedelta

from config.permissions import IsAdminOrHasAccess
from apps.sales.models import Sale, Shipment
from apps.warehouse.models import WarehouseBatch
from apps.materials.models import Incoming as MaterialIncoming, RawMaterial
from apps.production.models import ProductionBatch, Line
from apps.chemistry.models import ChemistryStock


class AnalyticsSummaryView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        # Фильтры по периоду
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        day = request.query_params.get('day')
        
        # Определяем период (с валидацией)
        try:
            year = int(year) if year else datetime.now().year
        except (ValueError, TypeError):
            year = datetime.now().year
        try:
            month = int(month) if month else None
        except (ValueError, TypeError):
            month = None
        try:
            day = int(day) if day else None
        except (ValueError, TypeError):
            day = None
        
        period = {'year': year, 'month': month, 'day': day}
        
        # Базовый фильтр по году
        date_filter_sales = Q(date__year=year)
        date_filter_incoming = Q(date__year=year)
        date_filter_batches = Q(date__year=year)
        date_filter_shipments = Q(sale__date__year=year)
        
        if month is not None:
            date_filter_sales &= Q(date__month=month)
            date_filter_incoming &= Q(date__month=month)
            date_filter_batches &= Q(date__month=month)
            date_filter_shipments &= Q(sale__date__month=month)
        
        if day is not None:
            date_filter_sales &= Q(date__day=day)
            date_filter_incoming &= Q(date__day=day)
            date_filter_batches &= Q(date__day=day)
            date_filter_shipments &= Q(sale__date__day=day)
        
        # ========== FINANCES ==========
        # Приход (продажи)
        sales_data = Sale.objects.filter(date_filter_sales).aggregate(
            revenue=Sum(F('quantity') * F('price'), output_field=models.DecimalField()),
            count=Count('id')
        )
        total_revenue = float(sales_data['revenue'] or 0)
        
        # Расход (закупки сырья)
        expenses_data = MaterialIncoming.objects.filter(date_filter_incoming).aggregate(
            expenses=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField())
        )
        total_expenses = float(expenses_data['expenses'] or 0)
        
        # Прибыль
        profit = total_revenue - total_expenses
        
        # Себестоимость проданных товаров
        cost_price_data = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            cost=Sum('cost_price')
        )
        total_cost_price = float(cost_price_data['cost'] or 0)
        
        # ========== SALES ==========
        # ТОП-5 продуктов
        top_products = Sale.objects.filter(date_filter_sales).values('product').annotate(
            quantity=Sum('quantity'),
            total_price=Sum('price')
        ).annotate(
            revenue=F('quantity') * F('total_price')
        ).order_by('-quantity')[:5]
        
        top_products_list = []
        for p in top_products:
            # Пересчитываем revenue правильно
            sales_for_product = Sale.objects.filter(
                date_filter_sales, product=p['product']
            ).aggregate(
                qty=Sum('quantity'),
                rev=Sum(F('quantity') * F('price'), output_field=models.DecimalField())
            )
            top_products_list.append({
                'product_name': p['product'],
                'quantity': float(sales_for_product['qty'] or 0),
                'revenue': float(sales_for_product['rev'] or 0)
            })
        
        # ТОП-5 клиентов
        top_clients_data = Sale.objects.filter(date_filter_sales).values('client__name').annotate(
            quantity=Sum('quantity')
        ).order_by('-quantity')[:5]
        
        top_clients_list = []
        for c in top_clients_data:
            # Пересчитываем revenue правильно
            sales_for_client = Sale.objects.filter(
                date_filter_sales, client__name=c['client__name']
            ).aggregate(
                qty=Sum('quantity'),
                rev=Sum(F('quantity') * F('price'), output_field=models.DecimalField())
            )
            top_clients_list.append({
                'client_name': c['client__name'],
                'quantity': float(sales_for_client['qty'] or 0),
                'revenue': float(sales_for_client['rev'] or 0)
            })
        
        sales_total_qty = Sale.objects.filter(date_filter_sales).aggregate(s=Sum('quantity'))['s'] or 0
        
        # ========== EXPENSES BREAKDOWN ==========
        raw_materials_expenses = float(expenses_data['expenses'] or 0)
        
        # ТОП-5 поставщиков
        top_suppliers_data = MaterialIncoming.objects.filter(date_filter_incoming).exclude(
            supplier=''
        ).values('supplier').annotate(
            quantity_sum=Sum('quantity')
        ).order_by('-quantity_sum')[:5]
        
        top_suppliers_list = []
        for s in top_suppliers_data:
            supplier_total = MaterialIncoming.objects.filter(
                date_filter_incoming, supplier=s['supplier']
            ).aggregate(
                amount=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField())
            )
            top_suppliers_list.append({
                'supplier': s['supplier'],
                'amount': float(supplier_total['amount'] or 0)
            })
        
        # ========== PRODUCTION ==========
        batches_stats = ProductionBatch.objects.filter(date_filter_batches).aggregate(
            total_batches=Count('id'),
            total_quantity=Sum('quantity')
        )
        
        # По продуктам
        production_by_product = ProductionBatch.objects.filter(date_filter_batches).values(
            'product'
        ).annotate(
            batches=Count('id'),
            quantity=Sum('quantity')
        ).order_by('-quantity')[:10]
        
        production_by_product_list = [
            {
                'product_name': p['product'],
                'batches': p['batches'],
                'quantity': float(p['quantity'])
            }
            for p in production_by_product
        ]
        
        # По линиям
        production_by_line = ProductionBatch.objects.filter(date_filter_batches).values(
            'order__line__name'
        ).annotate(
            batches=Count('id'),
            quantity=Sum('quantity')
        ).order_by('-quantity')
        
        production_by_line_list = [
            {
                'line_name': l['order__line__name'],
                'batches': l['batches'],
                'quantity': float(l['quantity'])
            }
            for l in production_by_line
        ]
        
        # ========== WAREHOUSE ==========
        warehouse_stats = WarehouseBatch.objects.aggregate(
            available=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_AVAILABLE)),
            reserved=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_RESERVED)),
            shipped=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_SHIPPED))
        )
        
        # По продуктам
        warehouse_by_product = WarehouseBatch.objects.values('product').annotate(
            available=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_AVAILABLE)),
            reserved=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_RESERVED))
        ).order_by('-available')[:10]
        
        warehouse_by_product_list = [
            {
                'product_name': w['product'],
                'available': float(w['available'] or 0),
                'reserved': float(w['reserved'] or 0)
            }
            for w in warehouse_by_product
        ]
        
        # ========== SHIPMENTS ==========
        shipments_stats = Shipment.objects.filter(date_filter_shipments).aggregate(
            total_count=Count('id'),
            pending=Count('id', filter=Q(status=Shipment.STATUS_PENDING)),
            shipped=Count('id', filter=Q(status=Shipment.STATUS_SHIPPED)),
            delivered=Count('id', filter=Q(status=Shipment.STATUS_DELIVERED))
        )
        
        # ========== STOCK BALANCES ==========
        # Остатки сырья
        from apps.materials.models import MaterialWriteoff
        raw_materials = RawMaterial.objects.all()
        raw_materials_list = []
        for m in raw_materials:
            total_in = MaterialIncoming.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            total_out = MaterialWriteoff.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            balance = float(total_in - total_out)
            if balance > 0:
                raw_materials_list.append({
                    'name': m.name,
                    'balance': balance,
                    'unit': m.unit,
                    'low_stock': balance < 50  # Порог можно настроить
                })
        
        # Остатки химии
        chemistry_stocks = ChemistryStock.objects.filter(quantity__gt=0).select_related('chemistry')
        chemistry_list = [
            {
                'name': c.chemistry.name,
                'balance': float(c.quantity),
                'unit': c.unit,
                'low_stock': float(c.quantity) < 10  # Порог можно настроить
            }
            for c in chemistry_stocks
        ]
        
        # ========== TRENDS ==========
        # Определяем диапазон для трендов (последние 30 дней или период фильтра)
        if day is not None and month is not None:
            # Если выбран день, показываем последние 7 дней
            end_date = datetime(year, month, day).date()
            start_date = end_date - timedelta(days=6)
        elif month is not None:
            # Если выбран месяц, показываем все дни месяца
            from calendar import monthrange
            start_date = datetime(year, month, 1).date()
            last_day = monthrange(year, month)[1]
            end_date = datetime(year, month, last_day).date()
        else:
            # Если только год, показываем последние 30 дней
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=29)
        
        # Продажи по дням
        daily_revenue_data = Sale.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).values('date').annotate(
            quantity_sum=Sum('quantity')
        ).order_by('date')
        
        daily_revenue_list = []
        for d in daily_revenue_data:
            day_revenue = Sale.objects.filter(date=d['date']).aggregate(
                revenue=Sum(F('quantity') * F('price'), output_field=models.DecimalField())
            )
            daily_revenue_list.append({
                'date': d['date'].strftime('%Y-%m-%d'),
                'revenue': float(day_revenue['revenue'] or 0)
            })
        
        # Производство по дням
        daily_production = ProductionBatch.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).values('date').annotate(
            quantity=Sum('quantity')
        ).order_by('date')
        
        daily_production_list = [
            {
                'date': d['date'].strftime('%Y-%m-%d'),
                'quantity': float(d['quantity'])
            }
            for d in daily_production
        ]
        
        # Расходы по дням (закупки сырья)
        daily_expenses_data = MaterialIncoming.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).values('date').annotate(
            quantity_sum=Sum('quantity')
        ).order_by('date')
        
        daily_expenses_list = []
        for d in daily_expenses_data:
            day_expense = MaterialIncoming.objects.filter(date=d['date']).aggregate(
                expense=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField())
            )
            daily_expenses_list.append({
                'date': d['date'].strftime('%Y-%m-%d'),
                'expense': float(day_expense['expense'] or 0)
            })
        
        # ========== RESPONSE ==========
        return Response({
            'period': period,
            'finances': {
                'revenue': total_revenue,
                'expenses': total_expenses,
                'profit': profit,
                'cost_price': total_cost_price,
            },
            'sales': {
                'total_count': sales_data['count'],
                'total_quantity': float(sales_total_qty),
                'total_revenue': total_revenue,
                'top_products': top_products_list,
                'top_clients': top_clients_list,
            },
            'expenses_breakdown': {
                'raw_materials': raw_materials_expenses,
                'chemistry': 0,  # Химия = переработка, не прямые расходы
                'by_supplier': top_suppliers_list,
            },
            'production': {
                'total_batches': batches_stats['total_batches'],
                'total_quantity': float(batches_stats['total_quantity'] or 0),
                'by_product': production_by_product_list,
                'by_line': production_by_line_list,
            },
            'warehouse': {
                'total_available': float(warehouse_stats['available'] or 0),
                'total_reserved': float(warehouse_stats['reserved'] or 0),
                'total_shipped': float(warehouse_stats['shipped'] or 0),
                'by_product': warehouse_by_product_list,
            },
            'shipments': {
                'total_count': shipments_stats['total_count'],
                'pending': shipments_stats['pending'],
                'shipped': shipments_stats['shipped'],
                'delivered': shipments_stats['delivered'],
            },
            'stock_balances': {
                'raw_materials': raw_materials_list,
                'chemistry': chemistry_list,
            },
            'trends': {
                'daily_revenue': daily_revenue_list,
                'daily_expenses': daily_expenses_list,
                'daily_production': daily_production_list,
            },
        })


class AnalyticsRevenueDetailsView(viewsets.ViewSet):
    """Детализация приходов (продажи)"""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        # Фильтры по периоду (с валидацией)
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        day = request.query_params.get('day')
        try:
            year = int(year) if year else datetime.now().year
        except (ValueError, TypeError):
            year = datetime.now().year
        try:
            month = int(month) if month else None
        except (ValueError, TypeError):
            month = None
        try:
            day = int(day) if day else None
        except (ValueError, TypeError):
            day = None
        
        date_filter = Q(date__year=year)
        if month is not None:
            date_filter &= Q(date__month=month)
        if day is not None:
            date_filter &= Q(date__day=day)
        
        # Получаем все продажи за период
        sales = Sale.objects.filter(date_filter).select_related('client').order_by('-date', '-id')
        
        items = []
        total = Decimal('0')
        
        for sale in sales:
            sale_total = (sale.quantity or 0) * (sale.price or 0)
            total += sale_total
            
            items.append({
                'date': sale.date.strftime('%Y-%m-%d'),
                'client_name': sale.client.name,
                'product_name': sale.product,
                'quantity': float(sale.quantity),
                'price_per_unit': float(sale.price or 0),
                'total': float(sale_total),
                'sale_id': sale.id,
            })
        
        return Response({
            'total': float(total),
            'items': items,
        })


class AnalyticsExpenseDetailsView(viewsets.ViewSet):
    """Детализация расходов (закупки сырья)"""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'analytics'

    def list(self, request):
        # Фильтры по периоду (с валидацией)
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        day = request.query_params.get('day')
        try:
            year = int(year) if year else datetime.now().year
        except (ValueError, TypeError):
            year = datetime.now().year
        try:
            month = int(month) if month else None
        except (ValueError, TypeError):
            month = None
        try:
            day = int(day) if day else None
        except (ValueError, TypeError):
            day = None
        
        date_filter = Q(date__year=year)
        if month is not None:
            date_filter &= Q(date__month=month)
        if day is not None:
            date_filter &= Q(date__day=day)
        
        # Получаем все закупки за период
        incomings = MaterialIncoming.objects.filter(date_filter).select_related('material').order_by('-date', '-id')
        
        items = []
        total = Decimal('0')
        
        for incoming in incomings:
            incoming_total = (incoming.quantity or 0) * (incoming.price_per_unit or 0)
            total += incoming_total
            
            items.append({
                'date': incoming.date.strftime('%Y-%m-%d'),
                'material_name': incoming.material.name,
                'supplier': incoming.supplier or '',
                'quantity': float(incoming.quantity),
                'unit': incoming.unit,
                'price_per_unit': float(incoming.price_per_unit or 0),
                'total': float(incoming_total),
                'incoming_id': incoming.id,
            })
        
        return Response({
            'total': float(total),
            'items': items,
        })
