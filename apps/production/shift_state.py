"""Состояние смены по истории линии (LineHistory)."""
from __future__ import annotations

from typing import List, Optional

from .models import Line, LineHistory, Shift


def line_shift_is_open(line: Line, *, histories: Optional[List[LineHistory]] = None) -> bool:
    if histories is not None:
        last = histories[0] if histories else None
    else:
        last = LineHistory.objects.filter(line=line).order_by('-date', '-time', '-id').first()
    if not last:
        return False
    return last.action != LineHistory.ACTION_CLOSE


def line_current_shift_open_event(
    line: Line,
    *,
    histories: Optional[List[LineHistory]] = None,
) -> Optional[LineHistory]:
    """
    Событие открытия текущей открытой смены: последнее OPEN до «хвоста» без предшествующего CLOSE.
    Если смена закрыта — None.
    histories — список записей по линии от новых к старым (как из order_by -date -time -id).
    """
    if histories is not None:
        rows = histories
    else:
        rows = list(LineHistory.objects.filter(line=line).order_by('-date', '-time', '-id')[:500])
    if not rows:
        return None
    if rows[0].action == LineHistory.ACTION_CLOSE:
        return None
    for row in rows:
        if row.action == LineHistory.ACTION_OPEN:
            return row
        if row.action == LineHistory.ACTION_CLOSE:
            break
    return None


def line_current_shift_params_event(
    line: Line,
    *,
    histories: Optional[List[LineHistory]] = None,
) -> Optional[LineHistory]:
    """
    Актуальные height/width/angle_deg открытой смены: последнее событие open или params_update
    в текущей сессии (shift_pause / shift_resume не несут размеров — пропускаются).
    """
    if not line_shift_is_open(line, histories=histories):
        return None
    if histories is not None:
        rows = histories
    else:
        rows = list(LineHistory.objects.filter(line=line).order_by('-date', '-time', '-id')[:500])
    if not rows or rows[0].action == LineHistory.ACTION_CLOSE:
        return None
    for row in rows:
        if row.action == LineHistory.ACTION_CLOSE:
            break
        if row.action in (LineHistory.ACTION_OPEN, LineHistory.ACTION_PARAMS_UPDATE):
            return row
    return None


def _line_shift_pause_scan_from_newest(
    rows: List[LineHistory],
) -> tuple[bool, Optional[str]]:
    """
    По истории линии от новых к старым в пределах текущей сессии (до CLOSE):
    (True, reason|None) если последнее по времени среди pause/resume — pause; иначе (False, None).
    """
    for row in rows:
        if row.action == LineHistory.ACTION_CLOSE:
            break
        if row.action == LineHistory.ACTION_SHIFT_RESUME:
            return False, None
        if row.action == LineHistory.ACTION_SHIFT_PAUSE:
            s = (row.comment or '').strip()
            return True, (s or None)
    return False, None


def line_shift_pause_reason(
    line: Line,
    *,
    histories: Optional[List[LineHistory]] = None,
) -> Optional[str]:
    """При активной паузе — причина (может быть None, если в данных пусто); если не в паузе — None."""
    if not line_shift_is_open(line, histories=histories):
        return None
    if histories is not None:
        rows = histories
    else:
        rows = list(LineHistory.objects.filter(line=line).order_by('-date', '-time', '-id')[:500])
    if not rows or rows[0].action == LineHistory.ACTION_CLOSE:
        return None
    paused, reason = _line_shift_pause_scan_from_newest(rows)
    if not paused:
        return None
    return reason


def line_shift_is_paused(
    line: Line,
    *,
    histories: Optional[List[LineHistory]] = None,
) -> bool:
    if not line_shift_is_open(line, histories=histories):
        return False
    if histories is not None:
        rows = histories
    else:
        rows = list(LineHistory.objects.filter(line=line).order_by('-date', '-time', '-id')[:500])
    if not rows or rows[0].action == LineHistory.ACTION_CLOSE:
        return False
    paused, _ = _line_shift_pause_scan_from_newest(rows)
    return paused


def prefetch_line_histories_map(line_ids: list):
    """
    Одним запросом: для каждой линии — список LineHistory от новых к старым.
    Возвращает dict[line_id, list[LineHistory]].
    """
    if not line_ids:
        return {}
    from collections import defaultdict

    qs = (
        LineHistory.objects.filter(line_id__in=line_ids)
        .select_related('user', 'line')
        .order_by('line_id', '-date', '-time', '-id')
    )
    out = defaultdict(list)
    for h in qs:
        out[h.line_id].append(h)
    return dict(out)


def line_history_audit_shift_context(hist: LineHistory):
    line_id = hist.line_id
    if not line_id:
        return None, None, None
    ln = Line.objects.filter(pk=line_id).first()
    if not ln:
        return None, line_id, None
    sh = Shift.objects.filter(line_id=line_id, closed_at__isnull=True).order_by('-opened_at').first()
    shift_id = sh.pk if sh else None
    ev = line_current_shift_open_event(ln)
    open_ev_id = ev.id if ev else None
    return shift_id, line_id, open_ev_id


def shift_instance_audit_context(shift):
    line_id = shift.line_id
    open_ev_id = None
    if line_id:
        ln = Line.objects.filter(pk=line_id).first()
        if ln:
            ev = line_current_shift_open_event(ln)
            open_ev_id = ev.id if ev else None
    return shift.pk, line_id, open_ev_id
