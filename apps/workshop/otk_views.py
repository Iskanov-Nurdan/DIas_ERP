"""API ОТК: пулы, приходы, учёт."""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from django.db.models import Max, Prefetch, Q
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_numbers import api_decimal_str
from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess

from apps.workshop.models import (
    OtkAccountBlankAllocation,
    OtkAccountLine,
    OtkAccountSession,
    OtkBlankIntake,
    OtkBlankPool,
    WorkshopBlank,
)
from apps.workshop.otk_pool import POOL_EPSILON, account_otk_blank, account_otk_v2


def _load_account_session(session_id: int) -> OtkAccountSession:
    return (
        OtkAccountSession.objects.prefetch_related(
            'lines',
            'packers',
            Prefetch(
                'blank_allocations',
                queryset=OtkAccountBlankAllocation.objects.select_related('blank'),
            ),
        )
        .select_related('blank', 'defect_blank', 'operator', 'chemist', 'packer', 'shift')
        .get(pk=session_id)
    )


def _schedule_otk_account_ws(session: OtkAccountSession) -> None:
    from apps.realtime.broadcast import schedule_otk_push, schedule_push

    blank_ids = list(
        session.blank_allocations.values_list('blank_id', flat=True).distinct()
    )
    if not blank_ids:
        blank_ids = [session.blank_id]
    for bid in blank_ids:
        schedule_otk_push(action='updated', entity_id=bid, extra={'blank_id': bid})
        schedule_push(resource='workshop', action='updated', entity_id=bid, extra={'blank_id': bid})
    schedule_push(resource='warehouse', action='updated', entity_id=None)
    if session.shift_id:
        schedule_push(resource='shift', action='updated', entity_id=session.shift_id)


def _parse_date(raw) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        d = datetime.strptime(str(raw).strip()[:10], '%Y-%m-%d').date()
        return timezone.make_aware(datetime.combine(d, time.min))
    except (TypeError, ValueError):
        return None


class OtkBlankPoolSerializer(serializers.Serializer):
    blank_id = serializers.IntegerField()
    blank_name = serializers.CharField()
    remaining_kg = serializers.CharField()
    total_intake_kg = serializers.CharField()
    can_account = serializers.BooleanField()
    last_intake_at = serializers.DateTimeField(allow_null=True)


class OtkBlankIntakeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    blank_id = serializers.IntegerField()
    blank_name = serializers.CharField()
    kg = serializers.CharField()
    run_id = serializers.IntegerField(allow_null=True)
    created_at = serializers.DateTimeField()
    source = serializers.CharField()


class OtkAccountLineOutSerializer(serializers.ModelSerializer):
    profile_id = serializers.IntegerField(read_only=True)
    profile_name = serializers.CharField(source='profile_name_snapshot', read_only=True)
    kg = serializers.SerializerMethodField()

    class Meta:
        model = OtkAccountLine
        fields = ('profile_id', 'profile_name', 'pieces', 'kg')

    def get_kg(self, obj):
        return api_decimal_str(obj.kg)


class OtkAccountSessionSerializer(serializers.ModelSerializer):
    lines = OtkAccountLineOutSerializer(many=True, read_only=True)
    blank_id = serializers.IntegerField(read_only=True)
    blank_name = serializers.SerializerMethodField()
    consumed_kg = serializers.SerializerMethodField()
    defect_kg = serializers.SerializerMethodField()
    defect_blank_id = serializers.IntegerField(read_only=True, allow_null=True)
    remaining_kg_after = serializers.SerializerMethodField()
    warehouse_posted = serializers.SerializerMethodField()
    operator_id = serializers.IntegerField(allow_null=True, read_only=True)
    chemist_id = serializers.IntegerField(allow_null=True, read_only=True)
    packer_id = serializers.IntegerField(allow_null=True, read_only=True)
    packer_ids = serializers.SerializerMethodField()
    operator_name = serializers.SerializerMethodField()
    chemist_name = serializers.SerializerMethodField()
    packer_name = serializers.SerializerMethodField()
    packer_names = serializers.SerializerMethodField()
    blank_breakdown = serializers.SerializerMethodField()

    class Meta:
        model = OtkAccountSession
        fields = (
            'id',
            'blank_id',
            'blank_name',
            'consumed_kg',
            'defect_kg',
            'defect_blank_id',
            'remaining_kg_after',
            'warehouse_posted',
            'shift_period',
            'lines',
            'operator_id',
            'chemist_id',
            'packer_id',
            'packer_ids',
            'operator_name',
            'chemist_name',
            'packer_name',
            'packer_names',
            'blank_breakdown',
            'comment',
            'created_at',
        )

    def get_blank_name(self, obj):
        return obj.blank.name if obj.blank_id else ''

    def get_consumed_kg(self, obj):
        return api_decimal_str(obj.consumed_kg)

    def get_defect_kg(self, obj):
        return api_decimal_str(obj.defect_kg)

    def get_remaining_kg_after(self, obj):
        return api_decimal_str(obj.remaining_kg_after)

    def get_warehouse_posted(self, obj):
        return True

    def get_packer_ids(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'packers' in obj._prefetched_objects_cache:
            return [u.pk for u in obj.packers.all()]
        return list(obj.packers.values_list('pk', flat=True))

    def get_packer_names(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'packers' in obj._prefetched_objects_cache:
            users = obj.packers.all()
        else:
            users = obj.packers.all()
        return [self._user_name(u) for u in users if self._user_name(u)]

    def get_blank_breakdown(self, obj):
        rows = getattr(obj, '_prefetched_objects_cache', {}).get('blank_allocations')
        if rows is None:
            qs = obj.blank_allocations.select_related('blank').all()
        else:
            qs = rows
        return [
            {
                'blank_id': row.blank_id,
                'blank_name': row.blank.name,
                'consumed_kg': api_decimal_str(row.consumed_kg),
                'remaining_kg_after': api_decimal_str(row.remaining_kg_after),
            }
            for row in qs
        ]

    def _user_name(self, user):
        if user is None:
            return ''
        return (getattr(user, 'name', None) or getattr(user, 'email', None) or '').strip()

    def get_operator_name(self, obj):
        return self._user_name(obj.operator)

    def get_chemist_name(self, obj):
        return self._user_name(obj.chemist)

    def get_packer_name(self, obj):
        return self._user_name(obj.packer)


class OtkAccountLineInSerializer(serializers.Serializer):
    profile_id = serializers.IntegerField(min_value=1)
    pieces = serializers.IntegerField(min_value=1)


class OtkDefectInSerializer(serializers.Serializer):
    unit = serializers.ChoiceField(choices=['kg', 'pieces'], required=False, default='kg')
    value = serializers.DecimalField(max_digits=14, decimal_places=6, required=False, default=Decimal('0'))
    profile_id = serializers.IntegerField(required=False, allow_null=True)


class OtkAccountInSerializer(serializers.Serializer):
    lines = OtkAccountLineInSerializer(many=True)
    defect = OtkDefectInSerializer(required=False)
    shift_period = serializers.ChoiceField(
        choices=['day', 'night'],
        required=False,
        default='day',
    )
    operator_id = serializers.IntegerField(required=False, allow_null=True)
    chemist_id = serializers.IntegerField(required=False, allow_null=True)
    packer_id = serializers.IntegerField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError('Укажите хотя бы одну строку профиля.')
        return value

    def validate(self, attrs):
        if attrs.get('defect') is None:
            attrs['defect'] = {'unit': 'kg', 'value': Decimal('0')}
        return attrs


class OtkAccountV2InSerializer(serializers.Serializer):
    lines = OtkAccountLineInSerializer(many=True)
    defect_kg = serializers.DecimalField(
        max_digits=14, decimal_places=6, required=False, allow_null=True,
    )
    defect_blank_id = serializers.IntegerField(required=False, allow_null=True)
    shift_period = serializers.ChoiceField(choices=['day', 'night'])
    operator_id = serializers.IntegerField(required=False, allow_null=True)
    chemist_id = serializers.IntegerField(required=False, allow_null=True)
    packer_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError('Укажите хотя бы одну строку профиля.')
        return value

    def validate_packer_ids(self, value):
        if not value:
            return []
        return list(dict.fromkeys(value))


class OtkBlanksListView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def get(self, request):
        qs = (
            OtkBlankPool.objects.select_related('blank')
            .annotate(last_intake_at=Max('blank__otk_intakes__created_at'))
            .order_by('-remaining_kg', 'blank__name')
        )
        only_positive = request.query_params.get('only_positive', '1') != '0'
        if only_positive:
            qs = qs.filter(remaining_kg__gte=POOL_EPSILON)

        items = []
        for pool in qs:
            rem = Decimal(str(pool.remaining_kg))
            items.append(
                {
                    'blank_id': pool.blank_id,
                    'blank_name': pool.blank.name,
                    'remaining_kg': api_decimal_str(rem),
                    'total_intake_kg': api_decimal_str(pool.total_intake_kg),
                    'can_account': rem >= POOL_EPSILON,
                    'last_intake_at': pool.last_intake_at,
                }
            )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(items, request, view=self)
        if page is not None:
            return paginator.get_paginated_response(page)
        return Response({'items': items, 'results': items, 'count': len(items)})


class OtkBlanksIntakesView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def get(self, request):
        qs = OtkBlankIntake.objects.select_related('blank').order_by('-created_at', '-pk')
        blank_id = request.query_params.get('blank_id')
        if blank_id:
            qs = qs.filter(blank_id=blank_id)
        date_from = _parse_date(request.query_params.get('date_from'))
        date_to = _parse_date(request.query_params.get('date_to'))
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            end = timezone.make_aware(datetime.combine(date_to.date(), time.max))
            qs = qs.filter(created_at__lte=end)

        ordering = request.query_params.get('ordering', '-created_at')
        if ordering.lstrip('-') == 'created_at':
            qs = qs.order_by(ordering, '-pk')

        items = [
            {
                'id': row.pk,
                'blank_id': row.blank_id,
                'blank_name': row.blank.name,
                'kg': api_decimal_str(row.kg),
                'run_id': row.run_id,
                'created_at': row.created_at,
                'source': 'produce',
            }
            for row in qs[:500]
        ]
        return Response({'items': items, 'results': items})


class OtkBlankAccountView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def post(self, request, blank_id: int):
        ser = OtkAccountInSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        session = account_otk_blank(
            blank_id=int(blank_id),
            lines=v['lines'],
            defect=v['defect'],
            operator_id=v.get('operator_id'),
            chemist_id=v.get('chemist_id'),
            packer_id=v.get('packer_id'),
            comment=v.get('comment') or '',
            shift_period=v.get('shift_period') or 'day',
        )
        _schedule_otk_account_ws(session)
        out = OtkAccountSessionSerializer(_load_account_session(session.pk)).data
        return Response(out, status=status.HTTP_201_CREATED)


class OtkAccountView(APIView):
    """POST /api/workshop/otk-account/ — учёт v2 (multi-blank)."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def post(self, request):
        ser = OtkAccountV2InSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        session = account_otk_v2(
            lines=v['lines'],
            defect_kg=v.get('defect_kg'),
            defect_blank_id=v.get('defect_blank_id'),
            shift_period=v['shift_period'],
            operator_id=v.get('operator_id'),
            chemist_id=v.get('chemist_id'),
            packer_ids=v.get('packer_ids') or [],
            comment=v.get('comment') or '',
        )
        _schedule_otk_account_ws(session)
        out = OtkAccountSessionSerializer(_load_account_session(session.pk)).data
        return Response(out, status=status.HTTP_201_CREATED)


class OtkAccountingViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    serializer_class = OtkAccountSessionSerializer
    pagination_class = StandardResultsSetPagination
    queryset = (
        OtkAccountSession.objects.select_related(
            'blank', 'defect_blank', 'operator', 'chemist', 'packer', 'shift',
        )
        .prefetch_related(
            'packers',
            Prefetch('lines', queryset=OtkAccountLine.objects.select_related('profile')),
            Prefetch(
                'blank_allocations',
                queryset=OtkAccountBlankAllocation.objects.select_related('blank'),
            ),
        )
        .order_by('-created_at', '-pk')
    )

    def get_queryset(self):
        qs = super().get_queryset()
        blank_id = self.request.query_params.get('blank_id')
        if blank_id:
            qs = qs.filter(blank_id=blank_id)
        return qs
