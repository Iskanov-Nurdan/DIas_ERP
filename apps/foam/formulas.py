"""Формулы расчёта выхода — см. BACKEND_FOAM_REQUIREMENTS.md §1. Не доверять числам с фронта."""
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .constants import CUBE_HEIGHT_CM, CUBE_VOLUME_M3, LOSS_RATE

_ONE_DP = Decimal('0.1')


def usable_kg(input_kg: Decimal) -> Decimal:
    return input_kg * (Decimal('1') - LOSS_RATE)


def cube_weight_kg(grade_min_kg_m3: Decimal, grade_max_kg_m3: Decimal) -> Decimal:
    mid_density = (grade_min_kg_m3 + grade_max_kg_m3) / Decimal('2')
    return mid_density * CUBE_VOLUME_M3


def cube_output_qty(input_kg: Decimal, grade_min_kg_m3: Decimal, grade_max_kg_m3: Decimal) -> Decimal:
    weight = cube_weight_kg(grade_min_kg_m3, grade_max_kg_m3)
    if weight <= 0:
        return Decimal('0')
    qty = usable_kg(input_kg) / weight
    return qty.quantize(_ONE_DP, rounding=ROUND_HALF_UP)


def granule_output_qty(input_kg: Decimal) -> Decimal:
    return usable_kg(input_kg).quantize(_ONE_DP, rounding=ROUND_HALF_UP)


def sheets_from_cut(thickness_cm: int, cubes_qty: Decimal) -> int:
    sheets_per_cube = CUBE_HEIGHT_CM // int(thickness_cm)
    total = (Decimal(sheets_per_cube) * cubes_qty).to_integral_value(rounding=ROUND_FLOOR)
    return int(total)
