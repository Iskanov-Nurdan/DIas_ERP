"""
Списание сырья и расход остатков химии при сохранении замеса (recipe-run).

Правила:
- Движения только в связке с POST/PATCH chemistry/recipe-runs/ (атомарно с сохранением партий).
- PATCH: сначала откат старых движений по этому run, затем сохранение состава, затем новое списание.
- Сырьё: MaterialWriteoff с reason=recipe_run, reference_id = id замеса.
- Химия: списание с ChemistryStock (как склад химэлементов).
- ОТК / incoming / склад ГП здесь не трогаются.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Tuple

from django.db import transaction
from django.db.models import F, Sum

from rest_framework.exceptions import ValidationError

from apps.materials.models import Incoming, MaterialWriteoff, RawMaterial
from apps.chemistry.models import ChemistryCatalog, ChemistryStock
from apps.production.models import RecipeRun, RecipeRunBatchComponent

if TYPE_CHECKING:
    pass

RECIPE_RUN_REASON = 'recipe_run'


def _q(d) -> Decimal:
    if d is None:
        return Decimal('0')
    return Decimal(str(d))


def aggregate_run_consumption(run_id: int) -> Tuple[Dict[int, Decimal], Dict[int, Decimal]]:
    """Суммарный расход по сырью (material_id) и по химии (chemistry_id) для замеса."""
    raw: Dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    chem: Dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    qs = RecipeRunBatchComponent.objects.filter(batch__run_id=run_id).only(
        'raw_material_id', 'chemistry_id', 'quantity',
    )
    for row in qs.iterator():
        q = _q(row.quantity)
        if q <= 0:
            continue
        if row.raw_material_id:
            raw[row.raw_material_id] += q
        elif row.chemistry_id:
            chem[row.chemistry_id] += q
    return dict(raw), dict(chem)


def raw_material_available(material_id: int) -> Decimal:
    inc = Incoming.objects.filter(material_id=material_id).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    woff = MaterialWriteoff.objects.filter(material_id=material_id).aggregate(s=Sum('quantity'))['s'] or Decimal('0')
    return _q(inc) - _q(woff)


@transaction.atomic
def reverse_recipe_run_stock(run: RecipeRun) -> None:
    """
    Откат движений по замесу: удалить списания сырья с reason=recipe_run;
    вернуть количество химии на склад по текущему составу партий (до следующего сохранения).
    """
    RecipeRun.objects.select_for_update().get(pk=run.pk)
    _, chem_agg = aggregate_run_consumption(run.pk)
    MaterialWriteoff.objects.filter(reason=RECIPE_RUN_REASON, reference_id=run.pk).delete()
    for chem_id, qty in chem_agg.items():
        if qty <= 0:
            continue
        cat = ChemistryCatalog.objects.filter(pk=chem_id).only('id', 'unit').first()
        if not cat:
            continue
        stock, _ = ChemistryStock.objects.select_for_update().get_or_create(
            chemistry_id=chem_id,
            defaults={'quantity': Decimal('0'), 'unit': cat.unit or 'кг'},
        )
        ChemistryStock.objects.filter(pk=stock.pk).update(quantity=F('quantity') + qty)

    RecipeRun.objects.filter(pk=run.pk).update(recipe_run_consumption_applied=False)


@transaction.atomic
def apply_recipe_run_stock(run: RecipeRun) -> None:
    """
    Проверить остатки и применить расход по текущему составу замеса в БД.
    """
    RecipeRun.objects.select_for_update().get(pk=run.pk)
    raw_agg, chem_agg = aggregate_run_consumption(run.pk)
    missing: list[dict] = []

    for mid, req in raw_agg.items():
        if req <= 0:
            continue
        avail = raw_material_available(mid)
        if avail < req:
            name = RawMaterial.objects.filter(pk=mid).values_list('name', flat=True).first() or f'id={mid}'
            unit = RawMaterial.objects.filter(pk=mid).values_list('unit', flat=True).first() or 'кг'
            missing.append({
                'component': name,
                'required': float(req),
                'available': float(avail),
                'unit': unit,
            })

    for cid, req in chem_agg.items():
        if req <= 0:
            continue
        cat = ChemistryCatalog.objects.filter(pk=cid).only('id', 'name', 'unit').first()
        if not cat:
            missing.append({
                'component': f'Химия id={cid}',
                'required': float(req),
                'available': 0.0,
                'unit': '—',
            })
            continue
        stock = ChemistryStock.objects.select_for_update().filter(chemistry_id=cid).first()
        avail = _q(stock.quantity) if stock else Decimal('0')
        if avail < req:
            missing.append({
                'component': cat.name,
                'required': float(req),
                'available': float(avail),
                'unit': cat.unit or 'кг',
            })

    if missing:
        raise ValidationError({
            'code': 'INSUFFICIENT_STOCK',
            'error': 'Недостаточно остатков для замеса',
            'detail': 'Недостаточно остатков для замеса',
            'missing': missing,
        })

    for mid, req in raw_agg.items():
        if req <= 0:
            continue
        m = RawMaterial.objects.get(pk=mid)
        MaterialWriteoff.objects.create(
            material=m,
            quantity=req,
            unit=(m.unit or 'кг')[:50],
            reason=RECIPE_RUN_REASON,
            reference_id=run.pk,
        )

    for cid, req in chem_agg.items():
        if req <= 0:
            continue
        cat = ChemistryCatalog.objects.get(pk=cid)
        stock, _ = ChemistryStock.objects.select_for_update().get_or_create(
            chemistry_id=cid,
            defaults={'quantity': Decimal('0'), 'unit': cat.unit or 'кг'},
        )
        ChemistryStock.objects.filter(pk=stock.pk).update(quantity=F('quantity') - req)

    RecipeRun.objects.filter(pk=run.pk).update(recipe_run_consumption_applied=True)
