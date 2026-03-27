"""
Общая логика периода и вспомогательные агрегаты для аналитики.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Tuple

from django.db.models import F, Q, Sum
from django.db import models

from apps.materials.models import Incoming


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
        return self._date_field_q('date')

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


def material_avg_unit_prices() -> dict[int, Decimal]:
    """
    Средневзвешенная цена закупки по каждому сырью (по всем приходам).
    Используется как оценка стоимости списаний.
    """
    rows = (
        Incoming.objects.values('material_id')
        .annotate(
            tq=Sum('quantity'),
            tv=Sum(F('quantity') * F('price_per_unit'), output_field=models.DecimalField()),
        )
    )
    out: dict[int, Decimal] = {}
    for r in rows:
        mid = r['material_id']
        tq = r['tq'] or Decimal('0')
        tv = r['tv'] or Decimal('0')
        if tq and tq > 0:
            out[mid] = (tv / tq).quantize(Decimal('0.0001'))
    return out


def estimate_writeoff_value(writeoffs_qs, prices: dict[int, Decimal]) -> Tuple[Decimal, int]:
    """Оценка стоимости списаний по средней цене закупки (только строки, где цена известна)."""
    total = Decimal('0')
    n = 0
    for w in writeoffs_qs.only('id', 'material_id', 'quantity').iterator():
        p = prices.get(w.material_id)
        if p is None:
            continue
        total += (w.quantity or Decimal('0')) * p
        n += 1
    return total, n
