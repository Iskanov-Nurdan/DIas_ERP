from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.response import Response

from apps.production.models import ProductionBatch
from apps.production.serializers import BatchListSerializer
from config.permissions import IsAdminOrHasAccess


@extend_schema_view(
    list=extend_schema(
        tags=['otk'],
        summary='Партии в очереди ОТК',
        description='Тело: `{ "items": [ ... ] }` — элементы как у GET /api/batches/ (BatchListSerializer). Приёмка: POST /api/batches/{id}/otk_accept/.',
        responses={200: OpenApiTypes.OBJECT},
    ),
)
class OtkPendingView(viewsets.ViewSet):
    """
    GET /api/otk/pending/ — список партий, ожидающих проверки ОТК.
    Для проверки партии: POST /api/batches/{id}/otk_accept/
    """
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'otk'

    def list(self, request):
        qs = (
            ProductionBatch.objects.filter(otk_status=ProductionBatch.OTK_PENDING)
            .select_related('order', 'order__recipe', 'order__line', 'operator')
            .prefetch_related('otk_checks__inspector')
        )
        return Response({'items': BatchListSerializer(qs, many=True).data})
