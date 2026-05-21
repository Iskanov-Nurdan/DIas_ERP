import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DrfValidationError

from config.api_numbers import api_decimal_str
from config.fields import CleanDecimalField
from apps.recipes.models import PlasticProfile, Recipe
from apps.warehouse.models import GpPackUnit, WarehouseBatch
from apps.warehouse.packaging import q4
from apps.warehouse.stock_ops import (
    PIECE_FROM_SEALED,
    PIECE_FROM_OPEN,
    PIECE_LOOSE,
    normalize_inventory_form,
    normalize_piece_pick,
)
from .defect_service import create_defect_split_from_good_batch
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
    phone_extra = serializers.CharField(
        source='phone_alt', required=False, allow_blank=True, max_length=255,
    )

    class Meta:
        model = Client
        fields = (
            'id', 'name', 'contact', 'phone', 'phone_alt', 'phone_extra',
            'settlement_account', 'inn', 'address', 'email', 'messenger',
            'client_type', 'is_active', 'status',
            'sales_count', 'sales_total', 'has_sales',
            'credit_limit', 'credit_limit_mode',
        )
        extra_kwargs = {
            'name': {'required': True, 'allow_blank': False},
            'client_type': {'required': False},
            'phone': {'required': False, 'allow_blank': True},
            'phone_alt': {'required': False, 'allow_blank': True},
            'settlement_account': {'required': False, 'allow_blank': True},
            'inn': {'required': False, 'allow_blank': True},
            'address': {'required': False, 'allow_blank': True},
        }

    def get_status(self, obj):
        return 'active' if obj.is_active else 'inactive'

    def get_has_sales(self, obj):
        if hasattr(obj, 'sales_count'):
            return int(obj.sales_count or 0) > 0
        return obj.sales.exists()

    def to_internal_value(self, data):
        if hasattr(data, 'get'):
            d = data.copy() if isinstance(data, dict) else dict(data)
            st = d.pop('status', None)
            if st is not None and str(st).strip() != '':
                st = str(st).strip()
                if st == 'active':
                    d['is_active'] = True
                elif st == 'inactive':
                    d['is_active'] = False
                else:
                    raise serializers.ValidationError({'status': 'status должен быть active или inactive'})
            return super().to_internal_value(d)
        return super().to_internal_value(data)

    def validate_client_type(self, value):
        if value in (None, ''):
            return Client.TYPE_INDIVIDUAL
        if value not in (Client.TYPE_INDIVIDUAL, Client.TYPE_COMPANY):
            raise serializers.ValidationError('client_type должен быть individual или company')
        return value

    def validate(self, attrs):
        client_type = attrs.get(
            'client_type',
            self.instance.client_type if self.instance is not None else Client.TYPE_INDIVIDUAL,
        )
        if client_type == Client.TYPE_INDIVIDUAL:
            attrs['settlement_account'] = ''
            attrs['inn'] = ''
            attrs['address'] = ''
        return attrs

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
    PAYMENT_FULL = 'full'
    PAYMENT_PARTIAL = 'partial'
    PAYMENT_DEBT = 'debt'
    PAYMENT_KIND_CHOICES = (PAYMENT_FULL, PAYMENT_PARTIAL, PAYMENT_DEBT)
    PAYMENT_METHOD_CHOICES = ('cash', 'card', 'transfer')

    lines = OrderLineSerializer(many=True, required=False)
    order_lines = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True,
    )
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
    # Производство (заявка): рецепт и проверка только с сервера
    request_status = serializers.CharField(
        read_only=True, allow_null=True, required=False, default=None,
    )
    profile = serializers.PrimaryKeyRelatedField(
        queryset=PlasticProfile.objects.all(),
        source='production_profile',
        required=False,
        allow_null=True,
    )
    length = CleanDecimalField(
        max_digits=14, decimal_places=4, source='production_length', required=False, allow_null=True, coerce_to_string=True,
    )
    quantity = serializers.IntegerField(
        source='production_quantity', required=False, allow_null=True,
    )
    recipe = serializers.PrimaryKeyRelatedField(
        queryset=Recipe.objects.filter(is_active=True),
        source='resolved_recipe',
        required=False,
        allow_null=True,
    )
    recipe_id = serializers.IntegerField(source='resolved_recipe_id', read_only=True)
    recipe_name = serializers.SerializerMethodField()
    payment_type = serializers.CharField(write_only=True, required=False, allow_blank=False)
    payment_method = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    total_meters = serializers.SerializerMethodField()
    resource_check = serializers.SerializerMethodField()
    payment_type_label = serializers.SerializerMethodField()
    payment_method_label = serializers.SerializerMethodField()
    prepayment_amount = serializers.SerializerMethodField()
    advance_amount = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id', 'order_number', 'date', 'client', 'client_name',
            'source_type', 'comment', 'status',
            'created_by', 'created_by_name',
            'responsible_user', 'responsible_user_name',
            'created_at', 'updated_at',
            'lines', 'order_lines',
            'total_amount', 'shipped_amount', 'remaining_amount',
            'paid_amount', 'payment_status', 'debt_amount', 'refund_amount',
            'payment_type', 'payment_type_label', 'payment_method', 'payment_method_label',
            'prepayment_amount', 'advance_amount',
            'has_company_debt_by_goods',
            'request_status', 'profile', 'length', 'quantity', 'recipe', 'recipe_id', 'recipe_name', 'total_meters', 'resource_check',
        )
        read_only_fields = (
            'order_number', 'created_at', 'updated_at',
            'total_amount', 'shipped_amount', 'remaining_amount',
            'paid_amount', 'payment_status', 'debt_amount', 'refund_amount',
            'payment_type_label', 'payment_method_label',
            'prepayment_amount', 'advance_amount',
            'has_company_debt_by_goods',
            'request_status', 'recipe_id', 'recipe_name', 'total_meters', 'resource_check',
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

    def _order_payment_payload(self, obj) -> tuple[str, str | None]:
        from .payment_status import order_payment_metrics

        metrics = order_payment_metrics(obj)
        paid = Decimal(str(metrics['paid_amount'] or 0)).quantize(Decimal('0.01'))
        debt = Decimal(str(metrics['debt_amount'] or 0)).quantize(Decimal('0.01'))
        if debt == 0:
            ptype = self.PAYMENT_FULL
        elif paid > 0:
            ptype = self.PAYMENT_PARTIAL
        else:
            ptype = self.PAYMENT_DEBT
        latest = (
            obj.payments.filter(
                status=Payment.STATUS_ACTIVE,
                payment_type__in=(Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE),
            )
            .order_by('-id')
            .first()
        )
        if latest is None:
            return ptype, None
        pmethod = (
            'cash' if latest.payment_method == Payment.METHOD_CASH
            else ('card' if latest.payment_method == Payment.METHOD_CARD
            else ('transfer' if latest.payment_method == Payment.METHOD_TRANSFER else None))
        )
        return ptype, pmethod

    def get_payment_type_label(self, obj):
        ptype, _ = self._order_payment_payload(obj)
        return {'full': 'Полная оплата', 'partial': 'Частичная оплата', 'debt': 'В долг'}[ptype]

    def get_payment_method_label(self, obj):
        _, pmethod = self._order_payment_payload(obj)
        return {'cash': 'Наличные', 'card': 'Карта', 'transfer': 'Перевод'}.get(pmethod, '')

    def get_recipe_name(self, obj):
        r = obj.resolved_recipe
        if r is None:
            return None
        return (r.recipe or '').strip() or (r.product or '')[:255]

    def get_recipe(self, obj):
        return obj.resolved_recipe_id

    def get_prepayment_amount(self, obj):
        return self.get_paid_amount(obj)

    def get_advance_amount(self, obj):
        return self.get_paid_amount(obj)

    def get_total_meters(self, obj):
        if obj.request_total_meters is not None:
            return api_decimal_str(Decimal(str(obj.request_total_meters)))
        if obj.production_length is not None and obj.production_quantity is not None:
            return api_decimal_str(
                Decimal(str(obj.production_length)) * Decimal(int(obj.production_quantity)),
            )
        return None

    def get_resource_check(self, obj):
        snap = obj.resource_check_snapshot
        if snap is None:
            return None
        if isinstance(snap, dict) and not snap:
            return None
        return snap

    @staticmethod
    def allowed_blank_ids_for_profile(profile_id: int | None) -> list[int]:
        from django.db.models import Q

        from apps.workshop.models import WorkshopBlank

        q = Q(plastic_profile_id__isnull=True)
        if profile_id is not None:
            q |= Q(plastic_profile_id=profile_id)
        return list(
            WorkshopBlank.objects.filter(is_active=True)
            .filter(q)
            .order_by('id')
            .values_list('id', flat=True)[:400],
        )

    @classmethod
    def build_order_payment_read_fields(cls, order: Order) -> dict:
        """Поля оплаты заявки для list/detail/select-sources (как при создании в кассе)."""
        from .payment_status import order_payment_metrics

        metrics = order_payment_metrics(order)
        total = Decimal(str(metrics['total_due'] or 0)).quantize(Decimal('0.01'))
        paid = Decimal(str(metrics['paid_amount'] or 0)).quantize(Decimal('0.01'))
        remaining = Decimal(str(metrics['debt_amount'] or 0)).quantize(Decimal('0.01'))
        ptype, pmethod = cls()._order_payment_payload(order)
        return {
            'payment_type': ptype,
            'payment_method': pmethod,
            'total_amount': api_decimal_str(total),
            'paid_amount': api_decimal_str(paid),
            'amount_remaining': api_decimal_str(remaining),
            'prepayment_amount': api_decimal_str(paid),
            'advance_amount': api_decimal_str(paid),
            'debt_amount': api_decimal_str(remaining),
        }

    @classmethod
    def build_order_lines_read_payload(
        cls,
        order: Order,
        *,
        include_line_allowed_blanks: bool = False,
        order_level_blank_fallback: list[int] | None = None,
    ) -> tuple[list[dict], Decimal, Decimal]:
        """Все строки заявки для GET list/detail (и алиасов lines/items/…)."""
        order_lines_payload: list[dict] = []
        if order_level_blank_fallback is None and include_line_allowed_blanks:
            profile_ids: set[int] = set()
            if order.production_profile_id:
                profile_ids.add(order.production_profile_id)
            for line in order.lines.all():
                if line.profile_id:
                    profile_ids.add(line.profile_id)
            fallback_set: set[int] = set()
            for pid in profile_ids:
                fallback_set.update(cls.allowed_blank_ids_for_profile(pid))
            order_level_blank_fallback = sorted(fallback_set)
        total_qty = Decimal('0')
        total_m = Decimal('0')
        lines_qs = order.lines.all()
        if hasattr(lines_qs, 'all'):
            lines_iter = lines_qs.all()
        else:
            lines_iter = lines_qs
        for line in lines_iter:
            meta = cls._extract_order_line_meta(line.comment)
            length = meta.get('length')
            qty = Decimal(str(line.ordered_quantity or 0))
            total_qty += qty
            if length not in (None, ''):
                try:
                    total_m += (qty * Decimal(str(length)))
                except (InvalidOperation, TypeError, ValueError):
                    pass
            row = {
                'id': line.id,
                'profile': line.profile_id,
                'profile_id': line.profile_id,
                'profile_name': line.profile.name if line.profile_id else (line.product or ''),
                'recipe': meta.get('recipe_id'),
                'recipe_id': meta.get('recipe_id'),
                'recipe_name': meta.get('recipe_name'),
                'length': api_decimal_str(Decimal(str(length))) if length not in (None, '') else None,
                'quantity': api_decimal_str(qty),
                'unit_type': meta.get('unit_type') or Sale.MODE_PIECES,
                'ordered_quantity': api_decimal_str(qty),
                'shipped_quantity': api_decimal_str(Decimal(str(line.shipped_quantity or 0))),
                'remaining_quantity': api_decimal_str(line.remaining_quantity),
            }
            if include_line_allowed_blanks:
                line_blanks = cls.allowed_blank_ids_for_profile(line.profile_id)
                if not line_blanks and order_level_blank_fallback:
                    line_blanks = order_level_blank_fallback
                row['allowed_blank_ids'] = line_blanks
            order_lines_payload.append(row)
        return order_lines_payload, total_qty, total_m

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ptype, pmethod = self._order_payment_payload(instance)
        ret['payment_type'] = ptype
        ret['payment_method'] = pmethod
        ret['prepayment_amount'] = ret.get('paid_amount')
        ret['advance_amount'] = ret.get('paid_amount')
        order_lines_payload, total_qty, total_m = self.build_order_lines_read_payload(instance)
        ret['order_lines'] = order_lines_payload
        ret['lines_count'] = len(order_lines_payload)
        for alias in ('lines', 'items', 'request_lines', 'positions', 'products'):
            ret[alias] = order_lines_payload
        if instance.client_id is not None:
            ret['client_id'] = instance.client_id
        if order_lines_payload:
            first = order_lines_payload[0]
            if first.get('profile_id') is not None:
                ret.setdefault('profile_id', first['profile_id'])
            if first.get('quantity') is not None and ret.get('quantity') in (None, ''):
                ret['quantity'] = int(Decimal(str(first['quantity'])))
        ret['total_quantity'] = api_decimal_str(total_qty)
        ret['total_meters'] = api_decimal_str(total_m) if total_m > 0 else ret.get('total_meters')
        ret['status_label'] = instance.get_status_display()
        ret['request_status_label'] = (
            instance.get_request_status_display() if instance.request_status else None
        )
        ret.update(self.build_order_payment_read_fields(instance))
        return ret

    @staticmethod
    def _extract_order_line_meta(comment: str) -> dict:
        marker = '[line_meta]'
        raw = comment or ''
        pos = raw.find(marker)
        if pos < 0:
            return {}
        payload = raw[pos + len(marker):].strip()
        try:
            val = json.loads(payload)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _inject_order_line_meta(comment: str, *, recipe_id, recipe_name, length, unit_type) -> str:
        marker = '[line_meta]'
        base = (comment or '').strip()
        if marker in base:
            base = base.split(marker, 1)[0].strip()
        meta = {
            'recipe_id': recipe_id,
            'recipe_name': recipe_name,
            'length': str(length) if length is not None else None,
            'unit_type': unit_type or Sale.MODE_PIECES,
        }
        suffix = f'{marker}{json.dumps(meta, ensure_ascii=False)}'
        return f'{base}\n{suffix}'.strip()

    def _normalize_cart_order_lines(self, initial_data: dict) -> list[dict]:
        raw = initial_data.get('order_lines')
        if raw in (None, ''):
            raw = initial_data.get('lines')
        if (raw in (None, '')) and all(k in initial_data for k in ('profile', 'quantity')):
            raw = [
                {
                    'profile': initial_data.get('profile'),
                    'recipe': initial_data.get('recipe'),
                    'length': initial_data.get('length'),
                    'quantity': initial_data.get('quantity'),
                },
            ]
        if not isinstance(raw, list) or len(raw) < 1:
            self._raise_order_error('MISSING_ORDER_LINES', 'Поле order_lines обязательно и должно содержать строки.', field='order_lines')

        errors: list[dict] = []
        parsed: list[dict] = []
        for idx, row in enumerate(raw):
            row = row or {}
            profile_id = row.get('profile') or row.get('profile_id')
            recipe_id = row.get('recipe') or row.get('recipe_id')
            length_raw = row.get('length')
            qty_raw = row.get('quantity') or row.get('ordered_quantity')
            unit_type = (row.get('unit_type') or Sale.MODE_PIECES).strip().lower() if isinstance(row.get('unit_type', Sale.MODE_PIECES), str) else Sale.MODE_PIECES
            if unit_type not in (Sale.MODE_PIECES, Sale.MODE_PACKAGES):
                unit_type = Sale.MODE_PIECES
            line_errors: list[dict] = []
            if not profile_id:
                line_errors.append({'field': f'order_lines[{idx}].profile', 'message': 'Поле profile обязательно.'})
            if qty_raw in (None, ''):
                line_errors.append({'field': f'order_lines[{idx}].quantity', 'message': 'Поле quantity обязательно.'})
            length_d = None
            if length_raw not in (None, ''):
                try:
                    length_d = Decimal(str(length_raw))
                    if length_d <= 0:
                        raise InvalidOperation()
                except Exception:
                    line_errors.append({'field': f'order_lines[{idx}].length', 'message': 'length должно быть > 0.'})
            profile_obj = None
            recipe_obj = None
            qty_d = None
            if not line_errors:
                try:
                    profile_obj = PlasticProfile.objects.get(pk=profile_id)
                except PlasticProfile.DoesNotExist:
                    line_errors.append({'field': f'order_lines[{idx}].profile', 'message': 'Профиль не найден.'})
                if recipe_id:
                    try:
                        recipe_obj = Recipe.objects.get(pk=recipe_id, is_active=True)
                    except Recipe.DoesNotExist:
                        line_errors.append({'field': f'order_lines[{idx}].recipe', 'message': 'Рецепт не найден или неактивен.'})
                try:
                    qty_d = Decimal(str(qty_raw))
                    if qty_d <= 0:
                        raise InvalidOperation()
                except Exception:
                    line_errors.append({'field': f'order_lines[{idx}].quantity', 'message': 'quantity должно быть > 0.'})
                if profile_obj is not None and recipe_obj is not None and recipe_obj.profile_id != profile_obj.id:
                    line_errors.append({'field': f'order_lines[{idx}].recipe', 'message': 'recipe не относится к profile.'})
            if line_errors:
                errors.extend(line_errors)
                continue
            parsed.append(
                {
                    'row': row,
                    'profile_obj': profile_obj,
                    'recipe_obj_explicit': recipe_obj,
                    'recipe_id': recipe_id,
                    'length_d': length_d,
                    'qty_d': qty_d,
                    'unit_type': unit_type,
                },
            )

        if errors:
            raise serializers.ValidationError(
                {
                    'code': 'ORDER_LINES_VALIDATION_ERROR',
                    'detail': 'Ошибка в строках корзины заявки.',
                    'errors': errors,
                },
            )

        implicit_profile_ids: set[int] = set()
        for p in parsed:
            if not p['recipe_id'] and p['profile_obj'] is not None:
                implicit_profile_ids.add(p['profile_obj'].id)

        ambiguous_ids: list[int] = []
        implicit_map: dict[int, Recipe | None] = {}
        for pid in sorted(implicit_profile_ids):
            recipes_two = list(Recipe.objects.filter(profile_id=pid, is_active=True).order_by('id')[:2])
            if len(recipes_two) > 1:
                ambiguous_ids.append(pid)
            elif len(recipes_two) == 1:
                implicit_map[pid] = recipes_two[0]
            else:
                implicit_map[pid] = None

        if ambiguous_ids:
            ids_str = ', '.join(str(i) for i in ambiguous_ids)
            self._raise_order_error(
                'AMBIGUOUS_RECIPE_FOR_PROFILE',
                f'Несколько активных рецептов у профиля(ей): {ids_str}. Укажите recipe в строке заявки.',
                field='order_lines',
            )

        normalized: list[dict] = []
        for p in parsed:
            profile_obj = p['profile_obj']
            recipe_obj = p['recipe_obj_explicit']
            if recipe_obj is None and profile_obj is not None:
                recipe_obj = implicit_map.get(profile_obj.id)
            recipe_name = (
                (recipe_obj.recipe or '').strip() or (recipe_obj.product or '')
                if recipe_obj is not None
                else None
            )
            display_name = (profile_obj.name or '').strip() or (recipe_name or '') or f'#{profile_obj.id}'
            normalized.append(
                {
                    'product': display_name,
                    'profile': profile_obj,
                    'ordered_quantity': p['qty_d'],
                    'unit_price': Decimal('0'),
                    'comment': self._inject_order_line_meta(
                        p['row'].get('comment', ''),
                        recipe_id=recipe_obj.id if recipe_obj is not None else None,
                        recipe_name=recipe_name,
                        length=p['length_d'],
                        unit_type=p['unit_type'],
                    ),
                    '_recipe': recipe_obj,
                    '_length': p['length_d'],
                },
            )
        return normalized

    @staticmethod
    def _raise_order_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'message': message,
                'detail': message,
                'fields': [{'field': field, 'message': message}],
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

    @staticmethod
    def _is_production_payload(raw: dict) -> bool:
        if not raw:
            return False
        if raw.get('profile') in (None, ''):
            return False
        if raw.get('length') in (None, ''):
            return False
        if raw.get('quantity') in (None, ''):
            return False
        return True

    @staticmethod
    def _parse_total_amount_input(initial: dict) -> Decimal | None:
        if not hasattr(initial, 'get'):
            return None
        raw = initial.get('total_amount')
        if raw in (None, ''):
            return None
        try:
            return Decimal(str(raw)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError(
                {
                    'code': 'INVALID_TOTAL_AMOUNT',
                    'message': 'total_amount должен быть числом.',
                    'detail': 'total_amount должен быть числом.',
                    'fields': [{'field': 'total_amount', 'message': 'total_amount должен быть числом.'}],
                    'errors': [{'field': 'total_amount', 'message': 'total_amount должен быть числом.'}],
                },
            )

    @staticmethod
    def _apply_declared_total_to_normalized_lines(normalized: list[dict], declared_total: Decimal) -> None:
        """Распределяет сумму заявки по unit_price строк (должна совпадать с order.total_amount)."""
        declared_total = declared_total.quantize(Decimal('0.01'))
        total_qty = sum(Decimal(str(row['ordered_quantity'])) for row in normalized)
        if total_qty <= 0:
            return
        even = (declared_total / total_qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        acc_line_totals = Decimal('0')
        for i, row in enumerate(normalized):
            qty = Decimal(str(row['ordered_quantity']))
            if i < len(normalized) - 1:
                line_total = (even * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                acc_line_totals += line_total
                row['unit_price'] = (line_total / qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if qty else Decimal('0')
            else:
                line_total = (declared_total - acc_line_totals).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                row['unit_price'] = (line_total / qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if qty else Decimal('0')

    @staticmethod
    def _parse_paid_amount_input(initial: dict) -> Decimal | None:
        if not hasattr(initial, 'get'):
            return None
        raw = initial.get('paid_amount')
        if raw in (None, ''):
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError(
                {
                    'code': 'INVALID_PAID_AMOUNT',
                    'message': 'paid_amount должен быть числом.',
                    'detail': 'paid_amount должен быть числом.',
                    'fields': [{'field': 'paid_amount', 'message': 'paid_amount должен быть числом.'}],
                    'errors': [{'field': 'paid_amount', 'message': 'paid_amount должен быть числом.'}],
                },
            )

    def _validate_order_payment_input(self, *, total_amount: Decimal, initial: dict) -> tuple[str, str | None, Decimal]:
        ptype = (initial.get('payment_type') or '').strip().lower() if hasattr(initial, 'get') else ''
        pmethod = (initial.get('payment_method') or '').strip().lower() if hasattr(initial, 'get') else ''
        paid_input = self._parse_paid_amount_input(initial)

        if not ptype:
            self._raise_order_error('MISSING_PAYMENT_TYPE', 'Поле payment_type обязательно.', field='payment_type')
        if ptype not in self.PAYMENT_KIND_CHOICES:
            self._raise_order_error('INVALID_PAYMENT_TYPE', 'payment_type: full | partial | debt', field='payment_type')
        if paid_input is not None and paid_input < 0:
            self._raise_order_error('INVALID_PAID_AMOUNT', 'paid_amount не может быть отрицательной.', field='paid_amount')
        if ptype != self.PAYMENT_DEBT and not pmethod:
            self._raise_order_error('MISSING_PAYMENT_METHOD', 'Поле payment_method обязательно.', field='payment_method')
        if pmethod and pmethod not in self.PAYMENT_METHOD_CHOICES:
            self._raise_order_error('INVALID_PAYMENT_METHOD', 'payment_method: cash | card | transfer', field='payment_method')

        total = Decimal(str(total_amount or 0)).quantize(Decimal('0.01'))
        if ptype == self.PAYMENT_FULL:
            paid = total if paid_input is None else Decimal(str(paid_input)).quantize(Decimal('0.01'))
            if total > 0 and paid != total:
                self._raise_order_error(
                    'FULL_PAYMENT_MUST_EQUAL_TOTAL',
                    'Для payment_type=full paid_amount должен быть равен total_amount.',
                    field='paid_amount',
                )
            return ptype, pmethod, paid
        if ptype == self.PAYMENT_DEBT:
            if paid_input not in (None, Decimal('0')):
                self._raise_order_error('PAYMENT_TYPE_CONFLICT', 'Для payment_type=debt paid_amount должен быть 0.', field='paid_amount')
            return ptype, pmethod, Decimal('0')

        if paid_input is None:
            self._raise_order_error('PAID_AMOUNT_REQUIRED', 'Для payment_type=partial поле paid_amount обязательно.', field='paid_amount')
        paid = Decimal(str(paid_input)).quantize(Decimal('0.01'))
        if paid <= 0:
            self._raise_order_error('INVALID_PAID_AMOUNT', 'Для payment_type=partial paid_amount должен быть > 0.', field='paid_amount')
        if total > 0 and paid > total:
            self._raise_order_error('PAID_AMOUNT_EXCEEDS_TOTAL', 'paid_amount не должен превышать total_amount заявки.', field='paid_amount')
        return ptype, pmethod, paid

    @staticmethod
    def _sync_embedded_order_payment(*, order: Order, payment_method: str | None, paid_amount: Decimal, user) -> None:
        marker = '[embedded_order_payment]'
        existing = order.payments.filter(
            status=Payment.STATUS_ACTIVE,
            payment_type=Payment.TYPE_PREPAYMENT,
            comment__startswith=marker,
            linked_sale__isnull=True,
        )
        if existing.exists():
            existing.update(status=Payment.STATUS_CANCELED)
        paid = Decimal(str(paid_amount or 0)).quantize(Decimal('0.01'))
        if paid <= 0:
            return
        Payment.objects.create(
            date=order.date,
            client=order.client,
            linked_order=order,
            payment_type=Payment.TYPE_PREPAYMENT,
            amount=paid,
            payment_method=(
                Payment.METHOD_CASH if payment_method == 'cash'
                else (Payment.METHOD_CARD if payment_method == 'card' else Payment.METHOD_TRANSFER)
            ),
            status=Payment.STATUS_ACTIVE,
            comment=f'{marker} order={order.pk}',
            created_by=user if getattr(user, 'is_authenticated', False) else None,
        )

    def validate(self, attrs):
        idata = self.initial_data or {}
        for forbidden in (
            'resolved_recipe', 'request_total_meters', 'resource_check_snapshot', 'request_status',
        ):
            if forbidden in idata:
                self._raise_order_error('FORBIDDEN_FIELD', f'Поле {forbidden} задаётся только на сервере.', field=forbidden)
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
            raw_order_lines = idata.get('order_lines')
            raw_lines = idata.get('lines')
            has_explicit_lines = isinstance(raw_order_lines, list) or isinstance(raw_lines, list)
            legacy_keys = {'profile', 'recipe', 'length', 'quantity'}
            has_legacy_any = any(k in idata for k in legacy_keys)
            if not has_explicit_lines and has_legacy_any:
                profile = attrs.get('production_profile')
                if profile is None:
                    self._raise_order_error('MISSING_PROFILE', 'Поле profile обязательно.', field='profile')
                recipe = attrs.get('resolved_recipe')
                if recipe is not None and profile is not None and recipe.profile_id != profile.id:
                    self._raise_order_error(
                        'RECIPE_PROFILE_MISMATCH',
                        'recipe не относится к выбранному profile.',
                        field='recipe',
                    )
                ln = attrs.get('production_length')
                qt = attrs.get('production_quantity')
                if qt in (None, ''):
                    self._raise_order_error('INVALID_QUANTITY', 'Поле quantity обязательно.', field='quantity')
                if ln not in (None, ''):
                    try:
                        ln_d = Decimal(str(ln))
                    except Exception:
                        self._raise_order_error('INVALID_LENGTH', 'Некорректная длина (length).', field='length')
                    if ln_d <= 0:
                        self._raise_order_error('INVALID_LENGTH', 'length должно быть > 0.', field='length')
                try:
                    q_d = Decimal(str(qt))
                except Exception:
                    self._raise_order_error('INVALID_QUANTITY', 'Некорректное количество (quantity).', field='quantity')
                if q_d <= 0:
                    self._raise_order_error('INVALID_QUANTITY', 'quantity должно быть > 0.', field='quantity')
            self._normalized_order_lines = self._normalize_cart_order_lines(idata)
            declared_total = self._parse_total_amount_input(idata)
            if declared_total is not None and declared_total < 0:
                self._raise_order_error(
                    'INVALID_TOTAL_AMOUNT',
                    'total_amount не может быть отрицательным.',
                    field='total_amount',
                )
            if declared_total is not None:
                self._apply_declared_total_to_normalized_lines(self._normalized_order_lines, declared_total)
            total_amount = Decimal('0')
            for normalized in self._normalized_order_lines:
                total_amount += (
                    Decimal(str(normalized.get('ordered_quantity') or 0))
                    * Decimal(str(normalized.get('unit_price') or 0))
                )
            self._order_payment_input = self._validate_order_payment_input(
                total_amount=total_amount.quantize(Decimal('0.01')),
                initial=idata,
            )
            return attrs

        if any(k in idata for k in ('profile', 'recipe')):
            profile = attrs.get('production_profile', self.instance.production_profile)
            recipe = attrs.get('resolved_recipe', self.instance.resolved_recipe)
            if recipe is not None and profile is not None and recipe.profile_id != profile.id:
                self._raise_order_error(
                    'RECIPE_PROFILE_MISMATCH',
                    'recipe не относится к выбранному profile.',
                    field='recipe',
                )
        if any(k in idata for k in ('payment_type', 'payment_method', 'paid_amount')):
            total_amount = Decimal(str(self.instance.total_amount or 0)).quantize(Decimal('0.01'))
            self._order_payment_input = self._validate_order_payment_input(
                total_amount=total_amount,
                initial=idata,
            )

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
        if self.instance.request_status in (
            Order.REQUEST_STATUS_APPROVED,
            Order.REQUEST_STATUS_CHECKING,
            Order.REQUEST_STATUS_READY,
            Order.REQUEST_STATUS_IN_PRODUCTION,
        ) and any(k in idata for k in ('profile', 'length', 'quantity')):
            self._raise_order_error(
                'PRODUCTION_FIELD_LOCKED',
                'Нельзя менять profile, length, quantity, если request_status: approved, checking, ready или in_production.',
                field='profile',
            )
        if self.instance.request_status == Order.REQUEST_STATUS_IN_PRODUCTION and 'lines' in idata:
            self._raise_order_error(
                'PRODUCTION_FIELD_LOCKED',
                'Заявка в производстве: строки не редактируются.',
                field='lines',
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
        is_prod = False
        payment_input = getattr(self, '_order_payment_input', (None, None, Decimal('0')))
        validated_data.pop('payment_type', None)
        validated_data.pop('payment_method', None)
        validated_data.pop('order_lines', None)
        lines_data = validated_data.pop('lines', [])
        normalized_order_lines = getattr(self, '_normalized_order_lines', None)
        if normalized_order_lines is not None:
            lines_data = normalized_order_lines
        if (
            validated_data.get('production_profile') is not None
            and validated_data.get('production_length') is not None
            and validated_data.get('production_quantity') not in (None, 0)
        ):
            is_prod = True
        if is_prod:
            ln_d = validated_data.get('production_length')
            q_i = int(validated_data.get('production_quantity') or 0)
            validated_data['request_status'] = Order.REQUEST_STATUS_DRAFT
            validated_data['request_total_meters'] = (
                Decimal(str(ln_d)) * Decimal(int(q_i))
            ).quantize(Decimal('0.0001'))
            validated_data.setdefault('resource_check_snapshot', {})
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
            first_line_internal = None
            for line_data in lines_data:
                if normalized_order_lines is not None:
                    if first_line_internal is None:
                        first_line_internal = line_data
                    ld = {k: v for k, v in line_data.items() if not str(k).startswith('_')}
                    OrderLine.objects.create(order=order, **ld)
                else:
                    normalized = self._validate_order_line_payload(line_data)
                    OrderLine.objects.create(order=order, **normalized)
            if first_line_internal is not None:
                r = first_line_internal.get('_recipe')
                prof = first_line_internal.get('profile')
                ln_d = first_line_internal.get('_length')
                qty_raw = first_line_internal.get('ordered_quantity')
                uf = []
                if prof is not None:
                    order.production_profile = prof
                    uf.append('production_profile')
                if r is not None:
                    order.resolved_recipe = r
                    uf.append('resolved_recipe')
                if ln_d is not None:
                    order.production_length = ln_d
                    uf.append('production_length')
                if qty_raw is not None:
                    order.production_quantity = int(qty_raw)
                    uf.append('production_quantity')
                if r is not None and prof is not None and ln_d is not None and qty_raw is not None:
                    order.request_status = Order.REQUEST_STATUS_DRAFT
                    order.request_total_meters = (
                        Decimal(str(ln_d)) * Decimal(int(qty_raw))
                    ).quantize(Decimal('0.0001'))
                    order.resource_check_snapshot = {}
                    uf.extend(['request_status', 'request_total_meters', 'resource_check_snapshot'])
                if uf:
                    order.save(update_fields=list(dict.fromkeys(uf)) + ['updated_at'])
            pay_type, pay_method, pay_amount = payment_input
            if pay_type in (self.PAYMENT_FULL, self.PAYMENT_PARTIAL):
                req = self.context.get('request')
                user = getattr(req, 'user', None)
                self._sync_embedded_order_payment(
                    order=order,
                    payment_method=pay_method,
                    paid_amount=pay_amount,
                    user=user,
                )
        return order

    def update(self, instance, validated_data):
        idata = self.initial_data or {}
        validated_data.pop('payment_type', None)
        validated_data.pop('payment_method', None)
        lines_data = validated_data.pop('lines', None)
        if any(
            k in idata for k in ('profile', 'length', 'quantity', 'recipe')
        ) and instance.request_status in (
            None, Order.REQUEST_STATUS_DRAFT, Order.REQUEST_STATUS_NOT_READY,
        ):
            l = validated_data.get('production_length', instance.production_length)
            q = validated_data.get('production_quantity', instance.production_quantity)
            if l is not None and q is not None:
                try:
                    validated_data['request_total_meters'] = (
                        Decimal(str(l)) * Decimal(int(q))
                    ).quantize(Decimal('0.0001'))
                except (ArithmeticError, TypeError, ValueError):
                    pass
            validated_data['resource_check_snapshot'] = {}
            if instance.request_status in (None, Order.REQUEST_STATUS_NOT_READY):
                validated_data['request_status'] = Order.REQUEST_STATUS_DRAFT
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
            payment_input = getattr(self, '_order_payment_input', None)
            if payment_input is not None:
                pay_type, pay_method, pay_amount = payment_input
                req = self.context.get('request')
                user = getattr(req, 'user', None)
                if pay_type in (self.PAYMENT_FULL, self.PAYMENT_PARTIAL):
                    self._sync_embedded_order_payment(
                        order=instance,
                        payment_method=pay_method,
                        paid_amount=pay_amount,
                        user=user,
                    )
                else:
                    self._sync_embedded_order_payment(
                        order=instance,
                        payment_method='cash',
                        paid_amount=Decimal('0'),
                        user=user,
                    )
        return instance


class ClientOrderProductionRequestSerializer(serializers.ModelSerializer):
    """GET /api/production/requests/ — заявки клиента (id = id в /api/orders/)."""
    client = serializers.SerializerMethodField()
    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    profile = serializers.SerializerMethodField()
    recipe = serializers.SerializerMethodField()
    length = serializers.SerializerMethodField()
    quantity = serializers.SerializerMethodField()
    total_meters = serializers.SerializerMethodField()
    order_lines = serializers.SerializerMethodField()
    lines_count = serializers.SerializerMethodField()
    allowed_blank_ids = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id',
            'order_number',
            'date',
            'status',
            'request_status',
            'client',
            'client_name',
            'profile',
            'recipe',
            'length',
            'quantity',
            'total_meters',
            'order_lines',
            'lines_count',
            'allowed_blank_ids',
        )

    def get_client(self, obj):
        c = obj.client
        if c is None:
            return None
        return {'id': c.id, 'name': c.name}

    def get_profile(self, obj):
        p = obj.production_profile
        if p is None:
            return None
        return {'id': p.id, 'name': p.name, 'code': p.code}

    def get_recipe(self, obj):
        r = obj.resolved_recipe
        if r is None:
            return None
        return {
            'id': r.id,
            'name': (r.recipe or '').strip() or (r.product or '')[:255],
            'profile_id': r.profile_id,
        }

    def get_length(self, obj):
        if obj.production_length is None:
            return None
        return api_decimal_str(Decimal(str(obj.production_length)))

    def get_quantity(self, obj):
        return obj.production_quantity

    def get_total_meters(self, obj):
        if obj.request_total_meters is not None:
            return api_decimal_str(Decimal(str(obj.request_total_meters)))
        if obj.production_length is not None and obj.production_quantity is not None:
            return api_decimal_str(
                Decimal(str(obj.production_length)) * Decimal(int(obj.production_quantity)),
            )
        return None

    def get_order_lines(self, obj):
        lines, _, _ = OrderSerializer.build_order_lines_read_payload(
            obj,
            include_line_allowed_blanks=True,
        )
        return lines

    def get_lines_count(self, obj):
        return len(self.get_order_lines(obj))

    def get_allowed_blank_ids(self, obj):
        profile_ids: set[int] = set()
        if obj.production_profile_id:
            profile_ids.add(obj.production_profile_id)
        for line in obj.lines.all():
            if line.profile_id:
                profile_ids.add(line.profile_id)
        if not profile_ids:
            return []
        fallback_set: set[int] = set()
        for pid in profile_ids:
            fallback_set.update(OrderSerializer.allowed_blank_ids_for_profile(pid))
        return sorted(fallback_set)
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

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = getattr(exc, 'detail', {})
            if isinstance(detail, dict):
                if 'payment_type' in detail:
                    self._raise_payment_error(
                        'INVALID_PAYMENT_TYPE',
                        'Некорректный payment_type. Допустимо: prepayment/payment/surcharge/refund.',
                        field='payment_type',
                    )
                if 'payment_method' in detail:
                    self._raise_payment_error(
                        'INVALID_PAYMENT_METHOD',
                        'Некорректный payment_method. Допустимо: cash/transfer/card/other.',
                        field='payment_method',
                    )
            raise

    @staticmethod
    def _raise_payment_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'detail': message,
                'errors': [{'field': field, 'message': message}],
            },
        )

    def validate_amount(self, v):
        return v

    def validate(self, attrs):
        initial = self.initial_data or {}
        is_create = self.instance is None
        if not is_create:
            frozen_in_update = ('amount', 'client', 'linked_sale', 'linked_order', 'linked_return', 'payment_type', 'status')
            for key in frozen_in_update:
                if key in initial:
                    if key == 'status':
                        self._raise_payment_error(
                            'PAYMENT_STATUS_UPDATE_FORBIDDEN',
                            'Статус оплаты меняется только через /cancel/.',
                            field='status',
                        )
                    self._raise_payment_error(
                        'PAYMENT_STATUS_UPDATE_FORBIDDEN',
                        (
                            f'После создания поле "{key}" нельзя менять; '
                            'отмена записи — только POST/PATCH /api/payments/{id}/cancel/.'
                        ),
                        field=key,
                    )
        if 'status' in initial:
            self._raise_payment_error(
                'PAYMENT_STATUS_UPDATE_FORBIDDEN',
                'Статус оплаты меняется только через /cancel/.',
                field='status',
            )

        if is_create and ('client' not in initial or initial.get('client') in (None, '', 'null')):
            self._raise_payment_error('MISSING_CLIENT', 'Поле client обязательно.', field='client')
        client = attrs.get('client', getattr(self.instance, 'client', None) if self.instance else None)
        if client is None:
            self._raise_payment_error('MISSING_CLIENT', 'Поле client обязательно.', field='client')
        if is_create and not client.is_active:
            self._raise_payment_error(
                'INACTIVE_CLIENT',
                'Клиент неактивен. Создание оплаты запрещено.',
                field='client',
            )

        ptype = attrs.get('payment_type', getattr(self.instance, 'payment_type', None) if self.instance else None)
        if is_create and ('payment_type' not in initial or initial.get('payment_type') in (None, '')):
            self._raise_payment_error('INVALID_PAYMENT_TYPE', 'Поле payment_type обязательно.', field='payment_type')
        valid_types = {x[0] for x in Payment.TYPE_CHOICES}
        if ptype not in valid_types:
            self._raise_payment_error(
                'INVALID_PAYMENT_TYPE',
                'Некорректный payment_type. Допустимо: prepayment/payment/surcharge/refund.',
                field='payment_type',
            )

        pmethod = attrs.get('payment_method', getattr(self.instance, 'payment_method', None) if self.instance else None)
        if is_create and ('payment_method' not in initial or initial.get('payment_method') in (None, '')):
            self._raise_payment_error('INVALID_PAYMENT_METHOD', 'Поле payment_method обязательно.', field='payment_method')
        valid_methods = {x[0] for x in Payment.METHOD_CHOICES}
        if pmethod not in valid_methods:
            self._raise_payment_error(
                'INVALID_PAYMENT_METHOD',
                'Некорректный payment_method. Допустимо: cash/transfer/card/other.',
                field='payment_method',
            )

        amount_value = attrs.get('amount', getattr(self.instance, 'amount', None) if self.instance else None)
        if amount_value is None:
            self._raise_payment_error('INVALID_AMOUNT', 'Сумма обязательна.', field='amount')
        if Decimal(str(amount_value)) <= 0:
            self._raise_payment_error('INVALID_AMOUNT', 'Сумма должна быть больше 0.', field='amount')

        lo = attrs.get('linked_order', getattr(self.instance, 'linked_order', None) if self.instance else None)
        ls = attrs.get('linked_sale', getattr(self.instance, 'linked_sale', None) if self.instance else None)
        lr = attrs.get('linked_return', getattr(self.instance, 'linked_return', None) if self.instance else None)

        if lo is not None and lo.client_id and lo.client_id != client.pk:
            self._raise_payment_error('CLIENT_MISMATCH', 'Заявка привязана к другому клиенту.', field='linked_order')
        if ls is not None and ls.client_id and ls.client_id != client.pk:
            self._raise_payment_error('CLIENT_MISMATCH', 'Продажа привязана к другому клиенту.', field='linked_sale')
        if lr is not None and lr.sale and lr.sale.client_id and lr.sale.client_id != client.pk:
            self._raise_payment_error('CLIENT_MISMATCH', 'Возврат относится к другому клиенту.', field='linked_return')

        if ptype == Payment.TYPE_PREPAYMENT:
            if lo is None:
                self._raise_payment_error(
                    'MISSING_LINKED_ENTITY',
                    'Для prepayment обязательно поле linked_order.',
                    field='linked_order',
                )
            if lo.status in (Order.STATUS_CANCELED, Order.STATUS_CLOSED):
                self._raise_payment_error(
                    'MISSING_LINKED_ENTITY',
                    'Нельзя делать prepayment по canceled/closed заявке.',
                    field='linked_order',
                )

        if ptype in (Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE):
            if lo is None and ls is None:
                self._raise_payment_error(
                    'MISSING_LINKED_ENTITY',
                    'Для payment/surcharge укажите linked_sale или linked_order.',
                    field='linked_sale',
                )
            if lo is not None and lo.status == Order.STATUS_CANCELED:
                self._raise_payment_error(
                    'MISSING_LINKED_ENTITY',
                    'Нельзя проводить payment/surcharge по canceled заявке.',
                    field='linked_order',
                )
            if ls is not None and ls.sale_status == Sale.STATUS_CANCELED:
                self._raise_payment_error(
                    'MISSING_LINKED_ENTITY',
                    'Нельзя проводить payment/surcharge по canceled продаже.',
                    field='linked_sale',
                )

        if ptype == Payment.TYPE_REFUND:
            mrr = (attrs.get('manual_refund_reason') or '').strip() or (
                self.instance and (self.instance.manual_refund_reason or '').strip() if self.instance else ''
            )
            if lr is None and not mrr:
                self._raise_payment_error(
                    'REFUND_RETURN_REQUIRED',
                    'Для refund укажите linked_return либо manual_refund_reason (ручной возврат).',
                    field='linked_return',
                )
            if lr is None and 'manual_refund_reason' in initial and not str(initial.get('manual_refund_reason') or '').strip():
                self._raise_payment_error(
                    'REFUND_REASON_REQUIRED',
                    'Для ручного refund поле manual_refund_reason обязательно.',
                    field='manual_refund_reason',
                )
            if lr is not None:
                if lr.status != Return.STATUS_COMPLETED:
                    self._raise_payment_error(
                        'REFUND_RETURN_NOT_COMPLETED',
                        'Refund разрешен только для return в статусе completed.',
                        field='linked_return',
                    )
                return_total = Decimal('0')
                for rl in lr.lines.select_related('sale_line').all():
                    unit_price = Decimal(str(rl.sale_line.unit_price or 0)) if rl.sale_line_id else Decimal('0')
                    qty = Decimal(str(rl.quantity or 0))
                    return_total += (unit_price * qty)
                active_refunds_qs = Payment.objects.filter(
                    linked_return=lr,
                    payment_type=Payment.TYPE_REFUND,
                    status=Payment.STATUS_ACTIVE,
                )
                if self.instance is not None:
                    active_refunds_qs = active_refunds_qs.exclude(pk=self.instance.pk)
                already_refunded = active_refunds_qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')
                requested = Decimal(str(attrs.get('amount', self.instance.amount if self.instance else 0)))
                available = max(Decimal('0'), return_total - Decimal(str(already_refunded)))
                if requested > available:
                    self._raise_payment_error(
                        'REFUND_AMOUNT_EXCEEDED',
                        f'Сумма refund превышает доступный лимит ({api_decimal_str(available)}).',
                        field='amount',
                    )

        return attrs

    def create(self, validated_data):
        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()
        validated_data.pop('status', None)
        validated_data['status'] = Payment.STATUS_ACTIVE
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
            self._raise_payment_error('PAYMENT_ALREADY_CANCELED', 'Отмененную оплату нельзя редактировать.', field='status')
        frozen = ('amount', 'client', 'linked_sale', 'linked_order', 'linked_return', 'payment_type', 'status')
        for key in frozen:
            if key in validated_data:
                if key == 'status':
                    self._raise_payment_error(
                        'PAYMENT_STATUS_UPDATE_FORBIDDEN',
                        'Статус оплаты меняется только через /cancel/.',
                        field='status',
                    )
                self._raise_payment_error(
                    'PAYMENT_STATUS_UPDATE_FORBIDDEN',
                    (
                        f'После создания поле "{key}" нельзя менять; '
                        'отмена записи — только POST/PATCH /api/payments/{id}/cancel/.'
                    ),
                    field=key,
                )
        return super().update(instance, validated_data)


# ─────────────────────────────────────────────────────────────────────────────
# SALE (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

class SaleLineSerializer(serializers.ModelSerializer):
    unit_type = serializers.CharField(required=False, allow_blank=False)
    warehouse_batch_id = serializers.IntegerField(read_only=True, allow_null=True)
    gp_package_id = serializers.IntegerField(source='gp_pack_unit_id', read_only=True, allow_null=True)

    class Meta:
        model = SaleLine
        fields = (
            'id', 'product', 'warehouse_batch', 'warehouse_batch_id', 'order_line',
            'stock_form', 'piece_pick', 'quantity', 'unit_price', 'line_total',
            'cost', 'profit', 'defect_flag', 'comment', 'unit_type', 'gp_pack_unit', 'gp_package_id',
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
        if not ret.get('unit_type'):
            ret['unit_type'] = self._derive_unit_type(instance)
        return ret

    @staticmethod
    def _derive_unit_type(line: SaleLine) -> str:
        from apps.warehouse.models import WarehouseBatch

        if line.gp_pack_unit_id:
            return Sale.MODE_PACKAGES
        wb = line.warehouse_batch
        if wb and wb.inventory_form == WarehouseBatch.INVENTORY_PACKED and wb.pieces_per_package:
            ppp = Decimal(str(wb.pieces_per_package))
            qty = Decimal(str(line.quantity or 0))
            if ppp > 0 and qty > 0 and qty % ppp == 0 and line.piece_pick == 'from_sealed_package':
                return Sale.MODE_PACKAGES
        return Sale.MODE_PIECES


class SaleSerializer(serializers.ModelSerializer):
    PAYMENT_FULL = 'full'
    PAYMENT_PARTIAL = 'partial'
    PAYMENT_DEBT = 'debt'
    PAYMENT_KIND_CHOICES = (PAYMENT_FULL, PAYMENT_PARTIAL, PAYMENT_DEBT)
    PAYMENT_METHOD_CHOICES = ('cash', 'card', 'transfer')

    client_name = serializers.CharField(source='client.name', read_only=True, allow_null=True, default='')
    inventory_form = serializers.SerializerMethodField()
    order_number = serializers.CharField(required=False, allow_blank=True)
    date = serializers.DateField(required=False, allow_null=True)
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True,
    )
    sale_unit = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    packaging = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    stock_form = serializers.CharField(required=False, allow_blank=True, max_length=20, default='')
    piece_pick = serializers.CharField(required=False, allow_blank=True, max_length=40, default='')
    order = serializers.PrimaryKeyRelatedField(
        source='linked_order',
        queryset=Order.objects.all(),
        required=False,
        allow_null=True,
    )
    total_amount = serializers.SerializerMethodField()
    original_total_amount = serializers.SerializerMethodField()
    returned_amount = serializers.SerializerMethodField()
    total_amount_after_returns = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    payment_type = serializers.CharField(write_only=True, required=False, allow_blank=False)
    payment_method = serializers.CharField(write_only=True, required=False, allow_blank=False)
    paid_amount = serializers.DecimalField(
        max_digits=16,
        decimal_places=2,
        required=False,
        allow_null=True,
        write_only=True,
    )
    order_paid_amount_applied = serializers.DecimalField(
        max_digits=16,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    unit_type = serializers.CharField(required=False, allow_blank=False, write_only=True)
    profile_name = serializers.SerializerMethodField()
    sale_lines = SaleLineSerializer(many=True, read_only=True)
    payment_status = serializers.SerializerMethodField()
    debt_amount = serializers.SerializerMethodField()
    refund_amount = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')

    class Meta:
        model = Sale
        fields = (
            'id', 'order_number', 'sale_number', 'invoice_number', 'receipt_number',
            'sale_status', 'linked_order',
            'order',
            'client', 'client_name',
            'product', 'sale_mode', 'unit_type', 'sold_pieces', 'sold_packages',
            'length_per_piece', 'total_meters',
            'quantity_input', 'price', 'revenue',
            'original_total_amount', 'returned_amount', 'total_amount',
            'total_amount_after_returns', 'cost', 'date',
            'comment',
            'sale_unit', 'packaging', 'stock_form', 'inventory_form', 'piece_pick', 'profit',
            'profile_name', 'stock_quality',
            'is_defect_sale',
            'warehouse_stock_applied', 'credit_limit_bypassed', 'updated_at',
            'created_by', 'created_by_name', 'created_at',
            'sale_lines', 'payment_status', 'paid_amount', 'debt_amount', 'refund_amount',
            'status',
            'payment_type', 'payment_method',
            'order_paid_amount_applied',
        )
        read_only_fields = (
            'profit', 'revenue', 'cost', 'total_meters', 'inventory_form',
            'profile_name', 'stock_quality',
            'created_at', 'sale_lines',
            'warehouse_stock_applied', 'credit_limit_bypassed', 'updated_at',
            'payment_status', 'debt_amount', 'refund_amount',
            'original_total_amount', 'returned_amount', 'total_amount',
            'total_amount_after_returns', 'status',
        )
        extra_kwargs = {
            'product': {'required': False, 'allow_blank': True},
            'linked_order': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def get_total_amount(self, obj):
        return api_decimal_str(sale_active_total_amount(obj))

    def get_original_total_amount(self, obj):
        return api_decimal_str(sale_original_total_amount(obj))

    def get_returned_amount(self, obj):
        return api_decimal_str(sale_returned_amount(obj))

    def get_total_amount_after_returns(self, obj):
        return api_decimal_str(sale_active_total_amount(obj))

    def get_status(self, obj):
        original = sale_original_total_amount(obj)
        returned = sale_returned_amount(obj)
        if original > 0 and returned >= original:
            return 'returned' if obj.sale_status != Sale.STATUS_CANCELED else Sale.STATUS_CANCELED
        if returned > 0:
            return 'partial_return'
        if obj.sale_status in (Sale.STATUS_SHIPPED, Sale.STATUS_CLOSED) or obj.warehouse_stock_applied:
            return 'completed'
        return obj.sale_status

    def get_debt_amount(self, obj):
        from .payment_status import sale_payment_metrics
        return api_decimal_str(sale_payment_metrics(obj)['debt_amount'])

    def get_refund_amount(self, obj):
        from .payment_status import sale_payment_metrics
        return api_decimal_str(sale_payment_metrics(obj)['refund_amount'])

    def to_internal_value(self, data):
        if hasattr(data, 'get'):
            d = data.copy() if isinstance(data, dict) else dict(data)
            unit_type = d.get('unit_type')
            if unit_type not in (None, ''):
                d['sale_mode'] = unit_type
            if d.get('client') in (None, '') and d.get('client_id') not in (None, ''):
                d['client'] = d.get('client_id')
            return super().to_internal_value(d)
        return super().to_internal_value(data)

    @staticmethod
    def _parse_paid_amount_input(initial: dict) -> Decimal | None:
        if not hasattr(initial, 'get'):
            return None
        raw = initial.get('paid_amount')
        if raw in (None, ''):
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError(
                {
                    'code': 'INVALID_PAID_AMOUNT',
                    'message': 'paid_amount должен быть числом.',
                    'detail': 'paid_amount должен быть числом.',
                    'fields': [{'field': 'paid_amount', 'message': 'paid_amount должен быть числом.'}],
                    'errors': [{'field': 'paid_amount', 'message': 'paid_amount должен быть числом.'}],
                },
            )

    def _validate_sale_supplemental_payment(
        self,
        *,
        sale_total: Decimal,
        order_prepaid: Decimal,
        initial: dict,
    ) -> tuple[str | None, str | None, Decimal]:
        """
        paid_amount — доплата при продаже; order_paid_amount_applied — аванс заявки (не в Payment).
        """
        ptype = (initial.get('payment_type') or '').strip().lower() if hasattr(initial, 'get') else ''
        pmethod = (initial.get('payment_method') or '').strip().lower() if hasattr(initial, 'get') else ''
        paid_input = self._parse_paid_amount_input(initial)

        if not ptype:
            if paid_input is None and not pmethod:
                return None, None, Decimal('0')
            if paid_input is not None and paid_input > 0:
                ptype = self.PAYMENT_PARTIAL
            else:
                ptype = self.PAYMENT_DEBT
                paid_input = None
        if not pmethod:
            pmethod = 'cash'
        if ptype not in self.PAYMENT_KIND_CHOICES:
            self._raise_sale_error(
                'INVALID_PAYMENT_TYPE',
                'payment_type: full | partial | debt',
                field='payment_type',
            )
        if pmethod not in self.PAYMENT_METHOD_CHOICES:
            self._raise_sale_error(
                'INVALID_PAYMENT_METHOD',
                'payment_method: cash | card | transfer',
                field='payment_method',
            )
        if paid_input is not None and paid_input < 0:
            self._raise_sale_error(
                'INVALID_PAID_AMOUNT',
                'paid_amount не может быть отрицательной.',
                field='paid_amount',
            )

        prepaid = Decimal(str(order_prepaid or 0)).quantize(Decimal('0.01'))
        sale_total = Decimal(str(sale_total or 0)).quantize(Decimal('0.01'))
        remaining_due = max(Decimal('0'), sale_total - prepaid).quantize(Decimal('0.01'))

        if ptype == self.PAYMENT_FULL:
            supplemental = (
                remaining_due if paid_input is None else Decimal(str(paid_input)).quantize(Decimal('0.01'))
            )
            if supplemental > remaining_due + Decimal('0.01'):
                self._raise_sale_error(
                    'PAID_AMOUNT_EXCEEDS_REMAINING',
                    'Для полной оплаты доплата не должна превышать остаток по продаже после аванса заявки.',
                    field='paid_amount',
                )
            return ptype, pmethod, supplemental

        if ptype == self.PAYMENT_PARTIAL:
            if paid_input is None:
                self._raise_sale_error(
                    'PAID_AMOUNT_REQUIRED',
                    'Для partial укажите paid_amount (доплата при продаже).',
                    field='paid_amount',
                )
            supplemental = Decimal(str(paid_input)).quantize(Decimal('0.01'))
            if supplemental <= 0:
                self._raise_sale_error(
                    'INVALID_PAID_AMOUNT',
                    'paid_amount (доплата) должен быть > 0.',
                    field='paid_amount',
                )
            if supplemental > remaining_due + Decimal('0.01'):
                self._raise_sale_error(
                    'PAID_AMOUNT_EXCEEDS_REMAINING',
                    'Доплата (paid_amount) не должна превышать остаток: '
                    f'продажа {sale_total}, аванс заявки {prepaid}, осталось {remaining_due}.',
                    field='paid_amount',
                )
            return ptype, pmethod, supplemental

        if paid_input is not None and paid_input > Decimal('0.01'):
            self._raise_sale_error(
                'PAYMENT_TYPE_CONFLICT',
                'Для payment_type=debt paid_amount (доплата) должен быть 0.',
                field='paid_amount',
            )
        return ptype, pmethod, Decimal('0')

    def _validate_embedded_payment(self, *, total_amount: Decimal, initial: dict) -> tuple[str | None, str | None, Decimal]:
        ptype = (initial.get('payment_type') or '').strip().lower() if hasattr(initial, 'get') else ''
        pmethod = (initial.get('payment_method') or '').strip().lower() if hasattr(initial, 'get') else ''
        paid_input = self._parse_paid_amount_input(initial)

        if not ptype:
            if paid_input is None and not pmethod:
                return None, None, Decimal('0')
            if paid_input is not None and paid_input > 0:
                ptype = self.PAYMENT_PARTIAL
            else:
                ptype = self.PAYMENT_DEBT
                paid_input = None
        if not pmethod:
            pmethod = 'cash'
        if ptype not in self.PAYMENT_KIND_CHOICES:
            self._raise_sale_error(
                'INVALID_PAYMENT_TYPE',
                'payment_type: full | partial | debt',
                field='payment_type',
            )
        if pmethod not in self.PAYMENT_METHOD_CHOICES:
            self._raise_sale_error(
                'INVALID_PAYMENT_METHOD',
                'payment_method: cash | card | transfer',
                field='payment_method',
            )
        if paid_input is not None and paid_input < 0:
            self._raise_sale_error(
                'INVALID_PAID_AMOUNT',
                'paid_amount не может быть отрицательной.',
                field='paid_amount',
            )

        total = Decimal(str(total_amount or 0)).quantize(Decimal('0.01'))
        if ptype == self.PAYMENT_FULL:
            paid = total if paid_input is None else Decimal(str(paid_input)).quantize(Decimal('0.01'))
            if paid_input is not None and paid != total:
                self._raise_sale_error(
                    'FULL_PAYMENT_MUST_EQUAL_TOTAL',
                    'Для payment_type=full paid_amount должен быть равен total_amount.',
                    field='paid_amount',
                )
            return ptype, pmethod, paid
        if ptype == self.PAYMENT_DEBT:
            return ptype, pmethod, Decimal('0')

        # partial
        if paid_input is None:
            self._raise_sale_error(
                'PAID_AMOUNT_REQUIRED',
                'Для payment_type=partial поле paid_amount обязательно.',
                field='paid_amount',
            )
        paid = Decimal(str(paid_input)).quantize(Decimal('0.01'))
        if paid <= 0:
            self._raise_sale_error(
                'INVALID_PAID_AMOUNT',
                'Для payment_type=partial paid_amount должен быть > 0.',
                field='paid_amount',
            )
        if paid > total:
            self._raise_sale_error(
                'PAID_AMOUNT_EXCEEDS_TOTAL',
                'Нельзя оплатить больше чем total_amount.',
                field='paid_amount',
            )
        return ptype, pmethod, paid

    @staticmethod
    def _raise_sale_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'message': message,
                'detail': message,
                'fields': [{'field': field, 'message': message}],
                'errors': [{'field': field, 'message': message}],
            },
        )

    @staticmethod
    def _is_shipping_target(status_value: str) -> bool:
        return status_value in (
            Sale.STATUS_PARTIALLY_SHIPPED,
            Sale.STATUS_SHIPPED,
        )

    def _validate_sale_line_payload(self, row: dict, *, shipping_target: bool) -> dict:
        payload = dict(row or {})
        order_line = payload.get('order_line')
        if order_line is not None and not isinstance(order_line, OrderLine):
            try:
                order_line = OrderLine.objects.get(pk=order_line)
            except OrderLine.DoesNotExist:
                self._raise_sale_error(
                    'PRODUCT_OR_ORDER_LINE_REQUIRED',
                    'Указанная order_line не найдена.',
                    field='sale_lines',
                )
        wb = payload.get('warehouse_batch')
        if wb is not None and not isinstance(wb, WarehouseBatch):
            try:
                wb = WarehouseBatch.objects.get(pk=wb)
            except WarehouseBatch.DoesNotExist:
                self._raise_sale_error(
                    'MISSING_WAREHOUSE_BATCH',
                    'Указанная warehouse_batch не найдена.',
                    field='sale_lines',
                )
        payload['order_line'] = order_line
        payload['warehouse_batch'] = wb
        product = (payload.get('product') or '').strip()
        if not product:
            if order_line is not None and getattr(order_line, 'product', None):
                product = order_line.product
            elif wb is not None and getattr(wb, 'product', None):
                product = wb.product
        if not product:
            self._raise_sale_error(
                'PRODUCT_OR_ORDER_LINE_REQUIRED',
                'Укажите product или передайте order_line/warehouse_batch для вывода product.',
                field='sale_lines',
            )
        payload['product'] = product

        if 'quantity' not in payload or payload.get('quantity') in (None, ''):
            self._raise_sale_error(
                'SALE_QUANTITY_REQUIRED',
                'Для строки продажи поле quantity обязательно.',
                field='sale_lines',
            )
        try:
            qty = Decimal(str(payload.get('quantity')))
        except (InvalidOperation, TypeError, ValueError):
            self._raise_sale_error(
                'SALE_QUANTITY_INVALID',
                'quantity должен быть числом больше 0.',
                field='sale_lines',
            )
        if qty <= 0:
            self._raise_sale_error(
                'SALE_QUANTITY_INVALID',
                'quantity должно быть больше 0.',
                field='sale_lines',
            )

        if payload.get('unit_price') in (None, ''):
            payload['unit_price'] = Decimal('0')
        else:
            try:
                unit_price = Decimal(str(payload.get('unit_price')))
            except (InvalidOperation, TypeError, ValueError):
                self._raise_sale_error(
                    'UNIT_PRICE_INVALID',
                    'unit_price должен быть числом.',
                    field='sale_lines',
                )
            if unit_price < 0:
                self._raise_sale_error(
                    'UNIT_PRICE_NEGATIVE',
                    'unit_price не может быть отрицательной.',
                    field='sale_lines',
                )

        if order_line is not None:
            remaining = Decimal(str(order_line.remaining_quantity or 0))
            if qty > remaining + Decimal('0.0001'):
                self._raise_sale_error(
                    'ORDER_LINE_QUANTITY_EXCEEDED',
                    f'Нельзя продать больше остатка строки заявки ({remaining}).',
                    field='sale_lines',
                )

        if wb is not None and wb.quality == WarehouseBatch.QUALITY_DEFECT and not payload.get('defect_flag', False):
            self._raise_sale_error(
                'DEFECT_BATCH_FORBIDDEN',
                'Обычная продажа не может использовать defect batch.',
                field='sale_lines',
            )

        if shipping_target:
            if wb is None:
                self._raise_sale_error(
                    'MISSING_WAREHOUSE_BATCH',
                    'Для partially_shipped/shipped в каждой строке обязателен warehouse_batch.',
                    field='sale_lines',
                )
            if wb.status != WarehouseBatch.STATUS_AVAILABLE:
                self._raise_sale_error(
                    'INSUFFICIENT_STOCK',
                    f'Партия #{wb.pk} недоступна для отгрузки (status={wb.status}).',
                    field='sale_lines',
                )
            if wb.quality != WarehouseBatch.QUALITY_GOOD:
                self._raise_sale_error(
                    'DEFECT_BATCH_FORBIDDEN',
                    'Для обычной продажи доступно только quality=good.',
                    field='sale_lines',
                )
            from .reservations import get_available_quantity
            available_qty = Decimal(str(get_available_quantity(wb.pk)))
            if qty > available_qty + Decimal('0.0001'):
                self._raise_sale_error(
                    'INSUFFICIENT_STOCK',
                    f'Недостаточно свободного остатка на партии (доступно {available_qty}).',
                    field='sale_lines',
                )

        return payload

    @staticmethod
    def _raise_sale_line_error(code: str, message: str, line_idx: int, field_name: str):
        field = f'sale_lines[{line_idx}].{field_name}'
        raise serializers.ValidationError(
            {
                'code': code,
                'message': message,
                'detail': message,
                'fields': [{'field': field, 'message': message}],
                'errors': [{'field': field, 'message': message}],
            },
        )

    def _bind_order_line_for_linked_order(self, line_payload: dict, linked_order: Order) -> dict:
        """
        Гарантирует совместимость sale_lines с linked_order и выставляет order_line, если он не передан.
        """
        order_line = line_payload.get('order_line')
        if order_line is not None:
            if order_line.order_id != linked_order.id:
                self._raise_sale_error(
                    'ORDER_LINE_ORDER_MISMATCH',
                    'order_line не принадлежит переданной заявке.',
                    field='sale_lines',
                )
            return line_payload

        product = (line_payload.get('product') or '').strip()
        if not product:
            self._raise_sale_error(
                'ORDER_LINE_REQUIRED',
                'Для продажи по заявке нужно указать order_line или продукт для сопоставления.',
                field='sale_lines',
            )

        def _norm(v: str) -> str:
            return ''.join(ch for ch in (v or '').strip().casefold() if ch.isalnum())

        lines = list(linked_order.lines.all().order_by('id'))
        normalized_product = _norm(product)
        candidates = [
            ln for ln in lines
            if _norm(ln.product or '') == normalized_product
        ]
        # Мягкий матч для случаев вроде "60 мм белый" vs "60мм белый профиль"
        if not candidates and normalized_product:
            candidates = [
                ln for ln in lines
                if normalized_product in _norm(ln.product or '') or _norm(ln.product or '') in normalized_product
            ]
        candidates.sort(key=lambda ln: ln.id)

        # UI может передать строку товара, отличающуюся от текста в заявке.
        # В этом случае привязываем к первой "живой" строке заявки.
        if not candidates:
            open_lines = [
                ln for ln in lines
                if Decimal(str(ln.remaining_quantity or 0)) > Decimal('0')
            ]
            open_lines.sort(key=lambda ln: ln.id)
            if open_lines:
                candidates = [open_lines[0]]
            elif lines:
                # Последний fallback: если строки в заявке есть, привязываем к первой,
                # а дальше проверка остатка даст точную бизнес-ошибку.
                candidates = [lines[0]]
            else:
                self._raise_sale_error(
                    'ORDER_HAS_NO_LINES',
                    'В заявке нет строк для продажи.',
                    field='sale_lines',
                )

        line_payload['order_line'] = candidates[0]
        qty = Decimal(str(line_payload.get('quantity') or 0))
        remaining = Decimal(str(line_payload['order_line'].remaining_quantity or 0))
        if qty > remaining + Decimal('0.0001'):
            self._raise_sale_error(
                'ORDER_LINE_QUANTITY_EXCEEDED',
                f'Нельзя продать больше остатка строки заявки ({remaining}).',
                field='sale_lines',
            )
        return line_payload

    def validate(self, attrs):
        initial = self.initial_data or {}
        if self.instance is not None and ('sale_status' in initial or 'status' in initial):
            self._raise_sale_error(
                'SALE_STATUS_UPDATE_FORBIDDEN',
                'Статус продажи меняется только через /status/.',
                field='sale_status',
            )

        if self.instance is not None:
            incoming_keys = set(initial.keys())
            safe_fields = {'date', 'comment', 'invoice_number', 'receipt_number'}
            unsafe_fields = incoming_keys - safe_fields
            if self.instance.sale_status in (
                Sale.STATUS_SHIPPED,
                Sale.STATUS_PARTIALLY_SHIPPED,
                Sale.STATUS_CLOSED,
                Sale.STATUS_CANCELED,
            ) and incoming_keys:
                self._raise_sale_error(
                    'SALE_UPDATE_FORBIDDEN',
                    f'Редактирование продажи в статусе "{self.instance.sale_status}" запрещено.',
                )
            if (
                self.instance.sale_status not in (Sale.STATUS_DRAFT, Sale.STATUS_CONFIRMED)
                and incoming_keys
            ):
                self._raise_sale_error(
                    'SALE_UPDATE_FORBIDDEN',
                    'Полное редактирование возможно только для draft/confirmed.',
                )
            if Payment.objects.filter(linked_sale=self.instance, status=Payment.STATUS_ACTIVE).exists() and incoming_keys:
                self._raise_sale_error(
                    'SALE_LOCKED_BY_PAYMENT',
                    'Продажа заблокирована: есть активные оплаты.',
                )
            if Return.objects.filter(sale=self.instance).exclude(status=Return.STATUS_CANCELED).exists() and incoming_keys:
                self._raise_sale_error(
                    'SALE_LOCKED_BY_RETURN',
                    'Продажа заблокирована: есть активные возвраты.',
                )
            if self.instance.warehouse_stock_applied and unsafe_fields:
                self._raise_sale_error(
                    'SALE_LOCKED_BY_WAREHOUSE',
                    'Продажа заблокирована: склад уже списан.',
                )
            if 'sale_lines' in initial:
                self._raise_sale_error(
                    'SALE_LINES_UPDATE_FORBIDDEN',
                    'Изменение sale_lines через PATCH/PUT продажи не поддерживается.',
                    field='sale_lines',
                )

        if self.instance is None:
            client = attrs.get('client')
            if client is None:
                self._raise_sale_error(
                    'MISSING_CLIENT',
                    'Поле client обязательно для создания продажи.',
                    field='client',
                )
            if client and not client.is_active:
                self._raise_sale_error(
                    'INACTIVE_CLIENT',
                    'Клиент неактивен. Создание продажи запрещено.',
                    field='client',
                )
            st = attrs.get('sale_status', Sale.STATUS_DRAFT)
            if st == Sale.STATUS_CLOSED:
                self._raise_sale_error(
                    'CLOSED_CREATE_FORBIDDEN',
                    'Создание продажи сразу в статусе closed запрещено.',
                    field='sale_status',
                )
            linked_order = attrs.get('linked_order')
            if linked_order is not None and linked_order.status in (Order.STATUS_CLOSED, Order.STATUS_CANCELED):
                self._raise_sale_error(
                    'ORDER_CLOSED_FOR_SALE',
                    'Заявка закрыта/отменена, создать продажу нельзя.',
                    field='order',
                )
            lines = initial.get('sale_lines')
            if not isinstance(lines, list) or len(lines) < 1:
                self._raise_sale_error(
                    'MISSING_SALE_LINES',
                    'sale_lines обязателен и должен содержать минимум одну строку.',
                    field='sale_lines',
                )
            for forbidden in ('warehouse_batch', 'quantity', 'price_per_unit'):
                if forbidden in initial:
                    self._raise_sale_error(
                        'ROOT_FIELDS_FORBIDDEN',
                        f'Поле {forbidden} в корне не используется. Передавайте данные только в sale_lines.',
                        field=forbidden,
                    )
            shipping_target = True
            normalized_lines = [
                self._validate_sale_line_payload(row, shipping_target=shipping_target)
                for row in lines
            ]
            if not attrs.get('product'):
                attrs['product'] = normalized_lines[0]['product']

        wb = attrs.get('warehouse_batch')
        prod = attrs.get('product')
        must_validate_product = self.instance is None or ('product' in attrs) or ('warehouse_batch' in attrs)
        if must_validate_product:
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
        ret['unit_type'] = instance.sale_mode
        for key in ('quantity', 'sold_pieces', 'sold_packages', 'length_per_piece', 'total_meters', 'price', 'revenue', 'cost', 'profit'):
            if key in ret and ret[key] is not None:
                ret[key] = api_decimal_str(Decimal(str(ret[key])))
        from .payment_status import sale_payment_metrics
        ret['paid_amount'] = api_decimal_str(sale_payment_metrics(instance)['paid_amount'])
        ret['order_paid_amount_applied'] = api_decimal_str(Decimal(str(instance.order_paid_amount_applied or 0)))
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
        # Служебные поля запроса не являются полями модели Sale.
        validated_data.pop('unit_type', None)
        validated_data.pop('payment_type', None)
        validated_data.pop('payment_method', None)
        validated_data.pop('paid_amount', None)
        requested_order_applied = validated_data.pop('order_paid_amount_applied', None)

        lines_payload = (self.initial_data or {}).get('sale_lines') or []
        top_level_unit_type_raw = (
            (self.initial_data or {}).get('unit_type')
            or validated_data.get('sale_mode')
            or Sale.MODE_PIECES
        )
        top_level_unit_type = str(top_level_unit_type_raw).strip().lower()
        if top_level_unit_type not in (Sale.MODE_PIECES, Sale.MODE_PACKAGES):
            top_level_unit_type = Sale.MODE_PIECES
        unit_type = top_level_unit_type
        linked_order = validated_data.get('linked_order')
        sale_client = validated_data.get('client')
        if linked_order is not None and sale_client is not None and linked_order.client_id != sale_client.id:
            self._raise_sale_error(
                'ORDER_CLIENT_MISMATCH',
                'Заявка принадлежит другому клиенту.',
                field='order',
            )
        normalized_lines = []
        for line_idx, row in enumerate(lines_payload):
            row_in = dict(row or {})
            line_unit_type_raw = row_in.get('unit_type')
            if line_unit_type_raw in (None, ''):
                line_unit_type = top_level_unit_type
            else:
                line_unit_type = str(line_unit_type_raw).strip().lower()
            if line_unit_type not in (Sale.MODE_PIECES, Sale.MODE_PACKAGES):
                self._raise_sale_line_error(
                    'INVALID_LINE_UNIT_TYPE',
                    'sale_lines[].unit_type: pieces | packages',
                    line_idx=line_idx,
                    field_name='unit_type',
                )
            try:
                input_qty = Decimal(str(row_in.get('quantity') or 0))
            except (InvalidOperation, TypeError, ValueError):
                self._raise_sale_error(
                    'SALE_QUANTITY_INVALID',
                    'quantity в строке должен быть числом больше 0.',
                    field='sale_lines',
                )
            if line_unit_type == Sale.MODE_PACKAGES:
                gp_pid = row_in.get('gp_package_id')
                if gp_pid not in (None, ''):
                    try:
                        gp_int = int(gp_pid)
                    except (TypeError, ValueError):
                        self._raise_sale_line_error(
                            'INVALID_GP_PACKAGE_ID',
                            'gp_package_id должен быть целым числом.',
                            line_idx=line_idx,
                            field_name='gp_package_id',
                        )
                    gp_unit_obj = (
                        GpPackUnit.objects.select_related('warehouse_batch', 'operation')
                        .filter(pk=gp_int)
                        .first()
                    )
                    if gp_unit_obj is None:
                        self._raise_sale_line_error(
                            'GP_PACKAGE_NOT_FOUND',
                            'Упаковка gp_package_id не найдена.',
                            line_idx=line_idx,
                            field_name='gp_package_id',
                        )
                    if not gp_unit_obj.warehouse_batch_id:
                        self._raise_sale_line_error(
                            'GP_PACKAGE_NOT_ON_STOCK',
                            'Упаковка GP не привязана к партии склада.',
                            line_idx=line_idx,
                            field_name='gp_package_id',
                        )
                    wb_id_in = row_in.get('warehouse_batch')
                    if wb_id_in not in (None, ''):
                        try:
                            if int(wb_id_in) != int(gp_unit_obj.warehouse_batch_id):
                                self._raise_sale_line_error(
                                    'WAREHOUSE_BATCH_GP_MISMATCH',
                                    'warehouse_batch не совпадает с партией выбранной упаковки gp_package_id.',
                                    line_idx=line_idx,
                                    field_name='warehouse_batch',
                                )
                        except (TypeError, ValueError):
                            self._raise_sale_line_error(
                                'INVALID_WAREHOUSE_BATCH',
                                'Некорректный warehouse_batch.',
                                line_idx=line_idx,
                                field_name='warehouse_batch',
                            )
                    row_in['warehouse_batch'] = gp_unit_obj.warehouse_batch_id
                    row_in['gp_pack_unit'] = gp_unit_obj

                wb_id = row_in.get('warehouse_batch')
                if wb_id in (None, ''):
                    self._raise_sale_error(
                        'MISSING_WAREHOUSE_BATCH',
                        'Для unit_type=packages в строке обязателен warehouse_batch или gp_package_id.',
                        field='sale_lines',
                    )
                try:
                    wb_pkg = WarehouseBatch.objects.get(pk=wb_id)
                except WarehouseBatch.DoesNotExist:
                    self._raise_sale_error(
                        'MISSING_WAREHOUSE_BATCH',
                        'Указанная warehouse_batch не найдена.',
                        field='sale_lines',
                    )
                if wb_pkg.inventory_form != WarehouseBatch.INVENTORY_PACKED:
                    self._raise_sale_line_error(
                        'BATCH_NOT_AVAILABLE_FOR_UNIT_TYPE',
                        'Для unit_type=packages нужна партия с inventory_form=packed.',
                        line_idx=line_idx,
                        field_name='warehouse_batch',
                    )
                try:
                    ppp = Decimal(str(wb_pkg.pieces_per_package or 0))
                except (InvalidOperation, TypeError, ValueError):
                    ppp = Decimal('0')
                if ppp <= 0:
                    self._raise_sale_error(
                        'INVALID_PACKAGE_BATCH',
                        'Для продажи упаковками у партии должен быть pieces_per_package > 0.',
                        field='sale_lines',
                    )
                row_in['quantity'] = (input_qty * ppp).quantize(Decimal('0.0001'))
                row_in['stock_form'] = WarehouseBatch.INVENTORY_PACKED
                row_in.setdefault('piece_pick', PIECE_FROM_SEALED)
            else:
                if row_in.get('warehouse_batch') not in (None, ''):
                    try:
                        wb_pcs = WarehouseBatch.objects.get(pk=row_in.get('warehouse_batch'))
                        inv = wb_pcs.inventory_form
                        if inv == WarehouseBatch.INVENTORY_UNPACKED:
                            row_in['stock_form'] = WarehouseBatch.INVENTORY_UNPACKED
                            row_in.setdefault('piece_pick', PIECE_LOOSE)
                        elif inv == WarehouseBatch.INVENTORY_OPEN_PACKAGE:
                            row_in['stock_form'] = WarehouseBatch.INVENTORY_OPEN_PACKAGE
                            row_in.setdefault('piece_pick', PIECE_FROM_OPEN)
                        elif inv == WarehouseBatch.INVENTORY_PACKED and not row_in.get('piece_pick'):
                            row_in['stock_form'] = WarehouseBatch.INVENTORY_PACKED
                            row_in['piece_pick'] = PIECE_FROM_SEALED
                    except WarehouseBatch.DoesNotExist:
                        pass
            sld = self._validate_sale_line_payload(row_in, shipping_target=True)
            if linked_order is not None and linked_order.lines.exists():
                sld = self._bind_order_line_for_linked_order(sld, linked_order)
            up = Decimal(str(sld.get('unit_price') or 0))
            qn_input = input_qty
            sld['line_total'] = (up * qn_input).quantize(Decimal('0.01'))
            sld['_input_quantity'] = qn_input
            sld['_line_unit_type'] = line_unit_type
            normalized_lines.append(sld)

        total_qty = sum((Decimal(str(x['quantity'])) for x in normalized_lines), Decimal('0')).quantize(Decimal('0.0001'))
        total_revenue = sum((Decimal(str(x['line_total'])) for x in normalized_lines), Decimal('0')).quantize(Decimal('0.01'))
        order_paid_available = Decimal('0')
        if linked_order is not None:
            from .payment_status import order_payment_metrics
            order_paid_available = Decimal(str(order_payment_metrics(linked_order)['paid_amount'] or 0)).quantize(Decimal('0.01'))
        if requested_order_applied in (None, ''):
            order_paid_applied = min(order_paid_available, total_revenue)
        else:
            try:
                order_paid_applied = Decimal(str(requested_order_applied)).quantize(Decimal('0.01'))
            except (InvalidOperation, TypeError, ValueError):
                self._raise_sale_error(
                    'INVALID_ORDER_PAID_AMOUNT_APPLIED',
                    'order_paid_amount_applied должен быть числом.',
                    field='order_paid_amount_applied',
                )
            if order_paid_applied < 0:
                self._raise_sale_error(
                    'INVALID_ORDER_PAID_AMOUNT_APPLIED',
                    'order_paid_amount_applied не может быть отрицательным.',
                    field='order_paid_amount_applied',
                )
            if order_paid_applied > order_paid_available:
                self._raise_sale_error(
                    'ORDER_PREPAYMENT_EXCEEDED',
                    'order_paid_amount_applied превышает доступную предоплату заявки.',
                    field='order_paid_amount_applied',
                )
            if order_paid_applied > total_revenue:
                self._raise_sale_error(
                    'ORDER_PREPAYMENT_EXCEEDS_TOTAL',
                    'order_paid_amount_applied не должен превышать total_amount продажи.',
                    field='order_paid_amount_applied',
                )

        if linked_order is not None:
            self._payment_input = self._validate_sale_supplemental_payment(
                sale_total=total_revenue,
                order_prepaid=order_paid_applied,
                initial=(self.initial_data or {}),
            )
        else:
            self._payment_input = self._validate_embedded_payment(
                total_amount=total_revenue,
                initial=(self.initial_data or {}),
            )

        validated_data['quantity'] = total_qty
        validated_data['sold_pieces'] = total_qty
        has_packages = any((x.get('_line_unit_type') == Sale.MODE_PACKAGES) for x in normalized_lines)
        if has_packages:
            validated_data['sold_packages'] = sum(
                (
                    Decimal(str(x.get('_input_quantity') or 0))
                    for x in normalized_lines
                    if x.get('_line_unit_type') == Sale.MODE_PACKAGES
                ),
                Decimal('0'),
            ).quantize(Decimal('0.0001'))
        else:
            validated_data['sold_packages'] = Decimal('0')
        validated_data['sale_mode'] = normalized_lines[0].get('_line_unit_type', unit_type) if normalized_lines else unit_type
        validated_data['price'] = (total_revenue / total_qty).quantize(Decimal('0.01')) if total_qty > 0 else Decimal('0')
        validated_data['revenue'] = total_revenue
        validated_data['cost'] = Decimal('0')
        validated_data['profit'] = total_revenue
        validated_data['order_paid_amount_applied'] = order_paid_applied
        validated_data['warehouse_batch'] = None
        validated_data['sale_status'] = validated_data.get('sale_status') or Sale.STATUS_DRAFT
        shipping = True
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

        validated_data['stock_quality'] = WarehouseBatch.QUALITY_GOOD
        from .sale_warehouse import apply_warehouse_for_sale, sale_requires_warehouse_apply
        from .reservations import auto_fulfill_sale_lines_after_shipping
        from .state_machine import validate_sale_ship
        sale_model_fields = {f.name for f in Sale._meta.concrete_fields}
        create_payload = {k: v for k, v in validated_data.items() if k in sale_model_fields}
        with transaction.atomic():
            try:
                instance = super().create(create_payload)
            except TypeError as exc:
                self._raise_sale_error(
                    'INVALID_SALE_PAYLOAD',
                    f'Некорректные поля для создания продажи: {exc}',
                )
            for sld in normalized_lines:
                sld.pop('_input_quantity', None)
                sld.pop('_line_unit_type', None)
                sld.pop('unit_type', None)
                sld.pop('gp_package_id', None)
                sld['cost'] = Decimal('0')
                sld['profit'] = Decimal(str(sld['line_total'] or 0))
                allowed_sl = {
                    'product', 'warehouse_batch', 'order_line', 'stock_form', 'piece_pick',
                    'quantity', 'unit_price', 'line_total', 'cost', 'profit', 'defect_flag', 'comment', 'gp_pack_unit',
                }
                create_kwargs = {k: sld[k] for k in allowed_sl if k in sld}
                create_kwargs['sale'] = instance
                SaleLine.objects.create(**create_kwargs)
            instance = Sale.objects.select_for_update().get(pk=instance.pk)
            if not instance.sale_lines.exists():
                raise serializers.ValidationError(
                    {'sale_lines': 'Должна быть минимум одна строка продажи (sale_lines).'},
                )
            try:
                validate_sale_ship(instance)
            except ValueError as e:
                raise serializers.ValidationError({'non_field_errors': [str(e)]})
            try:
                applied = apply_warehouse_for_sale(instance)
            except (ValueError, DrfValidationError) as e:
                msg = getattr(e, 'detail', e) if isinstance(e, DrfValidationError) else str(e)
                raise serializers.ValidationError({'non_field_errors': [str(msg)]})
            if sale_requires_warehouse_apply(instance) and not applied:
                self._raise_sale_error(
                    'WAREHOUSE_NOT_APPLIED',
                    'Не удалось списать склад по строкам продажи.',
                )
            if linked_order is not None:
                auto_fulfill_sale_lines_after_shipping(
                    sale=instance,
                    order=linked_order,
                    user=user,
                    request=request,
                )
                from .order_sync import sync_order_shipping_status
                sync_order_shipping_status(linked_order)

            payment_input = getattr(self, '_payment_input', (None, None, Decimal('0')))
            pay_type, pay_method, pay_amount = payment_input
            supplemental_pay = Decimal(str(pay_amount or 0)).quantize(Decimal('0.01'))
            if pay_type in (self.PAYMENT_FULL, self.PAYMENT_PARTIAL) and supplemental_pay > 0:
                Payment.objects.create(
                    date=instance.date,
                    client=instance.client,
                    linked_order=instance.linked_order,
                    linked_sale=instance,
                    payment_type=Payment.TYPE_PAYMENT,
                    amount=supplemental_pay,
                    payment_method=(
                        Payment.METHOD_CASH if pay_method == 'cash'
                        else (Payment.METHOD_CARD if pay_method == 'card' else Payment.METHOD_TRANSFER)
                    ),
                    status=Payment.STATUS_ACTIVE,
                    created_by=user if getattr(user, 'is_authenticated', False) else None,
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
        from .sale_warehouse import apply_warehouse_for_sale, sale_requires_warehouse_apply
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

def sale_return_source_is_valid(sale: Sale) -> bool:
    if sale.sale_status == Sale.STATUS_CANCELED:
        return False
    if sale.sale_status == Sale.STATUS_DRAFT and not sale.warehouse_stock_applied:
        return False
    return True


def sale_line_returned_quantity(sale_line_id: int, *, exclude_return_id: int | None = None) -> Decimal:
    qs = ReturnLine.objects.filter(
        sale_line_id=sale_line_id,
        return_doc__status=Return.STATUS_COMPLETED,
    )
    if exclude_return_id is not None:
        qs = qs.exclude(return_doc_id=exclude_return_id)
    return Decimal(str(qs.aggregate(s=Sum('quantity'))['s'] or 0))


def return_document_amount(ret_doc: Return) -> Decimal:
    total = Decimal('0')
    for line in ret_doc.lines.select_related('sale_line').all():
        unit_price = Decimal(str(line.sale_line.unit_price or 0)) if line.sale_line_id else Decimal('0')
        qty = Decimal(str(line.quantity or 0))
        total += unit_price * qty
    return total.quantize(Decimal('0.01'))


def sale_original_total_amount(sale: Sale) -> Decimal:
    lines = list(sale.sale_lines.all())
    if lines:
        return sum((Decimal(str(line.line_total or 0)) for line in lines), Decimal('0')).quantize(Decimal('0.01'))
    return Decimal(str(sale.revenue or 0)).quantize(Decimal('0.01'))


def sale_returned_amount(sale: Sale) -> Decimal:
    total = Decimal('0')
    for line in sale.sale_lines.all():
        unit_price = Decimal(str(line.unit_price or 0))
        returned_qty = sale_line_returned_quantity(line.pk)
        total += unit_price * returned_qty
    return total.quantize(Decimal('0.01'))


def sale_active_total_amount(sale: Sale) -> Decimal:
    return max(Decimal('0'), sale_original_total_amount(sale) - sale_returned_amount(sale)).quantize(Decimal('0.01'))


def sale_has_returnable_lines(sale: Sale) -> bool:
    if not sale_return_source_is_valid(sale):
        return False
    for line in sale.sale_lines.all():
        sold = Decimal(str(line.quantity or 0))
        returned = sale_line_returned_quantity(line.pk)
        if sold - returned > 0:
            return True
    return False


class ReturnLineSerializer(serializers.ModelSerializer):
    sale_line_label = serializers.SerializerMethodField()
    sale_line_id = serializers.IntegerField(source='sale_line.id', read_only=True)
    sale_line_sale_id = serializers.IntegerField(source='sale_line.sale_id', read_only=True)

    class Meta:
        model = ReturnLine
        fields = (
            'id', 'sale_line', 'sale_line_id', 'product', 'quantity',
            'return_target', 'condition_type',
            'rework_receipt_batch',
            'sale_line_label', 'sale_line_sale_id',
        )
        extra_kwargs = {
            'sale_line': {'required': True, 'allow_null': False},
            'product': {'read_only': True},
        }

    @staticmethod
    def _raise_return_line_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'detail': message,
                'errors': [{'field': field, 'message': message}],
            },
        )

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = getattr(exc, 'detail', {})
            if isinstance(detail, dict):
                if 'return_target' in detail:
                    self._raise_return_line_error(
                        'INVALID_RETURN_TARGET',
                        'Некорректный return_target. Допустимо: warehouse, rework.',
                        field='return_target',
                    )
                if 'condition_type' in detail:
                    self._raise_return_line_error(
                        'INVALID_CONDITION_TYPE',
                        'Некорректный condition_type. Допустимо: good, damaged.',
                        field='condition_type',
                    )
            raise

    def validate_return_target(self, value):
        if value == ReturnLine.TARGET_DEFECT:
            raise serializers.ValidationError('invalid')
        if value not in (ReturnLine.TARGET_WAREHOUSE, ReturnLine.TARGET_REWORK):
            raise serializers.ValidationError('invalid')
        return value

    def validate_condition_type(self, value):
        if value == ReturnLine.CONDITION_DEFECT:
            return ReturnLine.CONDITION_DAMAGED
        if value not in (ReturnLine.CONDITION_GOOD, ReturnLine.CONDITION_DAMAGED):
            raise serializers.ValidationError('invalid')
        return value

    def validate(self, attrs):
        if self.instance and self.instance.return_doc.status == Return.STATUS_COMPLETED:
            self._raise_return_line_error(
                'RETURN_LINE_UPDATE_FORBIDDEN',
                'Строку проведённого возврата нельзя изменять.',
            )
        sale_line = attrs.get('sale_line')
        if sale_line is None:
            self._raise_return_line_error('MISSING_SALE_LINE', 'Поле sale_line обязательно.', field='sale_line')
        qty = attrs.get('quantity')
        if qty is None:
            self._raise_return_line_error('INVALID_QUANTITY', 'Поле quantity обязательно.', field='quantity')
        qty_d = Decimal(str(qty))
        if qty_d <= 0:
            self._raise_return_line_error('INVALID_QUANTITY', 'quantity должно быть больше 0.', field='quantity')
        if not self.instance:
            total_returned = sum(
                rl.quantity
                for rl in ReturnLine.objects.filter(
                    sale_line=sale_line,
                    return_doc__status=Return.STATUS_COMPLETED,
                )
            )
            if total_returned + qty_d > sale_line.quantity:
                self._raise_return_line_error(
                    'RETURN_QUANTITY_EXCEEDED',
                    (
                        'Нельзя вернуть больше, чем было отгружено по строке '
                        f'(отгружено: {sale_line.quantity}, уже возвращено: {total_returned}).'
                    ),
                    field='quantity',
                )
        return attrs

    def get_sale_line_label(self, obj):
        sl = getattr(obj, 'sale_line', None)
        if sl is None:
            return ''
        return f'{sl.product} × {api_decimal_str(sl.quantity)}'

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if ret.get('return_target') == ReturnLine.TARGET_DEFECT:
            ret['return_target'] = ReturnLine.TARGET_REWORK
        if ret.get('condition_type') == ReturnLine.CONDITION_DEFECT:
            ret['condition_type'] = ReturnLine.CONDITION_DAMAGED
        return ret


class ReturnSerializer(serializers.ModelSerializer):
    lines = ReturnLineSerializer(many=True, required=False)
    sale_order_number = serializers.CharField(source='sale.order_number', read_only=True)
    display = serializers.SerializerMethodField()
    sale_id = serializers.IntegerField(source='sale.id', read_only=True)
    sale_display = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.name', read_only=True, allow_null=True, default='')
    total_refund_amount = serializers.SerializerMethodField()
    downstream_links = serializers.SerializerMethodField()

    class Meta:
        model = Return
        fields = (
            'id', 'display', 'return_number', 'date', 'status',
            'sale', 'sale_id', 'sale_order_number', 'sale_display',
            'linked_order', 'invoice_number',
            'return_reason',
            'created_by', 'created_by_name', 'created_at',
            'lines', 'client_name', 'total_refund_amount', 'downstream_links',
        )
        read_only_fields = (
            'return_number', 'created_at', 'sale_order_number',
            'display', 'sale_id', 'sale_display', 'client_name',
            'total_refund_amount', 'downstream_links',
        )
        extra_kwargs = {
            'linked_order': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
        }

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = getattr(exc, 'detail', {})
            if isinstance(detail, dict):
                if 'sale' in detail:
                    self._raise_return_error('MISSING_SALE', 'Поле sale обязательно.', field='sale')
                if 'lines' in detail:
                    lines_detail = detail.get('lines')
                    text = str(lines_detail)
                    if 'Нельзя вернуть больше' in text:
                        self._raise_return_error(
                            'RETURN_QUANTITY_EXCEEDED',
                            'Нельзя вернуть больше, чем было отгружено по строке продажи.',
                            field='lines',
                        )
                    if 'sale_line' in text:
                        self._raise_return_error('MISSING_SALE_LINE', 'Поле sale_line обязательно.', field='lines')
                    if 'quantity' in text:
                        self._raise_return_error('INVALID_QUANTITY', 'Поле quantity обязательно и должно быть > 0.', field='lines')
                    if 'return_target' in text:
                        self._raise_return_error(
                            'INVALID_RETURN_TARGET',
                            'Некорректный return_target. Допустимо: warehouse, rework.',
                            field='lines',
                        )
                    if 'condition_type' in text:
                        self._raise_return_error(
                            'INVALID_CONDITION_TYPE',
                            'Некорректный condition_type. Допустимо: good, damaged.',
                            field='lines',
                        )
                    self._raise_return_error('MISSING_LINES', 'Нужна минимум одна строка возврата (lines).', field='lines')
            raise

    @staticmethod
    def _raise_return_error(code: str, message: str, field: str = 'non_field_errors'):
        raise serializers.ValidationError(
            {
                'code': code,
                'detail': message,
                'errors': [{'field': field, 'message': message}],
            },
        )

    def _validate_line_payload(self, line_data: dict, *, sale: Sale, exclude_return_id: int | None = None) -> dict:
        payload = dict(line_data or {})
        sale_line = payload.get('sale_line')
        if sale_line is None:
            self._raise_return_error('MISSING_SALE_LINE', 'Поле sale_line обязательно.', field='lines')
        if not isinstance(sale_line, SaleLine):
            try:
                sale_line = SaleLine.objects.get(pk=sale_line)
            except SaleLine.DoesNotExist:
                self._raise_return_error('MISSING_SALE_LINE', 'Указанная sale_line не найдена.', field='lines')
        if sale_line.sale_id != sale.pk:
            self._raise_return_error(
                'SALE_LINE_NOT_IN_SALE',
                'sale_line не принадлежит выбранной sale.',
                field='lines',
            )

        qty = payload.get('quantity')
        if qty in (None, ''):
            self._raise_return_error('INVALID_QUANTITY', 'Поле quantity обязательно.', field='lines')
        qty_d = Decimal(str(qty))
        if qty_d <= 0:
            self._raise_return_error('INVALID_QUANTITY', 'quantity должно быть больше 0.', field='lines')

        total_returned = sale_line_returned_quantity(sale_line.pk, exclude_return_id=exclude_return_id)
        if total_returned + qty_d > Decimal(str(sale_line.quantity or 0)):
            self._raise_return_error(
                'RETURN_QUANTITY_EXCEEDED',
                (
                    'Нельзя вернуть больше, чем было отгружено по строке '
                    f'(отгружено: {sale_line.quantity}, уже возвращено: {total_returned}).'
                ),
                field='lines',
            )

        rt = payload.get('return_target', ReturnLine.TARGET_WAREHOUSE)
        ct = payload.get('condition_type', ReturnLine.CONDITION_GOOD)
        if rt == ReturnLine.TARGET_DEFECT:
            self._raise_return_error(
                'INVALID_RETURN_TARGET',
                'Некорректный return_target. Допустимо: warehouse, rework.',
                field='lines',
            )
        if rt not in (ReturnLine.TARGET_WAREHOUSE, ReturnLine.TARGET_REWORK):
            self._raise_return_error(
                'INVALID_RETURN_TARGET',
                'Некорректный return_target. Допустимо: warehouse, rework.',
                field='lines',
            )
        if ct == ReturnLine.CONDITION_DEFECT:
            ct = ReturnLine.CONDITION_DAMAGED
        if ct not in (ReturnLine.CONDITION_GOOD, ReturnLine.CONDITION_DAMAGED):
            self._raise_return_error(
                'INVALID_CONDITION_TYPE',
                'Некорректный condition_type. Допустимо: good, damaged.',
                field='lines',
            )
        ct = (
            ReturnLine.CONDITION_GOOD
            if rt == ReturnLine.TARGET_WAREHOUSE
            else ReturnLine.CONDITION_DAMAGED
        )

        payload['sale_line'] = sale_line
        payload['quantity'] = qty_d
        payload['product'] = sale_line.product
        payload['return_target'] = rt
        payload['condition_type'] = ct
        return payload

    def _validate_unique_payload_lines(self, lines: list[dict]) -> None:
        seen: set[int] = set()
        for line_data in lines:
            sale_line = (line_data or {}).get('sale_line')
            sale_line_id = sale_line.pk if isinstance(sale_line, SaleLine) else sale_line
            if sale_line_id in (None, ''):
                continue
            try:
                sale_line_id = int(sale_line_id)
            except (TypeError, ValueError):
                continue
            if sale_line_id in seen:
                self._raise_return_error(
                    'RETURN_QUANTITY_EXCEEDED',
                    'Одна строка продажи не может быть добавлена в возврат дважды.',
                    field='lines',
                )
            seen.add(sale_line_id)

    def get_display(self, obj):
        return f'Возврат №{obj.return_number or obj.id}'

    def get_sale_display(self, obj):
        if not obj.sale_id:
            return ''
        return f'Продажа №{obj.sale.sale_number or obj.sale.order_number or obj.sale_id}'

    def get_client_name(self, obj):
        if obj.sale and obj.sale.client:
            return obj.sale.client.name
        return ''

    def get_total_refund_amount(self, obj):
        return api_decimal_str(return_document_amount(obj))

    def get_downstream_links(self, obj):
        payments = Payment.objects.filter(
            linked_return=obj,
            payment_type=Payment.TYPE_REFUND,
            status=Payment.STATUS_ACTIVE,
        ).order_by('id')
        return [
            {
                'type': 'payment',
                'id': p.id,
                'label': f'Возврат денег №{p.payment_number or p.id} — {api_decimal_str(Decimal(str(p.amount or 0)))} сом',
            }
            for p in payments
        ]

    def validate(self, attrs):
        initial = self.initial_data or {}
        is_create = self.instance is None

        if is_create:
            if 'status' in initial:
                self._raise_return_error(
                    'RETURN_STATUS_CREATE_FORBIDDEN',
                    'При создании статус передавать нельзя. Возврат создается как draft.',
                    field='status',
                )
            if 'sale' not in initial or initial.get('sale') in (None, '', 'null'):
                self._raise_return_error('MISSING_SALE', 'Поле sale обязательно.', field='sale')

        if not is_create and 'status' in initial:
            self._raise_return_error(
                'RETURN_STATUS_UPDATE_FORBIDDEN',
                'Статус возврата меняется только через complete/cancel.',
                field='status',
            )

        sale = attrs.get('sale', self.instance.sale if self.instance else None)
        if sale is None:
            self._raise_return_error('MISSING_SALE', 'Поле sale обязательно.', field='sale')
        if is_create and not sale_has_returnable_lines(sale):
            self._raise_return_error(
                'INVALID_SALE_STATUS',
                'Продажа недоступна для возврата.',
                field='sale',
            )

        if is_create:
            lines = initial.get('lines')
            if not lines or not isinstance(lines, list) or len(lines) < 1:
                self._raise_return_error('MISSING_LINES', 'Нужна минимум одна строка возврата (lines).', field='lines')
            self._validate_unique_payload_lines(lines)
            for line_data in lines:
                self._validate_line_payload(line_data, sale=sale)
        else:
            if self.instance.status == Return.STATUS_CANCELED:
                self._raise_return_error('RETURN_UPDATE_FORBIDDEN', 'Отмененный возврат нельзя редактировать.', field='status')
            if self.instance.status == Return.STATUS_COMPLETED:
                allowed = {'return_reason', 'invoice_number'}
                for key in initial:
                    if key not in allowed:
                        self._raise_return_error(
                            'RETURN_UPDATE_FORBIDDEN',
                            'У проведенного возврата можно менять только return_reason и invoice_number.',
                            field=key,
                        )
            if self.instance.status == Return.STATUS_DRAFT:
                lines = initial.get('lines')
                if lines is not None:
                    if not isinstance(lines, list) or len(lines) < 1:
                        self._raise_return_error('MISSING_LINES', 'Нужна минимум одна строка возврата (lines).', field='lines')
                    self._validate_unique_payload_lines(lines)
                    base_sale = attrs.get('sale', self.instance.sale)
                    for line_data in lines:
                        self._validate_line_payload(line_data, sale=base_sale, exclude_return_id=self.instance.pk)
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
                normalized = self._validate_line_payload(line_data, sale=ret_doc.sale)
                ReturnLine.objects.create(
                    return_doc=ret_doc,
                    sale_line=normalized['sale_line'],
                    product=normalized['product'],
                    quantity=normalized['quantity'],
                    return_target=normalized.get('return_target', ReturnLine.TARGET_WAREHOUSE),
                    condition_type=normalized.get('condition_type', ReturnLine.CONDITION_GOOD),
                )
        return ret_doc

    def update(self, instance, validated_data):
        validated_data.pop('lines', None)
        if instance.status == Return.STATUS_COMPLETED:
            validated_data = {
                k: v for k, v in validated_data.items()
                if k in {'return_reason', 'invoice_number'}
            }
            return super().update(instance, validated_data)
        if instance.status == Return.STATUS_CANCELED:
            self._raise_return_error('RETURN_UPDATE_FORBIDDEN', 'Отмененный возврат нельзя редактировать.', field='status')
        lines_payload = (self.initial_data or {}).get('lines')
        if lines_payload is not None and instance.status == Return.STATUS_DRAFT:
            base_sale = validated_data.get('sale', instance.sale)
            with transaction.atomic():
                obj = super().update(instance, validated_data)
                obj.lines.all().delete()
                for line_data in lines_payload:
                    normalized = self._validate_line_payload(line_data, sale=base_sale, exclude_return_id=obj.pk)
                    ReturnLine.objects.create(
                        return_doc=obj,
                        sale_line=normalized['sale_line'],
                        product=normalized['product'],
                        quantity=normalized['quantity'],
                        return_target=normalized.get('return_target', ReturnLine.TARGET_WAREHOUSE),
                        condition_type=normalized.get('condition_type', ReturnLine.CONDITION_GOOD),
                    )
                return obj
        return super().update(instance, validated_data)

    def apply_completion_effects(self, ret_doc: Return) -> None:
        """Склад / брак / переделка — только при проведении возврата (после complete)."""
        lines = list(ret_doc.lines.all().select_related('sale_line', 'sale_line__warehouse_batch'))
        requested_by_line: dict[int, Decimal] = {}
        for line in lines:
            if not line.sale_line_id:
                self._raise_return_error('MISSING_SALE_LINE', 'Поле sale_line обязательно.', field='lines')
            requested_by_line[line.sale_line_id] = (
                requested_by_line.get(line.sale_line_id, Decimal('0'))
                + Decimal(str(line.quantity or 0))
            )
        if len(requested_by_line) != len(lines):
            self._raise_return_error(
                'RETURN_QUANTITY_EXCEEDED',
                'Одна строка продажи не может быть добавлена в возврат дважды.',
                field='lines',
            )
        for sale_line_id, requested_qty in requested_by_line.items():
            sale_line = SaleLine.objects.get(pk=sale_line_id)
            returned = sale_line_returned_quantity(sale_line_id, exclude_return_id=ret_doc.pk)
            if Decimal(str(returned)) + requested_qty > Decimal(str(sale_line.quantity or 0)):
                self._raise_return_error(
                    'RETURN_QUANTITY_EXCEEDED',
                    'Нельзя вернуть больше доступного количества на момент проведения.',
                    field='lines',
                )
        for line in lines:
            self._validate_line_payload(
                {
                    'sale_line': line.sale_line_id,
                    'quantity': line.quantity,
                    'return_target': line.return_target,
                    'condition_type': line.condition_type,
                },
                sale=ret_doc.sale,
                exclude_return_id=ret_doc.pk,
            )
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
            sl = line.sale_line
            src_wb = None
            if sl is not None and sl.warehouse_batch_id:
                src_wb = sl.warehouse_batch
            elif ret_doc.sale_id and ret_doc.sale.warehouse_batch_id:
                src_wb = ret_doc.sale.warehouse_batch
            qp = q4(Decimal(str(line.quantity)))
            product_name = (
                (line.product or '').strip()
                or (sl.product if sl else '')
                or (ret_doc.sale.product if ret_doc.sale_id else '')
                or '—'
            )
            quality = (
                WarehouseBatch.QUALITY_DEFECT
                if line.condition_type in (ReturnLine.CONDITION_DAMAGED, ReturnLine.CONDITION_DEFECT)
                else WarehouseBatch.QUALITY_GOOD
            )
            inv = src_wb.inventory_form if src_wb else WarehouseBatch.INVENTORY_UNPACKED
            wb = WarehouseBatch.objects.create(
                product=product_name[:255],
                quantity=qp,
                date=ret_doc.date,
                status=WarehouseBatch.STATUS_AVAILABLE,
                quality=quality,
                inventory_form=inv,
                stock_bucket=WarehouseBatch.STOCK_BUCKET_REWORKED,
                profile_id=src_wb.profile_id if src_wb else None,
                length_per_piece=src_wb.length_per_piece if src_wb else None,
                unit_meters=src_wb.unit_meters if src_wb else None,
                package_total_meters=src_wb.package_total_meters if src_wb else None,
                pieces_per_package=src_wb.pieces_per_package if src_wb else None,
                packages_count=src_wb.packages_count if src_wb else None,
                source_batch=None,
            )
            line.rework_receipt_batch = wb
            line.save(update_fields=['rework_receipt_batch'])


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
            'created_by', 'created_by_name', 'created_at', 'updated_at',
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
            'warehouse_batch': {'required': False, 'allow_null': True, 'validators': []},
        }

    @staticmethod
    def _raise_defect_update_forbidden():
        message = 'Статус и счётчики брака меняются только через действия.'
        raise serializers.ValidationError(
            {
                'code': 'DEFECT_UPDATE_FORBIDDEN',
                'detail': message,
                'errors': [{'field': 'non_field_errors', 'message': message}],
            },
        )

    def validate(self, attrs):
        protected_fields = {
            'quantity_pcs',
            'available_quantity_pcs',
            'original_quantity_pcs',
            'sold_quantity_pcs',
            'written_off_quantity_pcs',
            'sent_to_rework_quantity_pcs',
            'status',
        }
        if self.instance is not None and any(f in self.initial_data for f in protected_fields):
            self._raise_defect_update_forbidden()

        status = attrs.get('status', self.instance.status if self.instance else DefectRecord.STATUS_NEW)
        if status == DefectRecord.STATUS_WRITTEN_OFF:
            if not attrs.get('writeoff_reason') and not (self.instance and self.instance.writeoff_reason):
                raise serializers.ValidationError(
                    {'writeoff_reason': 'Причина списания обязательна при статусе «списан»'}
                )
        if self.instance is not None:
            incoming = set((self.initial_data or {}).keys())
            allowed_patch = {'defect_reason', 'kg_coefficient'}
            forbidden = incoming - allowed_patch
            if forbidden:
                raise serializers.ValidationError(
                    {
                        'code': 'DEFECT_PATCH_FORBIDDEN',
                        'detail': 'Разрешено менять только defect_reason.',
                        'errors': [{'field': k, 'message': 'Поле недоступно для изменения'} for k in sorted(forbidden)],
                    },
                )

        if self.instance is None:
            source_type = attrs.get('source_type')
            if source_type != DefectRecord.SOURCE_WAREHOUSE:
                raise serializers.ValidationError(
                    {
                        'code': 'INVALID_SOURCE_TYPE',
                        'detail': 'Создание через API только со склада ГП: source_type=warehouse.',
                        'errors': [{'field': 'source_type', 'message': 'Ожидается warehouse'}],
                    },
                )
            wb = attrs.get('warehouse_batch')
            if wb is None:
                raise serializers.ValidationError({'warehouse_batch': 'Поле warehouse_batch обязательно'})
            qp = attrs.get('quantity_pcs')
            try:
                qp_d = Decimal(str(qp))
            except Exception:
                qp_d = Decimal('-1')
            if qp is None or qp_d <= 0 or qp_d % Decimal('1') != 0:
                raise serializers.ValidationError(
                    {'quantity_pcs': 'Укажите целое количество штук > 0'},
                )
            if not (attrs.get('defect_reason') or '').strip():
                raise serializers.ValidationError({'defect_reason': 'Укажите причину брака'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('source_id', None)
        validated_data.pop('source_type', None)
        wb = validated_data.pop('warehouse_batch')
        qp = q4(Decimal(str(validated_data.pop('quantity_pcs'))))
        reason = (validated_data.pop('defect_reason') or '').strip()
        try:
            return create_defect_split_from_good_batch(
                source_batch=wb,
                quantity_pcs=qp,
                defect_reason=reason,
            )
        except ValueError as e:
            raise serializers.ValidationError({'non_field_errors': [str(e)]})

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


def _reserve_defect_for_rework_explicit(
    d: DefectRecord,
    send_pcs: Decimal,
    rework_kg: Decimal,
    *,
    eps: Decimal = Decimal('0.0001'),
) -> tuple[Decimal, Decimal]:
    """
    Списать с DefectRecord ровно send_pcs шт и зафиксировать rework_kg на заявке (d под select_for_update).
    Не вызывает save().
    """
    from .state_machine import validate_defect_transition

    if ReworkRequest.objects.filter(
        defect_record_id=d.pk,
        status__in=(ReworkRequest.STATUS_PENDING, ReworkRequest.STATUS_IN_PROGRESS),
    ).exists():
        raise ValueError('По этому браку уже есть активная переделка')

    if rework_kg <= 0:
        raise ValueError('quantity_kg должно быть > 0')

    sp = q4(Decimal(str(send_pcs)))
    if sp <= 0 or sp % Decimal('1') != 0:
        raise ValueError('quantity_pcs должно быть целым числом > 0')

    rem_pcs = Decimal(str(d.quantity_pcs or 0))
    if sp > rem_pcs + eps:
        raise ValueError('quantity_pcs превышает доступный остаток по браку')

    kg_raw = d.quantity_kg
    kg_before_d = Decimal(str(kg_raw)) if kg_raw is not None else Decimal('0')
    rem_before = rem_pcs

    d.sent_to_rework_quantity_pcs = Decimal(str(d.sent_to_rework_quantity_pcs or 0)) + sp
    d.recompute_remaining_pcs()
    if kg_before_d > 0 and rem_before > 0:
        kg_delta = (sp / rem_before * kg_before_d).quantize(Decimal('0.0001'))
        d.quantity_kg = max(Decimal('0'), (kg_before_d - kg_delta).quantize(Decimal('0.0001')))
    if sp >= rem_before - eps:
        try:
            validate_defect_transition(d.status, DefectRecord.STATUS_SENT_TO_REWORK)
        except ValueError:
            pass

    d.apply_terminal_status_from_counters()
    return sp, rework_kg


def _create_reworked_warehouse_batch_from_defect(
    defect: DefectRecord,
    quantity_kg: Decimal,
    result_name: str,
) -> WarehouseBatch:
    tpl = getattr(defect, 'warehouse_batch', None)
    return WarehouseBatch.objects.create(
        profile_id=defect.profile_id,
        product=(result_name or '').strip() or 'Переделанный материал',
        length_per_piece=None,
        quantity=q4(quantity_kg),
        cost_per_piece=tpl.cost_per_piece if tpl else Decimal('0'),
        cost_per_meter=tpl.cost_per_meter if tpl else Decimal('0'),
        date=timezone.now().date(),
        source_batch_id=tpl.source_batch_id if tpl else None,
        inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        stock_bucket=WarehouseBatch.STOCK_BUCKET_REWORKED,
        quality=WarehouseBatch.QUALITY_GOOD,
    )


def _reserve_defect_from_kg_only(
    d: DefectRecord,
    quantity_kg: Decimal,
    *,
    eps: Decimal = Decimal('0.0001'),
) -> tuple[Decimal | None, Decimal]:
    """Legacy: только quantity_kg в теле (пропорционально шт). Для POST …/defects/…/send-to-rework/."""
    from .state_machine import validate_defect_transition

    if ReworkRequest.objects.filter(
        defect_record_id=d.pk,
        status__in=(ReworkRequest.STATUS_PENDING, ReworkRequest.STATUS_IN_PROGRESS),
    ).exists():
        raise ValueError('По этому браку уже есть активная переделка')

    if quantity_kg <= 0:
        raise ValueError('quantity_kg должно быть > 0')

    rem_pcs = Decimal(str(d.quantity_pcs or 0))
    kg_raw = d.quantity_kg
    kg_before_d = Decimal(str(kg_raw)) if kg_raw is not None else Decimal('0')
    coeff_raw = d.kg_coefficient
    coeff = Decimal(str(coeff_raw)) if coeff_raw is not None else Decimal('0')

    send_pcs: Decimal | None = None

    if kg_before_d > 0 and rem_pcs > 0:
        if quantity_kg > kg_before_d + eps:
            raise ValueError('quantity_kg превышает остаток кг по браку')
        send_pcs = (quantity_kg / kg_before_d * rem_pcs).quantize(Decimal('0.0001'))
    elif kg_before_d > 0 and rem_pcs <= 0:
        if quantity_kg > kg_before_d + eps:
            raise ValueError('quantity_kg превышает остаток кг по браку')
        send_pcs = None
    elif rem_pcs > 0:
        if coeff <= 0:
            raise ValueError(
                'Для записи брака без quantity_kg задайте kg_coefficient (> 0), чтобы передавать quantity_kg в кг',
            )
        max_kg = (rem_pcs * coeff).quantize(Decimal('0.0001'))
        if quantity_kg > max_kg + eps:
            raise ValueError('quantity_kg превышает остаток по браку (кг)')
        send_pcs = (quantity_kg / coeff).quantize(Decimal('0.0001'))
    else:
        raise ValueError('Нет остатка для переделки по этой записи брака')

    if send_pcs is not None:
        if send_pcs <= 0:
            raise ValueError('Расчётное количество для переделки должно быть > 0')
        if send_pcs > rem_pcs + eps:
            send_pcs = rem_pcs

        rem_before = rem_pcs
        d.sent_to_rework_quantity_pcs = Decimal(str(d.sent_to_rework_quantity_pcs or 0)) + send_pcs
        d.recompute_remaining_pcs()
        if kg_before_d > 0 and rem_before > 0:
            kg_delta = (send_pcs / rem_before * kg_before_d).quantize(Decimal('0.0001'))
            d.quantity_kg = max(Decimal('0'), (kg_before_d - kg_delta).quantize(Decimal('0.0001')))
        if send_pcs >= rem_before - eps:
            try:
                validate_defect_transition(d.status, DefectRecord.STATUS_SENT_TO_REWORK)
            except ValueError:
                pass
    else:
        d.quantity_kg = max(Decimal('0'), (kg_before_d - quantity_kg).quantize(Decimal('0.0001')))

    d.apply_terminal_status_from_counters()
    return send_pcs, quantity_kg


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
    result_name = serializers.SerializerMethodField()
    result_warehouse_batch = serializers.SerializerMethodField()
    result_warehouse_batch_label = serializers.SerializerMethodField()
    result_warehouse_batch_id = serializers.IntegerField(read_only=True, allow_null=True)
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
            'product', 'result_name', 'quantity_pcs', 'quantity_kg', 'output_quantity_kg', 'loss_kg', 'conversion_rate',
            'status', 'result_warehouse_batch', 'result_warehouse_batch_id',
            'result_warehouse_batch_label',
            'rework_loss_kg', 'recovered_output',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'rework_number', 'created_at', 'updated_at', 'rework_loss_kg', 'recovered_output',
            'defect_record_id', 'defect_product_name', 'defect_quantity_pcs', 'defect_quantity_kg',
            'defect_reason', 'defect_source_type', 'defect_source_label',
            'display_quantity', 'display_quantity_label', 'result_name', 'result_warehouse_batch',
            'result_warehouse_batch_id',
        )
        extra_kwargs = {
            'return_doc': {'required': False, 'allow_null': True},
            'defect_record': {'required': False, 'allow_null': True},
            'original_sale': {'required': False, 'allow_null': True},
            'created_by': {'required': False, 'allow_null': True},
            'quantity_pcs': {'required': False, 'allow_null': True},
            'output_quantity_kg': {'required': False, 'allow_null': True},
            'loss_kg': {'required': False, 'allow_null': True},
            'conversion_rate': {'required': False, 'allow_null': True},
        }

    def to_internal_value(self, data):
        """Запятая как десятичный разделитель (напр. 12,5 кг) до парсинга DecimalField."""
        if hasattr(data, 'copy'):
            data = data.copy()
        elif isinstance(data, dict):
            data = dict(data)
        else:
            return super().to_internal_value(data)
        for k in ('quantity_pcs', 'quantity_kg'):
            if k not in data:
                continue
            v = data.get(k)
            if isinstance(v, str):
                data[k] = v.strip().replace(',', '.')
        return super().to_internal_value(data)

    def get_defect_product_name(self, obj):
        if obj.defect_record_id:
            return (obj.defect_record.product or '').strip()
        return ''

    def get_result_name(self, obj):
        return (obj.product or '').strip()

    def get_result_warehouse_batch(self, obj):
        wb = getattr(obj, 'result_warehouse_batch', None)
        if wb is None:
            return None
        qty = Decimal(str(wb.quantity or 0))
        return {
            'id': wb.pk,
            'product': wb.product,
            'product_name': wb.product,
            'quantity': api_decimal_str(qty),
            'available_quantity': api_decimal_str(qty),
            'stock_bucket': wb.stock_bucket,
            'status': wb.status,
            'date': wb.date.isoformat() if wb.date else None,
        }

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
        defect_ref = validated_data['defect_record']
        defect_pk = defect_ref.pk
        raw = getattr(self, 'initial_data', {}) or {}

        def _parse_dec(val, field: str) -> Decimal:
            if val in (None, ''):
                raise serializers.ValidationError({field: f'Обязательное поле {field}'})
            try:
                return Decimal(str(val).strip().replace(',', '.'))
            except Exception:
                raise serializers.ValidationError({field: 'Некорректное число'})

        try:
            send_pcs = _parse_dec(raw.get('quantity_pcs'), 'quantity_pcs')
            rework_kg = _parse_dec(raw.get('quantity_kg'), 'quantity_kg')
        except serializers.ValidationError:
            raise
        if send_pcs <= 0 or send_pcs % Decimal('1') != 0:
            raise serializers.ValidationError({'quantity_pcs': 'quantity_pcs должно быть целым > 0'})

        result_name = (
            str(raw.get('result_name') or '').strip()
            or (validated_data.get('product') or '').strip()
            or (defect_ref.product or '').strip()
        )
        if not result_name:
            raise serializers.ValidationError({'result_name': 'Поле result_name обязательно'})
        validated_data['product'] = result_name
        year = timezone.now().date().year
        last = ReworkRequest.objects.filter(rework_number__startswith=f'RWK-{year}-').order_by('-rework_number').first()
        try:
            last_n = int(last.rework_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        validated_data['rework_number'] = f'RWK-{year}-{last_n + 1:04d}'
        err_field = 'non_field_errors'
        try:
            with transaction.atomic():
                d = DefectRecord.objects.select_for_update().get(pk=defect_pk)
                qpcs, qkg = _reserve_defect_for_rework_explicit(d, send_pcs, rework_kg)
                if d.quantity_pcs <= Decimal('0.0001'):
                    d.status = DefectRecord.STATUS_REWORKED
                d.save(
                    update_fields=[
                        'sent_to_rework_quantity_pcs', 'quantity_pcs', 'quantity_kg',
                        'status', 'updated_at',
                    ],
                )
                wb = _create_reworked_warehouse_batch_from_defect(d, qkg, result_name)
                validated_data['defect_record'] = d
                validated_data['quantity_pcs'] = qpcs
                validated_data['quantity_kg'] = qkg
                validated_data['output_quantity_kg'] = qkg
                validated_data['loss_kg'] = Decimal('0')
                validated_data['conversion_rate'] = Decimal('1')
                validated_data['status'] = ReworkRequest.STATUS_COMPLETED
                validated_data['result_warehouse_batch'] = wb
                return super().create(validated_data)
        except ValueError as e:
            msg = str(e)
            if 'остаток' in msg or 'шт' in msg:
                err_field = 'quantity_pcs'
            elif 'кг' in msg or 'kg' in msg.lower():
                err_field = 'quantity_kg'
            raise serializers.ValidationError({err_field: [msg]})

    def validate(self, attrs):
        if self.instance is None:
            return attrs

        protected_fields = {'status', 'quantity_pcs', 'quantity_kg', 'output_quantity_kg', 'loss_kg', 'result_warehouse_batch'}
        if any(f in self.initial_data for f in protected_fields):
            message = 'Переделка меняется только через start/complete/cancel.'
            raise serializers.ValidationError(
                {
                    'code': 'REWORK_UPDATE_FORBIDDEN',
                    'detail': message,
                    'errors': [{'field': 'non_field_errors', 'message': message}],
                },
            )

        if self.instance.status in (ReworkRequest.STATUS_COMPLETED, ReworkRequest.STATUS_CANCELED):
            message = 'Переделка меняется только через start/complete/cancel.'
            raise serializers.ValidationError(
                {
                    'code': 'REWORK_UPDATE_FORBIDDEN',
                    'detail': message,
                    'errors': [{'field': 'status', 'message': message}],
                },
            )

        allowed = set()
        unknown_updates = [k for k in self.initial_data.keys() if k not in allowed]
        if unknown_updates:
            message = 'Переделка меняется только через start/complete/cancel.'
            raise serializers.ValidationError(
                {
                    'code': 'REWORK_UPDATE_FORBIDDEN',
                    'detail': message,
                    'errors': [{'field': k, 'message': message} for k in unknown_updates],
                },
            )
        return attrs

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
