"""Остатки ГП для продаж (только штуки, с ценой профиля)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from apps.recipes.models import PlasticProfile
from apps.warehouse.models import WarehouseBatch
from apps.workshop.models import WorkshopBlank

from .profile_sale_price import profile_pricing_payload
from .reservations import get_available_quantity


def build_profile_stock_rows(*, client_id=None) -> list[dict]:
    """Агрегат по profile_id (как warehouse/gp-stock/)."""
    qs = WarehouseBatch.objects.filter(
        status=WarehouseBatch.STATUS_AVAILABLE,
        quality=WarehouseBatch.QUALITY_GOOD,
        stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
        profile_id__isnull=False,
    ).select_related('profile', 'workshop_blank', 'blank_production_run__blank')

    agg: dict[int, dict] = defaultdict(
        lambda: {'pieces': Decimal('0'), 'product_name': '', 'blank_ids': set(), 'first_batch_id': None}
    )
    for wb in qs:
        avail = Decimal(str(get_available_quantity(wb.pk)))
        if avail <= 0:
            continue
        if wb.inventory_form == WarehouseBatch.INVENTORY_PACKED:
            continue
        pid = wb.profile_id
        row = agg[pid]
        row['pieces'] += avail
        row['product_name'] = wb.profile.name if wb.profile_id else wb.product
        if row['first_batch_id'] is None:
            row['first_batch_id'] = wb.pk
        bid = wb.workshop_blank_id
        if bid is None and wb.blank_production_run_id:
            bid = wb.blank_production_run.blank_id
        if bid:
            row['blank_ids'].add(bid)

    profiles = {p.pk: p for p in PlasticProfile.objects.filter(pk__in=agg.keys())}
    blanks = {b.pk: b for b in WorkshopBlank.objects.filter(pk__in={x for r in agg.values() for x in r['blank_ids']})}

    items = []
    for pid, row in sorted(agg.items(), key=lambda x: x[1]['product_name']):
        prof = profiles.get(pid)
        pricing = profile_pricing_payload(prof)
        blank_id = next(iter(row['blank_ids']), None) if row['blank_ids'] else None
        blank_name = blanks[blank_id].name if blank_id and blank_id in blanks else ''
        pieces = int(row['pieces'].to_integral_value())
        batch_id = row['first_batch_id']
        items.append(
            {
                'id': batch_id or pid,
                'warehouse_batch_id': batch_id,
                'product_id': pid,
                'profile_id': pid,
                'product_name': row['product_name'],
                'blank_id': blank_id,
                'blank_name': blank_name,
                'available_pieces': pieces,
                'pieces': pieces,
                **pricing,
            }
        )
    return items


def build_warehouse_batch_sale_sources(*, limit: int = 300) -> list[dict]:
    """Партии для выбора в кассе (только штуки, не packed)."""
    from config.api_numbers import api_decimal_str

    qs = (
        WarehouseBatch.objects.filter(
            status=WarehouseBatch.STATUS_AVAILABLE,
            quality=WarehouseBatch.QUALITY_GOOD,
            stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
        )
        .select_related('profile')
        .order_by('-date', '-id')[:limit]
    )
    rows = []
    for b in qs:
        if b.inventory_form == WarehouseBatch.INVENTORY_PACKED:
            continue
        raw_available = Decimal(str(get_available_quantity(b.pk)))
        if raw_available <= 0:
            continue
        pieces = int(raw_available.to_integral_value())
        pricing = profile_pricing_payload(b.profile if b.profile_id else None)
        pname = b.profile.name if b.profile_id else b.product
        rows.append(
            {
                'id': b.pk,
                'profile_id': b.profile_id,
                'product_name': pname,
                'available_pieces': pieces,
                'available_quantity': api_decimal_str(raw_available),
                'display': f'#{b.pk} — {pname} — {pieces} шт',
                'warehouse_batch_display': f'#{b.pk} — {pname} — {pieces} шт',
                'supports_pieces': True,
                'supports_packages': False,
                'inventory_form': b.inventory_form,
                'quality': b.quality,
                'status': b.status,
                **pricing,
            }
        )
    return rows
