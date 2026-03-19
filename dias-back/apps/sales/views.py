import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from .models import Client, Sale, Shipment
from .serializers import ClientSerializer, SaleSerializer, ShipmentSerializer

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'error': {'code': code, 'message': message}}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


class ClientViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'
    activity_section = 'Клиенты'
    activity_label = 'клиент'
    filterset_fields = []
    search_fields = ['name', 'inn', 'contact']
    ordering_fields = ['id', 'name']

    def perform_destroy(self, instance):
        if instance.sales.exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Нельзя удалить клиента с историей продаж'})
        instance.delete()

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        client = self.get_object()
        sales = Sale.objects.filter(client=client).order_by('-date')
        return Response({'items': SaleSerializer(sales, many=True).data})


class SaleViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Sale.objects.select_related('client').all()
    serializer_class = SaleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    activity_section = 'Продажи'
    activity_label = 'продажа'
    filterset_fields = ['client']
    ordering_fields = ['id', 'date']


class ShipmentViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Shipment.objects.select_related('sale__client').all()
    serializer_class = ShipmentSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'shipments'
    activity_section = 'Отгрузки'
    activity_label = 'отгрузка'
    filterset_fields = ['status']
    ordering_fields = ['id']

    @action(detail=True, methods=['post'], url_path='ship')
    def ship(self, request, pk=None):
        shipment = self.get_object()
        if shipment.status != Shipment.STATUS_PENDING:
            return _err('bad_request', 'Отгрузка уже оформлена')

        shipment_date = request.data.get('shipment_date')
        if shipment_date and isinstance(shipment_date, str):
            from datetime import datetime
            try:
                shipment_date = datetime.strptime(shipment_date[:10], '%Y-%m-%d').date()
            except ValueError:
                return _err('validation_error', 'Некорректный формат shipment_date (ожидается YYYY-MM-DD)',
                            errors=[{'field': 'shipment_date', 'message': 'Формат: YYYY-MM-DD'}])
        else:
            from django.utils import timezone
            shipment_date = timezone.now().date()

        shipment.shipment_date = shipment_date
        shipment.status = Shipment.STATUS_SHIPPED
        shipment.save(update_fields=['shipment_date', 'status'])
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=['post'], url_path='deliver')
    def deliver(self, request, pk=None):
        shipment = self.get_object()
        if shipment.status != Shipment.STATUS_SHIPPED:
            return _err('bad_request', 'Отгрузка должна быть в статусе «Отгружено»')

        delivery_date = request.data.get('delivery_date')
        if delivery_date and isinstance(delivery_date, str):
            from datetime import datetime
            try:
                delivery_date = datetime.strptime(delivery_date[:10], '%Y-%m-%d').date()
            except ValueError:
                return _err('validation_error', 'Некорректный формат delivery_date (ожидается YYYY-MM-DD)',
                            errors=[{'field': 'delivery_date', 'message': 'Формат: YYYY-MM-DD'}])
        else:
            from django.utils import timezone
            delivery_date = timezone.now().date()

        shipment.delivery_date = delivery_date
        shipment.status = Shipment.STATUS_DELIVERED
        shipment.save(update_fields=['delivery_date', 'status'])
        return Response(ShipmentSerializer(shipment).data)
