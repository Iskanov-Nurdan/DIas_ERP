import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Sum, Value
from django.db.models.fields import DecimalField
from django.db.models.functions import Coalesce
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from apps.activity.audit_service import instance_to_snapshot, schedule_entity_audit
from apps.materials.serializers import kg_to_display_unit, normalize_material_unit
from apps.production.models import RecipeRunBatchComponent
from apps.recipes.models import RecipeComponent
from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess
from .catalog_policy import chemistry_catalog_deletable, chemistry_unit_change_denied
from .fifo import chemistry_stock_kg
from .models import ChemistryCatalog, ChemistryRecipe, ChemistryTask, ChemistryBatch
from .produce import produce_chemistry
from .serializers import (
    ChemistryCatalogSerializer,
    ChemistryCatalogListSerializer,
    ChemistryTaskSerializer,
    ChemistryBatchSerializer,
    ChemistryProduceSerializer,
)

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


def _produce_error_status(detail) -> int:
    if isinstance(detail, dict):
        code = detail.get('code')
        if code in ('INSUFFICIENT_STOCK', 'EMPTY_CHEMISTRY_RECIPE'):
            return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


class ChemistryCatalogViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = ChemistryCatalog.objects.all()
    serializer_class = ChemistryCatalogSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    activity_section = 'Химия'
    activity_label = 'хим. элемент'
    filterset_fields = ['unit', 'is_active']
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def get_serializer_class(self):
        if self.action == 'list':
            return ChemistryCatalogListSerializer
        return ChemistryCatalogSerializer

    def get_queryset(self):
        recipe_ref = Exists(
            RecipeComponent.objects.filter(chemistry_id=OuterRef('pk'))
        )
        run_ref = Exists(
            RecipeRunBatchComponent.objects.filter(chemistry_id=OuterRef('pk'))
        )
        qs = (
            ChemistryCatalog.objects.prefetch_related(
                Prefetch(
                    'recipe_lines',
                    queryset=ChemistryRecipe.objects.select_related('raw_material'),
                )
            )
            .annotate(
                batches_count=Count('batches', distinct=True),
                balance=Coalesce(
                    Sum('batches__quantity_remaining'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=14, decimal_places=4),
                ),
                _has_recipe_ref=recipe_ref,
                _has_run_ref=run_ref,
            )
        )
        return qs

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if 'unit' in request.data:
            new_u = request.data.get('unit')
            if normalize_material_unit(new_u) != normalize_material_unit(instance.unit):
                denied, msg = chemistry_unit_change_denied(instance)
                if denied:
                    return Response(
                        {'detail': msg, 'error': msg},
                        status=status.HTTP_409_CONFLICT,
                    )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not chemistry_catalog_deletable(instance):
            msg = 'Нельзя удалить: есть партии выпуска, ссылки из рецептов или факт расхода в производстве.'
            return Response(
                {
                    'code': 'CHEMISTRY_IN_USE',
                    'detail': msg,
                    'error': msg,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['post'], url_path='produce')
    def produce(self, request):
        """Произвести химию: списать сырьё FIFO, создать ChemistryBatch."""
        ser = ChemistryProduceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cid = ser.validated_data['chemistry_id']
        qty = ser.validated_data['quantity']
        comment = ser.validated_data.get('comment') or ''
        try:
            batch = produce_chemistry(
                chemistry_id=cid,
                quantity=qty,
                user=request.user,
                comment=comment,
            )
        except DRFValidationError as e:
            return Response(e.detail, status=_produce_error_status(e.detail))
        return Response(ChemistryBatchSerializer(batch).data, status=status.HTTP_201_CREATED)


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
        Выпуск химии по заданию: то же, что produce, с привязкой к заданию.
        """
        task = self.get_object()
        if task.status == 'done':
            logger.warning('confirm task id=%s: already done', task.pk)
            return _err('bad_request', 'Задание уже выполнено')

        before = instance_to_snapshot(task)
        try:
            batch = produce_chemistry(
                chemistry_id=task.chemistry_id,
                quantity=task.quantity,
                user=request.user,
                comment=f'Задание #{task.pk}',
                source_task_id=task.pk,
            )
        except DRFValidationError as e:
            return Response(e.detail, status=_produce_error_status(e.detail))

        with transaction.atomic():
            task.status = 'done'
            task.save(update_fields=['status'])

        task.refresh_from_db()
        after = instance_to_snapshot(task)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='Химия',
            description=f'Подтверждение задания #{task.pk} (выпущена партия #{batch.pk})',
            action='update',
            model_cls=ChemistryTask,
            before=before,
            after=after,
            after_instance=task,
            payload_extra={'endpoint': 'POST /api/chemistry/tasks/{id}/confirm/', 'chemistry_batch_id': batch.pk},
        )
        return Response({
            'task': ChemistryTaskSerializer(task).data,
            'batch': ChemistryBatchSerializer(batch).data,
        })


class ChemistryBalancesView(viewsets.GenericViewSet):
    """GET /chemistry/balances/ — остатки в единицах карточки."""

    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    pagination_class = StandardResultsSetPagination
    queryset = ChemistryCatalog.objects.all()

    def list(self, request):
        rows = ChemistryCatalog.objects.all().order_by('name')
        items = []
        for r in rows:
            bal_kg = chemistry_stock_kg(r.pk)
            u = normalize_material_unit(r.unit)
            bal_disp = float(kg_to_display_unit(bal_kg, r.unit))
            min_b = r.min_balance
            min_disp = float(min_b) if min_b is not None else None
            items.append({
                'chemistry_id': r.id,
                'name': r.name,
                'balance': bal_disp,
                'min_balance': min_disp,
                'unit': u,
            })
        page = self.paginate_queryset(items)
        if page is not None:
            return self.get_paginated_response(page)
        return Response({'items': items})


class ChemistryBatchViewSet(ActivityLoggingMixin, viewsets.ReadOnlyModelViewSet):
    """История партий химии."""

    queryset = ChemistryBatch.objects.select_related('chemistry', 'produced_by', 'source_task').order_by(
        '-created_at', '-id'
    )
    serializer_class = ChemistryBatchSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'chemistry'
    activity_section = 'Химия'
    activity_label = 'партия химии'
    filterset_fields = ['chemistry']
    search_fields = ['comment']
    ordering_fields = ['id', 'created_at', 'cost_total']
