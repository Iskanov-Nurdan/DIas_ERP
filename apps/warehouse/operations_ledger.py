"""Лента движений склада ГП для GET /api/warehouse/operations/."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal
from typing import Any, Optional

from django.db.models import Prefetch, Q, Sum
from django.utils import timezone

from apps.sales.models import DefectRecord, Return, ReturnLine, ReworkRequest, Sale, SaleLine
from apps.workshop.models import BlankProductionRun

from .models import GpPackOperation, GpPackUnit, WarehouseBatch

KIND_LABELS = {
    'accept': 'Приёмка ГП',
    'otk_account': 'Учёт ОТК',
    'package': 'Упаковка',
    'sale': 'Продажа',
    'return': 'Возврат',
    'defect': 'Брак',
    'rework': 'Переделка',
}


@dataclass
class WarehouseOperationRow:
    id: str
    at: datetime
    kind: str
    direction: str
    product_id: Optional[int]
    product_name: str
    blank_id: Optional[int]
    blank_name: str
    pieces: int
    kg: float
    packages: int
    warehouse_batch_id: Optional[int]
    gp_package_id: Optional[int]
    sale_id: Optional[int]
    order_id: Optional[int]
    return_id: Optional[int]
    label: str
    actor: str
    comment: str
    package_kind: str

    def to_item(self) -> dict[str, Any]:
        at = self.at
        if timezone.is_naive(at):
            at = timezone.make_aware(at, timezone.get_current_timezone())
        at_utc = at.astimezone(dt_timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        return {
            'id': self.id,
            'at': at_utc,
            'kind': self.kind,
            'kind_label': KIND_LABELS.get(self.kind, self.kind),
            'direction': self.direction,
            'product_id': self.product_id,
            'product_name': self.product_name,
            'blank_id': self.blank_id,
            'blank_name': self.blank_name,
            'pieces': int(self.pieces),
            'kg': float(self.kg),
            'packages': int(self.packages),
            'warehouse_batch_id': self.warehouse_batch_id,
            'gp_package_id': self.gp_package_id,
            'sale_id': self.sale_id,
            'order_id': self.order_id,
            'return_id': self.return_id,
            'label': self.label,
            'actor': self.actor,
            'comment': self.comment,
            'package_kind': self.package_kind or '',
        }


def _parse_kinds(raw: Optional[str]) -> set[str] | None:
    if not raw or not str(raw).strip():
        return None
    allowed = {'accept', 'otk_account', 'package', 'sale', 'return', 'defect', 'rework'}
    parts = {p.strip().lower() for p in str(raw).split(',') if p.strip()}
    picked = parts & allowed
    return picked if picked else None


def _date_range(
    date_from: Optional[date],
    date_to: Optional[date],
) -> tuple[Optional[datetime], Optional[datetime]]:
    start = None
    end = None
    if date_from:
        start = timezone.make_aware(datetime.combine(date_from, time.min))
    if date_to:
        end = timezone.make_aware(datetime.combine(date_to, time.max))
    return start, end


def _in_range(at: datetime, start: Optional[datetime], end: Optional[datetime]) -> bool:
    if start and at < start:
        return False
    if end and at > end:
        return False
    return True


def _actor_name(user) -> str:
    if user is None:
        return ''
    return (getattr(user, 'name', None) or getattr(user, 'email', None) or '').strip()


def _kg_for_pieces(pieces: int, run: BlankProductionRun | None) -> float:
    if not run or not pieces:
        return 0.0
    wpp = Decimal(str(run.weight_kg_per_piece or 0))
    return float((Decimal(pieces) * wpp).quantize(Decimal('0.0001')))


def _collect_otk_account(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    from apps.workshop.models import OtkAccountLine, OtkAccountSession

    qs = (
        OtkAccountSession.objects.select_related('blank', 'operator')
        .prefetch_related(
            Prefetch('lines', queryset=OtkAccountLine.objects.select_related('profile'))
        )
        .order_by('-created_at')
    )
    if blank_id:
        qs = qs.filter(blank_id=blank_id)
    rows: list[WarehouseOperationRow] = []
    for session in qs:
        at = session.created_at
        if not _in_range(at, start, end):
            continue
        for line in session.lines.all():
            if product_id and line.profile_id != product_id:
                continue
            rows.append(
                WarehouseOperationRow(
                    id=f'otk_account-{session.pk}-{line.pk}',
                    at=at,
                    kind='otk_account',
                    direction='in',
                    product_id=line.profile_id,
                    product_name=line.profile_name_snapshot or (line.profile.name if line.profile_id else ''),
                    blank_id=session.blank_id,
                    blank_name=session.blank.name if session.blank_id else '',
                    pieces=int(line.pieces),
                    kg=float(Decimal(str(line.kg))),
                    packages=0,
                    warehouse_batch_id=None,
                    gp_package_id=None,
                    sale_id=None,
                    order_id=None,
                    return_id=None,
                    label='',
                    actor=_actor_name(session.operator),
                    comment=(session.comment or '')[:500],
                    package_kind='',
                )
            )
    return rows


def _collect_accept(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    qs = BlankProductionRun.objects.filter(
        status=BlankProductionRun.STATUS_GP_ACCEPTED,
        gp_accepted_at__isnull=False,
    ).select_related('product', 'blank')
    if product_id:
        qs = qs.filter(product_id=product_id)
    if blank_id:
        qs = qs.filter(blank_id=blank_id)
    rows: list[WarehouseOperationRow] = []
    for run in qs:
        at = run.gp_accepted_at
        if at is None or not _in_range(at, start, end):
            continue
        pcs = int(run.gp_accepted_pieces or 0)
        rows.append(
            WarehouseOperationRow(
                id=f'accept-{run.pk}',
                at=at,
                kind='accept',
                direction='in',
                product_id=run.product_id,
                product_name=(run.product_name_snapshot or (run.product.name if run.product_id else '')),
                blank_id=run.blank_id,
                blank_name=(run.blank_name_snapshot or (run.blank.name if run.blank_id else '')),
                pieces=pcs,
                kg=float(Decimal(str(run.gp_accepted_kg or 0))),
                packages=0,
                warehouse_batch_id=None,
                gp_package_id=None,
                sale_id=None,
                order_id=None,
                return_id=None,
                label='',
                actor='',
                comment='',
                package_kind='',
            )
        )
    return rows


def _collect_package(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    qs = GpPackOperation.objects.select_related('product', 'blank', 'created_by').prefetch_related(
        'units', 'run_allocations',
    )
    if product_id:
        qs = qs.filter(product_id=product_id)
    if blank_id:
        qs = qs.filter(blank_id=blank_id)
    rows: list[WarehouseOperationRow] = []
    for op in qs:
        at = op.created_at
        if not _in_range(at, start, end):
            continue
        pkg_count = op.units.count()
        rows.append(
            WarehouseOperationRow(
                id=f'package-{op.pk}',
                at=at,
                kind='package',
                direction='in',
                product_id=op.product_id,
                product_name=(op.product.name if op.product_id else ''),
                blank_id=op.blank_id,
                blank_name=(op.blank.name if op.blank_id else ''),
                pieces=int(op.total_pieces or 0),
                kg=float(
                    op.run_allocations.aggregate(s=Sum('kg'))['s'] or 0
                ),
                packages=pkg_count,
                warehouse_batch_id=None,
                gp_package_id=None,
                sale_id=None,
                order_id=None,
                return_id=None,
                label=(op.label or ''),
                actor=_actor_name(op.created_by),
                comment='',
                package_kind=(op.kind or ''),
            )
        )
    return rows


def _collect_sale(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    line_qs = SaleLine.objects.filter(
        sale__warehouse_stock_applied=True,
    ).exclude(sale__sale_status=Sale.STATUS_CANCELED).select_related(
        'sale',
        'sale__created_by',
        'sale__linked_order',
        'warehouse_batch',
        'warehouse_batch__profile',
        'gp_pack_unit',
        'gp_pack_unit__operation',
    )
    if product_id:
        line_qs = line_qs.filter(
            Q(warehouse_batch__profile_id=product_id)
            | Q(gp_pack_unit__operation__product_id=product_id)
        )
    if blank_id:
        line_qs = line_qs.filter(
            Q(gp_pack_unit__operation__blank_id=blank_id)
            | Q(warehouse_batch__blank_production_run__blank_id=blank_id)
        )
    rows: list[WarehouseOperationRow] = []
    for ln in line_qs:
        sale = ln.sale
        at = sale.updated_at or sale.created_at
        if sale.date:
            at = timezone.make_aware(datetime.combine(sale.date, time(12, 0)))
        if not _in_range(at, start, end):
            continue
        wb = ln.warehouse_batch
        gp = ln.gp_pack_unit
        pieces = int(Decimal(str(ln.quantity or 0)).to_integral_value())
        pkg = 0
        pkg_kind = ''
        if gp and gp.operation_id:
            pkg = 1
            pkg_kind = gp.operation.kind or ''
        elif wb and wb.inventory_form == WarehouseBatch.INVENTORY_PACKED and wb.pieces_per_package:
            ppp = Decimal(str(wb.pieces_per_package))
            if ppp > 0:
                pkg = int((Decimal(pieces) / ppp).to_integral_value())
        kg = 0.0
        if wb and wb.blank_production_run_id:
            run = BlankProductionRun.objects.filter(pk=wb.blank_production_run_id).first()
            kg = _kg_for_pieces(pieces, run)
        elif gp and gp.operation_id:
            alloc = gp.operation.run_allocations.select_related('blank_production_run').first()
            if alloc and alloc.blank_production_run_id:
                kg = _kg_for_pieces(pieces, alloc.blank_production_run)
        profile = wb.profile if wb and wb.profile_id else None
        rows.append(
            WarehouseOperationRow(
                id=f'sale-{ln.pk}',
                at=at,
                kind='sale',
                direction='out',
                product_id=profile.pk if profile else None,
                product_name=(
                    (profile.name if profile else '')
                    or (wb.product if wb else ln.product)
                ),
                blank_id=gp.operation.blank_id if gp and gp.operation_id else None,
                blank_name=(
                    gp.operation.blank.name if gp and gp.operation_id and gp.operation.blank_id else ''
                ),
                pieces=pieces,
                kg=kg,
                packages=pkg,
                warehouse_batch_id=ln.warehouse_batch_id,
                gp_package_id=ln.gp_pack_unit_id,
                sale_id=sale.pk,
                order_id=sale.linked_order_id,
                return_id=None,
                label=(gp.operation.label if gp and gp.operation_id else ''),
                actor=_actor_name(sale.created_by),
                comment=(ln.comment or sale.comment or '')[:500],
                package_kind=pkg_kind,
            )
        )
    return rows


def _collect_return(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    qs = ReturnLine.objects.filter(
        return_doc__status=Return.STATUS_COMPLETED,
        return_target=ReturnLine.TARGET_WAREHOUSE,
    ).select_related(
        'return_doc',
        'return_doc__created_by',
        'sale_line',
        'sale_line__warehouse_batch',
        'sale_line__warehouse_batch__profile',
    )
    rows: list[WarehouseOperationRow] = []
    for ln in qs:
        ret = ln.return_doc
        at = ret.created_at
        if ret.date:
            at = timezone.make_aware(datetime.combine(ret.date, time(12, 0)))
        if not _in_range(at, start, end):
            continue
        wb = ln.sale_line.warehouse_batch if ln.sale_line_id else None
        profile = wb.profile if wb and wb.profile_id else None
        if product_id and (not profile or profile.pk != product_id):
            continue
        if blank_id:
            continue
        pieces = int(Decimal(str(ln.quantity or 0)).to_integral_value())
        rows.append(
            WarehouseOperationRow(
                id=f'return-{ln.pk}',
                at=at,
                kind='return',
                direction='in',
                product_id=profile.pk if profile else None,
                product_name=(profile.name if profile else (ln.product or '')),
                blank_id=None,
                blank_name='',
                pieces=pieces,
                kg=0.0,
                packages=0,
                warehouse_batch_id=wb.pk if wb else None,
                gp_package_id=None,
                sale_id=ret.sale_id,
                order_id=ret.linked_order_id,
                return_id=ret.pk,
                label='',
                actor=_actor_name(ret.created_by),
                comment=(ln.comment or ret.comment or '')[:500],
                package_kind='',
            )
        )
    return rows


def _collect_defect(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    qs = DefectRecord.objects.select_related('profile', 'created_by', 'warehouse_batch')
    if product_id:
        qs = qs.filter(profile_id=product_id)
    rows: list[WarehouseOperationRow] = []
    for rec in qs:
        at = rec.created_at
        if not _in_range(at, start, end):
            continue
        direction = 'out' if rec.status in (
            DefectRecord.STATUS_WRITTEN_OFF,
            DefectRecord.STATUS_SOLD,
            DefectRecord.STATUS_SENT_TO_REWORK,
        ) else 'in'
        pieces = int(Decimal(str(rec.original_quantity_pcs or rec.quantity_pcs or 0)).to_integral_value())
        rows.append(
            WarehouseOperationRow(
                id=f'defect-{rec.pk}',
                at=at,
                kind='defect',
                direction=direction,
                product_id=rec.profile_id,
                product_name=(rec.profile.name if rec.profile_id else rec.product),
                blank_id=None,
                blank_name='',
                pieces=pieces,
                kg=float(Decimal(str(rec.quantity_kg or 0))),
                packages=0,
                warehouse_batch_id=rec.warehouse_batch_id,
                gp_package_id=None,
                sale_id=None,
                order_id=None,
                return_id=None,
                label='',
                actor=_actor_name(rec.created_by),
                comment=(rec.comment or rec.defect_reason or '')[:500],
                package_kind='',
            )
        )
    return rows


def _collect_rework(
    *,
    product_id: Optional[int],
    blank_id: Optional[int],
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[WarehouseOperationRow]:
    qs = ReworkRequest.objects.filter(status=ReworkRequest.STATUS_COMPLETED).select_related(
        'created_by', 'return_doc', 'defect_record', 'defect_record__profile',
    )
    rows: list[WarehouseOperationRow] = []
    for rw in qs:
        at = rw.updated_at or rw.created_at
        if not _in_range(at, start, end):
            continue
        prof = rw.defect_record.profile if rw.defect_record_id and rw.defect_record.profile_id else None
        if product_id and (not prof or prof.pk != product_id):
            continue
        qty = int(
            Decimal(
                str(
                    rw.defect_record.sent_to_rework_quantity_pcs
                    if rw.defect_record_id
                    else 0
                )
            ).to_integral_value()
        )
        rows.append(
            WarehouseOperationRow(
                id=f'rework-{rw.pk}',
                at=at,
                kind='rework',
                direction='out',
                product_id=prof.pk if prof else None,
                product_name=(prof.name if prof else ''),
                blank_id=None,
                blank_name='',
                pieces=qty,
                kg=0.0,
                packages=0,
                warehouse_batch_id=None,
                gp_package_id=None,
                sale_id=rw.original_sale_id,
                order_id=None,
                return_id=rw.return_doc_id,
                label=(rw.rework_number or ''),
                actor=_actor_name(rw.created_by),
                comment=(rw.comment or '')[:500],
                package_kind='',
            )
        )
    return rows


def build_warehouse_operations(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    product_id: Optional[int] = None,
    blank_id: Optional[int] = None,
    kinds: Optional[str] = None,
) -> list[WarehouseOperationRow]:
    start, end = _date_range(date_from, date_to)
    kind_set = _parse_kinds(kinds)
    collectors = {
        'accept': _collect_accept,
        'otk_account': _collect_otk_account,
        'package': _collect_package,
        'sale': _collect_sale,
        'return': _collect_return,
        'defect': _collect_defect,
        'rework': _collect_rework,
    }
    merged: list[WarehouseOperationRow] = []
    for kind, fn in collectors.items():
        if kind_set is not None and kind not in kind_set:
            continue
        merged.extend(
            fn(product_id=product_id, blank_id=blank_id, start=start, end=end)
        )
    merged.sort(key=lambda r: r.at, reverse=True)
    return merged
