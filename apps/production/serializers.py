from decimal import Decimal
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers

from config.decimal_format import format_decimal_plain
from config.fields import CleanDecimalField

from apps.accounts.models import User
from apps.recipes.models import PlasticProfile, Recipe, RecipeComponent
from apps.recipes.serializers import RecipeSerializer
from config.exceptions import LineShiftPausedForRecipeRun
from .batch_stock import apply_production_batch_stock_and_cost, reverse_production_batch_stock
from .shift_state import (
    line_current_shift_open_event,
    line_current_shift_params_event,
    line_shift_is_open,
    line_shift_is_paused,
    line_shift_pause_reason,
)
from .models import (
    Line,
    LineHistory,
    ProductionBatch,
    RecipeRun,
    RecipeRunBatch,
    RecipeRunBatchComponent,
    Shift,
    ShiftComplaint,
    ShiftNote,
)


def _recipe_display_name(obj) -> Optional[str]:
    """Человекочитаемое имя рецепта (Order / RecipeRun / ProductionBatch): живой FK или снимок."""
    if getattr(obj, 'recipe_id', None):
        try:
            n = (obj.recipe.recipe or '').strip()
            if n:
                return n
        except ObjectDoesNotExist:
            pass
    snap = (getattr(obj, 'recipe_name_snapshot', None) or '').strip()
    return snap or None


def _line_display_name(obj) -> Optional[str]:
    if getattr(obj, 'line_id', None):
        try:
            return (obj.line.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    snap = (getattr(obj, 'line_name_snapshot', None) or '').strip()
    return snap or None


def _recipe_run_display_recipe(run) -> Optional[str]:
    """Имя рецепта для запуска: снимок на запуске, затем заказ связанной партии ОТК."""
    n = _recipe_display_name(run)
    if n:
        return n
    pb = getattr(run, 'production_batch', None)
    if pb is None:
        return None
    try:
        order = pb.order
    except ObjectDoesNotExist:
        return None
    snap = (getattr(order, 'recipe_name_snapshot', None) or '').strip()
    if snap:
        return snap
    if getattr(order, 'recipe_id', None):
        try:
            return (order.recipe.recipe or '').strip() or None
        except ObjectDoesNotExist:
            pass
    return (getattr(order, 'product', None) or '').strip() or None


def _recipe_run_release_qty_decimal(obj) -> Optional[Decimal]:
    """Выпуск для ОТК: total_meters партии, иначе норма рецепта (не сумма ёмкостей)."""
    if getattr(obj, 'production_batch_id', None):
        try:
            pb = obj.production_batch
            return Decimal(str(pb.total_meters))
        except ObjectDoesNotExist:
            pass
    if getattr(obj, 'recipe_id', None):
        try:
            rq = obj.recipe.output_quantity
            if rq is not None:
                return Decimal(str(rq))
        except ObjectDoesNotExist:
            pass
    return None


def _recipe_norm_output_str(obj) -> Optional[str]:
    if not getattr(obj, 'recipe_id', None):
        return None
    try:
        rq = obj.recipe.output_quantity
        if rq is None:
            return None
        return format_decimal_plain(rq)
    except ObjectDoesNotExist:
        return None


def _recipe_output_unit_kind_value(obj) -> Optional[str]:
    if not getattr(obj, 'recipe_id', None):
        return None
    try:
        return obj.recipe.output_unit_kind
    except ObjectDoesNotExist:
        return None


def _recipe_run_display_line(run) -> Optional[str]:
    n = _line_display_name(run)
    if n:
        return n
    pb = getattr(run, 'production_batch', None)
    if pb is None:
        return None
    try:
        order = pb.order
    except ObjectDoesNotExist:
        return None
    snap = (getattr(order, 'line_name_snapshot', None) or '').strip()
    if snap:
        return snap
    if getattr(order, 'line_id', None):
        try:
            return (order.line.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    return None


class LineSerializer(serializers.ModelSerializer):
    shift_is_open = serializers.SerializerMethodField()
    shift_is_paused = serializers.SerializerMethodField()
    shift_pause_reason = serializers.SerializerMethodField()
    shift_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = Line
        fields = (
            'id',
            'name',
            'code',
            'notes',
            'is_active',
            'shift_is_open',
            'shift_is_paused',
            'shift_pause_reason',
            'shift_snapshot',
        )

    def get_shift_is_open(self, obj):
        m = self.context.get('line_histories') or {}
        hist = m.get(obj.pk)
        return line_shift_is_open(obj, histories=hist)

    def get_shift_is_paused(self, obj):
        m = self.context.get('line_histories') or {}
        hist = m.get(obj.pk)
        return line_shift_is_paused(obj, histories=hist)

    def get_shift_pause_reason(self, obj):
        m = self.context.get('line_histories') or {}
        hist = m.get(obj.pk)
        return line_shift_pause_reason(obj, histories=hist)

    def get_shift_snapshot(self, obj):
        m = self.context.get('line_histories') or {}
        hist = m.get(obj.pk)
        if not line_shift_is_open(obj, histories=hist):
            return None
        ev_open = line_current_shift_open_event(obj, histories=hist)
        if ev_open is None:
            return None
        ev_params = line_current_shift_params_event(obj, histories=hist)
        if ev_params is None:
            return None
        from datetime import datetime, time as time_cls
        from django.utils import timezone as dj_tz

        t = ev_open.time or time_cls.min
        dt = datetime.combine(ev_open.date, t)
        opened_at = dj_tz.make_aware(dt) if dj_tz.is_naive(dt) else dt
        opener = ev_open.user.name if ev_open.user_id else None
        cmt = (ev_params.comment or '').strip() or None
        return {
            'height': float(ev_params.height) if ev_params.height is not None else None,
            'width': float(ev_params.width) if ev_params.width is not None else None,
            'angle_deg': float(ev_params.angle_deg) if ev_params.angle_deg is not None else None,
            'comment': cmt,
            'opened_by': opener,
            'opened_by_name': opener,
            'opened_at': opened_at.isoformat(),
            'session_title': ev_open.session_title or None,
            'is_paused': line_shift_is_paused(obj, histories=hist),
            'pause_reason': line_shift_pause_reason(obj, histories=hist),
        }

    def to_representation(self, instance):
        if instance is None:
            return None
        ret = super().to_representation(instance)
        ret['comment'] = ret.get('notes') or ''
        return ret


class ShiftNoteSerializer(serializers.ModelSerializer):
    note = serializers.CharField(source='text', read_only=True)

    class Meta:
        model = ShiftNote
        fields = ('id', 'note', 'created_at')


class ShiftSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    line_label = serializers.SerializerMethodField()
    notes_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Shift
        fields = (
            'id', 'line', 'line_name', 'line_label', 'line_name_snapshot', 'former_line_id',
            'user', 'user_name',
            'opened_at', 'closed_at', 'status', 'comment', 'notes_count',
        )
        read_only_fields = ('line_name_snapshot', 'former_line_id',)

    def get_line_label(self, obj):
        return self.get_line_name(obj)

    def get_user_name(self, obj):
        if obj.user_id:
            try:
                return obj.user.name
            except ObjectDoesNotExist:
                return None
        return None

    def get_line_name(self, obj):
        return _line_display_name(obj)


class ShiftDetailSerializer(ShiftSerializer):
    """Расширенный сериализатор для GET /api/shifts/{id}/ — включает notes."""
    notes = serializers.SerializerMethodField()

    class Meta(ShiftSerializer.Meta):
        fields = ShiftSerializer.Meta.fields + ('notes',)

    def get_notes(self, obj):
        qs = obj.notes.order_by('-created_at')
        return ShiftNoteSerializer(qs, many=True).data


class ShiftComplaintListSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    mentioned_users = serializers.SerializerMethodField()

    class Meta:
        model = ShiftComplaint
        fields = ('id', 'body', 'created_at', 'author', 'mentioned_users', 'shift_id')

    def get_author(self, obj):
        u = obj.author
        return {'id': u.pk, 'name': u.name, 'username': u.email}

    def get_mentioned_users(self, obj):
        return [{'id': u.pk, 'name': u.name, 'username': u.email} for u in obj.mentioned_users.all()]


class ShiftComplaintCreateSerializer(serializers.ModelSerializer):
    mentioned_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )
    shift_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = ShiftComplaint
        fields = ('body', 'mentioned_user_ids', 'shift_id')

    def validate_body(self, value):
        s = (value or '').strip()
        if not s:
            raise serializers.ValidationError('Укажите текст жалобы.')
        return s

    def validate(self, attrs):
        request = self.context['request']
        shift_raw = attrs.pop('shift_id', None)
        if shift_raw is not None:
            sh = Shift.objects.filter(pk=shift_raw).first()
            if not sh:
                raise serializers.ValidationError({'shift_id': 'Смена не найдена'})
            if sh.user_id != request.user.pk:
                raise serializers.ValidationError({'shift_id': 'Можно указать только свою смену'})
            attrs['shift'] = sh

        ids = attrs.get('mentioned_user_ids', []) or []
        ids = list(dict.fromkeys(ids))
        ids = [i for i in ids if i != request.user.pk]
        attrs['mentioned_user_ids'] = ids
        if ids:
            found = set(User.objects.filter(pk__in=ids).values_list('pk', flat=True))
            missing = [i for i in ids if i not in found]
            if missing:
                raise serializers.ValidationError(
                    {'mentioned_user_ids': f'Неизвестные пользователи: {missing}'},
                )
        return attrs

    def create(self, validated_data):
        ids = validated_data.pop('mentioned_user_ids', [])
        author = self.context['request'].user
        complaint = ShiftComplaint.objects.create(author=author, **validated_data)
        if ids:
            complaint.mentioned_users.set(User.objects.filter(pk__in=ids))
        return complaint


class LineShiftSnapshotSerializer(serializers.Serializer):
    """Снимок параметров для open / close / params_update."""

    height = serializers.DecimalField(max_digits=10, decimal_places=2)
    width = serializers.DecimalField(max_digits=10, decimal_places=2)
    angle_deg = serializers.DecimalField(max_digits=8, decimal_places=2)
    comment = serializers.CharField(required=False, allow_blank=True, default='')


class LineShiftOpenSerializer(LineShiftSnapshotSerializer):
    session_title = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')


class LineShiftPauseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=4000)

    def validate_reason(self, value):
        s = (value or '').strip()
        if not s:
            raise serializers.ValidationError('Укажите непустую причину остановки.')
        return s


class LineHistorySerializer(serializers.ModelSerializer):
    line_name = serializers.SerializerMethodField()
    line_label = serializers.SerializerMethodField()
    date = serializers.DateField(format='%Y-%m-%d')
    time = serializers.TimeField(format='%H:%M')
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    angle_deg = serializers.SerializerMethodField()
    session_title = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()
    pause_reason = serializers.SerializerMethodField()
    opened_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LineHistory
        fields = (
            'id', 'line_id', 'date', 'time', 'line_name', 'line_label',
            'line_name_snapshot', 'former_line_id',
            'action',
            'user',
            'height', 'width', 'angle_deg', 'comment', 'session_title',
            'reason', 'pause_reason',
            'opened_by_name',
        )
        read_only_fields = ('line_name_snapshot', 'former_line_id')

    def get_line_name(self, obj):
        return _line_display_name(obj)

    def get_line_label(self, obj):
        return self.get_line_name(obj)

    def get_height(self, obj):
        return float(obj.height) if obj.height is not None else None

    def get_width(self, obj):
        return float(obj.width) if obj.width is not None else None

    def get_angle_deg(self, obj):
        return float(obj.angle_deg) if obj.angle_deg is not None else None

    def get_session_title(self, obj):
        if obj.action != LineHistory.ACTION_OPEN:
            return None
        return obj.session_title or None

    def get_reason(self, obj):
        if obj.action != LineHistory.ACTION_SHIFT_PAUSE:
            return None
        s = (obj.comment or '').strip()
        return s or None

    def get_pause_reason(self, obj):
        return self.get_reason(obj)

    def get_opened_by_name(self, obj):
        if obj.action != LineHistory.ACTION_OPEN:
            return None
        if obj.user_id:
            try:
                return obj.user.name
            except ObjectDoesNotExist:
                return None
        return None


class ProductionBatchSerializer(serializers.ModelSerializer):
    order_product = serializers.CharField(source='order.product', read_only=True)

    class Meta:
        model = ProductionBatch
        fields = (
            'id', 'order', 'order_product', 'profile', 'recipe', 'line', 'shift',
            'product', 'pieces', 'length_per_piece', 'total_meters', 'quantity',
            'operator', 'date', 'produced_at', 'comment', 'otk_status',
            'material_cost_total', 'cost_per_meter', 'cost_per_piece',
        )
        extra_kwargs = {'operator': {'allow_null': True}}


_ALLOWED_BATCH_CREATE_FIELDS = frozenset({
    'profile', 'recipe', 'line', 'pieces', 'length_per_piece', 'comment', 'date', 'produced_at', 'product',
})


class ProductionBatchCreateUpdateSerializer(serializers.ModelSerializer):
    """Создание/правка партии профиля: total_meters и себестоимость только с сервера (FIFO)."""

    total_meters = serializers.DecimalField(max_digits=16, decimal_places=4, read_only=True)

    class Meta:
        model = ProductionBatch
        fields = (
            'id', 'profile', 'recipe', 'line', 'product',
            'pieces', 'length_per_piece', 'total_meters',
            'date', 'produced_at', 'comment',
        )
        extra_kwargs = {
            'product': {'required': False, 'allow_blank': True},
            'produced_at': {'required': False, 'allow_null': True},
        }

    def to_internal_value(self, data):
        if self.instance is None and hasattr(data, 'keys'):
            extra = set(data.keys()) - _ALLOWED_BATCH_CREATE_FIELDS
            if extra:
                raise serializers.ValidationError(
                    {k: 'Поле не принимается сервером' for k in sorted(extra)},
                )
        return super().to_internal_value(data)

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        initial = getattr(self, 'initial_data', {}) or {}

        if self.instance is not None:
            forbidden = set(initial.keys()) & {
                'profile', 'recipe', 'line', 'shift', 'order', 'total_meters', 'material_cost_total',
                'cost_per_meter', 'cost_per_piece', 'cost_price', 'quantity', 'product',
            }
            if forbidden:
                raise serializers.ValidationError(
                    {k: 'Нельзя менять это поле после создания партии' for k in sorted(forbidden)},
                )

            if self.instance.lifecycle_status != ProductionBatch.LIFECYCLE_PENDING:
                bad = set(initial.keys()) - {'comment'}
                if bad:
                    raise serializers.ValidationError(
                        'После отправки в ОТК можно менять только поле comment',
                    )

        line = attrs.get('line')
        if line is None and self.instance is not None:
            line = self.instance.line
        if line is None:
            raise serializers.ValidationError({'line': 'Укажите линию'})

        hist_map = self.context.get('line_histories') or {}
        hist = hist_map.get(line.pk) if getattr(line, 'pk', None) else None
        if getattr(line, 'is_active', True) is False:
            raise serializers.ValidationError({'line': 'Линия неактивна'})
        if not line_shift_is_open(line, histories=hist):
            raise serializers.ValidationError(
                {'line': 'На линии нет открытой смены'},
            )
        if line_shift_is_paused(line, histories=hist):
            raise serializers.ValidationError(
                {'line': 'Смена на линии остановлена (пауза). Возобновите смену или выберите другую линию.'},
            )

        shift = Shift.objects.filter(
            user=user,
            line=line,
            closed_at__isnull=True,
            status=Shift.STATUS_OPEN,
        ).first()
        if not shift:
            raise serializers.ValidationError(
                {'shift': 'Нет активной открытой смены на этой линии для текущего пользователя.'},
            )
        if shift.line_id and line is not None and shift.line_id != line.pk:
            raise serializers.ValidationError({'line': 'Смена привязана к другой линии'})

        profile = attrs.get('profile')
        if profile is None and self.instance is not None:
            profile = self.instance.profile
        recipe = attrs.get('recipe')
        if recipe is None and self.instance is not None:
            recipe = self.instance.recipe
        err = {}
        if profile is None:
            err['profile'] = 'Обязательно укажите профиль'
        if recipe is None:
            err['recipe'] = 'Обязательно укажите рецепт'
        if err:
            raise serializers.ValidationError(err)
        if recipe.profile_id != profile.pk:
            raise serializers.ValidationError(
                {'recipe': 'Рецепт не относится к выбранному профилю'},
            )
        if not recipe.components.exists():
            raise serializers.ValidationError({'recipe': 'У рецепта нет компонентов'})

        pieces = attrs.get('pieces', getattr(self.instance, 'pieces', None))
        length = attrs.get('length_per_piece', getattr(self.instance, 'length_per_piece', None))
        if pieces is not None and int(pieces) <= 0:
            raise serializers.ValidationError({'pieces': 'Должно быть > 0'})
        if length is not None and Decimal(str(length)) <= 0:
            raise serializers.ValidationError({'length_per_piece': 'Должно быть > 0'})

        attrs['_shift'] = shift
        return attrs

    def create(self, validated_data):
        from django.utils import timezone

        shift = validated_data.pop('_shift')
        validated_data['shift'] = shift
        validated_data['operator'] = self.context['request'].user
        prof = validated_data['profile']
        if not (validated_data.get('product') or '').strip():
            validated_data['product'] = (prof.name or '')[:255]
        if validated_data.get('date') is None:
            validated_data['date'] = timezone.now().date()
        if validated_data.get('produced_at') is None:
            validated_data['produced_at'] = timezone.now()
        validated_data['otk_status'] = ProductionBatch.OTK_PENDING
        validated_data.setdefault('lifecycle_status', ProductionBatch.LIFECYCLE_PENDING)
        validated_data.setdefault('sent_to_otk', False)
        validated_data.setdefault('in_otk_queue', False)
        with transaction.atomic():
            batch = ProductionBatch.objects.create(**validated_data)
            apply_production_batch_stock_and_cost(batch)
        return batch

    def update(self, instance, validated_data):
        if RecipeRun.objects.filter(production_batch=instance).exists():
            raise serializers.ValidationError(
                {'non_field_errors': 'Партия связана с замесом (recipe-run): изменение через замес недоступно.'},
            )

        validated_data.pop('_shift', None)

        if instance.lifecycle_status != ProductionBatch.LIFECYCLE_PENDING:
            comment = validated_data.pop('comment', serializers.empty)
            if validated_data:
                raise serializers.ValidationError(
                    'После отправки в ОТК можно менять только поле comment',
                )
            if comment is serializers.empty:
                return instance
            instance.comment = comment
            instance.save(update_fields=['comment'])
            return instance

        if instance.otk_status != ProductionBatch.OTK_PENDING:
            raise serializers.ValidationError({'otk_status': 'Можно менять только партию в статусе «ожидает ОТК»'})

        old_recipe = instance.recipe
        old_tm = Decimal(str(instance.total_meters))

        with transaction.atomic():
            reverse_production_batch_stock(
                batch_id=instance.pk,
                recipe=old_recipe,
                total_meters=old_tm,
            )
            for k, v in validated_data.items():
                setattr(instance, k, v)
            prof = instance.profile
            if prof and not (getattr(instance, 'product', None) or '').strip():
                instance.product = (prof.name or '')[:255]
            instance.save()
            apply_production_batch_stock_and_cost(instance)
        return instance


class BatchListSerializer(serializers.ModelSerializer):
    """
    Контракт GET /api/batches/: id, order_name, product_name, quantity/released,
    operator_name, date, created_at, otk_status, otk_accepted, otk_defect,
    otk_defect_reason, otk_comment, otk_inspector, otk_checked_at.
    Дополнительно: recipe_name, recipe_output_quantity (норма по рецепту, справочно для ОТК).
    """
    order_name = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product', read_only=True)
    released = serializers.DecimalField(source='total_meters', max_digits=14, decimal_places=4, read_only=True)
    operator_name = serializers.SerializerMethodField()
    created_at = serializers.DateField(source='date', read_only=True)
    otk_accepted = serializers.SerializerMethodField()
    otk_defect = serializers.SerializerMethodField()
    otk_defect_reason = serializers.SerializerMethodField()
    otk_comment = serializers.SerializerMethodField()
    otk_inspector = serializers.SerializerMethodField()
    otk_inspector_name = serializers.SerializerMethodField()
    otk_checked_at = serializers.SerializerMethodField()
    recipe_name = serializers.SerializerMethodField()
    recipe_label = serializers.SerializerMethodField()
    recipe_name_snapshot = serializers.SerializerMethodField()
    former_recipe_id = serializers.SerializerMethodField()
    recipe_output_quantity = serializers.SerializerMethodField()
    recipe_output_unit_kind = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    line_label = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    angle_deg = serializers.SerializerMethodField()
    otk_status_display = serializers.SerializerMethodField()

    class Meta:
        model = ProductionBatch
        fields = (
            'id', 'order', 'order_name', 'profile', 'recipe', 'line', 'shift',
            'product', 'product_name', 'pieces', 'length_per_piece', 'total_meters',
            'quantity', 'released',
            'operator', 'operator_name', 'date', 'created_at',
            'otk_status', 'otk_status_display', 'otk_accepted', 'otk_defect', 'otk_defect_reason',
            'otk_comment', 'otk_inspector', 'otk_inspector_name', 'otk_checked_at',
            'recipe_name', 'recipe_label', 'recipe_name_snapshot', 'former_recipe_id',
            'recipe_output_quantity', 'recipe_output_unit_kind',
            'line_name', 'line_label', 'height', 'width', 'angle_deg',
            'material_cost_total', 'cost_per_meter', 'cost_per_piece',
            'lifecycle_status', 'sent_to_otk', 'in_otk_queue', 'otk_submitted_at',
        )

    def _last_check(self, obj):
        if not hasattr(obj, '_last_otk_check'):
            obj._last_otk_check = obj.otk_checks.select_related('inspector').order_by('-checked_date').first()
        return obj._last_otk_check

    def get_order_name(self, obj):
        if not obj.order_id:
            return None
        try:
            return obj.order.product
        except ObjectDoesNotExist:
            return None

    def get_operator_name(self, obj):
        return obj.operator.name if obj.operator_id else None

    def get_otk_accepted(self, obj):
        c = self._last_check(obj)
        return float(c.accepted) if c else None

    def get_otk_defect(self, obj):
        c = self._last_check(obj)
        return float(c.rejected) if c else None

    def get_otk_defect_reason(self, obj):
        c = self._last_check(obj)
        return c.reject_reason if c else None

    def get_otk_comment(self, obj):
        c = self._last_check(obj)
        return (c.comment or '') if c else None

    def get_otk_inspector(self, obj):
        c = self._last_check(obj)
        return c.inspector_id if c and c.inspector_id else None

    def get_otk_inspector_name(self, obj):
        c = self._last_check(obj)
        if not c:
            return None
        n = (c.inspector_name or '').strip()
        if n:
            return n
        if c.inspector_id:
            try:
                return (c.inspector.name or '').strip() or None
            except ObjectDoesNotExist:
                return None
        return None

    def get_otk_checked_at(self, obj):
        c = self._last_check(obj)
        if c and c.checked_date:
            return c.checked_date.isoformat()
        return None

    def get_recipe_name(self, obj):
        if getattr(obj, 'recipe_id', None):
            return _recipe_display_name(obj)
        if not obj.order_id:
            return None
        return _recipe_display_name(obj.order)

    def get_recipe_label(self, obj):
        return self.get_recipe_name(obj)

    def get_recipe_name_snapshot(self, obj):
        if getattr(obj, 'recipe_id', None):
            try:
                return (obj.recipe.recipe or '').strip() or None
            except ObjectDoesNotExist:
                pass
        if not obj.order_id:
            return None
        return obj.order.recipe_name_snapshot or None

    def get_former_recipe_id(self, obj):
        if getattr(obj, 'recipe_id', None):
            return None
        if not obj.order_id:
            return None
        return obj.order.former_recipe_id

    def get_recipe_output_quantity(self, obj):
        rec = None
        if getattr(obj, 'recipe_id', None):
            try:
                rec = obj.recipe
            except ObjectDoesNotExist:
                rec = None
        elif obj.order_id and getattr(obj.order, 'recipe_id', None):
            try:
                rec = obj.order.recipe
            except ObjectDoesNotExist:
                rec = None
        if rec is None:
            return None
        rq = getattr(rec, 'output_quantity', None)
        if rq is not None:
            return format_decimal_plain(rq)
        return None

    def get_recipe_output_unit_kind(self, obj):
        rec = None
        if getattr(obj, 'recipe_id', None):
            try:
                rec = obj.recipe
            except ObjectDoesNotExist:
                rec = None
        elif obj.order_id and getattr(obj.order, 'recipe_id', None):
            try:
                rec = obj.order.recipe
            except ObjectDoesNotExist:
                rec = None
        if rec is None:
            return None
        return getattr(rec, 'output_unit_kind', None)

    def get_otk_status_display(self, obj):
        return obj.get_otk_status_display()

    def get_height(self, obj):
        return float(obj.shift_height) if obj.shift_height is not None else None

    def get_width(self, obj):
        return float(obj.shift_width) if obj.shift_width is not None else None

    def get_angle_deg(self, obj):
        return float(obj.shift_angle_deg) if obj.shift_angle_deg is not None else None

    def get_line_name(self, obj):
        if getattr(obj, 'line_id', None):
            return _line_display_name(obj)
        if not obj.order_id:
            return None
        return _line_display_name(obj.order)

    def get_line_label(self, obj):
        return self.get_line_name(obj)


# ——— Запуски по рецепту (Химия → Элементы) ———

# Текст, если в БД не осталось ни FK, ни снимка (старые данные / частичное удаление).
_RECIPE_RUN_COMPONENT_LABEL_FALLBACK = 'Наименование недоступно (удалено из справочника)'


def _recipe_component_for_batch_line(obj: RecipeRunBatchComponent):
    if not getattr(obj, 'recipe_component_id', None):
        return None
    try:
        return obj.recipe_component
    except ObjectDoesNotExist:
        return None


def _material_label_for_batch_component(obj: RecipeRunBatchComponent) -> Optional[str]:
    if obj.raw_material_id:
        try:
            return (obj.raw_material.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    s = (getattr(obj, 'material_name_snapshot', None) or '').strip()
    if s:
        return s
    rc = _recipe_component_for_batch_line(obj)
    if rc is not None and rc.type == RecipeComponent.TYPE_RAW and rc.raw_material_id:
        try:
            return (rc.raw_material.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    return None


def _chemistry_label_for_batch_component(obj: RecipeRunBatchComponent) -> Optional[str]:
    if obj.chemistry_id:
        try:
            return (obj.chemistry.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    s = (getattr(obj, 'chemistry_name_snapshot', None) or '').strip()
    if s:
        return s
    rc = _recipe_component_for_batch_line(obj)
    if rc is not None and rc.type == RecipeComponent.TYPE_CHEM and rc.chemistry_id:
        try:
            return (rc.chemistry.name or '').strip() or None
        except ObjectDoesNotExist:
            pass
    return None


def _display_label_for_batch_component(obj: RecipeRunBatchComponent) -> str:
    m = _material_label_for_batch_component(obj)
    if m:
        return m
    c = _chemistry_label_for_batch_component(obj)
    if c:
        return c
    rc = _recipe_component_for_batch_line(obj)
    if rc is not None:
        if rc.type == RecipeComponent.TYPE_CHEM and rc.chemistry_id:
            try:
                n = (rc.chemistry.name or '').strip()
                if n:
                    return n
            except ObjectDoesNotExist:
                pass
        if rc.type == RecipeComponent.TYPE_RAW and rc.raw_material_id:
            try:
                n = (rc.raw_material.name or '').strip()
                if n:
                    return n
            except ObjectDoesNotExist:
                pass
        if rc.chemistry_id:
            try:
                n = (rc.chemistry.name or '').strip()
                if n:
                    return n
            except ObjectDoesNotExist:
                pass
        if rc.raw_material_id:
            try:
                n = (rc.raw_material.name or '').strip()
                if n:
                    return n
            except ObjectDoesNotExist:
                pass
    return _RECIPE_RUN_COMPONENT_LABEL_FALLBACK


class RecipeRunBatchComponentSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True, allow_null=True)
    chemistry_id = serializers.IntegerField(read_only=True, allow_null=True)
    recipe_component_id = serializers.IntegerField(read_only=True, allow_null=True)
    quantity = CleanDecimalField(
        max_digits=14, decimal_places=4, read_only=True, coerce_to_string=True,
    )
    material_name = serializers.SerializerMethodField()
    element_name = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    material_name_snapshot = serializers.SerializerMethodField()
    chemistry_name_snapshot = serializers.SerializerMethodField()

    def get_material_name(self, obj):
        m = _material_label_for_batch_component(obj)
        if m:
            return m
        if _chemistry_label_for_batch_component(obj):
            return None
        return _display_label_for_batch_component(obj)

    def get_element_name(self, obj):
        c = _chemistry_label_for_batch_component(obj)
        if c:
            return c
        if _material_label_for_batch_component(obj):
            return None
        return _display_label_for_batch_component(obj)

    def get_name(self, obj):
        return _display_label_for_batch_component(obj)

    def get_material_name_snapshot(self, obj):
        s = (getattr(obj, 'material_name_snapshot', None) or '').strip()
        if s:
            return s
        m = _material_label_for_batch_component(obj)
        if m:
            return m
        if _chemistry_label_for_batch_component(obj):
            return None
        return _display_label_for_batch_component(obj)

    def get_chemistry_name_snapshot(self, obj):
        s = (getattr(obj, 'chemistry_name_snapshot', None) or '').strip()
        if s:
            return s
        c = _chemistry_label_for_batch_component(obj)
        if c:
            return c
        if _material_label_for_batch_component(obj):
            return None
        return _display_label_for_batch_component(obj)

    class Meta:
        model = RecipeRunBatchComponent
        fields = (
            'id', 'material_id', 'material_name', 'material_name_snapshot',
            'chemistry_id', 'element_name', 'chemistry_name_snapshot', 'name',
            'quantity', 'unit', 'recipe_component_id',
        )


class RecipeRunBatchSerializer(serializers.ModelSerializer):
    quantity = CleanDecimalField(
        max_digits=14, decimal_places=4, read_only=True, allow_null=True, coerce_to_string=True,
    )
    components = RecipeRunBatchComponentSerializer(many=True, read_only=True)

    class Meta:
        model = RecipeRunBatch
        fields = ('id', 'index', 'label', 'quantity', 'components')


class RecipeRunListSerializer(serializers.ModelSerializer):
    recipe = serializers.SerializerMethodField()
    recipe_name = serializers.SerializerMethodField()
    recipe_label = serializers.SerializerMethodField()
    effective_recipe_id = serializers.SerializerMethodField()
    line = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    line_label = serializers.SerializerMethodField()
    batches = RecipeRunBatchSerializer(many=True, read_only=True)
    batches_count = serializers.IntegerField(read_only=True)
    total_quantity = serializers.SerializerMethodField()
    output_quantity = serializers.SerializerMethodField()
    recipe_output_quantity = serializers.SerializerMethodField()
    output_unit_kind = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = RecipeRun
        fields = (
            'id', 'recipe', 'recipe_name', 'recipe_label', 'effective_recipe_id',
            'recipe_name_snapshot', 'former_recipe_id',
            'line', 'line_name', 'line_label',
            'line_name_snapshot', 'former_line_id',
            'batches', 'batches_count',
            'total_quantity', 'output_quantity', 'recipe_output_quantity', 'output_unit_kind',
            'created_at', 'summary', 'production_batch_id',
        )
        read_only_fields = (
            'line_name_snapshot', 'former_line_id',
            'recipe_name_snapshot', 'former_recipe_id',
        )

    def get_recipe(self, obj):
        if obj.recipe_id:
            try:
                return RecipeSerializer(obj.recipe, context=self.context).data
            except ObjectDoesNotExist:
                pass
        label = _recipe_run_display_recipe(obj)
        if not label and obj.former_recipe_id is None:
            return None
        return {
            'id': obj.former_recipe_id,
            'recipe': label,
            'product': None,
            'components': [],
            'output_quantity': None,
            'output_unit_kind': None,
        }

    def get_line(self, obj):
        if obj.line_id:
            try:
                return LineSerializer(obj.line).data
            except ObjectDoesNotExist:
                pass
        name = _recipe_run_display_line(obj)
        if not name and obj.former_line_id is None:
            return None
        return {'id': obj.former_line_id, 'name': name}

    def get_recipe_name(self, obj):
        return _recipe_run_display_recipe(obj)

    def get_recipe_label(self, obj):
        return self.get_recipe_name(obj)

    def get_effective_recipe_id(self, obj):
        return obj.recipe_id or obj.former_recipe_id

    def get_line_name(self, obj):
        return _recipe_run_display_line(obj)

    def get_line_label(self, obj):
        return self.get_line_name(obj)

    def get_total_quantity(self, obj):
        q = _recipe_run_release_qty_decimal(obj)
        return format_decimal_plain(q) if q is not None else None

    def get_output_quantity(self, obj):
        return self.get_total_quantity(obj)

    def get_recipe_output_quantity(self, obj):
        return _recipe_norm_output_str(obj)

    def get_output_unit_kind(self, obj):
        return _recipe_output_unit_kind_value(obj)

    def get_summary(self, obj):
        n = getattr(obj, 'batches_count', None)
        if n is None:
            n = obj.batches.count()
        rel = _recipe_run_release_qty_decimal(obj)
        if rel is not None:
            return f'{n} ёмкостей, выпуск {format_decimal_plain(rel)}'
        return f'{n} ёмкостей'


class RecipeRunDetailSerializer(serializers.ModelSerializer):
    recipe = serializers.SerializerMethodField()
    recipe_name = serializers.SerializerMethodField()
    recipe_label = serializers.SerializerMethodField()
    recipe_snapshot = serializers.SerializerMethodField()
    line = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    line_label = serializers.SerializerMethodField()
    line_snapshot = serializers.SerializerMethodField()
    effective_recipe_id = serializers.SerializerMethodField()
    batches = RecipeRunBatchSerializer(many=True, read_only=True)
    batches_count = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()
    output_quantity = serializers.SerializerMethodField()
    recipe_output_quantity = serializers.SerializerMethodField()
    output_unit_kind = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    production_batch = serializers.SerializerMethodField()

    class Meta:
        model = RecipeRun
        fields = (
            'id', 'recipe', 'recipe_name', 'recipe_label', 'recipe_snapshot',
            'recipe_name_snapshot', 'former_recipe_id', 'effective_recipe_id',
            'line', 'line_name', 'line_label', 'line_snapshot',
            'line_name_snapshot', 'former_line_id',
            'batches', 'batches_count',
            'total_quantity', 'output_quantity', 'recipe_output_quantity', 'output_unit_kind',
            'created_at', 'summary', 'production_batch_id', 'production_batch',
        )
        read_only_fields = (
            'line_name_snapshot', 'former_line_id',
            'recipe_name_snapshot', 'former_recipe_id',
        )

    def get_recipe(self, obj):
        if obj.recipe_id:
            try:
                return RecipeSerializer(obj.recipe, context=self.context).data
            except ObjectDoesNotExist:
                pass
        label = _recipe_run_display_recipe(obj)
        if not label and obj.former_recipe_id is None:
            return None
        return {
            'id': obj.former_recipe_id,
            'recipe': label,
            'product': None,
            'components': [],
            'output_quantity': None,
            'output_unit_kind': None,
        }

    def get_line(self, obj):
        if obj.line_id:
            try:
                return LineSerializer(obj.line).data
            except ObjectDoesNotExist:
                pass
        name = _recipe_run_display_line(obj)
        if not name and obj.former_line_id is None:
            return None
        return {'id': obj.former_line_id, 'name': name}

    def get_recipe_name(self, obj):
        return _recipe_run_display_recipe(obj)

    def get_recipe_label(self, obj):
        return self.get_recipe_name(obj)

    def get_line_name(self, obj):
        return _recipe_run_display_line(obj)

    def get_line_label(self, obj):
        return self.get_line_name(obj)

    def get_recipe_snapshot(self, obj):
        if obj.recipe_id:
            try:
                r = obj.recipe
                return {
                    'id': r.pk,
                    'recipe': r.recipe,
                    'product': r.product,
                    'source': 'live',
                }
            except ObjectDoesNotExist:
                pass
        label = (obj.recipe_name_snapshot or '').strip() or None
        if not label:
            label = _recipe_run_display_recipe(obj)
        return {
            'id': obj.former_recipe_id,
            'recipe': label,
            'product': None,
            'source': 'snapshot',
        }

    def get_line_snapshot(self, obj):
        if obj.line_id:
            try:
                ln = obj.line
                return {'id': ln.pk, 'name': ln.name, 'source': 'live'}
            except ObjectDoesNotExist:
                pass
        name = (obj.line_name_snapshot or '').strip() or None
        if not name:
            name = _recipe_run_display_line(obj)
        return {
            'id': obj.former_line_id,
            'name': name,
            'source': 'snapshot',
        }

    def get_effective_recipe_id(self, obj):
        return obj.recipe_id or obj.former_recipe_id

    def get_production_batch(self, obj):
        bid = getattr(obj, 'production_batch_id', None)
        return {'id': bid} if bid else None

    def get_batches_count(self, obj):
        return obj.batches.count()

    def get_total_quantity(self, obj):
        q = _recipe_run_release_qty_decimal(obj)
        return format_decimal_plain(q) if q is not None else None

    def get_output_quantity(self, obj):
        return self.get_total_quantity(obj)

    def get_recipe_output_quantity(self, obj):
        return _recipe_norm_output_str(obj)

    def get_output_unit_kind(self, obj):
        return _recipe_output_unit_kind_value(obj)

    def get_summary(self, obj):
        n = obj.batches.count()
        rel = _recipe_run_release_qty_decimal(obj)
        if rel is not None:
            return f'{n} ёмкостей, выпуск {format_decimal_plain(rel)}'
        return f'{n} ёмкостей'


class RecipeRunBatchComponentInputSerializer(serializers.Serializer):
    material_id = serializers.IntegerField(required=False, allow_null=True)
    chemistry_id = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=Decimal('0.0001'))
    unit = serializers.CharField(required=False, default='кг', max_length=50)
    recipe_component_id = serializers.IntegerField(required=False, allow_null=True)


class RecipeRunBatchInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    index = serializers.IntegerField(required=False, min_value=0)
    label = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')
    quantity = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False, allow_null=True, min_value=Decimal('0.0001'),
    )
    components = RecipeRunBatchComponentInputSerializer(many=True)

    def validate(self, attrs):
        comps = attrs.get('components') or []
        if not comps:
            raise serializers.ValidationError({'components': 'Нужна хотя бы одна строка расхода (quantity > 0)'})
        recipe_id = self.context.get('recipe_id')
        for i, c in enumerate(comps):
            mid = c.get('material_id')
            ch = c.get('chemistry_id')
            has_m = mid is not None
            has_c = ch is not None
            if has_m == has_c:
                raise serializers.ValidationError(
                    {'components': f'Строка {i + 1}: укажите ровно одно из material_id, chemistry_id'}
                )
            rcid = c.get('recipe_component_id')
            if rcid is not None and recipe_id is not None:
                if not RecipeComponent.objects.filter(pk=rcid, recipe_id=recipe_id).exists():
                    raise serializers.ValidationError(
                        {'components': f'Строка {i + 1}: recipe_component_id не относится к этому рецепту'}
                    )
        return attrs


class RecipeRunWriteSerializer(serializers.Serializer):
    recipe_id = serializers.PrimaryKeyRelatedField(
        queryset=Recipe.objects.all(), source='recipe', required=False,
    )
    line_id = serializers.PrimaryKeyRelatedField(
        queryset=Line.objects.all(), source='line', required=False,
    )
    batches = serializers.ListField(
        child=serializers.DictField(allow_empty=True),
        required=False,
        allow_empty=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        updating = self.instance is not None
        self.fields['recipe_id'].required = not updating
        self.fields['line_id'].required = not updating

    def validate(self, attrs):
        recipe = attrs.get('recipe')
        if self.instance is not None:
            recipe = recipe or self.instance.recipe

        raw_batches = None
        if self.instance is None:
            if 'recipe' not in attrs or 'line' not in attrs:
                raise serializers.ValidationError('Укажите recipe_id и line_id')
            line = attrs.get('line')
            if line is not None:
                if not line_shift_is_open(line):
                    raise serializers.ValidationError(
                        {'line_id': 'На линии нет открытой смены. Откройте смену перед замесом.'},
                    )
                if line_shift_is_paused(line):
                    raise LineShiftPausedForRecipeRun()
            if 'batches' not in self.initial_data or self.initial_data.get('batches') is None:
                raise serializers.ValidationError({'batches': 'Нужна хотя бы одна партия'})
            raw_batches = self.initial_data.get('batches')
            if not raw_batches:
                raise serializers.ValidationError({'batches': 'Нужна хотя бы одна партия'})
        elif 'batches' in self.initial_data:
            # Состав ёмкостей — только план замеса; остатки не меняются (списание у ProductionBatch).
            raw_batches = self.initial_data.get('batches')
            if not raw_batches:
                raise serializers.ValidationError({
                    'batches': (
                        'Нужна хотя бы одна партия. Чтобы убрать все партии, удалите запуск: '
                        'DELETE /api/production/recipe-runs/{id}/'
                    ),
                })

        if self.instance is not None and 'line' in attrs and attrs.get('line') is not None:
            new_line = attrs['line']
            if not line_shift_is_open(new_line):
                raise serializers.ValidationError(
                    {'line_id': 'На выбранной линии нет открытой смены.'},
                )
            if line_shift_is_paused(new_line):
                raise LineShiftPausedForRecipeRun()

        if raw_batches is not None:
            if recipe is None:
                raise serializers.ValidationError(
                    'Рецепт у запуска отсутствует (удалён из справочника). '
                    'Изменение состава партий без живой карточки рецепта недоступно.'
                )
            ser = RecipeRunBatchInputSerializer(
                data=raw_batches,
                many=True,
                context={'recipe_id': recipe.pk},
            )
            ser.is_valid(raise_exception=True)
            attrs['batches'] = ser.validated_data

        return attrs

    def create(self, validated_data):
        batches_data = validated_data.pop('batches')
        run = RecipeRun.objects.create(
            recipe=validated_data['recipe'],
            line=validated_data['line'],
        )
        self._create_batches(run, batches_data)
        return run

    def update(self, instance, validated_data):
        batches_data = validated_data.pop('batches', None)
        if 'recipe' in validated_data:
            instance.recipe = validated_data['recipe']
        if 'line' in validated_data:
            instance.line = validated_data['line']
        instance.save()
        if batches_data is not None:
            self._sync_batches(instance, batches_data)
        return instance

    def _create_batches(self, run, batches_data):
        for pos, b in enumerate(batches_data):
            idx = b['index'] if b.get('index') is not None else pos
            label = (b.get('label') or '').strip()
            if not label:
                label = f'Партия {idx + 1}'
            batch = RecipeRunBatch.objects.create(
                run=run,
                index=idx,
                label=label,
                quantity=b.get('quantity'),
            )
            self._create_components(batch, b['components'])

    def _sync_batches(self, run, batches_data):
        seen_ids = []
        for pos, b in enumerate(batches_data):
            idx = b['index'] if b.get('index') is not None else pos
            label = (b.get('label') or '').strip()
            if not label:
                label = f'Партия {idx + 1}'
            bid = b.get('id')
            if bid is not None:
                batch = RecipeRunBatch.objects.filter(pk=bid, run_id=run.pk).first()
                if not batch:
                    raise serializers.ValidationError({'batches': f'Неизвестная партия id={bid}'})
                batch.index = idx
                batch.label = label
                batch.quantity = b.get('quantity')
                batch.save(update_fields=['index', 'label', 'quantity'])
            else:
                batch = RecipeRunBatch.objects.create(
                    run=run,
                    index=idx,
                    label=label,
                    quantity=b.get('quantity'),
                )
            seen_ids.append(batch.pk)
            batch.components.all().delete()
            self._create_components(batch, b['components'])
        RecipeRunBatch.objects.filter(run=run).exclude(pk__in=seen_ids).delete()

    def _create_components(self, batch, components_data):
        recipe_id = batch.run.recipe_id
        for c in components_data:
            mid = c.get('material_id')
            ch = c.get('chemistry_id')
            rcid = c.get('recipe_component_id')
            if rcid is not None and not RecipeComponent.objects.filter(pk=rcid, recipe_id=recipe_id).exists():
                raise serializers.ValidationError('recipe_component_id не относится к рецепту запуска')
            RecipeRunBatchComponent.objects.create(
                batch=batch,
                recipe_component_id=rcid,
                raw_material_id=mid if mid is not None else None,
                chemistry_id=ch if ch is not None else None,
                quantity=c['quantity'],
                unit=(c.get('unit') or 'кг')[:50],
            )
