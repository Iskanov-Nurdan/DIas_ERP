"""
Сводка и детализация аналитики: выручка/себестоимость продаж, себестоимость производства, ОТК, склад.
Денежный приход, закупки, production cost и sales cost не смешиваются в одном KPI.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Case, Count, DecimalField, F, Q, Sum, When
from django.db.models.functions import TruncDate, TruncMonth

from config.api_numbers import api_decimal_str

from apps.materials.models import MaterialBatch
from apps.production.models import ProductionBatch
from apps.sales.models import Sale
from apps.otk.models import OtkCheck
from apps.warehouse.models import WarehouseBatch

from .services import (
    AnalyticsScope,
    production_batch_scope_q,
    sale_scope_q,
    warehouse_batches_scope_qs,
)


def _d(v) -> Decimal:
    if v is None:
        return Decimal('0')
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _sold_units_expr():
    """Канонично: при заполненных sold_pieces > 0 — они, иначе quantity (legacy)."""
    return Case(
        When(sold_pieces__gt=0, then=F('sold_pieces')),
        default=F('quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=4),
    )


def trend_bounds(scope: AnalyticsScope) -> tuple[date, date]:
    p = scope.period
    if scope.date_from or scope.date_to:
        start = scope.date_from or scope.date_to or date.today()
        end = scope.date_to or scope.date_from or start
        if start > end:
            start, end = end, start
        return start, end
    if p.day is not None and p.month is not None:
        d = date(p.year, p.month, p.day)
        return d, d
    if p.month is not None:
        last = monthrange(p.year, p.month)[1]
        return date(p.year, p.month, 1), date(p.year, p.month, last)
    return date(p.year, 1, 1), date(p.year, 12, 31)


def default_trend_group(scope: AnalyticsScope) -> str:
    a, b = trend_bounds(scope)
    if (b - a).days > 62:
        return 'month'
    return 'day'


def _trend_bucket_canonical(bucket: Any, tg: str) -> date:
    """SQLite: TruncDate/TruncMonth по DateField и DateTimeField дают date vs datetime — нельзя смешивать в sort/merge."""
    if bucket is None:
        return date.min
    if isinstance(bucket, datetime):
        d = bucket.date()
    elif isinstance(bucket, date):
        d = bucket
    else:
        return date.min
    if tg == 'month':
        return date(d.year, d.month, 1)
    return d


def _profit_dec(a: Decimal, b: Decimal) -> Decimal:
    return (a - b).quantize(Decimal('0.01'))


def build_analytics_summary(scope: AnalyticsScope, trend_group: Optional[str] = None) -> dict[str, Any]:
    sq = sale_scope_q(scope)
    bq = production_batch_scope_q(scope)
    units_sum = _sold_units_expr()

    sales_agg = Sale.objects.filter(sq).aggregate(
        revenue=Sum('revenue'),
        cost=Sum('cost'),
        count=Count('id'),
        qty=Sum(units_sum),
    )
    revenue_total = _d(sales_agg['revenue'])
    sales_cost_total = _d(sales_agg['cost'])
    sales_count = int(sales_agg['count'] or 0)
    sold_units_total = _d(sales_agg['qty'])
    profit_total = _profit_dec(revenue_total, sales_cost_total)

    prod_agg = ProductionBatch.objects.filter(bq).aggregate(
        cost=Sum('material_cost_total'),
        pieces=Sum('pieces'),
        meters=Sum('total_meters'),
        batches=Count('id'),
    )
    production_cost_total = _d(prod_agg['cost'])
    produced_units_total = _d(prod_agg['pieces'])
    produced_meters_total = _d(prod_agg['meters'])
    batches_count = int(prod_agg['batches'] or 0)

    pb_in_period = ProductionBatch.objects.filter(bq)
    otk_agg = OtkCheck.objects.filter(batch__in=pb_in_period).aggregate(
        acc=Sum('accepted'),
        rej=Sum('rejected'),
    )
    otk_accepted = _d(otk_agg['acc'])
    otk_defect = _d(otk_agg['rej'])
    otk_sum = otk_accepted + otk_defect
    otk_pct = (
        (otk_defect / otk_sum * Decimal('100')).quantize(Decimal('0.0001'))
        if otk_sum > 0
        else Decimal('0')
    )

    wh_qs = warehouse_batches_scope_qs(scope)
    wh_agg = wh_qs.aggregate(
        av=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_AVAILABLE)),
        rs=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_RESERVED)),
        sh=Sum('quantity', filter=Q(status=WarehouseBatch.STATUS_SHIPPED)),
        good=Sum('quantity', filter=Q(quality=WarehouseBatch.QUALITY_GOOD)),
        defect=Sum('quantity', filter=Q(quality=WarehouseBatch.QUALITY_DEFECT)),
    )

    purchase_agg = MaterialBatch.objects.filter(scope.incoming_date_q()).aggregate(
        pt=Sum('total_price'),
    )
    purchase_total = _d(purchase_agg['pt'])

    tg = (trend_group or default_trend_group(scope)).strip().lower()
    if tg not in ('day', 'month'):
        tg = default_trend_group(scope)

    trends_list = _build_trends(scope, sq, bq, tg)
    sales_by_profile = _build_sales_by_profile(sq)
    sales_by_client = _build_sales_by_client(sq)
    production_by_line = _build_production_by_line(bq)

    cards = {
        'revenue_total': api_decimal_str(revenue_total),
        'sales_cost_total': api_decimal_str(sales_cost_total),
        'profit_total': api_decimal_str(profit_total),
        'production_cost_total': api_decimal_str(production_cost_total),
        'purchase_total': api_decimal_str(purchase_total),
        'sales_count': sales_count,
        'sold_units_total': api_decimal_str(sold_units_total),
        'produced_units_total': api_decimal_str(produced_units_total),
        'produced_meters_total': api_decimal_str(produced_meters_total),
        'otk_accepted_total': api_decimal_str(otk_accepted),
        'otk_defect_total': api_decimal_str(otk_defect),
        'otk_defect_percent': api_decimal_str(otk_pct),
        'warehouse_available_total': api_decimal_str(_d(wh_agg['av'])),
        'warehouse_reserved_total': api_decimal_str(_d(wh_agg['rs'])),
        'warehouse_shipped_total': api_decimal_str(_d(wh_agg['sh'])),
    }

    return {
        'period': scope.as_period_dict(),
        'trend_group': tg,
        'cards': cards,
        'otk_summary': {
            'accepted': api_decimal_str(otk_accepted),
            'defect': api_decimal_str(otk_defect),
            'defect_percent': api_decimal_str(otk_pct),
        },
        'warehouse_summary': {
            'available': api_decimal_str(_d(wh_agg['av'])),
            'reserved': api_decimal_str(_d(wh_agg['rs'])),
            'shipped': api_decimal_str(_d(wh_agg['sh'])),
            'good': api_decimal_str(_d(wh_agg['good'])),
            'defect': api_decimal_str(_d(wh_agg['defect'])),
        },
        'production_summary': {'batches_count': batches_count},
        'trends': trends_list,
        'sales_by_profile': sales_by_profile,
        'sales_by_client': sales_by_client,
        'production_by_line': production_by_line,
    }


def _build_trends(scope: AnalyticsScope, sq: Q, bq: Q, tg: str) -> list[dict[str, Any]]:
    start, end = trend_bounds(scope)
    trunc = TruncMonth('date') if tg == 'month' else TruncDate('date')
    mb_trunc = TruncMonth('received_at') if tg == 'month' else TruncDate('received_at')

    sale_rows = (
        Sale.objects.filter(sq, date__gte=start, date__lte=end)
        .annotate(bucket=trunc)
        .values('bucket')
        .annotate(revenue=Sum('revenue'), sales_cost=Sum('cost'))
        .order_by('bucket')
    )
    pb_rows = (
        ProductionBatch.objects.filter(bq, date__gte=start, date__lte=end)
        .annotate(bucket=trunc)
        .values('bucket')
        .annotate(production_cost=Sum('material_cost_total'))
        .order_by('bucket')
    )
    mb_q = scope.incoming_date_q() & Q(received_at__date__gte=start, received_at__date__lte=end)
    mb_rows = (
        MaterialBatch.objects.filter(mb_q)
        .annotate(bucket=mb_trunc)
        .values('bucket')
        .annotate(purchase_total=Sum('total_price'))
        .order_by('bucket')
    )

    buckets: dict[date, dict[str, Decimal]] = {}
    for r in sale_rows:
        k = r['bucket']
        if k is None:
            continue
        ck = _trend_bucket_canonical(k, tg)
        buckets.setdefault(ck, {})['revenue'] = _d(r.get('revenue'))
        buckets.setdefault(ck, {})['sales_cost'] = _d(r.get('sales_cost'))

    for r in pb_rows:
        k = r['bucket']
        if k is None:
            continue
        ck = _trend_bucket_canonical(k, tg)
        buckets.setdefault(ck, {})['production_cost'] = _d(r.get('production_cost'))

    for r in mb_rows:
        k = r['bucket']
        if k is None:
            continue
        ck = _trend_bucket_canonical(k, tg)
        buckets.setdefault(ck, {})['purchase_total'] = _d(r.get('purchase_total'))

    out: list[dict[str, Any]] = []
    for bucket in sorted(buckets.keys()):
        d = buckets[bucket]
        rev = d.get('revenue', Decimal('0'))
        sc = d.get('sales_cost', Decimal('0'))
        pc = d.get('production_cost', Decimal('0'))
        pt = d.get('purchase_total', Decimal('0'))
        if tg == 'month':
            period_key = f'{bucket.year}-{bucket.month:02d}'
        else:
            period_key = bucket.isoformat()
        out.append(
            {
                'period': period_key,
                'revenue': api_decimal_str(rev),
                'sales_cost': api_decimal_str(sc),
                'profit': api_decimal_str(_profit_dec(rev, sc)),
                'production_cost': api_decimal_str(pc),
                'purchase_total': api_decimal_str(pt),
            }
        )
    return out


def _build_sales_by_profile(sq: Q) -> list[dict[str, Any]]:
    units_sum = _sold_units_expr()
    rows = (
        Sale.objects.filter(sq)
        .filter(warehouse_batch__profile_id__isnull=False)
        .values('warehouse_batch__profile_id', 'warehouse_batch__profile__name')
        .annotate(
            sold_units=Sum(units_sum),
            revenue=Sum('revenue'),
            cost=Sum('cost'),
            sales_count=Count('id'),
        )
        .order_by('-revenue')[:50]
    )
    out = []
    for r in rows:
        rev = _d(r.get('revenue'))
        cst = _d(r.get('cost'))
        out.append(
            {
                'profile_id': r['warehouse_batch__profile_id'],
                'profile_name': (r.get('warehouse_batch__profile__name') or '').strip() or '—',
                'sales_count': int(r.get('sales_count') or 0),
                'sold_units': api_decimal_str(_d(r.get('sold_units'))),
                'revenue': api_decimal_str(rev),
                'profit': api_decimal_str(_profit_dec(rev, cst)),
            }
        )
    return out


def _build_sales_by_client(sq: Q) -> list[dict[str, Any]]:
    units_sum = _sold_units_expr()
    rows = (
        Sale.objects.filter(sq)
        .filter(client_id__isnull=False)
        .values('client_id', 'client__name')
        .annotate(
            sold_units=Sum(units_sum),
            revenue=Sum('revenue'),
            cost=Sum('cost'),
            sales_count=Count('id'),
        )
        .order_by('-revenue')[:50]
    )
    out = []
    for r in rows:
        rev = _d(r.get('revenue'))
        cst = _d(r.get('cost'))
        out.append(
            {
                'client_id': r['client_id'],
                'client_name': (r.get('client__name') or '').strip() or '—',
                'sales_count': int(r.get('sales_count') or 0),
                'sold_units': api_decimal_str(_d(r.get('sold_units'))),
                'revenue': api_decimal_str(rev),
                'profit': api_decimal_str(_profit_dec(rev, cst)),
            }
        )
    return out


def _build_production_by_line(bq: Q) -> list[dict[str, Any]]:
    rows = (
        ProductionBatch.objects.filter(bq)
        .values('line_id', 'line__name')
        .annotate(
            produced_units=Sum('pieces'),
            produced_meters=Sum('total_meters'),
            production_cost=Sum('material_cost_total'),
            batches=Count('id'),
        )
        .order_by('-produced_meters')
    )
    out = []
    for r in rows:
        if not (r.get('batches') or 0):
            continue
        lid = r.get('line_id')
        out.append(
            {
                'line_id': lid,
                'line_name': (r.get('line__name') or '').strip() or ('—' if lid is None else f'Линия #{lid}'),
                'produced_units': api_decimal_str(_d(r.get('produced_units'))),
                'produced_meters': api_decimal_str(_d(r.get('produced_meters'))),
                'production_cost': api_decimal_str(_d(r.get('production_cost'))),
                'batches': int(r.get('batches') or 0),
            }
        )
    return out


def _unit_cost_line(qty: Decimal, total: Decimal) -> Optional[str]:
    if qty <= 0:
        return None
    return api_decimal_str((total / qty).quantize(Decimal('0.0001')))


def build_sales_cost_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Себестоимость проданного (Sale.cost) — отдельно от производства и закупок."""
    sq = sale_scope_q(scope)
    items: list[dict[str, Any]] = []
    total = Decimal('0')
    for s in (
        Sale.objects.filter(sq)
        .select_related('client', 'warehouse_batch', 'warehouse_batch__profile')
        .order_by('-date', '-id')
    ):
        cst = _d(s.cost)
        qty = _d(s.sold_pieces if (s.sold_pieces and s.sold_pieces > 0) else s.quantity)
        total += cst
        profile_name = ''
        wb = s.warehouse_batch
        if wb and wb.profile_id and wb.profile:
            profile_name = (wb.profile.name or '').strip()
        items.append(
            {
                'date': s.date.isoformat(),
                'sale_id': s.id,
                'order_number': s.order_number,
                'product_name': (s.product or '').strip(),
                'profile_name': profile_name,
                'quantity': api_decimal_str(qty),
                'cost_per_unit': _unit_cost_line(qty, cst),
                'total_cost': api_decimal_str(cst),
            }
        )
    return {
        'period': scope.as_period_dict(),
        'total_sales_cost': api_decimal_str(total),
        'items': items,
    }


def build_production_cost_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Себестоимость производства (ProductionBatch.material_cost_total)."""
    bq = production_batch_scope_q(scope)
    items: list[dict[str, Any]] = []
    total = Decimal('0')
    for pb in (
        ProductionBatch.objects.filter(bq)
        .select_related('line', 'profile')
        .order_by('-date', '-id')
    ):
        cost = _d(pb.material_cost_total)
        total += cost
        pcs = _d(Decimal(str(pb.pieces or 0)))
        meters = _d(pb.total_meters)
        items.append(
            {
                'date': pb.date.isoformat(),
                'production_batch_id': pb.id,
                'profile_name': ((pb.profile.name or '').strip()) if pb.profile_id and pb.profile else '',
                'line_name': ((pb.line.name or '').strip()) if pb.line_id and pb.line else '',
                'quantity_pieces': api_decimal_str(pcs),
                'total_meters': api_decimal_str(meters),
                'material_cost_total': api_decimal_str(cost),
            }
        )
    return {
        'period': scope.as_period_dict(),
        'total_production_cost': api_decimal_str(total),
        'items': items,
    }


def build_purchase_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Закупка сырья (партии прихода), не смешивается с выручкой и sales cost."""
    iq = scope.incoming_date_q()
    items: list[dict[str, Any]] = []
    total = Decimal('0')
    for inc in (
        MaterialBatch.objects.filter(iq)
        .select_related('material')
        .order_by('-received_at', '-id')
    ):
        tot = _d(inc.total_price)
        total += tot
        qi = _d(inc.quantity_initial)
        up = inc.unit_price
        items.append(
            {
                'date': inc.received_at.date().isoformat() if inc.received_at else None,
                'material_name': inc.material.name,
                'supplier_name': (inc.supplier_name or '').strip(),
                'quantity': api_decimal_str(qi),
                'unit_price': api_decimal_str(_d(up)) if up is not None else None,
                'total_amount': api_decimal_str(tot),
            }
        )
    return {
        'period': scope.as_period_dict(),
        'total_purchase_amount': api_decimal_str(total),
        'items': items,
    }


def build_profit_details(scope: AnalyticsScope) -> dict[str, Any]:
    sq = sale_scope_q(scope)
    items: list[dict[str, Any]] = []
    revenue_t = Decimal('0')
    cost_t = Decimal('0')
    profit_t = Decimal('0')
    for s in (
        Sale.objects.filter(sq)
        .select_related('client', 'warehouse_batch', 'warehouse_batch__profile')
        .order_by('-date', '-id')
    ):
        rev = _d(s.revenue)
        cst = _d(s.cost)
        prf = _d(s.profit)
        revenue_t += rev
        cost_t += cst
        profit_t += prf
        wb = s.warehouse_batch
        profile_name = ''
        if wb and wb.profile_id:
            profile_name = ((wb.profile.name or '').strip()) if wb.profile else ''
        prod = (s.product or '').strip()
        obj = ' — '.join(x for x in (s.order_number, prod) if x) or f'Продажа #{s.id}'
        items.append(
            {
                'date': s.date.isoformat(),
                'sale_id': s.id,
                'order_number': s.order_number,
                'object': obj,
                'revenue': api_decimal_str(rev),
                'sales_cost': api_decimal_str(cst),
                'profit': api_decimal_str(prf),
            }
        )
    return {
        'period': scope.as_period_dict(),
        'totals': {
            'revenue': api_decimal_str(revenue_t),
            'sales_cost': api_decimal_str(cost_t),
            'profit': api_decimal_str(profit_t),
        },
        'items': items,
    }


def build_otk_details(scope: AnalyticsScope) -> dict[str, Any]:
    bq = production_batch_scope_q(scope)
    batch_ids = ProductionBatch.objects.filter(bq).values_list('id', flat=True)
    qs = (
        OtkCheck.objects.filter(batch_id__in=batch_ids)
        .select_related('batch', 'profile')
        .order_by('-checked_date', '-id')
    )
    items = []
    for oc in qs:
        acc = _d(oc.accepted)
        rej = _d(oc.rejected)
        sm = acc + rej
        pct = (rej / sm * Decimal('100')).quantize(Decimal('0.0001')) if sm > 0 else Decimal('0')
        items.append(
            {
                'id': oc.id,
                'date': oc.checked_date.date().isoformat() if oc.checked_date else None,
                'batch_id': oc.batch_id,
                'profile_id': oc.profile_id,
                'profile_name': ((oc.profile.name or '').strip()) if oc.profile else '',
                'accepted': api_decimal_str(acc),
                'defect': api_decimal_str(rej),
                'defect_percent': api_decimal_str(pct),
                'check_status': oc.check_status,
            }
        )
    return {'period': scope.as_period_dict(), 'items': items}


def build_revenue_details_items(scope: AnalyticsScope) -> dict[str, Any]:
    sq = sale_scope_q(scope)
    items: list[dict[str, Any]] = []
    total = Decimal('0')
    for sale in (
        Sale.objects.filter(sq)
        .select_related('client', 'warehouse_batch', 'warehouse_batch__profile')
        .order_by('-date', '-id')
    ):
        sale_total = sale.revenue or Decimal('0')
        total += sale_total
        wb = sale.warehouse_batch
        profile_name = ''
        if wb and wb.profile_id and wb.profile:
            profile_name = (wb.profile.name or '').strip()
        items.append(
            {
                'date': sale.date.strftime('%Y-%m-%d'),
                'client_name': sale.client.name if sale.client_id else '',
                'profile_name': profile_name,
                'product_name': sale.product or '',
                'quantity': api_decimal_str(
                    sale.sold_pieces
                    if sale.sold_pieces and sale.sold_pieces > 0
                    else sale.quantity
                ),
                'price_per_unit': api_decimal_str(sale.price or Decimal('0')),
                'revenue': api_decimal_str(sale.revenue or Decimal('0')),
            }
        )
    return {'period': scope.as_period_dict(), 'total': api_decimal_str(total), 'items': items}
