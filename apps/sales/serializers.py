from decimal import Decimal
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from config.api_numbers import api_decimal_str
from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import (
    PIECE_FROM_OPEN,
    PIECE_LOOSE,
    normalize_inventory_form,
    normalize_piece_pick,
    apply_sale_to_warehouse_batch,
)
from .models import (
    Client,
    ClientPrice,
    Order,
    OrderLine,
    OrderReservation,
    Payment,
    PriceList,
    ProductPrice,
    Return,
    ReturnLine,
    DefectRecord,
    ReworkRequest,
    Sale,
    SaleLine,
    Shipment,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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
    return api_decimal_str(Decimal(str(v)))


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ClientSerializer(serializers.ModelSerializer):
    contact_person = serializers.CharField(source='contact', required=False, allow_blank=True, write_only=True)
    whatsapp_telegram = serializers.CharField(source='messenger', required=False, allow_blank=True, write_only=True)
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
            'credit_limit', 'credit_limit_mode',
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
        st = ret.get('sales_total')
        if st is not None:
            ret['sales_total'] = api_decimal_str(Decimal(str(st)))
        else:
            ret['sales_total'] = '0'
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# ORDER (Заявка)
# ─────────────────────────────────────────────────────────────────────────────

class OrderLineSerializer(serializers.ModelSerializer):
    remaining_quantity = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()
    available_to_ship = serializers.SerializerMethodField()
    remaining_to_reserve = serializers.SerializerMethodField()

    class Meta:
        model = OrderLine
        fields = (
            'id', 'product', 'product_type', 'profile',
            'ordered_quantity', 'shipped_quantity', 'reserved_quantity',
            'unit_price', 'comment',
            'remaining_quantity', 'available_to_ship',
            'remaining_to_reserve', 'line_total',
        )
        read_only_fields = (
            'shipped_quantity', 'reserved_quantity',
            'remaining_quantity', 'available_to_ship',
            'remaining_to_reserve', 'line_total',
        )
        extra_kwargs = {
            'profile': {'required': False, 'allow_null': True},
        }

    def get_remaining_quantity(self, obj):
        return api_decimal_str(obj.remaining_quantity)

    def get_available_to_ship(self, obj):
        return api_decimal_str(obj.available_to_ship)

    def get_remaining_to_reserve(self, obj):
        return api_decimal_str(obj.remaining_to_reserve)

    def get_line_total(self, obj):
        return api_decimal_str(obj.line_total)


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, required=False)
    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    responsible_user_name = serializers.CharField(source='responsible_user.name', read_only=True, allow_null=True, default='')
    total_amount = serializers.SerializerMethodField()
    shipped_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    has_company_debt_by_goods = serializers.SerializerMethodField()
    paid_amount = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id', 'order_number', 'date', 'client', 'client_name',
            'source_type', 'comment', 'status',
            'created_by', 'created_by_name',
            'responsible_user', 'responsible_user_name',
            'created_at', 'updated_at',
            'lines',
            'total_amount', 'shipped_amount', 'remaining_amount',
            'paid_amount', 'has_company_debt_by_goods',
        )
        read_only_fields = (
            'order_number', 'created_at', 'updated_at',
            'total_amount', 'shipped_amount', 'remaining_amount',
            'paid_amount', 'has_company_debt_by_goods',
        )
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
            'responsible_user': {'required': False, 'allow_null': True},
        }

    def get_total_amount(self, obj):
        return api_decimal_str(obj.total_amount)

    def get_shipped_amount(self, obj):
        return api_decimal_str(obj.shipped_amount)

    def get_remaining_amount(self, obj):
        return api_decimal_str(obj.remaining_amount)

    def get_has_company_debt_by_goods(self, obj):
        return obj.has_company_debt_by_goods

    def get_paid_amount(self, obj):
        total = sum(
            (p.amount or Decimal('0'))
            for p in obj.payments.all()
            if p.payment_type in (Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE)
        )
        refunds = sum(
            (p.amount or Decimal('0'))
            for p in obj.payments.all()
            if p.payment_type == Payment.TYPE_REFUND
        )
        return api_decimal_str(total - refunds)

    def create(self, validated_data):
        lines_data = validated_data.pop('lines', [])
        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()

        # Автогенерация номера
        year = (validated_data.get('date') or timezone.now().date()).year
        last = Order.objects.filter(order_number__startswith=f'ORD-{year}-').order_by('-order_number').first()
        try:
            last_n = int(last.order_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        validated_data['order_number'] = f'ORD-{year}-{last_n + 1:04d}'

        with transaction.atomic():
            order = super().create(validated_data)
            for line_data in lines_data:
                OrderLine.objects.create(order=order, **line_data)
        return order

    def update(self, instance, validated_data):
        lines_data = validated_data.pop('lines', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if lines_data is not None:
                # Обновляем/создаём строки (не удаляем существующие без явного запроса)
                existing_ids = {line.id for line in instance.lines.all()}
                for line_data in lines_data:
                    line_id = line_data.pop('id', None)
                    if line_id and line_id in existing_ids:
                        OrderLine.objects.filter(pk=line_id).update(**line_data)
                    else:
                        OrderLine.objects.create(order=instance, **line_data)
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT (Оплата)
# ─────────────────────────────────────────────────────────────────────────────

class PaymentSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = Payment
        fields = (
            'id', 'payment_number', 'date', 'client', 'client_name',
            'linked_order', 'linked_sale',
            'payment_type', 'amount', 'payment_method',
            'comment', 'created_by', 'created_by_name', 'created_at',
        )
        read_only_fields = ('created_at', 'payment_number')
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'linked_order': {'required': False, 'allow_null': True},
            'linked_sale': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def validate_amount(self, v):
        if v is None:
            raise serializers.ValidationError('Сумма обязательна')
        if v < 0:
            raise serializers.ValidationError('Сумма не может быть отрицательной')
        return v

    def create(self, validated_data):
        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()
        if not validated_data.get('payment_number'):
            year = (validated_data.get('date') or timezone.now().date()).year
            last = Payment.objects.filter(payment_number__startswith=f'PAY-{year}-').order_by('-payment_number').first()
            try:
                last_n = int(last.payment_number.split('-')[-1]) if last else 0
            except (ValueError, IndexError):
                last_n = 0
            validated_data['payment_number'] = f'PAY-{year}-{last_n + 1:04d}'
        return super().create(validated_data)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['amount'] = api_decimal_str(Decimal(str(instance.amount or 0)))
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# SALE (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

class SaleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleLine
        fields = (
            'id', 'product', 'warehouse_batch', 'order_line',
            'stock_form', 'quantity', 'unit_price', 'line_total',
            'cost', 'profit', 'defect_flag', 'comment',
        )
        read_only_fields = ('line_total', 'cost', 'profit')
        extra_kwargs = {
            'warehouse_batch': {'required': False, 'allow_null': True},
            'order_line': {'required': False, 'allow_null': True},
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for k in ('quantity', 'unit_price', 'line_total', 'cost', 'profit'):
            if ret.get(k) is not None:
                ret[k] = api_decimal_str(Decimal(str(ret[k])))
        return ret


class SaleSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    inventory_form = serializers.SerializerMethodField()
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
    sale_lines = SaleLineSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = Sale
        fields = (
            'id', 'order_number', 'sale_number', 'invoice_number', 'receipt_number',
            'sale_status', 'linked_order',
            'client', 'client_name', 'warehouse_batch', 'warehouse_batch_id',
            'product', 'quantity', 'sale_mode', 'sold_pieces', 'sold_packages',
            'length_per_piece', 'total_meters',
            'quantity_input', 'price', 'revenue', 'cost', 'date',
            'comment',
            'sale_unit', 'packaging', 'stock_form', 'inventory_form', 'piece_pick', 'profit',
            'profile_name', 'stock_quality',
            'is_defect_sale',
            'created_by', 'created_by_name', 'created_at',
            'sale_lines',
        )
        read_only_fields = (
            'profit', 'revenue', 'cost', 'total_meters', 'inventory_form',
            'warehouse_batch_id', 'profile_name', 'stock_quality',
            'created_at', 'sale_lines',
        )
        extra_kwargs = {
            'product': {'required': False, 'allow_blank': True},
            'linked_order': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        wb = self.fields.get('warehouse_batch')
        if wb is not None:
            wb.queryset = WarehouseBatch.objects.filter(status=WarehouseBatch.STATUS_AVAILABLE)

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

    def get_inventory_form(self, obj):
        if obj.warehouse_batch_id:
            try:
                return obj.warehouse_batch.inventory_form
            except ObjectDoesNotExist:
                pass
        sf = (obj.stock_form or '').strip()
        return sf or None

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
            data.pop('quantity_unit', None)
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

        # ── Sale-without-reservation policy ───────────────────────────────────
        from django.conf import settings as django_settings
        if getattr(django_settings, 'SALE_REQUIRES_RESERVATION', False):
            linked_order = attrs.get('linked_order')
            if linked_order is not None:
                wb_for_policy = attrs.get('warehouse_batch')
                if wb_for_policy is not None:
                    has_reservation = OrderReservation.objects.filter(
                        order_line__order=linked_order,
                        warehouse_batch=wb_for_policy,
                        status=OrderReservation.STATUS_ACTIVE,
                    ).exists()
                    if not has_reservation:
                        raise serializers.ValidationError({
                            'linked_order': (
                                'Продажа без активного резерва запрещена политикой системы. '
                                'Сначала создайте резерв через /api/orders/{id}/reserve/.'
                            )
                        })

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
        for key in ('quantity', 'sold_pieces', 'sold_packages', 'length_per_piece', 'total_meters', 'price', 'revenue', 'cost', 'profit'):
            if key in ret and ret[key] is not None:
                ret[key] = api_decimal_str(Decimal(str(ret[key])))
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

        # ── Hard credit limit enforcement ─────────────────────────────────────
        client = validated_data.get('client')
        if client is not None:
            from .credit_check import enforce_credit_limit, CreditLimitBlocked
            revenue = validated_data.get('revenue') or Decimal('0')
            request = self.context.get('request')
            user = getattr(request, 'user', None)
            force_override = (
                str((self.initial_data or {}).get('force_credit_override', '')).lower()
                in ('1', 'true', 'yes')
            )
            try:
                enforce_credit_limit(client, revenue, user=user, force_override=force_override)
            except CreditLimitBlocked as exc:
                raise serializers.ValidationError({'credit_limit': str(exc)})

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
        if wb is not None:
            validated_data['stock_quality'] = wb.quality
        wb_pk = wb.pk if wb else None
        qty = validated_data['quantity']
        stock_sf = validated_data.get('stock_form') or ''
        pp = validated_data.get('piece_pick') or None
        linked_order = validated_data.get('linked_order')

        with transaction.atomic():
            instance = super().create(validated_data)
            if wb_pk:
                apply_sale_to_warehouse_batch(wb_pk, Decimal(str(qty)), stock_sf, pp)
            # ── Auto-fulfill reservations + update OrderLine.shipped_quantity ──
            if linked_order is not None and wb_pk is not None:
                from .reservations import auto_fulfill_for_sale
                request = self.context.get('request')
                user = getattr(request, 'user', None) if request else None
                auto_fulfill_for_sale(
                    sale=instance,
                    order=linked_order,
                    warehouse_batch_id=wb_pk,
                    quantity=Decimal(str(qty)),
                    user=user,
                    request=request,
                )
        return instance

    def update(self, instance, validated_data):
        attaching_wb = (
            instance.warehouse_batch_id is None
            and validated_data.get('warehouse_batch') is not None
        )
        wb_pk = validated_data['warehouse_batch'].pk if attaching_wb else None

        merged = {**{f: getattr(instance, f) for f in (
            'sale_mode', 'sold_pieces', 'sold_packages', 'length_per_piece', 'price', 'warehouse_batch',
        )}, **validated_data}
        self._apply_finance(merged)
        validated_data.update({k: merged[k] for k in (
            'sold_pieces', 'sold_packages', 'quantity', 'length_per_piece', 'total_meters',
            'revenue', 'cost', 'profit',
        ) if k in merged})
        if attaching_wb and validated_data.get('warehouse_batch') is not None:
            validated_data['stock_quality'] = validated_data['warehouse_batch'].quality

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


# ─────────────────────────────────────────────────────────────────────────────
# RETURN (Возврат)
# ─────────────────────────────────────────────────────────────────────────────

class ReturnLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnLine
        fields = (
            'id', 'sale_line', 'product', 'quantity',
            'return_target', 'condition_type', 'comment',
        )
        extra_kwargs = {
            'sale_line': {'required': False, 'allow_null': True},
        }

    def validate(self, attrs):
        sale_line = attrs.get('sale_line')
        if sale_line and not self.instance:
            qty = attrs.get('quantity', Decimal('0'))
            # Проверяем, что не возвращаем больше, чем было отгружено
            total_returned = sum(
                rl.quantity for rl in ReturnLine.objects.filter(sale_line=sale_line)
            )
            if total_returned + qty > sale_line.quantity:
                raise serializers.ValidationError({
                    'quantity': (
                        f'Нельзя вернуть больше, чем было отгружено по строке '
                        f'(отгружено: {sale_line.quantity}, уже возвращено: {total_returned})'
                    )
                })
        return attrs


class ReturnSerializer(serializers.ModelSerializer):
    lines = ReturnLineSerializer(many=True, required=False)
    sale_order_number = serializers.CharField(source='sale.order_number', read_only=True)
    client_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = Return
        fields = (
            'id', 'return_number', 'date', 'sale', 'sale_order_number',
            'linked_order', 'invoice_number',
            'return_reason', 'comment',
            'created_by', 'created_by_name', 'created_at',
            'lines', 'client_name',
        )
        read_only_fields = ('return_number', 'created_at', 'sale_order_number', 'client_name')
        extra_kwargs = {
            'linked_order': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def get_client_name(self, obj):
        if obj.sale and obj.sale.client:
            return obj.sale.client.name
        return ''

    def create(self, validated_data):
        lines_data = validated_data.pop('lines', [])
        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()

        year = (validated_data.get('date') or timezone.now().date()).year
        last = Return.objects.filter(return_number__startswith=f'RET-{year}-').order_by('-return_number').first()
        try:
            last_n = int(last.return_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        validated_data['return_number'] = f'RET-{year}-{last_n + 1:04d}'

        with transaction.atomic():
            ret_doc = super().create(validated_data)
            for line_data in lines_data:
                line = ReturnLine.objects.create(return_doc=ret_doc, **line_data)
                self._process_return_line(line, ret_doc)
        return ret_doc

    def _process_return_line(self, line: ReturnLine, ret_doc: Return):
        """Обрабатываем возврат — возврат на склад, в брак или на переделку."""
        from apps.warehouse.models import WarehouseBatch
        from apps.warehouse.packaging import q4

        if line.return_target == ReturnLine.TARGET_WAREHOUSE:
            # Возвращаем на склад ГП
            sale = ret_doc.sale
            wb = sale.warehouse_batch
            if wb:
                wb.quantity = q4(wb.quantity + line.quantity)
                if wb.status == WarehouseBatch.STATUS_SHIPPED:
                    wb.status = WarehouseBatch.STATUS_AVAILABLE
                wb.save(update_fields=['quantity', 'status'])

        elif line.return_target == ReturnLine.TARGET_DEFECT:
            # Создаём запись брака
            product = line.product or (
                line.sale_line.product if line.sale_line else ret_doc.sale.product
            )
            DefectRecord.objects.create(
                source_type=DefectRecord.SOURCE_RETURN,
                source_id=line.id,
                product=product,
                quantity_pcs=line.quantity,
                defect_reason=ret_doc.return_reason or '',
                status=DefectRecord.STATUS_ON_STOCK,
                comment=line.comment or '',
            )

        elif line.return_target == ReturnLine.TARGET_REWORK:
            # Создаём заявку на переделку
            product = line.product or (
                line.sale_line.product if line.sale_line else ret_doc.sale.product
            )
            defect = DefectRecord.objects.create(
                source_type=DefectRecord.SOURCE_RETURN,
                source_id=line.id,
                product=product,
                quantity_pcs=line.quantity,
                defect_reason=ret_doc.return_reason or '',
                status=DefectRecord.STATUS_SENT_TO_REWORK,
                comment=line.comment or '',
            )
            ReworkRequest.objects.create(
                return_doc=ret_doc,
                defect_record=defect,
                original_sale=ret_doc.sale,
                product=product,
                quantity_kg=Decimal('0'),
                status=ReworkRequest.STATUS_PENDING,
                comment=line.comment or '',
            )


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT RECORD (Брак)
# ─────────────────────────────────────────────────────────────────────────────

class DefectRecordSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    profile_name = serializers.CharField(source='profile.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = DefectRecord
        fields = (
            'id', 'source_type', 'source_id',
            'profile', 'profile_name', 'product',
            'quantity_pcs', 'quantity_kg', 'kg_coefficient',
            'defect_reason', 'status', 'writeoff_reason',
            'comment', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')
        extra_kwargs = {
            'profile': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
            'source_id': {'required': False, 'allow_null': True},
        }

    def validate(self, attrs):
        status = attrs.get('status', self.instance.status if self.instance else DefectRecord.STATUS_NEW)
        if status == DefectRecord.STATUS_WRITTEN_OFF:
            if not attrs.get('writeoff_reason') and not (self.instance and self.instance.writeoff_reason):
                raise serializers.ValidationError(
                    {'writeoff_reason': 'Причина списания обязательна при статусе «списан»'}
                )
        return attrs

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for k in ('quantity_pcs', 'quantity_kg', 'kg_coefficient'):
            if ret.get(k) is not None:
                ret[k] = api_decimal_str(Decimal(str(ret[k])))
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# REWORK REQUEST (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

class ReworkRequestSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    rework_loss_kg = serializers.SerializerMethodField()
    recovered_output = serializers.SerializerMethodField()

    class Meta:
        model = ReworkRequest
        fields = (
            'id', 'rework_number', 'return_doc', 'defect_record', 'original_sale',
            'product', 'quantity_kg', 'output_quantity_kg', 'loss_kg', 'conversion_rate',
            'status', 'result_warehouse_batch',
            'rework_loss_kg', 'recovered_output',
            'comment', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = ('rework_number', 'created_at', 'updated_at', 'rework_loss_kg', 'recovered_output')
        extra_kwargs = {
            'defect_record': {'required': False, 'allow_null': True},
            'original_sale': {'required': False, 'allow_null': True},
            'result_warehouse_batch': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
            'output_quantity_kg': {'required': False, 'allow_null': True},
            'loss_kg': {'required': False, 'allow_null': True},
            'conversion_rate': {'required': False, 'allow_null': True},
        }

    def get_rework_loss_kg(self, obj):
        v = obj.rework_loss_kg
        return api_decimal_str(v) if v is not None else None

    def get_recovered_output(self, obj):
        v = obj.recovered_output
        return api_decimal_str(Decimal(str(v))) if v is not None else None

    def create(self, validated_data):
        year = timezone.now().date().year
        last = ReworkRequest.objects.filter(rework_number__startswith=f'RWK-{year}-').order_by('-rework_number').first()
        try:
            last_n = int(last.rework_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        validated_data['rework_number'] = f'RWK-{year}-{last_n + 1:04d}'
        return super().create(validated_data)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for k in ('quantity_kg', 'output_quantity_kg', 'loss_kg', 'conversion_rate'):
            if ret.get(k) is not None:
                ret[k] = api_decimal_str(Decimal(str(ret[k])))
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT HISTORY (Карточка клиента — агрегированная история)
# ─────────────────────────────────────────────────────────────────────────────

class ClientHistorySerializer(serializers.Serializer):
    """Агрегированная история клиента для карточки."""
    client_id = serializers.IntegerField()
    client_name = serializers.CharField()
    orders = OrderSerializer(many=True)
    sales = SaleSerializer(many=True)
    payments = PaymentSerializer(many=True)
    returns = ReturnSerializer(many=True)
    total_ordered = serializers.CharField()
    total_paid = serializers.CharField()
    client_debt_money = serializers.CharField()
    client_advance_amount = serializers.CharField()
    has_unshipped_goods = serializers.BooleanField()


# ─────────────────────────────────────────────────────────────────────────────
# ПРАЙС-ЛИСТЫ
# ─────────────────────────────────────────────────────────────────────────────

class ProductPriceSerializer(serializers.ModelSerializer):
    profile_name = serializers.CharField(source='profile.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = ProductPrice
        fields = ('id', 'price_list', 'profile', 'profile_name', 'product', 'price', 'unit')
        extra_kwargs = {
            'profile': {'required': False, 'allow_null': True},
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if ret.get('price') is not None:
            ret['price'] = api_decimal_str(Decimal(str(ret['price'])))
        return ret


class PriceListSerializer(serializers.ModelSerializer):
    product_prices = ProductPriceSerializer(many=True, required=False)

    class Meta:
        model = PriceList
        fields = ('id', 'name', 'is_active', 'valid_from', 'valid_to', 'comment', 'created_at', 'product_prices')
        read_only_fields = ('created_at',)

    def create(self, validated_data):
        prices_data = validated_data.pop('product_prices', [])
        with transaction.atomic():
            pl = super().create(validated_data)
            for pd in prices_data:
                ProductPrice.objects.create(price_list=pl, **pd)
        return pl

    def update(self, instance, validated_data):
        prices_data = validated_data.pop('product_prices', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if prices_data is not None:
                instance.product_prices.all().delete()
                for pd in prices_data:
                    ProductPrice.objects.create(price_list=instance, **pd)
        return instance


class ClientPriceSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True, default='')
    profile_name = serializers.CharField(source='profile.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = ClientPrice
        fields = (
            'id', 'client', 'client_name', 'profile', 'profile_name',
            'product', 'price', 'unit',
            'valid_from', 'valid_to', 'comment', 'created_at',
        )
        read_only_fields = ('created_at',)
        extra_kwargs = {
            'profile': {'required': False, 'allow_null': True},
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if ret.get('price') is not None:
            ret['price'] = api_decimal_str(Decimal(str(ret['price'])))
        return ret


# ─────────────────────────────────────────────────────────────────────────────
# ORDER RESERVATION
# ─────────────────────────────────────────────────────────────────────────────

class OrderReservationSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    order_line_product = serializers.CharField(source='order_line.product', read_only=True, default='')
    warehouse_batch_product = serializers.CharField(source='warehouse_batch.product', read_only=True, default='')

    class Meta:
        model = OrderReservation
        fields = (
            'id', 'order_line', 'order_line_product',
            'warehouse_batch', 'warehouse_batch_product',
            'quantity', 'fulfilled_quantity', 'status',
            'sale_line', 'comment',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at', 'status', 'fulfilled_quantity', 'sale_line')
        extra_kwargs = {
            'created_by': {'required': False, 'allow_null': True},
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for key in ('quantity', 'fulfilled_quantity'):
            if ret.get(key) is not None:
                ret[key] = api_decimal_str(Decimal(str(ret[key])))
        return ret
