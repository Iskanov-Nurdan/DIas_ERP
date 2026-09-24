"""
Дашборд аналитики (GET /api/analytics/dashboard/, /api/analytics/dashboard-details/).

Отличия от старого /analytics/summary/ (он оставлен как есть — на него завязаны
тесты и старые отчёты):

* Прибыль НЕ вычитает закупки сырья: закупка — это движение денег/запаса,
  в расход она попадает через себестоимость проданного (COGS). Иначе материал
  считался дважды.
* Выручка уменьшается на завершённые возвраты (по дате возврата).
* Учтена вторая линия — Пенополистирол (FoamSale); себестоимость Foam —
  оценка по средней стоимости сырья на единицу выпуска (см. _foam_unit_costs).
* Ручные расходы/приходы — единый реестр AnalyticsOtherExpense (kind).

Все деньги — Decimal и отдаются строкой (api_decimal_str), без float.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.foam.constants import CUBE_HEIGHT_CM, OUTPUT_FORMAT_CUBE, OUTPUT_FORMAT_GRANULE, OUTPUT_FORMAT_SHEET
from apps.foam.models import FoamGpStock, FoamProductionRun, FoamRawLot, FoamSale, FoamSaleLine
from apps.materials.models import MaterialBatch
from apps.otk.models import OtkCheck
from apps.production.models import ProductionBatch
from apps.sales.models import Payment, Return, ReturnLine, Sale, SaleLine
from apps.sales.payment_status import sale_payment_metrics
from apps.warehouse.models import WarehouseBatch
from config.api_numbers import api_decimal_str

from .models import KIND_EXPENSE, KIND_INCOME, PRODUCT_LINE_FOAM, PRODUCT_LINE_PROFILE, AnalyticsOtherExpense
from .reporting import _analytics_sale_q
from .sale_pnl import _row_from_parts, _qty_from_sale
from .services import AnalyticsScope, Period

ZERO = Decimal('0')
CENT = Decimal('0.01')
LINES = ('all', PRODUCT_LINE_PROFILE, PRODUCT_LINE_FOAM)
DETAIL_LIMIT = 500
DEAD_STOCK_DAYS = 60

METHOD_LABELS = {
    Payment.METHOD_CASH: 'Наличные',
    Payment.METHOD_CARD: 'Карта',
    Payment.METHOD_TRANSFER: 'Перевод',
    Payment.METHOD_OTHER: 'Другое',
    'foam': 'Пенополистирол (способ не фиксируется)',
}
FOAM_FORMAT_LABELS = {OUTPUT_FORMAT_CUBE: 'Куб', OUTPUT_FORMAT_SHEET: 'Лист', OUTPUT_FORMAT_GRANULE: 'Гранулы'}


def _d(v) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _m(v) -> Optional[str]:
    """Деньги → строка с 2 знаками (без float)."""
    if v is None:
        return None
    return api_decimal_str(_d(v).quantize(CENT))


def _pct(part: Decimal, whole: Decimal) -> Optional[str]:
    if whole == 0:
        return None
    return api_decimal_str((part * 100 / whole).quantize(Decimal('0.1')))


def has_finance_access(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    return 'analytics_finance' in (user.get_access_keys() or [])


# ── Параметры ─────────────────────────────────────────────────────────────


@dataclass
class DashParams:
    start: date
    end: date
    line: str = 'all'

    @property
    def with_profile(self) -> bool:
        return self.line in ('all', PRODUCT_LINE_PROFILE)

    @property
    def with_foam(self) -> bool:
        return self.line in ('all', PRODUCT_LINE_FOAM)

    @property
    def group(self) -> str:
        return 'month' if (self.end - self.start).days > 62 else 'day'

    def previous(self) -> 'DashParams':
        length = (self.end - self.start).days + 1
        prev_end = self.start - timedelta(days=1)
        return DashParams(start=prev_end - timedelta(days=length - 1), end=prev_end, line=self.line)

    def scope(self) -> AnalyticsScope:
        return AnalyticsScope(period=Period(year=self.start.year, month=None, day=None), date_from=self.start, date_to=self.end)

    def bucket(self, d: date) -> str:
        return d.strftime('%Y-%m') if self.group == 'month' else d.isoformat()

    def buckets(self) -> list[str]:
        out: list[str] = []
        if self.group == 'month':
            y, m = self.start.year, self.start.month
            while (y, m) <= (self.end.year, self.end.month):
                out.append(f'{y:04d}-{m:02d}')
                m += 1
                if m > 12:
                    y, m = y + 1, 1
        else:
            d = self.start
            while d <= self.end:
                out.append(d.isoformat())
                d += timedelta(days=1)
        return out


def parse_dash_params(qp) -> DashParams:
    from rest_framework.exceptions import ValidationError

    from .services import _parse_iso_date

    start = _parse_iso_date(qp.get('date_from'))
    end = _parse_iso_date(qp.get('date_to'))
    today = date.today()
    if start is None and end is None:
        start, end = today.replace(day=1), today
    start = start or end
    end = end or start
    if start > end:
        start, end = end, start
    if (end - start).days > 366 * 3:
        raise ValidationError({'date_from': ['Период не больше 3 лет.']})
    line = (qp.get('product_line') or 'all').strip()
    if line not in LINES:
        raise ValidationError({'product_line': ['all | profile | foam']})
    return DashParams(start=start, end=end, line=line)


# ── Сбор сырых данных ─────────────────────────────────────────────────────


@dataclass
class ProductRow:
    key: str
    name: str
    line: str
    qty: Decimal = ZERO
    revenue: Decimal = ZERO
    cogs: Decimal = ZERO
    estimated: bool = False


@dataclass
class Collected:
    sales_revenue: Decimal = ZERO
    returns_amount: Decimal = ZERO
    returns_cogs: Decimal = ZERO
    foam_revenue: Decimal = ZERO
    profile_cogs: Decimal = ZERO  # материал + прочие расходы товара
    profile_material: Decimal = ZERO
    profile_product_other: Decimal = ZERO
    foam_cogs: Decimal = ZERO
    opex: Decimal = ZERO
    other_income: Decimal = ZERO
    payroll: Decimal = ZERO
    sales_count: int = 0
    foam_sales_count: int = 0
    cash_in_by_method: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))
    refunds_paid: Decimal = ZERO
    trend: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(lambda: ZERO)))
    products: dict = field(default_factory=dict)
    expenses_by_category: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))
    income_by_category: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))

    @property
    def revenue(self) -> Decimal:
        return self.sales_revenue - self.returns_amount + self.foam_revenue

    @property
    def cogs(self) -> Decimal:
        return self.profile_cogs - self.returns_cogs + self.foam_cogs

    @property
    def gross(self) -> Decimal:
        return self.revenue - self.cogs

    @property
    def net(self) -> Decimal:
        return self.gross - self.opex + self.other_income

    @property
    def cash_in(self) -> Decimal:
        return sum(self.cash_in_by_method.values(), ZERO) - self.refunds_paid


def _product(c: Collected, key: str, name: str, line: str) -> ProductRow:
    row = c.products.get(key)
    if row is None:
        row = c.products[key] = ProductRow(key=key, name=name or 'Без названия', line=line)
    return row


def _profile_sales_qs(p: DashParams):
    return Sale.objects.filter(_analytics_sale_q(p.scope()))


def _collect_profile(p: DashParams, c: Collected) -> None:
    sales = list(_profile_sales_qs(p).select_related('warehouse_batch__profile').order_by('date', 'id'))
    c.sales_count = len(sales)
    lines_by_sale: dict[int, list[SaleLine]] = defaultdict(list)
    if sales:
        for line in SaleLine.objects.filter(sale_id__in=[s.pk for s in sales]).select_related('warehouse_batch__profile', 'sale'):
            lines_by_sale[line.sale_id].append(line)
    for sale in sales:
        rev = _d(sale.revenue)
        c.sales_revenue += rev
        b = p.bucket(sale.date)
        c.trend[b]['revenue'] += rev
        lines = lines_by_sale.get(sale.pk) or []
        parts = [(ln, _d(ln.quantity), _d(ln.line_total)) for ln in lines] or [(None, _qty_from_sale(sale), rev)]
        for ln, qty, line_rev in parts:
            r = _row_from_parts(sale=sale, line=ln, qty=qty)
            cogs = r.material_cost + r.product_other_cost
            c.profile_material += r.material_cost
            c.profile_product_other += r.product_other_cost
            c.profile_cogs += cogs
            c.trend[b]['cogs'] += cogs
            key = f'p{r.profile_id}' if r.profile_id else f'n:{r.profile_name or r.product_name}'
            pr = _product(c, key, r.profile_name or r.product_name, PRODUCT_LINE_PROFILE)
            pr.qty += qty
            pr.revenue += line_rev
            pr.cogs += cogs
            if r.material_cost <= 0:
                pr.estimated = True

    # Возвраты — по дате возврата, сумма по цене строки продажи.
    for rl in (
        ReturnLine.objects.filter(
            return_doc__status=Return.STATUS_COMPLETED,
            return_doc__date__gte=p.start,
            return_doc__date__lte=p.end,
            sale_line__isnull=False,
        ).select_related('return_doc', 'sale_line__warehouse_batch__profile', 'sale_line__sale')
    ):
        sl = rl.sale_line
        qty = _d(rl.quantity)
        sold = _d(sl.quantity)
        amount = (_d(sl.line_total) * qty / sold).quantize(CENT) if sold > 0 else (qty * _d(sl.unit_price)).quantize(CENT)
        r = _row_from_parts(sale=sl.sale, line=sl, qty=sold if sold > 0 else qty)
        unit_cogs = ((r.material_cost + r.product_other_cost) / sold) if sold > 0 else ZERO
        cogs = (unit_cogs * qty).quantize(CENT)
        c.returns_amount += amount
        c.returns_cogs += cogs
        b = p.bucket(rl.return_doc.date)
        c.trend[b]['revenue'] -= amount
        c.trend[b]['cogs'] -= cogs
        key = f'p{r.profile_id}' if r.profile_id else f'n:{r.profile_name or r.product_name}'
        pr = _product(c, key, r.profile_name or r.product_name, PRODUCT_LINE_PROFILE)
        pr.qty -= qty
        pr.revenue -= amount
        pr.cogs -= cogs

    # Деньги, реально полученные за период (по дате платежа).
    pays = Payment.objects.filter(status=Payment.STATUS_ACTIVE, date__gte=p.start, date__lte=p.end)
    for row in pays.values('payment_method', 'payment_type').annotate(s=Sum('amount')):
        amt = _d(row['s'])
        if row['payment_type'] == Payment.TYPE_REFUND:
            c.refunds_paid += amt
        else:
            c.cash_in_by_method[row['payment_method'] or Payment.METHOD_OTHER] += amt


def _foam_unit_costs() -> dict[tuple, Decimal]:
    """
    Оценка себестоимости единицы ГП Foam: Σ(input_kg × цена кг лота) / Σ output_qty
    по (формат, плотность). Лист = куб / (высота куба // толщина).
    Ключи: (format, grade_id) и (format, None) — средняя по формату.
    """
    cost_expr = ExpressionWrapper(F('input_kg') * F('lot__unit_price'), output_field=DecimalField(max_digits=20, decimal_places=4))
    acc: dict[tuple, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for row in FoamProductionRun.objects.values('output_format', 'grade_id').annotate(cost=Sum(cost_expr), qty=Sum('output_qty')):
        for key in ((row['output_format'], row['grade_id']), (row['output_format'], None)):
            acc[key][0] += _d(row['cost'])
            acc[key][1] += _d(row['qty'])
    return {k: (v[0] / v[1]) for k, v in acc.items() if v[1] > 0}


def _foam_line_unit_cost(costs: dict, fmt: str, grade_id, thickness) -> Optional[Decimal]:
    if fmt == OUTPUT_FORMAT_SHEET:
        cube = costs.get((OUTPUT_FORMAT_CUBE, grade_id)) or costs.get((OUTPUT_FORMAT_CUBE, None))
        if cube is None or not thickness:
            return None
        per_cube = CUBE_HEIGHT_CM // int(thickness)
        return cube / per_cube if per_cube > 0 else None
    return costs.get((fmt, grade_id)) or costs.get((fmt, None))


def foam_variant_label(fmt: str, grade_code: Optional[str], thickness) -> str:
    parts = [FOAM_FORMAT_LABELS.get(fmt, fmt)]
    if grade_code:
        parts.append(grade_code)
    if thickness:
        parts.append(f'{thickness} см')
    return ' · '.join(parts)


def _collect_foam(p: DashParams, c: Collected) -> None:
    sales = FoamSale.objects.filter(sale_date__gte=p.start, sale_date__lte=p.end)
    agg = sales.aggregate(t=Sum('total_amount'), paid=Sum('paid_amount'))
    c.foam_sales_count = sales.count()
    c.foam_revenue += _d(agg['t'])
    # У Foam нет отдельных платежей — оплата фиксируется при продаже.
    if _d(agg['paid']):
        c.cash_in_by_method['foam'] += _d(agg['paid'])
    for row in sales.values('sale_date').annotate(t=Sum('total_amount')):
        c.trend[p.bucket(row['sale_date'])]['revenue'] += _d(row['t'])
    costs = _foam_unit_costs()
    for ln in FoamSaleLine.objects.filter(sale__in=sales).select_related('sale', 'stock__grade'):
        st = ln.stock
        qty = _d(ln.qty)
        unit = _foam_line_unit_cost(costs, st.output_format, st.grade_id, st.thickness_cm)
        cogs = (unit * qty).quantize(CENT) if unit is not None else ZERO
        c.foam_cogs += cogs
        c.trend[p.bucket(ln.sale.sale_date)]['cogs'] += cogs
        key = f'f{st.output_format}:{st.grade_id}:{st.thickness_cm}'
        pr = _product(c, key, foam_variant_label(st.output_format, st.grade.code if st.grade_id else None, st.thickness_cm), PRODUCT_LINE_FOAM)
        pr.qty += qty
        pr.revenue += (qty * _d(ln.unit_price)).quantize(CENT)
        pr.cogs += cogs
        pr.estimated = True


def manual_entries_qs(p: DashParams):
    qs = AnalyticsOtherExpense.objects.filter(
        status=AnalyticsOtherExpense.STATUS_ACCEPTED, recurring=False, date__gte=p.start, date__lte=p.end,
    )
    if p.line != 'all':
        qs = qs.filter(product_line=p.line)
    return qs


def _collect_manual(p: DashParams, c: Collected) -> None:
    for row in manual_entries_qs(p).select_related('category'):
        amt = _d(row.amount)
        cat = row.category.name if row.category_id else 'Без категории'
        b = p.bucket(row.date)
        if row.kind == KIND_INCOME:
            c.other_income += amt
            c.income_by_category[cat] += amt
            c.trend[b]['income'] += amt
        else:
            c.opex += amt
            c.expenses_by_category[cat] += amt
            c.trend[b]['opex'] += amt
            if row.category_id and row.category.is_payroll:
                c.payroll += amt


def collect(p: DashParams) -> Collected:
    c = Collected()
    if p.with_profile:
        _collect_profile(p, c)
    if p.with_foam:
        _collect_foam(p, c)
    _collect_manual(p, c)
    return c


# ── Блоки дашборда ────────────────────────────────────────────────────────


def _kpis(c: Collected, prev: Collected, finance: bool) -> list[dict[str, Any]]:
    def item(key, label, cur, before, is_finance, hint):
        locked = is_finance and not finance
        delta = None
        if not locked and before != 0:
            delta = api_decimal_str(((cur - before) * 100 / abs(before)).quantize(Decimal('0.1')))
        return {
            'key': key,
            'label': label,
            'value': None if locked else _m(cur),
            'previous': None if locked else _m(before),
            'delta_pct': delta,
            'locked': locked,
            'hint': hint,
        }

    return [
        item('revenue', 'Выручка', c.revenue, prev.revenue, False, 'Продажи − возвраты (по дате продажи/возврата)'),
        item('cash_in', 'Получено денег', c.cash_in, prev.cash_in, False, 'Платежи за период − возвраты денег'),
        item('gross_margin', 'Валовая маржа', c.gross, prev.gross, True, 'Выручка − себестоимость проданного'),
        item('expenses', 'Расходы', c.opex, prev.opex, True, 'Принятые ручные расходы (аренда, зарплата…)'),
        item('net_profit', 'Чистая прибыль', c.net, prev.net, True, 'Маржа − расходы + прочие доходы'),
    ]


def _trend(p: DashParams, c: Collected, finance: bool) -> list[dict[str, Any]]:
    out = []
    for b in p.buckets():
        t = c.trend.get(b) or {}
        rev = _d(t.get('revenue'))
        gross = rev - _d(t.get('cogs'))
        net = gross - _d(t.get('opex')) + _d(t.get('income'))
        out.append({
            'period': b,
            'revenue': _m(rev),
            'gross_margin': _m(gross) if finance else None,
            'net_profit': _m(net) if finance else None,
        })
    return out


def _top_products(c: Collected, finance: bool, limit: Optional[int] = 10) -> list[dict[str, Any]]:
    rows = [r for r in c.products.values() if r.revenue != 0 or r.qty != 0]
    rows.sort(key=(lambda r: r.revenue - r.cogs) if finance else (lambda r: r.revenue), reverse=True)
    if limit:
        rows = rows[:limit]
    return [
        {
            'key': r.key,
            'name': r.name,
            'product_line': r.line,
            'quantity': api_decimal_str(r.qty.quantize(Decimal('0.001'))),
            'revenue': _m(r.revenue),
            'cogs': _m(r.cogs) if finance else None,
            'margin': _m(r.revenue - r.cogs) if finance else None,
            'margin_pct': _pct(r.revenue - r.cogs, r.revenue) if finance else None,
            'cost_estimated': r.estimated,
        }
        for r in rows
    ]


def _by_category(d: dict) -> list[dict[str, Any]]:
    return [{'category': k, 'amount': _m(v)} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)]


def _debts(p: DashParams, limit: int = 10) -> dict[str, Any]:
    """Текущая дебиторка (на сегодня), с возрастом от даты продажи."""
    today = date.today()
    buckets = {'0_30': ZERO, '31_60': ZERO, '60_plus': ZERO}
    by_client: dict[str, dict] = {}

    def add(name: str, line: str, sale_date: date, debt: Decimal):
        age = (today - sale_date).days
        key = '0_30' if age <= 30 else ('31_60' if age <= 60 else '60_plus')
        buckets[key] += debt
        row = by_client.setdefault(f'{line}:{name}', {'client': name, 'product_line': line, 'debt': ZERO, 'sales': 0, 'oldest_days': 0})
        row['debt'] += debt
        row['sales'] += 1
        row['oldest_days'] = max(row['oldest_days'], age)

    if p.with_profile:
        paid_expr = Coalesce(
            Sum(
                'payments__amount',
                filter=Q(
                    payments__status=Payment.STATUS_ACTIVE,
                    payments__payment_type__in=(Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE),
                ),
            ),
            Value(ZERO),
            output_field=DecimalField(max_digits=16, decimal_places=2),
        )
        candidates = (
            Sale.objects.exclude(sale_status=Sale.STATUS_CANCELED)
            .exclude(sale_status=Sale.STATUS_DRAFT, warehouse_stock_applied=False)
            .annotate(paid_sum=paid_expr)
            .filter(revenue__gt=F('paid_sum') + F('order_paid_amount_applied') + Decimal('0.005'))
            .select_related('client')
            .prefetch_related('sale_lines')
        )
        for sale in candidates:
            debt = _d(sale_payment_metrics(sale)['debt_amount'])
            if debt > 0:
                add(sale.client.name if sale.client_id else 'Без клиента', PRODUCT_LINE_PROFILE, sale.date, debt)
    if p.with_foam:
        for fs in FoamSale.objects.filter(total_amount__gt=F('paid_amount')):
            add(fs.client or 'Без клиента', PRODUCT_LINE_FOAM, fs.sale_date, _d(fs.total_amount) - _d(fs.paid_amount))

    total = sum(buckets.values(), ZERO)
    top = sorted(by_client.values(), key=lambda r: r['debt'], reverse=True)[:limit]
    return {
        'total': _m(total),
        'aging': [
            {'key': '0_30', 'label': '0–30 дней', 'amount': _m(buckets['0_30'])},
            {'key': '31_60', 'label': '31–60 дней', 'amount': _m(buckets['31_60'])},
            {'key': '60_plus', 'label': 'Больше 60 дней', 'amount': _m(buckets['60_plus'])},
        ],
        'top_debtors': [{**r, 'debt': _m(r['debt'])} for r in top],
        'note': 'Возраст долга считается от даты продажи: срока оплаты в системе нет.',
    }


def _top_clients(p: DashParams, limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if p.with_profile:
        for r in (
            _profile_sales_qs(p).filter(client__isnull=False)
            .values('client_id', 'client__name').annotate(revenue=Sum('revenue'))
            .order_by('-revenue')[:limit]
        ):
            rows.append({'client': r['client__name'], 'product_line': PRODUCT_LINE_PROFILE, 'revenue': _d(r['revenue'])})
    if p.with_foam:
        for r in (
            FoamSale.objects.filter(sale_date__gte=p.start, sale_date__lte=p.end)
            .values('client').annotate(revenue=Sum('total_amount')).order_by('-revenue')[:limit]
        ):
            rows.append({'client': r['client'] or 'Без клиента', 'product_line': PRODUCT_LINE_FOAM, 'revenue': _d(r['revenue'])})
    rows.sort(key=lambda r: r['revenue'], reverse=True)
    return [{**r, 'revenue': _m(r['revenue'])} for r in rows[:limit]]


def _cashiers(p: DashParams) -> list[dict[str, Any]]:
    if not p.with_profile:
        return []
    out = []
    for r in (
        _profile_sales_qs(p).values('created_by_id', 'created_by__name')
        .annotate(revenue=Sum('revenue')).order_by('-revenue')
    ):
        cnt = _profile_sales_qs(p).filter(created_by_id=r['created_by_id']).count()
        rev = _d(r['revenue'])
        out.append({
            'cashier': r['created_by__name'] or 'Не указан',
            'sales_count': cnt,
            'revenue': _m(rev),
            'avg_check': _m(rev / cnt) if cnt else None,
        })
    return out


def _production(p: DashParams) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if p.with_profile:
        pb = ProductionBatch.objects.filter(date__gte=p.start, date__lte=p.end)
        agg = pb.aggregate(pieces=Sum('pieces'), meters=Sum('total_meters'))
        otk = OtkCheck.objects.filter(checked_date__date__gte=p.start, checked_date__date__lte=p.end).aggregate(
            ok=Sum('accepted'), bad=Sum('rejected'),
        )
        ok, bad = _d(otk['ok']), _d(otk['bad'])
        out['profile'] = {
            'batches': pb.count(),
            'pieces': int(agg['pieces'] or 0),
            'meters': api_decimal_str(_d(agg['meters']).quantize(Decimal('0.01'))),
            'otk_accepted': api_decimal_str(ok),
            'otk_rejected': api_decimal_str(bad),
            'defect_pct': _pct(bad, ok + bad),
        }
    if p.with_foam:
        runs = FoamProductionRun.objects.filter(produced_at__date__gte=p.start, produced_at__date__lte=p.end)
        agg = runs.aggregate(kg=Sum('input_kg'))
        by_fmt = {
            r['output_format']: api_decimal_str(_d(r['q']))
            for r in runs.values('output_format').annotate(q=Sum('output_qty'))
        }
        out['foam'] = {
            'runs': runs.count(),
            'input_kg': api_decimal_str(_d(agg['kg'])),
            'output_by_format': [
                {'format': k, 'label': FOAM_FORMAT_LABELS.get(k, k), 'qty': v} for k, v in by_fmt.items()
            ],
        }
    return out


def _warehouse(p: DashParams, finance: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    today = date.today()
    if p.with_profile:
        stock = WarehouseBatch.objects.filter(
            quantity__gt=0, quality=WarehouseBatch.QUALITY_GOOD,
        ).exclude(status=WarehouseBatch.STATUS_SHIPPED).select_related('profile')
        value = ZERO
        by_profile: dict[Any, dict] = {}
        dead = []
        dead_from = today - timedelta(days=DEAD_STOCK_DAYS)
        recently_sold = set(
            SaleLine.objects.filter(sale__date__gte=dead_from).exclude(sale__sale_status=Sale.STATUS_CANCELED)
            .values_list('warehouse_batch_id', flat=True)
        ) | set(
            Sale.objects.filter(date__gte=dead_from).exclude(sale_status=Sale.STATUS_CANCELED)
            .values_list('warehouse_batch_id', flat=True)
        )
        for wb in stock:
            qty = _d(wb.quantity)
            v = (qty * _d(wb.cost_per_piece)).quantize(CENT)
            value += v
            name = (wb.profile.name if wb.profile_id else '') or wb.product or 'Без названия'
            row = by_profile.setdefault(wb.profile_id or name, {'name': name, 'profile_id': wb.profile_id, 'qty': ZERO})
            row['qty'] += qty
            if wb.date and wb.date <= dead_from and wb.pk not in recently_sold:
                dead.append({'name': name, 'batch_id': wb.pk, 'date': wb.date.isoformat(), 'days': (today - wb.date).days,
                             'quantity': api_decimal_str(qty), 'value': _m(v) if finance else None, '_v': v})
        # «Заканчивается»: за последние 30 дней продали не меньше, чем осталось.
        sold_30 = defaultdict(lambda: ZERO)
        for r in (
            SaleLine.objects.filter(sale__date__gte=today - timedelta(days=30), warehouse_batch__profile__isnull=False)
            .exclude(sale__sale_status=Sale.STATUS_CANCELED)
            .values('warehouse_batch__profile_id').annotate(q=Sum('quantity'))
        ):
            sold_30[r['warehouse_batch__profile_id']] = _d(r['q'])
        low = []
        for row in by_profile.values():
            s = sold_30.get(row['profile_id'], ZERO)
            if s > 0 and row['qty'] <= s:
                low.append({'name': row['name'], 'quantity': api_decimal_str(row['qty']), 'sold_30d': api_decimal_str(s),
                            'days_left': int((row['qty'] * 30 / s)) if s else None})
        low.sort(key=lambda r: r['days_left'] if r['days_left'] is not None else 9999)
        dead.sort(key=lambda r: r['_v'], reverse=True)
        for r in dead:
            r.pop('_v', None)
        out['profile'] = {
            'stock_value': _m(value) if finance else None,
            'positions': len(by_profile),
            'low_stock': low[:10],
            'dead_stock': dead[:10],
            'dead_stock_days': DEAD_STOCK_DAYS,
        }
    if p.with_foam:
        out['foam'] = {
            'stock': [
                {'name': foam_variant_label(s.output_format, s.grade.code if s.grade_id else None, s.thickness_cm),
                 'quantity': api_decimal_str(_d(s.qty))}
                for s in FoamGpStock.objects.filter(qty__gt=0).select_related('grade')
            ],
            'raw_kg': api_decimal_str(_d(FoamRawLot.objects.aggregate(s=Sum('remaining_kg'))['s'])),
        }
    return out


def _purchases(p: DashParams) -> dict[str, Decimal]:
    out = {'profile': ZERO, 'foam': ZERO}
    if p.with_profile:
        out['profile'] = _d(MaterialBatch.objects.filter(
            received_at__date__gte=p.start, received_at__date__lte=p.end,
        ).aggregate(s=Sum('total_price'))['s'])
    if p.with_foam:
        expr = ExpressionWrapper(F('received_kg') * F('unit_price'), output_field=DecimalField(max_digits=20, decimal_places=4))
        out['foam'] = _d(FoamRawLot.objects.filter(
            received_at__date__gte=p.start, received_at__date__lte=p.end,
        ).aggregate(s=Sum(expr))['s'])
    return out


def build_dashboard(p: DashParams, user) -> dict[str, Any]:
    finance = has_finance_access(user)
    cur = collect(p)
    prev_p = p.previous()
    prev = collect(prev_p)
    purchases = _purchases(p)
    purchases_total = purchases['profile'] + purchases['foam']
    cash_out = purchases_total + cur.opex
    return {
        'period': {
            'date_from': p.start.isoformat(), 'date_to': p.end.isoformat(), 'group': p.group,
            'previous_from': prev_p.start.isoformat(), 'previous_to': prev_p.end.isoformat(),
        },
        'product_line': p.line,
        'finance_access': finance,
        'kpis': _kpis(cur, prev, finance),
        'counts': {'sales': cur.sales_count, 'foam_sales': cur.foam_sales_count},
        'trend': _trend(p, cur, finance),
        'expenses_by_category': _by_category(cur.expenses_by_category) if finance else None,
        'top_products': _top_products(cur, finance),
        'cash': {
            'in_by_method': [
                {'method': k, 'label': METHOD_LABELS.get(k, k), 'amount': _m(v)}
                for k, v in sorted(cur.cash_in_by_method.items(), key=lambda kv: kv[1], reverse=True)
            ],
            'refunds': _m(cur.refunds_paid),
            'purchases': _m(purchases_total) if finance else None,
            'cash_flow': _m(cur.cash_in - cash_out) if finance else None,
        },
        'people': {'payroll': _m(cur.payroll) if finance else None, 'cashiers': _cashiers(p)},
        'debts': _debts(p),
        'top_clients': _top_clients(p),
        'production': _production(p),
        'warehouse': _warehouse(p, finance),
    }


# ── Детализация KPI ───────────────────────────────────────────────────────

FINANCE_METRICS = {'gross_margin', 'expenses', 'net_profit'}
METRICS = {'revenue', 'cash_in'} | FINANCE_METRICS


def _formula(c: Collected) -> dict[str, list[dict[str, Any]]]:
    rev = [
        {'label': 'Продажи профиля', 'amount': _m(c.sales_revenue), 'sign': '+'},
        {'label': 'Возвраты профиля', 'amount': _m(c.returns_amount), 'sign': '−'},
        {'label': 'Продажи пенополистирола', 'amount': _m(c.foam_revenue), 'sign': '+'},
    ]
    cogs = [
        {'label': 'Материал проданного профиля', 'amount': _m(c.profile_material), 'sign': '+'},
        {'label': 'Прочие расходы в цене товара', 'amount': _m(c.profile_product_other), 'sign': '+'},
        {'label': 'Себестоимость возвращённого', 'amount': _m(c.returns_cogs), 'sign': '−'},
        {'label': 'Сырьё проданного пенополистирола (оценка)', 'amount': _m(c.foam_cogs), 'sign': '+'},
    ]
    return {
        'revenue': rev + [{'label': 'Выручка', 'amount': _m(c.revenue), 'sign': '=', 'total': True}],
        'cash_in': [
            {'label': METHOD_LABELS.get(k, k), 'amount': _m(v), 'sign': '+'} for k, v in c.cash_in_by_method.items()
        ] + [
            {'label': 'Возвраты денег клиентам', 'amount': _m(c.refunds_paid), 'sign': '−'},
            {'label': 'Получено денег', 'amount': _m(c.cash_in), 'sign': '=', 'total': True},
        ],
        'gross_margin': [
            {'label': 'Выручка', 'amount': _m(c.revenue), 'sign': '+'},
            {'label': 'Себестоимость проданного', 'amount': _m(c.cogs), 'sign': '−'},
        ] + cogs + [{'label': 'Валовая маржа', 'amount': _m(c.gross), 'sign': '=', 'total': True}],
        'expenses': [
            {'label': k, 'amount': _m(v), 'sign': '+'} for k, v in sorted(c.expenses_by_category.items(), key=lambda kv: kv[1], reverse=True)
        ] + [{'label': 'Расходы', 'amount': _m(c.opex), 'sign': '=', 'total': True}],
        'net_profit': [
            {'label': 'Валовая маржа', 'amount': _m(c.gross), 'sign': '+'},
            {'label': 'Расходы', 'amount': _m(c.opex), 'sign': '−'},
            {'label': 'Прочие доходы', 'amount': _m(c.other_income), 'sign': '+'},
            {'label': 'Чистая прибыль', 'amount': _m(c.net), 'sign': '=', 'total': True},
        ],
    }


NOTES = {
    'revenue': 'Продажа учитывается в дату продажи, возврат — в дату возврата. Отменённые и черновики без списания склада не входят.',
    'cash_in': 'Считаются платежи по дате платежа (предоплата, оплата, доплата). У пенополистирола оплата фиксируется при продаже.',
    'gross_margin': 'Закупка сырья не вычитается: в расход попадает только сырьё проданного товара. '
                    'Себестоимость пенополистирола — оценка по средней цене сырья на единицу выпуска.',
    'expenses': 'Только принятые записи реестра «Расходы и доходы». Шаблоны регулярных расходов не суммируются.',
    'net_profit': 'Закупки сырья и оплаты поставщикам в прибыль не входят — они показаны в блоке «Деньги».',
}


def _detail_items(p: DashParams, metric: str, c: Collected) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if metric == 'revenue':
        if p.with_profile:
            for s in _profile_sales_qs(p).select_related('client').order_by('-date', '-id')[:DETAIL_LIMIT]:
                items.append({'date': s.date.isoformat(), 'title': s.sale_number or f'#{s.pk}',
                              'subtitle': s.client.name if s.client_id else '', 'amount': _m(s.revenue), 'kind': 'sale'})
            for r in (
                Return.objects.filter(status=Return.STATUS_COMPLETED, date__gte=p.start, date__lte=p.end)
                .prefetch_related('lines__sale_line')[:DETAIL_LIMIT]
            ):
                amt = ZERO
                for rl in r.lines.all():
                    sl = rl.sale_line
                    if sl and _d(sl.quantity) > 0:
                        amt += (_d(sl.line_total) * _d(rl.quantity) / _d(sl.quantity)).quantize(CENT)
                items.append({'date': r.date.isoformat(), 'title': r.return_number or f'Возврат #{r.pk}',
                              'subtitle': 'Возврат', 'amount': _m(-amt), 'kind': 'return'})
        if p.with_foam:
            for s in FoamSale.objects.filter(sale_date__gte=p.start, sale_date__lte=p.end).order_by('-sale_date', '-id')[:DETAIL_LIMIT]:
                items.append({'date': s.sale_date.isoformat(), 'title': f'Пенополистирол #{s.pk}',
                              'subtitle': s.client, 'amount': _m(s.total_amount), 'kind': 'foam_sale'})
    elif metric == 'cash_in':
        if p.with_profile:
            for pay in (
                Payment.objects.filter(status=Payment.STATUS_ACTIVE, date__gte=p.start, date__lte=p.end)
                .select_related('client').order_by('-date', '-id')[:DETAIL_LIMIT]
            ):
                sign = -1 if pay.payment_type == Payment.TYPE_REFUND else 1
                items.append({'date': pay.date.isoformat(), 'title': pay.payment_number or f'Платёж #{pay.pk}',
                              'subtitle': f"{pay.client.name if pay.client_id else ''} · {METHOD_LABELS.get(pay.payment_method, pay.payment_method)}",
                              'amount': _m(_d(pay.amount) * sign), 'kind': pay.payment_type})
        if p.with_foam:
            for s in FoamSale.objects.filter(sale_date__gte=p.start, sale_date__lte=p.end, paid_amount__gt=0).order_by('-sale_date')[:DETAIL_LIMIT]:
                items.append({'date': s.sale_date.isoformat(), 'title': f'Пенополистирол #{s.pk}',
                              'subtitle': s.client, 'amount': _m(s.paid_amount), 'kind': 'foam_sale'})
    elif metric == 'gross_margin':
        for r in _top_products(c, True, limit=None):
            items.append({'title': r['name'], 'subtitle': f"Выручка {r['revenue']} · себестоимость {r['cogs']}",
                          'amount': r['margin'], 'kind': r['product_line'], 'estimated': r['cost_estimated']})
    elif metric in ('expenses', 'net_profit'):
        qs = manual_entries_qs(p).select_related('category').order_by('-date', '-id')
        if metric == 'expenses':
            qs = qs.filter(kind=KIND_EXPENSE)
        for e in qs[:DETAIL_LIMIT]:
            sign = 1 if e.kind == KIND_INCOME else -1
            items.append({'date': e.date.isoformat(), 'title': e.name,
                          'subtitle': e.category.name if e.category_id else '', 'amount': _m(_d(e.amount) * (sign if metric == 'net_profit' else 1)),
                          'kind': e.kind})
    if metric != 'gross_margin':
        items.sort(key=lambda r: r.get('date') or '', reverse=True)
    return items[:DETAIL_LIMIT]


def build_details(p: DashParams, metric: str, user) -> dict[str, Any]:
    from rest_framework.exceptions import PermissionDenied, ValidationError

    if metric not in METRICS:
        raise ValidationError({'metric': [f'Допустимо: {", ".join(sorted(METRICS))}']})
    if metric in FINANCE_METRICS and not has_finance_access(user):
        raise PermissionDenied('Нет доступа к финансовым показателям (analytics_finance).')
    c = collect(p)
    return {
        'metric': metric,
        'period': {'date_from': p.start.isoformat(), 'date_to': p.end.isoformat()},
        'product_line': p.line,
        'formula': _formula(c)[metric],
        'note': NOTES[metric],
        'items': _detail_items(p, metric, c),
    }
