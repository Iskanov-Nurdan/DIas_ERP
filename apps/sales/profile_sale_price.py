"""Цена продажи профиля: cost_price + markup_amount (см. BACKEND_PROFILE_COST_PRICE)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from rest_framework import serializers

from apps.recipes.models import PlasticProfile
from apps.recipes.profile_cost import serialize_profile_cost_price

PRICE_TOLERANCE = Decimal('0.01')


def _d(value) -> Decimal:
    if value is None:
        return Decimal('0')
    return Decimal(str(value))


def computed_unit_sale_price(profile: PlasticProfile) -> Decimal:
    """unit_price = cost_price + markup_amount."""
    cost_raw = profile.cost_price
    if cost_raw is None or _d(cost_raw) <= 0:
        raise serializers.ValidationError(
            {
                'code': 'PROFILE_COST_NOT_SET',
                'message': f'У профиля «{profile.name}» не рассчитана себестоимость (учёт ОТК).',
                'detail': f'У профиля «{profile.name}» не рассчитана себестоимость (учёт ОТК).',
            }
        )
    return (_d(cost_raw) + _d(profile.markup_amount)).quantize(Decimal('0.01'))


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
                'code': 'UNIT_PRICE_INVALID',
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
                    f'cost_price + markup_amount = {computed}.'
                ),
                'detail': (
                    f'{field} {given} не совпадает с расчётом '
                    f'cost_price + markup_amount = {computed}.'
                ),
            }
        )
    return computed


def profile_pricing_payload(profile: PlasticProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            'cost_price': None,
            'markup_amount': None,
            'unit_sale_price': None,
        }
    from config.api_numbers import api_decimal_str

    cost_s = serialize_profile_cost_price(profile.cost_price)
    markup_s = api_decimal_str(_d(profile.markup_amount)) or '0'
    unit_s = None
    if cost_s is not None:
        unit_s = api_decimal_str(computed_unit_sale_price(profile))
    return {
        'cost_price': cost_s,
        'markup_amount': markup_s,
        'unit_sale_price': unit_s,
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
