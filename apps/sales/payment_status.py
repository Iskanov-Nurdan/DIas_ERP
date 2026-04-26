"""
Единая логика payment_status: unpaid, partially_paid, paid, overpaid, refunded.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

PAY_UNPAID = 'unpaid'
PAY_PARTIALLY_PAID = 'partially_paid'
PAY_PAID = 'paid'
PAY_OVERPAID = 'overpaid'
PAY_REFUNDED = 'refunded'


def payment_status(
    *,
    total_due: Decimal,
    net_paid: Decimal,
    total_incoming: Decimal = Decimal('0'),
    total_refund: Decimal = Decimal('0'),
) -> str:
    """
    total_due — сумма к оплате (заявка/продажа).
    net_paid — чисто оплачено (входящие − возвраты), только активные платежи.
    total_incoming, total_refund — для метки «refunded».
    """
    total_due = Decimal(str(total_due or 0))
    net_paid = Decimal(str(net_paid or 0))
    total_incoming = Decimal(str(total_incoming or 0))
    total_refund = Decimal(str(total_refund or 0))

    if total_due < 0:
        total_due = Decimal('0')

    if net_paid < 0:
        return PAY_REFUNDED

    if total_refund > 0 and total_incoming == 0 and total_due > 0:
        return PAY_UNPAID

    if total_due == 0:
        if net_paid > 0:
            return PAY_OVERPAID
        return PAY_PAID

    if total_refund > 0 and net_paid == 0 and total_due > 0 and total_incoming == total_refund:
        return PAY_REFUNDED

    if net_paid == 0:
        return PAY_UNPAID

    if 0 < net_paid < total_due:
        return PAY_PARTIALLY_PAID

    if net_paid == total_due:
        return PAY_PAID

    if net_paid > total_due:
        return PAY_OVERPAID

    return PAY_UNPAID


def sale_payment_metrics(sale) -> dict[str, Any]:
    from .models import Payment, Sale, SaleLine

    active = [p for p in sale.payments.all() if p.status == Payment.STATUS_ACTIVE]
    total_in = sum(
        (p.amount or Decimal('0'))
        for p in active
        if p.payment_type in (Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE)
    )
    ref = sum(
        (p.amount or Decimal('0')) for p in active if p.payment_type == Payment.TYPE_REFUND
    )
    net = total_in - ref
    lines = list(sale.sale_lines.all())
    if lines:
        due = sum(
            (Decimal(str(sl.line_total or 0)) for sl in lines),
            Decimal('0'),
        )
    else:
        due = sale.revenue or Decimal('0')
    st = payment_status(
        total_due=due, net_paid=net, total_incoming=total_in, total_refund=ref,
    )
    if st == PAY_REFUNDED and due > 0:
        debt = max(Decimal('0'), -net)
    else:
        debt = max(Decimal('0'), due - net)
    return {
        'payment_status': st,
        'paid_amount': net,
        'debt_amount': debt,
        'refund_amount': ref,
        'net_paid': net,
        'total_due': due,
    }


def order_payment_metrics(order) -> dict[str, Any]:
    from .models import Payment

    active = [p for p in order.payments.all() if p.status == Payment.STATUS_ACTIVE]
    total_in = sum(
        (p.amount or Decimal('0'))
        for p in active
        if p.payment_type in (Payment.TYPE_PREPAYMENT, Payment.TYPE_PAYMENT, Payment.TYPE_SURCHARGE)
    )
    ref = sum(
        (p.amount or Decimal('0')) for p in active if p.payment_type == Payment.TYPE_REFUND
    )
    net = total_in - ref
    due = order.total_amount
    st = payment_status(
        total_due=due, net_paid=net, total_incoming=total_in, total_refund=ref,
    )
    if st == PAY_REFUNDED and due > 0:
        debt = max(Decimal('0'), -net)
    else:
        debt = max(Decimal('0'), due - net)
    return {
        'payment_status': st,
        'paid_amount': net,
        'debt_amount': debt,
        'refund_amount': ref,
        'net_paid': net,
        'total_due': due,
    }
