"""
Сервис определения рекомендованной цены.

Приоритет:
  1. Индивидуальная цена клиента (ClientPrice, действующая на сегодня)
  2. Базовая цена из активного прайс-листа (ProductPrice + PriceList)
  3. None — цена не определена (ручной ввод)

Ключ поиска: profile_id (FK на PlasticProfile) или текстовое название product.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class PriceSuggestion:
    price: Optional[Decimal]
    unit: Optional[str]
    source: Optional[str]  # 'client_price' | 'price_list' | None
    source_id: Optional[int]
    note: Optional[str]


def suggest_price(
    client_id: Optional[int],
    profile_id: Optional[int] = None,
    product: Optional[str] = None,
    on_date: Optional[date] = None,
) -> PriceSuggestion:
    """
    Вернуть рекомендованную цену по клиенту + товару (профилю).
    Сначала ищем индивидуальную цену клиента, затем базовый прайс.
    """
    if on_date is None:
        on_date = date.today()

    # 1. Индивидуальная цена клиента
    if client_id:
        cp = _find_client_price(client_id, profile_id, product, on_date)
        if cp is not None:
            return PriceSuggestion(
                price=cp.price,
                unit=cp.unit,
                source='client_price',
                source_id=cp.pk,
                note=None,
            )

    # 2. Базовый прайс
    pp = _find_product_price(profile_id, product, on_date)
    if pp is not None:
        return PriceSuggestion(
            price=pp.price,
            unit=pp.unit,
            source='price_list',
            source_id=pp.pk,
            note=f'Прайс: {pp.price_list.name}',
        )

    return PriceSuggestion(price=None, unit=None, source=None, source_id=None, note=None)


def _find_client_price(client_id, profile_id, product, on_date):
    from .models import ClientPrice
    qs = ClientPrice.objects.filter(client_id=client_id)
    if profile_id:
        qs_p = qs.filter(profile_id=profile_id)
        result = _filter_valid_date(qs_p, on_date).first()
        if result:
            return result
    if product:
        qs_t = qs.filter(profile__isnull=True, product__iexact=product)
        result = _filter_valid_date(qs_t, on_date).first()
        if result:
            return result
    return None


def _find_product_price(profile_id, product, on_date):
    from .models import ProductPrice, PriceList
    active_lists = PriceList.objects.filter(is_active=True)
    active_lists = _filter_valid_date(active_lists, on_date)
    active_list_ids = list(active_lists.values_list('id', flat=True))
    if not active_list_ids:
        return None

    qs = ProductPrice.objects.filter(price_list_id__in=active_list_ids).select_related('price_list')
    if profile_id:
        result = qs.filter(profile_id=profile_id).first()
        if result:
            return result
    if product:
        result = qs.filter(profile__isnull=True, product__iexact=product).first()
        if result:
            return result
    return None


def _filter_valid_date(qs, on_date):
    from django.db.models import Q
    return qs.filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=on_date),
        Q(valid_to__isnull=True) | Q(valid_to__gte=on_date),
    )


def price_suggestion_to_dict(s: PriceSuggestion) -> dict:
    from config.api_numbers import api_decimal_str
    return {
        'price': api_decimal_str(s.price) if s.price is not None else None,
        'unit': s.unit,
        'source': s.source,
        'source_id': s.source_id,
        'note': s.note,
    }
