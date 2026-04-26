import logging
from decimal import Decimal
from html import escape

from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse

from rest_framework import viewsets, status
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter

from apps.activity.mixins import ActivityLoggingMixin
from config.api_numbers import api_decimal_str
from config.permissions import IsAdminOrHasAccess
from .filters import ClientFilter, DefectFilter, OrderFilter, PaymentFilter, ReturnFilter, SaleFilter
from .models import (
    Client,
    ClientPrice,
    DefectRecord,
    Order,
    OrderLine,
    OrderReservation,
    Payment,
    PriceList,
    Return,
    ReturnLine,
    ReworkRequest,
    Sale,
    SaleLine,
    Shipment,
)
from .serializers import (
    ClientPriceSerializer,
    ClientSerializer,
    DefectRecordSerializer,
    OrderReservationSerializer,
    OrderSerializer,
    OrderLineSerializer,
    PaymentSerializer,
    PriceListSerializer,
    ReturnSerializer,
    ReworkRequestSerializer,
    SaleSerializer,
    defect_record_source_label,
    rework_quantities_from_defect_record,
)

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    """Единый стиль: строковые code / error / detail (для UI), опционально errors."""
    payload = {
        'code': code,
        'error': message,
        'detail': message,
    }
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ClientViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'
    activity_section = 'Клиенты'
    activity_label = 'клиент'
    filterset_class = ClientFilter
    search_fields = ['name', 'inn', 'contact', 'email', 'messenger']
    ordering_fields = ['id', 'name']

    def get_queryset(self):
        return (
            Client.objects.annotate(
                sales_count=Count(
                    'sales',
                    distinct=False,
                    filter=~Q(sales__sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED)),
                ),
                sales_total=Coalesce(
                    Sum(
                        'sales__revenue',
                        filter=~Q(sales__sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED)),
                    ),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                ),
            )
        )

    def destroy(self, request, *args, **kwargs):
        return Response(
            {
                'code': 'DELETE_DISABLED',
                'error': 'Физическое удаление клиентов отключено. Используйте is_active=false.',
                'detail': 'Патч клиента: {"is_active": false}.',
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """
        Агрегированная история клиента: заявки, продажи, оплаты, возвраты,
        долги, авансы, кредитный лимит, прибыль, просроченные долги.
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        from config.api_numbers import api_decimal_str
        from .credit_check import compute_client_debt, check_credit_limit

        client = self.get_object()

        orders = Order.objects.filter(client=client).prefetch_related('lines', 'payments').order_by('-date')
        sales = Sale.objects.filter(client=client).order_by('-date')
        sales_for_aggregates = sales.exclude(
            sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED),
        )
        payments = Payment.objects.filter(client=client, status=Payment.STATUS_ACTIVE).order_by('-date')
        returns = Return.objects.filter(sale__client=client).order_by('-date')

        # Денежная аналитика
        total_paid = sum(
            (p.amount or Decimal('0'))
            for p in payments
            if p.payment_type in (Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE)
        )
        total_refunded = sum(
            (p.amount or Decimal('0'))
            for p in payments
            if p.payment_type == Payment.TYPE_REFUND
        )
        net_paid = total_paid - total_refunded
        total_revenue = sum((s.revenue or Decimal('0')) for s in sales_for_aggregates)

        # Долг или аванс
        if net_paid < total_revenue:
            client_debt_money = total_revenue - net_paid
            client_advance_amount = Decimal('0')
        else:
            client_debt_money = Decimal('0')
            client_advance_amount = net_paid - total_revenue

        # Прибыль по клиенту
        total_cost = sum((s.cost or Decimal('0')) for s in sales_for_aggregates if not s.is_defect_sale)
        defect_revenue = sum((s.revenue or Decimal('0')) for s in sales_for_aggregates if s.is_defect_sale)
        total_profit = total_revenue - total_cost

        # Неотгруженные товары
        has_unshipped = any(order.has_company_debt_by_goods for order in orders)

        # Кредитный лимит
        credit_check = check_credit_limit(client)

        # Просроченные задолженности (продажи старше 30 дней с непогашенным долгом)
        from django.utils import timezone as tz
        import datetime
        threshold = tz.now().date() - datetime.timedelta(days=30)
        overdue_orders = [
            o for o in orders
            if o.has_company_debt_by_goods and o.date < threshold
        ]

        return Response({
            'client_id': client.id,
            'client_name': client.name,
            'orders': OrderSerializer(orders, many=True).data,
            'sales': SaleSerializer(sales, many=True).data,
            'payments': PaymentSerializer(payments, many=True).data,
            'returns': ReturnSerializer(returns, many=True).data,
            # Денежные итоги
            'total_revenue': api_decimal_str(total_revenue),
            'total_ordered': api_decimal_str(total_revenue),
            'total_paid': api_decimal_str(net_paid),
            'total_paid_gross': api_decimal_str(total_paid),
            'total_refunded': api_decimal_str(total_refunded),
            'client_debt_money': api_decimal_str(client_debt_money),
            'client_advance_amount': api_decimal_str(client_advance_amount),
            # Товарный долг
            'has_unshipped_goods': has_unshipped,
            'overdue_orders_count': len(overdue_orders),
            # Прибыль
            'total_profit': api_decimal_str(total_profit),
            'defect_revenue': api_decimal_str(defect_revenue),
            # Кредитный лимит
            'credit_limit': api_decimal_str(credit_check.credit_limit) if credit_check.credit_limit is not None else None,
            'credit_limit_mode': credit_check.block_mode,
            'credit_available': api_decimal_str(credit_check.credit_available) if credit_check.credit_available is not None else None,
            'credit_is_over_limit': credit_check.is_over_limit,
            'credit_warning': credit_check.warning,
        })


# ─────────────────────────────────────────────────────────────────────────────
# ORDER (Заявка)
# ─────────────────────────────────────────────────────────────────────────────

class OrderViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Order.objects.select_related(
        'client', 'created_by', 'responsible_user',
    ).prefetch_related('lines', 'lines__profile', 'payments', 'sales').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'client_orders'
    activity_section = 'Заявки'
    activity_label = 'заявка'
    ordering_fields = ['id', 'date', 'status']
    filterset_class = OrderFilter

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        self._broadcast(serializer.instance, created=True)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._broadcast(serializer.instance, created=False)

    def _broadcast(self, instance, created):
        from apps.realtime.broadcast import schedule_push
        schedule_push(
            resource='order',
            action='created' if created else 'updated',
            entity_id=instance.pk,
            extra={'client_id': instance.client_id, 'status': instance.status},
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = OrderSerializer(instance, context={'request': request}).data
        from .state_machine import ORDER_TRANSITIONS
        data['linked_entities'] = {
            'client': (
                {
                    'id': instance.client_id,
                    'label': instance.client.name,
                } if instance.client_id else None
            ),
            'responsible_user': (
                {
                    'id': instance.responsible_user_id,
                    'label': getattr(instance.responsible_user, 'name', '') or '',
                } if instance.responsible_user_id else None
            ),
        }
        data['available_status_transitions'] = ORDER_TRANSITIONS.get(instance.status, [])
        data['available_actions'] = {
            'set_status': bool(ORDER_TRANSITIONS.get(instance.status, [])),
            'reserve': instance.status in (
                Order.STATUS_NEW,
                Order.STATUS_CONFIRMED,
                Order.STATUS_IN_PROGRESS,
                Order.STATUS_PARTIALLY_SHIPPED,
            ),
            'release_reserve': True,
            'cancel': instance.status not in (Order.STATUS_CLOSED, Order.STATUS_CANCELED),
            'waybill': True,
            'history': True,
        }
        return Response(data)

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        from apps.recipes.models import PlasticProfile

        clients = list(
            Client.objects.filter(is_active=True)
            .order_by('name')
            .values('id', 'name')[:200]
        )
        profiles = list(
            PlasticProfile.objects.order_by('name')
            .values('id', 'name')[:300]
        )
        return Response({
            'clients': [{'id': c['id'], 'label': c['name']} for c in clients],
            'profiles': [{'id': p['id'], 'label': p['name']} for p in profiles],
        })

    @action(detail=True, methods=['patch'], url_path='status')
    def set_status(self, request, pk=None):
        """Изменить статус заявки с валидацией переходов (централизованный state machine)."""
        from .state_machine import validate_order_transition

        order = self.get_object()
        new_status = request.data.get('status')
        if not new_status:
            return _err('MISSING_STATUS', 'Укажите status')

        try:
            validate_order_transition(order.status, new_status)
        except ValueError as e:
            return _err('INVALID_STATUS_TRANSITION', str(e), http_status=422)

        if new_status in (
            Order.STATUS_SHIPPED,
            Order.STATUS_PARTIALLY_SHIPPED,
            Order.STATUS_CLOSED,
        ):
            from .order_sync import validate_order_for_new_status
            try:
                validate_order_for_new_status(order, new_status)
            except ValueError as e:
                return _err('ORDER_STATUS_BLOCKED', str(e), http_status=422)

        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])
        self._broadcast(order, created=False)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['get'], url_path='waybill')
    def waybill(self, request, pk=None):
        """HTML-накладная по заявке (канонический endpoint)."""
        order = self.get_object()
        return _order_html_response(order)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {
                'code': 'DELETE_DISABLED',
                'error': 'Физическое удаление заявок отключено. Используйте /api/orders/{id}/cancel/.',
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['get'], url_path='history')
    def order_history(self, request, pk=None):
        """Трассировка: заявка → продажи → возвраты → переделки."""
        order = self.get_object()
        sales = Sale.objects.filter(linked_order=order).prefetch_related('sale_lines')
        payments = Payment.objects.filter(linked_order=order, status=Payment.STATUS_ACTIVE)
        returns = Return.objects.filter(linked_order=order).prefetch_related('lines')
        return Response({
            'order': OrderSerializer(order).data,
            'sales': SaleSerializer(sales, many=True).data,
            'payments': PaymentSerializer(payments, many=True).data,
            'returns': ReturnSerializer(returns, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='reserve')
    def reserve(self, request, pk=None):
        """
        Зарезервировать товар под строку заявки.
        Body: { order_line_id, warehouse_batch_id, quantity, comment? }
        """
        from .reservations import reserve_order_line
        from apps.warehouse.models import WarehouseBatch

        order = self.get_object()
        line_id = request.data.get('order_line_id')
        wb_id = request.data.get('warehouse_batch_id')
        quantity_raw = request.data.get('quantity')
        comment = request.data.get('comment', '')

        if not line_id:
            return _err('MISSING_FIELD', 'Укажите order_line_id')
        if not wb_id:
            return _err('MISSING_FIELD', 'Укажите warehouse_batch_id')
        if quantity_raw is None:
            return _err('MISSING_FIELD', 'Укажите quantity')

        try:
            line = order.lines.get(pk=line_id)
        except OrderLine.DoesNotExist:
            return _err('NOT_FOUND', 'Строка заявки не найдена', http_status=404)

        try:
            wb = WarehouseBatch.objects.get(pk=wb_id)
        except WarehouseBatch.DoesNotExist:
            return _err('NOT_FOUND', 'Партия склада не найдена', http_status=404)

        try:
            qty = Decimal(str(quantity_raw))
        except Exception:
            return _err('INVALID_FIELD', 'Некорректное значение quantity')

        try:
            reservation = reserve_order_line(
                order_line=line,
                warehouse_batch=wb,
                quantity=qty,
                user=request.user if request.user.is_authenticated else None,
                comment=comment,
            )
        except ValueError as e:
            return _err('RESERVATION_ERROR', str(e), http_status=422)

        return Response(OrderReservationSerializer(reservation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='release-reserve')
    def release_reserve(self, request, pk=None):
        """
        Снять резерв.
        Body: { reservation_id }
        """
        from .reservations import release_reservation

        order = self.get_object()
        res_id = request.data.get('reservation_id')
        if not res_id:
            return _err('MISSING_FIELD', 'Укажите reservation_id')

        try:
            reservation = OrderReservation.objects.get(
                pk=res_id,
                order_line__order=order,
            )
        except OrderReservation.DoesNotExist:
            return _err('NOT_FOUND', 'Резерв не найден', http_status=404)

        try:
            reservation = release_reservation(reservation)
        except ValueError as e:
            return _err('RESERVATION_ERROR', str(e), http_status=422)

        return Response(OrderReservationSerializer(reservation).data)

    @action(detail=True, methods=['get'], url_path='reservations')
    def reservations(self, request, pk=None):
        """Список всех резервов по заявке."""
        order = self.get_object()
        line_ids = list(order.lines.values_list('id', flat=True))
        qs = OrderReservation.objects.filter(
            order_line_id__in=line_ids,
        ).select_related('order_line', 'warehouse_batch', 'created_by').order_by('-created_at')
        return Response(OrderReservationSerializer(qs, many=True).data)

    @action(detail=True, methods=['patch'], url_path='cancel')
    def cancel_order(self, request, pk=None):
        """
        Отменить заявку с автоматическим снятием всех активных резервов.
        """
        from .reservations import release_all_for_order
        from .state_machine import validate_order_transition, validate_order_cancel

        order = self.get_object()
        try:
            validate_order_transition(order.status, Order.STATUS_CANCELED)
            validate_order_cancel(order)
        except ValueError as e:
            return _err('INVALID_TRANSITION', str(e), http_status=422)

        released = release_all_for_order(order)
        order.status = Order.STATUS_CANCELED
        order.save(update_fields=['status', 'updated_at'])
        self._broadcast(order, created=False)
        return Response({
            'status': order.status,
            'reservations_released': released,
            'order': OrderSerializer(order).data,
        })


def _order_html_response(order: Order) -> HttpResponse:
    parts = [
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
        f'<title>Заявка {escape(order.order_number)}</title>',
        '<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;line-height:1.5;}',
        'table{border-collapse:collapse;width:100%;}td,th{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;}',
        'th{background:#f5f5f5;}</style>',
        '</head><body>',
        '<h1>Заявка</h1>',
        f'<p><strong>№</strong> {escape(order.order_number)} <strong>от</strong> {order.date.isoformat()}</p>',
        f'<p><strong>Статус:</strong> {escape(order.get_status_display())}</p>',
    ]
    if order.client_id:
        cl = order.client
        parts.append(f'<p><strong>Покупатель:</strong> {escape(cl.name)}</p>')
        if cl.inn:
            parts.append(f'<p>ИНН: {escape(cl.inn)}</p>')
        if cl.phone:
            parts.append(f'<p>Тел.: {escape(cl.phone)}</p>')
    parts.append(
        '<table><thead><tr>'
        '<th>№</th><th>Наименование</th><th>Тип</th>'
        '<th>Заказано</th><th>Отгружено</th><th>Остаток</th>'
        '<th>Цена</th><th>Сумма</th>'
        '</tr></thead><tbody>'
    )
    for i, line in enumerate(order.lines.all(), 1):
        price_str = str(line.unit_price) if line.unit_price is not None else '—'
        total_str = str(line.line_total) if line.unit_price is not None else '—'
        parts.append(
            f'<tr>'
            f'<td>{i}</td>'
            f'<td>{escape(line.product)}</td>'
            f'<td>{escape(line.product_type or "")}</td>'
            f'<td>{escape(str(line.ordered_quantity))}</td>'
            f'<td>{escape(str(line.shipped_quantity))}</td>'
            f'<td>{escape(str(line.remaining_quantity))}</td>'
            f'<td>{escape(price_str)}</td>'
            f'<td>{escape(total_str)}</td>'
            f'</tr>'
        )
    parts.append('</tbody></table>')
    if order.comment:
        parts.append(f'<p><strong>Комментарий:</strong> {escape(order.comment)}</p>')
    parts.append('<p><em>Сформировано автоматически.</em></p></body></html>')
    html = ''.join(parts)
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    resp['Content-Disposition'] = f'inline; filename="order-waybill-{order.id}.html"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# SALE (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

class SaleViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Sale.objects.select_related(
        'client', 'warehouse_batch', 'warehouse_batch__profile', 'linked_order',
    ).prefetch_related('sale_lines', 'payments').all()
    serializer_class = SaleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    activity_section = 'Продажи'
    activity_label = 'продажа'
    filterset_class = SaleFilter
    ordering_fields = ['id', 'date']

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.warehouse_batch_id:
            if 'warehouse_batch' in serializer.validated_data:
                wb = serializer.validated_data['warehouse_batch']
                new_id = wb.pk if wb else None
                if new_id is None:
                    raise ValidationError(
                        {'warehouse_batch': 'Нельзя отвязать партию склада у продажи'},
                    )
                if new_id != instance.warehouse_batch_id:
                    raise ValidationError(
                        {'warehouse_batch': 'Нельзя сменить партию склада у существующей продажи'},
                    )
            if 'quantity' in serializer.validated_data:
                new_q = serializer.validated_data['quantity']
                if Decimal(str(new_q)) != Decimal(str(instance.quantity)):
                    raise ValidationError(
                        {'quantity': 'Нельзя изменить количество: создайте новую продажу или отмените эту'},
                    )
            if 'quantity_input' in serializer.validated_data:
                new_qi = serializer.validated_data.get('quantity_input')
                old_qi = instance.quantity_input
                if new_qi is None and old_qi is None:
                    pass
                elif new_qi is None or old_qi is None:
                    raise ValidationError(
                        {'quantity_input': 'Нельзя изменить quantity_input: создайте новую продажу или отмените эту'},
                    )
                elif Decimal(str(new_qi)) != Decimal(str(old_qi)):
                    raise ValidationError(
                        {'quantity_input': 'Нельзя изменить quantity_input: создайте новую продажу или отмените эту'},
                    )
            if 'stock_form' in serializer.validated_data or 'piece_pick' in serializer.validated_data:
                raise ValidationError(
                    {'stock_form': 'Нельзя менять stock_form / piece_pick после создания продажи со складом'},
                )
        super().perform_update(serializer)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'code': 'DELETE_DISABLED', 'error': 'Удаление продажи отключено. Используйте /api/sales/{id}/cancel/.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = SaleSerializer(instance, context={'request': request}).data
        from .state_machine import SALE_TRANSITIONS
        data['linked_entities'] = {
            'client': (
                {
                    'id': instance.client_id,
                    'label': instance.client.name,
                } if instance.client_id else None
            ),
            'linked_order': (
                {
                    'id': instance.linked_order_id,
                    'label': instance.linked_order.order_number,
                } if instance.linked_order_id else None
            ),
            'warehouse_batch': (
                {
                    'id': instance.warehouse_batch_id,
                    'label': f'#{instance.warehouse_batch_id} {instance.warehouse_batch.product}',
                } if instance.warehouse_batch_id else None
            ),
        }
        data['available_status_transitions'] = SALE_TRANSITIONS.get(instance.sale_status, [])
        data['available_actions'] = {
            'set_status': bool(SALE_TRANSITIONS.get(instance.sale_status, [])),
            'credit_check': bool(instance.client_id),
            'waybill': True,
            'receipt': True,
        }
        return Response(data)

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        from apps.warehouse.models import WarehouseBatch
        from .reservations import get_available_quantity

        clients = list(
            Client.objects.filter(is_active=True)
            .order_by('name')
            .values('id', 'name')[:200]
        )
        orders_qs = Order.objects.order_by('-date', '-id')
        client_id = request.query_params.get('client_id')
        if client_id:
            orders_qs = orders_qs.filter(client_id=client_id)
        orders = list(orders_qs.select_related('client')[:200])
        order_id = request.query_params.get('order_id')
        order_lines = []
        if order_id:
            lines_qs = OrderLine.objects.filter(order_id=order_id).order_by('id')[:300]
            order_lines = [
                {
                    'id': line.id,
                    'label': (
                        f"{line.product} — заказано {api_decimal_str(line.ordered_quantity)} — "
                        f"продано {api_decimal_str(line.shipped_quantity)} — "
                        f"осталось {api_decimal_str(line.remaining_quantity)}"
                    ),
                    'product': line.product,
                    'ordered_quantity': api_decimal_str(line.ordered_quantity),
                    'shipped_quantity': api_decimal_str(line.shipped_quantity),
                    'remaining_quantity': api_decimal_str(line.remaining_quantity),
                    'unit_price': api_decimal_str(line.unit_price or Decimal('0')),
                }
                for line in lines_qs
            ]
        wb_qs = (
            WarehouseBatch.objects.filter(
                status=WarehouseBatch.STATUS_AVAILABLE,
                quality=WarehouseBatch.QUALITY_GOOD,
            )
            .order_by('-date', '-id')
            [:300]
        )
        warehouse_batches = []
        quality_labels = {
            WarehouseBatch.QUALITY_GOOD: 'Годный',
            WarehouseBatch.QUALITY_DEFECT: 'Брак',
        }
        inventory_labels = {
            WarehouseBatch.INVENTORY_PACKED: 'Упаковано',
            WarehouseBatch.INVENTORY_OPEN_PACKAGE: 'Открытая упаковка',
            WarehouseBatch.INVENTORY_UNPACKED: 'Неупаковано',
        }
        for b in wb_qs:
            available_qty = Decimal(str(get_available_quantity(b.pk)))
            if available_qty <= 0:
                continue
            warehouse_batches.append(
                {
                    'id': b.pk,
                    'label': (
                        f"#{b.pk} — {b.product} — свободно {api_decimal_str(available_qty)} шт — "
                        f"{quality_labels.get(b.quality, b.quality)} — "
                        f"{inventory_labels.get(b.inventory_form, b.inventory_form)}"
                    ),
                    'product': b.product,
                    'available_quantity': api_decimal_str(available_qty),
                    'quality': b.quality,
                    'status': b.status,
                    'inventory_form': b.inventory_form,
                },
            )
        return Response({
            'clients': [{'id': c['id'], 'label': c['name']} for c in clients],
            'orders': [
                {
                    'id': o.id,
                    'label': (
                        f"{o.order_number} — {(o.client.name if o.client_id else '—')} — "
                        f"осталось {api_decimal_str(o.remaining_amount)}"
                    ),
                }
                for o in orders
            ],
            'order_lines': order_lines,
            'warehouse_batches': warehouse_batches,
        })

    @staticmethod
    def _nakladnaya_html_response(sale):
        parts = [
            '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
            f'<title>Накладная {escape(sale.order_number)}</title>',
            '<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.45;}',
            'table{border-collapse:collapse;width:100%;}td,th{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;}',
            'th{background:#f5f5f5;}</style>',
            '</head><body>',
            '<h1>Накладная</h1>',
            f'<p><strong>№</strong> {escape(sale.invoice_number or sale.order_number)} '
            f'<strong>от</strong> {sale.date.isoformat()}</p>',
        ]
        if sale.client_id:
            cl = sale.client
            parts.append(f'<p><strong>Покупатель:</strong> {escape(cl.name)}</p>')
            if cl.inn:
                parts.append(f'<p>ИНН: {escape(cl.inn)}</p>')
            if cl.address:
                parts.append(f'<p>{escape(cl.address)}</p>')
            if cl.phone:
                parts.append(f'<p>Тел.: {escape(cl.phone)}</p>')
        else:
            parts.append('<p><strong>Покупатель:</strong> —</p>')

        # Строки продажи (новый формат)
        sale_lines = list(sale.sale_lines.all())
        if sale_lines:
            parts.append(
                '<table><thead><tr><th>№</th><th>Наименование</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>'
            )
            total_sum = Decimal('0')
            for i, sl in enumerate(sale_lines, 1):
                price_str = str(sl.unit_price) if sl.unit_price is not None else '—'
                total_str = str(sl.line_total) if sl.line_total is not None else '—'
                if sl.line_total:
                    total_sum += Decimal(str(sl.line_total))
                parts.append(
                    f'<tr><td>{i}</td><td>{escape(sl.product)}</td>'
                    f'<td>{escape(str(sl.quantity))}</td>'
                    f'<td>{escape(price_str)}</td>'
                    f'<td>{escape(total_str)}</td></tr>'
                )
            parts.append(f'</tbody></table>')
            parts.append(f'<p><strong>Итого:</strong> {total_sum}</p>')
        else:
            # Обратная совместимость — старый формат (одна строка)
            parts.append(
                '<table><thead><tr><th>Наименование</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>'
            )
            qty = sale.quantity
            price = sale.price
            line_sum = ''
            if price is not None:
                line_sum = str((price * qty).quantize(Decimal('0.01')))
            parts.append(
                '<tr>'
                f'<td>{escape(sale.product)}</td>'
                f'<td>{escape(str(qty))}</td>'
                f'<td>{escape(str(price if price is not None else "—"))}</td>'
                f'<td>{escape(line_sum or "—")}</td>'
                '</tr>'
            )
            parts.append('</tbody></table>')
            if price is not None:
                parts.append(f'<p><strong>Итого:</strong> {line_sum}</p>')

        # Оплата / остаток
        payments = list(sale.payments.filter(status=Payment.STATUS_ACTIVE).all())
        if payments:
            paid = sum(
                (p.amount or Decimal('0')) for p in payments
                if p.payment_type in (Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE)
            )
            refunded = sum(
                (p.amount or Decimal('0')) for p in payments
                if p.payment_type == Payment.TYPE_REFUND
            )
            net = paid - refunded
            parts.append(f'<p><strong>Оплачено:</strong> {net}</p>')

        if sale.comment:
            parts.append(f'<p><strong>Комментарий:</strong> {escape(sale.comment)}</p>')
        if sale.warehouse_batch_id:
            parts.append(f'<p><strong>Партия склада ГП:</strong> №{sale.warehouse_batch_id}</p>')
        if sale.is_defect_sale:
            parts.append('<p><em>⚠ Продажа брака</em></p>')
        parts.append('<p><em>Сформировано автоматически.</em></p></body></html>')
        html = ''.join(parts)
        resp = HttpResponse(html, content_type='text/html; charset=utf-8')
        resp['Content-Disposition'] = f'inline; filename="sale-waybill-{sale.id}.html"'
        return resp

    def _serve_nakladnaya(self, request, *args, **kwargs):
        sale = self.get_object()
        return SaleViewSet._nakladnaya_html_response(sale)

    @action(detail=True, methods=['post', 'patch'], url_path='cancel')
    def cancel_sale(self, request, pk=None):
        """Отмена продажи: откат склада, восстановление резервов, статус canceled."""
        from .state_machine import validate_sale_transition
        from .sale_warehouse import reverse_warehouse_for_sale
        from .reservations import restore_reservations_for_sale

        sale = self.get_object()
        if sale.sale_status == Sale.STATUS_CANCELED:
            return _err('ALREADY_CANCELED', 'Продажа уже отменена', http_status=422)
        if Return.objects.filter(sale=sale).exclude(status=Return.STATUS_CANCELED).exists():
            return _err('HAS_RETURNS', 'Нельзя отменить продажу: есть возвраты. Обработайте возвраты вручную.', http_status=409)
        if Payment.objects.filter(linked_sale=sale, status=Payment.STATUS_ACTIVE).exists():
            return _err('HAS_PAYMENTS', 'Нельзя отменить продажу: есть оплаты. Сначала отмените/скорректируйте оплаты.', http_status=409)
        try:
            validate_sale_transition(sale.sale_status, Sale.STATUS_CANCELED)
        except ValueError as e:
            return _err('INVALID_TRANSITION', str(e), http_status=422)
        with transaction.atomic():
            try:
                reverse_warehouse_for_sale(sale)
            except Exception as e:
                return _err('WAREHOUSE_ROLLBACK', str(e), http_status=422)
            restore_reservations_for_sale(sale=sale, user=request.user, request=request)
            sale.sale_status = Sale.STATUS_CANCELED
            sale.save(update_fields=['sale_status', 'updated_at'])
        if sale.linked_order_id:
            from .order_sync import recalculate_order_line_shipped_from_sale_lines_for_order
            recalculate_order_line_shipped_from_sale_lines_for_order(sale.linked_order_id)
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='sales',
            description=f'Отмена продажи #{sale.order_number}',
            model_cls=Sale,
            instance=sale,
            payload_extra={'action': 'cancel_sale', 'sale_id': sale.pk},
        )
        return Response(SaleSerializer(sale, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='waybill')
    def waybill(self, request, pk=None):
        return self._serve_nakladnaya(request)

    @action(detail=True, methods=['get'], url_path='credit-check')
    def credit_check_for_sale(self, request, pk=None):
        """Проверить кредитный лимит клиента перед оплатой/отгрузкой по продаже."""
        from .credit_check import check_credit_limit, credit_check_result_to_dict
        sale = self.get_object()
        if not sale.client_id:
            return _err('NO_CLIENT', 'У продажи не указан клиент')
        result = check_credit_limit(sale.client, additional_amount=sale.revenue or Decimal('0'))
        return Response(credit_check_result_to_dict(result))

    @action(detail=True, methods=['patch'], url_path='status')
    def set_status(self, request, pk=None):
        """
        Изменить статус продажи через централизованный state machine.

        Проверки перед shipped/closed:
          - Кредитный лимит (hard)
          - Наличие склада (если продажа привязана к партии)
        Допускает force_credit_override=true для пользователей с правом credit_limit_override.
        """
        from .state_machine import validate_sale_transition, validate_sale_ship
        from .credit_check import enforce_credit_limit, CreditLimitBlocked

        sale = self.get_object()
        new_status = request.data.get('status')
        if not new_status:
            return _err('MISSING_STATUS', 'Укажите status')

        try:
            validate_sale_transition(sale.sale_status, new_status)
        except ValueError as e:
            return _err('INVALID_STATUS_TRANSITION', str(e), http_status=422)

        shipping_statuses = (Sale.STATUS_SHIPPED, Sale.STATUS_CLOSED, Sale.STATUS_PARTIALLY_SHIPPED)
        if new_status in shipping_statuses:
            # Stock / reservation check
            try:
                validate_sale_ship(sale)
            except ValueError as e:
                return _err('SHIP_BLOCKED', str(e), http_status=422)

            # Hard credit limit check
            if sale.client_id:
                force_override = str(request.data.get('force_credit_override', '')).lower() in ('1', 'true', 'yes')
                if force_override:
                    sale.credit_limit_bypassed = True
                    sale.save(update_fields=['credit_limit_bypassed'])
                try:
                    enforce_credit_limit(
                        sale.client,
                        Decimal('0'),
                        user=request.user,
                        force_override=force_override,
                    )
                except CreditLimitBlocked as e:
                    return _err('CREDIT_LIMIT_BLOCKED', str(e), http_status=422)

        sale.sale_status = new_status
        sale.save(update_fields=['sale_status', 'updated_at'])
        sale.refresh_from_db()
        from .sale_warehouse import apply_warehouse_for_sale
        from .reservations import auto_fulfill_sale_lines_after_shipping
        try:
            apply_warehouse_for_sale(sale)
        except (ValueError, Exception) as e:
            from rest_framework.exceptions import ValidationError as DrfV
            if isinstance(e, DrfV):
                return _err('WAREHOUSE_APPLY', str(e.detail), http_status=422)
            return _err('WAREHOUSE_APPLY', str(e), http_status=422)
        if sale.linked_order_id and new_status in shipping_statuses:
            auto_fulfill_sale_lines_after_shipping(
                sale=sale,
                order=sale.linked_order,
                user=request.user if request.user.is_authenticated else None,
                request=request,
            )
        return Response(SaleSerializer(sale, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='receipt')
    def receipt(self, request, pk=None):
        """HTML-квитанция об оплате."""
        sale = self.get_object()
        payments = list(sale.payments.filter(status=Payment.STATUS_ACTIVE).all())
        parts = [
            '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
            f'<title>Квитанция {escape(sale.receipt_number or sale.order_number)}</title>',
            '<style>body{font-family:system-ui,sans-serif;max-width:600px;margin:2rem auto;line-height:1.5;}</style>',
            '</head><body><h1>Квитанция об оплате</h1>',
            f'<p><strong>Продажа №</strong> {escape(sale.order_number)} от {sale.date.isoformat()}</p>',
        ]
        if sale.client_id:
            parts.append(f'<p><strong>Клиент:</strong> {escape(sale.client.name)}</p>')
        if payments:
            for p in payments:
                parts.append(
                    f'<p>{p.date.isoformat()} — {p.get_payment_type_display()}: '
                    f'<strong>{p.amount}</strong> ({p.get_payment_method_display()})</p>'
                )
        else:
            parts.append('<p>Оплаты не зафиксированы</p>')
        parts.append('<p><em>Сформировано автоматически.</em></p></body></html>')
        html = ''.join(parts)
        resp = HttpResponse(html, content_type='text/html; charset=utf-8')
        resp['Content-Disposition'] = f'inline; filename="sale-receipt-{sale.id}.html"'
        return resp


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT (Оплата)
# ─────────────────────────────────────────────────────────────────────────────

class PaymentViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Payment.objects.select_related('client', 'linked_order', 'linked_sale', 'linked_return', 'created_by').all()
    serializer_class = PaymentSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'payments'
    activity_section = 'Оплаты'
    activity_label = 'оплата'
    ordering_fields = ['id', 'date']
    filterset_class = PaymentFilter

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'code': 'DELETE_DISABLED', 'error': 'Удаление оплат отключено. Используйте /api/payments/{id}/cancel/.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post', 'patch'], url_path='cancel')
    def cancel_payment(self, request, pk=None):
        p = self.get_object()
        if p.status == Payment.STATUS_CANCELED:
            return _err('PAYMENT_ALREADY_CANCELED', 'Оплата уже отменена', http_status=422)
        p.status = Payment.STATUS_CANCELED
        p.save(update_fields=['status'])
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='payments',
            description=f'Отмена оплаты #{p.payment_number or p.pk}',
            model_cls=Payment,
            instance=p,
            payload_extra={'action': 'cancel_payment', 'payment_id': p.pk},
        )
        return Response(PaymentSerializer(p).data)

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        inst = serializer.instance
        from apps.realtime.broadcast import schedule_push
        from django.db import transaction
        transaction.on_commit(lambda: schedule_push(
            resource='payment',
            action='created',
            entity_id=inst.pk,
            extra={'client_id': inst.client_id, 'payment_type': inst.payment_type},
        ))

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        client_id = request.query_params.get('client_id')
        sale_id = request.query_params.get('sale_id')
        order_id = request.query_params.get('order_id')
        return_id = request.query_params.get('return_id')

        from .payment_status import order_payment_metrics, sale_payment_metrics

        clients_qs = Client.objects.filter(is_active=True).order_by('name', 'id')
        if client_id:
            clients_qs = clients_qs.filter(pk=client_id)
        clients = [{'id': c.id, 'label': c.name} for c in clients_qs[:200]]

        orders_qs = Order.objects.select_related('client').order_by('-date', '-id')
        sales_qs = Sale.objects.select_related('client').order_by('-date', '-id')
        returns_qs = Return.objects.select_related('sale', 'sale__client').filter(status=Return.STATUS_COMPLETED).order_by('-date', '-id')

        if client_id:
            orders_qs = orders_qs.filter(client_id=client_id)
            sales_qs = sales_qs.filter(client_id=client_id)
            returns_qs = returns_qs.filter(sale__client_id=client_id)
        if order_id:
            orders_qs = orders_qs.filter(pk=order_id)
        if sale_id:
            sales_qs = sales_qs.filter(pk=sale_id)
            returns_qs = returns_qs.filter(sale_id=sale_id)
        if return_id:
            returns_qs = returns_qs.filter(pk=return_id)

        orders_payload = []
        for order in orders_qs[:200]:
            m = order_payment_metrics(order)
            debt = api_decimal_str(m['debt_amount'])
            orders_payload.append(
                {
                    'id': order.id,
                    'label': f'{order.order_number} — долг {debt}',
                    'client': order.client_id,
                    'debt_amount': debt,
                    'payment_status': m['payment_status'],
                    'status': order.status,
                },
            )

        sales_payload = []
        for sale in sales_qs[:200]:
            m = sale_payment_metrics(sale)
            debt = api_decimal_str(m['debt_amount'])
            sales_payload.append(
                {
                    'id': sale.id,
                    'label': f'{sale.sale_number or sale.order_number} — долг {debt}',
                    'client': sale.client_id,
                    'debt_amount': debt,
                    'payment_status': m['payment_status'],
                    'sale_status': sale.sale_status,
                },
            )

        returns_payload = []
        for ret in returns_qs[:200]:
            amount = Decimal('0')
            for line in ret.lines.select_related('sale_line').all():
                unit_price = Decimal(str(line.sale_line.unit_price or 0)) if line.sale_line_id else Decimal('0')
                amount += Decimal(str(line.quantity or 0)) * unit_price
            amount_str = api_decimal_str(amount)
            returns_payload.append(
                {
                    'id': ret.id,
                    'label': f'{ret.return_number or f"RET-{ret.id}"} — к возврату {amount_str}',
                    'client': ret.sale.client_id if ret.sale_id else None,
                    'return_amount': amount_str,
                    'status': ret.status,
                },
            )

        return Response(
            {
                'clients': clients,
                'orders': orders_payload,
                'sales': sales_payload,
                'returns': returns_payload,
            },
        )

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """Сводка оплат по клиенту."""
        client_id = request.query_params.get('client_id')
        if not client_id:
            return _err('MISSING_CLIENT', 'Укажите client_id')
        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return _err('NOT_FOUND', 'Клиент не найден', http_status=404)

        payments = Payment.objects.filter(client=client, status=Payment.STATUS_ACTIVE)
        sales = Sale.objects.filter(client=client).exclude(sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED))

        total_paid = payments.filter(
            payment_type__in=[Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE]
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        total_refunded = payments.filter(
            payment_type=Payment.TYPE_REFUND
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        net_paid = total_paid - total_refunded
        total_revenue = sales.aggregate(s=Sum('revenue'))['s'] or Decimal('0')

        from config.api_numbers import api_decimal_str
        return Response({
            'client_id': client.id,
            'client_name': client.name,
            'total_paid_gross': api_decimal_str(total_paid),
            'total_refunded': api_decimal_str(total_refunded),
            'total_paid_net': api_decimal_str(net_paid),
            'total_revenue': api_decimal_str(total_revenue),
            'client_debt_money': api_decimal_str(max(Decimal('0'), total_revenue - net_paid)),
            'client_advance_amount': api_decimal_str(max(Decimal('0'), net_paid - total_revenue)),
        })


# ─────────────────────────────────────────────────────────────────────────────
# RETURN (Возврат)
# ─────────────────────────────────────────────────────────────────────────────

class ReturnViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Return.objects.select_related(
        'sale', 'sale__client', 'linked_order', 'created_by',
    ).prefetch_related('lines').all()
    serializer_class = ReturnSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'returns'
    activity_section = 'Возвраты'
    activity_label = 'возврат'
    ordering_fields = ['id', 'date']
    filterset_class = ReturnFilter

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'code': 'DELETE_DISABLED', 'error': 'Удаление возвратов отключено. Используйте /api/returns/{id}/cancel/.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post', 'patch'], url_path='cancel')
    def cancel_return(self, request, pk=None):
        from .return_rollback import rollback_return_document

        ret_doc = self.get_object()
        if ret_doc.status == Return.STATUS_CANCELED:
            return _err('RETURN_ALREADY_CANCELED', 'Возврат уже отменён', http_status=422)
        with transaction.atomic():
            if ret_doc.status == Return.STATUS_COMPLETED:
                active_refund_exists = Payment.objects.filter(
                    linked_return=ret_doc,
                    payment_type=Payment.TYPE_REFUND,
                    status=Payment.STATUS_ACTIVE,
                ).exists()
                if active_refund_exists:
                    return _err(
                        'REFUND_PAYMENT_EXISTS',
                        'Нельзя отменить возврат: есть активный refund payment.',
                        http_status=409,
                    )

                line_ids = list(ret_doc.lines.values_list('id', flat=True))
                defects = list(
                    DefectRecord.objects.filter(
                        source_type=DefectRecord.SOURCE_RETURN,
                        source_id__in=line_ids,
                    ),
                )
                for d in defects:
                    used_statuses = {
                        DefectRecord.STATUS_SOLD,
                        DefectRecord.STATUS_WRITTEN_OFF,
                        DefectRecord.STATUS_SENT_TO_REWORK,
                        DefectRecord.STATUS_REWORKED,
                        DefectRecord.STATUS_CLOSED,
                    }
                    counters_changed = any(
                        Decimal(str(x or 0)) > 0
                        for x in (
                            d.sold_quantity_pcs,
                            d.written_off_quantity_pcs,
                            d.sent_to_rework_quantity_pcs,
                        )
                    )
                    if d.status in used_statuses or counters_changed:
                        return _err(
                            'DOWNSTREAM_USED',
                            'Нельзя отменить возврат: связанный defect уже использован.',
                            http_status=409,
                        )
                reworks = ReworkRequest.objects.filter(defect_record_id__in=[d.id for d in defects])
                for rw in reworks:
                    if rw.status in (
                        ReworkRequest.STATUS_IN_PROGRESS,
                        ReworkRequest.STATUS_COMPLETED,
                        ReworkRequest.STATUS_CANCELED,
                    ):
                        return _err(
                            'DOWNSTREAM_USED',
                            'Нельзя отменить возврат: связанная переделка уже использована.',
                            http_status=409,
                        )

                for line in ret_doc.lines.select_related('sale_line', 'sale_line__warehouse_batch').all():
                    if line.return_target != ReturnLine.TARGET_WAREHOUSE:
                        continue
                    wb = None
                    if line.sale_line_id and line.sale_line.warehouse_batch_id:
                        wb = line.sale_line.warehouse_batch
                    elif ret_doc.sale and ret_doc.sale.warehouse_batch_id:
                        wb = ret_doc.sale.warehouse_batch
                    if wb is None:
                        continue
                    current = Decimal(str(wb.quantity or 0))
                    delta = Decimal(str(line.quantity or 0))
                    if current - delta < 0:
                        return _err(
                            'WAREHOUSE_ROLLBACK_NEGATIVE',
                            'Нельзя откатить возврат: rollback уведет складской остаток в минус.',
                            http_status=409,
                        )
                try:
                    rollback_return_document(ret_doc)
                except Exception as exc:
                    return _err(
                        'RETURN_ROLLBACK_FAILED',
                        f'Не удалось откатить возврат: {exc}',
                        http_status=409,
                    )
            ret_doc.status = Return.STATUS_CANCELED
            ret_doc.save(update_fields=['status'])
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='returns',
            description=f'Отмена возврата #{ret_doc.return_number or ret_doc.pk}',
            model_cls=Return,
            instance=ret_doc,
            payload_extra={'action': 'cancel_return', 'return_id': ret_doc.pk},
        )
        return Response(ReturnSerializer(ret_doc, context={'request': request}).data)

    @action(detail=True, methods=['post', 'patch'], url_path='complete')
    def complete_return(self, request, pk=None):
        """Провести возврат: склад/брак/переделка только здесь (из статуса draft)."""
        ret_doc = self.get_object()
        if ret_doc.status == Return.STATUS_COMPLETED:
            return _err('RETURN_ALREADY_COMPLETED', 'Возврат уже проведен.', http_status=422)
        if ret_doc.status == Return.STATUS_CANCELED:
            return _err('RETURN_ALREADY_CANCELED', 'Отмененный возврат нельзя провести.', http_status=422)
        if ret_doc.status != Return.STATUS_DRAFT:
            return _err('RETURN_COMPLETE_FAILED', 'Провести можно только возврат в статусе draft.', http_status=422)
        if not ret_doc.lines.exists():
            return _err('NO_LINES', 'Нет строк возврата', http_status=422)
        ser = ReturnSerializer()
        with transaction.atomic():
            try:
                ser.apply_completion_effects(ret_doc)
            except Exception as exc:
                return _err('RETURN_COMPLETE_FAILED', f'Ошибка проведения возврата: {exc}', http_status=409)
            ret_doc.status = Return.STATUS_COMPLETED
            ret_doc.save(update_fields=['status'])
        return Response(ReturnSerializer(ret_doc, context={'request': request}).data)

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        inst = serializer.instance
        from apps.realtime.broadcast import schedule_push
        from django.db import transaction
        transaction.on_commit(lambda: schedule_push(
            resource='return',
            action='created',
            entity_id=inst.pk,
            extra={'sale_id': inst.sale_id},
        ))

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = ReturnSerializer(instance, context={'request': request}).data
        downstream = []
        line_ids = list(instance.lines.values_list('id', flat=True))
        warehouse_effects = []
        for line in instance.lines.select_related('sale_line', 'sale_line__warehouse_batch').all():
            if line.return_target != ReturnLine.TARGET_WAREHOUSE:
                continue
            batch_id = None
            if line.sale_line_id and line.sale_line.warehouse_batch_id:
                batch_id = line.sale_line.warehouse_batch_id
            elif instance.sale and instance.sale.warehouse_batch_id:
                batch_id = instance.sale.warehouse_batch_id
            warehouse_effects.append(
                {
                    'type': 'warehouse_effect',
                    'source_return_line_id': line.id,
                    'warehouse_batch_id': batch_id,
                    'quantity': api_decimal_str(Decimal(str(line.quantity or 0))),
                },
            )
        defects = DefectRecord.objects.filter(
            source_type=DefectRecord.SOURCE_RETURN,
            source_id__in=line_ids,
        ).values('id', 'status', 'source_id')
        defect_ids = [d['id'] for d in defects]
        reworks = ReworkRequest.objects.filter(defect_record_id__in=defect_ids).values(
            'id', 'status', 'defect_record_id', 'result_warehouse_batch_id',
        )
        data['linked_entities'] = {
            'sale': {
                'id': instance.sale_id,
                'label': instance.sale.order_number,
            },
            'client': (
                {
                    'id': instance.sale.client_id,
                    'label': instance.sale.client.name,
                } if instance.sale and instance.sale.client_id else None
            ),
            'linked_order': (
                {
                    'id': instance.linked_order_id,
                    'label': instance.linked_order.order_number,
                } if instance.linked_order_id else None
            ),
        }
        for d in defects:
            downstream.append({
                'type': 'defect_record',
                'id': d['id'],
                'status': d['status'],
                'source_return_line_id': d['source_id'],
            })
        for r in reworks:
            downstream.append({
                'type': 'rework_request',
                'id': r['id'],
                'status': r['status'],
                'defect_record_id': r['defect_record_id'],
                'result_warehouse_batch_id': r['result_warehouse_batch_id'],
            })
        active_refunds = Payment.objects.filter(
            linked_return=instance,
            payment_type=Payment.TYPE_REFUND,
            status=Payment.STATUS_ACTIVE,
        ).values('id', 'payment_number', 'amount', 'status')
        for p in active_refunds:
            downstream.append(
                {
                    'type': 'refund_payment',
                    'id': p['id'],
                    'payment_number': p['payment_number'],
                    'amount': api_decimal_str(Decimal(str(p['amount'] or 0))),
                    'status': p['status'],
                },
            )
        downstream.extend(warehouse_effects)
        data['downstream_links'] = downstream
        data['available_status_transitions'] = []
        data['available_actions'] = {
            'waybill': True,
            'complete': instance.status == Return.STATUS_DRAFT,
        }
        return Response(data)

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        sale_id = request.query_params.get('sale_id')
        sales_qs = Sale.objects.select_related('client').filter(
            sale_status__in=(Sale.STATUS_SHIPPED, Sale.STATUS_CLOSED),
        ).order_by('-date', '-id')
        sales_payload = []
        for sale in sales_qs[:200]:
            sale_lines = SaleLine.objects.filter(sale=sale).values('id', 'product', 'quantity', 'unit_price')
            total_returnable = Decimal('0')
            for sl in sale_lines:
                sold = Decimal(str(sl['quantity'] or 0))
                returned = ReturnLine.objects.filter(sale_line_id=sl['id']).exclude(
                    return_doc__status=Return.STATUS_CANCELED,
                ).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
                returnable = sold - Decimal(str(returned))
                if returnable > 0:
                    total_returnable += returnable
            if total_returnable <= 0:
                continue
            sales_payload.append(
                {
                    'id': sale.id,
                    'label': f'{sale.sale_number or sale.order_number} — {sale.client.name if sale.client_id else "—"} — доступно к возврату {api_decimal_str(total_returnable)}',
                    'client': sale.client_id,
                    'client_name': sale.client.name if sale.client_id else '',
                    'sale_status': sale.sale_status,
                    'returnable_quantity': api_decimal_str(total_returnable),
                },
            )
        lines = []
        if sale_id:
            sale_lines = (
                SaleLine.objects.filter(sale_id=sale_id)
                .order_by('id')
                .values('id', 'product', 'quantity', 'unit_price')
            )
            for sl in sale_lines:
                sold = Decimal(str(sl['quantity'] or 0))
                returned = ReturnLine.objects.filter(sale_line_id=sl['id']).exclude(
                    return_doc__status=Return.STATUS_CANCELED,
                ).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
                returnable = sold - Decimal(str(returned))
                if returnable <= 0:
                    continue
                sold_s = api_decimal_str(sold)
                ret_s = api_decimal_str(Decimal(str(returned)))
                retable_s = api_decimal_str(returnable)
                unit_price_s = api_decimal_str(Decimal(str(sl['unit_price'] or 0)))
                lines.append(
                    {
                        'id': sl['id'],
                        'label': f"{sl['product']} — продано {sold_s} — возвращено {ret_s} — доступно {retable_s}",
                        'product': sl['product'],
                        'sold_quantity': sold_s,
                        'returned_quantity': ret_s,
                        'returnable_quantity': retable_s,
                        'unit_price': unit_price_s,
                    },
                )
        return Response({
            'sales': sales_payload,
            'sale_lines': lines,
        })

    @action(detail=True, methods=['get'], url_path='waybill')
    def waybill(self, request, pk=None):
        """HTML-документ возврата (канонический endpoint)."""
        ret_doc = self.get_object()
        return _return_html_response(ret_doc)


def _return_html_response(ret_doc: Return) -> HttpResponse:
    parts = [
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
        f'<title>Возврат {escape(ret_doc.return_number or str(ret_doc.id))}</title>',
        '<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.45;}',
        'table{border-collapse:collapse;width:100%;}td,th{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;}',
        'th{background:#f5f5f5;}</style>',
        '</head><body>',
        '<h1>Акт возврата товара</h1>',
        f'<p><strong>№</strong> {escape(ret_doc.return_number or str(ret_doc.id))} '
        f'<strong>от</strong> {ret_doc.date.isoformat()}</p>',
        f'<p><strong>К продаже №:</strong> {escape(ret_doc.sale.order_number)}</p>',
    ]
    if ret_doc.sale.client_id:
        parts.append(f'<p><strong>Клиент:</strong> {escape(ret_doc.sale.client.name)}</p>')
    if ret_doc.return_reason:
        parts.append(f'<p><strong>Причина возврата:</strong> {escape(ret_doc.return_reason)}</p>')
    parts.append(
        '<table><thead><tr>'
        '<th>№</th><th>Товар</th><th>Кол-во</th><th>Состояние</th><th>Назначение</th><th>Комментарий</th>'
        '</tr></thead><tbody>'
    )
    for i, line in enumerate(ret_doc.lines.all(), 1):
        parts.append(
            f'<tr>'
            f'<td>{i}</td>'
            f'<td>{escape(line.product or "")}</td>'
            f'<td>{escape(str(line.quantity))}</td>'
            f'<td>{escape(line.get_condition_type_display())}</td>'
            f'<td>{escape(line.get_return_target_display())}</td>'
            f'<td>{escape(line.comment or "")}</td>'
            f'</tr>'
        )
    parts.append('</tbody></table>')
    if ret_doc.comment:
        parts.append(f'<p><strong>Комментарий:</strong> {escape(ret_doc.comment)}</p>')
    parts.append('<p><em>Сформировано автоматически.</em></p></body></html>')
    html = ''.join(parts)
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    resp['Content-Disposition'] = f'inline; filename="return-waybill-{ret_doc.id}.html"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT RECORD (Брак)
# ─────────────────────────────────────────────────────────────────────────────

class DefectRecordViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = DefectRecord.objects.select_related('profile', 'created_by').all()
    serializer_class = DefectRecordSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'defects'
    activity_section = 'Брак'
    activity_label = 'запись брака'
    ordering_fields = ['id', 'created_at', 'status']
    filterset_class = DefectFilter

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'code': 'DELETE_DISABLED', 'error': 'Удаление записей брака отключено (используйте writeoff / cancel сценарии).'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        inst = serializer.instance
        from apps.realtime.broadcast import schedule_push
        from django.db import transaction
        transaction.on_commit(lambda: schedule_push(
            resource='defect_record',
            action='created',
            entity_id=inst.pk,
            extra={'source_type': inst.source_type, 'status': inst.status},
        ))

    def perform_update(self, serializer):
        super().perform_update(serializer)
        inst = serializer.instance
        from apps.realtime.broadcast import schedule_push
        from django.db import transaction
        transaction.on_commit(lambda: schedule_push(
            resource='defect_record',
            action='updated',
            entity_id=inst.pk,
            extra={'status': inst.status},
        ))

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = DefectRecordSerializer(instance, context={'request': request}).data
        from .state_machine import DEFECT_TRANSITIONS
        linked_source = None
        if instance.source_type == DefectRecord.SOURCE_RETURN and instance.source_id:
            rl = ReturnLine.objects.select_related('return_doc', 'sale_line').filter(pk=instance.source_id).first()
            if rl is not None:
                linked_source = {
                    'return_line_id': rl.pk,
                    'return_doc_id': rl.return_doc_id,
                    'sale_line_id': rl.sale_line_id,
                    'label': f"ReturnLine #{rl.pk}: {rl.product} × {rl.quantity}",
                }
        reworks = list(
            ReworkRequest.objects.filter(defect_record=instance).values(
                'id', 'status', 'result_warehouse_batch_id', 'original_sale_id',
            )
        )
        data['linked_entities'] = {
            'source': linked_source,
            'rework_requests': reworks,
        }
        data['available_status_transitions'] = DEFECT_TRANSITIONS.get(instance.status, [])
        rem = Decimal(str(instance.quantity_pcs or 0))
        has_remainder = rem > Decimal('0.0001')
        data['available_actions'] = {
            'send_to_rework': has_remainder and DefectRecord.STATUS_SENT_TO_REWORK in DEFECT_TRANSITIONS.get(instance.status, []),
            'complete_rework': False,
            'writeoff': has_remainder and DefectRecord.STATUS_WRITTEN_OFF in DEFECT_TRANSITIONS.get(instance.status, []),
            'sell': has_remainder and instance.status in (
                DefectRecord.STATUS_NEW,
                DefectRecord.STATUS_ON_STOCK,
                DefectRecord.STATUS_REWORKED,
            ),
        }
        return Response(data)

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        from apps.warehouse.models import WarehouseBatch

        lines = (
            ReturnLine.objects.select_related('return_doc')
            .exclude(
                id__in=DefectRecord.objects.filter(source_type=DefectRecord.SOURCE_RETURN).values_list('source_id', flat=True),
            )
            .order_by('-id')
            .values('id', 'product', 'quantity', 'return_doc_id', 'return_doc__return_number', 'condition_type')[:300]
        )
        wh_def = list(
            WarehouseBatch.objects.filter(
                quality=WarehouseBatch.QUALITY_DEFECT,
                status=WarehouseBatch.STATUS_AVAILABLE,
                quantity__gt=0,
                linked_defect_record__isnull=True,
            )
            .order_by('-date', '-id')
            .values('id', 'product', 'quantity', 'quality', 'status')[:200]
        )
        return Response({
            'return_lines': [
                {
                    'id': rl['id'],
                    'label': (
                        f"{rl['product']} — возврат {api_decimal_str(Decimal(str(rl['quantity'] or 0)))} шт — "
                        f"причина: {rl['condition_type']}"
                    ),
                    'product': rl['product'],
                    'quantity_pcs': api_decimal_str(Decimal(str(rl['quantity'] or 0))),
                    'return_id': rl['return_doc_id'],
                    'return_number': rl['return_doc__return_number'] or f"RET-{rl['return_doc_id']}",
                }
                for rl in lines
            ],
            'warehouse_defect_batches': [
                {
                    'id': b['id'],
                    'label': f"#{b['id']} — {b['product']} — доступно {api_decimal_str(Decimal(str(b['quantity'] or 0)))} шт — Брак",
                    'product': b['product'],
                    'available_quantity_pcs': api_decimal_str(Decimal(str(b['quantity'] or 0))),
                    'quantity_pcs': api_decimal_str(Decimal(str(b['quantity'] or 0))),
                    'quality': b['quality'],
                    'status': b['status'],
                }
                for b in wh_def
            ],
        })

    @action(detail=True, methods=['post'], url_path='send-to-rework')
    def send_to_rework(self, request, pk=None):
        """Создать ReworkRequest; опционально часть остатка (body quantity)."""
        from django.utils import timezone as tz

        from .state_machine import validate_defect_transition

        record = self.get_object()
        if ReworkRequest.objects.filter(
            defect_record=record,
            status__in=(ReworkRequest.STATUS_PENDING, ReworkRequest.STATUS_IN_PROGRESS),
        ).exists():
            return _err('REWORK_ACTIVE', 'По этому браку уже есть активная переделка', http_status=422)
        rem_before = Decimal(str(record.quantity_pcs or 0))
        if rem_before <= 0:
            return _err('NO_QUANTITY', 'Нет остатка для отправки в переделку', http_status=422)
        qty_raw = request.data.get('quantity')
        try:
            send_qty = rem_before if qty_raw in (None, '') else Decimal(str(qty_raw))
        except Exception:
            return _err('INVALID_DECIMAL', 'Некорректное quantity', http_status=422)
        if send_qty <= 0:
            return _err('INVALID_QUANTITY', 'quantity должно быть > 0', http_status=422)
        if send_qty > rem_before + Decimal('0.0001'):
            return _err('QTY_TOO_HIGH', f'Нельзя отправить больше остатка ({rem_before})', http_status=422)
        eps = Decimal('0.0001')
        if send_qty >= rem_before - eps:
            try:
                validate_defect_transition(record.status, DefectRecord.STATUS_SENT_TO_REWORK)
            except ValueError as e:
                return _err('INVALID_STATUS', str(e), http_status=422)
        year = tz.now().date().year
        last = ReworkRequest.objects.filter(rework_number__startswith=f'RWK-{year}-').order_by('-rework_number').first()
        try:
            last_n = int(last.rework_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        rw_n = f'RWK-{year}-{last_n + 1:04d}'
        user = request.user if request.user.is_authenticated else None
        try:
            qmap = rework_quantities_from_defect_record(record, pcs_to_send=send_qty)
        except ValueError as e:
            return _err('NO_QUANTITY', str(e), http_status=422)
        kg_before = record.quantity_kg
        kg_before_d = Decimal(str(kg_before)) if kg_before is not None else None
        with transaction.atomic():
            record = DefectRecord.objects.select_for_update().get(pk=record.pk)
            record.sent_to_rework_quantity_pcs = Decimal(str(record.sent_to_rework_quantity_pcs or 0)) + send_qty
            record.recompute_remaining_pcs()
            if kg_before_d is not None and rem_before > 0 and kg_before_d > 0:
                kg_delta = (send_qty / rem_before * kg_before_d).quantize(Decimal('0.0001'))
                record.quantity_kg = max(Decimal('0'), (kg_before_d - kg_delta).quantize(Decimal('0.0001')))
            record.apply_terminal_status_from_counters()
            record.save(
                update_fields=[
                    'sent_to_rework_quantity_pcs', 'quantity_pcs', 'quantity_kg',
                    'status', 'updated_at',
                ],
            )
            rw = ReworkRequest.objects.create(
                return_doc=None,
                defect_record=record,
                original_sale=None,
                product=record.product,
                quantity_pcs=qmap['quantity_pcs'],
                quantity_kg=qmap['quantity_kg'],
                status=ReworkRequest.STATUS_PENDING,
                rework_number=rw_n,
                created_by=user,
            )
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='defects',
            description=f'Брак → переделка: defect #{record.pk}, {rw_n}',
            model_cls=DefectRecord,
            instance=record,
            payload_extra={'action': 'send_defect_to_rework', 'defect_id': record.pk, 'rework_id': rw.pk},
        )
        return Response({
            'defect': DefectRecordSerializer(record).data,
            'rework_request': ReworkRequestSerializer(rw).data,
        })

    @action(detail=True, methods=['post'], url_path='complete-rework')
    def complete_rework(self, request, pk=None):
        """Отключено: завершение переделки и выход на склад только через POST /api/rework-requests/{id}/complete/."""
        return Response(
            {
                'code': 'USE_REWORK_COMPLETE',
                'error': 'Завершение переделки только через POST /api/rework-requests/{id}/complete/',
                'detail': 'Завершение переделки только через POST /api/rework-requests/{id}/complete/',
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=['post'], url_path='writeoff')
    def writeoff(self, request, pk=None):
        """Списать брак (полностью или часть — body quantity)."""
        from .state_machine import validate_defect_transition, validate_defect_writeoff_qty

        record = self.get_object()
        reason = request.data.get('writeoff_reason', '').strip()
        if not reason:
            return _err('MISSING_REASON', 'Укажите writeoff_reason — причина списания обязательна')
        try:
            validate_defect_writeoff_qty(record)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        rem = Decimal(str(record.quantity_pcs or 0))
        qty_raw = request.data.get('quantity')
        try:
            wo_qty = rem if qty_raw in (None, '') else Decimal(str(qty_raw))
        except Exception:
            return _err('INVALID_DECIMAL', 'Некорректное quantity', http_status=422)
        if wo_qty <= 0:
            return _err('INVALID_QUANTITY', 'quantity должно быть > 0', http_status=422)
        if wo_qty > rem + Decimal('0.0001'):
            return _err('QTY_TOO_HIGH', f'Нельзя списать больше остатка ({rem})', http_status=422)
        eps = Decimal('0.0001')
        if wo_qty >= rem - eps:
            try:
                validate_defect_transition(record.status, DefectRecord.STATUS_WRITTEN_OFF)
            except ValueError as e:
                return _err('INVALID_STATUS', str(e), http_status=422)
        kg_before_d = Decimal(str(record.quantity_kg)) if record.quantity_kg is not None else None
        try:
            with transaction.atomic():
                record = DefectRecord.objects.select_for_update().get(pk=record.pk)
                record.written_off_quantity_pcs = Decimal(str(record.written_off_quantity_pcs or 0)) + wo_qty
                record.recompute_remaining_pcs()
                if kg_before_d is not None and rem > 0 and kg_before_d > 0:
                    kg_delta = (wo_qty / rem * kg_before_d).quantize(Decimal('0.0001'))
                    record.quantity_kg = max(Decimal('0'), (kg_before_d - kg_delta).quantize(Decimal('0.0001')))
                record.apply_terminal_status_from_counters()
                record.writeoff_reason = reason
                record.save(
                    update_fields=[
                        'written_off_quantity_pcs', 'quantity_pcs', 'quantity_kg',
                        'status', 'writeoff_reason', 'updated_at',
                    ],
                )
                if record.warehouse_batch_id:
                    from apps.warehouse.models import WarehouseBatch
                    from apps.warehouse.stock_ops import apply_sale_to_warehouse_batch

                    wb = WarehouseBatch.objects.select_for_update().get(pk=record.warehouse_batch_id)
                    apply_sale_to_warehouse_batch(wb.pk, wo_qty, wb.inventory_form, None)
        except ValidationError as e:
            det = getattr(e, 'detail', None)
            if isinstance(det, dict):
                msg = '; '.join(
                    f'{k}: {(v[0] if isinstance(v, list) else v)}' for k, v in det.items()
                )
            else:
                msg = str(det or e)
            return _err('WAREHOUSE_APPLY', msg, http_status=422)
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='defects',
            description=f'Списание брака #{record.pk}',
            model_cls=DefectRecord,
            instance=record,
            payload_extra={'action': 'writeoff_defect', 'defect_id': record.pk},
        )
        return Response(DefectRecordSerializer(record).data)

    @action(detail=True, methods=['post'], url_path='sell')
    def sell_defect(self, request, pk=None):
        """Продажа брака — создаёт Sale с is_defect_sale=True."""
        from django.utils import timezone
        from .state_machine import validate_defect_sell

        record = self.get_object()
        if record.status in (
            DefectRecord.STATUS_SOLD,
            DefectRecord.STATUS_WRITTEN_OFF,
            DefectRecord.STATUS_CLOSED,
        ):
            return _err('DEFECT_NOT_AVAILABLE', 'Нельзя продать запись брака в финальном статусе.', http_status=422)
        try:
            validate_defect_sell(record)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        client_id = request.data.get('client_id')
        price = request.data.get('price')
        quantity_raw = request.data.get('quantity')
        if client_id is None or client_id == '':
            return _err('MISSING_CLIENT', 'Укажите client_id', http_status=422)
        if price is None or str(price).strip() == '':
            return _err('MISSING_PRICE', 'Укажите price', http_status=422)
        if quantity_raw is None or str(quantity_raw).strip() == '':
            return _err('MISSING_QUANTITY', 'Укажите quantity (> 0, не больше остатка по браку)', http_status=422)
        try:
            client_pk = int(client_id)
        except Exception:
            return _err('MISSING_CLIENT', 'Укажите корректный client_id', http_status=422)
        client = Client.objects.filter(pk=client_pk).first()
        if client is None:
            return _err('MISSING_CLIENT', 'Клиент не найден', http_status=422)
        if not client.is_active:
            return _err('INACTIVE_CLIENT', 'Нельзя продавать брак неактивному клиенту.', http_status=422)
        try:
            price_d = Decimal(str(price))
        except Exception:
            return _err('INVALID_PRICE', 'Некорректный price', http_status=422)
        try:
            qty_d = Decimal(str(quantity_raw))
        except Exception:
            return _err('INVALID_QUANTITY', 'Некорректный quantity', http_status=422)
        if price_d <= 0:
            return _err('INVALID_PRICE', 'price должен быть > 0', http_status=422)
        if qty_d <= 0:
            return _err('INVALID_QUANTITY', 'quantity должен быть > 0', http_status=422)
        avail = Decimal(str(record.quantity_pcs or 0))
        if avail <= 0:
            return _err('DEFECT_NOT_AVAILABLE', 'Нет доступного остатка брака для продажи.', http_status=422)
        if qty_d > avail + Decimal('0.0001'):
            return _err('QUANTITY_EXCEEDED', f'Нельзя продать больше остатка по браку ({avail})', http_status=422)
        quantity = qty_d
        kg_before_d = Decimal(str(record.quantity_kg)) if record.quantity_kg is not None else None
        comment = request.data.get('comment', '')
        date = request.data.get('date') or timezone.now().date()

        wb_id = record.warehouse_batch_id
        stock_form = ''
        stock_quality = ''
        if wb_id:
            from apps.warehouse.models import WarehouseBatch

            try:
                wb_hdr = WarehouseBatch.objects.get(pk=wb_id)
            except WarehouseBatch.DoesNotExist:
                wb_hdr = None
                wb_id = None
            if wb_hdr is not None:
                stock_form = wb_hdr.inventory_form or ''
                stock_quality = (wb_hdr.quality or '').strip()

        from .sale_warehouse import apply_warehouse_for_sale

        try:
            with transaction.atomic():
                sale = Sale.objects.create(
                    order_number='',
                    product=record.product,
                    quantity=Decimal(str(quantity)),
                    sold_pieces=Decimal(str(quantity)),
                    price=price_d,
                    revenue=(price_d * quantity).quantize(Decimal('0.01')),
                    cost=Decimal('0'),
                    profit=Decimal('0'),
                    date=date,
                    comment=comment or f'Продажа брака #{record.id}',
                    is_defect_sale=True,
                    sale_status=Sale.STATUS_SHIPPED,
                    client_id=client.pk,
                    warehouse_batch_id=wb_id,
                    stock_form=stock_form,
                    stock_quality=stock_quality,
                )
                year = sale.date.year
                last = Sale.objects.filter(order_number__startswith=f'ORD-{year}-').exclude(pk=sale.pk).order_by('-order_number').first()
                try:
                    last_n = int(last.order_number.split('-')[-1]) if last else 0
                except (ValueError, IndexError):
                    last_n = 0
                sale.order_number = f'ORD-{year}-{last_n + 1:03d}'
                sale.save(update_fields=['order_number'])
                lt = (price_d * quantity).quantize(Decimal('0.01'))
                SaleLine.objects.create(
                    sale=sale,
                    product=record.product,
                    quantity=quantity,
                    unit_price=price_d,
                    line_total=lt,
                    cost=Decimal('0'),
                    profit=lt,
                )
                if wb_id:
                    applied = apply_warehouse_for_sale(Sale.objects.get(pk=sale.pk))
                    if not applied:
                        raise ValidationError({'warehouse_batch': 'Не удалось списать партию склада по продаже брака'})
                rec = DefectRecord.objects.select_for_update().get(pk=record.pk)
                rec.sold_quantity_pcs = Decimal(str(rec.sold_quantity_pcs or 0)) + quantity
                rec.recompute_remaining_pcs()
                if kg_before_d is not None and avail > 0 and kg_before_d > 0:
                    kg_delta = (quantity / avail * kg_before_d).quantize(Decimal('0.0001'))
                    rec.quantity_kg = max(Decimal('0'), (kg_before_d - kg_delta).quantize(Decimal('0.0001')))
                rec.apply_terminal_status_from_counters()
                rec.save(
                    update_fields=[
                        'sold_quantity_pcs', 'quantity_pcs', 'quantity_kg', 'status', 'updated_at',
                    ],
                )
        except ValidationError as e:
            det = getattr(e, 'detail', None)
            if isinstance(det, dict):
                msg = '; '.join(
                    f'{k}: {(v[0] if isinstance(v, list) else v)}' for k, v in det.items()
                )
            else:
                msg = str(det or e)
            return _err('WAREHOUSE_APPLY', msg, http_status=422)
        record.refresh_from_db()
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='defects',
            description=f'Продажа брака #{record.pk} → sale #{sale.pk}',
            model_cls=DefectRecord,
            instance=record,
            payload_extra={'action': 'sell_defect', 'defect_id': record.pk, 'sale_id': sale.pk},
        )
        return Response({'sale_id': sale.id, 'sale_order_number': sale.order_number}, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# REWORK REQUEST (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

class ReworkRequestViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ReworkRequest.objects.select_related(
        'return_doc', 'defect_record', 'defect_record__warehouse_batch', 'original_sale', 'result_warehouse_batch', 'created_by',
    ).all()
    serializer_class = ReworkRequestSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'defects'
    activity_section = 'Переделки'
    activity_label = 'переделка'
    ordering_fields = ['id', 'created_at', 'status']
    filterset_fields = ['status', 'original_sale']

    def destroy(self, request, *args, **kwargs):
        return Response(
            {'code': 'DELETE_DISABLED', 'error': 'Удаление переделок отключено. Используйте cancel/complete.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        inst = serializer.instance
        from apps.realtime.broadcast import schedule_push
        from django.db import transaction
        transaction.on_commit(lambda: schedule_push(
            resource='rework_request',
            action='created',
            entity_id=inst.pk,
            extra={'status': inst.status},
        ))

    def retrieve(self, request, *args, **kwargs):
        rework = self.get_object()
        data = ReworkRequestSerializer(rework, context={'request': request}).data
        from .state_machine import REWORK_TRANSITIONS
        data['linked_entities'] = {
            'return_doc': (
                {
                    'id': rework.return_doc_id,
                    'label': rework.return_doc.return_number or str(rework.return_doc_id),
                } if rework.return_doc_id else None
            ),
            'defect_record': (
                {
                    'id': rework.defect_record_id,
                    'label': f"#{rework.defect_record_id} {rework.defect_record.product}",
                } if rework.defect_record_id else None
            ),
            'original_sale': (
                {
                    'id': rework.original_sale_id,
                    'label': rework.original_sale.order_number,
                } if rework.original_sale_id else None
            ),
            'result_warehouse_batch': (
                {
                    'id': rework.result_warehouse_batch_id,
                    'label': f"#{rework.result_warehouse_batch_id} {rework.result_warehouse_batch.product}",
                } if rework.result_warehouse_batch_id else None
            ),
        }
        data['available_status_transitions'] = REWORK_TRANSITIONS.get(rework.status, [])
        data['available_actions'] = {
            'start': ReworkRequest.STATUS_IN_PROGRESS in REWORK_TRANSITIONS.get(rework.status, []),
            'complete': ReworkRequest.STATUS_COMPLETED in REWORK_TRANSITIONS.get(rework.status, []),
            'cancel': ReworkRequest.STATUS_CANCELED in REWORK_TRANSITIONS.get(rework.status, []),
        }
        return Response(data)

    @action(detail=False, methods=['get'], url_path='select-sources')
    def select_sources(self, request):
        from apps.warehouse.models import WarehouseBatch

        defects_qs = (
            DefectRecord.objects.exclude(
                status__in=(
                    DefectRecord.STATUS_SOLD,
                    DefectRecord.STATUS_WRITTEN_OFF,
                    DefectRecord.STATUS_CLOSED,
                ),
            )
            .select_related('warehouse_batch')
            .order_by('-created_at', '-id')[:300]
        )
        defect_records = []
        for d in defects_qs:
            pcs = Decimal(str(d.quantity_pcs or 0))
            kg_raw = d.quantity_kg
            kg = Decimal(str(kg_raw)) if kg_raw is not None else Decimal('0')
            if pcs <= 0 and kg <= 0:
                continue
            if pcs > 0:
                qty_part = f'{api_decimal_str(pcs)} шт'
            elif kg > 0:
                qty_part = f'{api_decimal_str(kg)} кг'
            else:
                qty_part = '—'
            reason = (d.defect_reason or '').strip() or '—'
            reason_short = (reason[:80] + '...') if len(reason) > 80 else reason
            label = f'{d.product or "—"} — {qty_part} — {reason_short} — {d.get_source_type_display()}'
            defect_records.append({
                'id': d.id,
                'label': label,
                'product_name': (d.product or '').strip(),
                'quantity_pcs': api_decimal_str(pcs) if pcs > 0 else None,
                'quantity_kg': api_decimal_str(kg) if kg > 0 else None,
                'defect_reason': (d.defect_reason or '').strip(),
                'source_type': d.source_type,
                'source_label': defect_record_source_label(d),
                'display_quantity': api_decimal_str(pcs) if pcs > 0 else api_decimal_str(kg) if kg > 0 else None,
                'display_quantity_label': qty_part if qty_part != '—' else None,
                'available_quantity_pcs': api_decimal_str(pcs) if pcs > 0 else None,
                'status': d.status,
            })
        sales = list(
            Sale.objects.order_by('-date', '-id').values('id', 'order_number', 'product')[:300]
        )
        returns = list(
            Return.objects.order_by('-date', '-id').values('id', 'return_number')[:300]
        )
        result_batches = list(
            WarehouseBatch.objects.filter(
                status=WarehouseBatch.STATUS_AVAILABLE,
                quality=WarehouseBatch.QUALITY_GOOD,
            )
            .order_by('-date', '-id')
            .values('id', 'product')[:300]
        )
        return Response({
            'defect_records': defect_records,
            'original_sales': [
                {'id': s['id'], 'label': f"{s['order_number']} / {s['product']}"}
                for s in sales
            ],
            'returns': [
                {'id': r['id'], 'label': r['return_number'] or f"RET#{r['id']}"}
                for r in returns
            ],
            'result_warehouse_batches': [
                {'id': b['id'], 'label': f"#{b['id']} {b['product']}"}
                for b in result_batches
            ],
        })

    @action(detail=True, methods=['post'], url_path='start')
    def start_rework(self, request, pk=None):
        """Перевести переделку в статус «В работе»."""
        from .state_machine import validate_rework_transition
        rework = self.get_object()
        try:
            validate_rework_transition(rework.status, ReworkRequest.STATUS_IN_PROGRESS)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        rework.status = ReworkRequest.STATUS_IN_PROGRESS
        rework.save(update_fields=['status', 'updated_at'])
        return Response(ReworkRequestSerializer(rework).data)

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        """
        Завершить переделку: создать новую партию ГП (good или defect) по output_quantity / loss_quantity.
        result_warehouse_batch_id опционален (legacy); при отсутствии — партия создаётся в backend.
        """
        from django.utils import timezone as tz

        from apps.warehouse.models import WarehouseBatch
        from .state_machine import validate_rework_complete
        from django.db import transaction as db_transaction
        from apps.realtime.broadcast import schedule_push

        rework = self.get_object()
        if rework.status == ReworkRequest.STATUS_COMPLETED:
            return _err('REWORK_ALREADY_COMPLETED', 'Переделка уже завершена.', http_status=422)
        if rework.status == ReworkRequest.STATUS_CANCELED:
            return _err('REWORK_ALREADY_CANCELED', 'Отмененную переделку нельзя завершить.', http_status=422)
        if rework.status != ReworkRequest.STATUS_IN_PROGRESS:
            return _err('REWORK_COMPLETE_FORBIDDEN', 'Завершить переделку можно только из статуса in_progress.', http_status=422)
        try:
            validate_rework_complete(rework)
        except ValueError as e:
            return _err('INVALID_TRANSITION', str(e), http_status=422)

        out_raw = request.data.get('output_quantity', request.data.get('output_quantity_kg'))
        loss_raw = request.data.get('loss_quantity', request.data.get('loss_kg'))
        quality_raw = request.data.get('quality')
        if quality_raw is None or str(quality_raw).strip() == '':
            return _err('MISSING_FIELDS', 'Укажите quality.', http_status=422)
        out_quality = str(quality_raw).strip()
        if out_raw is None or loss_raw is None:
            return _err('MISSING_FIELDS', 'Укажите output_quantity (или output_quantity_kg) и loss_quantity (или loss_kg)')
        try:
            output_pcs = Decimal(str(out_raw))
            loss_pcs = Decimal(str(loss_raw))
        except Exception:
            return _err('INVALID_QUANTITY', 'Некорректные output_quantity/loss_quantity', http_status=422)
        if output_pcs < 0 or loss_pcs < 0:
            return _err('NEGATIVE_QUANTITY', 'output_quantity и loss_quantity должны быть >= 0', http_status=422)
        if out_quality not in (WarehouseBatch.QUALITY_GOOD, WarehouseBatch.QUALITY_DEFECT):
            return _err('INVALID_QUALITY', 'quality: good | defect', http_status=422)
        defect = rework.defect_record
        if defect is None:
            return _err('NO_DEFECT', 'Нет defect_record', http_status=422)
        if rework.quantity_pcs is not None and Decimal(str(rework.quantity_pcs or 0)) > 0:
            input_pcs = Decimal(str(rework.quantity_pcs))
        elif rework.quantity_kg is not None and Decimal(str(rework.quantity_kg or 0)) > 0:
            input_pcs = Decimal(str(rework.quantity_kg))
        else:
            input_pcs = Decimal(str(defect.quantity_pcs or 0))
        if input_pcs and output_pcs + loss_pcs > input_pcs + Decimal('0.0001'):
            return _err('QTY_BOUNDS', f'output+loss не должно превышать вход ({input_pcs})', http_status=422)

        tpl = getattr(defect, 'warehouse_batch', None)
        with transaction.atomic():
            wb = WarehouseBatch.objects.create(
                profile_id=defect.profile_id,
                product=defect.product,
                length_per_piece=tpl.length_per_piece if tpl else None,
                quantity=output_pcs,
                cost_per_piece=tpl.cost_per_piece if tpl else Decimal('0'),
                cost_per_meter=tpl.cost_per_meter if tpl else Decimal('0'),
                date=tz.now().date(),
                source_batch_id=tpl.source_batch_id if tpl else None,
                inventory_form=tpl.inventory_form if tpl else WarehouseBatch.INVENTORY_UNPACKED,
                quality=out_quality,
                defect_reason=(defect.defect_reason or '')[:2000] if out_quality == WarehouseBatch.QUALITY_DEFECT else '',
            )
            if loss_pcs and loss_pcs > 0 and tpl:
                pass
            rework.output_quantity_kg = output_pcs
            rework.loss_kg = loss_pcs
            if rework.output_quantity_kg is not None:
                out_d = Decimal(str(rework.output_quantity_kg))
                input_d = None
                if rework.quantity_pcs is not None and Decimal(str(rework.quantity_pcs)) > 0:
                    input_d = Decimal(str(rework.quantity_pcs))
                elif rework.quantity_kg and Decimal(str(rework.quantity_kg)) > 0:
                    input_d = Decimal(str(rework.quantity_kg))
                if input_d is not None and input_d > 0:
                    rework.conversion_rate = (out_d / input_d).quantize(Decimal('0.000001'))
            rework.status = ReworkRequest.STATUS_COMPLETED
            rework.result_warehouse_batch = wb
            rework.save()
            if rework.defect_record_id:
                DefectRecord.objects.filter(pk=rework.defect_record_id).update(
                    status=DefectRecord.STATUS_REWORKED,
                )

        db_transaction.on_commit(lambda: schedule_push(
            resource='rework_request',
            action='updated',
            entity_id=rework.pk,
            extra={'status': ReworkRequest.STATUS_COMPLETED},
        ))
        from .commercial_audit import log_commercial_audit
        log_commercial_audit(
            user=request.user,
            request=request,
            section='rework',
            description=f'Завершение переделки {rework.rework_number}',
            model_cls=ReworkRequest,
            instance=rework,
            payload_extra={'action': 'complete_rework', 'rework_id': rework.pk},
        )
        rework.refresh_from_db()
        return Response(ReworkRequestSerializer(rework).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel_rework(self, request, pk=None):
        """Отменить переделку."""
        from .state_machine import validate_rework_transition
        rework = self.get_object()
        if rework.status == ReworkRequest.STATUS_COMPLETED:
            return _err('REWORK_ALREADY_COMPLETED', 'Завершенную переделку нельзя отменить.', http_status=422)
        if rework.status == ReworkRequest.STATUS_CANCELED:
            return _err('REWORK_ALREADY_CANCELED', 'Переделка уже отменена.', http_status=422)
        if rework.status not in (ReworkRequest.STATUS_PENDING, ReworkRequest.STATUS_IN_PROGRESS):
            return _err('REWORK_CANCEL_FORBIDDEN', 'Отмена переделки разрешена только из pending/in_progress.', http_status=422)
        try:
            validate_rework_transition(rework.status, ReworkRequest.STATUS_CANCELED)
        except ValueError as e:
            return _err('INVALID_TRANSITION', str(e), http_status=422)
        rpc = Decimal(str(rework.quantity_pcs or 0))
        rkg = Decimal(str(rework.quantity_kg or 0)) if rework.quantity_kg is not None else Decimal('0')
        try:
            with transaction.atomic():
                rw2 = ReworkRequest.objects.select_for_update().get(pk=rework.pk)
                rw2.status = ReworkRequest.STATUS_CANCELED
                rw2.save(update_fields=['status', 'updated_at'])
                if rw2.defect_record_id and (rpc > 0 or rkg > 0):
                    dr = DefectRecord.objects.select_for_update().get(pk=rw2.defect_record_id)
                    dr.sent_to_rework_quantity_pcs = max(
                        Decimal('0'),
                        Decimal(str(dr.sent_to_rework_quantity_pcs or 0)) - rpc,
                    )
                    dr.recompute_remaining_pcs()
                    if dr.quantity_kg is not None and rkg > 0:
                        dr.quantity_kg = (Decimal(str(dr.quantity_kg or 0)) + rkg).quantize(Decimal('0.0001'))
                    if dr.status == DefectRecord.STATUS_SENT_TO_REWORK:
                        dr.status = DefectRecord.STATUS_ON_STOCK
                    dr.save(
                        update_fields=[
                            'sent_to_rework_quantity_pcs', 'quantity_pcs', 'quantity_kg',
                            'status', 'updated_at',
                        ],
                    )
        except Exception as e:
            return _err('WAREHOUSE_ROLLBACK', str(e), http_status=422)
        rework.refresh_from_db()
        return Response(ReworkRequestSerializer(rework).data)


# ─────────────────────────────────────────────────────────────────────────────
# PRICE LIST (Прайс-лист)
# ─────────────────────────────────────────────────────────────────────────────

class PriceListViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = PriceList.objects.prefetch_related('product_prices', 'product_prices__profile').all()
    serializer_class = PriceListSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    activity_section = 'Прайсы'
    activity_label = 'прайс-лист'

    @action(detail=False, methods=['get'], url_path='suggest-price')
    def suggest_price(self, request):
        """
        GET /api/price-lists/suggest-price/?client_id=&profile_id=&product=
        Возвращает рекомендованную цену по приоритету:
          1. Индивидуальная цена клиента
          2. Базовый прайс
          3. null
        """
        from .pricing import suggest_price, price_suggestion_to_dict
        from datetime import date

        client_id = request.query_params.get('client_id')
        profile_id = request.query_params.get('profile_id')
        product = request.query_params.get('product')
        date_raw = request.query_params.get('date')

        on_date = None
        if date_raw:
            try:
                from datetime import datetime
                on_date = datetime.strptime(date_raw[:10], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass

        suggestion = suggest_price(
            client_id=int(client_id) if client_id else None,
            profile_id=int(profile_id) if profile_id else None,
            product=product,
            on_date=on_date,
        )
        return Response(price_suggestion_to_dict(suggestion))


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT PRICE (Индивидуальные цены клиента)
# ─────────────────────────────────────────────────────────────────────────────

class ClientPriceViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ClientPrice.objects.select_related('client', 'profile').all()
    serializer_class = ClientPriceSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    activity_section = 'Цены клиентов'
    activity_label = 'цена клиента'
    filterset_fields = ['client', 'profile']


# ─────────────────────────────────────────────────────────────────────────────
# ORDER RESERVATION (Резервы — отдельный список)
# ─────────────────────────────────────────────────────────────────────────────

class OrderReservationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Только чтение. Управление резервами — через /orders/{id}/reserve/
    """
    queryset = OrderReservation.objects.select_related(
        'order_line', 'order_line__order', 'warehouse_batch', 'created_by',
    ).all()
    serializer_class = OrderReservationSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'client_orders'
    filterset_fields = ['status', 'order_line', 'warehouse_batch']


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT FINANCIAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

class ClientFinancialSummaryView(viewsets.ViewSet):
    """
    Канонический endpoint:
    GET /api/client-financial-summary/?client_id=<id>

    Полная финансовая сводка по клиенту.
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'

    @extend_schema(
        summary='Финансовая сводка клиента',
        description='Канонический endpoint: GET /api/client-financial-summary/?client_id=<id>',
        parameters=[
            OpenApiParameter(
                name='client_id',
                type=int,
                required=True,
                location=OpenApiParameter.QUERY,
                description='ID клиента',
            ),
        ],
    )
    def list(self, request):
        from config.api_numbers import api_decimal_str
        from .credit_check import check_credit_limit
        from .payment_status import payment_status
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        client_id = request.query_params.get('client_id')
        if not client_id:
            return _err('MISSING_PARAM', 'Укажите client_id')

        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return _err('NOT_FOUND', 'Клиент не найден', http_status=404)

        sales = Sale.objects.filter(client=client).exclude(
            sale_status__in=(Sale.STATUS_DRAFT, Sale.STATUS_CANCELED),
        )
        payments = Payment.objects.filter(client=client, status=Payment.STATUS_ACTIVE)

        total_revenue = sales.aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t'] or Decimal('0')
        total_cost = (
            sales.exclude(is_defect_sale=True).aggregate(t=Coalesce(Sum('cost'), Decimal('0')))['t']
        ) or Decimal('0')
        total_profit = total_revenue - total_cost
        defect_revenue = (
            sales.filter(is_defect_sale=True).aggregate(t=Coalesce(Sum('revenue'), Decimal('0')))['t']
        ) or Decimal('0')

        total_incoming = (
            payments.filter(
                payment_type__in=[Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE]
            ).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
        ) or Decimal('0')
        total_refunded = (
            payments.filter(payment_type=Payment.TYPE_REFUND)
            .aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']
        ) or Decimal('0')
        net_paid = total_incoming - total_refunded

        client_debt = max(Decimal('0'), total_revenue - net_paid)
        client_advance = max(Decimal('0'), net_paid - total_revenue)

        credit_result = check_credit_limit(client)
        fin_payment_status = payment_status(
            total_due=total_revenue,
            net_paid=net_paid,
            total_incoming=total_incoming,
            total_refund=total_refunded,
        )

        return Response({
            'client_id': client.pk,
            'client_name': client.name,
            'payment_status': fin_payment_status,
            'total_revenue': api_decimal_str(total_revenue),
            'total_cost': api_decimal_str(total_cost),
            'total_profit': api_decimal_str(total_profit),
            'defect_revenue': api_decimal_str(defect_revenue),
            'total_paid_gross': api_decimal_str(total_incoming),
            'total_refunded': api_decimal_str(total_refunded),
            'total_paid_net': api_decimal_str(net_paid),
            'client_debt_money': api_decimal_str(client_debt),
            'client_advance_amount': api_decimal_str(client_advance),
            'credit_limit': api_decimal_str(credit_result.credit_limit) if credit_result.credit_limit is not None else None,
            'credit_limit_mode': credit_result.block_mode,
            'credit_available': api_decimal_str(credit_result.credit_available) if credit_result.credit_available is not None else None,
            'is_over_limit': credit_result.is_over_limit,
            'credit_warning': credit_result.warning,
        })
