import django_filters

from .models import WarehouseBatch


def _canonical_inventory_form(value: str) -> str:
    v = str(value).strip().lower()
    alias = {
        'not_packed': WarehouseBatch.INVENTORY_UNPACKED,
        'unpacked': WarehouseBatch.INVENTORY_UNPACKED,
        'opened': WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        'open': WarehouseBatch.INVENTORY_OPEN_PACKAGE,
        'packed': WarehouseBatch.INVENTORY_PACKED,
    }
    return alias.get(v, v)


class WarehouseBatchFilter(django_filters.FilterSet):
    """
    stock_form / packaging_status — алиасы inventory_form.
    not_packed → unpacked (как в контракте фронта).
    """

    inventory_form = django_filters.CharFilter(method='filter_inventory_form')
    stock_form = django_filters.CharFilter(method='filter_stock_form')
    packaging_status = django_filters.CharFilter(method='filter_packaging_status')

    def filter_inventory_form(self, queryset, name, value):
        if not value:
            return queryset
        canon = _canonical_inventory_form(value)
        return queryset.filter(inventory_form=canon)

    def filter_stock_form(self, queryset, name, value):
        return self.filter_inventory_form(queryset, name, value)

    def filter_packaging_status(self, queryset, name, value):
        return self.filter_inventory_form(queryset, name, value)

    class Meta:
        model = WarehouseBatch
        fields = ['status', 'product']
