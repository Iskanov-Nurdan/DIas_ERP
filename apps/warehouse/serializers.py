from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from .models import WarehouseBatch
from .packaging import warehouse_packaging_breakdown


def _packaging_int_field(value):
    """Целое для API (упаковки в БД — Decimal, допускают .0000)."""
    if value is None:
        return None
    q = Decimal(str(value))
    return int(q.to_integral_value())


def _packaging_status_api(obj: WarehouseBatch) -> str:
    """UI-синонимы: not_packed / packed / opened."""
    if obj.inventory_form == WarehouseBatch.INVENTORY_UNPACKED:
        return 'not_packed'
    if obj.inventory_form == WarehouseBatch.INVENTORY_PACKED:
        return 'packed'
    if obj.inventory_form == WarehouseBatch.INVENTORY_OPEN_PACKAGE:
        return 'opened'
    return obj.inventory_form


class WarehouseBatchSerializer(serializers.ModelSerializer):
    package_opened = serializers.SerializerMethodField()
    open_package = serializers.SerializerMethodField()
    stock_form = serializers.CharField(source='inventory_form', read_only=True)
    packaging_status = serializers.SerializerMethodField()
    line_name = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    angle_deg = serializers.SerializerMethodField()
    shift_height = serializers.SerializerMethodField()
    shift_width = serializers.SerializerMethodField()
    shift_angle_deg = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product', read_only=True)
    product_id = serializers.SerializerMethodField()
    product_detail = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    sealed_packages_count = serializers.SerializerMethodField()
    open_package_pieces = serializers.SerializerMethodField()
    pieces_in_open_package = serializers.SerializerMethodField()
    sealed_pieces = serializers.SerializerMethodField()
    packaging_quantity_consistent = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseBatch
        fields = (
            'id',
            'product',
            'product_name',
            'product_id',
            'product_detail',
            'quantity',
            'available_quantity',
            'status',
            'date',
            'source_batch',
            'inventory_form',
            'stock_form',
            'packaging_status',
            'line_name',
            'height',
            'width',
            'angle_deg',
            'shift_height',
            'shift_width',
            'shift_angle_deg',
            'package_opened',
            'open_package',
            'unit_meters',
            'package_total_meters',
            'pieces_per_package',
            'packages_count',
            'sealed_packages_count',
            'open_package_pieces',
            'pieces_in_open_package',
            'sealed_pieces',
            'packaging_quantity_consistent',
            'otk_accepted',
            'otk_defect',
            'otk_defect_reason',
            'otk_comment',
            'otk_inspector_name',
            'otk_checked_at',
            'otk_status',
        )

    def get_product_id(self, obj):
        """Ключ продукта на строке = наименование (как в фильтре/упаковке)."""
        return (obj.product or '').strip() or None

    def get_product_detail(self, obj):
        """Вложенный объект продукта (на строке склада нет FK — id = тот же ключ, что product_id)."""
        name = (obj.product or '').strip()
        if not name:
            return None
        return {'id': name, 'name': name}

    def get_available_quantity(self, obj):
        if obj.status == WarehouseBatch.STATUS_AVAILABLE:
            return float(obj.quantity) if obj.quantity is not None else None
        return None

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

    def get_pieces_in_open_package(self, obj):
        return self.get_open_package_pieces(obj)

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

    def get_packaging_status(self, obj):
        return _packaging_status_api(obj)

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

    def _shift_height_val(self, obj):
        if obj.unit_meters is not None:
            return float(obj.unit_meters)
        pb = obj.source_batch
        if pb is not None and pb.shift_height is not None:
            return float(pb.shift_height)
        return None

    def get_shift_height(self, obj):
        return self._shift_height_val(obj)

    def get_height(self, obj):
        v = self._shift_height_val(obj)
        if v is not None:
            return v
        pb = obj.source_batch
        if pb is not None and pb.shift_height is not None:
            return float(pb.shift_height)
        return None

    def get_shift_width(self, obj):
        pb = obj.source_batch
        if pb is None or pb.shift_width is None:
            return None
        return float(pb.shift_width)

    def get_width(self, obj):
        return self.get_shift_width(obj)

    def get_shift_angle_deg(self, obj):
        pb = obj.source_batch
        if pb is None or pb.shift_angle_deg is None:
            return None
        return float(pb.shift_angle_deg)

    def get_angle_deg(self, obj):
        return self.get_shift_angle_deg(obj)

    def get_package_opened(self, obj):
        return obj.inventory_form == WarehouseBatch.INVENTORY_OPEN_PACKAGE

    def get_open_package(self, obj):
        return obj.inventory_form == WarehouseBatch.INVENTORY_OPEN_PACKAGE

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        dim_keys = ('unit_meters', 'package_total_meters')
        for k in dim_keys:
            if ret.get(k) is None:
                ret.pop(k, None)

        inv = instance.inventory_form
        packaging_row = inv in (
            WarehouseBatch.INVENTORY_PACKED,
            WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        )
        if packaging_row:
            ppp = _packaging_int_field(instance.pieces_per_package)
            pc = _packaging_int_field(instance.packages_count)
            ret['pieces_per_package'] = ppp
            ret['packages_count'] = pc
            # Алиасы счёта упаковок = только запечатанные (как packages_count в БД), не «все штуки / ppp».
            bd = self._packaging_breakdown(instance)
            s_cnt = bd['sealed_packages_count']
            ret['pieces_in_package'] = ppp
            ret['pieces_per_pack'] = ppp
            ret['packs_count'] = s_cnt if s_cnt is not None else pc
            ret['pack_count'] = s_cnt if s_cnt is not None else pc
            ret['package_count'] = s_cnt if s_cnt is not None else pc
            ret['num_packages'] = s_cnt if s_cnt is not None else pc
        else:
            for k in (
                'pieces_per_package',
                'packages_count',
                'sealed_packages_count',
                'open_package_pieces',
                'pieces_in_open_package',
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
        if ret.get('product_detail') is None:
            ret.pop('product_detail', None)
        return ret
