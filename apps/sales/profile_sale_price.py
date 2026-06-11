"""Цена продажи профиля: cost_price + other_expenses_total + markup_amount."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from rest_framework import serializers

from apps.recipes.models import PlasticProfile
from apps.recipes.profile_cost import serialize_profile_cost_price
from apps.recipes.profile_pricing import sale_unit_price, serialize_sale_unit_price

PRICE_TOLERANCE = Decimal('0.01')


def _d(value) -> Decimal:
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def computed_unit_sale_price(profile: PlasticProfile) -> Decimal:
    """unit_price = sale_unit_price профиля."""
    price = sale_unit_price(profile)
    if price is None:
        raise serializers.ValidationError(
            {
                'code': 'PROFILE_COST_NOT_SET',
                'message': f'У профиля «{profile.name}» не рассчитана себестоимость (учёт ОТК).',
                'detail': f'У профиля «{profile.name}» не рассчитана себестоимость (учёт ОТК).',
            }
        )
    return price


def resolve_unit_sale_price(
    profile: PlasticProfile,
    provided: Any,
    *,
    field: str = 'unit_price',
) -> Decimal:
    """Авто-цена или сверка с переданной (допуск 0.01)."""
    computed = computed_unit_sale_price(profile)
    if provided in (None, ''):
        return computed
    try:
        given = Decimal(str(provided)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            {
                'code': 'UNIT_PRICE_MISMATCH',
                'message': f'{field} должен быть числом.',
                'detail': f'{field} должен быть числом.',
            }
        ) from exc
    if abs(given - computed) > PRICE_TOLERANCE:
        raise serializers.ValidationError(
            {
                'code': 'UNIT_PRICE_MISMATCH',
                'message': (
                    f'{field} {given} не совпадает с расчётом '
                    f'sale_unit_price = {computed}.'
                ),
                'detail': (
                    f'{field} {given} не совпадает с расчётом '
                    f'sale_unit_price = {computed}.'
                ),
            }
        )
    return computed


def profile_pricing_payload(profile: PlasticProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            'cost_price': None,
            'markup_amount': None,
            'other_expenses_total': None,
            'unit_sale_price': None,
            'sale_unit_price': None,
        }
    from apps.recipes.profile_pricing import serialize_other_expenses_total

    from config.api_numbers import api_decimal_str

    cost_s = serialize_profile_cost_price(profile.cost_price)
    markup_s = api_decimal_str(_d(profile.markup_amount)) or '0'
    other_s = serialize_other_expenses_total(profile)
    unit_s = serialize_sale_unit_price(profile)
    return {
        'cost_price': cost_s,
        'markup_amount': markup_s,
        'other_expenses_total': other_s,
        'unit_sale_price': unit_s,
        'sale_unit_price': unit_s,
    }


def require_profile_for_batch(wb) -> PlasticProfile:
    if wb is None or not wb.profile_id:
        raise serializers.ValidationError(
            {
                'code': 'PROFILE_REQUIRED',
                'message': 'Партия склада должна быть привязана к профилю (profile_id).',
                'detail': 'Партия склада должна быть привязана к профилю (profile_id).',
            }
        )
    return wb.profile
