"""Остаток неупакованного ГП по приёмкам (BlankProductionRun) и создание упаковок с FIFO по run."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.recipes.models import PlasticProfile
from apps.workshop.models import BlankProductionRun, WorkshopBlank

from .models import GpPackOperation, GpPackRunAllocation, GpPackUnit, WarehouseBatch

DEC_KG = Decimal('0.000001')


def _q_kg(x: Decimal) -> Decimal:
    return x.quantize(DEC_KG)


def _runs_base_qs():
    return BlankProductionRun.objects.filter(
        status=BlankProductionRun.STATUS_GP_ACCEPTED,
        gp_accepted_at__isnull=False,
        gp_accepted_pieces__isnull=False,
        gp_accepted_pieces__gt=0,
    ).select_related('blank', 'product')


def _annotated_runs_for_group(product_id: int, blank_id: int):
    return (
        _runs_base_qs()
        .filter(product_id=product_id, blank_id=blank_id)
        .annotate(
            packed_pieces=Coalesce(
                Sum('gp_pack_allocations__pieces'),
                Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by('gp_accepted_at', 'id')
    )


def compute_unpacked_for_run(run: BlankProductionRun) -> tuple[int, Decimal]:
    accepted = int(run.gp_accepted_pieces or 0)
    packed = int(getattr(run, 'packed_pieces', 0) or 0)
    up = max(0, accepted - packed)
    wpp = Decimal(str(run.weight_kg_per_piece))
    kg = _q_kg(Decimal(up) * wpp) if up else Decimal('0')
    return up, kg


@dataclass
class BalanceLine:
    run_id: int
    product_name: str
    blank_name: str
    gp_accepted_at: object | None
    gp_accepted_pieces: int
    gp_accepted_kg: Decimal
    unpacked_pieces: int
    unpacked_kg: Decimal


@dataclass
class BalanceGroup:
    product_id: int
    blank_id: int
    product_name: str
    blank_name: str
    total_accepted_pieces: int
    total_accepted_kg: Decimal
    total_unpacked_pieces: int
    total_unpacked_kg: Decimal
    lines: list[BalanceLine]


def build_gp_unpacked_balance() -> list[BalanceGroup]:
    """Все группы (product, blank) с положительным остатком к упаковке."""
    pairs = _runs_base_qs().values_list('product_id', 'blank_id').distinct().order_by('product_id', 'blank_id')
    groups: list[BalanceGroup] = []
    for pid, bid in pairs:
        g = balance_group_detail(product_id=pid, blank_id=bid)
        if g.total_unpacked_pieces > 0:
            groups.append(g)
    return groups


def balance_group_detail(*, product_id: int, blank_id: int) -> BalanceGroup:
    qs = _annotated_runs_for_group(product_id, blank_id)
    lines: list[BalanceLine] = []
    tap, tak, tup, tuk = 0, Decimal('0'), 0, Decimal('0')
    first_product_name = ''
    first_blank_name = ''
    for run in qs:
        if not first_product_name:
            first_product_name = run.product_name_snapshot or (run.product.name if run.product_id else '')
        if not first_blank_name:
            first_blank_name = run.blank_name_snapshot or (run.blank.name if run.blank_id else '')
        acc = int(run.gp_accepted_pieces or 0)
        acc_kg = Decimal(str(run.gp_accepted_kg or '0'))
        up, uk = compute_unpacked_for_run(run)
        tap += acc
        tak += acc_kg
        tup += up
        tuk += uk
        ln_product = run.product_name_snapshot or (run.product.name if run.product_id else '')
        ln_blank = run.blank_name_snapshot or (run.blank.name if run.blank_id else '')
        lines.append(
            BalanceLine(
                run_id=run.pk,
                product_name=ln_product,
                blank_name=ln_blank,
                gp_accepted_at=run.gp_accepted_at,
                gp_accepted_pieces=acc,
                gp_accepted_kg=_q_kg(acc_kg),
                unpacked_pieces=up,
                unpacked_kg=uk,
            )
        )
    return BalanceGroup(
        product_id=product_id,
        blank_id=blank_id,
        product_name=first_product_name,
        blank_name=first_blank_name,
        total_accepted_pieces=tap,
        total_accepted_kg=_q_kg(tak),
        total_unpacked_pieces=tup,
        total_unpacked_kg=_q_kg(tuk),
        lines=lines,
    )


def _create_warehouse_batch_for_gp_pack_unit(*, op: GpPackOperation, pieces: int):
    """Одна строка склада ГП = одна GP-упаковка (продажа упаковками, select-sources)."""
    from django.utils import timezone

    pcs = int(pieces)
    if pcs <= 0:
        raise ValueError('pieces')
    prof = op.product
    product_label = ((prof.name or '').strip()[:255]) if prof is not None else '—'
    d = timezone.now().date()
    ppp = Decimal(str(pcs))
    return WarehouseBatch.objects.create(
        profile_id=op.product_id,
        product=product_label,
        length_per_piece=None,
        total_meters=None,
        quantity=ppp,
        cost_per_piece=Decimal('0'),
        cost_per_meter=Decimal('0'),
        status=WarehouseBatch.STATUS_AVAILABLE,
        date=d,
        source_batch=None,
        inventory_form=WarehouseBatch.INVENTORY_PACKED,
        unit_meters=None,
        package_total_meters=None,
        pieces_per_package=ppp,
        packages_count=Decimal('1'),
        quality=WarehouseBatch.QUALITY_GOOD,
        stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
    )


def _expand_units_from_lines(lines: list[dict]) -> list[int]:
    """Список штук по каждой физической упаковке (порядок — как во входных строках)."""
    out: list[int] = []
    for ln in lines:
        pc = int(ln['package_count'])
        ppp = int(ln['pieces_per_package'])
        for _ in range(pc):
            out.append(ppp)
    return out


def _fifo_allocate(
    runs_ordered: list[BlankProductionRun],
    need: int,
) -> list[tuple[BlankProductionRun, int, Decimal]]:
    if need <= 0:
        raise DRFValidationError({'code': 'INVALID_SPLIT', 'detail': 'Количество штук должно быть > 0.'})
    left = need
    alloc: list[tuple[BlankProductionRun, int, Decimal]] = []
    for run in runs_ordered:
        if left <= 0:
            break
        up, _ = compute_unpacked_for_run(run)
        if up <= 0:
            continue
        take = min(up, left)
        wpp = Decimal(str(run.weight_kg_per_piece))
        kg = _q_kg(Decimal(take) * wpp)
        alloc.append((run, take, kg))
        left -= take
    if left > 0:
        raise DRFValidationError(
            {
                'code': 'INSUFFICIENT_UNPACKED_PIECES',
                'detail': f'Недостаточно неупакованных штук: не хватает {left} шт.',
            },
        )
    return alloc


def _deduct_unpacked_warehouse_for_allocations(
    fifo: list[tuple[BlankProductionRun, int, Decimal]],
) -> None:
    """
    Списывает штуки с неупакованных строк склада, привязанных к приёмке (BlankProductionRun).
    Без этого после GP-упаковки остаётся «фантомный» остаток на строке приёмки, дублирующий упаковки.
    """
    for run, take, _kg in fifo:
        need = int(take)
        if need <= 0:
            continue
        rows = list(
            WarehouseBatch.objects.select_for_update(of=('self',))
            .filter(
                blank_production_run_id=run.pk,
                status=WarehouseBatch.STATUS_AVAILABLE,
                quality=WarehouseBatch.QUALITY_GOOD,
                stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
                inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
            )
            .order_by('id'),
        )
        if not rows:
            # Старые данные без FK — не блокируем упаковку; возможен дубль в select-sources до ручной правки.
            continue
        for wb in rows:
            if need <= 0:
                break
            q = Decimal(str(wb.quantity))
            if q <= 0:
                continue
            use = min(q, Decimal(need))
            new_q = (q - use).quantize(Decimal('0.0001'))
            need -= int(use)
            wb.quantity = new_q
            wb.save(update_fields=['quantity'])
        if need > 0:
            raise DRFValidationError(
                {
                    'code': 'WAREHOUSE_GP_ACCEPTANCE_ROW_SHORT',
                    'detail': (
                        f'По приёмке #{run.pk} не хватает неупакованной строки склада для списания '
                        f'{take} шт (осталось {need} шт).'
                    ),
                },
            )


@transaction.atomic
def create_gp_pack_operation(
    *,
    user,
    product_id: int,
    blank_id: int,
    kind: str,
    label: str,
    split_mode: str,
    lines: list[dict],
    total_pieces: int,
    client_request_id: str = '',
) -> tuple[GpPackOperation, bool]:
    """Создаёт операцию, единицы упаковки и FIFO-списание по BlankProductionRun. (operation, created)."""
    crid = (client_request_id or '').strip() or None
    if crid:
        existing = GpPackOperation.objects.filter(client_request_id=crid).first()
        if existing:
            return existing, False

    if kind not in dict(GpPackOperation.KIND_CHOICES):
        raise DRFValidationError({'code': 'INVALID_KIND', 'detail': f'Неизвестный тип упаковки: {kind!r}.'})
    if split_mode not in dict(GpPackOperation.SPLIT_CHOICES):
        raise DRFValidationError({'code': 'INVALID_SPLIT', 'detail': f'Неизвестный режим: {split_mode!r}.'})

    if kind in (GpPackOperation.KIND_PALLET, GpPackOperation.KIND_BOX):
        if not (label or '').strip():
            raise DRFValidationError(
                {'code': 'EMPTY_LABEL_WHEN_REQUIRED', 'detail': 'Для короба и поддона укажите метку.'},
            )

    if not lines:
        raise DRFValidationError({'code': 'INVALID_SPLIT', 'detail': 'Поле lines не может быть пустым.'})

    if split_mode == GpPackOperation.SPLIT_SINGLE:
        if len(lines) != 1 or int(lines[0]['package_count']) != 1:
            raise DRFValidationError(
                {'code': 'INVALID_SPLIT', 'detail': 'Режим single: одна строка, package_count=1.'},
            )
        if int(lines[0]['pieces_per_package']) != int(total_pieces):
            raise DRFValidationError(
                {'code': 'INVALID_SPLIT', 'detail': 'Режим single: pieces_per_package должно совпадать с total_pieces.'},
            )
    elif split_mode == GpPackOperation.SPLIT_UNIFORM:
        if len(lines) != 1:
            raise DRFValidationError({'code': 'INVALID_SPLIT', 'detail': 'Режим uniform: ровно одна строка в lines.'})
        pc = int(lines[0]['package_count'])
        ppp = int(lines[0]['pieces_per_package'])
        if pc * ppp != int(total_pieces):
            raise DRFValidationError(
                {'code': 'INVALID_SPLIT', 'detail': 'Режим uniform: package_count * pieces_per_package = total_pieces.'},
            )
    else:  # custom
        s = sum(int(x['package_count']) * int(x['pieces_per_package']) for x in lines)
        if s != int(total_pieces):
            raise DRFValidationError(
                {'code': 'INVALID_SPLIT', 'detail': 'Режим custom: сумма package_count * pieces_per_package = total_pieces.'},
            )

    pieces_per_unit = _expand_units_from_lines(lines)
    if sum(pieces_per_unit) != int(total_pieces):
        raise DRFValidationError({'code': 'INVALID_SPLIT', 'detail': 'Сумма штук по lines не совпадает с total_pieces.'})

    if not PlasticProfile.objects.filter(pk=product_id).exists():
        raise DRFValidationError({'code': 'PRODUCT_BLANK_MISMATCH', 'detail': 'Профиль (product_id) не найден.'})
    if not WorkshopBlank.objects.filter(pk=blank_id).exists():
        raise DRFValidationError({'code': 'PRODUCT_BLANK_MISMATCH', 'detail': 'Заготовка (blank_id) не найдена.'})

    locked_runs = list(
        _annotated_runs_for_group(product_id, blank_id).select_for_update(of=('self',)),
    )
    if not locked_runs:
        raise DRFValidationError(
            {
                'code': 'INSUFFICIENT_UNPACKED_PIECES',
                'detail': 'Нет принятых партий ГП по этой паре product + blank.',
            },
        )

    tup_locked = sum(compute_unpacked_for_run(r)[0] for r in locked_runs)
    if int(total_pieces) > tup_locked:
        raise DRFValidationError(
            {
                'code': 'INSUFFICIENT_UNPACKED_PIECES',
                'detail': (
                    f'Запрошено {total_pieces} шт, доступно неупакованных {tup_locked} шт '
                    f'по группе product={product_id}, blank={blank_id}.'
                ),
            },
        )

    fifo = _fifo_allocate(locked_runs, int(total_pieces))

    op = GpPackOperation.objects.create(
        product_id=product_id,
        blank_id=blank_id,
        kind=kind,
        label=(label or '').strip()[:255],
        split_mode=split_mode,
        total_pieces=int(total_pieces),
        created_by=user if getattr(user, 'pk', None) else None,
        client_request_id=crid[:64] if crid else None,
    )
    for i, p in enumerate(pieces_per_unit, start=1):
        wb = _create_warehouse_batch_for_gp_pack_unit(op=op, pieces=int(p))
        GpPackUnit.objects.create(operation=op, sequence=i, pieces=int(p), warehouse_batch=wb)
    for run, take, kg in fifo:
        GpPackRunAllocation.objects.create(
            operation=op,
            blank_production_run=run,
            pieces=take,
            kg=kg,
        )
    _deduct_unpacked_warehouse_for_allocations(fifo)
    return op, True
