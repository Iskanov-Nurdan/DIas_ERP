from rest_framework import viewsets
from rest_framework.response import Response
from django.db.models import Sum

from config.permissions import IsAdminOrHasAccess
from .models import RawMaterial, Incoming, MaterialWriteoff
from .serializers import RawMaterialSerializer, IncomingSerializer


class RawMaterialViewSet(viewsets.ModelViewSet):
    queryset = RawMaterial.objects.all()
    serializer_class = RawMaterialSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    filterset_fields = ['unit']
    search_fields = ['name']
    ordering_fields = ['id', 'name']


class IncomingViewSet(viewsets.ModelViewSet):
    queryset = Incoming.objects.select_related('material').all()
    serializer_class = IncomingSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    filterset_fields = ['material', 'date']
    ordering_fields = ['id', 'date']


class MaterialsBalancesView(viewsets.ViewSet):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'

    def list(self, request):
        # Остатки по материалам: сумма приходов минус списания.
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
            })
        return Response({'items': result})
