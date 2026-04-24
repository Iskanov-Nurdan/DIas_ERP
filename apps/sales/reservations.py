"""
Сервис резервирования товара под строку заявки.

Правила:
- резерв привязывается к конкретной партии склада ГП (WarehouseBatch)
- один резерв = одна строка заявки + одна партия
- нельзя зарезервировать больше доступного остатка партии
- нельзя зарезервировать больше, чем остаток по строке заявки
- при отмене заявки все активные резервы снимаются автоматически
- при отгрузке резерв помечается как исполненный
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from .models import Order, OrderLine, OrderReservation


def reserve_order_line(
    order_line: OrderLine,
    warehouse_batch,
    quantity: Decimal,
    user=None,
    comment: str = '',
) -> OrderReservation:
    """
    Зарезервировать quantity единиц партии warehouse_batch под строку заявки.

    Raises:
        ValueError — если нарушено любое из бизнес-правил.
    """
    from apps.warehouse.models import WarehouseBatch

    if quantity <= 0:
        raise ValueError('Количество резерва должно быть больше 0')

    with transaction.atomic():
        wb: WarehouseBatch = (
            WarehouseBatch.objects.select_for_update().get(pk=warehouse_batch.pk)
        )

        if wb.status == WarehouseBatch.STATUS_SHIPPED:
            raise ValueError('Партия уже полностью отгружена — резерв невозможен')

        if wb.quality == WarehouseBatch.QUALITY_DEFECT:
            raise ValueError('Нельзя резервировать партию брака под заявку клиента')

        # Сколько уже зарезервировано из этой партии (другими строками)
        already_reserved_on_batch = _active_reserved_qty_on_batch(wb.pk)
        available_on_batch = Decimal(str(wb.quantity)) - already_reserved_on_batch
        if quantity > available_on_batch:
            raise ValueError(
                f'Недостаточно свободного остатка в партии: '
                f'доступно {available_on_batch}, запрошено {quantity}'
            )

        # Проверяем лимит по строке заявки
        line = OrderLine.objects.select_for_update().get(pk=order_line.pk)
        already_reserved_on_line = _active_reserved_qty_on_line(line.pk)
        can_reserve_on_line = (
            Decimal(str(line.ordered_quantity)) - already_reserved_on_line
        )
        if quantity > can_reserve_on_line:
            raise ValueError(
                f'Превышение резерва по строке заявки: '
                f'можно ещё зарезервировать {can_reserve_on_line}, запрошено {quantity}'
            )

        reservation = OrderReservation.objects.create(
            order_line=line,
            warehouse_batch=wb,
            quantity=quantity,
            status=OrderReservation.STATUS_ACTIVE,
            created_by=user,
            comment=comment,
        )

        # Обновляем счётчик на строке заявки
        line.reserved_quantity = (
            Decimal(str(line.reserved_quantity or 0)) + quantity
        ).quantize(Decimal('0.0001'))
        line.save(update_fields=['reserved_quantity'])

    return reservation


def release_reservation(
    reservation: OrderReservation,
    user=None,
) -> OrderReservation:
    """Снять активный резерв."""
    if reservation.status != OrderReservation.STATUS_ACTIVE:
        raise ValueError(
            f'Нельзя снять резерв в статусе «{reservation.get_status_display()}»'
        )

    with transaction.atomic():
        line = OrderLine.objects.select_for_update().get(pk=reservation.order_line_id)
        reservation.status = OrderReservation.STATUS_RELEASED
        reservation.save(update_fields=['status', 'updated_at'])

        # Уменьшаем счётчик резерва на строке
        qty = Decimal(str(reservation.quantity))
        new_reserved = max(
            Decimal('0'),
            Decimal(str(line.reserved_quantity or 0)) - qty,
        ).quantize(Decimal('0.0001'))
        line.reserved_quantity = new_reserved
        line.save(update_fields=['reserved_quantity'])

    return reservation


def fulfill_reservation(
    reservation: OrderReservation,
) -> OrderReservation:
    """
    Пометить резерв как исполненный (при отгрузке).
    Вызывается из логики продажи. Счётчик не трогаем — shipped_quantity уже обновлён.
    """
    if reservation.status != OrderReservation.STATUS_ACTIVE:
        return reservation
    reservation.status = OrderReservation.STATUS_FULFILLED
    reservation.save(update_fields=['status', 'updated_at'])
    return reservation


def release_all_for_order(order: Order) -> int:
    """
    Снять все активные резервы по всем строкам заявки.
    Вызывается при отмене заявки. Возвращает число снятых резервов.
    """
    count = 0
    with transaction.atomic():
        line_ids = list(order.lines.values_list('id', flat=True))
        active_reservations = (
            OrderReservation.objects.select_for_update()
            .filter(order_line_id__in=line_ids, status=OrderReservation.STATUS_ACTIVE)
        )
        for res in active_reservations:
            release_reservation(res)
            count += 1
    return count


def _active_reserved_qty_on_batch(batch_pk: int) -> Decimal:
    """Сколько единиц партии занято активными резервами."""
    from django.db.models import Sum, Value
    from django.db.models.functions import Coalesce
    result = (
        OrderReservation.objects
        .filter(warehouse_batch_id=batch_pk, status=OrderReservation.STATUS_ACTIVE)
        .aggregate(total=Coalesce(Sum('quantity'), Value(Decimal('0'))))
    )
    return result['total'] or Decimal('0')


def _active_reserved_qty_on_line(line_pk: int) -> Decimal:
    """Сколько единиц зарезервировано по строке заявки (активные резервы)."""
    from django.db.models import Sum, Value
    from django.db.models.functions import Coalesce
    result = (
        OrderReservation.objects
        .filter(order_line_id=line_pk, status=OrderReservation.STATUS_ACTIVE)
        .aggregate(total=Coalesce(Sum('quantity'), Value(Decimal('0'))))
    )
    return result['total'] or Decimal('0')


def get_available_quantity(batch_pk: int) -> Decimal:
    """Свободный остаток партии (за вычетом активных резервов)."""
    from apps.warehouse.models import WarehouseBatch
    try:
        wb = WarehouseBatch.objects.get(pk=batch_pk)
    except WarehouseBatch.DoesNotExist:
        return Decimal('0')
    reserved = _active_reserved_qty_on_batch(batch_pk)
    return max(Decimal('0'), Decimal(str(wb.quantity)) - reserved)
