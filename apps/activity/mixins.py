"""
ActivityLoggingMixin — журнал с полевым diff (payload_version=1) после успешного commit.

Классам ViewSet можно задать:
  activity_section, activity_label — как раньше;
  activity_entity_model — модель сущности (иначе берётся из serializer_class.Meta.model);
  activity_ignore_fields — поля не попадают в снимок (по умолчанию updated_at, modified_at);
  activity_only_fields — если задано, в аудит только эти поля.
"""

from __future__ import annotations

import logging
from typing import Optional, Set, Type

from django.db import models

logger = logging.getLogger(__name__)


class ActivityLoggingMixin:
    activity_section = ''
    activity_label = ''
    activity_entity_model: Optional[Type[models.Model]] = None
    activity_ignore_fields: tuple = ('updated_at', 'modified_at')
    activity_only_fields: Optional[tuple] = None

    def _activity_model_class(self) -> Optional[Type[models.Model]]:
        if self.activity_entity_model is not None:
            return self.activity_entity_model
        ser = getattr(self, 'serializer_class', None)
        meta = getattr(ser, 'Meta', None)
        return getattr(meta, 'model', None)

    def _activity_description(self, instance, action: str) -> str:
        action_map = {
            'create': 'Создал',
            'update': 'Изменил',
            'delete': 'Удалил',
            'restore': 'Восстановил',
        }
        verb = action_map.get(action, action)
        label = self.activity_label or self.activity_section.lower()
        try:
            obj_str = str(instance)
        except Exception:
            obj_str = f'#{getattr(instance, "pk", "?")}'
        return f'{verb} {label}: {obj_str}'

    def _activity_ignore_set(self) -> Set[str]:
        base = set(self.activity_ignore_fields or ())
        only = getattr(self, 'activity_only_fields', None)
        return base

    def _activity_only_set(self) -> Optional[Set[str]]:
        only = getattr(self, 'activity_only_fields', None)
        if only is None:
            return None
        return set(only)

    def _log_activity_legacy(self, action: str, instance) -> None:
        from apps.activity.models import UserActivity

        request = getattr(self, 'request', None)
        if request is None or not request.user.is_authenticated:
            return
        try:
            UserActivity.objects.create(
                user=request.user,
                action=action,
                section=self.activity_section,
                description=self._activity_description(instance, action),
            )
        except Exception as exc:
            logger.exception('Не удалось записать действие в журнал: %s', exc)

    def _schedule_detailed_audit(
        self,
        *,
        action: str,
        model_cls: Type[models.Model],
        before: Optional[dict],
        after: Optional[dict],
        entity_id_str: str,
        description: str,
        before_instance: Optional[models.Model] = None,
        after_instance: Optional[models.Model] = None,
    ) -> None:
        from apps.activity.audit_service import (
            PAYLOAD_VERSION,
            apply_request_overrides_to_audit_snapshots,
            build_field_changes,
            entity_type_for_model,
            schedule_user_activity,
        )
        from apps.activity.shift_context import resolve_audit_shift_context

        request = getattr(self, 'request', None)
        if request is None or not request.user.is_authenticated:
            return

        before, after = apply_request_overrides_to_audit_snapshots(request, model_cls, before, after)

        entity_type = entity_type_for_model(model_cls)
        summary = description[:500]

        changes = build_field_changes(
            action=action,
            model_class=model_cls,
            before=before,
            after=after,
            before_instance=before_instance if action == 'delete' else None,
            after_instance=after_instance if action in ('create', 'update') else None,
        )

        snapshot = {}
        if before is not None:
            snapshot['before'] = before
        if after is not None:
            snapshot['after'] = after

        payload = {
            'changes': changes,
            'snapshot': snapshot,
        }

        shift_id, line_id, open_ev_id = resolve_audit_shift_context(request, request.user)
        role_snap = ''
        role = getattr(request.user, 'role', None)
        if role is not None:
            role_snap = getattr(role, 'name', '') or ''

        schedule_user_activity(
            user=request.user,
            action=action,
            section=self.activity_section,
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

    def perform_create(self, serializer):
        super().perform_create(serializer)
        inst = serializer.instance
        model_cls = self._activity_model_class()
        request = getattr(self, 'request', None)
        if request is None or not request.user.is_authenticated:
            return
        if model_cls is None:
            self._log_activity_legacy('create', inst)
            return
        from apps.activity.audit_service import instance_to_snapshot

        only = self._activity_only_set()
        ignore = self._activity_ignore_set()
        after = instance_to_snapshot(inst, ignore_fields=ignore, only_fields=only)
        self._schedule_detailed_audit(
            action='create',
            model_cls=model_cls,
            before=None,
            after=after,
            entity_id_str=str(inst.pk),
            description=self._activity_description(inst, 'create'),
            after_instance=inst,
        )

    def perform_update(self, serializer):
        model_cls = self._activity_model_class()
        request = getattr(self, 'request', None)
        inst = serializer.instance
        before = None
        if model_cls is not None and request and request.user.is_authenticated:
            from apps.activity.audit_service import instance_to_snapshot

            only = self._activity_only_set()
            ignore = self._activity_ignore_set()
            try:
                inst.refresh_from_db()
            except Exception:
                pass
            before = instance_to_snapshot(inst, ignore_fields=ignore, only_fields=only)

        super().perform_update(serializer)
        inst = serializer.instance

        if request is None or not request.user.is_authenticated:
            return
        if model_cls is None:
            self._log_activity_legacy('update', inst)
            return

        from apps.activity.audit_service import instance_to_snapshot

        only = self._activity_only_set()
        ignore = self._activity_ignore_set()
        after = instance_to_snapshot(inst, ignore_fields=ignore, only_fields=only)
        self._schedule_detailed_audit(
            action='update',
            model_cls=model_cls,
            before=before,
            after=after,
            entity_id_str=str(inst.pk),
            description=self._activity_description(inst, 'update'),
            after_instance=inst,
        )

    def perform_destroy(self, instance):
        model_cls = self._activity_model_class()
        request = getattr(self, 'request', None)
        pk = instance.pk
        before = None
        if model_cls is not None and request and request.user.is_authenticated:
            from apps.activity.audit_service import instance_to_snapshot

            only = self._activity_only_set()
            ignore = self._activity_ignore_set()
            before = instance_to_snapshot(instance, ignore_fields=ignore, only_fields=only)

        description = self._activity_description(instance, 'delete')
        before_inst = instance
        super().perform_destroy(instance)

        if request is None or not request.user.is_authenticated:
            return
        if model_cls is None:
            from apps.activity.models import UserActivity

            try:
                UserActivity.objects.create(
                    user=request.user,
                    action='delete',
                    section=self.activity_section,
                    description=description,
                )
            except Exception as exc:
                logger.exception('Не удалось записать удаление в журнал: %s', exc)
            return

        self._schedule_detailed_audit(
            action='delete',
            model_cls=model_cls,
            before=before,
            after=None,
            entity_id_str=str(pk),
            description=description,
            before_instance=before_inst,
        )

    def _log_activity(self, action: str, instance) -> None:
        """Ручной вызов из кастомных action'ов: одна запись с diff как у update/create."""
        model_cls = self._activity_model_class()
        request = getattr(self, 'request', None)
        if request is None or not request.user.is_authenticated:
            return
        if model_cls is None:
            self._log_activity_legacy(action, instance)
            return
        from apps.activity.audit_service import instance_to_snapshot

        only = self._activity_only_set()
        ignore = self._activity_ignore_set()
        snap = instance_to_snapshot(instance, ignore_fields=ignore, only_fields=only)
        if action == 'create':
            self._schedule_detailed_audit(
                action='create',
                model_cls=model_cls,
                before=None,
                after=snap,
                entity_id_str=str(instance.pk),
                description=self._activity_description(instance, 'create'),
                after_instance=instance,
            )
        elif action == 'update':
            self._schedule_detailed_audit(
                action='update',
                model_cls=model_cls,
                before=None,
                after=snap,
                entity_id_str=str(instance.pk),
                description=self._activity_description(instance, 'update'),
                after_instance=instance,
            )
        else:
            self._log_activity_legacy(action, instance)
