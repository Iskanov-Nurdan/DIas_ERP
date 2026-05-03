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
    Бизнес-проверки перед отгрузкой/закрытием обычной продажи (не is_defect_sale):
      - должна быть партия в строках и/или в шапке (см. legacy ниже)
      - партия в статусе «доступна», качество «годный» (как при реальном списании stock_ops)
      - остаток партии покрывает количество
    Продажи брака (is_defect_sale) не ограничиваются здесь — отдельные сценарии / DefectRecord.sell.
    """
    from decimal import Decimal

    from apps.warehouse.models import WarehouseBatch

    if getattr(sale, 'is_defect_sale', False):
        return

    def _assert_good_available(wb: WarehouseBatch, label: str) -> None:
        if wb.status != WarehouseBatch.STATUS_AVAILABLE:
            raise ValueError(
                f'{label}: партия склада #{wb.pk} недоступна для отгрузки (статус: {wb.status}). '
                f'Нужна партия в статусе «{WarehouseBatch.STATUS_AVAILABLE}».'
            )
        if wb.quality != WarehouseBatch.QUALITY_GOOD:
            raise ValueError(
                f'{label}: партия #{wb.pk} с качеством «брак» не может участвовать в обычной отгрузке.'
            )

    lines = list(sale.sale_lines.select_related('warehouse_batch').all())
    any_line_has_batch = any(sl.warehouse_batch_id for sl in lines)

    if any_line_has_batch:
        has_positive = False
        for sl in lines:
            qty = Decimal(str(sl.quantity or 0))
            if qty <= 0:
                continue
            has_positive = True
            if not sl.warehouse_batch_id:
                raise ValueError(
                    f'Строка продажи #{sl.pk}: для отгрузки укажите warehouse_batch.'
                )
            wb = sl.warehouse_batch
            _assert_good_available(wb, f'Строка #{sl.pk}')
            avail = Decimal(str(wb.quantity))
            if qty > avail:
                raise ValueError(
                    f'Нельзя отгрузить {qty} шт. по строке #{sl.pk}: на партии доступно только {avail} шт.'
                )
        if not has_positive:
            raise ValueError(
                'Для отгрузки по строкам продажи укажите quantity > 0 хотя бы в одной строке с партией.'
            )
        return

    if not sale.warehouse_batch_id:
        raise ValueError(
            'Для отгрузки укажите партию склада в шапке продажи (warehouse_batch) '
            'или у каждой отгружаемой строки (sale_lines[].warehouse_batch). '
            'Fallback через шапку разрешён только если ни у одной строки нет своей партии.'
        )

    wb = sale.warehouse_batch
    _assert_good_available(wb, 'Шапка продажи')
    qty = Decimal(str(quantity)) if quantity is not None else Decimal(str(sale.quantity or 0))
    if qty <= 0:
        raise ValueError('Количество отгрузки по шапке продажи должно быть > 0.')
    avail = Decimal(str(wb.quantity))
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
    'reworked':       ['sold', 'written_off', 'sent_to_rework'],
    'sold':           [],
    'written_off':    [],
    'closed':         [],
}


def validate_defect_transition(current: str, new: str) -> None:
    allowed = DEFECT_TRANSITIONS.get(current, [])
    if new not in allowed:
        raise ValueError(
            f'Брак: недопустимый переход статуса из «{current}» в «{new}». '
            f'Допустимо: {allowed or "нет доступных переходов"}'
        )


def validate_defect_sell(defect_record) -> None:
    allowed_statuses = ('new', 'on_stock', 'reworked')
    if defect_record.status not in allowed_statuses:
        raise ValueError(
            f'Нельзя продать брак из статуса «{defect_record.get_status_display()}». '
            f'Допустимо только из: {allowed_statuses}'
        )


def validate_defect_writeoff_qty(defect_record) -> None:
    """Частичное или полное списание: нужен положительный остаток quantity_pcs."""
    from decimal import Decimal

    allowed_statuses = ('new', 'on_stock', 'reworked')
    if defect_record.status not in allowed_statuses:
        raise ValueError(
            f'Нельзя списать брак из статуса «{defect_record.get_status_display()}». '
            f'Допустимо из: {allowed_statuses}'
        )
    if Decimal(str(defect_record.quantity_pcs or 0)) <= 0:
        raise ValueError('Нет остатка шт для списания по этой записи брака.')


# ─────────────────────────────────────────────────────────────────────────────
# ReworkRequest (Переделка)
# ─────────────────────────────────────────────────────────────────────────────

REWORK_TRANSITIONS: dict[str, list[str]] = {
    'pending':     ['in_progress', 'canceled', 'completed'],
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
    if rework_request.status not in ('pending', 'in_progress'):
        raise ValueError(
            'Завершить переделку можно из статусов «Ожидает» или «В работе». '
            f'Текущий статус: «{rework_request.get_status_display()}»'
        )
