"""
Общая логика периода и вспомогательные агрегаты для аналитики.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Q, Sum

from apps.materials.models import MaterialBatch


@dataclass
class Period:
    year: int
    month: Optional[int]
    day: Optional[int]

    def sale_q(self) -> Q:
        q = Q(date__year=self.year)
        if self.month is not None:
            q &= Q(date__month=self.month)
        if self.day is not None:
            q &= Q(date__day=self.day)
        return q

    def incoming_q(self) -> Q:
        """Партии прихода — бизнес-дата received_at."""
        return self._date_field_q('received_at')

    def batch_q(self) -> Q:
        return self._date_field_q('date')

    def warehouse_batch_q(self) -> Q:
        return self._date_field_q('date')

    def writeoff_q(self) -> Q:
        """Списания сырья фиксируются по времени записи."""
        q = Q(created_at__year=self.year)
        if self.month is not None:
            q &= Q(created_at__month=self.month)
        if self.day is not None:
            q &= Q(created_at__day=self.day)
        return q

    def shift_opened_q(self) -> Q:
        q = Q(opened_at__year=self.year)
        if self.month is not None:
            q &= Q(opened_at__month=self.month)
        if self.day is not None:
            q &= Q(opened_at__day=self.day)
        return q

    def recipe_run_q(self) -> Q:
        q = Q(created_at__year=self.year)
        if self.month is not None:
            q &= Q(created_at__month=self.month)
        if self.day is not None:
            q &= Q(created_at__day=self.day)
        return q

    def activity_q(self) -> Q:
        q = Q(created_at__year=self.year)
        if self.month is not None:
            q &= Q(created_at__month=self.month)
        if self.day is not None:
            q &= Q(created_at__day=self.day)
        return q

    def complaint_q(self) -> Q:
        q = Q(created_at__year=self.year)
        if self.month is not None:
            q &= Q(created_at__month=self.month)
        if self.day is not None:
            q &= Q(created_at__day=self.day)
        return q

    def shipment_by_sale_q(self) -> Q:
        q = Q(sale__date__year=self.year)
        if self.month is not None:
            q &= Q(sale__date__month=self.month)
        if self.day is not None:
            q &= Q(sale__date__day=self.day)
        return q

    def _date_field_q(self, field: str) -> Q:
        q = Q(**{f'{field}__year': self.year})
        if self.month is not None:
            q &= Q(**{f'{field}__month': self.month})
        if self.day is not None:
            q &= Q(**{f'{field}__day': self.day})
        return q

    def as_dict(self) -> dict[str, Any]:
        return {'year': self.year, 'month': self.month, 'day': self.day}


def parse_period(request) -> Period:
    year_raw = request.query_params.get('year')
    month_raw = request.query_params.get('month')
    day_raw = request.query_params.get('day')
    try:
        year = int(year_raw) if year_raw else datetime.now().year
    except (ValueError, TypeError):
        year = datetime.now().year
    month: Optional[int]
    day: Optional[int]
    try:
        month = int(month_raw) if month_raw not in (None, '') else None
    except (ValueError, TypeError):
        month = None
    try:
        day = int(day_raw) if day_raw not in (None, '') else None
    except (ValueError, TypeError):
        day = None
    return Period(year=year, month=month, day=day)


def _parse_iso_date(raw) -> Optional[date]:
    if raw in (None, ''):
        return None
    try:
        return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _parse_int_param(raw) -> Optional[int]:
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class AnalyticsScope:
    """Период + фильтры для сводной аналитики (GET analytics/summary/)."""

    period: Period
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    line_id: Optional[int] = None
    client_id: Optional[int] = None
    profile_id: Optional[int] = None
    recipe_id: Optional[int] = None
    batch_id: Optional[int] = None
    status: Optional[str] = None

    def _range_q(self, field: str) -> Q:
        if not self.date_from and not self.date_to:
            return self.period._date_field_q(field)
        q = Q()
        if self.date_from:
            q &= Q(**{f'{field}__gte': self.date_from})
        if self.date_to:
            q &= Q(**{f'{field}__lte': self.date_to})
        return q

    def sale_date_q(self) -> Q:
        return self._range_q('date')

    def batch_date_q(self) -> Q:
        return self._range_q('date')

    def incoming_date_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.incoming_q()
        q = Q()
        if self.date_from:
            q &= Q(received_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(received_at__date__lte=self.date_to)
        return q

    def writeoff_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.writeoff_q()
        q = Q()
        if self.date_from:
            q &= Q(created_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(created_at__date__lte=self.date_to)
        return q

    def shift_opened_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.shift_opened_q()
        q = Q()
        if self.date_from:
            q &= Q(opened_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(opened_at__date__lte=self.date_to)
        return q

    def recipe_run_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.recipe_run_q()
        q = Q()
        if self.date_from:
            q &= Q(created_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(created_at__date__lte=self.date_to)
        return q

    def activity_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.activity_q()
        q = Q()
        if self.date_from:
            q &= Q(created_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(created_at__date__lte=self.date_to)
        return q

    def complaint_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.complaint_q()
        q = Q()
        if self.date_from:
            q &= Q(created_at__date__gte=self.date_from)
        if self.date_to:
            q &= Q(created_at__date__lte=self.date_to)
        return q

    def shipment_by_sale_q(self) -> Q:
        if not self.date_from and not self.date_to:
            return self.period.shipment_by_sale_q()
        q = Q()
        if self.date_from:
            q &= Q(sale__date__gte=self.date_from)
        if self.date_to:
            q &= Q(sale__date__lte=self.date_to)
        return q

    def warehouse_batch_q(self) -> Q:
        return self._range_q('date')

    def as_period_dict(self) -> dict[str, Any]:
        d = self.period.as_dict()
        d['date_from'] = self.date_from.isoformat() if self.date_from else None
        d['date_to'] = self.date_to.isoformat() if self.date_to else None
        d['line_id'] = self.line_id
        d['client_id'] = self.client_id
        d['profile_id'] = self.profile_id
        d['recipe_id'] = self.recipe_id
        d['batch_id'] = self.batch_id
        d['status'] = self.status
        return d


def sale_scope_q(scope: AnalyticsScope) -> Q:
    q = scope.sale_date_q()
    if scope.client_id:
        q &= Q(client_id=scope.client_id)
    if scope.line_id:
        q &= Q(warehouse_batch__source_batch__line_id=scope.line_id)
    if scope.profile_id:
        q &= Q(warehouse_batch__profile_id=scope.profile_id)
    if scope.batch_id:
        q &= Q(warehouse_batch__source_batch_id=scope.batch_id)
    return q


def production_batch_scope_q(scope: AnalyticsScope) -> Q:
    q = scope.batch_date_q()
    if scope.line_id:
        q &= Q(line_id=scope.line_id)
    if scope.profile_id:
        q &= Q(profile_id=scope.profile_id)
    if scope.recipe_id:
        q &= Q(recipe_id=scope.recipe_id)
    if scope.batch_id:
        q &= Q(id=scope.batch_id)
    if scope.status:
        q &= Q(otk_status=scope.status)
    return q


def recipe_run_scope_q(scope: AnalyticsScope) -> Q:
    q = scope.recipe_run_q()
    if scope.line_id:
        q &= Q(line_id=scope.line_id)
    if scope.recipe_id:
        q &= Q(recipe_id=scope.recipe_id)
    return q


def parse_analytics_scope(request) -> AnalyticsScope:
    p = parse_period(request)
    qp = request.query_params
    st = qp.get('status')
    if st == '':
        st = None
    return AnalyticsScope(
        period=p,
        date_from=_parse_iso_date(qp.get('date_from')),
        date_to=_parse_iso_date(qp.get('date_to')),
        line_id=_parse_int_param(qp.get('line_id')),
        client_id=_parse_int_param(qp.get('client_id')),
        profile_id=_parse_int_param(qp.get('profile_id')),
        recipe_id=_parse_int_param(qp.get('recipe_id')),
        batch_id=_parse_int_param(qp.get('batch_id')),
        status=st,
    )


def material_avg_unit_prices() -> dict[int, Decimal]:
    """
    Средневзвешенная цена закупки по каждому сырью (по сумме партий прихода).
    """
    rows = MaterialBatch.objects.values('material_id').annotate(
        tq=Sum('quantity_initial'),
        tv=Sum('total_price'),
    )
    out: dict[int, Decimal] = {}
    for r in rows:
        mid = r['material_id']
        tq = r['tq'] or Decimal('0')
        tv = r['tv'] or Decimal('0')
        if tq and tq > 0:
            out[mid] = (tv / tq).quantize(Decimal('0.0001'))
    return out
