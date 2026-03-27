"""
Упаковка ГП: расчёт штук в упаковке, отбор строк FIFO без смешивания параметров смены.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple

from .models import WarehouseBatch


Q4 = Decimal('0.0001')


def q4(d: Decimal) -> Decimal:
    return Decimal(str(d)).quantize(Q4)


def effective_unit_meters(row: WarehouseBatch) -> Optional[Decimal]:
    if row.unit_meters is not None:
        return Decimal(str(row.unit_meters))
    pb = row.source_batch
    if pb is not None and pb.shift_height is not None:
        return Decimal(str(pb.shift_height))
    return None


def effective_shift_width(row: WarehouseBatch) -> Optional[Decimal]:
    pb = row.source_batch
    if pb is None or pb.shift_width is None:
        return None
    return Decimal(str(pb.shift_width))


def effective_shift_angle(row: WarehouseBatch) -> Optional[Decimal]:
    pb = row.source_batch
    if pb is None or pb.shift_angle_deg is None:
        return None
    return Decimal(str(pb.shift_angle_deg))


def row_dim_tuple(row: WarehouseBatch) -> Optional[Tuple[Decimal, Optional[Decimal], Optional[Decimal]]]:
    um = effective_unit_meters(row)
    if um is None:
        return None
    w = effective_shift_width(row)
    a = effective_shift_angle(row)
    return (
        q4(um),
        q4(w) if w is not None else None,
        q4(a) if a is not None else None,
    )


def row_matches_request(
    row: WarehouseBatch,
    unit_meters: Decimal,
    width_req: Optional[Decimal],
    angle_req: Optional[Decimal],
) -> bool:
    um = effective_unit_meters(row)
    if um is None or q4(um) != q4(unit_meters):
        return False
    if width_req is not None:
        w = effective_shift_width(row)
        if w is None or q4(w) != q4(width_req):
            return False
    if angle_req is not None:
        a = effective_shift_angle(row)
        if a is None or q4(a) != q4(angle_req):
            return False
    return True


def compute_pieces_per_package(unit_meters: Decimal, package_total_meters: Decimal) -> int:
    if unit_meters <= 0 or package_total_meters <= 0:
        raise ValueError('positive')
    if package_total_meters < unit_meters:
        raise ValueError('pkg_lt_unit')
    ppp = int(package_total_meters // unit_meters)
    if ppp < 1:
        raise ValueError('ppp_lt_1')
    return ppp


@dataclass
class PackTake:
    row_id: int
    take: Decimal


def plan_fifo_pack(
    ordered_rows: List[WarehouseBatch],
    need: Decimal,
    unit_meters: Decimal,
    width_req: Optional[Decimal],
    angle_req: Optional[Decimal],
) -> Tuple[List[PackTake], Optional[str]]:
    """
    FIFO по ordered_rows; одна группа параметров (как у первой подходящей строки).
    Возвращает (списание, None) или ([], код_ошибки).
    """
    if need <= 0:
        return [], 'bad_need'
    group_key: Optional[Tuple] = None
    takes: List[PackTake] = []
    remaining = need

    for row in ordered_rows:
        if row.quantity <= 0:
            continue
        if not row_matches_request(row, unit_meters, width_req, angle_req):
            continue
        key = row_dim_tuple(row)
        if key is None:
            continue
        if group_key is None:
            group_key = key
        elif key != group_key:
            continue

        take = min(Decimal(str(row.quantity)), remaining)
        if take <= 0:
            continue
        takes.append(PackTake(row_id=row.pk, take=take))
        remaining -= take
        if remaining <= 0:
            break

    if not takes:
        return [], 'no_matching_lines'
    if remaining > 0:
        return [], 'insufficient'
    return takes, None


def _api_piece_number(d: Decimal):
    """Целые штуки в JSON как int, дробные как float."""
    d = q4(d)
    if d == d.to_integral_value():
        return int(d)
    return float(d)


def warehouse_packaging_breakdown(row: WarehouseBatch) -> dict:
    """
    Источник истины для строк packed / open_package:

    - В БД ``packages_count`` — только **целые запечатанные** упаковки (после частичного вскрытия
      одна упаковка «уходит» в хвост, счётчик уменьшается).
    - ``quantity`` — суммарные штуки на строке: запечатанные + остаток в открытой упаковке
      (при ``open_package``).

    Возвращает поля для API: sealed_packages_count, open_package_pieces, sealed_pieces,
    packaging_quantity_consistent.
    """
    empty = {
        'sealed_packages_count': None,
        'open_package_pieces': None,
        'sealed_pieces': None,
        'packaging_quantity_consistent': True,
    }
    inv = row.inventory_form
    if inv not in (WarehouseBatch.INVENTORY_PACKED, WarehouseBatch.INVENTORY_OPEN_PACKAGE):
        return empty

    qty = row.quantity
    if qty is None:
        return {**empty, 'packaging_quantity_consistent': False}

    qty_d = q4(Decimal(str(qty)))
    ppp = row.pieces_per_package
    pc = row.packages_count

    if ppp is None or Decimal(str(ppp)) <= 0:
        sealed_int = None
        if pc is not None:
            sealed_int = int(Decimal(str(pc)).to_integral_value())
        return {
            'sealed_packages_count': sealed_int,
            'open_package_pieces': None,
            'sealed_pieces': None,
            'packaging_quantity_consistent': True,
        }

    ppp_d = q4(Decimal(str(ppp)))
    sealed_dec = q4(Decimal(str(pc))) if pc is not None else Decimal('0')
    sealed_int = int(sealed_dec.to_integral_value())
    sealed_pieces = q4(sealed_dec * ppp_d)

    if inv == WarehouseBatch.INVENTORY_PACKED:
        open_p = Decimal('0')
        diff = q4(qty_d - sealed_pieces)
        consistent = diff.copy_abs() <= Q4
        return {
            'sealed_packages_count': sealed_int,
            'open_package_pieces': _api_piece_number(open_p),
            'sealed_pieces': _api_piece_number(sealed_pieces),
            'packaging_quantity_consistent': consistent,
        }

    open_p = q4(qty_d - sealed_pieces)
    if -Q4 <= open_p <= Q4:
        open_p = Decimal('0')
    consistent = Decimal('0') <= open_p <= q4(ppp_d + Q4)
    return {
        'sealed_packages_count': sealed_int,
        'open_package_pieces': _api_piece_number(open_p),
        'sealed_pieces': _api_piece_number(sealed_pieces),
        'packaging_quantity_consistent': consistent,
    }
