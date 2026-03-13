import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Sum, F

from config.permissions import IsAdminOrHasAccess

logger = logging.getLogger(__name__)
from .models import (
    ChemistryCatalog, ChemistryComposition, ChemistryTask,
    ChemistryStock,
)
from .serializers import (
    ChemistryCatalogSerializer, ChemistryTaskSerializer,
    ChemistryStockSerializer, ChemistryBalanceSerializer,
)
from apps.materials.models import Incoming, MaterialWriteoff


class ChemistryCatalogViewSet(viewsets.ModelViewSet):
    queryset = ChemistryCatalog.objects.prefetch_related('compositions').all()
    serializer_class = ChemistryCatalogSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    filterset_fields = ['unit']
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def perform_create(self, serializer):
        catalog = serializer.save()
        ChemistryStock.objects.get_or_create(chemistry=catalog, defaults={'quantity': 0, 'unit': catalog.unit})


class ChemistryTaskViewSet(viewsets.ModelViewSet):
    queryset = ChemistryTask.objects.select_related('chemistry').prefetch_related('elements').all()
    serializer_class = ChemistryTaskSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    filterset_fields = ['status', 'chemistry']
    ordering_fields = ['id', 'created_at', 'deadline']

    def perform_destroy(self, instance):
        if instance.status == 'done':
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Нельзя удалить выполненное задание'})
        instance.delete()

    @action(detail=True, methods=['post'], url_path='confirm')
    def confirm(self, request, pk=None):
        """
        Подтвердить выполнение задания (бизнес-логика):
        1. Проверить остатки сырья по составу хим. элемента.
        2. Списать сырьё (MaterialWriteoff).
        3. Добавить quantity хим. элемента в «Остатки» (ChemistryStock).
        4. Пометить задание как выполненное (status='done').
        До подтверждения задание в статусе «В работе» / «К выполнению», в остатки не попадает.
        После — элемент появляется в GET /api/chemistry/balances/.
        """
        task = self.get_object()
        if task.status == 'done':
            logger.warning('confirm task id=%s: ALREADY_DONE', task.pk)
            return Response({
                'error': 'Задание уже выполнено',
                'code': 'ALREADY_DONE',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        composition = ChemistryComposition.objects.filter(chemistry=task.chemistry).select_related('raw_material')
        missing = []
        for comp in composition:
            required = float(comp.quantity_per_unit * task.quantity)
            incoming_sum = Incoming.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
            writeoff_sum = MaterialWriteoff.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
            available = float(incoming_sum - writeoff_sum)
            if available < required:
                missing.append({
                    'component': comp.raw_material.name,
                    'required': required,
                    'available': available,
                    'unit': comp.raw_material.unit,
                })
        if missing:
            logger.warning('confirm task id=%s: INSUFFICIENT_STOCK missing=%s', task.pk, missing)
            return Response({
                'error': 'Недостаточно остатков сырья',
                'code': 'INSUFFICIENT_STOCK',
                'missing': missing,
            }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Списание сырья по составу
            for comp in composition:
                required = comp.quantity_per_unit * task.quantity
                MaterialWriteoff.objects.create(
                    material=comp.raw_material,
                    quantity=required,
                    unit=comp.raw_material.unit,
                    reason='chemistry_task',
                    reference_id=task.id,
                )
            # Добавить quantity хим. элемента в остатки (склад хим. элементов)
            stock, created = ChemistryStock.objects.get_or_create(
                chemistry=task.chemistry,
                defaults={'quantity': 0, 'unit': task.chemistry.unit},
            )
            ChemistryStock.objects.filter(pk=stock.pk).update(
                quantity=F('quantity') + task.quantity,
                last_task_id=task.id,
            )
            # Пометить задание как выполненное
            task.status = 'done'
            task.save(update_fields=['status'])

        return Response(ChemistryTaskSerializer(task).data)


class ChemistryStockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Остатки хим. элементов. GET /api/chemistry/balances/.
    Учитываются только подтверждённые задания: приход в остатки происходит
    только при POST .../chemistry/tasks/{id}/confirm/. До подтверждения задание
    в остатки не попадает. Для списка возвращаются только записи с balance > 0,
    формат: { "items": [ { "element_name", "unit", "balance" } ] }.
    """
    queryset = ChemistryStock.objects.select_related('chemistry', 'last_task').all()
    serializer_class = ChemistryStockSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    filterset_fields = ['chemistry']
    ordering_fields = ['chemistry__name', 'quantity']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'list':
            # В остатки показываем только элементы с balance > 0 (после подтверждения)
            qs = qs.filter(quantity__gt=0)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ChemistryBalanceSerializer
        return ChemistryStockSerializer
