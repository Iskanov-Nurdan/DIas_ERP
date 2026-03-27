"""Человекочитаемый вывод Decimal без лишних нулей справа (10 вместо 10.0000)."""
from decimal import Decimal, InvalidOperation
from typing import Optional, Union


def format_decimal_plain(value: Union[Decimal, None, str, int, float]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return str(value)
    t = d.normalize()
    s = format(t, 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.') or '0'
    return s
