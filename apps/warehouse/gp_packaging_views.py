"""API: остаток неупакованного ГП, журнал упаковок (по физ. единице), создание операции (FIFO)."""
from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Prefetch
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from config.pagination import WarehouseResultsSetPagination
from config.permissions import IsAdminOrHasAccess

from apps.sales.models import SaleLine

from .gp_packaging_service import balance_group_detail, build_gp_unpacked_balance, create_gp_pack_operation
from .models import GpPackOperation, GpPackUnit, WarehouseBatch
from .serializers import GpPackageCreateSerializer, GpPackageJournalSerializer, GpPackOperationDetailSerializer


def _kg_json(d: Decimal) -> float:
    return float(d)


def _serialize_balance_line(ln):
    rid = int(ln.run_id)
    row = {
        'run_id': rid,
        'blank_production_run_id': rid,
        'product_name': ln.product_name,
        'blank_name': ln.blank_name,
        'gp_accepted_at': ln.gp_accepted_at,
        'gp_accepted_pieces': int(ln.gp_accepted_pieces),
        'gp_accepted_kg': _kg_json(ln.gp_accepted_kg),
        'unpacked_pieces': int(ln.unpacked_pieces),
        'unpacked_kg': _kg_json(ln.unpacked_kg),
    }
    if getattr(ln, 'sold_pieces', 0):
        row['sold_pieces'] = int(ln.sold_pieces)
        row['sold_kg'] = _kg_json(getattr(ln, 'sold_kg', Decimal('0')))
    return row


def _serialize_balance_group(g):
    return {
        'product_id': g.product_id,
        'blank_id': g.blank_id,
        'product_name': g.product_name,
        'blank_name': g.blank_name,
        'unpacked_pieces': int(g.total_unpacked_pieces),
        'unpacked_kg': _kg_json(g.total_unpacked_kg),
        'lines': [_serialize_balance_line(ln) for ln in g.lines],
    }


def _normalize_post_error(detail) -> dict:
    """Ответ 400 с полем code для фронта."""
    if isinstance(detail, dict):
        if 'code' in detail:
            return dict(detail)
        return {'code': 'validation_error', 'detail': detail}
    if isinstance(detail, list):
        return {'code': 'validation_error', 'detail': detail}
    return {'code': 'validation_error', 'detail': str(detail)}


class GpUnpackedBalanceView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'

    def get(self, request):
        pid = request.query_params.get('product_id')
        bid = request.query_params.get('blank_id')
        if (pid is None) ^ (bid is None):
            return Response(
                {'code': 'validation_error', 'detail': 'Укажите оба query-параметра product_id и blank_id или ни одного.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if pid is not None and bid is not None:
            try:
                pid_i, bid_i = int(pid), int(bid)
            except (TypeError, ValueError):
                return Response(
                    {'code': 'validation_error', 'detail': 'Некорректные product_id / blank_id.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            g = balance_group_detail(product_id=pid_i, blank_id=bid_i)
            if g.total_unpacked_pieces <= 0:
                return Response({'groups': []})
            return Response({'groups': [_serialize_balance_group(g)]})
        groups = build_gp_unpacked_balance()
        return Response({'groups': [_serialize_balance_group(g) for g in groups]})


class GpPackageViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """
    GET — журнал: одна строка = одна физическая упаковка (GpPackUnit).
    Ответ пагинации — как у склада ГП: items, meta, links (page, page_size до 500, ordering=-created_at).
    POST — операция упаковки (FIFO по BlankProductionRun).
    """

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'
    pagination_class = WarehouseResultsSetPagination
    filter_backends = [OrderingFilter]
    ordering_fields = ['created_at', 'id']
    ordering = ['-created_at', '-id']
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        status_filter = (self.request.query_params.get('status') or 'available').strip().lower()
        qs = (
            GpPackUnit.objects.select_related(
                'operation', 'operation__product', 'operation__blank', 'warehouse_batch',
            )
            .prefetch_related(
                'operation__run_allocations',
                Prefetch(
                    'sale_lines',
                    queryset=SaleLine.objects.select_related('sale').order_by('-sale__date', '-sale__id'),
                ),
            )
            .filter(warehouse_batch__quality=WarehouseBatch.QUALITY_GOOD)
            .annotate(created_at=models.F('operation__created_at'))
        )
        if status_filter == 'sold':
            qs = qs.filter(warehouse_batch__status=WarehouseBatch.STATUS_SHIPPED)
        elif status_filter == 'all':
            pass
        else:
            qs = qs.filter(warehouse_batch__status=WarehouseBatch.STATUS_AVAILABLE)
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return GpPackageCreateSerializer
        return GpPackageJournalSerializer

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        if not isinstance(response.data, dict):
            return response
        items = response.data.get('items') or []
        pieces_total = sum(int(x.get('total_pieces') or 0) for x in items)
        meta = dict(response.data.get('meta') or {})
        total = meta.get('total')
        if total is None:
            total = len(items)
        meta['summary'] = {
            'rows_count': len(items),
            'packages_count': int(total),
            'pieces_total': pieces_total,
        }
        response.data['meta'] = meta
        return response

    def create(self, request, *args, **kwargs):
        return Response(
            {'detail': 'Устарело: упаковка снята с фронта. Остатки — GET /api/warehouse/gp-stock/.'},
            status=status.HTTP_410_GONE,
        )
        ser = GpPackageCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({'code': 'validation_error', 'errors': ser.errors}, status=status.HTTP_400_BAD_REQUEST)
        v = ser.validated_data
        lines = [
            {'package_count': int(x['package_count']), 'pieces_per_package': int(x['pieces_per_package'])}
            for x in v['lines']
        ]
        try:
            op, created = create_gp_pack_operation(
                user=request.user,
                product_id=int(v['product_id']),
                blank_id=int(v['blank_id']),
                kind=str(v['kind']).strip(),
                label=str(v.get('label') or ''),
                split_mode=str(v['split_mode']).strip(),
                lines=lines,
                total_pieces=int(v['total_pieces']),
                client_request_id=str(v.get('client_request_id') or ''),
            )
        except DRFValidationError as exc:
            return Response(_normalize_post_error(exc.detail), status=status.HTTP_400_BAD_REQUEST)
        op = GpPackOperation.objects.prefetch_related('units', 'run_allocations').get(pk=op.pk)
        out = GpPackOperationDetailSerializer(op).data
        st = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(out, status=st)
