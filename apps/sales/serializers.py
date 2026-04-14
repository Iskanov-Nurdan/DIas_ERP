from decimal import Decimal
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import (
    PIECE_FROM_OPEN,
    PIECE_LOOSE,
    normalize_inventory_form,
    normalize_piece_pick,
)
from .models import Client, Sale, Shipment


def _sale_unit_is_package(sale_unit: str) -> bool:
    s = (sale_unit or '').strip().lower()
    return s in ('package', 'packages', 'pack')


def _normalize_sale_unit(value) -> str:
    if value is None:
        return ''
    s = str(value).strip().lower()
    if s in ('package', 'packages', 'pack'):
        return 'package'
    if s in ('piece', 'pieces', 'pcs', 'pc', 'шт', 'штук', 'штуки', 'штука'):
        return 'piece'
    return str(value).strip()


def _derive_quantity_input_packages(qty: Decimal, wb: WarehouseBatch) -> Optional[Decimal]:
    if wb is None:
        return None
    ppp = wb.pieces_per_package
    if ppp is None or Decimal(str(ppp)) <= 0:
        return None
    qd = Decimal(str(qty))
    ppp_d = Decimal(str(ppp))
    if qd % ppp_d != 0:
        return None
    return (qd / ppp_d).quantize(Decimal('1'))


def _quantity_input_api_value(v):
    if v is None:
        return None
    d = Decimal(str(v))
    if d == d.to_integral_value():
        return int(d)
    return float(d)


class ClientSerializer(serializers.ModelSerializer):
    contact_person = serializers.CharField(source='contact', required=False, allow_blank=True)
    whatsapp_telegram = serializers.CharField(source='messenger', required=False, allow_blank=True)
    sales_count = serializers.IntegerField(read_only=True, required=False, default=0)
    sales_total = serializers.DecimalField(
        max_digits=20, decimal_places=2, read_only=True, required=False, coerce_to_string=False,
    )
    has_sales = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = (
            'id', 'name', 'contact', 'contact_person', 'phone', 'phone_alt',
            'inn', 'address', 'email', 'messenger', 'whatsapp_telegram',
            'client_type', 'notes', 'is_active', 'status',
            'sales_count', 'sales_total', 'has_sales',
        )

    def get_status(self, obj):
        return 'active' if obj.is_active else 'inactive'

    def get_has_sales(self, obj):
        if hasattr(obj, 'sales_count'):
            return int(obj.sales_count or 0) > 0
        return obj.sales.exists()

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = dict(data)
            if data.get('phone_alt') in (None, '') and data.get('second_phone') not in (None, ''):
                data['phone_alt'] = data.get('second_phone')
            if data.get('notes') in (None, '') and data.get('comment') not in (None, ''):
                data['notes'] = data.get('comment')
            cp = data.get('contact_person')
            if cp not in (None, '') and (data.get('contact') in (None, '')):
                data['contact'] = cp
            if data.get('messenger') in (None, '') and data.get('whatsapp_telegram') not in (None, ''):
                data['messenger'] = data.get('whatsapp_telegram')
        return super().to_internal_value(data)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['second_phone'] = ret.get('phone_alt') or ''
        ret['comment'] = ret.get('notes') or ''
        st = ret.get('sales_total')
        if st is None:
            ret['sales_total'] = Decimal('0')
        return ret


class SaleSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    inventory_form = serializers.SerializerMethodField()
    quantity_unit = serializers.SerializerMethodField()
    order_number = serializers.CharField(required=False, allow_blank=True)
    date = serializers.DateField(required=False, allow_null=True)
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True,
    )
    warehouse_batch = serializers.PrimaryKeyRelatedField(
        queryset=WarehouseBatch.objects.all(), required=False, allow_null=True,
    )
    warehouse_batch_id = serializers.IntegerField(read_only=True)
    sale_unit = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    packaging = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    stock_form = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    piece_pick = serializers.CharField(required=False, allow_blank=True, max_length=40, default='')
    profile_name = serializers.SerializerMethodField()
    sale_date = serializers.SerializerMethodField()
    cost_total = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            'id', 'order_number', 'client', 'client_name', 'warehouse_batch', 'warehouse_batch_id',
            'product', 'quantity', 'sale_mode', 'sold_pieces', 'sold_packages',
            'length_per_piece', 'total_meters',
            'quantity_input', 'quantity_unit', 'price', 'revenue', 'cost', 'cost_total', 'date', 'sale_date',
            'comment',
            'sale_unit', 'packaging', 'stock_form', 'inventory_form', 'piece_pick', 'profit',
            'profile_name',
        )
        read_only_fields = (
            'profit', 'revenue', 'cost', 'cost_total', 'total_meters', 'inventory_form', 'quantity_unit',
            'warehouse_batch_id', 'profile_name', 'sale_date',
        )
        extra_kwargs = {
            'product': {'required': False, 'allow_blank': True},
        }

    def get_profile_name(self, obj):
        if not obj.warehouse_batch_id:
            return None
        try:
            wb = obj.warehouse_batch
            if wb.profile_id:
                return wb.profile.name
        except ObjectDoesNotExist:
            pass
        return None

    def get_sale_date(self, obj):
        return obj.date.isoformat() if obj.date else None

    def get_cost_total(self, obj):
        return obj.cost

    def get_inventory_form(self, obj):
        if obj.warehouse_batch_id:
            try:
                return obj.warehouse_batch.inventory_form
            except ObjectDoesNotExist:
                pass
        sf = (obj.stock_form or '').strip()
        return sf or None

    def get_quantity_unit(self, obj):
        s = (obj.sale_unit or '').strip()
        return s if s else None

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = dict(data)
            wb = data.get('warehouse_batch')
            wb_id = data.get('warehouse_batch_id')
            if wb in (None, '') and wb_id not in (None, ''):
                data['warehouse_batch'] = wb_id
            su = data.get('sale_unit')
            qu = data.get('quantity_unit')
            if (su is None or str(su).strip() == '') and qu is not None and str(qu).strip() != '':
                data['sale_unit'] = qu
            if data.get('sold_pieces') in (None, '') and data.get('quantity') not in (None, ''):
                data['sold_pieces'] = data.get('quantity')
            if data.get('date') in (None, '') and data.get('sale_date') not in (None, ''):
                data['date'] = data.get('sale_date')
        return super().to_internal_value(data)

    def validate(self, attrs):
        wb = attrs.get('warehouse_batch')
        prod = attrs.get('product')
        if prod is not None and str(prod).strip() == '':
            prod = None
            attrs['product'] = None
        if wb is not None and not prod:
            attrs['product'] = wb.product
        if not attrs.get('product'):
            raise serializers.ValidationError(
                {'product': 'Укажите product (наименование/артикул) или warehouse_batch_id партии склада ГП'},
            )

        if 'sale_unit' in attrs:
            attrs['sale_unit'] = _normalize_sale_unit(attrs['sale_unit'])

        mode = attrs.get('sale_mode') or (self.instance.sale_mode if self.instance else Sale.MODE_PIECES)
        if mode not in (Sale.MODE_PIECES, Sale.MODE_PACKAGES):
            mode = Sale.MODE_PIECES
        attrs['sale_mode'] = mode

        wb = attrs.get('warehouse_batch')
        if wb is not None and attrs.get('length_per_piece') is None:
            try:
                if wb.length_per_piece is not None:
                    attrs['length_per_piece'] = wb.length_per_piece
            except ObjectDoesNotExist:
                pass

        link_warehouse_first_time = wb is not None and (
            self.instance is None or self.instance.warehouse_batch_id is None
        )
        if link_warehouse_first_time:
            raw_sf = (self.initial_data or {}).get('stock_form', '')
            if raw_sf not in (None, ''):
                stock_form = normalize_inventory_form(raw_sf)
            else:
                stock_form = None
            raw_pp = (self.initial_data or {}).get('piece_pick', '')
            inv = wb.inventory_form
            if inv == WarehouseBatch.INVENTORY_UNPACKED:
                if raw_pp in (None, ''):
                    piece_pick = PIECE_LOOSE
                else:
                    piece_pick = normalize_piece_pick(raw_pp)
            elif inv == WarehouseBatch.INVENTORY_OPEN_PACKAGE:
                if raw_pp in (None, ''):
                    piece_pick = PIECE_FROM_OPEN
                else:
                    piece_pick = normalize_piece_pick(raw_pp)
            else:
                if raw_pp in (None, ''):
                    raise serializers.ValidationError(
                        {'piece_pick': 'Для упакованной партии укажите from_sealed_package или from_open_package'},
                    )
                piece_pick = normalize_piece_pick(raw_pp)
            attrs['stock_form'] = stock_form or inv
            attrs['piece_pick'] = piece_pick
            if attrs.get('stock_form') and not (attrs.get('packaging') or '').strip():
                attrs['packaging'] = attrs['stock_form']
        elif wb is None:
            attrs['stock_form'] = attrs.get('stock_form', '') or ''
            attrs['piece_pick'] = attrs.get('piece_pick', '') or ''

        return attrs

    def _fill_quantity_input(self, validated_data):
        unit = (validated_data.get('sale_unit') or '').strip().lower()
        if not _sale_unit_is_package(unit):
            return validated_data
        qi = validated_data.get('quantity_input')
        if qi is not None:
            return validated_data
        wb = validated_data.get('warehouse_batch')
        qty = validated_data.get('quantity')
        if qty is None:
            return validated_data
        derived = _derive_quantity_input_packages(Decimal(str(qty)), wb)
        if derived is not None:
            validated_data['quantity_input'] = derived
        return validated_data

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if _sale_unit_is_package(instance.sale_unit):
            qi = instance.quantity_input
            if qi is None and instance.warehouse_batch_id:
                try:
                    qi = _derive_quantity_input_packages(
                        Decimal(str(instance.quantity)),
                        instance.warehouse_batch,
                    )
                except ObjectDoesNotExist:
                    qi = None
            ret['quantity_input'] = _quantity_input_api_value(qi)
        else:
            ret.pop('quantity_input', None)
        return ret

    def _apply_finance(self, validated_data):
        mode = validated_data.get('sale_mode') or Sale.MODE_PIECES
        wb = validated_data.get('warehouse_batch')
        price = validated_data.get('price') or Decimal('0')
        if mode == Sale.MODE_PACKAGES:
            spk = validated_data.get('sold_packages') or Decimal('0')
            ppp = None
            if wb:
                try:
                    ppp = wb.pieces_per_package
                except ObjectDoesNotExist:
                    ppp = None
            if ppp and Decimal(str(ppp)) > 0:
                validated_data['sold_pieces'] = (Decimal(str(spk)) * Decimal(str(ppp))).quantize(Decimal('0.0001'))
            validated_data['revenue'] = (Decimal(str(price)) * Decimal(str(spk))).quantize(Decimal('0.01'))
        else:
            sp = validated_data.get('sold_pieces')
            if sp is None:
                sp = validated_data.get('quantity') or Decimal('0')
                validated_data['sold_pieces'] = sp
            validated_data['revenue'] = (Decimal(str(price)) * Decimal(str(sp))).quantize(Decimal('0.01'))
        spieces = Decimal(str(validated_data.get('sold_pieces') or 0))
        validated_data['quantity'] = spieces
        lp = validated_data.get('length_per_piece')
        if lp is not None:
            validated_data['total_meters'] = (spieces * Decimal(str(lp))).quantize(Decimal('0.0001'))
        cpp = Decimal('0')
        if wb:
            try:
                cpp = Decimal(str(wb.cost_per_piece or 0))
            except ObjectDoesNotExist:
                cpp = Decimal('0')
        validated_data['cost'] = (spieces * cpp).quantize(Decimal('0.01'))
        validated_data['profit'] = (validated_data['revenue'] - validated_data['cost']).quantize(Decimal('0.01'))

    def create(self, validated_data):
        validated_data = self._fill_quantity_input(validated_data)
        self._apply_finance(validated_data)
        if not validated_data.get('order_number'):
            today = timezone.now().date()
            year = today.year
            last_sale = Sale.objects.filter(
                order_number__startswith=f'ORD-{year}-'
            ).order_by('-order_number').first()

            if last_sale:
                try:
                    last_number = int(last_sale.order_number.split('-')[-1])
                    new_number = last_number + 1
                except (ValueError, IndexError):
                    new_number = 1
            else:
                new_number = 1

            validated_data['order_number'] = f'ORD-{year}-{new_number:03d}'

        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()

        wb = validated_data.get('warehouse_batch')
        wb_pk = wb.pk if wb else None
        qty = validated_data['quantity']
        stock_sf = validated_data.get('stock_form') or ''
        pp = validated_data.get('piece_pick') or None

        from apps.warehouse.stock_ops import apply_sale_to_warehouse_batch

        with transaction.atomic():
            instance = super().create(validated_data)
            if wb_pk:
                apply_sale_to_warehouse_batch(wb_pk, Decimal(str(qty)), stock_sf, pp)
        return instance

    def update(self, instance, validated_data):
        attaching_wb = (
            instance.warehouse_batch_id is None
            and validated_data.get('warehouse_batch') is not None
        )
        wb_pk = validated_data['warehouse_batch'].pk if attaching_wb else None

        from apps.warehouse.stock_ops import apply_sale_to_warehouse_batch

        merged = {**{f: getattr(instance, f) for f in (
            'sale_mode', 'sold_pieces', 'sold_packages', 'length_per_piece', 'price', 'warehouse_batch',
        )}, **validated_data}
        self._apply_finance(merged)
        validated_data.update({k: merged[k] for k in (
            'sold_pieces', 'sold_packages', 'quantity', 'length_per_piece', 'total_meters',
            'revenue', 'cost', 'profit',
        ) if k in merged})

        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if wb_pk:
                apply_sale_to_warehouse_batch(
                    wb_pk,
                    Decimal(str(instance.quantity)),
                    instance.stock_form or '',
                    instance.piece_pick or None,
                )
        return instance
