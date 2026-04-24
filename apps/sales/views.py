import logging
from decimal import Decimal
from html import escape

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse

from rest_framework import viewsets, status
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
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
    ReworkRequest,
    Sale,
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
                sales_count=Count('sales', distinct=False),
                sales_total=Coalesce(
                    Sum('sales__revenue'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                ),
            )
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        sales_count = instance.sales.count()
        if sales_count:
            return Response(
                {
                    'code': 'CLIENT_IN_USE',
                    'error': 'Нельзя удалить клиента: есть связанные продажи.',
                    'detail': (
                        'Сначала удалите или переназначьте продажи, привязанные к этому клиенту '
                        '(или оставьте клиента в справочнике для истории).'
                    ),
                    'sales_count': sales_count,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

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
        payments = Payment.objects.filter(client=client).order_by('-date')
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
        total_revenue = sum((s.revenue or Decimal('0')) for s in sales)

        # Долг или аванс
        if net_paid < total_revenue:
            client_debt_money = total_revenue - net_paid
            client_advance_amount = Decimal('0')
        else:
            client_debt_money = Decimal('0')
            client_advance_amount = net_paid - total_revenue

        # Прибыль по клиенту
        total_cost = sum((s.cost or Decimal('0')) for s in sales if not s.is_defect_sale)
        defect_revenue = sum((s.revenue or Decimal('0')) for s in sales if s.is_defect_sale)
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

    @action(detail=True, methods=['patch'], url_path='status')
    def set_status(self, request, pk=None):
        """Изменить статус заявки с валидацией переходов."""
        order = self.get_object()
        new_status = request.data.get('status')
        if not new_status:
            return _err('MISSING_STATUS', 'Укажите status')

        allowed = {
            Order.STATUS_NEW: [Order.STATUS_CONFIRMED, Order.STATUS_CANCELED],
            Order.STATUS_CONFIRMED: [Order.STATUS_IN_PROGRESS, Order.STATUS_CANCELED],
            Order.STATUS_IN_PROGRESS: [
                Order.STATUS_PARTIALLY_SHIPPED, Order.STATUS_SHIPPED, Order.STATUS_CANCELED,
            ],
            Order.STATUS_PARTIALLY_SHIPPED: [Order.STATUS_SHIPPED, Order.STATUS_CLOSED, Order.STATUS_CANCELED],
            Order.STATUS_SHIPPED: [Order.STATUS_CLOSED],
            Order.STATUS_CLOSED: [],
            Order.STATUS_CANCELED: [],
        }
        if new_status not in allowed.get(order.status, []):
            return _err(
                'INVALID_STATUS_TRANSITION',
                f'Нельзя перейти из «{order.get_status_display()}» в «{new_status}»',
                http_status=422,
            )

        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])
        self._broadcast(order, created=False)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['get'], url_path='nakladnaya')
    def nakladnaya(self, request, pk=None):
        """HTML-накладная по заявке."""
        order = self.get_object()
        return _order_html_response(order)

    @action(detail=True, methods=['get'], url_path='history')
    def order_history(self, request, pk=None):
        """Трассировка: заявка → продажи → возвраты → переделки."""
        order = self.get_object()
        sales = Sale.objects.filter(linked_order=order).prefetch_related('sale_lines')
        payments = Payment.objects.filter(linked_order=order)
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
    resp['Content-Disposition'] = f'inline; filename="order-{order.id}.html"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# SALE (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

class SaleViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Sale.objects.select_related(
        'client', 'warehouse_batch', 'warehouse_batch__profile', 'linked_order',
    ).prefetch_related('sale_lines').all()
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

    def perform_destroy(self, instance):
        """Shipment.sale — PROTECT; иначе DELETE продажи даёт 500."""
        from django.db import transaction

        with transaction.atomic():
            Shipment.objects.filter(sale_id=instance.pk).delete()
            super().perform_destroy(instance)

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
        payments = list(sale.payments.all())
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
        resp['Content-Disposition'] = f'inline; filename="nakladnaya-{sale.id}.html"'
        return resp

    def _serve_nakladnaya(self, request, *args, **kwargs):
        sale = self.get_object()
        return SaleViewSet._nakladnaya_html_response(sale)

    @action(detail=True, methods=['get'], url_path='nakladnaya')
    def nakladnaya(self, request, pk=None):
        return self._serve_nakladnaya(request)

    @action(detail=True, methods=['get'], url_path='waybill')
    def waybill(self, request, pk=None):
        return self._serve_nakladnaya(request)

    @action(detail=True, methods=['get'], url_path='invoice')
    def invoice(self, request, pk=None):
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

    @action(detail=True, methods=['get'], url_path='receipt')
    def receipt(self, request, pk=None):
        """HTML-квитанция об оплате."""
        sale = self.get_object()
        payments = list(sale.payments.all())
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
        resp['Content-Disposition'] = f'inline; filename="receipt-{sale.id}.html"'
        return resp


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT (Оплата)
# ─────────────────────────────────────────────────────────────────────────────

class PaymentViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Payment.objects.select_related('client', 'linked_order', 'linked_sale', 'created_by').all()
    serializer_class = PaymentSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'payments'
    activity_section = 'Оплаты'
    activity_label = 'оплата'
    ordering_fields = ['id', 'date']
    filterset_class = PaymentFilter

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

        payments = Payment.objects.filter(client=client)
        sales = Sale.objects.filter(client=client)

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

    @action(detail=True, methods=['get'], url_path='nakladnaya')
    def nakladnaya(self, request, pk=None):
        """HTML-документ возврата."""
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
    resp['Content-Disposition'] = f'inline; filename="return-{ret_doc.id}.html"'
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

    @action(detail=True, methods=['post'], url_path='send-to-rework')
    def send_to_rework(self, request, pk=None):
        """Передать брак на переработку."""
        from .state_machine import validate_defect_transition
        record = self.get_object()
        try:
            validate_defect_transition(record.status, DefectRecord.STATUS_SENT_TO_REWORK)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        record.status = DefectRecord.STATUS_SENT_TO_REWORK
        record.save(update_fields=['status', 'updated_at'])
        return Response(DefectRecordSerializer(record).data)

    @action(detail=True, methods=['post'], url_path='complete-rework')
    def complete_rework(self, request, pk=None):
        """Завершить переработку брака."""
        from .state_machine import validate_defect_transition
        record = self.get_object()
        try:
            validate_defect_transition(record.status, DefectRecord.STATUS_REWORKED)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        record.status = DefectRecord.STATUS_REWORKED
        record.save(update_fields=['status', 'updated_at'])
        return Response(DefectRecordSerializer(record).data)

    @action(detail=True, methods=['post'], url_path='writeoff')
    def writeoff(self, request, pk=None):
        """Списать брак."""
        from .state_machine import validate_defect_transition
        record = self.get_object()
        reason = request.data.get('writeoff_reason', '').strip()
        if not reason:
            return _err('MISSING_REASON', 'Укажите writeoff_reason — причина списания обязательна')
        try:
            validate_defect_transition(record.status, DefectRecord.STATUS_WRITTEN_OFF)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        record.status = DefectRecord.STATUS_WRITTEN_OFF
        record.writeoff_reason = reason
        record.save(update_fields=['status', 'writeoff_reason', 'updated_at'])
        return Response(DefectRecordSerializer(record).data)

    @action(detail=True, methods=['post'], url_path='sell')
    def sell_defect(self, request, pk=None):
        """Продажа брака — создаёт Sale с is_defect_sale=True."""
        from django.utils import timezone
        from .state_machine import validate_defect_sell

        record = self.get_object()
        try:
            validate_defect_sell(record)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        client_id = request.data.get('client_id')
        price = request.data.get('price')
        quantity = request.data.get('quantity', record.quantity_pcs)
        comment = request.data.get('comment', '')
        date = request.data.get('date') or timezone.now().date()

        sale = Sale.objects.create(
            order_number='',
            product=record.product,
            quantity=Decimal(str(quantity)),
            sold_pieces=Decimal(str(quantity)),
            price=Decimal(str(price)) if price else None,
            revenue=(Decimal(str(price)) * Decimal(str(quantity))).quantize(Decimal('0.01')) if price else Decimal('0'),
            cost=Decimal('0'),
            profit=Decimal('0'),
            date=date,
            comment=comment or f'Продажа брака #{record.id}',
            is_defect_sale=True,
            sale_status=Sale.STATUS_SHIPPED,
            client_id=client_id,
        )
        # Автономер
        year = sale.date.year
        last = Sale.objects.filter(order_number__startswith=f'ORD-{year}-').exclude(pk=sale.pk).order_by('-order_number').first()
        try:
            last_n = int(last.order_number.split('-')[-1]) if last else 0
        except (ValueError, IndexError):
            last_n = 0
        sale.order_number = f'ORD-{year}-{last_n + 1:03d}'
        sale.save(update_fields=['order_number'])

        record.status = DefectRecord.STATUS_SOLD
        record.save(update_fields=['status', 'updated_at'])
        return Response({'sale_id': sale.id, 'sale_order_number': sale.order_number}, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# REWORK REQUEST (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

class ReworkRequestViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ReworkRequest.objects.select_related(
        'return_doc', 'defect_record', 'original_sale', 'result_warehouse_batch', 'created_by',
    ).all()
    serializer_class = ReworkRequestSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'defects'
    activity_section = 'Переделки'
    activity_label = 'переделка'
    ordering_fields = ['id', 'created_at', 'status']
    filterset_fields = ['status', 'original_sale']

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
        Завершить переделку — указать результирующую партию ГП,
        фактический выход (output_quantity_kg) и потери (loss_kg).
        """
        from .state_machine import validate_rework_complete
        from django.db import transaction as db_transaction

        rework = self.get_object()
        wb_id = request.data.get('result_warehouse_batch_id')
        if not wb_id:
            return _err('MISSING_BATCH', 'Укажите result_warehouse_batch_id')

        from apps.warehouse.models import WarehouseBatch
        try:
            wb = WarehouseBatch.objects.get(pk=wb_id)
        except WarehouseBatch.DoesNotExist:
            return _err('NOT_FOUND', 'Партия склада не найдена', http_status=404)

        try:
            validate_rework_complete(rework)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)

        update_fields = ['status', 'result_warehouse_batch', 'updated_at']

        output_kg_raw = request.data.get('output_quantity_kg')
        loss_kg_raw = request.data.get('loss_kg')

        if output_kg_raw is not None:
            rework.output_quantity_kg = Decimal(str(output_kg_raw))
            update_fields.append('output_quantity_kg')
        if loss_kg_raw is not None:
            rework.loss_kg = Decimal(str(loss_kg_raw))
            update_fields.append('loss_kg')
        elif rework.quantity_kg and rework.output_quantity_kg is not None:
            rework.loss_kg = max(
                Decimal('0'),
                Decimal(str(rework.quantity_kg)) - Decimal(str(rework.output_quantity_kg)),
            )
            update_fields.append('loss_kg')

        if rework.quantity_kg and rework.output_quantity_kg is not None:
            input_d = Decimal(str(rework.quantity_kg))
            if input_d > 0:
                rework.conversion_rate = (
                    Decimal(str(rework.output_quantity_kg)) / input_d
                ).quantize(Decimal('0.000001'))
                update_fields.append('conversion_rate')

        rework.status = ReworkRequest.STATUS_COMPLETED
        rework.result_warehouse_batch = wb
        rework.save(update_fields=list(set(update_fields)))

        # Обновить статус брака
        if rework.defect_record_id:
            DefectRecord.objects.filter(pk=rework.defect_record_id).update(status=DefectRecord.STATUS_REWORKED)

        from apps.realtime.broadcast import schedule_push
        db_transaction.on_commit(lambda: schedule_push(
            resource='rework_request',
            action='updated',
            entity_id=rework.pk,
            extra={'status': rework.status},
        ))
        rework.refresh_from_db()
        return Response(ReworkRequestSerializer(rework).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel_rework(self, request, pk=None):
        """Отменить переделку."""
        from .state_machine import validate_rework_transition
        rework = self.get_object()
        try:
            validate_rework_transition(rework.status, ReworkRequest.STATUS_CANCELED)
        except ValueError as e:
            return _err('INVALID_STATUS', str(e), http_status=422)
        rework.status = ReworkRequest.STATUS_CANCELED
        rework.save(update_fields=['status', 'updated_at'])
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
    GET /api/clients/{client_id}/financial-summary/
    Полная финансовая сводка по клиенту.
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'

    def list(self, request):
        from config.api_numbers import api_decimal_str
        from .credit_check import check_credit_limit, compute_client_debt
        from django.db.models import Sum
        from django.db.models.functions import Coalesce

        client_id = request.query_params.get('client_id')
        if not client_id:
            return _err('MISSING_PARAM', 'Укажите client_id')

        try:
            client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return _err('NOT_FOUND', 'Клиент не найден', http_status=404)

        sales = Sale.objects.filter(client=client).exclude(sale_status=Sale.STATUS_CANCELED)
        payments = Payment.objects.filter(client=client)

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

        return Response({
            'client_id': client.pk,
            'client_name': client.name,
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
