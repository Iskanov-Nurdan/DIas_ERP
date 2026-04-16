"""Стабильный вывод чисел в JSON API (без скрытого float для денег и норм)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional, Union

from config.decimal_format import format_decimal_plain


def api_decimal_str(value: Optional[Union[Decimal, str, int, float]]) -> Optional[str]:
    if value is None:
        return None
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    plain = format_decimal_plain(d)
    return plain if plain is not None else '0'
