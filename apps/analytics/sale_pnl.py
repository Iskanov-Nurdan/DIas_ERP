"""P&L по строкам продаж: себестоимость материала и прочие расходы товара."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterator, Optional

from django.db.models import Q

from apps.recipes.models import PlasticProfile
from apps.recipes.profile_pricing import EXTRA_FIELD_NAMES, other_expenses_total
from apps.sales.models import Sale, SaleLine

from .services import AnalyticsScope


def _d(v) -> Decimal:
    if v is None:
        return Decimal('0')
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@dataclass
class SalePnlRow:
    sale_id: int
    sale_date: date
    profile_id: Optional[int]
    profile_name: str
    product_name: str
    quantity: Decimal
    material_cost: Decimal
    unit_material_cost: Decimal
    unit_other_expenses: Decimal
    product_other_cost: Decimal
    profile: Optional[PlasticProfile]


def _qty_from_sale(sale: Sale) -> Decimal:
    sp = sale.sold_pieces
    if sp is not None and Decimal(str(sp)) > 0:
        return _d(sp)
    return _d(sale.quantity)


def _unit_material_from_cost(qty: Decimal, cost: Decimal) -> Decimal:
    if qty <= 0:
        return Decimal('0')
    return (cost / qty).quantize(Decimal('0.0001'))


def _resolve_profile(*, line: SaleLine | None, sale: Sale) -> Optional[PlasticProfile]:
    if line is not None and line.warehouse_batch_id and line.warehouse_batch.profile_id:
        return line.warehouse_batch.profile
    if sale.warehouse_batch_id and sale.warehouse_batch.profile_id:
        return sale.warehouse_batch.profile
    return None


def _material_cost_for(*, line: SaleLine | None, sale: Sale, qty: Decimal, profile: Optional[PlasticProfile]) -> Decimal:
    if line is not None and _d(line.cost) > 0:
        return _d(line.cost).quantize(Decimal('0.01'))
    if line is None and _d(sale.cost) > 0:
        return _d(sale.cost).quantize(Decimal('0.01'))
    if profile is not None and profile.cost_price is not None and _d(profile.cost_price) > 0:
        return (qty * _d(profile.cost_price)).quantize(Decimal('0.01'))
    return Decimal('0')


def _row_from_parts(*, sale: Sale, line: SaleLine | None, qty: Decimal) -> SalePnlRow:
    profile = _resolve_profile(line=line, sale=sale)
    material = _material_cost_for(line=line, sale=sale, qty=qty, profile=profile)
    unit_other = other_expenses_total(profile) if profile is not None else Decimal('0')
    product_other = (qty * unit_other).quantize(Decimal('0.01'))
    unit_material = _unit_material_from_cost(qty, material)
    if profile is not None and unit_material <= 0 and profile.cost_price is not None:
        unit_material = _d(profile.cost_price).quantize(Decimal('0.0001'))
    pname = (profile.name if profile else '') or (line.product if line else sale.product) or ''
    pid = profile.pk if profile else None
    return SalePnlRow(
        sale_id=sale.pk,
        sale_date=sale.date,
        profile_id=pid,
        profile_name=pname.strip(),
        product_name=((line.product if line else sale.product) or '').strip(),
        quantity=qty,
        material_cost=material,
        unit_material_cost=unit_material,
        unit_other_expenses=unit_other,
        product_other_cost=product_other,
        profile=profile,
    )


def iter_sale_pnl_rows(scope: AnalyticsScope) -> Iterator[SalePnlRow]:
    from .reporting import _analytics_sale_q, trend_bounds

    sq = _analytics_sale_q(scope)
    start, end = trend_bounds(scope)
    sales = (
        Sale.objects.filter(sq, date__gte=start, date__lte=end)
        .select_related('warehouse_batch__profile')
        .order_by('date', 'id')
    )
    sale_ids = list(sales.values_list('pk', flat=True))
    if not sale_ids:
        return iter(())

    lines_by_sale: dict[int, list[SaleLine]] = {}
    for line in (
        SaleLine.objects.filter(sale_id__in=sale_ids)
        .select_related('warehouse_batch__profile', 'sale')
        .order_by('id')
    ):
        lines_by_sale.setdefault(line.sale_id, []).append(line)

    def _gen():
        for sale in sales:
            line_rows = lines_by_sale.get(sale.pk) or []
            if line_rows:
                for line in line_rows:
                    yield _row_from_parts(sale=sale, line=line, qty=_d(line.quantity))
            else:
                yield _row_from_parts(sale=sale, line=None, qty=_qty_from_sale(sale))

    return _gen()


def aggregate_sale_pnl(scope: AnalyticsScope) -> dict[str, Decimal]:
    sales_cost = Decimal('0')
    product_other = Decimal('0')
    for row in iter_sale_pnl_rows(scope):
        sales_cost += row.material_cost
        product_other += row.product_other_cost
    return {
        'sales_cost_total': sales_cost.quantize(Decimal('0.01')),
        'product_other_expenses_total': product_other.quantize(Decimal('0.01')),
    }


def profile_extra_breakdown(profile: Optional[PlasticProfile]) -> dict[str, str]:
    from config.api_numbers import api_decimal_str

    if profile is None:
        return {name: '0' for name in EXTRA_FIELD_NAMES}
    return {name: api_decimal_str(_d(getattr(profile, name, 0))) or '0' for name in EXTRA_FIELD_NAMES}
