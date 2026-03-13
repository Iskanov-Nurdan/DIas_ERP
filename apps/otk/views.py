from rest_framework import viewsets
from rest_framework.response import Response

from config.permissions import IsAdminOrHasAccess
from apps.production.models import ProductionBatch


class OtkPendingView(viewsets.ViewSet):
    """
    GET /api/otk/pending/ — список партий ожидающих проверки ОТК.
    Для проверки партии используйте POST /api/batches/{id}/otk_accept/
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'otk'

    def list(self, request):
        qs = ProductionBatch.objects.filter(otk_status=ProductionBatch.OTK_PENDING).select_related('order')
        from apps.production.serializers import ProductionBatchSerializer
        return Response({'items': ProductionBatchSerializer(qs, many=True).data})


class OtkCheckView(viewsets.ViewSet):
    """
    DEPRECATED: используйте POST /api/batches/{id}/otk_accept/ вместо этого эндпоинта.
    Оставлено для обратной совместимости.
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'otk'

    def create(self, request):
        from rest_framework.response import Response
        from rest_framework import status
        return Response({
            'error': 'Этот эндпоинт устарел. Используйте POST /api/batches/{id}/otk_accept/',
            'code': 'DEPRECATED',
            'details': {},
        }, status=status.HTTP_410_GONE)
