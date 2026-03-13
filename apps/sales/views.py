from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction

from config.permissions import IsAdminOrHasAccess
from .models import Client, Sale, Shipment
from .serializers import ClientSerializer, SaleSerializer, ShipmentSerializer


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'
    filterset_fields = []
    search_fields = ['name', 'inn', 'contact']
    ordering_fields = ['id', 'name']

    def perform_destroy(self, instance):
        if instance.sales.exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Нельзя удалить клиента с продажами'})
        instance.delete()

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        client = self.get_object()
        sales = Sale.objects.filter(client=client).order_by('-date')
        return Response({'items': SaleSerializer(sales, many=True).data})


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.select_related('client').all()
    serializer_class = SaleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    filterset_fields = ['client']
    ordering_fields = ['id', 'date']


class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.select_related('sale__client').all()
    serializer_class = ShipmentSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'shipments'
    filterset_fields = ['status']
    ordering_fields = ['id']

    @action(detail=True, methods=['post'], url_path='ship')
    def ship(self, request, pk=None):
        shipment = self.get_object()
        if shipment.status != Shipment.STATUS_PENDING:
            return Response({
                'error': 'Отгрузка уже оформлена',
                'code': 'INVALID_STATUS',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        shipment_date = request.data.get('shipment_date')
        if shipment_date:
            from datetime import datetime
            if isinstance(shipment_date, str):
                shipment_date = datetime.strptime(shipment_date[:10], '%Y-%m-%d').date()
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
            return Response({
                'error': 'Отгрузка должна быть в статусе "Отгружено"',
                'code': 'INVALID_STATUS',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        delivery_date = request.data.get('delivery_date')
        if delivery_date and isinstance(delivery_date, str):
            from datetime import datetime
            delivery_date = datetime.strptime(delivery_date[:10], '%Y-%m-%d').date()
        else:
            from django.utils import timezone
            delivery_date = timezone.now().date()
        
        shipment.delivery_date = delivery_date
        shipment.status = Shipment.STATUS_DELIVERED
        shipment.save(update_fields=['delivery_date', 'status'])
        return Response(ShipmentSerializer(shipment).data)
