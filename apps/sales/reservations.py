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


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _log_reservation_event(
    *,
    reservation: OrderReservation,
    action: str,
    description: str,
    user=None,
    request=None,
    payload_extra: dict | None = None,
) -> None:
    """Записать событие резерва в UserActivity через schedule_entity_audit."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return
    try:
        from apps.activity.audit_service import schedule_entity_audit, instance_to_snapshot
        after_snap = instance_to_snapshot(reservation)
        schedule_entity_audit(
            user=user,
            request=request,
            section='reservations',
            description=description,
            action=action,
            model_cls=OrderReservation,
            after=after_snap,
            after_instance=reservation,
            payload_extra=payload_extra or {},
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def reserve_order_line(
    order_line: OrderLine,
    warehouse_batch,
    quantity: Decimal,
    user=None,
    comment: str = '',
    request=None,
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

        already_reserved_on_batch = _active_reserved_qty_on_batch(wb.pk)
        available_on_batch = Decimal(str(wb.quantity)) - already_reserved_on_batch
        if quantity > available_on_batch:
            raise ValueError(
                f'Недостаточно свободного остатка в партии: '
                f'доступно {available_on_batch}, запрошено {quantity}'
            )

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

        line.reserved_quantity = (
            Decimal(str(line.reserved_quantity or 0)) + quantity
        ).quantize(Decimal('0.0001'))
        line.save(update_fields=['reserved_quantity'])

    _log_reservation_event(
        reservation=reservation,
        action='create',
        description=(
            f'Резерв создан: партия #{wb.pk}, '
            f'строка заявки #{line.pk}, кол-во {quantity}'
        ),
        user=user,
        request=request,
        payload_extra={
            'order_line_id': line.pk,
            'warehouse_batch_id': wb.pk,
            'quantity': str(quantity),
        },
    )
    return reservation


def release_reservation(
    reservation: OrderReservation,
    user=None,
    request=None,
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

        qty = Decimal(str(reservation.quantity))
        new_reserved = max(
            Decimal('0'),
            Decimal(str(line.reserved_quantity or 0)) - qty,
        ).quantize(Decimal('0.0001'))
        line.reserved_quantity = new_reserved
        line.save(update_fields=['reserved_quantity'])

    _log_reservation_event(
        reservation=reservation,
        action='update',
        description=(
            f'Резерв снят: партия #{reservation.warehouse_batch_id}, '
            f'строка заявки #{reservation.order_line_id}, кол-во {reservation.quantity}'
        ),
        user=user,
        request=request,
        payload_extra={
            'order_line_id': reservation.order_line_id,
            'warehouse_batch_id': reservation.warehouse_batch_id,
            'quantity': str(reservation.quantity),
            'previous_status': OrderReservation.STATUS_ACTIVE,
        },
    )
    return reservation


def fulfill_reservation(
    reservation: OrderReservation,
    fulfilled_quantity: Decimal | None = None,
    sale_line=None,
    user=None,
    request=None,
) -> OrderReservation:
    """
    Пометить резерв как полностью или частично исполненный.

    - fulfilled_quantity=None → исполняется вся сумма резерва
    - fulfilled_quantity < reservation.quantity → резерв остаётся активным
      с уменьшенным количеством (частичное исполнение)
    - fulfilled_quantity >= reservation.quantity → резерв переходит в FULFILLED
    """
    if reservation.status != OrderReservation.STATUS_ACTIVE:
        return reservation

    qty_total = Decimal(str(reservation.quantity))
    fq = Decimal(str(fulfilled_quantity)) if fulfilled_quantity is not None else qty_total

    if fq <= 0:
        return reservation

    with transaction.atomic():
        reservation = OrderReservation.objects.select_for_update().get(pk=reservation.pk)
        if reservation.status != OrderReservation.STATUS_ACTIVE:
            return reservation

        qty_total = Decimal(str(reservation.quantity))
        already_fulfilled = Decimal(str(reservation.fulfilled_quantity or 0))
        new_fulfilled = (already_fulfilled + fq).quantize(Decimal('0.0001'))

        if fq >= qty_total:
            reservation.status = OrderReservation.STATUS_FULFILLED
            reservation.fulfilled_quantity = qty_total
            if sale_line is not None:
                reservation.sale_line = sale_line
            reservation.save(update_fields=['status', 'fulfilled_quantity', 'sale_line', 'updated_at'])

            line = OrderLine.objects.select_for_update().get(pk=reservation.order_line_id)
            new_reserved = max(
                Decimal('0'),
                Decimal(str(line.reserved_quantity or 0)) - qty_total,
            ).quantize(Decimal('0.0001'))
            line.reserved_quantity = new_reserved
            line.save(update_fields=['reserved_quantity'])
        else:
            reservation.fulfilled_quantity = new_fulfilled
            reservation.quantity = (qty_total - fq).quantize(Decimal('0.0001'))
            if sale_line is not None:
                reservation.sale_line = sale_line
            reservation.save(update_fields=['quantity', 'fulfilled_quantity', 'sale_line', 'updated_at'])

    _log_reservation_event(
        reservation=reservation,
        action='update',
        description=(
            f'Резерв исполнен ({reservation.status}): '
            f'партия #{reservation.warehouse_batch_id}, '
            f'исполнено {fq} из {qty_total}'
        ),
        user=user,
        request=request,
        payload_extra={
            'order_line_id': reservation.order_line_id,
            'warehouse_batch_id': reservation.warehouse_batch_id,
            'fulfilled_quantity': str(fq),
            'total_quantity': str(qty_total),
            'sale_line_id': sale_line.pk if sale_line else None,
        },
    )
    return reservation


def auto_fulfill_for_sale(
    *,
    sale,
    order,
    warehouse_batch_id: int,
    quantity: Decimal,
    user=None,
    request=None,
    sale_line=None,
) -> int:
    """
    При создании продажи (Sale) автоматически исполнить активные резервы
    по данному заказу и партии на запрошенное количество.

    Также обновляет OrderLine.shipped_quantity для связанных строк заявки.

    Возвращает количество обновлённых резервов.
    """
    if sale_line is None:
        sale_line = (
            sale.sale_lines.filter(warehouse_batch_id=warehouse_batch_id)
            .order_by('id')
            .first()
        )
        if sale_line is None:
            sale_line = sale.sale_lines.order_by('id').first()
    if sale_line is None:
        return 0

    fulfilled_count = 0
    remaining = Decimal(str(quantity))

    with transaction.atomic():
        line_ids = list(order.lines.values_list('id', flat=True))

        active_reservations = (
            OrderReservation.objects.select_for_update()
            .filter(
                order_line_id__in=line_ids,
                warehouse_batch_id=warehouse_batch_id,
                status=OrderReservation.STATUS_ACTIVE,
            )
            .order_by('created_at')
        )

        for res in active_reservations:
            if remaining <= 0:
                break
            res_qty = Decimal(str(res.quantity))
            take = min(res_qty, remaining)
            fulfill_reservation(
                res,
                fulfilled_quantity=take,
                sale_line=sale_line,
                user=user,
                request=request,
            )
            remaining -= take
            fulfilled_count += 1

            # Update OrderLine.shipped_quantity
            try:
                line = OrderLine.objects.select_for_update().get(pk=res.order_line_id)
                line.shipped_quantity = (
                    Decimal(str(getattr(line, 'shipped_quantity', None) or 0)) + take
                ).quantize(Decimal('0.0001'))
                line.save(update_fields=['shipped_quantity'])
            except OrderLine.DoesNotExist:
                pass

    return fulfilled_count


def restore_reservations_for_sale(
    *,
    sale,
    user=None,
    request=None,
) -> int:
    """
    При отмене/удалении Sale восстановить (снять fulfilled-пометку) резервы,
    которые были исполнены этой продажей, и откатить shipped_quantity.

    Возвращает количество восстановленных резервов.
    """
    restored_count = 0
    with transaction.atomic():
        line_ids = list(sale.sale_lines.values_list('id', flat=True))
        fulfilled = (
            OrderReservation.objects.select_for_update()
            .filter(
                sale_line_id__in=line_ids,
                status=OrderReservation.STATUS_FULFILLED,
            )
        )
        for res in fulfilled:
            fq = Decimal(str(res.fulfilled_quantity or res.quantity))
            res.status = OrderReservation.STATUS_ACTIVE
            res.quantity = (Decimal(str(res.quantity)) + fq).quantize(Decimal('0.0001'))
            res.fulfilled_quantity = Decimal('0')
            res.sale_line = None
            res.save(update_fields=['status', 'quantity', 'fulfilled_quantity', 'sale_line', 'updated_at'])

            try:
                line = OrderLine.objects.select_for_update().get(pk=res.order_line_id)
                line.reserved_quantity = (
                    Decimal(str(line.reserved_quantity or 0)) + fq
                ).quantize(Decimal('0.0001'))
                line.shipped_quantity = max(
                    Decimal('0'),
                    Decimal(str(getattr(line, 'shipped_quantity', None) or 0)) - fq,
                ).quantize(Decimal('0.0001'))
                line.save(update_fields=['reserved_quantity', 'shipped_quantity'])
            except OrderLine.DoesNotExist:
                pass

            _log_reservation_event(
                reservation=res,
                action='update',
                description=(
                    f'Резерв восстановлен при отмене продажи #{getattr(sale, "pk", "?")} : '
                    f'партия #{res.warehouse_batch_id}, строка заявки #{res.order_line_id}'
                ),
                user=user,
                request=request,
                payload_extra={
                    'sale_id': getattr(sale, 'pk', None),
                    'restored_quantity': str(fq),
                },
            )
            restored_count += 1
    return restored_count


def release_all_for_order(order: Order, user=None, request=None) -> int:
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
            release_reservation(res, user=user, request=request)
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

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
