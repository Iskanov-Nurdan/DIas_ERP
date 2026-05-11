"""Контекст смены для строк аудита: shift_id, line_id, session_open_event_id."""
from __future__ import annotations

from typing import Optional, Tuple

from django.http import HttpRequest


def resolve_audit_shift_context(
    request: Optional[HttpRequest],
    user,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Политика: shift_id заполняется по открытой смене пользователя (без closed_at).
    Опционально фронт передаёт X-Audit-Shift-Id (приоритет над авто).
    Если открыто несколько смен (личная + на линии) и заголовка нет — берётся **самая поздно открытая**.
    session_open_event_id — из LineHistory для линии этой смены, если линия задана.
    Вне смены — shift_id=None (события всё равно логируются).
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None, None, None

    shift_id_override = None
    if request is not None:
        raw = request.META.get('HTTP_X_AUDIT_SHIFT_ID') or request.META.get('HTTP_X_SHIFT_ID')
        if raw:
            try:
                shift_id_override = int(raw)
            except (TypeError, ValueError):
                shift_id_override = None

    from apps.production.models import Line, Shift
    from apps.production.shift_state import line_current_shift_open_event

    shift = None
    if shift_id_override is not None:
        shift = Shift.objects.filter(pk=shift_id_override, user=user).first()
    if shift is None:
        shift = (
            Shift.objects.filter(user=user, closed_at__isnull=True)
            .select_related('line')
            .order_by('-opened_at')
            .first()
        )

    if not shift:
        return None, None, None

    line_id = shift.line_id
    open_ev_id = None
    if line_id:
        line = shift.line or Line.objects.filter(pk=line_id).first()
        if line:
            ev = line_current_shift_open_event(line)
            open_ev_id = ev.id if ev else None

    return shift.id, line_id, open_ev_id
