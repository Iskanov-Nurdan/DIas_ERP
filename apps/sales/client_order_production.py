"""
Производственная ветка client_orders: рецепт по профилю, проверка сырья/химии.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from apps.chemistry.fifo import chemistry_stock_kg
from apps.chemistry.models import ChemistryCatalog
from apps.materials.fifo import material_stock_kg
from apps.materials.models import RawMaterial
from apps.production.batch_stock import aggregate_consumption_for_recipe
from apps.recipes.models import PlasticProfile, Recipe, RecipeComponent


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return Decimal(str(x))


def recipe_for_profile(profile_id: int) -> Optional[Recipe]:
    """Первый активный рецепт для профиля (стабильный выбор: по id)."""
    return (
        Recipe.objects.filter(profile_id=profile_id, is_active=True)
        .order_by('id')
        .first()
    )


def _fmt_dec(d: Decimal) -> str:
    from config.api_numbers import api_decimal_str

    return api_decimal_str(d)


def run_resource_check(recipe: Recipe, total_meters: Decimal) -> dict[str, Any]:
    """
    Сравнение потребности по рецепту с остатками (серверная копия логики batch_stock).
    Возвращает: ok, items (нужно / есть), total_meters.
    """
    tm = _d(total_meters)
    out_items: list[dict[str, Any]] = []
    if tm <= 0:
        return {
            'ok': False,
            'items': out_items,
            'total_meters': _fmt_dec(tm),
        }

    try:
        recipe = Recipe.objects.prefetch_related('components').get(pk=recipe.pk)
    except Recipe.DoesNotExist:
        return {'ok': False, 'items': out_items, 'total_meters': _fmt_dec(tm)}

    if not recipe.components.exists():
        return {
            'ok': False,
            'items': [
                {
                    'type': 'system',
                    'id': None,
                    'name': 'Рецепт без компонентов',
                    'needed': _fmt_dec(Decimal('0')),
                    'available': _fmt_dec(Decimal('0')),
                    'enough': False,
                    'unit': '—',
                }
            ],
            'total_meters': _fmt_dec(tm),
        }

    raw_agg, chem_agg = aggregate_consumption_for_recipe(recipe, tm)
    all_ok = True

    for mid, need in raw_agg.items():
        if need <= 0:
            continue
        avail = material_stock_kg(mid)
        mat = RawMaterial.objects.filter(pk=mid).only('id', 'name', 'unit').first()
        name = (mat.name if mat else f'Сырьё id={mid}') or f'Сырьё id={mid}'
        unit = (mat.unit or 'кг') if mat else 'кг'
        enough = avail >= need
        if not enough:
            all_ok = False
        out_items.append(
            {
                'type': RecipeComponent.TYPE_RAW,
                'id': mid,
                'name': name,
                'needed': _fmt_dec(need),
                'available': _fmt_dec(avail),
                'enough': enough,
                'unit': unit,
            }
        )

    for cid, need in chem_agg.items():
        if need <= 0:
            continue
        avail = chemistry_stock_kg(cid)
        cat = ChemistryCatalog.objects.filter(pk=cid).only('id', 'name', 'unit').first()
        if not cat:
            all_ok = False
            out_items.append(
                {
                    'type': RecipeComponent.TYPE_CHEM,
                    'id': cid,
                    'name': f'Химия id={cid}',
                    'needed': _fmt_dec(need),
                    'available': _fmt_dec(avail),
                    'enough': False,
                    'unit': 'кг',
                }
            )
            continue
        enough = avail >= need
        if not enough:
            all_ok = False
        out_items.append(
            {
                'type': RecipeComponent.TYPE_CHEM,
                'id': cid,
                'name': cat.name,
                'needed': _fmt_dec(need),
                'available': _fmt_dec(avail),
                'enough': enough,
                'unit': cat.unit or 'кг',
            }
        )

    if not out_items:
        all_ok = False
        out_items.append(
            {
                'type': 'system',
                'id': None,
                'name': 'Нет требований к сырью/химии по нормам',
                'needed': _fmt_dec(Decimal('0')),
                'available': _fmt_dec(Decimal('0')),
                'enough': False,
                'unit': '—',
            }
        )

    return {'ok': all_ok, 'items': out_items, 'total_meters': _fmt_dec(tm)}


def apply_resource_check_to_order(order) -> None:
    """
    Заполняет у заявки resolved_recipe, resource_check_snapshot, request_status
    (ready / not_ready) по production_profile, длине и количеству. Только in-memory; вызывающий делает save().
    """
    from . import models as sales_models

    if not order.production_profile_id or order.production_length is None or not order.production_quantity:
        order.resolved_recipe = None
        order.resource_check_snapshot = {
            'ok': False,
            'items': [],
            'total_meters': '0',
        }
        order.request_status = sales_models.REQUEST_STATUS_NOT_READY
        return
    tm = order.request_total_meters
    if tm is None:
        tm = (Decimal(str(order.production_length)) * Decimal(int(order.production_quantity))).quantize(
            Decimal('0.0001'),
        )
    else:
        tm = Decimal(str(tm))

    recipe = recipe_for_profile(order.production_profile_id)
    if recipe is None:
        order.resolved_recipe = None
        order.resource_check_snapshot = {
            'ok': False,
            'items': [
                {
                    'type': 'system',
                    'id': None,
                    'name': 'Нет активного рецепта для профиля',
                    'needed': '0',
                    'available': '0',
                    'enough': False,
                    'unit': '—',
                }
            ],
            'total_meters': _fmt_dec(tm),
        }
        order.request_status = sales_models.REQUEST_STATUS_NOT_READY
        return

    check = run_resource_check(recipe, tm)
    order.resolved_recipe = recipe
    order.resource_check_snapshot = check
    order.request_status = (
        sales_models.REQUEST_STATUS_READY if check.get('ok') else sales_models.REQUEST_STATUS_NOT_READY
    )


def profile_name(profile_id: Optional[int]) -> str:
    if not profile_id:
        return '—'
    p = PlasticProfile.objects.filter(pk=profile_id).only('name').first()
    return (p.name if p else '—') or '—'
