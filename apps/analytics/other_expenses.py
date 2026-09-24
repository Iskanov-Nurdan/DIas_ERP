"""Прочие расходы: список, создание, accept/reject, агрегация в P&L."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError as DRFValidationError

from config.api_numbers import api_decimal_str

from .models import (
    KIND_EXPENSE,
    KIND_INCOME,
    PRODUCT_LINE_FOAM,
    PRODUCT_LINE_GENERAL,
    PRODUCT_LINE_PROFILE,
    AnalyticsExpenseCategory,
    AnalyticsOtherExpense,
)
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
    agg = AnalyticsOtherExpense.objects.filter(accepted_other_expenses_q(scope)).filter(
        kind=KIND_EXPENSE, recurring=False,
    ).aggregate(
        s=Sum('amount'),
    )
    return _d(agg['s'])


def serialize_other_expense(row: AnalyticsOtherExpense) -> dict[str, Any]:
    cat = row.category
    return {
        'id': row.pk,
        'name': row.name,
        'amount': api_decimal_str(_d(row.amount)),
        'date': row.date.isoformat(),
        'status': row.status,
        'kind': row.kind,
        'category_id': row.category_id,
        'category_name': cat.name if cat else '',
        'is_payroll': bool(cat and cat.is_payroll),
        'product_line': row.product_line,
        'comment': row.comment,
        'recurring': row.recurring,
        'recurring_source_id': row.recurring_source_id,
        'created_by_name': (getattr(row.created_by, 'name', '') or '') if row.created_by_id else '',
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_by_name': (getattr(row.updated_by, 'name', '') or '') if row.updated_by_id else '',
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def list_other_expenses(period: Period, *, date_from: date | None = None, date_to: date | None = None) -> dict[str, Any]:
    generate_recurring_occurrences()
    if date_from or date_to:
        start = date_from or date_to
        end = date_to or date_from
        q = Q(date__gte=start, date__lte=end)
    else:
        q = other_expense_list_q(period)
    qs = (
        AnalyticsOtherExpense.objects.filter(q)
        .filter(recurring=False)
        .select_related('category', 'created_by', 'updated_by')
        .order_by('-date', '-id')
    )
    templates = (
        AnalyticsOtherExpense.objects.filter(recurring=True)
        .select_related('category', 'created_by', 'updated_by')
        .order_by('name')
    )
    return {
        'items': [serialize_other_expense(r) for r in qs],
        'recurring_templates': [serialize_other_expense(r) for r in templates],
    }


def _validate_payload(*, amount, expense_date, kind, category_id, product_line):
    amt = _d(amount)
    if amt <= 0:
        raise DRFValidationError({'amount': ['Сумма должна быть больше нуля.']})
    if expense_date is None:
        raise DRFValidationError({'date': ['Укажите дату расхода.']})
    if expense_date > date.today():
        raise DRFValidationError({'date': ['Дата не может быть в будущем.']})
    if kind not in (KIND_EXPENSE, KIND_INCOME):
        raise DRFValidationError({'kind': ['kind: expense | income.']})
    if product_line not in (PRODUCT_LINE_GENERAL, PRODUCT_LINE_PROFILE, PRODUCT_LINE_FOAM):
        raise DRFValidationError({'product_line': ['product_line: general | profile | foam.']})
    category = None
    if category_id not in (None, ''):
        category = AnalyticsExpenseCategory.objects.filter(pk=category_id, is_active=True).first()
        if category is None:
            raise DRFValidationError({'category_id': ['Категория не найдена.']})
        if category.kind != kind:
            raise DRFValidationError({'category_id': ['Категория не подходит к типу записи.']})
    return amt.quantize(Decimal('0.01')), category


def create_other_expense(
    *, name: str, amount: Decimal, expense_date: date, user,
    kind: str = KIND_EXPENSE, category_id=None, product_line: str = PRODUCT_LINE_GENERAL,
    comment: str = '', recurring: bool = False,
) -> AnalyticsOtherExpense:
    amt, category = _validate_payload(
        amount=amount, expense_date=expense_date, kind=kind, category_id=category_id, product_line=product_line,
    )
    nm = (name or '').strip() or (category.name if category else '')
    if not nm:
        raise DRFValidationError({'name': ['Укажите наименование или категорию.']})
    row = AnalyticsOtherExpense.objects.create(
        name=nm[:255],
        amount=amt,
        date=expense_date,
        status=AnalyticsOtherExpense.STATUS_PENDING,
        kind=kind,
        category=category,
        product_line=product_line,
        comment=(comment or '').strip(),
        recurring=bool(recurring),
        created_by=user if getattr(user, 'pk', None) else None,
    )
    if row.recurring:
        generate_recurring_occurrences(template=row)
    return row


def update_other_expense(row: AnalyticsOtherExpense, data: dict, user) -> AnalyticsOtherExpense:
    """Правка записи (кто и когда — updated_by/updated_at + журнал действий во view)."""
    from .services import _parse_iso_date

    kind = data.get('kind', row.kind)
    expense_date = _parse_iso_date(data['date']) if 'date' in data else row.date
    product_line = data.get('product_line', row.product_line)
    amt, category = _validate_payload(
        amount=data.get('amount', row.amount),
        expense_date=expense_date,
        kind=kind,
        category_id=data.get('category_id', row.category_id),
        product_line=product_line,
    )
    row.amount = amt
    row.date = expense_date
    row.kind = kind
    row.category = category
    row.product_line = product_line
    if 'name' in data:
        row.name = (str(data.get('name') or '').strip() or (category.name if category else row.name))[:255]
    if 'comment' in data:
        row.comment = str(data.get('comment') or '').strip()
    if 'recurring' in data:
        row.recurring = bool(data.get('recurring'))
    row.updated_by = user if getattr(user, 'pk', None) else None
    row.save()
    if row.recurring:
        generate_recurring_occurrences(template=row)
    return row


def generate_recurring_occurrences(*, template: AnalyticsOtherExpense | None = None, today: date | None = None) -> int:
    """
    Для каждого регулярного шаблона создаёт pending-запись на каждый месяц от
    месяца шаблона до текущего включительно (день = день шаблона, не больше
    конца месяца). Уникальность (шаблон, месяц) — на уровне БД, повторный вызов
    ничего не задваивает. Сам шаблон в суммы не входит (recurring=True).
    """
    from django.db import IntegrityError, transaction

    today = today or date.today()
    if template is not None:
        templates = [template]
    else:
        templates = list(AnalyticsOtherExpense.objects.filter(recurring=True, recurring_source__isnull=True))
    created = 0
    for t in templates:
        if not t.recurring:
            continue
        existing = set(t.occurrences.values_list('period_key', flat=True))
        y, m = t.date.year, t.date.month
        while (y, m) <= (today.year, today.month):
            key = f'{y:04d}-{m:02d}'
            if key not in existing:
                day = min(t.date.day, monthrange(y, m)[1])
                occ_date = date(y, m, day)
                if occ_date <= today:
                    try:
                        with transaction.atomic():
                            AnalyticsOtherExpense.objects.create(
                                name=t.name, amount=t.amount, date=occ_date,
                                status=AnalyticsOtherExpense.STATUS_PENDING,
                                kind=t.kind, category=t.category, product_line=t.product_line,
                                comment=t.comment, recurring=False, recurring_source=t,
                                period_key=key, created_by=t.created_by,
                            )
                        created += 1
                    except IntegrityError:
                        pass
            m += 1
            if m > 12:
                y, m = y + 1, 1
    return created


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
