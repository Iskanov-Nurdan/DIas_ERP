from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from config.api_numbers import api_decimal_str

from .models import GpPackOperation, GpPackRunAllocation, GpPackUnit, WarehouseBatch
from .packaging import warehouse_packaging_breakdown


def _packaging_int_field(value):
    """Целое для API (упаковки в БД — Decimal, допускают .0000)."""
    if value is None:
        return None
    q = Decimal(str(value))
    return int(q.to_integral_value())


class WarehouseBatchSerializer(serializers.ModelSerializer):
    """Склад ГП: один канон `inventory_form`, без дублей габаритов и алиасов упаковки."""
    stock_bucket = serializers.CharField(read_only=True)
    product_name = serializers.SerializerMethodField()
    result_rework_request = serializers.SerializerMethodField()
    linked_entities = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    angle_deg = serializers.SerializerMethodField()
    sealed_packages_count = serializers.SerializerMethodField()
    open_package_pieces = serializers.SerializerMethodField()
    sealed_pieces = serializers.SerializerMethodField()
    packaging_quantity_consistent = serializers.SerializerMethodField()
    reserved_quantity = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseBatch
        fields = (
            'id',
            'product',
            'product_name',
            'quantity',
            'reserved_quantity',
            'available_quantity',
            'stock_bucket',
            'result_rework_request',
            'linked_entities',
            'status',
            'date',
            'source_batch',
            'inventory_form',
            'line_name',
            'height',
            'width',
            'angle_deg',
            'unit_meters',
            'package_total_meters',
            'pieces_per_package',
            'packages_count',
            'sealed_packages_count',
            'open_package_pieces',
            'sealed_pieces',
            'packaging_quantity_consistent',
            'otk_accepted',
            'otk_defect',
            'otk_defect_reason',
            'otk_comment',
            'otk_inspector_name',
            'otk_checked_at',
            'otk_status',
            'quality',
            'defect_reason',
            'length_per_piece',
            'cost_per_piece',
            'cost_per_meter',
            'total_meters',
        )

    def get_product_name(self, obj):
        return obj.product

    def get_result_rework_request(self, obj):
        if obj.stock_bucket != WarehouseBatch.STOCK_BUCKET_REWORKED:
            return None
        rw = obj.rework_requests.order_by('-created_at', '-id').first()
        return rw.pk if rw else None

    def get_linked_entities(self, obj):
        return {
            'profile': (
                {
                    'id': obj.profile_id,
                    'label': obj.profile.name,
                }
                if obj.profile_id
                else None
            ),
            'source_batch': (
                {
                    'id': obj.source_batch_id,
                    'label': f'#{obj.source_batch_id} {obj.source_batch.product}',
                }
                if obj.source_batch_id
                else None
            ),
        }

    def get_line_name(self, obj):
        pb = obj.source_batch
        if pb is None or not pb.order_id:
            return None
        try:
            order = pb.order
        except ObjectDoesNotExist:
            return None
        if getattr(order, 'line_id', None):
            try:
                n = (order.line.name or '').strip()
                if n:
                    return n
            except ObjectDoesNotExist:
                pass
        snap = (getattr(order, 'line_name_snapshot', None) or '').strip()
        return snap or None

    def _unit_meters_dec(self, obj):
        if obj.unit_meters is not None:
            return Decimal(str(obj.unit_meters))
        pb = obj.source_batch
        if pb is not None and pb.shift_height is not None:
            return Decimal(str(pb.shift_height))
        return None

    def get_height(self, obj):
        return api_decimal_str(self._unit_meters_dec(obj))

    def get_width(self, obj):
        pb = obj.source_batch
        if pb is None or pb.shift_width is None:
            return None
        return api_decimal_str(pb.shift_width)

    def get_angle_deg(self, obj):
        pb = obj.source_batch
        if pb is None or pb.shift_angle_deg is None:
            return None
        return api_decimal_str(pb.shift_angle_deg)

    def _packaging_breakdown(self, obj: WarehouseBatch) -> dict:
        cache = getattr(self, '_wh_breakdown_cache', None)
        if cache is None:
            cache = {}
            setattr(self, '_wh_breakdown_cache', cache)
        key = (
            obj.pk,
            obj.inventory_form,
            str(obj.quantity),
            str(obj.packages_count),
            str(obj.pieces_per_package),
        )
        if key not in cache:
            cache[key] = warehouse_packaging_breakdown(obj)
        return cache[key]

    def get_sealed_packages_count(self, obj):
        inv = obj.inventory_form
        if inv not in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        ):
            return None
        return self._packaging_breakdown(obj)['sealed_packages_count']

    def get_open_package_pieces(self, obj):
        inv = obj.inventory_form
        if inv not in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        ):
            return None
        return self._packaging_breakdown(obj)['open_package_pieces']

    def get_sealed_pieces(self, obj):
        inv = obj.inventory_form
        if inv not in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        ):
            return None
        return self._packaging_breakdown(obj)['sealed_pieces']

    def get_packaging_quantity_consistent(self, obj):
        inv = obj.inventory_form
        if inv not in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        ):
            return None
        return self._packaging_breakdown(obj)['packaging_quantity_consistent']

    def get_reserved_quantity(self, obj):
        """Количество, занятое активными резервами."""
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        from django.db.models import Value
        from decimal import Decimal as D
        try:
            from apps.sales.models import OrderReservation
        except ImportError:
            return None
        val = (
            OrderReservation.objects
            .filter(warehouse_batch_id=obj.pk, status=OrderReservation.STATUS_ACTIVE)
            .aggregate(t=Coalesce(Sum('quantity'), Value(D('0'))))['t']
        )
        return api_decimal_str(val or D('0'))

    def get_available_quantity(self, obj):
        """Свободный остаток = physical_quantity − reserved_quantity."""
        from decimal import Decimal as D
        phys = D(str(obj.quantity or 0))
        try:
            from apps.sales.models import OrderReservation
            from django.db.models import Sum
            from django.db.models.functions import Coalesce
            from django.db.models import Value
            reserved = (
                OrderReservation.objects
                .filter(warehouse_batch_id=obj.pk, status=OrderReservation.STATUS_ACTIVE)
                .aggregate(t=Coalesce(Sum('quantity'), Value(D('0'))))['t']
            ) or D('0')
        except ImportError:
            reserved = D('0')
        return api_decimal_str(max(D('0'), phys - reserved))

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for k in ('quantity', 'length_per_piece', 'total_meters', 'cost_per_piece', 'cost_per_meter'):
            v = ret.get(k)
            if v is not None:
                ret[k] = api_decimal_str(v)
        for k in ('unit_meters', 'package_total_meters', 'pieces_per_package', 'packages_count'):
            v = ret.get(k)
            if v is None:
                ret.pop(k, None)
            elif k in ('pieces_per_package', 'packages_count') and ret.get('inventory_form') in (
                WarehouseBatch.INVENTORY_PACKED,
                WarehouseBatch.INVENTORY_OPEN_PACKAGE,
            ):
                ret[k] = _packaging_int_field(v) if v is not None else None
            else:
                ret[k] = api_decimal_str(v)

        inv = instance.inventory_form
        if inv not in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        ):
            for k in (
                'pieces_per_package',
                'packages_count',
                'sealed_packages_count',
                'open_package_pieces',
                'sealed_pieces',
                'packaging_quantity_consistent',
            ):
                ret.pop(k, None)

        otk_keys = (
            'otk_accepted',
            'otk_defect',
            'otk_defect_reason',
            'otk_comment',
            'otk_inspector_name',
            'otk_checked_at',
            'otk_status',
        )
        for k in otk_keys:
            v = ret.get(k)
            if v is None or v == '':
                ret.pop(k, None)
            elif k in ('otk_accepted', 'otk_defect'):
                ret[k] = api_decimal_str(v)
        return ret


class GpPackageLineInputSerializer(serializers.Serializer):
    package_count = serializers.IntegerField(min_value=1)
    pieces_per_package = serializers.IntegerField(min_value=1)


class GpPackageCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    blank_id = serializers.IntegerField(min_value=1)
    kind = serializers.CharField(max_length=16)
    label = serializers.CharField(max_length=255, allow_blank=True, default='')
    split_mode = serializers.CharField(max_length=16)
    lines = GpPackageLineInputSerializer(many=True)
    total_pieces = serializers.IntegerField(min_value=1)
    client_request_id = serializers.CharField(max_length=64, allow_blank=True, required=False, default='')


class GpPackUnitOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = GpPackUnit
        fields = ('id', 'sequence', 'pieces')


class GpPackageJournalSerializer(serializers.ModelSerializer):
    """
    Одна строка журнала = одна физическая упаковка (GpPackUnit).
    total_kg — доля массы операции (сумма kg по FIFO-аллокациям), пропорционально total_pieces этой строки.
    """

    created_at = serializers.DateTimeField(source='operation.created_at', read_only=True)
    product_id = serializers.IntegerField(source='operation.product_id', read_only=True)
    blank_id = serializers.IntegerField(source='operation.blank_id', read_only=True)
    product_name = serializers.SerializerMethodField()
    blank_name = serializers.SerializerMethodField()
    kind = serializers.CharField(source='operation.kind', read_only=True)
    label = serializers.CharField(source='operation.label', read_only=True)
    total_pieces = serializers.IntegerField(source='pieces', read_only=True)
    total_kg = serializers.SerializerMethodField()
    total_weight_kg = serializers.SerializerMethodField()
    warehouse_batch_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = GpPackUnit
        fields = (
            'id',
            'created_at',
            'product_id',
            'blank_id',
            'product_name',
            'blank_name',
            'kind',
            'label',
            'total_pieces',
            'total_kg',
            'total_weight_kg',
            'warehouse_batch_id',
        )

    def get_product_name(self, obj):
        op = obj.operation
        if op.product_id and op.product:
            return op.product.name or ''
        return ''

    def get_blank_name(self, obj):
        op = obj.operation
        if op.blank_id and op.blank:
            return op.blank.name or ''
        return ''

    def get_total_kg(self, obj):
        from decimal import Decimal

        op = obj.operation
        cached = getattr(op, '_prefetched_objects_cache', {}).get('run_allocations')
        allocs = list(cached) if cached is not None else list(op.run_allocations.all())
        total_k = sum(Decimal(str(a.kg or 0)) for a in allocs)
        tp = int(op.total_pieces or 0)
        if tp <= 0 or not allocs:
            return 0.0
        share = Decimal(int(obj.pieces)) / Decimal(tp)
        kg = (total_k * share).quantize(Decimal('0.000001'))
        return float(kg)

    def get_total_weight_kg(self, obj):
        return self.get_total_kg(obj)


class GpPackRunAllocationOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = GpPackRunAllocation
        fields = ('id', 'blank_production_run_id', 'pieces', 'kg')


class GpPackOperationDetailSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)
    blank_id = serializers.IntegerField(read_only=True)
    created_by_id = serializers.IntegerField(read_only=True, allow_null=True)
    units = GpPackUnitOutSerializer(many=True, read_only=True)
    run_allocations = GpPackRunAllocationOutSerializer(many=True, read_only=True)

    class Meta:
        model = GpPackOperation
        fields = (
            'id',
            'product_id',
            'blank_id',
            'kind',
            'label',
            'split_mode',
            'total_pieces',
            'created_at',
            'created_by_id',
            'client_request_id',
            'units',
            'run_allocations',
        )
