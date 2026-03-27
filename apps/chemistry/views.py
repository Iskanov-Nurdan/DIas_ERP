import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Prefetch, Sum, F

from apps.activity.mixins import ActivityLoggingMixin
from apps.activity.audit_service import instance_to_snapshot, schedule_entity_audit
from config.permissions import IsAdminOrHasAccess
from .models import ChemistryCatalog, ChemistryComposition, ChemistryTask, ChemistryStock
from .serializers import (
    ChemistryCatalogSerializer, ChemistryTaskSerializer,
    ChemistryStockSerializer, ChemistryBalanceSerializer,
)
from apps.materials.models import Incoming, MaterialWriteoff

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


class ChemistryCatalogViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ChemistryCatalog.objects.prefetch_related(
        Prefetch(
            'compositions',
            queryset=ChemistryComposition.objects.select_related('raw_material'),
        )
    ).all()
    serializer_class = ChemistryCatalogSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    activity_section = 'Химия'
    activity_label = 'хим. элемент'
    filterset_fields = ['unit']
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def perform_create(self, serializer):
        catalog = serializer.save()
        ChemistryStock.objects.get_or_create(
            chemistry=catalog,
            defaults={'quantity': 0, 'unit': catalog.unit},
        )


class ChemistryTaskViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ChemistryTask.objects.select_related('chemistry').prefetch_related('elements').all()
    serializer_class = ChemistryTaskSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    activity_section = 'Химия'
    activity_label = 'задание'
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
        Подтверждение выполнения задания:
        1. Проверка остатков сырья по составу хим. элемента.
        2. Списание сырья (MaterialWriteoff).
        3. Начисление quantity хим. элемента в ChemistryStock.
        4. Перевод задания в статус «done».
        """
        task = self.get_object()
        if task.status == 'done':
            logger.warning('confirm task id=%s: already done', task.pk)
            return _err('bad_request', 'Задание уже выполнено')

        composition = ChemistryComposition.objects.filter(
            chemistry=task.chemistry
        ).select_related('raw_material')

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
            logger.warning('confirm task id=%s: insufficient stock missing=%s', task.pk, missing)
            return _err(
                'bad_request',
                'Недостаточно остатков сырья',
                errors=[
                    {'field': m['component'],
                     'message': f"Требуется {m['required']} {m['unit']}, доступно {m['available']}"}
                    for m in missing
                ],
            )

        before = instance_to_snapshot(task)
        with transaction.atomic():
            for comp in composition:
                required = comp.quantity_per_unit * task.quantity
                MaterialWriteoff.objects.create(
                    material=comp.raw_material,
                    quantity=required,
                    unit=comp.raw_material.unit,
                    reason='chemistry_task',
                    reference_id=task.id,
                )
            stock, _ = ChemistryStock.objects.get_or_create(
                chemistry=task.chemistry,
                defaults={'quantity': 0, 'unit': task.chemistry.unit},
            )
            ChemistryStock.objects.filter(pk=stock.pk).update(
                quantity=F('quantity') + task.quantity,
                last_task_id=task.id,
            )
            task.status = 'done'
            task.save(update_fields=['status'])

        task.refresh_from_db()
        after = instance_to_snapshot(task)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='Химия',
            description=f'Подтверждение задания #{task.pk} (выполнено)',
            action='update',
            model_cls=ChemistryTask,
            before=before,
            after=after,
            after_instance=task,
            payload_extra={'endpoint': 'POST /api/chemistry/tasks/{id}/confirm/'},
        )
        return Response(ChemistryTaskSerializer(task).data)


class ChemistryStockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/chemistry/balances/ — остатки хим. элементов (только quantity > 0).
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
            qs = qs.filter(quantity__gt=0)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ChemistryBalanceSerializer
        return ChemistryStockSerializer
