"""
Сводка и детализация аналитики: выручка/себестоимость продаж, себестоимость производства, ОТК, склад.
Денежный приход, закупки, production cost и sales cost не смешиваются в одном KPI.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Case, Count, DecimalField, F, Q, Sum, When

from config.api_numbers import api_decimal_str

from apps.materials.models import MaterialBatch
from apps.production.models import ProductionBatch
from apps.recipes.models import PlasticProfile
from apps.sales.models import Sale
from apps.sales.payment_status import sale_payment_metrics
from apps.otk.models import OtkCheck
from apps.warehouse.models import GpPackOperation, GpPackRunAllocation, GpPackUnit, WarehouseBatch

from .services import (
    AnalyticsScope,
    production_batch_scope_q,
    sale_scope_q,
    warehouse_batches_scope_qs,
)
from .sale_pnl import aggregate_sale_pnl, iter_sale_pnl_rows, profile_extra_breakdown

# Непогашенный долг: продажи в фильтре периода (дата Sale.date), остаток — по активным платежам на момент запроса.
DEBT_AS_OF_SEMANTICS = 'current_outstanding_by_sale_date_in_period'

ANALYTICS_DETAIL_ITEMS_LIMIT = 500


def _analytics_sale_q(scope: AnalyticsScope) -> Q:
    """Продажи для KPI: период/клиент; draft без списания склада не учитываются."""
    q = sale_scope_q(scope)
    q &= ~Q(sale_status=Sale.STATUS_CANCELED)
    q &= ~Q(sale_status=Sale.STATUS_DRAFT, warehouse_stock_applied=False)
    return q


def _sales_by_profile_limit() -> int:
    return 15


def _trend_metric_num(d: Decimal) -> float:
    return float(d.quantize(Decimal('0.01')))


def _warehouse_qty_num(d: Decimal) -> float:
    return float(_d(d).quantize(Decimal('0.0001')))


def _gp_packaging_date_q(scope: AnalyticsScope) -> Q:
    start, end = trend_bounds(scope)
    return Q(created_at__date__gte=start, created_at__date__lte=end)


def _build_packaging_summary(scope: AnalyticsScope) -> Optional[dict[str, Any]]:
    """Упаковка ГП за период (операции GP по дате создания)."""
    pq = _gp_packaging_date_q(scope)
    op_ids = list(GpPackOperation.objects.filter(pq).values_list('id', flat=True))
    if not op_ids:
        return None
    pieces = GpPackOperation.objects.filter(pk__in=op_ids).aggregate(s=Sum('total_pieces'))['s'] or 0
    pieces_i = int(pieces)
    pkgs = GpPackUnit.objects.filter(operation_id__in=op_ids).count()
    kg_raw = GpPackRunAllocation.objects.filter(operation_id__in=op_ids).aggregate(s=Sum('kg'))['s']
    kg = _d(kg_raw)
    if pieces_i <= 0 and pkgs <= 0 and kg <= 0:
        return None
    w = float(kg.quantize(Decimal('0.001'))) if kg else 0.0
    return {
        'pieces_total': pieces_i,
        'packages_count': pkgs,
        'weight_kg_total': w,
        'pieces': pieces_i,
        'packages': pkgs,
        'weight_kg': w,
    }


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


def _profit_dec(a: Decimal, b: Decimal) -> Decimal:
    return (a - b).quantize(Decimal('0.01'))


def build_analytics_summary(scope: AnalyticsScope, trend_group: Optional[str] = None) -> dict[str, Any]:
    sq = _analytics_sale_q(scope)
    bq = production_batch_scope_q(scope)
    units_sum = _sold_units_expr()

    sales_agg = Sale.objects.filter(sq).distinct().aggregate(
        revenue=Sum('revenue'),
        count=Count('id'),
        qty=Sum(units_sum),
    )
    revenue_total = _d(sales_agg['revenue'])
    pnl = aggregate_sale_pnl(scope)
    sales_cost_total = pnl['sales_cost_total']
    product_other_expenses_total = pnl['product_other_expenses_total']
    sold_goods_cost_total = (sales_cost_total + product_other_expenses_total).quantize(Decimal('0.01'))
    sales_count = int(sales_agg['count'] or 0)
    sold_units_total = _d(sales_agg['qty'])
    client_debt_total = _compute_client_debt_total(scope)

    prod_agg = ProductionBatch.objects.filter(bq).aggregate(cost=Sum('material_cost_total'))
    production_cost_total = _d(prod_agg['cost'])
    purchase_agg = MaterialBatch.objects.filter(scope.incoming_date_q()).aggregate(pt=Sum('total_price'))
    purchase_total = _d(purchase_agg['pt'])
    from .other_expenses import sum_accepted_other_expenses

    other_expenses_total = sum_accepted_other_expenses(scope)
    period_expenses_total = (
        purchase_total + other_expenses_total + sales_cost_total + product_other_expenses_total
    ).quantize(Decimal('0.01'))
    profit_total = _profit_dec(revenue_total, period_expenses_total)
    expense_total = (purchase_total + production_cost_total + sales_cost_total).quantize(Decimal('0.01'))

    pb_in_period = ProductionBatch.objects.filter(bq)
    otk_agg = OtkCheck.objects.filter(batch__in=pb_in_period).aggregate(
        acc=Sum('accepted'),
        rej=Sum('rejected'),
    )
    otk_accepted = _d(otk_agg['acc'])
    otk_defect = _d(otk_agg['rej'])
    start_otk, end_otk = trend_bounds(scope)
    from apps.workshop.models import BlankProductionRun
    from apps.sales.models import DefectRecord

    ws_q = Q(
        status=BlankProductionRun.STATUS_GP_ACCEPTED,
        gp_accepted_at__date__gte=start_otk,
        gp_accepted_at__date__lte=end_otk,
    )
    if scope.profile_id:
        ws_q &= Q(product_id=scope.profile_id)
    ws_agg = BlankProductionRun.objects.filter(ws_q).aggregate(acc=Sum('gp_accepted_pieces'))
    otk_accepted += _d(ws_agg['acc'])

    # Брак с приёмки ГП: defect_kg / weight_kg_per_piece (период по otk_recorded_at, иначе gp_accepted_at).
    blank_defect_q = Q(otk_recorded_at__date__gte=start_otk, otk_recorded_at__date__lte=end_otk) | Q(
        otk_recorded_at__isnull=True,
        gp_accepted_at__date__gte=start_otk,
        gp_accepted_at__date__lte=end_otk,
    )
    if scope.profile_id:
        blank_defect_q &= Q(product_id=scope.profile_id)
    blank_defect_rows = BlankProductionRun.objects.filter(blank_defect_q).values_list(
        'defect_kg', 'weight_kg_per_piece',
    )
    blank_defect_pieces = Decimal('0')
    for dkg, wpp in blank_defect_rows:
        d_dkg = _d(dkg)
        d_wpp = _d(wpp)
        if d_dkg > 0 and d_wpp > 0:
            blank_defect_pieces += (d_dkg / d_wpp).quantize(Decimal('0.0001'))
    otk_defect += blank_defect_pieces

    # Брак из вкладки «Брак» (DefectRecord), период по created_at.
    dr_q = Q(created_at__date__gte=start_otk, created_at__date__lte=end_otk)
    if scope.profile_id:
        dr_q &= Q(profile_id=scope.profile_id)
    dr_agg = DefectRecord.objects.filter(dr_q).aggregate(s=Sum('original_quantity_pcs'))
    otk_defect += _d(dr_agg['s'])

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
    )
    av_num = _warehouse_qty_num(_d(wh_agg['av']))
    rs_num = _warehouse_qty_num(_d(wh_agg['rs']))

    tg = (trend_group or default_trend_group(scope)).strip().lower()
    if tg not in ('day', 'month'):
        tg = default_trend_group(scope)

    trends_list = _build_trends(scope, sq, tg)
    sales_by_profile = _build_sales_by_profile(sq)
    packaging_summary = _build_packaging_summary(scope)

    pt_str = api_decimal_str(purchase_total)
    oe_str = api_decimal_str(other_expenses_total)
    sc_str = api_decimal_str(sales_cost_total)
    poe_str = api_decimal_str(product_other_expenses_total)
    sgc_str = api_decimal_str(sold_goods_cost_total)
    pc_str = api_decimal_str(production_cost_total)
    pe_str = api_decimal_str(period_expenses_total)
    cards = {
        'revenue_total': api_decimal_str(revenue_total),
        'sales_cost_total': sc_str,
        'product_other_expenses_total': poe_str,
        'sold_goods_cost_total': sgc_str,
        'profit_total': api_decimal_str(profit_total),
        'purchase_total': pt_str,
        'purchases_total': pt_str,
        'material_purchase_total': pt_str,
        'total_purchase_amount': pt_str,
        'raw_purchase_total': pt_str,
        'other_expenses_total': oe_str,
        'accepted_other_expenses_total': oe_str,
        'misc_expenses_total': oe_str,
        'production_cost_total': pc_str,
        'material_cost_production': pc_str,
        'production_total_cost': pc_str,
        'period_expenses_total': pe_str,
        'operating_expenses_total': pe_str,
        'expense_total': api_decimal_str(expense_total),
        'sales_count': sales_count,
        'sold_units_total': api_decimal_str(sold_units_total),
        'client_debt_total': api_decimal_str(client_debt_total),
    }

    otk_rate = (otk_defect / otk_sum) if otk_sum > 0 else Decimal('0')
    otk_pct_str = api_decimal_str(otk_pct)
    out: dict[str, Any] = {
        'period': scope.as_period_dict(),
        'trend_group': tg,
        'debt_as_of': DEBT_AS_OF_SEMANTICS,
        'cards': cards,
        'otk_summary': {
            'accepted': api_decimal_str(otk_accepted),
            'defect': api_decimal_str(otk_defect),
            'defect_percent': otk_pct_str,
            'defect_rate_pct': otk_pct_str,
            'defect_rate': float(otk_rate.quantize(Decimal('0.000001'))),
            'accepted_total': api_decimal_str(otk_accepted),
            'defect_total': api_decimal_str(otk_defect),
            'rejected': api_decimal_str(otk_defect),
        },
        'warehouse_summary': {
            'available': av_num,
            'reserved': rs_num,
        },
        'trends': trends_list,
        'sales_by_profile': sales_by_profile,
    }
    if packaging_summary is not None:
        out['packaging_summary'] = packaging_summary
    return out


def _trend_bucket_key(d: date, tg: str) -> date:
    return date(d.year, d.month, 1) if tg == 'month' else d


def _empty_trend_slot() -> dict[str, Decimal]:
    return {
        'revenue': Decimal('0'),
        'purchase_total': Decimal('0'),
        'other_expenses_total': Decimal('0'),
        'sales_cost_total': Decimal('0'),
        'product_other_expenses_total': Decimal('0'),
    }


def _build_trends(scope: AnalyticsScope, sq: Q, tg: str) -> list[dict[str, Any]]:
    """
    Динамика P&L: revenue (Sale.date), purchase_total (приход сырья), other_expenses_total (accepted).
    Маржу считает фронт: revenue − period_expenses_total.
    """
    from .models import AnalyticsOtherExpense

    start, end = trend_bounds(scope)
    iq = scope.incoming_date_q()

    buckets: dict[date, dict[str, Decimal]] = {}

    for r in Sale.objects.filter(sq, date__gte=start, date__lte=end).values('date', 'revenue'):
        d = r.get('date')
        if d is None:
            continue
        ck = _trend_bucket_key(d, tg)
        slot = buckets.setdefault(ck, _empty_trend_slot())
        slot['revenue'] += _d(r.get('revenue'))

    scope_in_range = AnalyticsScope(
        period=scope.period,
        date_from=start,
        date_to=end,
        line_id=scope.line_id,
        client_id=scope.client_id,
        profile_id=scope.profile_id,
        recipe_id=scope.recipe_id,
        batch_id=scope.batch_id,
        otk_status=scope.otk_status,
    )
    for row in iter_sale_pnl_rows(scope_in_range):
        ck = _trend_bucket_key(row.sale_date, tg)
        slot = buckets.setdefault(ck, _empty_trend_slot())
        slot['sales_cost_total'] += row.material_cost
        slot['product_other_expenses_total'] += row.product_other_cost

    for r in (
        MaterialBatch.objects.filter(iq, received_at__date__gte=start, received_at__date__lte=end)
        .values('received_at', 'total_price')
    ):
        ra = r.get('received_at')
        if ra is None:
            continue
        d = ra.date() if hasattr(ra, 'date') else ra
        ck = _trend_bucket_key(d, tg)
        slot = buckets.setdefault(ck, _empty_trend_slot())
        slot['purchase_total'] += _d(r.get('total_price'))

    for r in (
        AnalyticsOtherExpense.objects.filter(
            status=AnalyticsOtherExpense.STATUS_ACCEPTED,
            date__gte=start,
            date__lte=end,
        ).values('date', 'amount')
    ):
        d = r.get('date')
        if d is None:
            continue
        ck = _trend_bucket_key(d, tg)
        slot = buckets.setdefault(ck, _empty_trend_slot())
        slot['other_expenses_total'] += _d(r.get('amount'))

    out: list[dict[str, Any]] = []
    for bucket in sorted(buckets.keys()):
        slot = buckets[bucket]
        rev = slot.get('revenue', Decimal('0'))
        pt = slot.get('purchase_total', Decimal('0'))
        oe = slot.get('other_expenses_total', Decimal('0'))
        sc = slot.get('sales_cost_total', Decimal('0'))
        poe = slot.get('product_other_expenses_total', Decimal('0'))
        if tg == 'month':
            period_key = f'{bucket.year}-{bucket.month:02d}'
        else:
            period_key = bucket.isoformat()
        pt_num = _trend_metric_num(pt)
        oe_num = _trend_metric_num(oe)
        sc_num = _trend_metric_num(sc)
        poe_num = _trend_metric_num(poe)
        pe_num = _trend_metric_num(pt + oe + sc + poe)
        out.append(
            {
                'period': period_key,
                'revenue': _trend_metric_num(rev),
                'purchase_total': pt_num,
                'purchase': pt_num,
                'other_expenses_total': oe_num,
                'misc_expenses_total': oe_num,
                'sales_cost_total': sc_num,
                'product_other_expenses_total': poe_num,
                'sold_goods_cost_total': _trend_metric_num(sc + poe),
                'period_expenses_total': pe_num,
                'period_expenses': pe_num,
                'expenses': pe_num,
            }
        )
    return out


def _build_sales_by_profile(sq: Q) -> list[dict[str, Any]]:
    from apps.sales.models import SaleLine

    sale_ids = Sale.objects.filter(sq).distinct().values_list('pk', flat=True)
    rows = (
        SaleLine.objects.filter(sale_id__in=sale_ids, warehouse_batch__profile_id__isnull=False)
        .values('warehouse_batch__profile_id', 'warehouse_batch__profile__name')
        .annotate(
            sold_units=Sum('quantity'),
            revenue=Sum('line_total'),
        )
        .order_by('-sold_units')[:_sales_by_profile_limit()]
    )
    if not rows:
        units_sum = _sold_units_expr()
        rows = (
            Sale.objects.filter(sq)
            .filter(warehouse_batch__profile_id__isnull=False)
            .values('warehouse_batch__profile_id', 'warehouse_batch__profile__name')
            .annotate(
                sold_units=Sum(units_sum),
                revenue=Sum('revenue'),
            )
            .order_by('-sold_units')[:_sales_by_profile_limit()]
        )
    out = []
    for r in rows:
        su = _d(r.get('sold_units'))
        rv = _d(r.get('revenue'))
        out.append(
            {
                'profile_id': r['warehouse_batch__profile_id'],
                'profile_name': (r.get('warehouse_batch__profile__name') or '').strip() or '—',
                'sold_units': int(su) if su == su.to_integral_value() else float(su.quantize(Decimal('0.0001'))),
                'revenue': api_decimal_str(rv),
            }
        )
    return out


def _compute_client_debt_total(scope: AnalyticsScope) -> Decimal:
    return _aggregate_client_debts(scope)['total']


def _aggregate_client_debts(scope: AnalyticsScope) -> dict[str, Any]:
    """Группировка долга по клиентам для карточки и debt-details."""
    from collections import defaultdict

    by_client: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            'debt': Decimal('0'),
            'sales_count': 0,
            'oldest': None,
            'client_name': '',
        },
    )
    qs = (
        Sale.objects.filter(_analytics_sale_q(scope))
        .filter(client_id__isnull=False)
        .select_related('client')
        .order_by('date', 'id')
    )
    for sale in qs:
        debt = _d(sale_payment_metrics(sale)['debt_amount'])
        if debt <= 0:
            continue
        row = by_client[sale.client_id]
        row['debt'] += debt
        row['sales_count'] += 1
        row['client_name'] = (sale.client.name or '').strip() or '—'
        if row['oldest'] is None or sale.date < row['oldest']:
            row['oldest'] = sale.date

    items: list[dict[str, Any]] = []
    total = Decimal('0')
    for client_id, row in sorted(by_client.items(), key=lambda x: x[1]['debt'], reverse=True):
        debt = row['debt'].quantize(Decimal('0.01'))
        total += debt
        items.append(
            {
                'client_id': client_id,
                'client_name': row['client_name'],
                'debt_amount': api_decimal_str(debt),
                'sales_count': row['sales_count'],
                'oldest_debt_date': row['oldest'].isoformat() if row['oldest'] else None,
            },
        )
    return {'total': total.quantize(Decimal('0.01')), 'items': items}


def build_debt_details(scope: AnalyticsScope) -> dict[str, Any]:
    agg = _aggregate_client_debts(scope)
    return {
        'period': scope.as_period_dict(),
        'debt_as_of': DEBT_AS_OF_SEMANTICS,
        'total_debt': api_decimal_str(agg['total']),
        'items': agg['items'],
    }


def _unit_cost_line(qty: Decimal, total: Decimal) -> Optional[str]:
    if qty <= 0:
        return None
    return api_decimal_str((total / qty).quantize(Decimal('0.0001')))


def _profile_unit_cost_per_piece(profile_id: int) -> Optional[Decimal]:
    """
    Актуальная себестоимость 1 шт (справочник «на сейчас»):
    ProductionBatch → склад (взвеш.) → цеховая приёмка ГП (FIFO состава заготовки) → рецепт.
    """
    pb_cost = (
        ProductionBatch.objects.filter(profile_id=profile_id, cost_per_piece__gt=0)
        .order_by('-date', '-id')
        .values_list('cost_per_piece', flat=True)
        .first()
    )
    if pb_cost is not None:
        c = _d(pb_cost)
        if c > 0:
            return c.quantize(Decimal('0.0001'))

    total_qty = Decimal('0')
    total_value = Decimal('0')
    for cpp, qty in (
        WarehouseBatch.objects.filter(
            profile_id=profile_id,
            cost_per_piece__gt=0,
            quality=WarehouseBatch.QUALITY_GOOD,
            status__in=(WarehouseBatch.STATUS_AVAILABLE, WarehouseBatch.STATUS_RESERVED),
        ).values_list('cost_per_piece', 'quantity')
    ):
        q = _d(qty)
        unit = _d(cpp)
        if q > 0 and unit > 0:
            total_qty += q
            total_value += q * unit
    if total_qty > 0:
        return (total_value / total_qty).quantize(Decimal('0.0001'))

    from apps.workshop.unit_cost import (
        recipe_estimated_unit_cost_per_piece,
        workshop_profile_unit_cost_per_piece,
    )

    ws_cost = workshop_profile_unit_cost_per_piece(profile_id)
    if ws_cost is not None and ws_cost > 0:
        return ws_cost

    recipe_cost = recipe_estimated_unit_cost_per_piece(profile_id)
    if recipe_cost is not None and recipe_cost > 0:
        return recipe_cost

    return None


def build_product_unit_costs() -> dict[str, Any]:
    """Справочник профилей и текущей себестоимости за 1 шт (не зависит от периода в query)."""
    from apps.recipes.profile_pricing import serialize_other_expenses_total, serialize_sale_unit_price

    items: list[dict[str, Any]] = []
    for p in PlasticProfile.objects.order_by('name', 'id'):
        cost = _profile_unit_cost_per_piece(p.pk)
        cost_str = api_decimal_str(cost) if cost is not None and cost > 0 else None
        other_s = serialize_other_expenses_total(p)
        sale_s = serialize_sale_unit_price(p)
        items.append(
            {
                'profile_id': p.pk,
                'profile_name': p.name,
                'product_name': None,
                'code': (p.code or '').strip(),
                'is_active': bool(p.is_active),
                'unit_cost_per_piece': cost_str,
                'cost_per_piece': cost_str,
                'material_cost_per_piece': cost_str,
                'current_unit_cost': cost_str,
                'unit_cost': cost_str,
                'cost_price': cost_str,
                'other_expenses_total': other_s,
                'sale_unit_price': sale_s,
            }
        )
    return {'items': items}


def build_sales_cost_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Себестоимость проданного (материал) по строкам продаж."""
    total = Decimal('0')
    items: list[dict[str, Any]] = []
    for row in iter_sale_pnl_rows(scope):
        total += row.material_cost
        if len(items) >= ANALYTICS_DETAIL_ITEMS_LIMIT:
            continue
        d_iso = row.sale_date.isoformat()
        items.append(
            {
                'date': d_iso,
                'sale_date': d_iso,
                'created_at': d_iso,
                'sale_id': row.sale_id,
                'profile_id': row.profile_id,
                'profile_name': row.profile_name,
                'product_name': row.product_name or None,
                'quantity': api_decimal_str(row.quantity),
                'cost_per_unit': api_decimal_str(row.unit_material_cost),
                'total_cost': api_decimal_str(row.material_cost),
            }
        )
    tot_str = api_decimal_str(total.quantize(Decimal('0.01')))
    return {
        'period': scope.as_period_dict(),
        'total_sales_cost': tot_str,
        'total': tot_str,
        'items': items,
    }


def build_product_other_expenses_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Прочие расходы товара (extra_* профиля × проданные шт)."""
    total = Decimal('0')
    items: list[dict[str, Any]] = []
    for row in iter_sale_pnl_rows(scope):
        if row.product_other_cost <= 0:
            continue
        total += row.product_other_cost
        if len(items) >= ANALYTICS_DETAIL_ITEMS_LIMIT:
            continue
        unit_s = api_decimal_str(row.unit_other_expenses)
        d_iso = row.sale_date.isoformat()
        items.append(
            {
                'date': d_iso,
                'sale_id': row.sale_id,
                'profile_id': row.profile_id,
                'profile_name': row.profile_name,
                'quantity': api_decimal_str(row.quantity),
                'unit_other_expenses': unit_s,
                'other_per_unit': unit_s,
                'total_other_expenses': api_decimal_str(row.product_other_cost),
                'breakdown': profile_extra_breakdown(row.profile),
            }
        )
    tot_str = api_decimal_str(total.quantize(Decimal('0.01')))
    return {
        'period': scope.as_period_dict(),
        'total_product_other_expenses': tot_str,
        'total': tot_str,
        'items': items,
    }


def build_production_cost_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Себестоимость производства (ProductionBatch.material_cost_total)."""
    bq = production_batch_scope_q(scope)
    qs = (
        ProductionBatch.objects.filter(bq)
        .select_related('line', 'profile')
        .order_by('-date', '-id')
    )
    total = _d(qs.aggregate(t=Sum('material_cost_total'))['t'])
    items: list[dict[str, Any]] = []
    for pb in qs[:ANALYTICS_DETAIL_ITEMS_LIMIT]:
        cost = _d(pb.material_cost_total)
        pcs = _d(Decimal(str(pb.pieces or 0)))
        meters = _d(pb.total_meters)
        d_iso = pb.date.isoformat()
        items.append(
            {
                'date': d_iso,
                'created_at': d_iso,
                'production_batch_id': pb.id,
                'profile_name': ((pb.profile.name or '').strip()) if pb.profile_id and pb.profile else '',
                'line_name': ((pb.line.name or '').strip()) if pb.line_id and pb.line else '',
                'quantity_pieces': api_decimal_str(pcs),
                'pieces': api_decimal_str(pcs),
                'quantity': api_decimal_str(pcs),
                'total_meters': api_decimal_str(meters),
                'meters': api_decimal_str(meters),
                'material_cost_total': api_decimal_str(cost),
                'total_cost': api_decimal_str(cost),
            }
        )
    tot_str = api_decimal_str(total)
    return {
        'period': scope.as_period_dict(),
        'total_production_cost': tot_str,
        'total': tot_str,
        'items': items,
    }


def build_purchase_details(scope: AnalyticsScope) -> dict[str, Any]:
    """Закупка сырья (партии прихода), не смешивается с выручкой и sales cost."""
    iq = scope.incoming_date_q()
    qs = (
        MaterialBatch.objects.filter(iq)
        .select_related('material')
        .order_by('-received_at', '-id')
    )
    total = _d(qs.aggregate(t=Sum('total_price'))['t'])
    items: list[dict[str, Any]] = []
    for inc in qs[:ANALYTICS_DETAIL_ITEMS_LIMIT]:
        tot = _d(inc.total_price)
        qi = _d(inc.quantity_initial)
        up = inc.unit_price
        d_str = inc.received_at.date().isoformat() if inc.received_at else None
        items.append(
            {
                'date': d_str,
                'created_at': inc.received_at.isoformat() if inc.received_at else None,
                'material_name': inc.material.name,
                'supplier_name': (inc.supplier_name or '').strip(),
                'quantity': api_decimal_str(qi),
                'unit_price': api_decimal_str(_d(up)) if up is not None else None,
                'total_amount': api_decimal_str(tot),
            }
        )
    tot_str = api_decimal_str(total)
    return {
        'period': scope.as_period_dict(),
        'total_purchase_amount': tot_str,
        'total': tot_str,
        'items': items,
    }


def build_profit_details(scope: AnalyticsScope) -> dict[str, Any]:
    sq = _analytics_sale_q(scope)
    qs = (
        Sale.objects.filter(sq)
        .select_related('client', 'warehouse_batch', 'warehouse_batch__profile')
        .order_by('-date', '-id')
    )
    pnl = aggregate_sale_pnl(scope)
    sales_cost_total = pnl['sales_cost_total']
    product_other_expenses_total = pnl['product_other_expenses_total']
    from .other_expenses import sum_accepted_other_expenses

    purchase_total = _d(
        MaterialBatch.objects.filter(scope.incoming_date_q()).aggregate(pt=Sum('total_price'))['pt']
    )
    other_expenses_total = sum_accepted_other_expenses(scope)
    revenue_t = _d(qs.aggregate(rev=Sum('revenue'))['rev'])
    period_expenses_total = (
        purchase_total + other_expenses_total + sales_cost_total + product_other_expenses_total
    ).quantize(Decimal('0.01'))
    profit_t = _profit_dec(revenue_t, period_expenses_total)
    items: list[dict[str, Any]] = []
    for s in qs[:ANALYTICS_DETAIL_ITEMS_LIMIT]:
        rev = _d(s.revenue)
        cst = _d(s.cost)
        prf = _d(s.profit)
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
                'id': s.id,
                'order_number': s.order_number,
                'client_name': (s.client.name if s.client_id else '') or '',
                'profile_name': profile_name,
                'product_name': prod,
                'object': obj,
                'revenue': api_decimal_str(rev),
                'sales_cost': api_decimal_str(cst),
                'profit': api_decimal_str(prf),
            }
        )
    tot = {
        'revenue': api_decimal_str(revenue_t),
        'purchase_total': api_decimal_str(purchase_total),
        'other_expenses_total': api_decimal_str(other_expenses_total),
        'sales_cost_total': api_decimal_str(sales_cost_total),
        'product_other_expenses_total': api_decimal_str(product_other_expenses_total),
        'period_expenses_total': api_decimal_str(period_expenses_total),
        'profit': api_decimal_str(profit_t),
        'sales_cost': api_decimal_str(sales_cost_total),
    }
    return {
        'period': scope.as_period_dict(),
        'totals': tot,
        'revenue': tot['revenue'],
        'sales_cost': tot['sales_cost'],
        'profit': tot['profit'],
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
    sq = _analytics_sale_q(scope)
    qs = (
        Sale.objects.filter(sq)
        .select_related('client', 'warehouse_batch', 'warehouse_batch__profile')
        .order_by('-date', '-id')
    )
    total = _d(qs.aggregate(t=Sum('revenue'))['t'])
    items: list[dict[str, Any]] = []
    for sale in qs[:ANALYTICS_DETAIL_ITEMS_LIMIT]:
        sale_total = sale.revenue or Decimal('0')
        wb = sale.warehouse_batch
        profile_name = ''
        if wb and wb.profile_id and wb.profile:
            profile_name = (wb.profile.name or '').strip()
        qty_str = api_decimal_str(
            sale.sold_pieces
            if sale.sold_pieces and sale.sold_pieces > 0
            else sale.quantity
        )
        rev_str = api_decimal_str(sale.revenue or Decimal('0'))
        price_str = api_decimal_str(sale.price or Decimal('0'))
        d_str = sale.date.strftime('%Y-%m-%d')
        items.append(
            {
                'id': sale.id,
                'date': d_str,
                'client_name': sale.client.name if sale.client_id else '',
                'profile_name': profile_name,
                'product_name': sale.product or '',
                'quantity': qty_str,
                'price_per_unit': price_str,
                'unit_price': price_str,
                'revenue': rev_str,
                'total': rev_str,
                'total_price': rev_str,
            }
        )
    tot_str = api_decimal_str(total)
    return {
        'period': scope.as_period_dict(),
        'total': tot_str,
        'items': items,
    }
