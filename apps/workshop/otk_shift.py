"""Привязка учёта ОТК к смене day/night."""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone


def shift_date_for_period(shift_period: str, *, now=None) -> date:
    """Дата смены: ночь до 08:00 — предыдущий календарный день."""
    now = now or timezone.localtime()
    if shift_period == 'night' and now.hour < 8:
        return (now - timedelta(days=1)).date()
    return now.date()


def link_otk_session_to_shift(session, *, shift_period: str, user_ids: list[int | None]):
    """Связать учёт с открытой личной сменой сотрудника (если есть)."""
    from apps.production.models import Shift

    if not shift_period:
        return None
    shift_date = shift_date_for_period(shift_period)
    linked = None
    for uid in user_ids:
        if not uid:
            continue
        row = (
            Shift.objects.filter(
                user_id=uid,
                line_id__isnull=True,
                closed_at__isnull=True,
                opened_at__date=shift_date,
            )
            .order_by('-opened_at')
            .first()
        )
        if row:
            linked = row
            break
    if linked:
        session.shift_id = linked.pk
        session.save(update_fields=['shift_id'])
    return linked
