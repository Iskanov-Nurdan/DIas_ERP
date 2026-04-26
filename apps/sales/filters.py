import django_filters

from .models import Client, DefectRecord, Order, Payment, Return, Sale


class ClientFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = Client
        fields = ['is_active']


class SaleFilter(django_filters.FilterSet):
    client_id = django_filters.NumberFilter(field_name='client_id')
    sale_status = django_filters.CharFilter(field_name='sale_status')
    is_defect_sale = django_filters.BooleanFilter()
    linked_order = django_filters.NumberFilter(field_name='linked_order_id')
    date_from = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_to = django_filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Sale
        fields = ['client', 'sale_status', 'is_defect_sale', 'linked_order']


class OrderFilter(django_filters.FilterSet):
    client_id = django_filters.NumberFilter(field_name='client_id')
    status = django_filters.CharFilter(field_name='status')
    source_type = django_filters.CharFilter(field_name='source_type')
    date_from = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_to = django_filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Order
        fields = ['client', 'status', 'source_type']


class PaymentFilter(django_filters.FilterSet):
    client_id = django_filters.NumberFilter(field_name='client_id')
    payment_type = django_filters.CharFilter(field_name='payment_type')
    payment_method = django_filters.CharFilter(field_name='payment_method')
    date_from = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_to = django_filters.DateFilter(field_name='date', lookup_expr='lte')
    linked_order = django_filters.NumberFilter(field_name='linked_order_id')
    linked_sale = django_filters.NumberFilter(field_name='linked_sale_id')

    class Meta:
        model = Payment
        fields = ['client', 'payment_type', 'payment_method']


class ReturnFilter(django_filters.FilterSet):
    sale_id = django_filters.NumberFilter(field_name='sale_id')
    client_id = django_filters.NumberFilter(field_name='sale__client_id')
    date_from = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_to = django_filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Return
        fields = ['sale']


class DefectFilter(django_filters.FilterSet):
    source_type = django_filters.CharFilter(field_name='source_type')
    status = django_filters.CharFilter(field_name='status')
    profile_id = django_filters.NumberFilter(field_name='profile_id')

    class Meta:
        model = DefectRecord
        fields = ['source_type', 'status', 'profile']
