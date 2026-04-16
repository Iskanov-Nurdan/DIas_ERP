from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from config.api_numbers import api_decimal_str

from .models import WarehouseBatch
from .packaging import warehouse_packaging_breakdown


def _packaging_int_field(value):
    """Целое для API (упаковки в БД — Decimal, допускают .0000)."""
    if value is None:
        return None
    q = Decimal(str(value))
    return int(q.to_integral_value())


class WarehouseBatchSerializer(serializers.ModelSerializer):
    """Склад ГП: один канон `inventory_form`, без дублей габаритов и алиасов упаковки."""
    line_name = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    angle_deg = serializers.SerializerMethodField()
    sealed_packages_count = serializers.SerializerMethodField()
    open_package_pieces = serializers.SerializerMethodField()
    sealed_pieces = serializers.SerializerMethodField()
    packaging_quantity_consistent = serializers.SerializerMethodField()

    class Meta:
        model = WarehouseBatch
        fields = (
            'id',
            'product',
            'quantity',
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
