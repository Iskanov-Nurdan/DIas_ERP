"""
Пересчёт отгруженного по заявке из SaleLine; валидация статусов заявки.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from .models import Order, OrderLine, Sale, SaleLine


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def recalculate_order_line_shipped_from_sale_lines_for_order(order: Order | int) -> None:
    """
    Источник правды: сумма quantity по SaleLine, продажа привязана к заявке, статус в «отгрузке».
    """
    oid = order if isinstance(order, int) else order.pk
    order = (
        order if not isinstance(order, int) else Order.objects.get(pk=oid)
    )
    # В текущем бизнес-процессе списание склада выполняется при создании продажи,
    # поэтому draft/confirmed также учитываются как фактическая отгрузка для заявки.
    ship_states = (
        Sale.STATUS_DRAFT,
        Sale.STATUS_CONFIRMED,
        Sale.STATUS_PARTIALLY_SHIPPED,
        Sale.STATUS_SHIPPED,
        Sale.STATUS_CLOSED,
    )
    for line in order.lines.all():
        agg = (
            SaleLine.objects.filter(
                order_line=line,
                sale__linked_order_id=oid,
                sale__sale_status__in=ship_states,
            )
            .aggregate(s=Sum('quantity'))['s']
        )
        sq = _d(agg)
        if sq != _d(line.shipped_quantity):
            OrderLine.objects.filter(pk=line.pk).update(shipped_quantity=sq)


def sync_order_shipping_status(order: Order | int) -> Order:
    """
    Пересчитывает shipped по строкам заявки и обновляет статус заявки по фактической отгрузке:
    - полностью отгружено -> closed
    - частично отгружено -> partially_shipped
    - не отгружено -> без ложного перехода
    """
    oid = order if isinstance(order, int) else order.pk
    recalculate_order_line_shipped_from_sale_lines_for_order(oid)
    order_obj = Order.objects.prefetch_related('lines').get(pk=oid)

    lines = [ln for ln in order_obj.lines.all() if _d(ln.ordered_quantity) > 0]
    if lines:
        any_shipped = any(_d(ln.shipped_quantity) > 0 for ln in lines)
        all_full = all(_d(ln.shipped_quantity) >= _d(ln.ordered_quantity) for ln in lines)
    else:
        # Производственные заявки могут не иметь order.lines.
        # Тогда ориентируемся на production_quantity и суммарно проданное по linked_order.
        ship_states = (
            Sale.STATUS_DRAFT,
            Sale.STATUS_CONFIRMED,
            Sale.STATUS_PARTIALLY_SHIPPED,
            Sale.STATUS_SHIPPED,
            Sale.STATUS_CLOSED,
        )
        sold_total = Sale.objects.filter(
            linked_order_id=order_obj.pk,
            sale_status__in=ship_states,
        ).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
        planned_total = _d(order_obj.production_quantity)
        any_shipped = _d(sold_total) > 0
        all_full = planned_total > 0 and _d(sold_total) >= planned_total

    new_status = order_obj.status
    if all_full:
        new_status = Order.STATUS_CLOSED
    elif any_shipped:
        new_status = Order.STATUS_PARTIALLY_SHIPPED

    update_fields = []
    if new_status != order_obj.status:
        order_obj.status = new_status
        update_fields.append('status')

    # Для производственных заявок синхронизируем request_status с фактом отгрузки:
    # полная отгрузка -> финал по отгрузке (request_status очищаем, чтобы UI брал closed из order.status),
    # частичная -> in_production,
    # без отгрузки -> без ложного перехода.
    if order_obj.request_status and order_obj.request_status != Order.REQUEST_STATUS_REJECTED:
        if all_full and order_obj.request_status is not None:
            order_obj.request_status = None
            update_fields.append('request_status')
        elif any_shipped and order_obj.request_status != Order.REQUEST_STATUS_IN_PRODUCTION:
            order_obj.request_status = Order.REQUEST_STATUS_IN_PRODUCTION
            update_fields.append('request_status')

    if update_fields:
        update_fields.append('updated_at')
        order_obj.save(update_fields=update_fields)
    return order_obj


def validate_order_for_new_status(order: Order, new_status: str) -> None:
    from .models import Order as O
    from .models import OrderReservation
    from .state_machine import validate_order_close

    recalculate_order_line_shipped_from_sale_lines_for_order(order)
    order = Order.objects.prefetch_related('lines').get(pk=order.pk)
    if new_status == O.STATUS_SHIPPED:
        for line in order.lines.all():
            if _d(line.shipped_quantity) < _d(line.ordered_quantity):
                raise ValueError(
                    f'Нельзя перевести в «отгружено»: строка #{line.id} не полностью отгружена '
                    f'({line.shipped_quantity} из {line.ordered_quantity})'
                )
        line_ids = list(order.lines.values_list('id', flat=True))
        if OrderReservation.objects.filter(
            order_line_id__in=line_ids,
            status=OrderReservation.STATUS_ACTIVE,
        ).exists():
            raise ValueError('Нельзя: по заявке есть активные резервы, не освобождённые в продажу')
    if new_status == O.STATUS_PARTIALLY_SHIPPED:
        with_qty = [ln for ln in order.lines.all() if _d(ln.ordered_quantity) > 0]
        if not with_qty:
            raise ValueError('Нет строк заявки с ненулевым заказом')
        any_s = any(_d(ln.shipped_quantity) > 0 for ln in with_qty)
        all_full = all(
            _d(ln.shipped_quantity) >= _d(ln.ordered_quantity) for ln in with_qty
        )
        for line in order.lines.all():
            if _d(line.shipped_quantity) > _d(line.ordered_quantity) + Decimal('0.0001'):
                raise ValueError(f'Отгружено по строке #{line.id} больше, чем заказано')
        if not any_s or all_full:
            raise ValueError(
                'Статус «частично отгружено»: по строкам с заказом должна быть отгрузка, '
                'и хотя бы одна строка не полностью отгружена'
            )
    if new_status == O.STATUS_CLOSED:
        validate_order_close(order)
