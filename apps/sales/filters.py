import django_filters

from .models import Client, Sale


class ClientFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = Client
        fields = ['is_active']


class SaleFilter(django_filters.FilterSet):
    client_id = django_filters.NumberFilter(field_name='client_id')

    class Meta:
        model = Sale
        fields = ['client']
