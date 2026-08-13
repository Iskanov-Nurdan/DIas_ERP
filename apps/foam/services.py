"""Транзакционная бизнес-логика линии «Пенополистирол» — см. BACKEND_FOAM_REQUIREMENTS.md."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.realtime.broadcast import schedule_push

from . import formulas
from .constants import OUTPUT_FORMAT_CUBE, OUTPUT_FORMAT_GRANULE, OUTPUT_FORMAT_SHEET
from .models import (
    FoamDensityGrade,
    FoamGpOperation,
    FoamGpStock,
    FoamProductionRun,
    FoamRawLot,
    FoamSale,
    FoamSaleLine,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def generate_lot_number(*, at=None) -> str:
    """KG-{YYMM}-{seq:02d}, seq — порядковый номер лота в этом месяце."""
    at = at or timezone.localtime()
    prefix = f'KG-{at.strftime("%y%m")}-'
    count = FoamRawLot.objects.filter(
        received_at__year=at.year, received_at__month=at.month
    ).count()
    return f'{prefix}{count + 1:02d}'


def _get_or_create_stock(
    *, output_format: str, grade: Optional[FoamDensityGrade], thickness_cm: Optional[int]
) -> FoamGpStock:
    stock, _ = FoamGpStock.objects.select_for_update().get_or_create(
        output_format=output_format,
        grade=grade,
        thickness_cm=thickness_cm,
        defaults={'qty': Decimal('0')},
    )
    return stock


@transaction.atomic
def run_production(
    *,
    lot_id: int,
    input_kg: Decimal,
    output_format: str,
    grade_code: Optional[str],
    user: Optional['AbstractUser'] = None,
) -> FoamProductionRun:
    input_kg = _d(input_kg)
    if input_kg <= 0:
        raise ValidationError({'input_kg': 'Количество должно быть > 0'})

    lot = FoamRawLot.objects.select_for_update().filter(pk=lot_id).first()
    if lot is None:
        raise ValidationError({'lot_id': 'Лот сырья не найден'})
    if input_kg > lot.remaining_kg:
        raise ValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Расход больше остатка лота',
                'detail': 'Расход больше остатка лота',
                'input_kg': str(input_kg),
                'remaining_kg': str(lot.remaining_kg),
            }
        )

    grade = None
    if output_format == OUTPUT_FORMAT_CUBE:
        if not grade_code:
            raise ValidationError({'grade_code': 'Обязательно для формата "куб"'})
        grade = FoamDensityGrade.objects.filter(code=grade_code).first()
        if grade is None:
            raise ValidationError({'grade_code': 'Плотность не найдена в справочнике'})
        output_qty = formulas.cube_output_qty(input_kg, grade.min_kg_m3, grade.max_kg_m3)
    elif output_format == OUTPUT_FORMAT_GRANULE:
        output_qty = formulas.granule_output_qty(input_kg)
    else:
        raise ValidationError({'output_format': 'Допустимо: cube или granule'})

    lot.remaining_kg = lot.remaining_kg - input_kg
    lot.save(update_fields=['remaining_kg'])

    run = FoamProductionRun.objects.create(
        lot=lot,
        grade=grade,
        input_kg=input_kg,
        output_format=output_format,
        output_qty=output_qty,
        operator=user if user is not None and getattr(user, 'is_authenticated', False) else None,
    )

    stock = _get_or_create_stock(output_format=output_format, grade=grade, thickness_cm=None)
    stock.qty = stock.qty + output_qty
    stock.save(update_fields=['qty'])

    FoamGpOperation.objects.create(
        kind='production_intake',
        output_format=output_format,
        grade=grade,
        thickness_cm=None,
        qty=output_qty,
        ref=f'production-run-{run.pk}',
    )

    transaction.on_commit(lambda: schedule_push(resource='foam_raw_lot', action='updated', entity_id=lot.pk))
    transaction.on_commit(
        lambda: schedule_push(resource='foam_production_run', action='created', entity_id=run.pk)
    )
    transaction.on_commit(lambda: schedule_push(resource='foam_gp_stock', action='changed'))

    return run


@transaction.atomic
def cut_cube(
    *, cube_stock_id: int, thickness_cm: int, cubes_qty: Decimal
) -> tuple[FoamGpStock, FoamGpStock]:
    cubes_qty = _d(cubes_qty)
    if cubes_qty <= 0:
        raise ValidationError({'cubes_qty': 'Количество должно быть > 0'})
    thickness_cm = int(thickness_cm)
    if thickness_cm <= 0:
        raise ValidationError({'thickness_cm': 'Толщина должна быть > 0'})

    cube_stock = FoamGpStock.objects.select_for_update().filter(pk=cube_stock_id).first()
    if cube_stock is None or cube_stock.output_format != OUTPUT_FORMAT_CUBE:
        raise ValidationError({'cube_stock_id': 'Строка остатка не найдена или это не куб'})
    if cubes_qty > cube_stock.qty:
        raise ValidationError(
            {
                'code': 'INSUFFICIENT_STOCK',
                'error': 'Количество кубов больше остатка',
                'detail': 'Количество кубов больше остатка',
                'cubes_qty': str(cubes_qty),
                'available': str(cube_stock.qty),
            }
        )

    sheets_qty = formulas.sheets_from_cut(thickness_cm, cubes_qty)

    cube_stock.qty = cube_stock.qty - cubes_qty
    cube_stock.save(update_fields=['qty'])

    sheet_stock = _get_or_create_stock(
        output_format=OUTPUT_FORMAT_SHEET, grade=cube_stock.grade, thickness_cm=thickness_cm
    )
    sheet_stock.qty = sheet_stock.qty + sheets_qty
    sheet_stock.save(update_fields=['qty'])

    now = timezone.now()
    FoamGpOperation.objects.create(
        kind='cut_in',
        output_format=OUTPUT_FORMAT_SHEET,
        grade=cube_stock.grade,
        thickness_cm=thickness_cm,
        qty=Decimal(sheets_qty),
        created_at=now,
        ref=f'cut-{cube_stock.pk}',
    )
    FoamGpOperation.objects.create(
        kind='cut_out',
        output_format=OUTPUT_FORMAT_CUBE,
        grade=cube_stock.grade,
        thickness_cm=None,
        qty=-cubes_qty,
        created_at=now,
        ref=f'cut-{cube_stock.pk}',
    )

    transaction.on_commit(lambda: schedule_push(resource='foam_gp_stock', action='changed'))

    return cube_stock, sheet_stock


def _payment_status(total_amount: Decimal, paid_amount: Decimal) -> str:
    if paid_amount <= 0:
        return 'debt'
    if paid_amount < total_amount:
        return 'partial'
    return 'paid'


@transaction.atomic
def create_sale(
    *, client: str, sale_date, lines_data: list[dict], paid_amount: Decimal
) -> FoamSale:
    if not lines_data:
        raise ValidationError({'lines': 'Нужна минимум одна строка'})

    resolved_lines: list[tuple[FoamGpStock, Decimal, Decimal]] = []
    total_amount = Decimal('0')
    for line in lines_data:
        stock_id = line.get('stock_id')
        qty = _d(line.get('qty'))
        unit_price = _d(line.get('unit_price'))
        if qty <= 0:
            raise ValidationError({'lines': 'qty должно быть > 0'})
        if unit_price < 0:
            raise ValidationError({'lines': 'unit_price должно быть ≥ 0'})
        stock = FoamGpStock.objects.select_for_update().filter(pk=stock_id).first()
        if stock is None:
            raise ValidationError({'lines': f'Остаток stock_id={stock_id} не найден'})
        if qty > stock.qty:
            raise ValidationError(
                {
                    'code': 'INSUFFICIENT_STOCK',
                    'error': 'Недостаточно остатка на складе',
                    'detail': 'Недостаточно остатка на складе',
                    'stock_id': stock_id,
                    'qty': str(qty),
                    'available': str(stock.qty),
                }
            )
        resolved_lines.append((stock, qty, unit_price))
        total_amount += qty * unit_price

    total_amount = total_amount.quantize(Decimal('0.01'))
    paid_amount = _d(paid_amount)
    if paid_amount < 0 or paid_amount > total_amount:
        raise ValidationError({'paid_amount': 'Должно быть в диапазоне 0..total_amount'})

    sale = FoamSale.objects.create(
        client=client,
        sale_date=sale_date,
        total_amount=total_amount,
        paid_amount=paid_amount,
        payment_status=_payment_status(total_amount, paid_amount),
    )

    for stock, qty, unit_price in resolved_lines:
        FoamSaleLine.objects.create(sale=sale, stock=stock, qty=qty, unit_price=unit_price)
        stock.qty = stock.qty - qty
        stock.save(update_fields=['qty'])
        FoamGpOperation.objects.create(
            kind='sale',
            output_format=stock.output_format,
            grade=stock.grade,
            thickness_cm=stock.thickness_cm,
            qty=-qty,
            ref=f'sale-{sale.pk}',
        )

    transaction.on_commit(lambda: schedule_push(resource='foam_sale', action='created', entity_id=sale.pk))
    transaction.on_commit(lambda: schedule_push(resource='foam_gp_stock', action='changed'))

    return sale
