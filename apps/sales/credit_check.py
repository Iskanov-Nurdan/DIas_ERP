"""
Сервис проверки кредитного лимита клиента.

Долг = выручка по всем продажам — чистые поступления (оплаты - возвраты денег).
Предоплата учитывается как уменьшение долга.
Возврат денег (refund) уменьшает чистую оплату.

Режимы:
  soft — предупреждение (API возвращает предупреждение, но не блокирует)
  hard — блокировка (API возвращает 422, отгрузка невозможна)
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

from django.db.models import Sum
from django.db.models.functions import Coalesce


@dataclass
class CreditCheckResult:
    client_id: int
    credit_limit: Optional[Decimal]
    current_debt: Decimal
    credit_used: Decimal
    credit_available: Optional[Decimal]
    is_over_limit: bool
    block_mode: str
    warning: Optional[str]
    blocked: bool


def compute_client_debt(client) -> Decimal:
    """
    Текущий долг клиента.
    Долг = сумма выручки (все не-черновик продажи) минус чистые поступления.
    Предоплата сокращает долг. Возврат денег сокращает поступления.
    """
    from .models import Payment, Sale

    total_revenue = (
        Sale.objects.filter(client=client)
        .exclude(sale_status=Sale.STATUS_DRAFT)
        .exclude(sale_status=Sale.STATUS_CANCELED)
        .aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t']
    ) or Decimal('0')

    payments_qs = Payment.objects.filter(client=client, status=Payment.STATUS_ACTIVE)
    total_incoming = (
        payments_qs.filter(
            payment_type__in=[Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE]
        ).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
    ) or Decimal('0')
    total_refunded = (
        payments_qs.filter(payment_type=Payment.TYPE_REFUND)
        .aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
    ) or Decimal('0')

    net_paid = total_incoming - total_refunded
    debt = total_revenue - net_paid
    return max(Decimal('0'), debt)


def check_credit_limit(client, additional_amount: Decimal = Decimal('0')) -> CreditCheckResult:
    """
    Проверить кредитный лимит клиента перед отгрузкой.
    additional_amount — сумма предстоящей отгрузки (может быть 0 для справки).
    """
    current_debt = compute_client_debt(client)
    projected_debt = current_debt + additional_amount
    limit = client.credit_limit
    mode = client.credit_limit_mode or 'soft'

    if limit is None:
        return CreditCheckResult(
            client_id=client.pk,
            credit_limit=None,
            current_debt=current_debt,
            credit_used=Decimal('0'),
            credit_available=None,
            is_over_limit=False,
            block_mode=mode,
            warning=None,
            blocked=False,
        )

    credit_used = projected_debt
    credit_available = max(Decimal('0'), limit - projected_debt)
    is_over = projected_debt > limit

    warning = None
    blocked = False
    if is_over:
        excess = projected_debt - limit
        warning = (
            f'Кредитный лимит клиента {client.name} превышен на {excess:.2f}. '
            f'Лимит: {limit:.2f}, текущий долг: {current_debt:.2f}, '
            f'с данной отгрузкой: {projected_debt:.2f}.'
        )
        if mode == 'hard':
            blocked = True

    return CreditCheckResult(
        client_id=client.pk,
        credit_limit=limit,
        current_debt=current_debt,
        credit_used=credit_used,
        credit_available=credit_available,
        is_over_limit=is_over,
        block_mode=mode,
        warning=warning,
        blocked=blocked,
    )


def credit_check_result_to_dict(result: CreditCheckResult) -> dict:
    from config.api_numbers import api_decimal_str
    return {
        'client_id': result.client_id,
        'credit_limit': api_decimal_str(result.credit_limit) if result.credit_limit is not None else None,
        'current_debt': api_decimal_str(result.current_debt),
        'credit_used': api_decimal_str(result.credit_used),
        'credit_available': api_decimal_str(result.credit_available) if result.credit_available is not None else None,
        'is_over_limit': result.is_over_limit,
        'block_mode': result.block_mode,
        'warning': result.warning,
        'blocked': result.blocked,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HARD ENFORCEMENT — вызывается из сериализатора/вью перед созданием Sale
# ─────────────────────────────────────────────────────────────────────────────

CREDIT_OVERRIDE_ACCESS_KEY = 'credit_limit_override'


def can_override_credit_limit(user) -> bool:
    """
    Пользователь может обойти hard-блокировку, если у него есть
    access_key 'credit_limit_override' (или он admin/system).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False):
        return True
    from apps.accounts.models import UserAccess
    return UserAccess.objects.filter(
        user=user, access_key=CREDIT_OVERRIDE_ACCESS_KEY,
    ).exists()


def enforce_credit_limit(client, additional_amount: Decimal, user=None, force_override: bool = False) -> Optional[str]:
    """
    Проверить лимит и:
      - вернуть warning (str) для soft-режима
      - поднять CreditLimitBlocked для hard-режима (если нет override)
      - вернуть None если всё ок

    Raises:
        CreditLimitBlocked — при hard-блокировке без override.
    """
    result = check_credit_limit(client, additional_amount)
    if not result.is_over_limit:
        return None

    if result.block_mode == 'hard' and not force_override:
        if user is not None and can_override_credit_limit(user):
            return result.warning
        raise CreditLimitBlocked(result.warning or 'Кредитный лимит превышен (hard)')

    return result.warning


class CreditLimitBlocked(Exception):
    """Поднимается при hard-блокировке лимита без override."""
    pass
