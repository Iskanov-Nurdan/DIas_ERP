from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.activity.audit_service import schedule_entity_audit
from apps.activity.mixins import ActivityLoggingMixin
from apps.realtime.broadcast import schedule_push
from config.pagination import StandardResultsSetPagination
from config.permissions import IsAdminOrHasAccess

from . import services
from .models import FoamDensityGrade, FoamGpOperation, FoamGpStock, FoamProductionRun, FoamRawLot, FoamSale
from .serializers import (
    FoamDensityGradeSerializer,
    FoamGpOperationSerializer,
    FoamGpStockCutSerializer,
    FoamGpStockSerializer,
    FoamProductionRunCreateSerializer,
    FoamProductionRunReadSerializer,
    FoamRawLotSerializer,
    FoamSaleCreateSerializer,
    FoamSaleReadSerializer,
)

ACTIVITY_SECTION = 'Пенопласт'


def _err(code: str, message: str, http_status: int = 400, extra: dict | None = None) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if extra:
        payload.update(extra)
    return Response(payload, status=http_status)


class FoamRawLotViewSet(
    ActivityLoggingMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = FoamRawLot.objects.all()
    serializer_class = FoamRawLotSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    pagination_class = StandardResultsSetPagination
    search_fields = ['material_name', 'supplier', 'lot_number']
    ordering_fields = ['id', 'received_at']
    activity_section = ACTIVITY_SECTION
    activity_label = 'лот сырья (пенопласт)'

    def perform_create(self, serializer):
        super().perform_create(serializer)
        lot = serializer.instance
        schedule_push(resource='foam_raw_lot', action='created', entity_id=lot.pk)


class FoamDensityGradeViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = FoamDensityGrade.objects.all()
    serializer_class = FoamDensityGradeSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'materials'
    pagination_class = None

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        return Response({'items': self.get_serializer(qs, many=True).data})

    def create(self, request, *args, **kwargs):
        code = str(request.data.get('code') or '').strip()
        if code and FoamDensityGrade.objects.filter(code=code).exists():
            return _err('DENSITY_GRADE_EXISTS', f'Плотность с кодом "{code}" уже существует', status.HTTP_409_CONFLICT)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grade = serializer.save()
        schedule_entity_audit(
            user=request.user,
            request=request,
            section=ACTIVITY_SECTION,
            description=f'Добавлена плотность {grade.code}',
            action='create',
            model_cls=FoamDensityGrade,
            after_instance=grade,
            payload_extra={'endpoint': 'POST /api/foam/density-grades/'},
        )
        schedule_push(resource='foam_density_grade', action='created')
        return Response(self.get_serializer(grade).data, status=status.HTTP_201_CREATED)


class FoamProductionRunViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = FoamProductionRun.objects.select_related('lot', 'grade', 'operator')
    serializer_class = FoamProductionRunReadSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'production'
    pagination_class = StandardResultsSetPagination
    ordering_fields = ['id', 'produced_at']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        lot_id = params.get('lot_id')
        if lot_id:
            qs = qs.filter(lot_id=lot_id)
        date_from = params.get('date_from')
        date_to = params.get('date_to')
        if date_from:
            qs = qs.filter(produced_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(produced_at__date__lte=date_to)
        return qs

    def create(self, request, *args, **kwargs):
        ser = FoamProductionRunCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            run = services.run_production(
                lot_id=data['lot_id'],
                input_kg=data['input_kg'],
                output_format=data['output_format'],
                grade_code=data.get('grade_code'),
                user=request.user,
            )
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section=ACTIVITY_SECTION,
            description=f'Выпуск производства #{run.pk}: {run.output_qty} ({run.output_format})',
            action='create',
            model_cls=FoamProductionRun,
            after_instance=run,
            payload_extra={'endpoint': 'POST /api/foam/production-runs/'},
        )
        return Response(FoamProductionRunReadSerializer(run).data, status=status.HTTP_201_CREATED)


class FoamGpStockViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = FoamGpStock.objects.select_related('grade').filter(qty__gt=0)
    serializer_class = FoamGpStockSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'
    pagination_class = None

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        return Response({'items': self.get_serializer(qs, many=True).data})

    @action(detail=False, methods=['post'], url_path='cut')
    def cut(self, request):
        ser = FoamGpStockCutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            cube_stock, sheet_stock = services.cut_cube(
                cube_stock_id=data['cube_stock_id'],
                thickness_cm=data['thickness_cm'],
                cubes_qty=data['cubes_qty'],
            )
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section=ACTIVITY_SECTION,
            description=f'Нарезка куба #{cube_stock.pk} на листы {sheet_stock.thickness_cm} см',
            action='update',
            model_cls=FoamGpStock,
            after_instance=cube_stock,
            payload_extra={'endpoint': 'POST /api/foam/gp-stock/cut/'},
        )
        return Response(
            {
                'cube_stock': FoamGpStockSerializer(cube_stock).data,
                'sheet_stock': FoamGpStockSerializer(sheet_stock).data,
            },
            status=status.HTTP_200_OK,
        )


class FoamGpOperationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = FoamGpOperation.objects.select_related('grade')
    serializer_class = FoamGpOperationSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'
    pagination_class = StandardResultsSetPagination
    ordering_fields = ['id', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        date_from = params.get('date_from')
        date_to = params.get('date_to')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        kind = params.get('kind')
        if kind:
            qs = qs.filter(kind__in=[k.strip() for k in kind.split(',') if k.strip()])
        output_format = params.get('output_format')
        if output_format:
            qs = qs.filter(output_format=output_format)
        return qs


class FoamSaleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = FoamSale.objects.prefetch_related('lines__stock__grade')
    serializer_class = FoamSaleReadSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    pagination_class = StandardResultsSetPagination
    search_fields = ['client']
    ordering_fields = ['id', 'sale_date']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        client = params.get('client')
        if client:
            qs = qs.filter(client__icontains=client)
        payment_status = params.get('payment_status')
        if payment_status:
            qs = qs.filter(payment_status=payment_status)
        date_from = params.get('date_from')
        date_to = params.get('date_to')
        if date_from:
            qs = qs.filter(sale_date__gte=date_from)
        if date_to:
            qs = qs.filter(sale_date__lte=date_to)
        return qs

    def create(self, request, *args, **kwargs):
        ser = FoamSaleCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            sale = services.create_sale(
                client=data['client'],
                sale_date=data['sale_date'],
                lines_data=data['lines'],
                paid_amount=data['paid_amount'],
            )
        except DRFValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section=ACTIVITY_SECTION,
            description=f'Продажа #{sale.pk}: {sale.client} на {sale.total_amount}',
            action='create',
            model_cls=FoamSale,
            after_instance=sale,
            payload_extra={'endpoint': 'POST /api/foam/sales/'},
        )
        return Response(FoamSaleReadSerializer(sale).data, status=status.HTTP_201_CREATED)
