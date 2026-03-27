import django_filters

from .models import Incoming


class IncomingFilter(django_filters.FilterSet):
    """
    Точный отбор по сырью: material_id или алиас material (оба — id сырья).
    GET /incoming/?material_id=1 или ?material=1
    """

    material_id = django_filters.NumberFilter(field_name='material_id')
    material = django_filters.NumberFilter(field_name='material_id')

    class Meta:
        model = Incoming
        fields = ('date',)
