"""
Человекочитаемые summary/description для журнала действий (список смены, деталь).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, Optional, Type

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

# Не показывать в diff / create-снимке
AUDIT_DIFF_SKIP_ALWAYS = frozenset(
    {'created_at', 'updated_at', 'modified_at', 'password', 'last_login'}
)
AUDIT_EMPTY_SKIP_FIELDS = frozenset(
    {
        'comment',
        'notes',
        'supplier_name',
        'supplier_batch_number',
        'supplier_phone',
        'messenger',
        'email',
        'contact',
        'session_title',
    }
)

_TECH_ENDPOINT_RE = re.compile(
    r'\b(?:GET|POST|PUT|PATCH|DELETE)\s+/api/[^\s]+',
    re.IGNORECASE,
)
_TECH_ENTITY_RE = re.compile(
    r'\b[a-z_]+\.[a-z_]+\s+#\d+',
    re.IGNORECASE,
)
_ISO_IN_PARENS_RE = re.compile(
    r'\(\d{4}-\d{2}-\d{2}T[\d:.+-Z]+\)',
)


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def should_skip_audit_field(name: str, value: Any) -> bool:
    if name in AUDIT_DIFF_SKIP_ALWAYS:
        return True
    if name in AUDIT_EMPTY_SKIP_FIELDS and _is_empty_value(value):
        return True
    if name.startswith('supplier_') and _is_empty_value(value):
        return True
    return False


def format_audit_datetime(value: Any) -> Optional[str]:
    if value is None or value == '':
        return None
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime('%d.%m.%Y')
    elif isinstance(value, time):
        return value.strftime('%H:%M')
    elif isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed:
            dt = parsed
        else:
            d = parse_date(value)
            if d:
                return d.strftime('%d.%m.%Y')
            return value
    else:
        return str(value)
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime('%d.%m.%Y %H:%M')


def _line_label_from_shift(shift) -> Optional[str]:
    if shift.line_id and getattr(shift, 'line', None):
        return shift.line.name
    snap = (getattr(shift, 'line_name_snapshot', None) or '').strip()
    return snap or None


def shift_audit_text(*, endpoint: str, shift, action: str) -> str:
    """Краткий текст для open/close смены (без URL и #id)."""
    line_label = _line_label_from_shift(shift)
    ep = (endpoint or '').lower()
    is_close = 'close' in ep or action == 'update' and 'close' in ep

    if is_close:
        if line_label:
            return f'Смена на линии «{line_label}» закрыта'
        return 'Смена закрыта'

    # open / create
    if line_label:
        return f'Открыта смена на линии «{line_label}»'
    return 'Открыта смена'


def line_history_audit_text(hist) -> str:
    from apps.production.models import LineHistory

    line_label = hist.line_name_snapshot or (
        hist.line.name if getattr(hist, 'line_id', None) and getattr(hist, 'line', None) else '—'
    )
    texts = {
        LineHistory.ACTION_OPEN: f'Линия «{line_label}» открыта',
        LineHistory.ACTION_CLOSE: f'Линия «{line_label}» закрыта',
        LineHistory.ACTION_PARAMS_UPDATE: f'Параметры линии «{line_label}» обновлены',
        LineHistory.ACTION_SHIFT_PAUSE: f'Смена на линии «{line_label}» приостановлена',
        LineHistory.ACTION_SHIFT_RESUME: f'Смена на линии «{line_label}» возобновлена',
    }
    return texts.get(hist.action, f'{hist.get_action_display()} — линия «{line_label}»')


def shift_note_audit_text(note) -> str:
    preview = (getattr(note, 'text', None) or '').strip()
    if preview:
        short = preview if len(preview) <= 80 else f'{preview[:77]}…'
        return f'Заметка к смене: {short}'
    return 'Заметка к смене'


def material_batch_incoming_text(batch) -> str:
    material = getattr(batch, 'material', None)
    name = material.name if material else 'Сырьё'
    qty = batch.quantity_initial
    unit = (getattr(batch, 'unit', None) or 'кг').strip()
    qty_s = str(qty).rstrip('0').rstrip('.') if qty is not None else '—'
    when = format_audit_datetime(getattr(batch, 'received_at', None)) or ''
    if when:
        return f'Приход: {name} — {qty_s} {unit}, {when}'
    return f'Приход: {name} — {qty_s} {unit}'


def _action_verb_ru(action: str) -> str:
    return {
        'create': 'Создан',
        'update': 'Изменён',
        'delete': 'Удалён',
        'restore': 'Восстановлен',
    }.get(action, action)


def human_activity_description(
    *,
    action: str,
    instance: models.Model,
    section: str = '',
    label: str = '',
    model_cls: Optional[Type[models.Model]] = None,
) -> str:
    model_cls = model_cls or instance.__class__
    label_lower = model_cls._meta.label_lower

    if label_lower == 'materials.materialbatch' and action == 'create':
        return material_batch_incoming_text(instance)
    if label_lower == 'materials.rawmaterial':
        name = getattr(instance, 'name', str(instance))
        if action == 'create':
            return f'Добавлено сырьё: {name}'
        if action == 'delete':
            return f'Удалено сырьё: {name}'
        return f'{_action_verb_ru(action)} сырьё: {name}'
    if label_lower == 'sales.client':
        name = getattr(instance, 'name', str(instance))
        if action == 'create':
            return f'Создан клиент: {name}'
        if action == 'delete':
            return f'Удалён клиент: {name}'
        return f'{_action_verb_ru(action)} клиент: {name}'
    if label_lower == 'production.shiftnote' and action == 'create':
        return shift_note_audit_text(instance)
    if label_lower == 'production.shiftcomplaint' and action == 'create':
        return 'Жалоба к смене'
    if label_lower == 'production.shift':
        return shift_audit_text(endpoint='', shift=instance, action=action)

    kind = label or section.lower() or model_cls._meta.verbose_name
    try:
        obj_str = str(instance)
    except Exception:
        obj_str = f'#{getattr(instance, "pk", "?")}'
    obj_str = _strip_technical_noise(obj_str)
    verb = _action_verb_ru(action)
    return f'{verb} {kind}: {obj_str}'


def build_audit_summary(
    *,
    description: str,
    action: str,
    model_cls: type[models.Model],
    after_instance: Optional[models.Model] = None,
    before_instance: Optional[models.Model] = None,
) -> str:
    inst = after_instance or before_instance
    if inst is not None:
        text = human_activity_description(
            action=action,
            instance=inst,
            model_cls=model_cls,
        )
    else:
        text = _strip_technical_noise(description)
    return (text or description or '')[:500]


def _strip_technical_noise(text: str) -> str:
    if not text:
        return text
    t = _TECH_ENDPOINT_RE.sub('', text)
    t = _TECH_ENTITY_RE.sub('', t)
    t = _ISO_IN_PARENS_RE.sub('', t)
    t = re.sub(r'\s+#\d+\b', '', t)
    t = re.sub(r'\s+', ' ', t).strip(' ,:—')
    return t


# Подписи полей для детального diff (опционально в API)
AUDIT_FIELD_LABELS: Dict[str, Dict[str, str]] = {
    'materials.materialbatch': {
        'material': 'Сырьё',
        'quantity_initial': 'Количество',
        'quantity_remaining': 'Остаток',
        'unit': 'Единица',
        'unit_price': 'Цена за ед.',
        'total_price': 'Сумма',
        'supplier_name': 'Поставщик',
        'supplier_batch_number': 'Партия поставщика',
        'received_at': 'Дата прихода',
        'comment': 'Комментарий',
    },
    'materials.rawmaterial': {
        'name': 'Название',
        'unit': 'Единица',
        'min_balance': 'Мин. остаток',
        'is_active': 'Активен',
        'comment': 'Комментарий',
    },
    'sales.client': {
        'name': 'Название',
        'phone': 'Телефон',
        'phone_alt': 'Доп. телефон',
        'client_type': 'Тип',
        'is_active': 'Активен',
        'credit_limit': 'Кредитный лимит',
        'address': 'Адрес',
        'inn': 'ИНН',
    },
    'sales.sale': {
        'order_number': 'Номер',
        'sale_status': 'Статус',
        'client': 'Клиент',
        'revenue': 'Выручка',
        'quantity': 'Количество',
    },
    'sales.order': {
        'order_number': 'Номер заявки',
        'status': 'Статус',
        'client': 'Клиент',
    },
    'production.productionbatch': {
        'product': 'Изделие',
        'pieces': 'Штук',
        'otk_status': 'ОТК',
        'lifecycle_status': 'Этап',
    },
    'production.shift': {
        'status': 'Статус',
        'opened_at': 'Начало',
        'closed_at': 'Конец',
        'comment': 'Комментарий',
        'line': 'Линия',
    },
    'warehouse.warehousebatch': {
        'quantity': 'Количество',
        'status': 'Статус',
        'profile': 'Профиль',
    },
}


def field_labels_for_entity_type(entity_type: str) -> Dict[str, str]:
    if not entity_type:
        return {}
    return dict(AUDIT_FIELD_LABELS.get(entity_type, {}))


def prune_audit_snapshot(snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if snap is None:
        return None
    return {k: v for k, v in snap.items() if not should_skip_audit_field(k, v)}
