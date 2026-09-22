"""API цеха: заготовки (бочки + дробь), партии, ОТК, приёмка ГП."""
from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import IntegerField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
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
    BlankProductionRunCreateSerializer,
    BlankProductionRunSerializer,
    WorkshopBlankCreateSerializer,
    WorkshopBlankPartialSerializer,
    WorkshopBlankReadSerializer,
    WorkshopPreparedAggregateSerializer,
)
from apps.workshop.barrel_materials import add_prepared_barrel_with_stock
from apps.workshop.blank_run_stock import create_run_deduct_workshop_only


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
            BlankProductionRun.objects.select_related('blank', 'product', 'order_line', 'client_request')
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
            product=v.get('product'),
            validated={
                'blank_total_kg': v['blank_total_kg'],
                'blank_used_in_production_kg': v['blank_used_in_production_kg'],
                'vat_max_kg_demo': v['vat_max_kg_demo'],
                'weight_kg_per_piece': v.get('weight_kg_per_piece'),
            },
        )
        from apps.realtime.broadcast import schedule_otk_push, schedule_push

        schedule_otk_push(action='created', entity_id=run.blank_id, extra={'blank_id': run.blank_id})
        schedule_push(resource='workshop', action='updated', entity_id=run.blank_id, extra={'blank_id': run.blank_id})
        out = BlankProductionRunSerializer(run)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='otk-defect')
    def otk_defect(self, request, pk=None):
        return Response(
            {'detail': 'Устарело: учёт ОТК через POST /api/workshop/otk-blanks/{blank_id}/account/.'},
            status=status.HTTP_410_GONE,
        )

    @action(detail=True, methods=['post'], url_path='accept-gp')
    def accept_gp(self, request, pk=None):
        return Response(
            {'detail': 'Устарело: приёмка ГП выполняется в POST /api/workshop/otk-blanks/{blank_id}/account/.'},
            status=status.HTTP_410_GONE,
        )
