"""
Централизованные правила переходов статусов для коммерческого контура.

Правила валидируются ДО применения изменения. При нарушении поднимается
ValueError с понятным сообщением.
"""
from __future__ import annotations

from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Order (Заявка)
# ─────────────────────────────────────────────────────────────────────────────

ORDER_TRANSITIONS: dict[str, list[str]] = {
    'new':               ['confirmed', 'canceled'],
    'confirmed':         ['in_progress', 'canceled'],
    'in_progress':       ['partially_shipped', 'shipped', 'canceled'],
    'partially_shipped': ['shipped', 'closed', 'canceled'],
    'shipped':           ['closed'],
    'closed':            [],
    'canceled':          [],
}


def validate_order_transition(current: str, new: str) -> None:
    allowed = ORDER_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValueError(
            f'Заявка: недопустимый переход статуса из «{current}» в «{new}». '
            f'Допустимо: {allowed or "нет доступных переходов"}'
        )


def validate_order_cancel(order) -> None:
    """
    Проверить дополнительные условия перед отменой заявки.
    Если по заявке уже есть продажи в статусе не-черновик — запрещаем отмену без явного снятия.
    """
    from .models import Sale
    active_sales = order.sales.exclude(
        sale_status__in=[Sale.STATUS_DRAFT, Sale.STATUS_CANCELED]
    )
    if active_sales.exists():
        raise ValueError(
            'Нельзя отменить заявку: по ней уже есть активные продажи. '
            'Сначала отмените или закройте связанные продажи.'
        )


def validate_order_close(order) -> None:
    """
    Проверки перед закрытием заявки.

    - Все строки заявки должны быть полностью отгружены или явно сняты.
    - Открытые активные резервы должны отсутствовать.
    - Политика: если ordered_quantity > shipped_quantity по хотя бы одной строке
      И есть активные резервы — запрещаем закрытие.
    """
    from decimal import Decimal
    from .models import OrderReservation

    # Проверяем активные резервы
    line_ids = list(order.lines.values_list('id', flat=True))
    active_res = OrderReservation.objects.filter(
        order_line_id__in=line_ids,
        status=OrderReservation.STATUS_ACTIVE,
    ).exists()
    if active_res:
        raise ValueError(
            'Нельзя закрыть заявку: по ней остались активные резервы. '
            'Сначала снимите резервы или исполните продажи.'
        )

    # Проверяем незакрытые строки (отгружено < заказано)
    for line in order.lines.all():
        ordered = Decimal(str(line.ordered_quantity or 0))
        shipped = Decimal(str(getattr(line, 'shipped_quantity', None) or 0))
        if shipped < ordered:
            raise ValueError(
                f'Нельзя закрыть заявку: строка #{line.pk} ({line.product}) '
                f'не полностью отгружена ({shipped} из {ordered}). '
                f'Частичное закрытие разрешено только в статусе "partially_shipped".'
            )


# ─────────────────────────────────────────────────────────────────────────────
# Sale (Продажа)
# ─────────────────────────────────────────────────────────────────────────────

SALE_TRANSITIONS: dict[str, list[str]] = {
    'draft':             ['confirmed', 'canceled'],
    'confirmed':         ['partially_shipped', 'shipped', 'canceled'],
    'partially_shipped': ['shipped', 'closed', 'canceled'],
    'shipped':           ['closed'],
    'closed':            [],
    'canceled':          [],
}


def validate_sale_transition(current: str, new: str) -> None:
    allowed = SALE_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValueError(
            f'Продажа: недопустимый переход статуса из «{current}» в «{new}». '
            f'Допустимо: {allowed or "нет доступных переходов"}'
        )


def validate_sale_ship(sale, quantity: Optional[float] = None) -> None:
    """
    Бизнес-проверки перед отгрузкой/закрытием продажи:
      - партия должна быть доступна
      - остаток партии должен покрывать количество
      - кредитный лимит (hard) должен быть проверен вызывающей стороной отдельно
    """
    from decimal import Decimal

    lines = list(sale.sale_lines.all())
    if lines:
        any_wb = False
        for sl in lines:
            if not sl.warehouse_batch_id:
                continue
            any_wb = True
            wb = sl.warehouse_batch
            if wb.status not in ('available', 'reserved'):
                raise ValueError(
                    f'Партия склада #{wb.pk} недоступна для отгрузки (статус: {wb.status})'
                )
            avail = Decimal(str(wb.quantity))
            qty = Decimal(str(sl.quantity))
            if qty > avail:
                raise ValueError(
                    f'Нельзя отгрузить {qty} шт. по строке #{sl.pk}: на партии доступно только {avail} шт.'
                )
        if any_wb:
            return

    if sale.warehouse_batch_id:
        wb = sale.warehouse_batch
        if wb.status not in ('available', 'reserved'):
            raise ValueError(
                f'Партия склада #{wb.pk} недоступна для отгрузки (статус: {wb.status})'
            )
        avail = Decimal(str(wb.quantity))
        qty = Decimal(str(quantity)) if quantity is not None else Decimal(str(sale.quantity))
        if qty > avail:
            raise ValueError(
                f'Нельзя отгрузить {qty} шт.: на партии доступно только {avail} шт.'
            )


# ─────────────────────────────────────────────────────────────────────────────
# Return (Возврат)
# ─────────────────────────────────────────────────────────────────────────────

def validate_return_quantity(sale_line, return_quantity) -> None:
    """
    Нельзя вернуть больше, чем отгружено по строке продажи.
    """
    from decimal import Decimal
    from .models import Return, ReturnLine

    already_returned = sum(
        rl.quantity
        for rl in ReturnLine.objects.filter(sale_line=sale_line).exclude(
            return_doc__status=Return.STATUS_CANCELED,
        )
    )
    total = Decimal(str(already_returned)) + Decimal(str(return_quantity))
    if total > Decimal(str(sale_line.quantity)):
        raise ValueError(
            f'Нельзя вернуть {return_quantity}: по строке продажи отгружено '
            f'{sale_line.quantity}, уже возвращено {already_returned}'
        )


# ─────────────────────────────────────────────────────────────────────────────
# DefectRecord (Брак)
# ─────────────────────────────────────────────────────────────────────────────

DEFECT_TRANSITIONS: dict[str, list[str]] = {
    'new':            ['on_stock', 'sent_to_rework', 'written_off'],
    'on_stock':       ['sent_to_rework', 'sold', 'written_off'],
    'sent_to_rework': ['reworked', 'on_stock'],
    'reworked':       ['sold', 'written_off'],
    'sold':           [],
    'written_off':    [],
}


def validate_defect_transition(current: str, new: str) -> None:
    allowed = DEFECT_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValueError(
            f'Брак: недопустимый переход статуса из «{current}» в «{new}». '
            f'Допустимо: {allowed or "нет доступных переходов"}'
        )


def validate_defect_sell(defect_record) -> None:
    allowed_statuses = ('on_stock', 'reworked')
    if defect_record.status not in allowed_statuses:
        raise ValueError(
            f'Нельзя продать брак из статуса «{defect_record.get_status_display()}». '
            f'Допустимо только из: {allowed_statuses}'
        )


# ─────────────────────────────────────────────────────────────────────────────
# ReworkRequest (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

REWORK_TRANSITIONS: dict[str, list[str]] = {
    'pending':     ['in_progress', 'canceled'],
    'in_progress': ['completed', 'canceled'],
    'completed':   [],
    'canceled':    [],
}


def validate_rework_transition(current: str, new: str) -> None:
    allowed = REWORK_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValueError(
            f'Переделка: недопустимый переход статуса из «{current}» в «{new}». '
            f'Допустимо: {allowed or "нет доступных переходов"}'
        )


def validate_rework_complete(rework_request) -> None:
    if rework_request.status != 'in_progress':
        raise ValueError(
            'Завершить переделку можно только из статуса «В работе». '
            f'Текущий статус: «{rework_request.get_status_display()}»'
        )
