from django.db.models import Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from apps.production.models import RecipeRunBatchComponent
from apps.recipes.models import RecipeComponent
from config.permissions import IsAdminOrHasAccess
from .filters import IncomingFilter
from .models import RawMaterial, Incoming, MaterialWriteoff
from .serializers import RawMaterialSerializer, IncomingSerializer


@extend_schema_view(
    list=extend_schema(
        tags=['materials'],
        summary='Остатки сырья по справочнику',
        responses={
            200: OpenApiTypes.OBJECT,
        },
        description='Тело: `{ "items": [ { "id", "name", "balance", "unit", "min_balance" } ] }` без пагинации.',
    ),
)
class MaterialsBalancesView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def list(self, request):
        materials = RawMaterial.objects.all()
        result = []
        for m in materials:
            total_in = Incoming.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            total_out = MaterialWriteoff.objects.filter(material=m).aggregate(s=Sum('quantity'))['s'] or 0
            result.append({
                'id': m.id,
                'name': m.name,
                'balance': float(total_in - total_out),
                'unit': m.unit,
                'min_balance': float(m.min_balance) if m.min_balance is not None else None,
            })
        return Response({'items': result})


class RawMaterialViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = RawMaterial.objects.all()
    serializer_class = RawMaterialSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    activity_section = 'Материалы'
    activity_label = 'материал'
    filterset_fields = ['unit']
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        blockers = []
        if RecipeComponent.objects.filter(raw_material=instance).exists():
            blockers.append('сырьё указано в рецептах')
        if RecipeRunBatchComponent.objects.filter(raw_material=instance).exists():
            blockers.append('есть расход в замесах')
        inc = Incoming.objects.filter(material=instance).aggregate(s=Sum('quantity'))['s'] or 0
        woff = MaterialWriteoff.objects.filter(material=instance).aggregate(s=Sum('quantity'))['s'] or 0
        if inc or woff:
            blockers.append('есть движения (приходы/списания)')
        if blockers:
            msg = 'Нельзя удалить сырьё: ' + '; '.join(blockers) + '.'
            return Response(
                {
                    'code': 'MATERIAL_IN_USE',
                    'error': msg,
                    'detail': msg,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class IncomingViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Incoming.objects.select_related('material').all()
    serializer_class = IncomingSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    activity_section = 'Материалы'
    activity_label = 'приход материала'
    filterset_class = IncomingFilter
    # Без material__name: search не подмешивает строки других сырьёв с похожими именами.
    search_fields = ['supplier', 'comment', 'batch']
    ordering_fields = ['id', 'date']
