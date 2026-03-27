import django_filters

from .models import Sale


class SaleFilter(django_filters.FilterSet):
    client_id = django_filters.NumberFilter(field_name='client_id')

    class Meta:
        model = Sale
        fields = ['client']
