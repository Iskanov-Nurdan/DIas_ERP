"""Расчёты массы бочки/дроби и списание с цеха."""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from django.db import transaction

from apps.warehouse.models import WarehouseBatch
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import BlankProductionRun, WorkshopBlank, WorkshopPreparedState

DEC_KG_QUANTIZE = Decimal('0.000001')


def _q_kg(value: Decimal) -> Decimal:
    return value.quantize(DEC_KG_QUANTIZE)


def total_on_workshop(barrels: int, extra_kg: Decimal, recipe_kg_per_barrel: Decimal) -> Decimal:
    return _q_kg(Decimal(barrels) * recipe_kg_per_barrel + Decimal(str(extra_kg)))


def split_total_to_barrels_and_extra(total_kg: Decimal, recipe_kg_per_barrel: Decimal) -> tuple[int, Decimal]:
    r = Decimal(str(recipe_kg_per_barrel))
    t = Decimal(str(total_kg))
    if r <= 0:
        raise ValueError('recipe_kg_per_barrel должно быть > 0')
    barrels = int((t / r).to_integral_value(rounding=ROUND_DOWN))
    extra = t - barrels * r
    return barrels, _q_kg(extra)


def get_or_create_prepared(blank: WorkshopBlank) -> WorkshopPreparedState:
    state, _ = WorkshopPreparedState.objects.get_or_create(
        blank_id=blank.pk,
        defaults={'barrels': 0, 'extra_kg': Decimal('0')},
    )
    return state


@transaction.atomic
def deduct_blank_from_workshop(blank: WorkshopBlank, use_kg: Decimal) -> WorkshopPreparedState:
    use_kg = _q_kg(Decimal(str(use_kg)))
    if use_kg < 0:
        raise WorkshopConflict(detail='Списание с цеха отрицательно.')
    barrel_kg = _q_kg(Decimal(str(blank.recipe_kg_per_barrel)))
    if barrel_kg <= 0:
        raise WorkshopConflict(detail='В справочнике заготовки задан некорректный вес бочки.')
    get_or_create_prepared(blank)
    prepared = WorkshopPreparedState.objects.select_for_update().get(blank_id=blank.pk)
    cur_total = total_on_workshop(prepared.barrels, prepared.extra_kg, barrel_kg)
    if cur_total < use_kg:
        raise WorkshopConflict(detail='Нехватка массы заготовки на цеху для указанного списания.')
    new_total = cur_total - use_kg
    new_barrels, new_extra = split_total_to_barrels_and_extra(new_total, barrel_kg)
    prepared.barrels = new_barrels
    prepared.extra_kg = new_extra
    # Заготовка на цеху — одна перемешанная масса, а не помеченные партии:
    # при списании доля брака в остатке уменьшается пропорционально тому,
    # какую часть от всей массы забрали (тот же принцип, что при усреднении
    # в перемешанной бочке). Пример: было 10 кг, из них 2 кг брака (20%),
    # списали 4 кг → из них «ушло» 4*20% = 0.8 кг брака, в остатке осталось
    # 2 - 0.8 = 1.2 кг брака из 6 кг.
    prepared.defect_kg = _q_kg(prepared.defect_kg * new_total / cur_total) if cur_total > 0 else Decimal('0')
    if prepared.defect_kg > new_total:
        prepared.defect_kg = new_total
    prepared.save(update_fields=['barrels', 'extra_kg', 'defect_kg'])
    return prepared


@transaction.atomic
def append_kg_to_workshop_prepared(
    blank: WorkshopBlank, kg: Decimal, *, defect_kg: Decimal = Decimal('0'),
) -> WorkshopPreparedState:
    """
    Добавить кг к остатку заготовки на цеху (остаток машины после ГП, возврат
    брака ОТК и т.д.). defect_kg — какая часть добавляемых kg является браком
    (должна быть <= kg); увеличивает счётчик WorkshopPreparedState.defect_kg,
    который используется для плитки «Брак» на карточке цеха.
    """
    kg = _q_kg(Decimal(str(kg)))
    if kg <= 0:
        return get_or_create_prepared(blank)
    defect_kg = min(_q_kg(Decimal(str(defect_kg))), kg)
    barrel_kg = _q_kg(Decimal(str(blank.recipe_kg_per_barrel)))
    if barrel_kg <= 0:
        raise WorkshopConflict(detail='В справочнике заготовки задан некорректный вес бочки.')
    prepared = get_or_create_prepared(blank)
    prepared = WorkshopPreparedState.objects.select_for_update().get(pk=prepared.pk)
    cur_total = total_on_workshop(prepared.barrels, prepared.extra_kg, barrel_kg)
    new_total = cur_total + kg
    new_barrels, new_extra = split_total_to_barrels_and_extra(new_total, barrel_kg)
    prepared.barrels = new_barrels
    prepared.extra_kg = new_extra
    if defect_kg > 0:
        prepared.defect_kg = _q_kg(prepared.defect_kg + defect_kg)
    prepared.save(update_fields=['barrels', 'extra_kg', 'defect_kg'])
    return prepared


@transaction.atomic
def append_machine_remainder_to_workshop(blank: WorkshopBlank, remainder_kg: Decimal) -> WorkshopPreparedState:
    return append_kg_to_workshop_prepared(blank, remainder_kg)


def accept_goods_to_warehouse_gp(
    run: BlankProductionRun,
    accepted_pieces: int,
) -> WarehouseBatch | None:
    """Оприходование на склад ГП (0 штук — без строки склада)."""

    wp = BlankProductionRun.objects.select_for_update().get(pk=run.pk)

    if accepted_pieces <= 0:
        return None

    from .unit_cost import workshop_run_unit_cost_per_piece

    unit_cost = workshop_run_unit_cost_per_piece(wp) or Decimal('0')

    warehouse_row = WarehouseBatch.objects.create(
        profile=wp.product,
        product=wp.product_name_snapshot or wp.product.name,
        length_per_piece=None,
        total_meters=None,
        quantity=_q_kg(Decimal(accepted_pieces)),
        cost_per_piece=unit_cost.quantize(Decimal('0.0001')),
        date=_today_for_warehouse(),
        status=WarehouseBatch.STATUS_AVAILABLE,
        inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        quality=WarehouseBatch.QUALITY_GOOD,
        otk_checked_at=wp.otk_recorded_at,
        blank_production_run=wp,
    )
    return warehouse_row


def _today_for_warehouse():
    from django.utils import timezone

    return timezone.now().date()
