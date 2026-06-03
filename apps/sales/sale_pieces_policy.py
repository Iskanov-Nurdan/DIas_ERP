"""Политика продаж: только штуки, без упаковок (BACKEND_SALES_SIMPLIFICATION)."""
from __future__ import annotations

from typing import Any

from apps.sales.models import Sale

PACKAGES_GONE_DETAIL = (
    'Продажа в упаковках снята. Используйте unit_type=pieces или не передавайте unit_type.'
)


def reject_packages_unit_type(unit_type: str | None) -> str | None:
    ut = (unit_type or '').strip().lower()
    if ut == Sale.MODE_PACKAGES:
        return PACKAGES_GONE_DETAIL
    return None


def reject_sale_line_packages(row: dict, *, idx: int) -> dict | None:
    if row.get('gp_package_id') not in (None, ''):
        return {
            'field': f'sale_lines[{idx}].gp_package_id',
            'message': 'gp_package_id снят с API. Продажа только в штуках по warehouse_batch.',
        }
    lut = (row.get('unit_type') or '').strip().lower()
    if lut == Sale.MODE_PACKAGES:
        return {
            'field': f'sale_lines[{idx}].unit_type',
            'message': PACKAGES_GONE_DETAIL,
        }
    return None
