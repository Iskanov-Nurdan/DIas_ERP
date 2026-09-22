"""Прочие расходы: список, создание, accept/reject, агрегация в P&L."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError as DRFValidationError

from config.api_numbers import api_decimal_str

from .models import AnalyticsOtherExpense
from .services import AnalyticsScope, Period


def period_date_bounds(period: Period) -> tuple[date, date]:
    """Границы периода по календарной дате (year / month / day)."""
    if period.month is not None and period.day is not None:
        d = date(period.year, period.month, period.day)
        return d, d
    if period.month is not None:
        last = monthrange(period.year, period.month)[1]
        return date(period.year, period.month, 1), date(period.year, period.month, last)
    return date(period.year, 1, 1), date(period.year, 12, 31)


def _d(v) -> Decimal:
    if v is None:
        return Decimal('0')
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def other_expense_list_q(period: Period) -> Q:
    start, end = period_date_bounds(period)
    return Q(
        date__gte=start,
        date__lte=end,
        status__in=(AnalyticsOtherExpense.STATUS_PENDING, AnalyticsOtherExpense.STATUS_ACCEPTED),
    )


def scope_date_bounds(scope: AnalyticsScope) -> tuple[date, date]:
    """Те же границы периода, что у summary/trends."""
    p = scope.period
    if scope.date_from or scope.date_to:
        start = scope.date_from or scope.date_to or date.today()
        end = scope.date_to or scope.date_from or start
        if start > end:
            start, end = end, start
        return start, end
    return period_date_bounds(p)


def accepted_other_expenses_q(scope: AnalyticsScope) -> Q:
    start, end = scope_date_bounds(scope)
    return Q(
        status=AnalyticsOtherExpense.STATUS_ACCEPTED,
        date__gte=start,
        date__lte=end,
    )


def sum_accepted_other_expenses(scope: AnalyticsScope) -> Decimal:
    agg = AnalyticsOtherExpense.objects.filter(accepted_other_expenses_q(scope)).aggregate(
        s=Sum('amount'),
    )
    return _d(agg['s'])


def serialize_other_expense(row: AnalyticsOtherExpense) -> dict[str, Any]:
    return {
        'id': row.pk,
        'name': row.name,
        'amount': api_decimal_str(_d(row.amount)),
        'date': row.date.isoformat(),
        'status': row.status,
    }


def list_other_expenses(period: Period) -> dict[str, Any]:
    qs = AnalyticsOtherExpense.objects.filter(other_expense_list_q(period)).order_by('-date', '-id')
    return {'items': [serialize_other_expense(r) for r in qs]}


def create_other_expense(*, name: str, amount: Decimal, expense_date: date, user) -> AnalyticsOtherExpense:
    nm = (name or '').strip()
    if not nm:
        raise DRFValidationError({'name': ['Укажите наименование.']})
    amt = _d(amount)
    if amt <= 0:
        raise DRFValidationError({'amount': ['Сумма должна быть больше нуля.']})
    if expense_date is None:
        raise DRFValidationError({'date': ['Укажите дату расхода.']})
    return AnalyticsOtherExpense.objects.create(
        name=nm[:255],
        amount=amt.quantize(Decimal('0.01')),
        date=expense_date,
        status=AnalyticsOtherExpense.STATUS_PENDING,
        created_by=user if getattr(user, 'pk', None) else None,
    )


def accept_other_expense(pk: int) -> tuple[AnalyticsOtherExpense | None, str | None]:
    """(row, error_code) error_code: not_found | already_accepted."""
    row = AnalyticsOtherExpense.objects.filter(pk=pk).first()
    if row is None:
        return None, 'not_found'
    if row.status == AnalyticsOtherExpense.STATUS_ACCEPTED:
        return row, 'already_accepted'
    row.status = AnalyticsOtherExpense.STATUS_ACCEPTED
    row.save(update_fields=['status'])
    return row, None


def reject_other_expense(pk: int) -> bool:
    """False если не найдено."""
    deleted, _ = AnalyticsOtherExpense.objects.filter(pk=pk).delete()
    return bool(deleted)
