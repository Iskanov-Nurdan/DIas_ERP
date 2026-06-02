"""Сериализация GP-упаковок для продажи (select-sources, preview)."""
from __future__ import annotations

from apps.warehouse.models import GpPackUnit, WarehouseBatch


def available_gp_pack_units_queryset():
    return (
        GpPackUnit.objects.filter(
            warehouse_batch_id__isnull=False,
            warehouse_batch__status=WarehouseBatch.STATUS_AVAILABLE,
            warehouse_batch__quality=WarehouseBatch.QUALITY_GOOD,
            warehouse_batch__stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
        )
        .select_related(
            'operation',
            'operation__product',
            'operation__blank',
            'warehouse_batch',
            'warehouse_batch__profile',
        )
        .order_by('-operation__created_at', '-pk')
    )


def warehouse_batch_ids_with_gp_units(qs=None) -> set[int]:
    base = qs if qs is not None else available_gp_pack_units_queryset()
    return set(base.values_list('warehouse_batch_id', flat=True))


def gp_package_is_sold(unit: GpPackUnit) -> bool:
    wb = unit.warehouse_batch
    if wb is None:
        return True
    if wb.status != WarehouseBatch.STATUS_AVAILABLE:
        return True
    from apps.sales.models import Sale

    return unit.sale_lines.exclude(sale__sale_status=Sale.STATUS_CANCELED).exists()


def serialize_gp_package_for_sale(unit: GpPackUnit) -> dict:
    op = unit.operation
    wb = unit.warehouse_batch
    product_name = op.product.name if op.product_id else (wb.product if wb else '')
    blank_name = op.blank.name if op.blank_id else ''
    kind = op.kind or ''
    label = op.label or ''
    pieces = int(unit.pieces)
    display = (
        f'{product_name} — {kind}'
        + (f', {label}' if label else '')
        + f' — 1 уп. / {pieces} шт.'
    )
    return {
        'id': unit.pk,
        'gp_package_id': unit.pk,
        'warehouse_batch_id': unit.warehouse_batch_id,
        'product_id': op.product_id,
        'product_name': product_name,
        'blank_id': op.blank_id,
        'blank_name': blank_name,
        'kind': kind,
        'label': label,
        'total_pieces': pieces,
        'pieces_per_package': pieces,
        'status': 'available',
        'is_sold': False,
        'display': display,
        'warehouse_batch_display': display,
    }
