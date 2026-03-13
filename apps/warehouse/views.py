from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsAdminOrHasAccess
from .models import WarehouseBatch
from .serializers import WarehouseBatchSerializer


class WarehouseBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WarehouseBatch.objects.all()
    serializer_class = WarehouseBatchSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'
    filterset_fields = ['status', 'product']
    ordering_fields = ['id', 'date']

    @action(detail=False, methods=['post'], url_path='reserve')
    def reserve(self, request):
        batch_id = request.data.get('batchId')
        quantity = request.data.get('quantity')
        sale_id = request.data.get('saleId')
        if not batch_id or quantity is None:
            return Response({
                'error': 'Укажите batchId, quantity, saleId',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        batch = WarehouseBatch.objects.filter(pk=batch_id).first()
        if not batch:
            return Response({
                'error': 'Партия не найдена',
                'code': 'NOT_FOUND',
                'details': {},
            }, status=status.HTTP_404_NOT_FOUND)
        if batch.status != WarehouseBatch.STATUS_AVAILABLE:
            return Response({
                'error': 'Партия недоступна для резерва',
                'code': 'INVALID_STATUS',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        from decimal import Decimal, InvalidOperation
        try:
            q = Decimal(str(quantity))
        except (InvalidOperation, TypeError, ValueError):
            return Response({
                'error': 'Некорректное quantity',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        if q <= 0:
            return Response({
                'error': 'quantity должно быть больше 0',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        if q > batch.quantity:
            return Response({
                'error': 'Количество превышает доступный остаток партии',
                'code': 'INSUFFICIENT_QUANTITY',
                'details': {'available': float(batch.quantity)},
            }, status=status.HTTP_400_BAD_REQUEST)
        batch.status = WarehouseBatch.STATUS_RESERVED
        batch.save(update_fields=['status'])
        return Response(WarehouseBatchSerializer(batch).data)
