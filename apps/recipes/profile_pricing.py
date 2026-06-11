"""Прочие расходы и sale_unit_price профиля (BACKEND_PLASTIC_PROFILE_BLANK_EXPENSES)."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from config.api_numbers import api_decimal_str

if TYPE_CHECKING:
    from apps.recipes.models import PlasticProfile

EXTRA_FIELD_NAMES = (
    'extra_rubber',
    'extra_label',
    'extra_labor',
    'extra_electricity',
    'extra_repair',
)

READ_ONLY_PRICING_FIELDS = frozenset({
    'cost_price',
    'other_expenses_total',
    'sale_unit_price',
    'blank_name',
})


def _d(value) -> Decimal:
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def other_expenses_total(profile: PlasticProfile) -> Decimal:
    total = sum(_d(getattr(profile, name, 0)) for name in EXTRA_FIELD_NAMES)
    return total.quantize(Decimal('0.01'))


def sale_unit_price(profile: PlasticProfile) -> Optional[Decimal]:
    cost_raw = profile.cost_price
    if cost_raw is None or _d(cost_raw) <= 0:
        return None
    return (
        _d(cost_raw) + other_expenses_total(profile) + _d(profile.markup_amount)
    ).quantize(Decimal('0.01'))


def serialize_other_expenses_total(profile: PlasticProfile) -> str:
    return api_decimal_str(other_expenses_total(profile)) or '0'


def serialize_sale_unit_price(profile: PlasticProfile) -> Optional[str]:
    price = sale_unit_price(profile)
    if price is None:
        return None
    return api_decimal_str(price)
