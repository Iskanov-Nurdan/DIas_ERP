import logging
from decimal import Decimal, InvalidOperation

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsAdminOrHasAccess
from .models import WarehouseBatch
from .serializers import WarehouseBatchSerializer

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'error': {'code': code, 'message': message}}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


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
        quantity_raw = request.data.get('quantity')

        errors = []
        if not batch_id:
            errors.append({'field': 'batchId', 'message': 'Обязательное поле'})
        if quantity_raw is None:
            errors.append({'field': 'quantity', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите batchId и quantity', errors=errors)

        batch = WarehouseBatch.objects.filter(pk=batch_id).first()
        if not batch:
            return _err('not_found', 'Партия не найдена', http_status=404)

        if batch.status != WarehouseBatch.STATUS_AVAILABLE:
            return _err('bad_request', 'Партия недоступна для резервирования')

        try:
            q = Decimal(str(quantity_raw))
        except (InvalidOperation, TypeError, ValueError):
            return _err('validation_error', 'Некорректное значение quantity',
                        errors=[{'field': 'quantity', 'message': 'Должно быть числом'}])

        if q <= 0:
            return _err('validation_error', 'quantity должно быть больше 0',
                        errors=[{'field': 'quantity', 'message': 'Должно быть больше 0'}])

        if q > batch.quantity:
            return _err('bad_request',
                        f'Количество превышает доступный остаток ({batch.quantity})',
                        errors=[{'field': 'quantity', 'message': f'Максимум: {batch.quantity}'}])

        batch.status = WarehouseBatch.STATUS_RESERVED
        batch.save(update_fields=['status'])
        return Response(WarehouseBatchSerializer(batch).data)
