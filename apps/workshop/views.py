"""API цеха: заготовки (бочки + дробь), партии, ОТК, приёмка ГП."""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from django.db import models, transaction
from django.db.models import IntegerField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters import rest_framework as dj_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers, status, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.workshop.exceptions import WorkshopConflict

from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess

from apps.workshop.models import (
    BlankProductionRun,
    WorkshopBlank,
    WorkshopBlankCompositionLine,
)
from apps.workshop.serializers import (
    AcceptGpSerializer,
    BlankProductionRunCreateSerializer,
    BlankProductionRunSerializer,
    OtkDefectSerializer,
    WorkshopBlankCreateSerializer,
    WorkshopBlankPartialSerializer,
    WorkshopBlankReadSerializer,
    WorkshopPreparedAggregateSerializer,
)
from apps.workshop.barrel_materials import add_prepared_barrel_with_stock
from apps.workshop.blank_run_stock import create_run_deduct_workshop_only
from apps.workshop.services import (
    accept_goods_to_warehouse_gp,
    append_kg_to_workshop_prepared,
    append_machine_remainder_to_workshop,
)


class WorkshopPreparedPagination(PageNumberPagination):
    """Контракт фронта: count, next, previous, results."""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                'count': self.page.paginator.count,
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
                'results': data,
            }
        )


class PreparedBlankViewSet(
    viewsets.GenericViewSet,
    viewsets.mixins.ListModelMixin,
    viewsets.mixins.RetrieveModelMixin,
):
    """GET workshop/prepared-blanks/, GET …/{id}/, POST …/{id}/add-barrel/."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    serializer_class = WorkshopPreparedAggregateSerializer
    pagination_class = WorkshopPreparedPagination

    def get_queryset(self):
        dec_field = models.DecimalField(max_digits=14, decimal_places=6)
        zero = Value(Decimal('0'), output_field=dec_field)
        return (
            WorkshopBlank.objects.prefetch_related('prepared_state')
            .annotate(
                _agg_machine_return_kg=Coalesce(
                    Sum(
                        'production_runs__gp_machine_remainder_kg',
                        filter=Q(production_runs__gp_accepted_at__isnull=False),
                    ),
                    zero,
                ),
                _agg_defect_return_kg=Coalesce(
                    Sum(
                        'production_runs__defect_kg',
                        filter=Q(production_runs__otk_recorded_at__isnull=False),
                    ),
                    zero,
                ),
            )
            .order_by('name', 'pk')
        )

    @action(detail=True, methods=['post'], url_path='add-barrel')
    def add_barrel(self, request, pk=None):
        blank = self.get_object()
        blank = add_prepared_barrel_with_stock(blank_id=blank.pk, user=request.user, request=request)
        return Response(WorkshopPreparedAggregateSerializer(blank).data, status=status.HTTP_200_OK)


class WorkshopBlankViewSet(viewsets.ModelViewSet):
    """Каталог заготовок цеха: CRUD + состав (сырьё)."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    serializer_class = WorkshopBlankReadSerializer
    pagination_class = StandardResultsSetPagination
    queryset = WorkshopBlank.objects.prefetch_related(
        Prefetch(
            'composition_lines',
            queryset=WorkshopBlankCompositionLine.objects.select_related('raw_material'),
        )
    ).order_by('name', 'pk')
    filter_backends = [OrderingFilter]
    ordering_fields = ('id', 'name', 'recipe_kg_per_barrel')
    ordering = ('name', 'id')
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'create':
            return WorkshopBlankCreateSerializer
        if self.action in ('update', 'partial_update'):
            return WorkshopBlankPartialSerializer
        return WorkshopBlankReadSerializer

    def _serialize_detail(self, blank: WorkshopBlank):
        obj = self.get_queryset().get(pk=blank.pk)
        return WorkshopBlankReadSerializer(obj).data

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        blank = ser.save()
        return Response(self._serialize_detail(blank), status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        ser = self.get_serializer(instance, data=request.data, partial=partial)
        ser.is_valid(raise_exception=True)
        blank = ser.save()
        return Response(self._serialize_detail(blank))

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if BlankProductionRun.objects.filter(blank_id=instance.pk).exists():
            raise WorkshopConflict(
                detail='Нельзя удалить заготовку: есть партии производства, привязанные к этой заготовке.',
            )
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BlankProductionRunFilter(dj_filters.FilterSet):
    blank_id = dj_filters.NumberFilter(field_name='blank_id')

    class Meta:
        model = BlankProductionRun
        fields = ['blank_id', 'status']


class BlankProductionRunViewSet(viewsets.ModelViewSet):
    """CRUD-лист/деталь/создание + otk-defect + accept-gp."""

    http_method_names = ['get', 'post', 'head', 'options']
    serializer_class = BlankProductionRunSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = BlankProductionRunFilter
    ordering_fields = ['created_at', 'id', 'status', 'gp_accepted_at', 'otk_recorded_at']
    ordering = ['-created_at', '-pk']

    def get_queryset(self):
        return (
            BlankProductionRun.objects.select_related('blank', 'product')
            .annotate(
                packed_pieces=Coalesce(
                    Sum('gp_pack_allocations__pieces'),
                    Value(0),
                    output_field=IntegerField(),
                ),
            )
            .order_by('-created_at', '-pk')
        )

    def get_serializer_class(self):
        if self.action == 'create':
            return BlankProductionRunCreateSerializer
        return BlankProductionRunSerializer

    def create(self, request, *args, **kwargs):
        ser = BlankProductionRunCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        run = create_run_deduct_workshop_only(
            blank=v['blank'],
            product=v['product'],
            validated={
                'blank_total_kg': v['blank_total_kg'],
                'blank_used_in_production_kg': v['blank_used_in_production_kg'],
                'vat_max_kg_demo': v['vat_max_kg_demo'],
                'weight_kg_per_piece': v['weight_kg_per_piece'],
            },
        )
        out = BlankProductionRunSerializer(run)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='otk-defect')
    def otk_defect(self, request, pk=None):
        run = self.get_object()
        ser = OtkDefectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        defect_kg = Decimal(str(ser.validated_data['defect_kg']))
        used = Decimal(str(run.blank_used_in_production_kg))
        if defect_kg > used:
            raise serializers.ValidationError({'defect_kg': 'Брак не может превышать массу партии с цеха.'})
        if run.otk_recorded_at is not None:
            raise WorkshopConflict(detail='Результат ОТК по этой партии уже зафиксирован.')

        good_kg = used - defect_kg
        w = Decimal(str(run.weight_kg_per_piece))
        good_pieces = int((good_kg / w).to_integral_value(rounding=ROUND_DOWN))

        with transaction.atomic():
            run = BlankProductionRun.objects.select_for_update().get(pk=run.pk)
            if run.otk_recorded_at is not None:
                raise WorkshopConflict(detail='Результат ОТК по этой партии уже зафиксирован.')
            run.defect_kg = defect_kg
            run.good_kg = good_kg
            run.good_pieces = good_pieces
            run.otk_recorded_at = timezone.now()
            run.status = BlankProductionRun.STATUS_OTK_DONE
            run.save(
                update_fields=[
                    'defect_kg',
                    'good_kg',
                    'good_pieces',
                    'otk_recorded_at',
                    'status',
                ]
            )
            if defect_kg > 0:
                blank_w = WorkshopBlank.objects.select_for_update().get(pk=run.blank_id)
                append_kg_to_workshop_prepared(blank_w, defect_kg)
        return Response(BlankProductionRunSerializer(run).data)

    @action(detail=True, methods=['post'], url_path='accept-gp')
    def accept_gp(self, request, pk=None):
        run = self.get_object()
        ser = AcceptGpSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        if run.otk_recorded_at is None:
            raise serializers.ValidationError({'detail': 'Сначала зафиксируйте ОТК по партии.'})
        if run.gp_accepted_at is not None:
            raise WorkshopConflict(detail='Партия уже принята на склад ГП.')

        good_pieces = run.good_pieces
        if good_pieces is None:
            raise serializers.ValidationError({'detail': 'Отсутствует расчётное количество годных штук.'})

        good_kg = Decimal(str(run.good_kg))
        w = Decimal(str(run.weight_kg_per_piece))
        vat = Decimal(str(run.vat_max_kg_demo))

        max_by_vat = None
        if vat > 0 and w > 0:
            max_by_vat = int((vat / w).to_integral_value(rounding=ROUND_DOWN))

        candidates = [good_pieces]
        if max_by_vat is not None:
            candidates.append(max_by_vat)
        allow = min(candidates)

        req = ser.validated_data.get('accepted_pieces')
        if req is None:
            accepted = allow
        else:
            accepted = int(req)
            if accepted > allow:
                raise serializers.ValidationError(
                    {'accepted_pieces': f'Не более допустимых {allow} шт с учётом годного и лимита.'}
                )

        accepted_kg = (w * Decimal(accepted)).quantize(Decimal('0.000001'))
        machine_remainder_kg = (good_kg - accepted_kg).quantize(Decimal('0.000001'))
        if machine_remainder_kg < 0:
            machine_remainder_kg = Decimal('0')

        with transaction.atomic():
            run = BlankProductionRun.objects.select_for_update().get(pk=run.pk)
            if run.gp_accepted_at is not None:
                raise WorkshopConflict(detail='Партия уже принята на склад ГП.')

            accept_goods_to_warehouse_gp(run, accepted)
            if machine_remainder_kg > Decimal('0'):
                append_machine_remainder_to_workshop(
                    WorkshopBlank.objects.get(pk=run.blank_id), machine_remainder_kg
                )

            now = timezone.now()
            run.gp_accepted_at = now
            run.gp_accepted_pieces = accepted
            run.gp_accepted_kg = accepted_kg
            run.gp_machine_remainder_kg = machine_remainder_kg
            run.status = BlankProductionRun.STATUS_GP_ACCEPTED
            run.save(
                update_fields=[
                    'gp_accepted_at',
                    'gp_accepted_pieces',
                    'gp_accepted_kg',
                    'gp_machine_remainder_kg',
                    'status',
                ]
            )

        fresh = BlankProductionRun.objects.select_related('blank', 'product').get(pk=run.pk)
        return Response(BlankProductionRunSerializer(fresh).data)
