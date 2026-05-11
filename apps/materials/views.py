from decimal import Decimal

from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess
from apps.materials.fifo import material_stock_kg

from .filters import MaterialBatchFilter
from .models import MaterialBatch, MaterialStockDeduction, RawMaterial
from .serializers import (
    RawMaterialSerializer,
    MaterialBatchSerializer,
    normalize_material_unit,
    kg_to_display_unit,
)
from .usage_checks import raw_material_is_deletable, raw_material_unit_change_denial


REASON_TO_MOVEMENT = {
    'chemistry_batch_produce': 'writeoff_chemistry',
    'production_batch': 'writeoff_production',
}


def _build_movement_items():
    rows = []
    for b in MaterialBatch.objects.select_related('material').iterator():
        mat = b.material
        u = normalize_material_unit(mat.unit)
        qi = kg_to_display_unit(Decimal(str(b.quantity_initial)), mat.unit)
        rows.append({
            'id': f'incoming-{b.pk}',
            'occurred_at': b.received_at,
            'material_id': mat.pk,
            'material_name': mat.name,
            'movement_type': 'incoming',
            'quantity': float(qi),
            'unit': u,
            'comment': b.comment or '',
            'batch_id': b.pk,
        })
    for d in MaterialStockDeduction.objects.select_related('batch__material').iterator():
        b = d.batch
        mat = b.material
        u = normalize_material_unit(mat.unit)
        q = -kg_to_display_unit(Decimal(str(d.quantity)), mat.unit)
        mt = REASON_TO_MOVEMENT.get((d.reason or '').strip(), 'writeoff_other')
        rows.append({
            'id': f'writeoff-{d.pk}',
            'occurred_at': d.created_at,
            'material_id': mat.pk,
            'material_name': mat.name,
            'movement_type': mt,
            'quantity': float(q),
            'unit': u,
            'comment': '',
            'batch_id': b.pk,
        })
    rows.sort(key=lambda x: x['occurred_at'], reverse=True)
    return rows


@extend_schema_view(
    list=extend_schema(
        tags=['materials'],
        summary='Остатки сырья по справочнику',
        responses={
            200: OpenApiTypes.OBJECT,
        },
        description='items: material_id, name, balance, unit, min_balance, deletable, флаги блокировки единицы.',
    ),
)
class MaterialsBalancesView(viewsets.GenericViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    pagination_class = StandardResultsSetPagination
    queryset = RawMaterial.objects.all()

    def list(self, request):
        materials = RawMaterial.objects.all().order_by('name', 'id')
        inc_counts = {
            r['material_id']: r['c']
            for r in MaterialBatch.objects.values('material_id').annotate(c=Count('id'))
        }
        mov_counts = {
            r['batch__material_id']: r['c']
            for r in MaterialStockDeduction.objects.filter(batch__material_id__isnull=False)
            .values('batch__material_id')
            .annotate(c=Count('id'))
        }
        result = []
        for m in materials:
            inc = inc_counts.get(m.pk, 0)
            mov = mov_counts.get(m.pk, 0)
            denied, _ = raw_material_unit_change_denial(m)
            bal_kg = material_stock_kg(m.pk)
            u = normalize_material_unit(m.unit)
            if u == 'g':
                bal_disp = float((bal_kg * Decimal('1000')).quantize(Decimal('0.0001')))
            else:
                bal_disp = float(bal_kg)
            min_b = m.min_balance
            min_disp = float(min_b) if min_b is not None else None
            result.append({
                'material_id': m.id,
                'id': m.id,
                'material_name': m.name,
                'name': m.name,
                'unit': u,
                'balance': bal_disp,
                'min_balance': min_disp,
                'is_active': m.is_active,
                'comment': m.comment or '',
                'deletable': raw_material_is_deletable(m),
                'unit_locked': denied,
                'unit_change_allowed': not denied,
                'has_receipts': inc > 0,
                'has_movements': mov > 0,
                'incoming_count': inc,
                'movement_count': mov,
            })
        page = self.paginate_queryset(result)
        if page is not None:
            return self.get_paginated_response(page)
        return Response({'items': result})


@extend_schema_view(
    list=extend_schema(tags=['materials'], summary='Журнал движений сырья'),
)
class MaterialsMovementsView(viewsets.GenericViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    pagination_class = StandardResultsSetPagination
    queryset = MaterialBatch.objects.none()

    def list(self, request):
        items = _build_movement_items()
        page = self.paginate_queryset(items)
        if page is not None:
            return self.get_paginated_response(page)
        return Response({'items': items})


class RawMaterialViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = RawMaterial.objects.all()
    serializer_class = RawMaterialSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    activity_section = 'Материалы'
    activity_label = 'материал'
    filterset_fields = ['unit', 'is_active']
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if 'unit' in request.data:
            new_u = request.data.get('unit')
            if normalize_material_unit(new_u) != normalize_material_unit(instance.unit):
                denied, msg = raw_material_unit_change_denial(instance)
                if denied:
                    return Response(
                        {'detail': msg, 'error': msg},
                        status=status.HTTP_409_CONFLICT,
                    )
        return super().partial_update(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not raw_material_is_deletable(instance):
            msg = (
                'Нельзя удалить сырьё: есть приходы, движения или использование '
                'в рецептах, химии или производстве.'
            )
            return Response(
                {
                    'code': 'MATERIAL_IN_USE',
                    'error': msg,
                    'detail': msg,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class IncomingViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """Партии прихода (Пополнить). URL: /api/incoming/ — только список и создание."""

    queryset = MaterialBatch.objects.select_related('material').all()
    serializer_class = MaterialBatchSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    activity_section = 'Материалы'
    activity_label = 'приход материала'
    filterset_class = MaterialBatchFilter
    search_fields = ['supplier_name', 'comment', 'supplier_batch_number', 'material__name']
    ordering_fields = ['id', 'created_at', 'received_at']
    http_method_names = ['get', 'post', 'head', 'options']
