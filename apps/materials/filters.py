import django_filters

from .models import MaterialBatch


class MaterialBatchFilter(django_filters.FilterSet):
    """
    GET /incoming/?material_id=1
    Дата прихода: received_at (диапазон).
    """

    material_id = django_filters.NumberFilter(field_name='material_id')
    material = django_filters.NumberFilter(field_name='material_id')
    received_at = django_filters.DateFromToRangeFilter(field_name='received_at')

    class Meta:
        model = MaterialBatch
        fields = ('material_id', 'received_at')
