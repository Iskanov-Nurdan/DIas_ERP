"""Сериализация cost_price профиля (только чтение, null если не рассчитана)."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

from config.api_numbers import api_decimal_str


def serialize_profile_cost_price(value: Optional[Union[Decimal, str, int, float]]) -> Optional[str]:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except Exception:
        return None
    if d <= 0:
        return None
    return api_decimal_str(d)
