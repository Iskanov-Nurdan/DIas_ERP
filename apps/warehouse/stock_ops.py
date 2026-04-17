"""
Операции остатков склада ГП: форма учёта, упаковка, списание при продаже.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers as drf_serializers

from .models import WarehouseBatch
from .packaging import q4


def _normalize_stock_quality(quality: str) -> str:
    q = (quality or '').strip()
    if q not in (WarehouseBatch.QUALITY_GOOD, WarehouseBatch.QUALITY_DEFECT):
        raise ValueError(
            f'quality must be {WarehouseBatch.QUALITY_GOOD!r} or {WarehouseBatch.QUALITY_DEFECT!r}, got {quality!r}'
        )
    return q


def _close_warehouse_row_after_full_sale(b: WarehouseBatch) -> None:
    """Остаток исчерпан — строка не удаляется, статус «отгружено/продано»."""
    b.quantity = Decimal('0')
    b.status = WarehouseBatch.STATUS_SHIPPED
    fields = ['quantity', 'status', 'inventory_form']
    if b.packages_count is not None:
        b.packages_count = Decimal('0')
        fields.append('packages_count')
    b.save(update_fields=fields)


def normalize_inventory_form(value):
    """Канонические значения + переходный маппинг со старого API."""
    if value is None or value == '':
        return None
    s = str(value).strip().lower()
    legacy = {
        'not_packed': WarehouseBatch.INVENTORY_UNPACKED,
        'opened': WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        'open': WarehouseBatch.INVENTORY_OPEN_PACKAGE,
    }
    s = legacy.get(s, s)
    valid = {c[0] for c in WarehouseBatch.INVENTORY_FORM_CHOICES}
    if s not in valid:
        raise drf_serializers.ValidationError(
            {
                'stock_form': (
                    f'Допустимо: {", ".join(sorted(valid))}, not_packed/unpacked, '
                    f'opened/open → open_package'
                ),
            }
        )
    return s


PIECE_LOOSE = 'loose_remainder'
PIECE_FROM_SEALED = 'from_sealed_package'
PIECE_FROM_OPEN = 'from_open_package'


def normalize_piece_pick(value):
    if value is None or value == '':
        return None
    s = str(value).strip()
    valid = {PIECE_LOOSE, PIECE_FROM_SEALED, PIECE_FROM_OPEN}
    if s not in valid:
        raise drf_serializers.ValidationError(
            {'piece_pick': f'Допустимо: {", ".join(sorted(valid))}'}
        )
    return s


def loose_quantity_for_packaging(pb, *, quality: str) -> Decimal:
    """
    Объём (шт) указанного качества для упаковки с производственной партии pb:
    сначала unpacked этого quality; иначе cap из ОТК минус всё уже на складе этого quality.
    """
    from apps.production.models import ProductionBatch

    q = _normalize_stock_quality(quality)
    if pb.otk_status != ProductionBatch.OTK_ACCEPTED:
        return Decimal('0')
    check = pb.otk_checks.order_by('-checked_date', '-id').first()
    if not check:
        return Decimal('0')
    cap = (
        Decimal(str(check.accepted or 0))
        if q == WarehouseBatch.QUALITY_GOOD
        else Decimal(str(check.rejected or 0))
    )
    unpacked = WarehouseBatch.objects.filter(
        source_batch=pb,
        inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        quality=q,
    ).aggregate(s=Sum('quantity'))['s']
    if unpacked is not None and Decimal(str(unpacked)) > 0:
        return Decimal(str(unpacked))
    on_wh = WarehouseBatch.objects.filter(source_batch=pb, quality=q).aggregate(s=Sum('quantity'))['s'] or 0
    left = cap - Decimal(str(on_wh))
    return left if left > 0 else Decimal('0')


def _duplicate_warehouse_batch(
    template: WarehouseBatch,
    *,
    quantity: Decimal,
    packages_count: Decimal | None,
    inventory_form: str,
) -> WarehouseBatch:
    """Копия строки склада с тем же продуктом/партией/геометрией/качеством/снимком ОТК."""
    pc = q4(packages_count) if packages_count is not None else None
    return WarehouseBatch.objects.create(
        profile_id=template.profile_id,
        product=template.product,
        length_per_piece=template.length_per_piece,
        cost_per_piece=template.cost_per_piece,
        cost_per_meter=template.cost_per_meter,
        quantity=q4(quantity),
        status=template.status,
        date=template.date,
        source_batch=template.source_batch,
        inventory_form=inventory_form,
        unit_meters=template.unit_meters,
        package_total_meters=template.package_total_meters,
        pieces_per_package=template.pieces_per_package,
        packages_count=pc,
        quality=template.quality,
        defect_reason=template.defect_reason or '',
        otk_accepted=template.otk_accepted,
        otk_defect=template.otk_defect,
        otk_defect_reason=template.otk_defect_reason or '',
        otk_comment=template.otk_comment or '',
        otk_inspector_name=template.otk_inspector_name or '',
        otk_checked_at=template.otk_checked_at,
        otk_status=(template.otk_status or '')[:20],
    )


def _maybe_split_legacy_combined_open_row(b: WarehouseBatch) -> None:
    """
    Старый формат: одна строка open_package с packages_count > 0 (запечатанные + хвост).
    Делим на две: packed (только целые короба) и open_package (только хвост).
    """
    if b.inventory_form != WarehouseBatch.INVENTORY_OPEN_PACKAGE:
        return
    pc = b.packages_count
    if pc is None or Decimal(str(pc)) < 1:
        return
    ppp = b.pieces_per_package
    if ppp is None or Decimal(str(ppp)) <= 0:
        return
    qty_d = q4(Decimal(str(b.quantity)))
    pc_d = q4(Decimal(str(pc)))
    ppp_d = q4(Decimal(str(ppp)))
    sealed_qty = q4(pc_d * ppp_d)
    open_tail = q4(qty_d - sealed_qty)
    if open_tail <= 0:
        b.inventory_form = WarehouseBatch.INVENTORY_PACKED
        b.quantity = sealed_qty
        b.save(update_fields=['inventory_form', 'quantity'])
        return
    _duplicate_warehouse_batch(
        b,
        quantity=sealed_qty,
        packages_count=pc_d,
        inventory_form=WarehouseBatch.INVENTORY_PACKED,
    )
    b.packages_count = q4(Decimal('0'))
    b.quantity = open_tail
    b.save(update_fields=['packages_count', 'quantity'])


def deduct_unpacked_quantity(pb, qty: Decimal, *, quality: str) -> None:
    """Списывает qty со строк unpacked по партии pb (FIFO по id) для указанного quality."""
    q = _normalize_stock_quality(quality)
    remaining = qty
    if remaining <= 0:
        return
    qs = (
        WarehouseBatch.objects.select_for_update()
        .filter(
            source_batch=pb,
            inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
            quality=q,
        )
        .order_by('id')
    )
    for row in qs:
        if remaining <= 0:
            break
        take = min(row.quantity, remaining)
        row.quantity -= take
        remaining -= take
        if row.quantity <= 0:
            row.delete()
        else:
            row.save(update_fields=['quantity'])


def apply_sale_to_warehouse_batch(batch_id: int, quantity: Decimal, stock_form: str, piece_pick) -> None:
    """
    Списание со строки склада ГП при продаже (select_for_update внутри).
    piece_pick обязателен для packed/open_package при продаже штук (см. validate в сериализаторе).
    """
    with transaction.atomic():
        b = WarehouseBatch.objects.select_for_update().get(pk=batch_id)
        if b.status != WarehouseBatch.STATUS_AVAILABLE:
            raise drf_serializers.ValidationError(
                {'warehouse_batch': 'Партия склада недоступна для продажи (не в статусе «доступна»)'}
            )
        _maybe_split_legacy_combined_open_row(b)
        b.refresh_from_db()
        if quantity <= 0:
            raise drf_serializers.ValidationError({'quantity': 'Должно быть > 0'})
        if quantity > b.quantity:
            raise drf_serializers.ValidationError(
                {'quantity': f'Недостаточно остатка на партии (доступно {b.quantity})'}
            )

        sf = normalize_inventory_form(stock_form)
        if sf is None:
            sf = b.inventory_form
        if sf != b.inventory_form:
            raise drf_serializers.ValidationError(
                {
                    'stock_form': (
                        f'Не совпадает с формой строки склада ({b.inventory_form}). '
                        f'Ожидалось {b.inventory_form}'
                    ),
                }
            )

        pp = normalize_piece_pick(piece_pick)

        if b.inventory_form == WarehouseBatch.INVENTORY_UNPACKED:
            if pp and pp != PIECE_LOOSE:
                raise drf_serializers.ValidationError(
                    {'piece_pick': 'Для неупакованного остатка используйте loose_remainder или не передавайте поле'}
                )
            b.quantity -= quantity
            if b.quantity <= 0:
                _close_warehouse_row_after_full_sale(b)
            else:
                b.save(update_fields=['quantity'])
            return

        if b.inventory_form == WarehouseBatch.INVENTORY_OPEN_PACKAGE:
            if pp and pp != PIECE_FROM_OPEN:
                raise drf_serializers.ValidationError(
                    {'piece_pick': 'Для открытой упаковки укажите from_open_package'}
                )
            b.quantity -= quantity
            if b.quantity <= 0:
                _close_warehouse_row_after_full_sale(b)
            else:
                b.save(update_fields=['quantity'])
            return

        # PACKED
        if pp is None:
            raise drf_serializers.ValidationError(
                {'piece_pick': 'Для упакованного остатка укажите loose_remainder / from_sealed_package / from_open_package'}
            )
        if pp == PIECE_LOOSE:
            raise drf_serializers.ValidationError(
                {'piece_pick': 'Со строки packed нельзя списать loose_remainder — выберите другую партию или форму'}
            )
        if pp == PIECE_FROM_OPEN:
            raise drf_serializers.ValidationError(
                {'piece_pick': 'Строка в форме packed: используйте from_sealed_package'}
            )

        if pp == PIECE_FROM_SEALED:
            ppc = q4(Decimal(str(b.pieces_per_package)))
            if not ppc or ppc <= 0:
                raise drf_serializers.ValidationError(
                    {'warehouse_batch': 'Для вскрытия упаковки у строки должен быть задан pieces_per_package'}
                )
            if b.packages_count is None or b.packages_count < 1:
                raise drf_serializers.ValidationError(
                    {'warehouse_batch': 'Нет целых упаковок (packages_count)'}
                )
            sold = q4(Decimal(str(quantity)))
            old_pc = int(q4(Decimal(str(b.packages_count))).to_integral_value())
            expected_qty = q4(Decimal(old_pc) * ppc)
            if q4(Decimal(str(b.quantity)) - expected_qty).copy_abs() > q4(Decimal('0.0001')):
                raise drf_serializers.ValidationError(
                    {
                        'warehouse_batch': (
                            'Строка «упаковано»: quantity не совпадает с packages_count × pieces_per_package. '
                            'Обратитесь к администратору для исправления остатка.'
                        ),
                    }
                )
            if sold > expected_qty:
                raise drf_serializers.ValidationError(
                    {'quantity': f'Недостаточно остатка на партии (доступно {expected_qty})'}
                )

            k_full = int(sold // ppc)
            remainder = q4(sold - q4(Decimal(k_full) * ppc))
            zero_eps = q4(Decimal('0.0001'))

            if remainder.copy_abs() <= zero_eps:
                sealed_after = old_pc - k_full
                b.packages_count = q4(Decimal(sealed_after))
                b.quantity = q4(Decimal(sealed_after) * ppc)
                if b.quantity <= 0:
                    _close_warehouse_row_after_full_sale(b)
                else:
                    b.save(update_fields=['quantity', 'packages_count'])
                return

            packages_to_remove = k_full + 1
            sealed_after = old_pc - packages_to_remove
            tail = q4(ppc - remainder)

            if sealed_after > 0:
                b.packages_count = q4(Decimal(sealed_after))
                b.quantity = q4(Decimal(sealed_after) * ppc)
                b.inventory_form = WarehouseBatch.INVENTORY_PACKED
                b.save(update_fields=['quantity', 'packages_count', 'inventory_form'])
                _duplicate_warehouse_batch(
                    b,
                    quantity=tail,
                    packages_count=q4(Decimal('0')),
                    inventory_form=WarehouseBatch.INVENTORY_OPEN_PACKAGE,
                )
            else:
                b.packages_count = q4(Decimal('0'))
                b.quantity = tail
                b.inventory_form = WarehouseBatch.INVENTORY_OPEN_PACKAGE
                b.save(update_fields=['quantity', 'packages_count', 'inventory_form'])
            return

        raise drf_serializers.ValidationError({'piece_pick': 'Неизвестное значение'})
