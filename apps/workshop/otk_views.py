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

from apps.workshop.models import OtkAccountLine, OtkAccountSession, OtkBlankIntake, OtkBlankPool, WorkshopBlank
from apps.workshop.otk_pool import POOL_EPSILON, account_otk_blank


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
    consumed_kg = serializers.SerializerMethodField()
    defect_kg = serializers.SerializerMethodField()
    remaining_kg_after = serializers.SerializerMethodField()
    warehouse_posted = serializers.SerializerMethodField()
    operator_id = serializers.IntegerField(allow_null=True, read_only=True)
    chemist_id = serializers.IntegerField(allow_null=True, read_only=True)
    packer_id = serializers.IntegerField(allow_null=True, read_only=True)
    operator_name = serializers.SerializerMethodField()
    chemist_name = serializers.SerializerMethodField()
    packer_name = serializers.SerializerMethodField()

    class Meta:
        model = OtkAccountSession
        fields = (
            'id',
            'blank_id',
            'consumed_kg',
            'defect_kg',
            'remaining_kg_after',
            'warehouse_posted',
            'lines',
            'operator_id',
            'chemist_id',
            'packer_id',
            'operator_name',
            'chemist_name',
            'packer_name',
            'comment',
            'created_at',
        )

    def get_consumed_kg(self, obj):
        return api_decimal_str(obj.consumed_kg)

    def get_defect_kg(self, obj):
        return api_decimal_str(obj.defect_kg)

    def get_remaining_kg_after(self, obj):
        return api_decimal_str(obj.remaining_kg_after)

    def get_warehouse_posted(self, obj):
        return True

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
    unit = serializers.ChoiceField(choices=['kg', 'pieces'])
    value = serializers.DecimalField(max_digits=14, decimal_places=6)
    profile_id = serializers.IntegerField(required=False, allow_null=True)


class OtkAccountInSerializer(serializers.Serializer):
    lines = OtkAccountLineInSerializer(many=True)
    defect = OtkDefectInSerializer()
    operator_id = serializers.IntegerField(required=False, allow_null=True)
    chemist_id = serializers.IntegerField(required=False, allow_null=True)
    packer_id = serializers.IntegerField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError('Укажите хотя бы одну строку профиля.')
        return value


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
        )
        from apps.realtime.broadcast import schedule_otk_push, schedule_push

        schedule_otk_push(action='updated', entity_id=blank_id, extra={'blank_id': blank_id})
        schedule_push(resource='warehouse', action='updated', entity_id=None)
        schedule_push(resource='workshop', action='updated', entity_id=blank_id, extra={'blank_id': blank_id})

        session = (
            OtkAccountSession.objects.prefetch_related('lines')
            .select_related('operator', 'chemist', 'packer')
            .get(pk=session.pk)
        )
        out = OtkAccountSessionSerializer(session).data
        return Response(out, status=status.HTTP_201_CREATED)


class OtkAccountingViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    serializer_class = OtkAccountSessionSerializer
    pagination_class = StandardResultsSetPagination
    queryset = (
        OtkAccountSession.objects.select_related('blank', 'operator', 'chemist', 'packer')
        .prefetch_related(Prefetch('lines', queryset=OtkAccountLine.objects.select_related('profile')))
        .order_by('-created_at', '-pk')
    )

    def get_queryset(self):
        qs = super().get_queryset()
        blank_id = self.request.query_params.get('blank_id')
        if blank_id:
            qs = qs.filter(blank_id=blank_id)
        return qs
