"""Касса: оплата (cash/card), смешанная оплата, реквизит."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from rest_framework import serializers

CHECKOUT_METHODS = frozenset({'cash', 'card'})
METHOD_LABELS = {'cash': 'Наличные', 'card': 'Карта'}


def normalize_checkout_method(raw: str | None) -> str:
    """transfer и прочее → card; только cash | card."""
    m = (raw or 'cash').strip().lower()
    if m == 'transfer':
        return 'card'
    if m not in CHECKOUT_METHODS:
        raise serializers.ValidationError(
            {
                'code': 'INVALID_PAYMENT_METHOD',
                'message': 'payment_method: cash | card (перевод учитывается как card).',
                'detail': 'payment_method: cash | card (перевод учитывается как card).',
            }
        )
    return m


@dataclass
class CheckoutPaymentResult:
    payment_type: str
    primary_method: str
    supplemental_amount: Decimal
    splits: list[dict[str, Any]]
    payment_reference: str


def _parse_amount(raw, *, field: str) -> Decimal:
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError(
            {
                'code': 'INVALID_PAID_AMOUNT',
                'message': f'{field} должен быть числом.',
                'detail': f'{field} должен быть числом.',
            }
        ) from exc


def parse_payment_splits(raw) -> list[dict[str, Any]]:
    if raw in (None, ''):
        return []
    if not isinstance(raw, list) or len(raw) < 1:
        raise serializers.ValidationError(
            {
                'code': 'INVALID_PAYMENT_SPLITS',
                'message': 'payment_splits должен быть непустым массивом.',
                'detail': 'payment_splits должен быть непустым массивом.',
            }
        )
    out = []
    for idx, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            raise serializers.ValidationError(
                {
                    'code': 'INVALID_PAYMENT_SPLITS',
                    'message': f'payment_splits[{idx}] должен быть объектом.',
                }
            )
        method = normalize_checkout_method(row.get('payment_method'))
        amount = _parse_amount(row.get('amount'), field=f'payment_splits[{idx}].amount')
        if amount <= 0:
            raise serializers.ValidationError(
                {
                    'code': 'INVALID_PAYMENT_SPLITS',
                    'message': f'payment_splits[{idx}].amount должен быть > 0.',
                }
            )
        out.append({'payment_method': method, 'amount': amount})
    return out


def resolve_checkout_payment(
    *,
    initial: dict,
    sale_total: Decimal,
    order_prepaid: Decimal = Decimal('0'),
    payment_kind_choices: tuple[str, ...],
) -> CheckoutPaymentResult:
    """
    paid_amount — доплата при продаже (не аванс заявки).
    payment_splits — опционально; сумма splits должна совпасть с paid_amount.
    """
    ptype = (initial.get('payment_type') or '').strip().lower()
    paid_input_raw = initial.get('paid_amount')
    paid_input = None
    if paid_input_raw not in (None, ''):
        paid_input = _parse_amount(paid_input_raw, field='paid_amount')

    splits = parse_payment_splits(initial.get('payment_splits'))
    payment_reference = str(initial.get('payment_reference') or '').strip()[:255]

    if not ptype:
        if paid_input is not None and paid_input > 0:
            ptype = 'partial'
        elif splits:
            ptype = 'full' if paid_input is None else 'partial'
        else:
            ptype = 'debt'

    if ptype not in payment_kind_choices:
        raise serializers.ValidationError(
            {
                'code': 'INVALID_PAYMENT_TYPE',
                'message': 'payment_type: full | partial | debt',
            }
        )

    sale_total = Decimal(str(sale_total or 0)).quantize(Decimal('0.01'))
    prepaid = Decimal(str(order_prepaid or 0)).quantize(Decimal('0.01'))
    remaining_due = max(Decimal('0'), sale_total - prepaid).quantize(Decimal('0.01'))

    if splits:
        splits_total = sum((s['amount'] for s in splits), Decimal('0')).quantize(Decimal('0.01'))
        if paid_input is None:
            paid_input = splits_total
        elif abs(paid_input - splits_total) > Decimal('0.01'):
            raise serializers.ValidationError(
                {
                    'code': 'PAYMENT_SPLITS_MISMATCH',
                    'message': 'paid_amount должен равняться сумме payment_splits.',
                }
            )
        primary = 'card' if any(s['payment_method'] == 'card' for s in splits) else 'cash'
    else:
        primary = normalize_checkout_method(initial.get('payment_method'))

    if ptype == 'full':
        supplemental = remaining_due if paid_input is None else paid_input
        if supplemental > remaining_due + Decimal('0.01'):
            raise serializers.ValidationError(
                {
                    'code': 'PAID_AMOUNT_EXCEEDS_REMAINING',
                    'message': 'Доплата превышает остаток по продаже после аванса заявки.',
                }
            )
        if not splits and paid_input is not None and remaining_due > 0:
            if abs(paid_input - remaining_due) > Decimal('0.01') and prepaid == 0:
                if abs(paid_input - sale_total) > Decimal('0.01'):
                    pass
        if prepaid == 0 and paid_input is not None and abs(paid_input - sale_total) > Decimal('0.01'):
            if supplemental > sale_total + Decimal('0.01'):
                raise serializers.ValidationError(
                    {'code': 'PAID_AMOUNT_EXCEEDS_TOTAL', 'message': 'paid_amount превышает total_amount.'}
                )
    elif ptype == 'partial':
        if paid_input is None and not splits:
            raise serializers.ValidationError(
                {'code': 'PAID_AMOUNT_REQUIRED', 'message': 'Для partial укажите paid_amount.'}
            )
        supplemental = paid_input or Decimal('0')
        if supplemental <= 0:
            raise serializers.ValidationError(
                {'code': 'INVALID_PAID_AMOUNT', 'message': 'paid_amount (доплата) должен быть > 0.'}
            )
        if supplemental > remaining_due + Decimal('0.01'):
            raise serializers.ValidationError(
                {'code': 'PAID_AMOUNT_EXCEEDS_REMAINING', 'message': 'Доплата превышает остаток по продаже.'}
            )
    else:
        if paid_input is not None and paid_input > Decimal('0.01'):
            raise serializers.ValidationError(
                {'code': 'PAYMENT_TYPE_CONFLICT', 'message': 'Для debt paid_amount должен быть 0.'}
            )
        supplemental = Decimal('0')
        splits = []

    if not splits and supplemental > 0:
        splits = [{'payment_method': primary, 'amount': supplemental}]

    return CheckoutPaymentResult(
        payment_type=ptype,
        primary_method=primary,
        supplemental_amount=supplemental.quantize(Decimal('0.01')),
        splits=splits,
        payment_reference=payment_reference,
    )
