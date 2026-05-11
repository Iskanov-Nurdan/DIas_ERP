"""
Снимки моделей, дифф полей, маскирование PII и постановка записи в журнал после commit.
payload_version=1 — контракт с массивом changes; 0 — наследие (только description).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from django.db import IntegrityError, models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = 1

_MISSING = object()


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            return value.isoformat()
        return timezone.make_aware(value, timezone.get_current_timezone()).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _mask_field_value(field_name: str, value: Any) -> Any:
    if value is None or value == '':
        return value
    lower = field_name.lower()
    if 'password' in lower or 'secret' in lower or 'token' in lower:
        return None
    if not isinstance(value, str):
        return value
    if 'email' in lower:
        if '@' not in value:
            return '***'
        local, _, domain = value.partition('@')
        if len(local) <= 2:
            return f'*@{domain}'
        return f'{local[0]}***@{domain}'
    if 'phone' in lower or lower.endswith('_tel'):
        digits = ''.join(c for c in value if c.isdigit())
        if len(digits) < 4:
            return '***'
        return f'***{digits[-4:]}'
    return value


def _audit_field_value(field_name: str, value: Any, *, mask_pii: bool) -> Any:
    if value is None or value == '':
        return value
    lower = (field_name or '').lower()
    if 'password' in lower or 'secret' in lower or 'token' in lower:
        return None
    if mask_pii:
        return _mask_field_value(field_name, value)
    return value


def _looks_like_masked_phone_store_value(s: Any) -> bool:
    if s is None:
        return False
    t = s.strip() if isinstance(s, str) else str(s).strip()
    if not t:
        return False
    if t == '***':
        return True
    return bool(re.fullmatch(r'\*+\d{0,12}', t))


def _client_phone_candidates_from_request(request) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {'phone': None, 'phone_alt': None}
    if request is None or not hasattr(request, 'data'):
        return out
    d = request.data
    if d is None or not hasattr(d, 'get'):
        return out

    def first_clean(*keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            if v is None:
                continue
            t = v.strip() if isinstance(v, str) else str(v).strip()
            if not t or _looks_like_masked_phone_store_value(t):
                continue
            return t
        return None

    out['phone'] = first_clean(
        'phone', 'phone_number', 'tel', 'phone_main', 'contact_phone', 'mobile', 'phone_raw',
    )
    out['phone_alt'] = first_clean(
        'phone_alt', 'second_phone', 'phone_secondary', 'phone2', 'alt_phone', 'phone_alt_raw',
    )
    return out


def apply_request_overrides_to_audit_snapshots(
    request,
    model_cls: type[models.Model],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        label = model_cls._meta.label_lower
    except Exception:
        return before, after
    if label != 'sales.client':
        return before, after

    cand = _client_phone_candidates_from_request(request)

    def patch(snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if snap is None:
            return None
        out = dict(snap)
        for field in ('phone', 'phone_alt'):
            raw = cand.get(field)
            if raw is None:
                continue
            cur = out.get(field)
            cur_s = cur if isinstance(cur, str) else (str(cur) if cur is not None else '')
            cur_s = cur_s.strip()
            if not cur_s:
                out[field] = raw
            elif _looks_like_masked_phone_store_value(cur_s):
                out[field] = raw
        return out

    return patch(before), patch(after)


def _field_type_label(field: models.Field) -> str:
    if getattr(field, 'choices', None):
        return 'enum'
    if field.get_internal_type() == 'ForeignKey':
        return 'fk'
    if field.get_internal_type() in ('JSONField',):
        return 'json'
    if field.get_internal_type() in ('BinaryField', 'FileField', 'ImageField'):
        return 'file_meta'
    return 'scalar'


def _raw_local_value(instance: models.Model, field: models.Field) -> Any:
    name = field.name
    if field.get_internal_type() == 'ForeignKey':
        return getattr(instance, f'{name}_id', None)
    if field.get_internal_type() in ('FileField', 'ImageField'):
        f = getattr(instance, name, None)
        if not f:
            return None
        return {'name': getattr(f, 'name', str(f))}
    if field.get_internal_type() == 'BinaryField':
        return '[binary]'
    val = getattr(instance, name, None)
    return val


def instance_to_snapshot(
    instance: models.Model,
    *,
    ignore_fields: Optional[Set[str]] = None,
    only_fields: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    ignore = set(ignore_fields or ())
    ignore.update({'password', 'last_login'})
    out: Dict[str, Any] = {}
    opts = instance._meta
    for field in opts.local_concrete_fields:
        if field.name in ignore:
            continue
        if only_fields is not None and field.name not in only_fields:
            continue
        out[field.name] = _json_safe(_raw_local_value(instance, field))
    return out


def _fk_display(model_class, field: models.ForeignKey, pk: Any) -> Optional[str]:
    if pk is None:
        return None
    rel = field.related_model
    try:
        obj = rel.objects.get(pk=pk)
        return str(obj)
    except rel.DoesNotExist:
        return f'id={pk}'


def _choice_display(instance: models.Model, field_name: str, raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        meth = getattr(instance, f'get_{field_name}_display')
        return str(meth())
    except (AttributeError, TypeError, ValueError):
        return None


def _enum_label(field: Optional[models.Field], raw: Any) -> Optional[str]:
    if field is None or raw is None:
        return None
    for val, label in getattr(field, 'flatchoices', []) or []:
        if val == raw:
            return str(label)
    return None


def _json_structural_diff(
    old: Any,
    new: Any,
    path: str,
) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        keys = set(old) | set(new)
        for k in sorted(keys, key=lambda x: str(x)):
            p = f'{path}.{k}' if path else str(k)
            ov = old.get(k, _MISSING)
            nv = new.get(k, _MISSING)
            if ov is _MISSING and nv is not _MISSING:
                changes.append(
                    {
                        'field': str(k),
                        'path': p,
                        'type': 'json',
                        'old': None,
                        'new': _json_safe(nv),
                    }
                )
            elif nv is _MISSING and ov is not _MISSING:
                changes.append(
                    {
                        'field': str(k),
                        'path': p,
                        'type': 'json',
                        'old': _json_safe(ov),
                        'new': None,
                    }
                )
            elif ov != nv:
                if isinstance(ov, dict) and isinstance(nv, dict):
                    changes.extend(_json_structural_diff(ov, nv, p))
                else:
                    changes.append(
                        {
                            'field': str(k),
                            'path': p,
                            'type': 'json',
                            'old': _json_safe(ov),
                            'new': _json_safe(nv),
                        }
                    )
        return changes
    if old != new:
        changes.append(
            {
                'field': path.split('.')[-1] if path else 'value',
                'path': path or 'value',
                'type': 'json',
                'old': _json_safe(old),
                'new': _json_safe(new),
            }
        )
    return changes


def build_field_changes(
    *,
    action: str,
    model_class: type[models.Model],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    before_instance: Optional[models.Model] = None,
    after_instance: Optional[models.Model] = None,
    mask_pii: bool = False,
) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    opts = model_class._meta
    field_by_name = {f.name: f for f in opts.local_concrete_fields}

    if action == 'create' and after:
        for name, new_val in sorted(after.items()):
            if name in ('password', 'last_login'):
                continue
            field = field_by_name.get(name)
            ftype = _field_type_label(field) if field else 'scalar'
            entry: Dict[str, Any] = {
                'field': name,
                'path': name,
                'type': ftype,
                'new': _audit_field_value(name, new_val, mask_pii=mask_pii),
            }
            disp_e = _enum_label(field, new_val)
            if disp_e is not None:
                entry['new_display'] = disp_e
            elif after_instance is not None:
                disp = _choice_display(after_instance, name, new_val)
                if disp is not None:
                    entry['new_display'] = disp
            if ftype == 'fk' and field and isinstance(field, models.ForeignKey):
                entry['new_display'] = _fk_display(model_class, field, new_val)
            changes.append(entry)
        return changes

    if action == 'delete' and before:
        for name, old_val in sorted(before.items()):
            if name in ('password', 'last_login'):
                continue
            field = field_by_name.get(name)
            ftype = _field_type_label(field) if field else 'scalar'
            entry = {
                'field': name,
                'path': name,
                'type': ftype,
                'old': _audit_field_value(name, old_val, mask_pii=mask_pii),
            }
            disp_e = _enum_label(field, old_val)
            if disp_e is not None:
                entry['old_display'] = disp_e
            elif before_instance is not None:
                disp = _choice_display(before_instance, name, old_val)
                if disp is not None:
                    entry['old_display'] = disp
            if ftype == 'fk' and field and isinstance(field, models.ForeignKey):
                entry['old_display'] = _fk_display(model_class, field, old_val)
            changes.append(entry)
        return changes

    # update
    if after is None:
        return changes
    if before is None:
        for name, new_val in sorted(after.items()):
            if name in ('password', 'last_login'):
                continue
            field = field_by_name.get(name)
            ftype = _field_type_label(field) if field else 'scalar'
            entry: Dict[str, Any] = {
                'field': name,
                'path': name,
                'type': ftype,
                'new': _audit_field_value(name, new_val, mask_pii=mask_pii),
            }
            disp_e = _enum_label(field, new_val)
            if disp_e is not None:
                entry['new_display'] = disp_e
            elif after_instance is not None:
                disp = _choice_display(after_instance, name, new_val)
                if disp is not None:
                    entry['new_display'] = disp
            if ftype == 'fk' and field and isinstance(field, models.ForeignKey):
                entry['new_display'] = entry.get('new_display') or _fk_display(
                    model_class, field, new_val
                )
            changes.append(entry)
        return changes
    keys = set(before) | set(after)
    for name in sorted(keys):
        if name in ('password', 'last_login'):
            continue
        old_val, new_val = before.get(name), after.get(name)
        if old_val == new_val:
            continue
        field = field_by_name.get(name)
        ftype = _field_type_label(field) if field else 'scalar'
        if ftype == 'json':
            od = old_val if isinstance(old_val, dict) else {}
            nd = new_val if isinstance(new_val, dict) else {}
            if not isinstance(old_val, dict) or not isinstance(new_val, dict):
                changes.append(
                    {
                        'field': name,
                        'path': name,
                        'type': 'json',
                        'raw_json': True,
                        'old': _audit_field_value(name, _json_safe(old_val), mask_pii=mask_pii),
                        'new': _audit_field_value(name, _json_safe(new_val), mask_pii=mask_pii),
                    }
                )
            else:
                for sub in _json_structural_diff(od, nd, name):
                    sub['old'] = _audit_field_value(sub['field'], sub.get('old'), mask_pii=mask_pii)
                    sub['new'] = _audit_field_value(sub['field'], sub.get('new'), mask_pii=mask_pii)
                    changes.append(sub)
            continue

        entry: Dict[str, Any] = {
            'field': name,
            'path': name,
            'type': ftype,
            'old': _audit_field_value(name, old_val, mask_pii=mask_pii),
            'new': _audit_field_value(name, new_val, mask_pii=mask_pii),
        }
        if ftype == 'enum':
            lo = _enum_label(field, old_val)
            ln = _enum_label(field, new_val)
            if lo is not None:
                entry['old_display'] = lo
            elif before_instance is not None:
                d = _choice_display(before_instance, name, old_val)
                if d is not None:
                    entry['old_display'] = d
            if ln is not None:
                entry['new_display'] = ln
            elif after_instance is not None:
                d = _choice_display(after_instance, name, new_val)
                if d is not None:
                    entry['new_display'] = d
        if ftype == 'fk' and field and isinstance(field, models.ForeignKey):
            entry['old_display'] = _fk_display(model_class, field, old_val)
            entry['new_display'] = _fk_display(model_class, field, new_val)
        changes.append(entry)
    return changes


def entity_type_for(instance: models.Model) -> str:
    opts = instance._meta
    return f'{opts.app_label}.{opts.model_name}'


def entity_type_for_model(model_cls: type[models.Model]) -> str:
    opts = model_cls._meta
    return f'{opts.app_label}.{opts.model_name}'


def schedule_user_activity(
    *,
    user,
    action: str,
    section: str,
    description: str,
    summary: str,
    entity_type: str,
    entity_id: str,
    request,
    payload: Dict[str, Any],
    payload_version: int = PAYLOAD_VERSION,
    shift_id: Optional[int] = None,
    line_id: Optional[int] = None,
    session_open_event_id: Optional[int] = None,
    actor_role_snapshot: str = '',
) -> None:
    from .models import AuditOutbox, UserActivity

    rid = ''
    ip = None
    ua = ''
    if request is not None:
        rid = getattr(request, 'request_id', '') or ''
        ua = (request.META.get('HTTP_USER_AGENT') or '')[:2000]
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            ip = xff.split(',')[0].strip()[:45]
        else:
            ip = (request.META.get('REMOTE_ADDR') or '')[:45] or None

    row = {
        'user_id': user.pk if getattr(user, 'is_authenticated', False) else None,
        'action': action,
        'section': section,
        'description': description,
        'summary': summary or description[:500],
        'shift_id': shift_id,
        'line_id': line_id,
        'session_open_event_id': session_open_event_id,
        'request_id': rid,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'payload': payload,
        'payload_version': payload_version,
        'actor_role_snapshot': actor_role_snapshot[:200],
        'client_ip': (ip or '')[:45],
        'user_agent': ua,
    }

    def _commit_write():
        try:
            UserActivity.objects.create(**row)
        except IntegrityError:
            logger.debug('audit idempotent skip request_id=%s entity=%s', rid, entity_type)
        except Exception as exc:
            logger.exception('audit write failed: %s', exc)
            try:
                AuditOutbox.objects.create(payload=row, last_error=str(exc)[:2000])
            except Exception as ob_exc:
                logger.exception('audit outbox failed: %s', ob_exc)

    transaction.on_commit(_commit_write)


_DEFAULT_IGNORE = ('updated_at', 'modified_at')


def schedule_entity_audit(
    *,
    user,
    request,
    section: str,
    description: str,
    action: str,
    model_cls: type[models.Model],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    before_instance: Optional[models.Model] = None,
    after_instance: Optional[models.Model] = None,
    ignore_fields: Optional[Set[str]] = None,
    only_fields: Optional[Set[str]] = None,
    payload_extra: Optional[Dict[str, Any]] = None,
    shift_context: Optional[Tuple[Optional[int], Optional[int], Optional[int]]] = None,
) -> None:
    if not user or not getattr(user, 'is_authenticated', False):
        return

    ignore = set(ignore_fields or _DEFAULT_IGNORE)
    only = set(only_fields) if only_fields else None

    if before is None and before_instance is not None:
        before = instance_to_snapshot(before_instance, ignore_fields=ignore, only_fields=only)
    if after is None and after_instance is not None:
        after = instance_to_snapshot(after_instance, ignore_fields=ignore, only_fields=only)

    before, after = apply_request_overrides_to_audit_snapshots(request, model_cls, before, after)

    entity_type = entity_type_for_model(model_cls)
    entity_id_str = ''
    if action == 'delete' and before is not None:
        pk_val = before.get('id') or before.get('pk')
        if pk_val is not None:
            entity_id_str = str(pk_val)
    elif after is not None:
        pk_val = after.get('id') or after.get('pk')
        if pk_val is not None:
            entity_id_str = str(pk_val)
    elif before is not None:
        pk_val = before.get('id') or before.get('pk')
        if pk_val is not None:
            entity_id_str = str(pk_val)

    if not entity_id_str:
        import uuid

        entity_id_str = str(uuid.uuid4())

    summary = (description or '')[:500]
    changes = build_field_changes(
        action=action,
        model_class=model_cls,
        before=before,
        after=after,
        before_instance=before_instance if action == 'delete' else None,
        after_instance=after_instance if action in ('create', 'update') else None,
    )

    snapshot: Dict[str, Any] = {}
    if before is not None:
        snapshot['before'] = before
    if after is not None:
        snapshot['after'] = after

    payload: Dict[str, Any] = {
        'changes': changes,
        'snapshot': snapshot,
    }
    if payload_extra:
        payload['meta'] = payload_extra

    from apps.activity.shift_context import resolve_audit_shift_context

    if shift_context is not None:
        shift_id, line_id, open_ev_id = shift_context
    else:
        shift_id, line_id, open_ev_id = resolve_audit_shift_context(request, user)
    role_snap = ''
    role = getattr(user, 'role', None)
    if role is not None:
        role_snap = getattr(role, 'name', '') or ''

    schedule_user_activity(
        user=user,
        action=action,
        section=section,
        description=description,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id_str,
        request=request,
        payload=payload,
        payload_version=PAYLOAD_VERSION,
        shift_id=shift_id,
        line_id=line_id,
        session_open_event_id=open_ev_id,
        actor_role_snapshot=role_snap,
    )
