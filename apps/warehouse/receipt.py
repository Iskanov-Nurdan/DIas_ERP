"""Поступление на склад ГП после ОТК (отдельные строки good / defect)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List

from apps.production.models import ProductionBatch

from .models import WarehouseBatch


def create_warehouse_batches_from_otk(
    batch: ProductionBatch,
    *,
    accepted: Decimal,
    rejected: Decimal,
    defect_reason: str,
    comment: str,
    inspector_name: str,
    checked_at,
    otk_status_snapshot: str,
) -> List[WarehouseBatch]:
    """
    Создаёт 0–2 строки склада. Good и defect не смешиваются.
    Себестоимость и геометрия — с ProductionBatch (без ввода в ОТК).
    """
    acc = Decimal(str(accepted or 0))
    rej = Decimal(str(rejected or 0))
    reason = (defect_reason or '').strip()
    ins = (inspector_name or '')[:255]
    otk_st = (otk_status_snapshot or '')[:20]

    common = {
        'profile_id': batch.profile_id,
        'product': batch.product,
        'length_per_piece': batch.length_per_piece,
        'cost_per_piece': batch.cost_per_piece,
        'cost_per_meter': batch.cost_per_meter,
        'status': WarehouseBatch.STATUS_AVAILABLE,
        'date': date.today(),
        'source_batch': batch,
        'inventory_form': WarehouseBatch.INVENTORY_UNPACKED,
        'unit_meters': batch.length_per_piece,
        'otk_comment': comment or '',
        'otk_inspector_name': ins,
        'otk_checked_at': checked_at,
        'otk_status': otk_st,
        'otk_accepted': acc,
        'otk_defect': rej,
        'otk_defect_reason': reason,
    }

    created: List[WarehouseBatch] = []
    if acc > 0:
        created.append(
            WarehouseBatch.objects.create(
                **common,
                quantity=acc,
                quality=WarehouseBatch.QUALITY_GOOD,
                defect_reason='',
            )
        )
    if rej > 0:
        created.append(
            WarehouseBatch.objects.create(
                **common,
                quantity=rej,
                quality=WarehouseBatch.QUALITY_DEFECT,
                defect_reason=reason,
            )
        )
    return created
