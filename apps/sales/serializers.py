from decimal import Decimal
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DrfValidationError

from config.api_numbers import api_decimal_str
from apps.warehouse.models import WarehouseBatch
from apps.warehouse.stock_ops import (
    PIECE_FROM_OPEN,
    PIECE_LOOSE,
    normalize_inventory_form,
    normalize_piece_pick,
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
    sales_count = serializers.IntegerField(read_only=True, required=False, default=0)
    sales_total = serializers.DecimalField(
        max_digits=20, decimal_places=2, read_only=True, required=False, coerce_to_string=False,
    )
    has_sales = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = (
            'id', 'name', 'contact', 'phone', 'phone_alt',
            'inn', 'address', 'email', 'messenger',
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
            'product': {'required': False, 'allow_blank': True},
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
    payment_status = serializers.SerializerMethodField()
    debt_amount = serializers.SerializerMethodField()
    refund_amount = serializers.SerializerMethodField()

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
            'paid_amount', 'payment_status', 'debt_amount', 'refund_amount',
            'has_company_debt_by_goods',
        )
        read_only_fields = (
            'order_number', 'created_at', 'updated_at',
            'total_amount', 'shipped_amount', 'remaining_amount',
            'paid_amount', 'payment_status', 'debt_amount', 'refund_amount',
            'has_company_debt_by_goods',
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
        from .payment_status import order_payment_metrics
        return api_decimal_str(order_payment_metrics(obj)['paid_amount'])

    def get_payment_status(self, obj):
        from .payment_status import order_payment_metrics
        return order_payment_metrics(obj)['payment_status']

    def get_debt_amount(self, obj):
        from .payment_status import order_payment_metrics
        return api_decimal_str(order_payment_metrics(obj)['debt_amount'])

    def get_refund_amount(self, obj):
        from .payment_status import order_payment_metrics
        return api_decimal_str(order_payment_metrics(obj)['refund_amount'])

    @staticmethod
    def _raise_order_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'detail': message,
                'errors': [{'field': field, 'message': message}],
            },
        )

    @staticmethod
    def _validate_order_line_payload(line_data: dict, existing_line: OrderLine | None = None) -> dict:
        payload = dict(line_data)
        raw_product = payload.get('product', None)
        if (raw_product is None or not str(raw_product).strip()) and existing_line is not None:
            product = existing_line.product
        else:
            product = raw_product or ''
        raw_profile = payload.get('profile', None)
        if raw_profile is None and existing_line is not None:
            profile = existing_line.profile
        else:
            profile = raw_profile
        if not (str(product or '').strip()) and profile is None:
            OrderSerializer._raise_order_error(
                'PRODUCT_OR_PROFILE_REQUIRED',
                'Укажите product или profile в строке заявки.',
                field='lines',
            )

        ordered = payload.get(
            'ordered_quantity',
            existing_line.ordered_quantity if existing_line else None,
        )
        if ordered is None:
            OrderSerializer._raise_order_error(
                'ORDERED_QUANTITY_REQUIRED',
                'Поле ordered_quantity обязательно.',
                field='lines',
            )
        ordered_d = Decimal(str(ordered))
        if ordered_d <= 0:
            OrderSerializer._raise_order_error(
                'ORDERED_QUANTITY_INVALID',
                'ordered_quantity должно быть больше 0.',
                field='lines',
            )

        if existing_line is not None:
            shipped_d = Decimal(str(existing_line.shipped_quantity or 0))
            if ordered_d < shipped_d:
                OrderSerializer._raise_order_error(
                    'ORDERED_QUANTITY_LT_SHIPPED',
                    f'ordered_quantity не может быть меньше уже проданного количества ({shipped_d}).',
                    field='lines',
                )

        unit_price = payload.get('unit_price', existing_line.unit_price if existing_line else None)
        if unit_price is None or unit_price == '':
            payload['unit_price'] = Decimal('0')
        else:
            unit_price_d = Decimal(str(unit_price))
            if unit_price_d < 0:
                OrderSerializer._raise_order_error(
                    'UNIT_PRICE_NEGATIVE',
                    'unit_price не может быть отрицательной.',
                    field='lines',
                )

        return payload

    def validate(self, attrs):
        if self.instance is None:
            client = attrs.get('client')
            if client is None:
                self._raise_order_error(
                    'MISSING_CLIENT',
                    'Поле client обязательно для создания заявки.',
                    field='client',
                )
            if client and not client.is_active:
                self._raise_order_error(
                    'INACTIVE_CLIENT',
                    'Клиент неактивен. Создание заявки запрещено.',
                    field='client',
                )
            lines = (self.initial_data or {}).get('lines')
            if not isinstance(lines, list) or len(lines) < 1:
                self._raise_order_error(
                    'MISSING_LINES',
                    'Нужна минимум одна строка заявки.',
                    field='lines',
                )
            for line in lines:
                self._validate_order_line_payload(line)
            return attrs

        if 'status' in (self.initial_data or {}) or 'status' in attrs:
            self._raise_order_error(
                'STATUS_UPDATE_FORBIDDEN',
                'Статус заявки меняется только через /status/.',
                field='status',
            )

        if self.instance.status in (Order.STATUS_CLOSED, Order.STATUS_CANCELED):
            self._raise_order_error(
                'ORDER_UPDATE_FORBIDDEN',
                f'Редактирование заявки в статусе "{self.instance.status}" запрещено.',
            )

        if 'lines' in (self.initial_data or {}):
            if self.instance.status in (
                Order.STATUS_PARTIALLY_SHIPPED,
                Order.STATUS_SHIPPED,
                Order.STATUS_CLOSED,
                Order.STATUS_CANCELED,
            ):
                self._raise_order_error(
                    'ORDER_LINES_UPDATE_FORBIDDEN',
                    f'Изменение строк заявки в статусе "{self.instance.status}" запрещено.',
                    field='lines',
                )
            active_sales = self.instance.sales.exclude(
                sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED),
            )
            if active_sales.exists():
                self._raise_order_error(
                    'ORDER_LINES_UPDATE_FORBIDDEN',
                    'Нельзя менять строки заявки: есть активные продажи.',
                    field='lines',
                )
            if SaleLine.objects.filter(order_line__order=self.instance).exists():
                self._raise_order_error(
                    'ORDER_LINES_UPDATE_FORBIDDEN',
                    'Нельзя менять строки заявки: есть связанные строки продажи.',
                    field='lines',
                )

        return attrs

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
                normalized = self._validate_order_line_payload(line_data)
                OrderLine.objects.create(order=order, **normalized)
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
                        line = OrderLine.objects.get(pk=line_id, order=instance)
                        has_returns = ReturnLine.objects.filter(sale_line__order_line=line).exists()
                        if line.sale_lines.exists() or line.reservations.exists() or has_returns:
                            self._raise_order_error(
                                'ORDER_LINE_LOCKED',
                                f'Нельзя изменить строку #{line.pk}: по ней уже есть продажа/резерв/возврат.',
                                field='lines',
                            )
                        normalized = self._validate_order_line_payload(line_data, existing_line=line)
                        OrderLine.objects.filter(pk=line_id).update(**normalized)
                    else:
                        normalized = self._validate_order_line_payload(line_data)
                        OrderLine.objects.create(order=instance, **normalized)
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
            'linked_order', 'linked_sale', 'linked_return',
            'payment_type', 'amount', 'payment_method', 'status',
            'manual_refund_reason',
            'comment', 'created_by', 'created_by_name', 'created_at',
        )
        read_only_fields = ('created_at', 'payment_number')
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'linked_order': {'required': False, 'allow_null': True},
            'linked_sale': {'required': False, 'allow_null': True},
            'linked_return': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def validate_amount(self, v):
        if v is None:
            raise serializers.ValidationError('Сумма обязательна')
        if v < 0:
            raise serializers.ValidationError('Сумма не может быть отрицательной')
        return v

    def validate(self, attrs):
        client = attrs.get('client', getattr(self.instance, 'client', None) if self.instance else None)
        lo = attrs.get('linked_order', getattr(self.instance, 'linked_order', None) if self.instance else None)
        ls = attrs.get('linked_sale', getattr(self.instance, 'linked_sale', None) if self.instance else None)
        if lo is not None and client is not None and lo.client_id and lo.client_id != client.pk:
            raise serializers.ValidationError({'linked_order': 'Заявка привязана к другому клиенту, чем оплата.'})
        if ls is not None and client is not None and ls.client_id and ls.client_id != client.pk:
            raise serializers.ValidationError({'linked_sale': 'Продажа привязана к другому клиенту, чем оплата.'})
        lr = attrs.get('linked_return', getattr(self.instance, 'linked_return', None) if self.instance else None)
        ptype = attrs.get('payment_type')
        if ptype is None and self.instance is not None:
            ptype = self.instance.payment_type
        if ptype is None:
            ptype = Payment.TYPE_PAYMENT
        if ptype == Payment.TYPE_REFUND:
            mrr = (attrs.get('manual_refund_reason') or '').strip() or (self.instance and (self.instance.manual_refund_reason or '').strip() if self.instance else '')
            if lr is None and not mrr:
                raise serializers.ValidationError(
                    {'linked_return': 'Для refund укажите linked_return либо manual_refund_reason (ручной возврат).'},
                )
            if lr is not None and client is not None and lr.sale and lr.sale.client_id and lr.sale.client_id != client.pk:
                raise serializers.ValidationError({'linked_return': 'Возврат относится к другому клиенту.'})
        if self.instance is None and client and not client.is_active:
            raise serializers.ValidationError({'client': 'Клиент неактивен. Создание оплаты запрещено.'})
        return attrs

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

    def update(self, instance, validated_data):
        if instance.status == Payment.STATUS_CANCELED:
            raise serializers.ValidationError({'status': 'Отменённую оплату нельзя редактировать'})
        frozen = ('amount', 'client', 'linked_sale', 'linked_order', 'linked_return', 'payment_type')
        for key in frozen:
            if key in validated_data:
                raise serializers.ValidationError({
                    key: (
                        'После создания это поле нельзя менять; '
                        'отмена записи — только POST /api/payments/{id}/cancel/'
                    ),
                })
        return super().update(instance, validated_data)


# ─────────────────────────────────────────────────────────────────────────────
# SALE (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

class SaleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleLine
        fields = (
            'id', 'product', 'warehouse_batch', 'order_line',
            'stock_form', 'piece_pick', 'quantity', 'unit_price', 'line_total',
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
    payment_status = serializers.SerializerMethodField()
    paid_amount = serializers.SerializerMethodField()
    debt_amount = serializers.SerializerMethodField()
    refund_amount = serializers.SerializerMethodField()
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
            'warehouse_stock_applied', 'credit_limit_bypassed', 'updated_at',
            'created_by', 'created_by_name', 'created_at',
            'sale_lines', 'payment_status', 'paid_amount', 'debt_amount', 'refund_amount',
        )
        read_only_fields = (
            'profit', 'revenue', 'cost', 'total_meters', 'inventory_form',
            'warehouse_batch_id', 'profile_name', 'stock_quality',
            'created_at', 'sale_lines',
            'warehouse_stock_applied', 'credit_limit_bypassed', 'updated_at',
            'payment_status', 'paid_amount', 'debt_amount', 'refund_amount',
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

    def get_payment_status(self, obj):
        from .payment_status import sale_payment_metrics
        return sale_payment_metrics(obj)['payment_status']

    def get_paid_amount(self, obj):
        from .payment_status import sale_payment_metrics
        return api_decimal_str(sale_payment_metrics(obj)['paid_amount'])

    def get_debt_amount(self, obj):
        from .payment_status import sale_payment_metrics
        return api_decimal_str(sale_payment_metrics(obj)['debt_amount'])

    def get_refund_amount(self, obj):
        from .payment_status import sale_payment_metrics
        return api_decimal_str(sale_payment_metrics(obj)['refund_amount'])

    def to_internal_value(self, data):
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

        is_def = attrs.get('is_defect_sale', self.instance.is_defect_sale if self.instance else False)
        owb = attrs.get('warehouse_batch') or (self.instance.warehouse_batch if self.instance else None)
        if owb and owb.quality == WarehouseBatch.QUALITY_DEFECT and not is_def:
            raise serializers.ValidationError(
                {'warehouse_batch': 'Обычная продажа не может выбирать партию с качеством «брак».'},
            )

        if self.instance is None and attrs.get('client') and not attrs['client'].is_active:
            raise serializers.ValidationError({'client': 'Клиент неактивен. Создание продажи запрещено.'})

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

    def _build_legacy_sale_line(self, instance: Sale) -> None:
        if instance.sale_lines.exists():
            return
        if not (instance.warehouse_batch_id or (instance.product or '').strip()):
            return
        lt = ((instance.price or Decimal('0')) * (instance.quantity or Decimal('0'))).quantize(Decimal('0.01'))
        SaleLine.objects.create(
            sale=instance,
            product=instance.product,
            warehouse_batch_id=instance.warehouse_batch_id,
            stock_form=instance.stock_form or '',
            piece_pick=instance.piece_pick or '',
            quantity=instance.quantity,
            unit_price=instance.price,
            line_total=lt,
            cost=instance.cost or 0,
            profit=instance.profit or 0,
        )

    @staticmethod
    def _is_shipping_status(status: str) -> bool:
        return status in (
            Sale.STATUS_PARTIALLY_SHIPPED,
            Sale.STATUS_SHIPPED,
            Sale.STATUS_CLOSED,
        )

    def create(self, validated_data):
        validated_data = self._fill_quantity_input(validated_data)
        self._apply_finance(validated_data)

        ss = validated_data.get('sale_status', Sale.STATUS_DRAFT)
        shipping = self._is_shipping_status(ss)
        client = validated_data.get('client')
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        force_override = str((self.initial_data or {}).get('force_credit_override', '')).lower() in (
            '1', 'true', 'yes',
        )
        if force_override and shipping:
            validated_data['credit_limit_bypassed'] = True

        if client is not None and shipping:
            from .credit_check import enforce_credit_limit, CreditLimitBlocked
            revenue = validated_data.get('revenue') or Decimal('0')
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
        linked_order = validated_data.get('linked_order')
        lines_payload = (self.initial_data or {}).get('sale_lines')
        from .sale_warehouse import apply_warehouse_for_sale
        from .reservations import auto_fulfill_sale_lines_after_shipping
        from .state_machine import validate_sale_ship
        with transaction.atomic():
            instance = super().create(validated_data)
            if lines_payload and isinstance(lines_payload, list) and len(lines_payload) > 0:
                allowed = {
                    'order_line', 'product', 'warehouse_batch', 'stock_form', 'piece_pick',
                    'quantity', 'unit_price', 'defect_flag', 'comment',
                }
                for row in lines_payload:
                    extra = set(row.keys()) - allowed
                    if extra:
                        raise serializers.ValidationError(
                            {'sale_lines': f'Недопустимые поля в строке: {", ".join(sorted(extra))}'},
                        )
                    sld = {k: v for k, v in row.items() if k in allowed}
                    up = Decimal(str(sld.get('unit_price') or 0))
                    qn = Decimal(str(sld.get('quantity') or 0))
                    sld['line_total'] = (up * qn).quantize(Decimal('0.01'))
                    sld['cost'] = 0
                    sld['profit'] = 0
                    sld['sale'] = instance
                    SaleLine.objects.create(**sld)
            else:
                self._build_legacy_sale_line(instance)
            instance = Sale.objects.select_for_update().get(pk=instance.pk)
            if not instance.sale_lines.exists():
                raise serializers.ValidationError(
                    {'sale_lines': 'Должна быть минимум одна строка продажи (sale_lines или данные «шапки»)'},
                )
            if shipping:
                try:
                    validate_sale_ship(instance)
                except ValueError as e:
                    raise serializers.ValidationError({'non_field_errors': [str(e)]})
                try:
                    apply_warehouse_for_sale(instance)
                except (ValueError, DrfValidationError) as e:
                    msg = getattr(e, 'detail', e) if isinstance(e, DrfValidationError) else str(e)
                    raise serializers.ValidationError({'non_field_errors': [str(msg)]})
            if shipping and linked_order is not None:
                auto_fulfill_sale_lines_after_shipping(
                    sale=instance,
                    order=linked_order,
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

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        from .sale_warehouse import apply_warehouse_for_sale
        from .reservations import auto_fulfill_sale_lines_after_shipping
        from .state_machine import validate_sale_ship
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if not instance.sale_lines.exists() and (instance.warehouse_batch_id or (instance.product or '').strip()):
                self._build_legacy_sale_line(instance)
            if self._is_shipping_status(instance.sale_status):
                try:
                    validate_sale_ship(instance)
                except ValueError as e:
                    raise serializers.ValidationError({'non_field_errors': [str(e)]})
                try:
                    apply_warehouse_for_sale(instance)
                except (ValueError, DrfValidationError) as e:
                    msg = getattr(e, 'detail', e) if isinstance(e, DrfValidationError) else str(e)
                    raise serializers.ValidationError({'non_field_errors': [str(msg)]})
                if instance.linked_order_id:
                    auto_fulfill_sale_lines_after_shipping(
                        sale=instance,
                        order=instance.linked_order,
                        user=user,
                        request=request,
                    )
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# RETURN (Возврат)
# ─────────────────────────────────────────────────────────────────────────────

class ReturnLineSerializer(serializers.ModelSerializer):
    sale_line_label = serializers.SerializerMethodField()
    sale_line_sale_id = serializers.IntegerField(source='sale_line.sale_id', read_only=True)

    class Meta:
        model = ReturnLine
        fields = (
            'id', 'sale_line', 'product', 'quantity',
            'return_target', 'condition_type', 'comment',
            'sale_line_label', 'sale_line_sale_id',
        )
        extra_kwargs = {
            'sale_line': {'required': True, 'allow_null': False},
            'product': {'read_only': True},
        }

    def validate(self, attrs):
        if self.instance and self.instance.return_doc.status == Return.STATUS_COMPLETED:
            raise serializers.ValidationError(
                'Строку проведённого возврата нельзя изменять',
            )
        sale_line = attrs.get('sale_line')
        if sale_line is None:
            raise serializers.ValidationError({'sale_line': 'Поле sale_line обязательно'})
        if not self.instance:
            qty = attrs.get('quantity', Decimal('0'))
            total_returned = sum(
                rl.quantity
                for rl in ReturnLine.objects.filter(sale_line=sale_line).exclude(
                    return_doc__status=Return.STATUS_CANCELED,
                )
            )
            if total_returned + qty > sale_line.quantity:
                raise serializers.ValidationError({
                    'quantity': (
                        f'Нельзя вернуть больше, чем было отгружено по строке '
                        f'(отгружено: {sale_line.quantity}, уже возвращено: {total_returned})'
                    )
                })
        return attrs

    def get_sale_line_label(self, obj):
        sl = getattr(obj, 'sale_line', None)
        if sl is None:
            return ''
        return f'{sl.product} × {api_decimal_str(sl.quantity)}'


class ReturnSerializer(serializers.ModelSerializer):
    lines = ReturnLineSerializer(many=True, required=False)
    sale_order_number = serializers.CharField(source='sale.order_number', read_only=True)
    client_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = Return
        fields = (
            'id', 'return_number', 'date', 'status', 'sale', 'sale_order_number',
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

    def validate(self, attrs):
        if not self.instance:
            lines = (self.initial_data or {}).get('lines')
            if not lines or not isinstance(lines, list) or len(lines) < 1:
                raise serializers.ValidationError(
                    {'lines': 'Нужна минимум одна строка возврата (sale_line)'},
                )
            st = (self.initial_data or {}).get('status')
            if st == Return.STATUS_COMPLETED:
                raise serializers.ValidationError({
                    'status': 'Создайте возврат как черновик (draft), затем POST /api/returns/{id}/complete/',
                })
        return attrs

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
        validated_data.pop('status', None)
        validated_data['status'] = Return.STATUS_DRAFT

        with transaction.atomic():
            ret_doc = super().create(validated_data)
            for line_data in lines_data:
                sale_line = line_data.get('sale_line')
                if sale_line is not None:
                    line_data['product'] = sale_line.product
                ReturnLine.objects.create(return_doc=ret_doc, **line_data)
        return ret_doc

    def update(self, instance, validated_data):
        if instance.status == Return.STATUS_CANCELED:
            raise serializers.ValidationError({'status': 'Отменённый возврат нельзя редактировать'})
        if instance.status == Return.STATUS_COMPLETED:
            allowed = {'comment', 'return_reason', 'invoice_number'}
            initial = self.initial_data or {}
            for key in initial:
                if key not in allowed:
                    raise serializers.ValidationError({
                        key: 'У проведённого возврата можно менять только comment, return_reason, invoice_number',
                    })
            validated_data = {k: v for k, v in validated_data.items() if k in allowed}
            return super().update(instance, validated_data)
        return super().update(instance, validated_data)

    def apply_completion_effects(self, ret_doc: Return) -> None:
        """Склад / брак / переделка — только при проведении возврата (после complete)."""
        for line in ret_doc.lines.all().select_related('sale_line', 'sale_line__warehouse_batch'):
            self._process_return_line(line, ret_doc)

    def _process_return_line(self, line: ReturnLine, ret_doc: Return):
        """Обрабатываем возврат — возврат на склад, в брак или на переделку."""
        from apps.warehouse.models import WarehouseBatch
        from apps.warehouse.packaging import q4

        if line.return_target == ReturnLine.TARGET_WAREHOUSE:
            wb = None
            if line.sale_line_id and line.sale_line.warehouse_batch_id:
                wb = line.sale_line.warehouse_batch
            else:
                wb = ret_doc.sale.warehouse_batch
            if wb:
                wb.quantity = q4(wb.quantity + line.quantity)
                if wb.status == WarehouseBatch.STATUS_SHIPPED:
                    wb.status = WarehouseBatch.STATUS_AVAILABLE
                wb.save(update_fields=['quantity', 'status'])

        elif line.return_target == ReturnLine.TARGET_DEFECT:
            # Создаём запись брака
            product = line.sale_line.product
            qp = Decimal(str(line.quantity))
            DefectRecord.objects.create(
                source_type=DefectRecord.SOURCE_RETURN,
                source_id=line.id,
                product=product,
                original_quantity_pcs=qp,
                quantity_pcs=qp,
                defect_reason=ret_doc.return_reason or '',
                status=DefectRecord.STATUS_ON_STOCK,
                comment=line.comment or '',
            )

        elif line.return_target == ReturnLine.TARGET_REWORK:
            # Создаём заявку на переделку (вся строка возврата сразу уходит в переделку)
            product = line.sale_line.product
            qp = Decimal(str(line.quantity))
            defect = DefectRecord.objects.create(
                source_type=DefectRecord.SOURCE_RETURN,
                source_id=line.id,
                product=product,
                original_quantity_pcs=qp,
                quantity_pcs=Decimal('0'),
                sent_to_rework_quantity_pcs=qp,
                defect_reason=ret_doc.return_reason or '',
                status=DefectRecord.STATUS_SENT_TO_REWORK,
                comment=line.comment or '',
            )
            ReworkRequest.objects.create(
                return_doc=ret_doc,
                defect_record=defect,
                original_sale=ret_doc.sale,
                product=product,
                quantity_pcs=qp,
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
    source_label = serializers.SerializerMethodField()
    available_quantity_pcs = serializers.SerializerMethodField()
    display_quantity_label = serializers.SerializerMethodField()

    class Meta:
        model = DefectRecord
        fields = (
            'id', 'source_type', 'source_id', 'warehouse_batch',
            'profile', 'profile_name', 'product',
            'original_quantity_pcs', 'quantity_pcs', 'available_quantity_pcs',
            'sold_quantity_pcs', 'written_off_quantity_pcs', 'sent_to_rework_quantity_pcs',
            'quantity_kg', 'kg_coefficient',
            'defect_reason', 'status', 'writeoff_reason',
            'source_label', 'display_quantity_label',
            'comment', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'created_at', 'updated_at',
            'original_quantity_pcs', 'sold_quantity_pcs', 'written_off_quantity_pcs',
            'sent_to_rework_quantity_pcs', 'available_quantity_pcs', 'display_quantity_label',
        )
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
        if self.instance is None:
            source_type = attrs.get('source_type', DefectRecord.SOURCE_OTK)
            source_id = attrs.get('source_id')
            if source_type == DefectRecord.SOURCE_MANUAL:
                if not (attrs.get('defect_reason') or '').strip():
                    raise serializers.ValidationError({'defect_reason': 'Для ручного брака укажите причину'})
                if not (attrs.get('product') or '').strip():
                    raise serializers.ValidationError({'product': 'Для ручного брака укажите продукт'})
                if not attrs.get('quantity_pcs') or attrs.get('quantity_pcs') <= 0:
                    raise serializers.ValidationError({'quantity_pcs': 'Для ручного брака укажите количество > 0'})
            elif source_type in (DefectRecord.SOURCE_WAREHOUSE, DefectRecord.SOURCE_QC):
                if not attrs.get('warehouse_batch') and source_id is None:
                    raise serializers.ValidationError(
                        {'warehouse_batch': 'Укажите warehouse_batch или source_id (ID партии/ОТК)'}
                    )
            elif source_type == DefectRecord.SOURCE_RETURN:
                if source_id is None or not ReturnLine.objects.filter(pk=source_id).exists():
                    raise serializers.ValidationError({'source_id': 'ReturnLine с указанным source_id не найден'})
            else:
                if source_id is None:
                    raise serializers.ValidationError({'source_id': 'Поле source_id обязательно при создании (ОТК)'})
        return attrs

    def create(self, validated_data):
        source_type = validated_data.get('source_type', DefectRecord.SOURCE_OTK)
        source_id = validated_data.get('source_id')
        if source_type == DefectRecord.SOURCE_RETURN and source_id is not None:
            rl = ReturnLine.objects.select_related('sale_line').filter(pk=source_id).first()
            if rl is not None:
                validated_data['product'] = rl.sale_line.product
                validated_data['quantity_pcs'] = rl.quantity
        qp = validated_data.get('quantity_pcs')
        if qp is not None and Decimal(str(qp or 0)) > 0:
            if not validated_data.get('original_quantity_pcs'):
                validated_data['original_quantity_pcs'] = qp
        return super().create(validated_data)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        for k in (
            'quantity_pcs', 'quantity_kg', 'kg_coefficient',
            'original_quantity_pcs', 'sold_quantity_pcs', 'written_off_quantity_pcs',
            'sent_to_rework_quantity_pcs',
        ):
            if ret.get(k) is not None:
                ret[k] = api_decimal_str(Decimal(str(ret[k])))
        return ret

    def get_available_quantity_pcs(self, obj):
        v = Decimal(str(obj.quantity_pcs or 0))
        return api_decimal_str(v) if v > 0 else api_decimal_str(Decimal('0'))

    def get_display_quantity_label(self, obj):
        v = Decimal(str(obj.quantity_pcs or 0))
        if v > 0:
            return f'{api_decimal_str(v)} шт'
        kg = obj.quantity_kg
        if kg is not None and Decimal(str(kg)) > 0:
            return f'{api_decimal_str(Decimal(str(kg)))} кг'
        return '0 шт'

    def get_source_label(self, obj):
        if obj.warehouse_batch_id:
            return f'Складская партия #{obj.warehouse_batch_id}'
        if obj.source_type == DefectRecord.SOURCE_RETURN and obj.source_id:
            rl = ReturnLine.objects.filter(pk=obj.source_id).select_related('return_doc').first()
            if rl is not None:
                return f'ReturnLine #{rl.pk} / Return #{rl.return_doc_id}'
        if obj.source_type in (DefectRecord.SOURCE_OTK, DefectRecord.SOURCE_QC) and obj.source_id:
            return f'ОТК/источник #{obj.source_id}'
        return ''


# ─────────────────────────────────────────────────────────────────────────────
# REWORK REQUEST (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

def defect_record_source_label(dr: DefectRecord) -> str:
    if dr.warehouse_batch_id:
        return f'Складская партия #{dr.warehouse_batch_id}'
    if dr.source_type == DefectRecord.SOURCE_RETURN and dr.source_id:
        rl = ReturnLine.objects.filter(pk=dr.source_id).select_related('return_doc').first()
        if rl is not None:
            return f'ReturnLine #{rl.pk} / Return #{rl.return_doc_id}'
    if dr.source_type in (DefectRecord.SOURCE_OTK, DefectRecord.SOURCE_QC) and dr.source_id:
        return f'ОТК/источник #{dr.source_id}'
    return ''


def rework_quantities_from_defect_record(
    defect: DefectRecord,
    pcs_to_send: Decimal | None = None,
) -> dict:
    """
    quantity_pcs / quantity_kg для новой ReworkRequest по остатку DefectRecord.
    pcs_to_send: если задано — не больше остатка quantity_pcs; иначе весь остаток.
    """
    remaining_pcs = Decimal(str(defect.quantity_pcs or 0))
    kg_raw = defect.quantity_kg
    kg_rem = Decimal(str(kg_raw)) if kg_raw is not None else Decimal('0')
    if remaining_pcs > 0:
        limit = remaining_pcs if pcs_to_send is None else min(Decimal(str(pcs_to_send)), remaining_pcs)
        if limit <= 0:
            raise ValueError('Количество для переделки должно быть > 0 и не больше остатка по браку')
        kg_part = Decimal('0')
        if kg_rem > 0 and remaining_pcs > 0:
            kg_part = (limit / remaining_pcs * kg_rem).quantize(Decimal('0.0001'))
        return {
            'quantity_pcs': limit,
            'quantity_kg': kg_part,
        }
    if kg_rem > 0:
        limit_kg = kg_rem if pcs_to_send is None else min(Decimal(str(pcs_to_send)), kg_rem)
        if limit_kg <= 0:
            raise ValueError('Количество для переделки должно быть > 0')
        return {
            'quantity_pcs': None,
            'quantity_kg': limit_kg,
        }
    raise ValueError('У записи брака нет положительного количества (шт или кг) для переделки')


class ReworkRequestSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    rework_loss_kg = serializers.SerializerMethodField()
    recovered_output = serializers.SerializerMethodField()
    return_doc_number = serializers.CharField(source='return_doc.return_number', read_only=True, default='')
    defect_status = serializers.CharField(source='defect_record.status', read_only=True, default='')
    original_sale_number = serializers.CharField(source='original_sale.order_number', read_only=True, default='')
    result_warehouse_batch_label = serializers.SerializerMethodField()
    defect_record_id = serializers.IntegerField(read_only=True, allow_null=True)
    defect_product_name = serializers.SerializerMethodField()
    defect_quantity_pcs = serializers.SerializerMethodField()
    defect_quantity_kg = serializers.SerializerMethodField()
    defect_reason = serializers.SerializerMethodField()
    defect_source_type = serializers.SerializerMethodField()
    defect_source_label = serializers.SerializerMethodField()
    display_quantity = serializers.SerializerMethodField()
    display_quantity_label = serializers.SerializerMethodField()

    class Meta:
        model = ReworkRequest
        fields = (
            'id', 'rework_number', 'return_doc', 'defect_record', 'defect_record_id', 'original_sale',
            'return_doc_number', 'defect_status', 'original_sale_number',
            'defect_product_name', 'defect_quantity_pcs', 'defect_quantity_kg',
            'defect_reason', 'defect_source_type', 'defect_source_label',
            'display_quantity', 'display_quantity_label',
            'product', 'quantity_pcs', 'quantity_kg', 'output_quantity_kg', 'loss_kg', 'conversion_rate',
            'status', 'result_warehouse_batch',
            'result_warehouse_batch_label',
            'rework_loss_kg', 'recovered_output',
            'comment', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'rework_number', 'created_at', 'updated_at', 'rework_loss_kg', 'recovered_output',
            'defect_record_id', 'defect_product_name', 'defect_quantity_pcs', 'defect_quantity_kg',
            'defect_reason', 'defect_source_type', 'defect_source_label',
            'display_quantity', 'display_quantity_label',
        )
        extra_kwargs = {
            'return_doc': {'required': False, 'allow_null': True},
            'defect_record': {'required': False, 'allow_null': True},
            'original_sale': {'required': False, 'allow_null': True},
            'result_warehouse_batch': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
            'quantity_pcs': {'required': False, 'allow_null': True},
            'output_quantity_kg': {'required': False, 'allow_null': True},
            'loss_kg': {'required': False, 'allow_null': True},
            'conversion_rate': {'required': False, 'allow_null': True},
        }

    def get_defect_product_name(self, obj):
        if obj.defect_record_id:
            return (obj.defect_record.product or '').strip()
        return ''

    def _defect_qty_pcs_str(self, obj):
        if not obj.defect_record_id:
            return None
        v = obj.defect_record.quantity_pcs
        if v is None or Decimal(str(v)) == 0:
            return None
        return api_decimal_str(Decimal(str(v)))

    def _defect_qty_kg_str(self, obj):
        if not obj.defect_record_id:
            return None
        v = obj.defect_record.quantity_kg
        if v is None or Decimal(str(v)) == 0:
            return None
        return api_decimal_str(Decimal(str(v)))

    def get_defect_quantity_pcs(self, obj):
        return self._defect_qty_pcs_str(obj)

    def get_defect_quantity_kg(self, obj):
        return self._defect_qty_kg_str(obj)

    def get_defect_reason(self, obj):
        if obj.defect_record_id:
            return (obj.defect_record.defect_reason or '').strip()
        return ''

    def get_defect_source_type(self, obj):
        if obj.defect_record_id:
            return obj.defect_record.source_type
        return ''

    def get_defect_source_label(self, obj):
        if obj.defect_record_id:
            return defect_record_source_label(obj.defect_record)
        return ''

    def get_display_quantity(self, obj):
        if obj.quantity_pcs is not None and Decimal(str(obj.quantity_pcs)) > 0:
            return api_decimal_str(Decimal(str(obj.quantity_pcs)))
        if obj.quantity_kg is not None and Decimal(str(obj.quantity_kg)) > 0:
            return api_decimal_str(Decimal(str(obj.quantity_kg)))
        return None

    def get_display_quantity_label(self, obj):
        if obj.quantity_pcs is not None and Decimal(str(obj.quantity_pcs)) > 0:
            return f'{api_decimal_str(Decimal(str(obj.quantity_pcs)))} шт'
        if obj.quantity_kg is not None and Decimal(str(obj.quantity_kg)) > 0:
            return f'{api_decimal_str(Decimal(str(obj.quantity_kg)))} кг'
        return None

    def get_rework_loss_kg(self, obj):
        v = obj.rework_loss_kg
        return api_decimal_str(v) if v is not None else None

    def get_recovered_output(self, obj):
        v = obj.recovered_output
        return api_decimal_str(Decimal(str(v))) if v is not None else None

    def get_result_warehouse_batch_label(self, obj):
        wb = getattr(obj, 'result_warehouse_batch', None)
        if wb is None:
            return ''
        return f'#{wb.pk} {wb.product}'

    def create(self, validated_data):
        if validated_data.get('defect_record') is None:
            raise serializers.ValidationError({'defect_record': 'Поле defect_record обязательно'})
        defect = validated_data['defect_record']
        if not (validated_data.get('product') or '').strip():
            validated_data['product'] = defect.product
        try:
            qmap = rework_quantities_from_defect_record(defect)
        except ValueError as e:
            raise serializers.ValidationError({'defect_record': str(e)})
        validated_data['quantity_pcs'] = qmap['quantity_pcs']
        validated_data['quantity_kg'] = qmap['quantity_kg']
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
        for k in ('quantity_pcs', 'quantity_kg', 'output_quantity_kg', 'loss_kg', 'conversion_rate'):
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
