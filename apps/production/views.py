import logging
from decimal import Decimal
from datetime import date

from django.core.cache import cache
from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError as DRFValidationError
from rest_framework.response import Response
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Case, Count, Prefetch, Q, When

from apps.activity.mixins import ActivityLoggingMixin
from apps.activity.audit_service import instance_to_snapshot, schedule_entity_audit
from config.exceptions import _extract_validation_errors, _make_error_response
from config.openapi_common import DiasErrorSerializer, paginated_inline
from config.permissions import CanAccessShiftComplaints, IsAdminOrHasAccess, IsAdminOrHasProductionOrOtk
from config.pagination import StandardResultsSetPagination
from .shift_state import (
    line_current_shift_open_event,
    line_current_shift_params_event,
    line_history_audit_shift_context,
    line_shift_is_open,
    line_shift_is_paused,
    prefetch_line_histories_map,
    shift_instance_audit_context,
)
from .models import (
    Line,
    LineHistory,
    Order,
    ProductionBatch,
    RecipeRun,
    RecipeRunBatch,
    RecipeRunBatchComponent,
    Shift,
    ShiftComplaint,
    ShiftNote,
)
from .serializers import (
    LineSerializer,
    LineHistorySerializer,
    LineShiftOpenSerializer,
    LineShiftPauseSerializer,
    LineShiftSnapshotSerializer,
    ProductionBatchSerializer, ProductionBatchCreateUpdateSerializer, BatchListSerializer,
    RecipeRunDetailSerializer, RecipeRunListSerializer, RecipeRunWriteSerializer,
    ShiftSerializer,
    ShiftDetailSerializer,
    ShiftNoteSerializer,
    ShiftComplaintCreateSerializer,
    ShiftComplaintListSerializer,
)
from apps.recipes.models import RecipeComponent

from .batch_stock import (
    assert_production_batch_ready_for_otk_pipeline,
    apply_production_batch_stock_and_cost,
    resync_production_batch_consumption,
    reverse_production_batch_stock,
)

logger = logging.getLogger(__name__)


def _audit_line_history_row(
    request,
    user,
    hist: LineHistory,
    *,
    endpoint: str,
    shift_context=None,
) -> None:
    line_label = hist.line_name_snapshot or (hist.line.name if hist.line_id else '—')
    if shift_context is None:
        shift_context = line_history_audit_shift_context(hist)
    if hist.action in (LineHistory.ACTION_SHIFT_PAUSE, LineHistory.ACTION_SHIFT_RESUME):
        section = 'Смены'
    else:
        section = 'Линии'
    schedule_entity_audit(
        user=user,
        request=request,
        section=section,
        description=f'{hist.get_action_display()} на линии «{line_label}» (событие #{hist.pk})',
        action='create',
        model_cls=LineHistory,
        after_instance=hist,
        payload_extra={'endpoint': endpoint, 'line_id': hist.line_id},
        shift_context=shift_context,
    )


def _audit_shift_row(
    request,
    user,
    shift: Shift,
    *,
    action: str,
    endpoint: str,
    before=None,
    after=None,
    shift_context=None,
) -> None:
    if shift_context is None:
        shift_context = shift_instance_audit_context(shift)
    kw = dict(
        user=user,
        request=request,
        section='Смены',
        description=f'Смена #{shift.pk}: {endpoint}',
        action=action,
        model_cls=Shift,
        payload_extra={'endpoint': endpoint},
        shift_context=shift_context,
    )
    if action == 'create':
        kw['after_instance'] = shift
    elif action == 'update':
        kw['before'] = before
        kw['after'] = after
        kw['after_instance'] = shift
    schedule_entity_audit(**kw)


def _audit_shift_note_row(request, user, note: ShiftNote, *, endpoint: str) -> None:
    schedule_entity_audit(
        user=user,
        request=request,
        section='Смены',
        description=f'Заметка к смене #{note.shift_id} (запись #{note.pk})',
        action='create',
        model_cls=ShiftNote,
        after_instance=note,
        payload_extra={'endpoint': endpoint},
        shift_context=shift_instance_audit_context(note.shift),
    )


class RecipeRunDeleteConflict(APIException):
    status_code = 409
    default_code = 'conflict'
    default_detail = 'Нельзя удалить запуск: партия ОТК уже не в статусе «ожидает».'


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


def _body_for_line_shift_close(line, request_data):
    """
    Закрытие смены с линией: размеры из тела или из актуального open/params_update в истории.
    Возвращает (dict | None, Response | None): при ошибке второй элемент — ответ 400.
    """
    raw = request_data if hasattr(request_data, 'get') else {}
    dim_keys = ('height', 'width', 'angle_deg')
    present = [k for k in dim_keys if k in raw and raw.get(k) not in (None, '')]
    if len(present) == 3:
        return {
            'height': raw.get('height'),
            'width': raw.get('width'),
            'angle_deg': raw.get('angle_deg'),
            'comment': raw.get('comment', '') or '',
        }, None
    if present:
        return None, _err(
            'bad_request',
            'При указании размеров передайте все поля: height, width, angle_deg.',
            http_status=400,
        )
    ev = line_current_shift_params_event(line)
    if ev is None or ev.height is None or ev.width is None or ev.angle_deg is None:
        return None, _err(
            'bad_request',
            'Нет актуальных параметров смены на линии; укажите height, width, angle_deg в теле запроса.',
            http_status=400,
        )
    return {
        'height': ev.height,
        'width': ev.width,
        'angle_deg': ev.angle_deg,
        'comment': raw.get('comment', '') or '',
    }, None


class LineViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Line.objects.all()
    serializer_class = LineSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'lines'
    activity_section = 'Линии'
    activity_label = 'линия'
    filterset_fields = []
    search_fields = ['name', 'code']
    ordering_fields = ['id', 'name']

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        hist_map = prefetch_line_histories_map([instance.pk])
        serializer = self.get_serializer(
            instance,
            context={**self.get_serializer_context(), 'line_histories': hist_map},
        )
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        raw_eligible = request.query_params.get('eligible_for_recipe_run')
        raw_pb = request.query_params.get('eligible_for_production_batch')
        want_eligible = False
        if raw_eligible is not None and str(raw_eligible).strip().lower() in ('1', 'true', 'yes'):
            want_eligible = True
        if raw_pb is not None and str(raw_pb).strip().lower() in ('1', 'true', 'yes'):
            want_eligible = True
        if want_eligible:
            ordered_pks = list(queryset.values_list('pk', flat=True))
            hist_tmp = prefetch_line_histories_map(ordered_pks)
            eligible_pks = []
            for pk in ordered_pks:
                ln = Line(pk=pk)
                h = hist_tmp.get(pk)
                if line_shift_is_open(ln, histories=h) and not line_shift_is_paused(ln, histories=h):
                    eligible_pks.append(pk)
            if not eligible_pks:
                queryset = queryset.none()
            else:
                order_case = Case(*[When(pk=pk, then=i) for i, pk in enumerate(eligible_pks)])
                queryset = queryset.filter(pk__in=eligible_pks).order_by(order_case)

        ids = list(queryset.values_list('pk', flat=True))
        hist_map = prefetch_line_histories_map(ids)
        ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True, context=ctx)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True, context=ctx)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if line_shift_is_open(instance) or Shift.objects.filter(
            line=instance, closed_at__isnull=True
        ).exists():
            return Response(
                {
                    'code': 'LINE_SHIFT_OPEN',
                    'error': 'Нельзя удалить линию: на ней открыта смена. Сначала закройте смену на линии.',
                    'detail': 'Закройте смену через POST /api/lines/{id}/close/ (или соответствующий сценарий в UI).',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='open')
    def open_shift(self, request, pk=None):
        from django.utils import timezone

        line = self.get_object()
        if not getattr(line, 'is_active', True):
            return _err('bad_request', 'Линия неактивна: открытие смены недоступно', http_status=400)
        if line_shift_is_open(line):
            return _err('conflict', 'На линии уже есть открытая смена', http_status=409)
        ser = LineShiftOpenSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        d = ser.validated_data
        now = timezone.now()
        try:
            with transaction.atomic():
                if Shift.objects.select_for_update().filter(
                    user=request.user, line=line, closed_at__isnull=True
                ).exists():
                    return _err(
                        'conflict',
                        'У вас уже есть открытая смена на этой линии. Закройте её через POST /api/lines/{id}/close/.',
                        http_status=409,
                    )
                hist = LineHistory.objects.create(
                    line=line,
                    action=LineHistory.ACTION_OPEN,
                    date=now.date(),
                    time=now.time(),
                    user=request.user,
                    height=d['height'],
                    width=d['width'],
                    angle_deg=d['angle_deg'],
                    comment=d.get('comment', '') or '',
                    session_title=(d.get('session_title') or '')[:255],
                )
                shift = Shift.objects.create(line=line, user=request.user, opened_at=now)
        except IntegrityError:
            return _err(
                'conflict',
                'Не удалось открыть смену на линии (конфликт записи). Возможно, смена на этой линии уже открыта — закройте её и повторите.',
                http_status=409,
            )
        _audit_line_history_row(request, request.user, hist, endpoint='POST /api/lines/{id}/open/')
        _audit_shift_row(
            request, request.user, shift, action='create', endpoint='POST /api/lines/{id}/open/',
        )
        hist_map = prefetch_line_histories_map([line.pk])
        line_ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        return Response(
            {'detail': 'Смена открыта', 'line': LineSerializer(line, context=line_ctx).data},
        )

    @action(detail=True, methods=['post'], url_path='close')
    def close_shift(self, request, pk=None):
        from django.utils import timezone

        line = self.get_object()
        if not line_shift_is_open(line):
            return _err('bad_request', 'Смена на линии не открыта', http_status=400)
        merged, bad = _body_for_line_shift_close(line, request.data)
        if bad:
            return bad
        ser = LineShiftSnapshotSerializer(data=merged)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        d = ser.validated_data
        open_shift = (
            Shift.objects.filter(line=line, user=request.user, closed_at__isnull=True)
            .order_by('-opened_at')
            .first()
        )
        if not open_shift:
            return _err('bad_request', 'Нет открытой смены текущего пользователя на этой линии', http_status=400)
        before_shift = instance_to_snapshot(open_shift)
        open_ev_pre = line_current_shift_open_event(line)
        open_ev_id_pre = open_ev_pre.id if open_ev_pre else None
        close_shift_context = (open_shift.pk, line.pk, open_ev_id_pre)
        now = timezone.now()
        comment = d.get('comment', '') or ''
        with transaction.atomic():
            hist = LineHistory.objects.create(
                line=line,
                action=LineHistory.ACTION_CLOSE,
                date=now.date(),
                time=now.time(),
                user=request.user,
                height=d['height'],
                width=d['width'],
                angle_deg=d['angle_deg'],
                comment=comment,
                session_title='',
            )
            open_shift.closed_at = now
            open_shift.comment = comment
            open_shift.save(update_fields=['closed_at', 'comment'])
        open_shift.refresh_from_db()
        after_shift = instance_to_snapshot(open_shift)
        _audit_line_history_row(
            request,
            request.user,
            hist,
            endpoint='POST /api/lines/{id}/close/',
            shift_context=close_shift_context,
        )
        _audit_shift_row(
            request,
            request.user,
            open_shift,
            action='update',
            endpoint='POST /api/lines/{id}/close/',
            before=before_shift,
            after=after_shift,
            shift_context=close_shift_context,
        )
        hist_map = prefetch_line_histories_map([line.pk])
        line_ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        return Response(
            {'detail': 'Смена закрыта', 'line': LineSerializer(line, context=line_ctx).data},
        )

    @action(detail=True, methods=['patch'], url_path='shift-params')
    def shift_params(self, request, pk=None):
        from django.utils import timezone

        line = self.get_object()
        if not line_shift_is_open(line):
            return _err('bad_request', 'Смена на линии не открыта', http_status=400)
        ser = LineShiftSnapshotSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        d = ser.validated_data
        now = timezone.now()
        with transaction.atomic():
            hist = LineHistory.objects.create(
                line=line,
                action=LineHistory.ACTION_PARAMS_UPDATE,
                date=now.date(),
                time=now.time(),
                user=request.user,
                height=d['height'],
                width=d['width'],
                angle_deg=d['angle_deg'],
                comment=d.get('comment', '') or '',
                session_title='',
            )
        _audit_line_history_row(request, request.user, hist, endpoint='PATCH /api/lines/{id}/shift-params/')
        hist_map = prefetch_line_histories_map([line.pk])
        line_ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        line_payload = LineSerializer(line, context=line_ctx).data
        return Response(
            {
                'detail': 'Параметры зафиксированы',
                'shift_snapshot': line_payload.get('shift_snapshot'),
                'line': line_payload,
            }
        )

    @action(detail=True, methods=['post'], url_path='shift-pause')
    def shift_pause(self, request, pk=None):
        from django.utils import timezone

        line = self.get_object()
        if not line_shift_is_open(line):
            return _err('bad_request', 'Смена на линии не открыта', http_status=400)
        if line_shift_is_paused(line):
            return _err(
                'conflict',
                'Смена на линии уже остановлена. Сначала возобновите смену (POST …/shift-resume/).',
                http_status=409,
            )
        ser = LineShiftPauseSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        reason = ser.validated_data['reason']
        now = timezone.now()
        with transaction.atomic():
            hist = LineHistory.objects.create(
                line=line,
                action=LineHistory.ACTION_SHIFT_PAUSE,
                date=now.date(),
                time=now.time(),
                user=request.user,
                height=None,
                width=None,
                angle_deg=None,
                comment=reason,
                session_title='',
            )
        _audit_line_history_row(request, request.user, hist, endpoint='POST /api/lines/{id}/shift-pause/')
        Shift.objects.filter(line=line, closed_at__isnull=True).update(status=Shift.STATUS_PAUSED)
        hist_map = prefetch_line_histories_map([line.pk])
        line_ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        return Response(
            {
                'detail': 'Смена остановлена',
                'line': LineSerializer(line, context=line_ctx).data,
            }
        )

    @action(detail=True, methods=['post'], url_path='shift-resume')
    def shift_resume(self, request, pk=None):
        from django.utils import timezone

        line = self.get_object()
        if not line_shift_is_open(line):
            return _err('bad_request', 'Смена на линии не открыта', http_status=400)
        if not line_shift_is_paused(line):
            return _err(
                'conflict',
                'Смена на линии не остановлена, возобновление не требуется.',
                http_status=409,
            )
        now = timezone.now()
        with transaction.atomic():
            hist = LineHistory.objects.create(
                line=line,
                action=LineHistory.ACTION_SHIFT_RESUME,
                date=now.date(),
                time=now.time(),
                user=request.user,
                height=None,
                width=None,
                angle_deg=None,
                comment='',
                session_title='',
            )
        _audit_line_history_row(request, request.user, hist, endpoint='POST /api/lines/{id}/shift-resume/')
        Shift.objects.filter(line=line, closed_at__isnull=True).update(status=Shift.STATUS_OPEN)
        hist_map = prefetch_line_histories_map([line.pk])
        line_ctx = {**self.get_serializer_context(), 'line_histories': hist_map}
        return Response(
            {
                'detail': 'Смена возобновлена',
                'line': LineSerializer(line, context=line_ctx).data,
            }
        )

    @action(detail=False, methods=['get'], url_path='history')
    def history_list(self, request):
        """GET /lines/history/ — вся история по линиям (как items у lines/{id}/history/), с page_size."""
        qs = (
            LineHistory.objects.all()
            .select_related('line', 'user')
            .order_by('-date', '-time', '-id')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        ser = LineHistorySerializer(page, many=True)
        return paginator.get_paginated_response(ser.data)

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        line = self.get_object()
        qs = (
            LineHistory.objects.filter(line=line)
            .select_related('line', 'user')
            .order_by('-date', '-time', '-id')
        )
        ser = LineHistorySerializer(qs, many=True)
        return Response({'items': ser.data})

    @action(detail=True, methods=['get'], url_path='history/session')
    def history_session(self, request, pk=None):
        """
        GET /lines/{id}/history/session/?open_event_id=… — таймлайн одной смены:
        open, updates (params_update), pause_resume, close (если есть).
        """
        line = self.get_object()
        raw = request.query_params.get('open_event_id')
        if raw is None or str(raw).strip() == '':
            return _err('bad_request', 'Укажите open_event_id', http_status=400)
        try:
            eid = int(raw)
        except (TypeError, ValueError):
            return _err('bad_request', 'Некорректный open_event_id', http_status=400)

        open_row = (
            LineHistory.objects.filter(
                pk=eid,
                line=line,
                action=LineHistory.ACTION_OPEN,
            )
            .select_related('line', 'user')
            .first()
        )
        if not open_row:
            return _err('not_found', 'Событие открытия не найдено', http_status=404)

        after_open = _line_history_q_after_row(open_row)
        close_row = (
            LineHistory.objects.filter(line=line, action=LineHistory.ACTION_CLOSE)
            .filter(after_open)
            .order_by('date', 'time', 'id')
            .select_related('line', 'user')
            .first()
        )

        updates_qs = (
            LineHistory.objects.filter(line=line, action=LineHistory.ACTION_PARAMS_UPDATE)
            .filter(after_open)
            .select_related('line', 'user')
            .order_by('date', 'time', 'id')
        )
        pause_resume_qs = (
            LineHistory.objects.filter(
                line=line,
                action__in=(LineHistory.ACTION_SHIFT_PAUSE, LineHistory.ACTION_SHIFT_RESUME),
            )
            .filter(after_open)
            .select_related('line', 'user')
            .order_by('date', 'time', 'id')
        )
        if close_row:
            updates_qs = updates_qs.filter(_line_history_q_before_row(close_row))
            pause_resume_qs = pause_resume_qs.filter(_line_history_q_before_row(close_row))

        ser = LineHistorySerializer
        pause_data = ser(pause_resume_qs, many=True).data
        payload = {
            'open': ser(open_row).data,
            'updates': ser(updates_qs, many=True).data,
            'pause_resume': pause_data,
        }
        if close_row:
            payload['close'] = ser(close_row).data
        else:
            payload['close'] = None
        return Response(payload)


def _line_history_q_after_row(row):
    """События строго после row по (date, time, id)."""
    return (
        Q(date__gt=row.date)
        | (Q(date=row.date) & Q(time__gt=row.time))
        | (Q(date=row.date) & Q(time=row.time) & Q(id__gt=row.id))
    )


def _line_history_q_before_row(row):
    """События строго до row по (date, time, id)."""
    return (
        Q(date__lt=row.date)
        | (Q(date=row.date) & Q(time__lt=row.time))
        | (Q(date=row.date) & Q(time=row.time) & Q(id__lt=row.id))
    )


class BatchViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    GET/POST /api/batches/ — партии; POST — выпуск профиля (штуки × длина, смена, рецепт на 1 м).
    POST /api/batches/{id}/otk_accept/ — результат ОТК.
    """
    queryset = ProductionBatch.objects.select_related(
        'order', 'order__recipe', 'order__line', 'operator',
        'profile', 'recipe', 'recipe__profile', 'line', 'shift',
    ).prefetch_related('otk_checks__inspector').all()
    serializer_class = BatchListSerializer
    permission_classes = [IsAdminOrHasProductionOrOtk]
    filterset_fields = ['otk_status', 'order', 'line', 'profile', 'lifecycle_status']
    ordering_fields = ['id', 'date']
    activity_section = 'Производство'
    activity_label = 'партия'
    activity_entity_model = ProductionBatch

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return BatchListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return ProductionBatchCreateUpdateSerializer
        return ProductionBatchSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.action not in ('create', 'update', 'partial_update'):
            return ctx
        line_id = None
        data = getattr(self.request, 'data', None) or {}
        raw_line = data.get('line') if hasattr(data, 'get') else None
        if raw_line is not None and str(raw_line).strip() != '':
            try:
                line_id = int(raw_line)
            except (TypeError, ValueError):
                line_id = None
        if line_id is None and self.action in ('update', 'partial_update'):
            pk = self.kwargs.get('pk')
            if pk is not None:
                line_id = ProductionBatch.objects.filter(pk=pk).values_list('line_id', flat=True).first()
        if line_id:
            ctx['line_histories'] = prefetch_line_histories_map([line_id])
        return ctx

    def create(self, request, *args, **kwargs):
        idem = request.headers.get('X-Request-Id') or request.headers.get('Idempotency-Key')
        if idem and str(idem).strip():
            key = f'production_batch:create:{request.user.pk}:{str(idem).strip()}'
            existing = cache.get(key)
            if existing:
                batch = ProductionBatch.objects.filter(pk=existing).first()
                if batch:
                    serializer = self.get_serializer(batch)
                    return Response(serializer.data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        req = self.request
        idem = req.headers.get('X-Request-Id') or req.headers.get('Idempotency-Key')
        if idem and str(idem).strip() and serializer.instance and serializer.instance.pk:
            cache.set(
                f'production_batch:create:{req.user.pk}:{str(idem).strip()}',
                serializer.instance.pk,
                86400,
            )

    @action(detail=True, methods=['post'], url_path='submit-for-otk')
    def submit_for_otk(self, request, pk=None):
        """Пустое тело. pending → очередь ОТК; повтор — 200 без дубля."""
        from django.utils import timezone as dj_tz

        with transaction.atomic():
            batch = ProductionBatch.objects.select_for_update().select_related(
                'line', 'recipe', 'profile',
            ).get(
                pk=self.kwargs['pk'],
            )
            if batch.lifecycle_status == ProductionBatch.LIFECYCLE_OTK:
                return Response(BatchListSerializer(batch).data, status=status.HTTP_200_OK)
            if batch.lifecycle_status == ProductionBatch.LIFECYCLE_DONE:
                return _err(
                    'conflict',
                    'Партия уже завершена',
                    http_status=status.HTTP_409_CONFLICT,
                )
            if batch.lifecycle_status != ProductionBatch.LIFECYCLE_PENDING:
                return _err(
                    'conflict',
                    'Отправка в ОТК доступна только из статуса «производство» (pending)',
                    http_status=status.HTTP_409_CONFLICT,
                )
            if batch.otk_status != ProductionBatch.OTK_PENDING:
                return _err(
                    'conflict',
                    'Партия уже обработана ОТК',
                    http_status=status.HTTP_409_CONFLICT,
                )
            try:
                assert_production_batch_ready_for_otk_pipeline(batch)
            except DRFValidationError as exc:
                return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
            now = dj_tz.now()
            line = batch.line
            if line:
                _apply_shift_snapshot_to_batch(batch, line)
            batch.lifecycle_status = ProductionBatch.LIFECYCLE_OTK
            batch.sent_to_otk = True
            batch.in_otk_queue = True
            batch.otk_submitted_at = now
            batch.save()
        batch = self.get_object()
        return Response(BatchListSerializer(batch).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='otk_accept')
    def otk_accept(self, request, pk=None):
        from apps.otk.models import OtkCheck
        from django.contrib.auth import get_user_model
        UserModel = get_user_model()

        batch = self.get_object()
        before_batch = instance_to_snapshot(batch)
        if batch.otk_status != ProductionBatch.OTK_PENDING:
            return _err('bad_request', 'Партия уже прошла ОТК-проверку')
        try:
            assert_production_batch_ready_for_otk_pipeline(batch)
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        accepted_raw = request.data.get('otk_accepted') or request.data.get('accepted')
        rejected_raw = request.data.get('otk_defect') or request.data.get('rejected')
        defect_reason = request.data.get('otk_defect_reason') or request.data.get('rejectReason', '')
        comment = request.data.get('otk_comment') or ''
        otk_inspector_raw = request.data.get('otk_inspector')
        otk_inspector_name_in = (request.data.get('otk_inspector_name') or '').strip()
        checked_at = request.data.get('otk_checked_at')

        errors = []
        if accepted_raw is None:
            errors.append({'field': 'otk_accepted', 'message': 'Обязательное поле'})
        if rejected_raw is None:
            errors.append({'field': 'otk_defect', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите otk_accepted и otk_defect', errors=errors)

        try:
            accepted = Decimal(str(accepted_raw))
            rejected = Decimal(str(rejected_raw))
        except Exception:
            return _err('validation_error', 'Некорректные значения otk_accepted/otk_defect',
                        errors=[{'field': 'otk_accepted', 'message': 'Должно быть числом'}])

        q_step = Decimal('0.0001')
        sum_q = (accepted + rejected).quantize(q_step)
        batch_pieces = Decimal(str(batch.pieces))
        if sum_q != batch_pieces:
            return _err(
                'validation_error',
                f'Принято + Брак должно равняться числу штук партии ({batch.pieces})',
                errors=[{'field': 'otk_accepted', 'message': f'Сумма должна быть {batch.pieces}'}],
            )

        if rejected > 0 and not str(defect_reason).strip():
            return _err('validation_error', 'Причина брака обязательна при наличии брака',
                        errors=[{'field': 'otk_defect_reason', 'message': 'Обязательное поле при браке'}])

        inspector = None
        if otk_inspector_raw is not None:
            if isinstance(otk_inspector_raw, int) or (
                isinstance(otk_inspector_raw, str) and str(otk_inspector_raw).isdigit()
            ):
                try:
                    inspector = UserModel.objects.filter(pk=int(otk_inspector_raw)).first()
                except (TypeError, ValueError):
                    inspector = None
            elif isinstance(otk_inspector_raw, str) and otk_inspector_raw.strip():
                inspector = UserModel.objects.filter(name=otk_inspector_raw).first()
        if inspector is None and request.data.get('otk_inspector_id') is not None:
            try:
                inspector = UserModel.objects.filter(pk=int(request.data.get('otk_inspector_id'))).first()
            except (TypeError, ValueError):
                inspector = None
        if inspector is None:
            inspector = request.user

        inspector_name_stored = otk_inspector_name_in[:255] if otk_inspector_name_in else ''
        if not inspector_name_stored and inspector:
            inspector_name_stored = (getattr(inspector, 'name', None) or '')[:255]

        if checked_at and isinstance(checked_at, str):
            from django.utils.dateparse import parse_datetime
            from django.utils import timezone as tz
            try:
                parsed = parse_datetime(checked_at)
                if parsed:
                    checked_at = tz.make_aware(parsed) if tz.is_naive(parsed) else parsed
                else:
                    checked_at = None
            except Exception:
                checked_at = None
        if checked_at is None:
            from django.utils import timezone as tz
            checked_at = tz.now()

        with transaction.atomic():
            otk_st = (
                OtkCheck.STATUS_REJECTED if rejected > 0 and accepted == 0
                else OtkCheck.STATUS_ACCEPTED
            )
            check = OtkCheck.objects.create(
                batch=batch,
                profile_id=batch.profile_id,
                pieces=int(batch.pieces),
                length_per_piece=batch.length_per_piece,
                total_meters=batch.total_meters,
                accepted=accepted,
                rejected=rejected,
                check_status=otk_st,
                reject_reason=defect_reason,
                comment=comment,
                inspector=inspector,
                inspector_name=inspector_name_stored,
            )
            OtkCheck.objects.filter(pk=check.pk).update(checked_date=checked_at)
            batch.otk_status = (
                ProductionBatch.OTK_REJECTED if rejected > 0 and accepted == 0
                else ProductionBatch.OTK_ACCEPTED
            )
            batch.lifecycle_status = ProductionBatch.LIFECYCLE_DONE
            batch.sent_to_otk = True
            batch.in_otk_queue = False
            batch.save(update_fields=[
                'otk_status', 'lifecycle_status', 'sent_to_otk', 'in_otk_queue',
            ])
            from apps.warehouse.receipt import create_warehouse_batches_from_otk

            create_warehouse_batches_from_otk(
                batch,
                accepted=accepted,
                rejected=rejected,
                defect_reason=defect_reason or '',
                comment=comment or '',
                inspector_name=inspector_name_stored,
                checked_at=checked_at,
                otk_status_snapshot=batch.otk_status,
            )
            if batch.order_id:
                Order.objects.filter(pk=batch.order_id).update(status=Order.STATUS_DONE)

        batch = ProductionBatch.objects.select_related(
            'order', 'operator'
        ).prefetch_related('otk_checks__inspector').get(pk=batch.pk)
        after_batch = instance_to_snapshot(batch)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='ОТК',
            description=f'Приёмка ОТК: партия #{batch.pk}, заказ #{batch.order_id}',
            action='update',
            model_cls=ProductionBatch,
            before=before_batch,
            after=after_batch,
            after_instance=batch,
            payload_extra={
                'endpoint': 'POST /api/batches/{id}/otk_accept/',
                'request_body': {
                    'otk_accepted': str(accepted),
                    'otk_defect': str(rejected),
                    'otk_defect_reason': (defect_reason or '')[:500],
                },
            },
        )
        return Response(BatchListSerializer(batch).data)


class ShiftViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET  /api/shifts/         — список всех смен (фильтры: date_from, date_to, line, user).
    POST /api/shifts/open/    — тело: line_id (опц.). Без line_id — только **личная** смена (параллельно
        можно держать смену на линии через POST /api/lines/{id}/open/). С line_id — как открытие на линии.
    POST /api/shifts/close/   — без line_id закрывает **личную** смену; с line_id — смену на этой линии
        (альтернатива POST /api/lines/{id}/close/).
    """
    serializer_class = ShiftSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'my_shift'
    ordering_fields = ['id', 'opened_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ShiftDetailSerializer
        return ShiftSerializer

    def get_queryset(self):
        from django.db.models import Count, Q
        from datetime import datetime
        qs = Shift.objects.select_related('line', 'user').prefetch_related('notes').annotate(
            notes_count=Count('notes')
        ).order_by('-opened_at')

        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        line_id = self.request.query_params.get('line')
        user_id = self.request.query_params.get('user')

        if date_from:
            try:
                df = datetime.strptime(date_from, '%Y-%m-%d').date()
                qs = qs.filter(Q(closed_at__isnull=True) | Q(closed_at__date__gte=df))
            except ValueError:
                pass

        if date_to:
            try:
                dt = datetime.strptime(date_to, '%Y-%m-%d').date()
                qs = qs.filter(opened_at__date__lte=dt)
            except ValueError:
                pass

        if line_id:
            qs = qs.filter(line_id=line_id)
        if user_id:
            qs = qs.filter(user_id=user_id)

        return qs

    @action(detail=False, methods=['post'], url_path='open')
    def open(self, request):
        """
        POST /api/shifts/open/
        При указании line_id в теле обязательны height, width, angle_deg (снимок для line_history);
        опционально comment, session_title. Без line_id — смена без привязки к линии, без записи в историю линии.
        """
        from django.utils import timezone
        from django.db.models import Count

        line_id = request.data.get('line_id')
        line = Line.objects.filter(pk=line_id).first() if line_id else None

        now = timezone.now()
        hist = None
        try:
            with transaction.atomic():
                if line:
                    if Shift.objects.select_for_update().filter(
                        user=request.user, line=line, closed_at__isnull=True
                    ).exists():
                        return _err(
                            'conflict',
                            'У вас уже есть открытая смена на этой линии. Закройте её через POST /api/lines/{id}/close/ или POST /api/shifts/close/ с line_id.',
                            http_status=409,
                        )
                    if not getattr(line, 'is_active', True):
                        return _err('bad_request', 'Линия неактивна', http_status=400)
                    if line_shift_is_open(line):
                        return _err('conflict', 'На линии уже есть открытая смена', http_status=409)
                    ser = LineShiftOpenSerializer(data=request.data)
                    if not ser.is_valid():
                        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
                    d = ser.validated_data
                    hist = LineHistory.objects.create(
                        line=line,
                        action=LineHistory.ACTION_OPEN,
                        date=now.date(),
                        time=now.time(),
                        user=request.user,
                        height=d['height'],
                        width=d['width'],
                        angle_deg=d['angle_deg'],
                        comment=d.get('comment', '') or '',
                        session_title=(d.get('session_title') or '')[:255],
                    )
                else:
                    if Shift.objects.select_for_update().filter(
                        user=request.user, line_id__isnull=True, closed_at__isnull=True
                    ).exists():
                        return _err(
                            'conflict',
                            'Личная смена уже открыта. Закройте её через POST /api/shifts/close/ (без line_id).',
                            http_status=409,
                        )
                shift = Shift.objects.create(line=line, user=request.user, opened_at=now)
        except IntegrityError:
            return _err(
                'conflict',
                'Не удалось открыть смену (конфликт записи). Проверьте, нет ли уже открытой личной смены или смены на этой линии.',
                http_status=409,
            )

        if hist:
            _audit_line_history_row(request, request.user, hist, endpoint='POST /api/shifts/open/')
        _audit_shift_row(
            request, request.user, shift, action='create', endpoint='POST /api/shifts/open/',
        )

        shift = Shift.objects.annotate(notes_count=Count('notes')).get(pk=shift.pk)
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='close')
    def close(self, request):
        """
        POST /api/shifts/close/
        Тело: опционально **line_id** — какую открытую смену закрыть.
        - **Без line_id** — закрывается только **личная** смена (`line` пустой). Смену на линии закройте
          через `POST /api/lines/{id}/close/` или передайте здесь **line_id**.
        - С **line_id** — закрытие смены пользователя на этой линии (как доп. путь к `lines/{id}/close/`).
        Для смены на линии: comment и размеры — как раньше (из тела или истории).
        """
        from django.utils import timezone
        from django.db.models import Count

        raw_lid = request.data.get('line_id')
        open_shift = None
        if raw_lid is not None and str(raw_lid).strip() != '':
            try:
                lid = int(raw_lid)
            except (TypeError, ValueError):
                return _err(
                    'validation_error',
                    'Некорректный line_id',
                    errors=[{'field': 'line_id', 'message': 'Должно быть целым числом'}],
                )
            open_shift = (
                Shift.objects.filter(user=request.user, line_id=lid, closed_at__isnull=True)
                .order_by('-opened_at')
                .first()
            )
            if not open_shift:
                return _err(
                    'not_found',
                    'Нет открытой смены на указанной линии. Проверьте line_id или используйте POST /api/lines/{id}/close/.',
                    http_status=404,
                )
        else:
            open_shift = (
                Shift.objects.filter(
                    user=request.user, line_id__isnull=True, closed_at__isnull=True
                )
                .order_by('-opened_at')
                .first()
            )
            if not open_shift:
                return _err(
                    'not_found',
                    'Нет открытой личной смены. Для закрытия смены на линии укажите line_id в теле или вызовите POST /api/lines/{id}/close/.',
                    http_status=404,
                )

        before_shift = instance_to_snapshot(open_shift)
        open_ev_id_pre = None
        if open_shift.line_id:
            ln = open_shift.line or Line.objects.filter(pk=open_shift.line_id).first()
            if ln:
                ev_pre = line_current_shift_open_event(ln)
                open_ev_id_pre = ev_pre.id if ev_pre else None
        close_shift_context = (open_shift.pk, open_shift.line_id, open_ev_id_pre)
        now = timezone.now()
        hist = None
        with transaction.atomic():
            if open_shift.line_id:
                merged, bad = _body_for_line_shift_close(open_shift.line, request.data)
                if bad:
                    return bad
                ser = LineShiftSnapshotSerializer(data=merged)
                if not ser.is_valid():
                    return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
                d = ser.validated_data
                comment = d.get('comment', '') or ''
                hist = LineHistory.objects.create(
                    line=open_shift.line,
                    action=LineHistory.ACTION_CLOSE,
                    date=now.date(),
                    time=now.time(),
                    user=request.user,
                    height=d['height'],
                    width=d['width'],
                    angle_deg=d['angle_deg'],
                    comment=comment,
                    session_title='',
                )
            else:
                comment = request.data.get('comment', '') or ''
            open_shift.closed_at = now
            open_shift.comment = comment
            open_shift.save(update_fields=['closed_at', 'comment'])

        open_shift.refresh_from_db()
        after_shift = instance_to_snapshot(open_shift)
        if hist:
            _audit_line_history_row(
                request,
                request.user,
                hist,
                endpoint='POST /api/shifts/close/',
                shift_context=close_shift_context,
            )
        _audit_shift_row(
            request,
            request.user,
            open_shift,
            action='update',
            endpoint='POST /api/shifts/close/',
            before=before_shift,
            after=after_shift,
            shift_context=close_shift_context,
        )

        shift = Shift.objects.annotate(notes_count=Count('notes')).get(pk=open_shift.pk)
        return Response(ShiftSerializer(shift).data)

    @action(detail=True, methods=['get'], url_path='notes')
    def shift_notes(self, request, pk=None):
        """GET /api/shifts/{id}/notes/ — заметки конкретной смены."""
        from .models import ShiftNote
        shift = self.get_object()
        notes = ShiftNote.objects.filter(shift=shift).order_by('-created_at')
        return Response({'items': ShiftNoteSerializer(notes, many=True).data})

    @action(detail=False, methods=['get'], url_path='my')
    def my(self, request):
        """
        GET /api/shifts/my/ — текущая открытая **личная** смена (`line` = null).
        Смена на линии при этом не показывается здесь (см. экран линий / POST lines/{id}/close).
        """
        from django.db.models import Count

        open_shift = (
            Shift.objects.filter(
                user=request.user, line_id__isnull=True, closed_at__isnull=True
            )
            .select_related('line')
            .annotate(notes_count=Count('notes'))
            .order_by('-opened_at')
            .first()
        )
        if not open_shift:
            return Response({'shift': None})
        return Response({'shift': ShiftSerializer(open_shift).data})

    @action(detail=False, methods=['get', 'post'], url_path='notes')
    def notes(self, request):
        """
        GET  /api/shifts/notes/ — заметки к текущей **личной** открытой смене.
        POST /api/shifts/notes/ — добавить заметку (тело: { "note": "текст" }).
        """
        from .models import ShiftNote

        open_shift = (
            Shift.objects.filter(
                user=request.user, line_id__isnull=True, closed_at__isnull=True
            )
            .order_by('-opened_at')
            .first()
        )

        if request.method == 'GET':
            if not open_shift:
                return Response({'items': []})
            notes = ShiftNote.objects.filter(shift=open_shift).order_by('-created_at')
            return Response({
                'items': [
                    {'id': n.id, 'shift': open_shift.id, 'note': n.text, 'created_at': n.created_at}
                    for n in notes
                ]
            })

        # POST
        note_text = request.data.get('note', '').strip()
        if not note_text:
            return _err('validation_error', 'Укажите текст заметки',
                        errors=[{'field': 'note', 'message': 'Обязательное поле'}])
        if not open_shift:
            return _err(
                'not_found',
                'Нет открытой личной смены для заметки. Откройте «Мою смену» (POST /api/shifts/open/ без line_id).',
                http_status=404,
            )

        note = ShiftNote.objects.create(shift=open_shift, user=request.user, text=note_text)
        _audit_shift_note_row(request, request.user, note, endpoint='POST /api/shifts/notes/')
        return Response({
            'id': note.id,
            'shift': open_shift.id,
            'note': note.text,
            'created_at': note.created_at,
        }, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=['production'],
        summary='История смен текущего пользователя',
        parameters=[
            OpenApiParameter('page', int, required=False),
            OpenApiParameter('page_size', int, required=False),
        ],
        responses={
            200: paginated_inline('ShiftHistoryPaginated', ShiftSerializer),
            401: DiasErrorSerializer,
        },
    ),
)
class ShiftHistoryView(viewsets.ViewSet):
    """
    GET /api/shifts/history/ — история смен текущего пользователя (от новых к старым).
    Параметр: page_size.
    """

    def list(self, request):
        from django.db.models import Count
        qs = (
            Shift.objects.filter(user=request.user)
            .select_related('line')
            .annotate(notes_count=Count('notes'))
            .order_by('-opened_at')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ShiftSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ShiftComplaintViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    GET  /api/shifts/complaints/ — лента жалоб (пагинация: page, page_size).
    POST /api/shifts/complaints/ — создать жалобу: body, mentioned_user_ids?, shift_id?

    Полная лента: суперпользователь или ключ доступа «shifts».
    Иначе: только жалобы автора и где пользователь упомянут.
    Фильтры: date, date_from, date_to, author_id, mentioned_user_id.
    """

    activity_section = 'Смены'
    activity_label = 'жалоба по смене'
    http_method_names = ['get', 'post', 'head', 'options']
    permission_classes = [CanAccessShiftComplaints]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        if self.action == 'create':
            return ShiftComplaintCreateSerializer
        return ShiftComplaintListSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return ShiftComplaint.objects.none()
        from datetime import datetime

        qs = (
            ShiftComplaint.objects.select_related('author', 'shift')
            .prefetch_related('mentioned_users')
            .order_by('-created_at')
        )
        user = self.request.user
        if not getattr(user, 'is_superuser', False) and 'shifts' not in user.get_access_keys():
            qs = qs.filter(Q(author=user) | Q(mentioned_users=user)).distinct()

        p = self.request.query_params
        raw_date = p.get('date')
        if raw_date and str(raw_date).strip():
            try:
                qs = qs.filter(created_at__date=datetime.strptime(str(raw_date).strip(), '%Y-%m-%d').date())
            except ValueError:
                pass
        df = p.get('date_from')
        if df and str(df).strip():
            try:
                qs = qs.filter(created_at__date__gte=datetime.strptime(str(df).strip(), '%Y-%m-%d').date())
            except ValueError:
                pass
        dt = p.get('date_to')
        if dt and str(dt).strip():
            try:
                qs = qs.filter(created_at__date__lte=datetime.strptime(str(dt).strip(), '%Y-%m-%d').date())
            except ValueError:
                pass

        aid = p.get('author_id') or p.get('author')
        if aid not in (None, ''):
            try:
                qs = qs.filter(author_id=int(aid))
            except (TypeError, ValueError):
                pass

        mid = p.get('mentioned_user_id') or p.get('mentioned_user')
        if mid not in (None, ''):
            try:
                qs = qs.filter(mentioned_users__pk=int(mid)).distinct()
            except (TypeError, ValueError):
                pass

        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        read = ShiftComplaintListSerializer(serializer.instance, context={'request': request})
        return Response(read.data, status=status.HTTP_201_CREATED)


def _recipe_run_detail_prefetches():
    comp_qs = RecipeRunBatchComponent.objects.order_by('id').select_related(
        'raw_material',
        'chemistry',
        'recipe_component',
        'recipe_component__raw_material',
        'recipe_component__chemistry',
    )
    batch_qs = RecipeRunBatch.objects.order_by('index', 'id').prefetch_related(
        Prefetch('components', queryset=comp_qs),
    )
    recipe_comp_qs = RecipeComponent.objects.order_by('id').select_related('raw_material', 'chemistry')
    return (
        Prefetch('batches', queryset=batch_qs),
        Prefetch('recipe__components', queryset=recipe_comp_qs),
    )


def _apply_shift_snapshot_to_batch(batch: ProductionBatch, line) -> None:
    """Параметры открытой смены на линии на момент постановки в очередь ОТК."""
    from datetime import datetime, time as time_cls

    from django.utils import timezone as dj_tz

    if line is None:
        batch.shift_height = None
        batch.shift_width = None
        batch.shift_angle_deg = None
        batch.shift_opener_name = ''
        batch.shift_opened_at = None
        return
    ev_open = line_current_shift_open_event(line)
    ev_params = line_current_shift_params_event(line)
    if ev_open is None or ev_params is None:
        batch.shift_height = None
        batch.shift_width = None
        batch.shift_angle_deg = None
        batch.shift_opener_name = ''
        batch.shift_opened_at = None
        return
    batch.shift_height = ev_params.height
    batch.shift_width = ev_params.width
    batch.shift_angle_deg = ev_params.angle_deg
    if ev_open.user_id:
        try:
            batch.shift_opener_name = (ev_open.user.name or '')[:255]
        except Exception:
            batch.shift_opener_name = ''
    else:
        batch.shift_opener_name = ''
    t = ev_open.time or time_cls.min
    dt = datetime.combine(ev_open.date, t)
    batch.shift_opened_at = dj_tz.make_aware(dt) if dj_tz.is_naive(dt) else dt


def _parse_output_scale(data) -> Decimal | None:
    """Множитель к норме рецепта: output_scale или scale в теле; None = 1."""
    if not data:
        return None
    raw = data.get('output_scale')
    if raw in (None, ''):
        raw = data.get('scale')
    if raw in (None, ''):
        return None
    s = Decimal(str(raw))
    if s <= 0:
        raise ValueError('output_scale')
    return s


def _recipe_run_otk_quantity(
    run,
    override=None,
    *,
    output_scale: Decimal | None = None,
    fallback_qty=None,
):
    """
    Объём для ProductionBatch / ОТК: явный quantity из тела, иначе норма рецепта (output_quantity × scale),
    иначе fallback_qty (текущая партия при pending). Не сумма партий ёмкостей и не их количество.
    """
    if override is not None:
        q = Decimal(str(override))
        if q <= 0:
            raise ValueError('qty')
        return q
    qn = None
    if run.recipe_id:
        try:
            rq = run.recipe.output_quantity
            if rq is not None:
                qn = Decimal(str(rq))
        except ObjectDoesNotExist:
            pass
    if qn is not None and qn > 0:
        if output_scale is not None:
            qn = (qn * output_scale).quantize(Decimal('0.0001'))
        return qn
    if fallback_qty is not None:
        fq = Decimal(str(fallback_qty))
        if fq > 0:
            return fq
    raise ValueError('qty')


def submit_recipe_run_to_otk(run, user, quantity_override=None, output_scale=None):
    """
    Создаёт заказ + ProductionBatch (pending) и связывает с RecipeRun.
    Списание сырья/химии и material_cost_total — только через apply_production_batch_stock_and_cost
    (единая логика с POST /api/batches/).
    При изменении объёма у pending-партии: reverse по старым метрам + повторный apply.
    """
    from django.utils import timezone

    with transaction.atomic():
        run_locked = RecipeRun.objects.select_for_update().select_related('recipe', 'line').get(pk=run.pk)
        if run_locked.production_batch_id:
            batch = ProductionBatch.objects.select_for_update().select_related('order', 'recipe').get(
                pk=run_locked.production_batch_id
            )
            if batch.otk_status == ProductionBatch.OTK_PENDING:
                old_recipe = batch.recipe if batch.recipe_id else None
                old_tm = Decimal(str(batch.total_meters))
                try:
                    qty = _recipe_run_otk_quantity(
                        run_locked,
                        quantity_override,
                        output_scale=output_scale,
                        fallback_qty=batch.quantity,
                    )
                except ValueError:
                    raise DRFValidationError({
                        'quantity': (
                            'Укажите quantity > 0 в корне тела, либо задайте в рецепте output_quantity > 0, '
                            'либо используйте output_scale к норме рецепта.'
                        ),
                    })
                batch.pieces = 1
                batch.length_per_piece = qty
                batch.recompute_totals()
                batch.quantity = batch.total_meters
                ufs = ['pieces', 'length_per_piece', 'quantity', 'total_meters']
                if run_locked.line and line_shift_is_open(run_locked.line):
                    _apply_shift_snapshot_to_batch(batch, run_locked.line)
                    ufs.extend([
                        'shift_height', 'shift_width', 'shift_angle_deg',
                        'shift_opener_name', 'shift_opened_at',
                    ])
                batch.save(update_fields=ufs)
                if batch.order_id:
                    order = batch.order
                    if order.quantity != qty:
                        order.quantity = qty
                        order.save(update_fields=['quantity'])
                resync_production_batch_consumption(
                    batch,
                    previous_recipe=old_recipe,
                    previous_total_meters=old_tm,
                )
            return (
                ProductionBatch.objects.select_related('order', 'operator')
                .prefetch_related('otk_checks__inspector')
                .get(pk=batch.pk)
            )

        try:
            qty = _recipe_run_otk_quantity(
                run_locked,
                quantity_override,
                output_scale=output_scale,
                fallback_qty=None,
            )
        except ValueError:
            raise DRFValidationError({
                'quantity': (
                    'Укажите quantity > 0 в корне тела или задайте в рецепте output_quantity > 0 '
                    '(опционально output_scale / scale как множитель к норме).'
                ),
            })
        recipe = run_locked.recipe
        line = run_locked.line
        if recipe is None:
            raise DRFValidationError(
                {
                    'recipe_id': (
                        'Для создания партии производства у замеса должен быть рецепт (FK). '
                        'Партия без рецепта не может пройти FIFO и себестоимость.'
                    ),
                },
            )
        if not recipe.components.exists():
            raise DRFValidationError({'recipe': 'У рецепта нет компонентов'})
        if line is None:
            raise DRFValidationError(
                {'line_id': 'Укажите линию замеса: партия производства привязана к линии и смене.'},
            )
        operator = user if getattr(user, 'is_authenticated', False) else None
        if operator is None:
            raise DRFValidationError({'detail': 'Нужен авторизованный пользователь для создания партии.'})
        hist_map = prefetch_line_histories_map([line.pk])
        hist = hist_map.get(line.pk)
        if getattr(line, 'is_active', True) is False:
            raise DRFValidationError({'line': 'Линия неактивна'})
        if not line_shift_is_open(line, histories=hist):
            raise DRFValidationError({'line': 'На линии нет открытой смены'})
        if line_shift_is_paused(line, histories=hist):
            raise DRFValidationError(
                {'line': 'Смена на линии остановлена (пауза). Возобновите смену или выберите другую линию.'},
            )
        shift_open = (
            Shift.objects.filter(
                user=operator,
                line=line,
                closed_at__isnull=True,
                status=Shift.STATUS_OPEN,
            )
            .order_by('-opened_at')
            .first()
        )
        if not shift_open:
            raise DRFValidationError(
                {
                    'shift': 'Нет активной открытой смены на этой линии для текущего пользователя '
                    '(как при POST /api/batches/).',
                },
            )

        product = (recipe.product or recipe.recipe or '').strip() or recipe.recipe
        now_d = timezone.now().date()
        now_ts = timezone.now()
        order = Order.objects.create(
            recipe=recipe,
            line=line,
            quantity=qty,
            product=product,
            operator=operator,
            date=now_d,
            status=Order.STATUS_IN_PROGRESS,
        )
        prof_id = recipe.profile_id
        batch = ProductionBatch(
            order=order,
            profile_id=prof_id,
            recipe_id=recipe.pk,
            line=line,
            shift=shift_open,
            product=product,
            pieces=1,
            length_per_piece=qty,
            quantity=qty,
            total_meters=qty,
            operator=operator,
            date=now_d,
            produced_at=now_ts,
            otk_status=ProductionBatch.OTK_PENDING,
            lifecycle_status=ProductionBatch.LIFECYCLE_PENDING,
            sent_to_otk=False,
            in_otk_queue=False,
            cost_price=0,
            material_cost_total=0,
        )
        _apply_shift_snapshot_to_batch(batch, line)
        batch.save()
        RecipeRun.objects.filter(pk=run_locked.pk).update(production_batch=batch)
        apply_production_batch_stock_and_cost(batch)

    return (
        ProductionBatch.objects.select_related('order', 'operator')
        .prefetch_related('otk_checks__inspector')
        .get(pk=batch.pk)
    )


class RecipeRunViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    Замес (план): ёмкости и строки расхода для интерфейса. Не альтернатива производству.

    Связанная ProductionBatch создаётся тем же FIFO/себестоимостью, что и POST /api/batches/
    (apply_production_batch_stock_and_cost сразу после сохранения партии).

    POST   /api/production/recipe-runs/ — замес + партия pending + списание по рецепту.
    POST   /api/production/recipe-runs/{id}/submit-to-otk/ — создать/обновить партию pending с пересчётом списания.
    PATCH  — состав ёмкостей (план); при смене объёма партии — submit-to-otk или PATCH с quantity.
    DELETE — при pending-партии: откат списаний (reverse) и удаление партии/заказа.
    """

    permission_classes = [IsAdminOrHasProductionOrOtk]
    activity_section = 'Производство'
    activity_label = 'запуск по рецепту'
    activity_entity_model = RecipeRun
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    pagination_class = StandardResultsSetPagination
    filterset_fields = ['recipe', 'line']
    ordering_fields = ['id', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = (
            RecipeRun.objects.select_related(
                'recipe',
                'line',
                'production_batch',
                'production_batch__order',
                'production_batch__order__recipe',
                'production_batch__order__line',
            )
            .prefetch_related(*_recipe_run_detail_prefetches())
        )
        if self.action == 'list':
            qs = qs.annotate(batches_count=Count('batches', distinct=True))
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return RecipeRunListSerializer
        if self.action == 'retrieve':
            return RecipeRunDetailSerializer
        return RecipeRunWriteSerializer

    def create(self, request, *args, **kwargs):
        try:
            out_scale = _parse_output_scale(request.data)
        except ValueError:
            return _err(
                'validation_error',
                'output_scale / scale должны быть числом > 0',
                errors=[{'field': 'output_scale', 'message': 'Должно быть > 0'}],
            )
        ser = RecipeRunWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            run = ser.save()
            qty_raw = request.data.get('quantity')
            override = None if qty_raw is None or qty_raw == '' else qty_raw
            submit_recipe_run_to_otk(
                run, request.user, quantity_override=override, output_scale=out_scale,
            )
        self._log_activity('create', run)
        run = (
            RecipeRun.objects.select_related(
                'recipe',
                'line',
                'production_batch',
                'production_batch__order',
                'production_batch__order__recipe',
                'production_batch__order__line',
            )
            .prefetch_related(*_recipe_run_detail_prefetches())
            .get(pk=run.pk)
        )
        return Response(RecipeRunDetailSerializer(run).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        if instance.production_batch_id:
            pb = instance.production_batch
            if pb.otk_status != ProductionBatch.OTK_PENDING:
                return _err(
                    'conflict',
                    'Запуск нельзя редактировать: партия уже прошла ОТК.',
                    http_status=status.HTTP_409_CONFLICT,
                )
        try:
            out_scale = _parse_output_scale(request.data)
        except ValueError:
            return _err(
                'validation_error',
                'output_scale / scale должны быть числом > 0',
                errors=[{'field': 'output_scale', 'message': 'Должно быть > 0'}],
            )
        ser = RecipeRunWriteSerializer(instance, data=request.data, partial=partial)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            RecipeRun.objects.select_for_update().get(pk=instance.pk)
            ser.save()
            run_locked = RecipeRun.objects.select_for_update().get(pk=instance.pk)
            if run_locked.production_batch_id:
                pb = ProductionBatch.objects.filter(pk=run_locked.production_batch_id).first()
                if pb and pb.otk_status == ProductionBatch.OTK_PENDING:
                    qty_raw = request.data.get('quantity')
                    override = None if qty_raw is None or qty_raw == '' else qty_raw
                    submit_recipe_run_to_otk(
                        run_locked, request.user, quantity_override=override, output_scale=out_scale,
                    )
        run = (
            RecipeRun.objects.select_related(
                'recipe',
                'line',
                'production_batch',
                'production_batch__order',
                'production_batch__order__recipe',
                'production_batch__order__line',
            )
            .prefetch_related(*_recipe_run_detail_prefetches())
            .get(pk=instance.pk)
        )
        self._log_activity('update', run)
        return Response(RecipeRunDetailSerializer(run).data)

    @action(detail=True, methods=['post'], url_path='submit-to-otk')
    def submit_to_otk(self, request, pk=None):
        """Создать/привязать партию ОТК, если ещё нет (или вернуть существующую). Опционально quantity в теле."""
        run = self.get_object()
        before_run = instance_to_snapshot(run)
        already = run.production_batch_id is not None
        try:
            out_scale = _parse_output_scale(request.data)
        except ValueError:
            return _err(
                'validation_error',
                'output_scale / scale должны быть числом > 0',
                errors=[{'field': 'output_scale', 'message': 'Должно быть > 0'}],
            )
        qty_raw = request.data.get('quantity')
        override = None if qty_raw is None or qty_raw == '' else qty_raw
        try:
            batch = submit_recipe_run_to_otk(
                run, request.user, quantity_override=override, output_scale=out_scale,
            )
        except DRFValidationError as exc:
            v_errors = _extract_validation_errors(exc.detail)
            v_msg = v_errors[0]['message'] if v_errors else 'Ошибка валидации'
            return _make_error_response('validation_error', v_msg, errors=v_errors, http_status=400)
        run.refresh_from_db()
        run = (
            RecipeRun.objects.select_related(
                'recipe',
                'line',
                'production_batch',
                'production_batch__order',
                'production_batch__order__recipe',
                'production_batch__order__line',
            )
            .prefetch_related(*_recipe_run_detail_prefetches())
            .get(pk=run.pk)
        )
        payload = {
            'production_batch': BatchListSerializer(batch).data,
            'recipe_run': RecipeRunDetailSerializer(run).data,
        }
        if already:
            payload['already_submitted'] = True
        after_run = instance_to_snapshot(run)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='Производство',
            description=f'Отправка запуска #{run.pk} в ОТК (партия {batch.pk})',
            action='update',
            model_cls=RecipeRun,
            before=before_run,
            after=after_run,
            after_instance=run,
            payload_extra={
                'endpoint': 'POST /api/production/recipe-runs/{id}/submit-to-otk/',
                'production_batch_id': batch.pk,
                'already_had_batch': already,
            },
        )
        return Response(payload, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        with transaction.atomic():
            run = (
                RecipeRun.objects.select_for_update()
                .select_related('production_batch', 'production_batch__order', 'production_batch__recipe')
                .get(pk=instance.pk)
            )
            pb = run.production_batch
            if pb is not None:
                if pb.otk_status != ProductionBatch.OTK_PENDING:
                    raise RecipeRunDeleteConflict()
                reverse_production_batch_stock(
                    batch_id=pb.pk,
                    recipe=pb.recipe if pb.recipe_id else None,
                    total_meters=Decimal(str(pb.total_meters)),
                )
                order_pk = pb.order_id
                RecipeRun.objects.filter(pk=run.pk).update(production_batch_id=None)
                ProductionBatch.objects.filter(pk=pb.pk).delete()
                if order_pk:
                    ord_row = Order.objects.filter(pk=order_pk).first()
                    if ord_row is not None and not ord_row.batches.exists():
                        Order.objects.filter(pk=order_pk).delete()
        super().perform_destroy(instance)
