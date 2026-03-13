from decimal import Decimal
from datetime import date
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Sum, F

from config.permissions import IsAdminOrHasAccess
from .models import Line, LineHistory, Order, ProductionBatch
from .serializers import (
    LineSerializer, LineHistorySerializer, OrderSerializer,
    ProductionBatchSerializer, BatchListSerializer,
)
from apps.recipes.models import RecipeComponent
from apps.materials.models import Incoming, MaterialWriteoff
from apps.chemistry.models import ChemistryStock
from apps.warehouse.models import WarehouseBatch


class LineViewSet(viewsets.ModelViewSet):
    queryset = Line.objects.all()
    serializer_class = LineSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'lines'
    filterset_fields = []
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    @action(detail=True, methods=['post'], url_path='open')
    def open_shift(self, request, pk=None):
        line = self.get_object()
        from django.utils import timezone
        now = timezone.now()
        LineHistory.objects.create(line=line, action=LineHistory.ACTION_OPEN, date=now.date(), time=now.time(), user=request.user)
        return Response({'detail': 'Смена открыта', 'line': LineSerializer(line).data})

    @action(detail=True, methods=['post'], url_path='close')
    def close_shift(self, request, pk=None):
        line = self.get_object()
        from django.utils import timezone
        now = timezone.now()
        LineHistory.objects.create(line=line, action=LineHistory.ACTION_CLOSE, date=now.date(), time=now.time(), user=request.user)
        return Response({'detail': 'Смена закрыта', 'line': LineSerializer(line).data})

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        line = self.get_object()
        qs = LineHistory.objects.filter(line=line).select_related('user').order_by('-date', '-time')
        ser = LineHistorySerializer(qs, many=True)
        return Response({'items': ser.data})


def _line_shift_is_open(line):
    qs = LineHistory.objects.filter(line=line).order_by('-date', '-time')
    last = qs.first()
    return last and last.action == LineHistory.ACTION_OPEN


def _perform_release(order, quantity, operator):
    """
    Списывает материалы по рецепту заказа, создаёт партию (batch) с otk_status=pending.
    Рассчитывает себестоимость только на основе сырья (химия - это переработка сырья).
    Возвращает (batch, None) при успехе или (None, error_dict, status_code) при ошибке.
    """
    if order.status not in (Order.STATUS_CREATED, Order.STATUS_IN_PROGRESS):
        return None, {
            'error': 'Заказ не в статусе Создан/В работе',
            'code': 'INVALID_STATUS',
            'details': {},
        }, status.HTTP_400_BAD_REQUEST

    if not _line_shift_is_open(order.line):
        return None, {
            'error': 'Смена на линии не открыта',
            'code': 'SHIFT_CLOSED',
            'lineName': order.line.name,
            'details': {},
        }, status.HTTP_400_BAD_REQUEST

    recipe = order.recipe
    missing = []
    total_cost = Decimal('0')
    
    # Проверка остатков и расчет себестоимости
    for comp in recipe.components.select_related('raw_material', 'chemistry').all():
        required = comp.quantity * quantity
        if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
            inc = Incoming.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
            woff = MaterialWriteoff.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
            available = float(inc - woff)
            if available < float(required):
                missing.append({
                    'component': comp.raw_material.name,
                    'required': float(required),
                    'available': available,
                    'unit': comp.raw_material.unit,
                })
            else:
                # Расчет средней цены сырья
                total_qty = Incoming.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 1
                total_price = Incoming.objects.filter(material=comp.raw_material).aggregate(
                    s=Sum(F('quantity') * F('price_per_unit'))
                )['s'] or 0
                avg_price = Decimal(str(total_price)) / Decimal(str(total_qty))
                total_cost += avg_price * required
                    
        elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
            stock = ChemistryStock.objects.filter(chemistry=comp.chemistry).first()
            available = float(stock.quantity or 0) if stock else 0
            if available < float(required):
                missing.append({
                    'component': comp.chemistry.name,
                    'required': float(required),
                    'available': available,
                    'unit': comp.unit,
                })
            # Химия - это переработка сырья, не добавляем к себестоимости напрямую
                    
    if missing:
        return None, {
            'error': 'Недостаточно остатков',
            'code': 'INSUFFICIENT_STOCK',
            'details': {'missing': missing},
        }, status.HTTP_400_BAD_REQUEST

    with transaction.atomic():
        for comp in recipe.components.select_related('raw_material', 'chemistry').all():
            required = comp.quantity * quantity
            if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
                MaterialWriteoff.objects.create(
                    material=comp.raw_material,
                    quantity=required,
                    unit=comp.raw_material.unit,
                    reason='production_batch',
                    reference_id=order.id,
                )
            elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
                # Списываем химию из остатков
                stock = ChemistryStock.objects.get(chemistry=comp.chemistry)
                stock.quantity = (stock.quantity or 0) - required
                stock.save(update_fields=['quantity', 'updated_at'])

        batch = ProductionBatch.objects.create(
            order=order,
            product=order.product,
            quantity=quantity,
            operator=operator or order.operator,
            date=date.today(),
            otk_status=ProductionBatch.OTK_PENDING,
            cost_price=total_cost,
        )
        if order.status == Order.STATUS_CREATED:
            order.status = Order.STATUS_IN_PROGRESS
            order.save(update_fields=['status'])

    return batch, None, None


class OrderViewSet(viewsets.ModelViewSet):
    """
    GET /api/orders/ — query: page, page_size, status (created | in_progress | done).
    POST /api/orders/ — body: recipe_id, line_id, quantity (product и date подставятся из рецепта и сегодня).
    PATCH — то же тело (recipe_id, line_id, quantity).
    """
    queryset = Order.objects.select_related('recipe', 'line', 'operator').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'orders'
    filterset_fields = ['status', 'recipe', 'line']
    ordering_fields = ['id', 'date', 'status']

    def get_serializer(self, *args, **kwargs):
        """Принимаем recipe_id/line_id (фронт) и приводим к recipe/line для сериализатора."""
        if kwargs.get('data') is not None:
            data = dict(kwargs['data'])
            if 'recipe_id' in data and 'recipe' not in data:
                data['recipe'] = data.pop('recipe_id', None)
            if 'line_id' in data and 'line' not in data:
                data['line'] = data.pop('line_id', None)
            kwargs = dict(kwargs)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)

    def perform_create(self, serializer):
        recipe = serializer.validated_data.get('recipe')
        if recipe:
            for comp in recipe.components.filter(type=RecipeComponent.TYPE_CHEM):
                if comp.chemistry_id:
                    stock = ChemistryStock.objects.filter(chemistry=comp.chemistry).first()
                    if not stock or (stock.quantity or 0) <= 0:
                        from rest_framework.exceptions import ValidationError
                        raise ValidationError({'detail': 'Рецепт содержит неподтверждённый хим. элемент или с нулевым остатком.'})
        # product и date по умолчанию: из рецепта и сегодня
        from django.utils import timezone
        product = serializer.validated_data.get('product') or (recipe and recipe.product) or ''
        date = serializer.validated_data.get('date') or timezone.now().date()
        serializer.save(product=product, date=date)

    def perform_update(self, serializer):
        instance = serializer.instance
        recipe = serializer.validated_data.get('recipe') or instance.recipe
        product = serializer.validated_data.get('product')
        if product is None and recipe:
            serializer.save(product=recipe.product)
        else:
            serializer.save()

    def perform_destroy(self, instance):
        if instance.status == Order.STATUS_IN_PROGRESS:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'Нельзя удалить заказ в производстве'})
        instance.delete()

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        """
        POST /api/orders/{id}/release/
        Тело: { "quantity": 12 } — количество выпускаемой продукции (шт).
        Списывает материалы, создаёт партию (batch) с otk_status=pending для ОТК.
        """
        order = self.get_object()
        quantity = request.data.get('quantity')
        if quantity is None:
            return Response({
                'error': 'Укажите quantity (количество выпускаемой продукции, шт)',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = Decimal(str(quantity))
        except Exception:
            return Response({
                'error': 'Некорректное quantity',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        if quantity <= 0:
            return Response({
                'error': 'quantity должно быть больше 0',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        if quantity > order.quantity:
            return Response({
                'error': f'quantity не может быть больше заказа ({order.quantity})',
                'code': 'VALIDATION_ERROR',
                'details': {'order_quantity': float(order.quantity)},
            }, status=status.HTTP_400_BAD_REQUEST)

        batch, err_dict, err_status = _perform_release(order, quantity, getattr(request, 'user', None))
        if batch is None:
            return Response(err_dict, status=err_status)
        return Response({
            'batch': ProductionBatchSerializer(batch).data,
            'order_id': order.id,
            'quantity': float(quantity),
        }, status=status.HTTP_200_OK)


class ProductionOrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Заказы для выпуска (статус Создан/В работе)."""
    queryset = Order.objects.filter(status__in=[Order.STATUS_CREATED, Order.STATUS_IN_PROGRESS]).select_related('recipe', 'line')
    serializer_class = OrderSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'production'
    filterset_fields = ['line']
    ordering_fields = ['id', 'date']


class ProductionBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProductionBatch.objects.select_related('order', 'operator').all()
    serializer_class = ProductionBatchSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'production'
    filterset_fields = ['order', 'otk_status']
    ordering_fields = ['id', 'date']


class BatchViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/batches/ — список партий с пагинацией (page, page_size).
    Поля: id, order_name, product_name, quantity, released, operator_name, date, created_at,
    otk_status, otk_accepted, otk_defect, otk_defect_reason, otk_comment, otk_inspector, otk_checked_at.
    POST /api/batches/{id}/otk_accept/ — сохранение результата ОТК.
    """
    queryset = ProductionBatch.objects.select_related(
        'order', 'operator'
    ).prefetch_related('otk_checks__inspector').all()
    serializer_class = BatchListSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'otk'
    filterset_fields = ['otk_status', 'order']
    ordering_fields = ['id', 'date']

    def get_serializer_class(self):
        if self.action == 'list' or self.action == 'retrieve':
            return BatchListSerializer
        return ProductionBatchSerializer

    @action(detail=True, methods=['post'], url_path='otk_accept')
    def otk_accept(self, request, pk=None):
        """Сохранение результата ОТК. Тело: otk_accepted, otk_defect, otk_defect_reason, otk_comment, otk_status, otk_inspector, otk_checked_at."""
        from apps.otk.models import OtkCheck
        from django.contrib.auth import get_user_model
        User = get_user_model()

        batch = self.get_object()
        if batch.otk_status != ProductionBatch.OTK_PENDING:
            return Response({
                'error': 'Партия уже проверена',
                'code': 'ALREADY_CHECKED',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        # Новый контракт (фронт)
        accepted = request.data.get('otk_accepted') or request.data.get('accepted')
        rejected = request.data.get('otk_defect') or request.data.get('rejected')
        defect_reason = request.data.get('otk_defect_reason') or request.data.get('rejectReason', '')
        comment = request.data.get('otk_comment') or ''
        inspector_name = request.data.get('otk_inspector')
        checked_at = request.data.get('otk_checked_at')

        if accepted is None or rejected is None:
            return Response({
                'error': 'Укажите otk_accepted и otk_defect (или accepted, rejected)',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            accepted = Decimal(str(accepted))
            rejected = Decimal(str(rejected))
        except Exception:
            return Response({
                'error': 'Некорректные otk_accepted/otk_defect',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        if accepted + rejected != batch.quantity:
            return Response({
                'error': 'Принято + Брак должно равняться выпущенному количеству',
                'code': 'VALIDATION_ERROR',
                'details': {'batch_quantity': float(batch.quantity)},
            }, status=status.HTTP_400_BAD_REQUEST)

        if rejected > 0 and not defect_reason.strip():
            return Response({
                'error': 'Причина брака обязательна при наличии брака',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        inspector = None
        if inspector_name and isinstance(inspector_name, str):
            inspector = User.objects.filter(name=inspector_name).first()
        if inspector is None and request.data.get('otk_inspector_id'):
            inspector = User.objects.filter(pk=request.data.get('otk_inspector_id')).first()
        if inspector is None:
            inspector = request.user

        if checked_at and isinstance(checked_at, str):
            from django.utils.dateparse import parse_datetime
            try:
                parsed = parse_datetime(checked_at)
                if parsed:
                    from django.utils import timezone
                    if timezone.is_naive(parsed):
                        parsed = timezone.make_aware(parsed)
                    checked_at = parsed
                else:
                    checked_at = None
            except Exception:
                checked_at = None
        if checked_at is None:
            from django.utils import timezone
            checked_at = timezone.now()

        with transaction.atomic():
            check = OtkCheck.objects.create(
                batch=batch,
                accepted=accepted,
                rejected=rejected,
                reject_reason=defect_reason,
                comment=comment,
                inspector=inspector,
            )
            OtkCheck.objects.filter(pk=check.pk).update(checked_date=checked_at)
            batch.otk_status = ProductionBatch.OTK_ACCEPTED if rejected == 0 else ProductionBatch.OTK_REJECTED
            if rejected > 0 and accepted > 0:
                batch.otk_status = ProductionBatch.OTK_ACCEPTED
            batch.save(update_fields=['otk_status'])
            if accepted > 0:
                WarehouseBatch.objects.create(
                    product=batch.product,
                    quantity=accepted,
                    status=WarehouseBatch.STATUS_AVAILABLE,
                    date=date.today(),
                    source_batch=batch,
                )
            Order.objects.filter(pk=batch.order_id).update(status=Order.STATUS_DONE)

        batch = ProductionBatch.objects.select_related(
            'order', 'operator'
        ).prefetch_related('otk_checks__inspector').get(pk=batch.pk)
        return Response(BatchListSerializer(batch).data)


class ProductionReleaseView(viewsets.ViewSet):
    """POST /api/production/release/ — тело: { orderId, quantity }. Альтернатива: POST /api/orders/{id}/release/ с телом { quantity }."""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'production'

    def create(self, request):
        order_id = request.data.get('orderId')
        quantity = request.data.get('quantity')
        if not order_id or quantity is None:
            return Response({
                'error': 'Укажите orderId и quantity',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = Decimal(str(quantity))
        except Exception:
            return Response({
                'error': 'Некорректное quantity',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)

        order = Order.objects.select_related('recipe', 'line').filter(pk=order_id).first()
        if not order:
            return Response({
                'error': 'Заказ не найден',
                'code': 'NOT_FOUND',
                'details': {},
            }, status=status.HTTP_404_NOT_FOUND)

        batch, err_dict, err_status = _perform_release(order, quantity, getattr(request, 'user', None))
        if batch is None:
            return Response(err_dict, status=err_status)
        return Response(ProductionBatchSerializer(batch).data, status=status.HTTP_201_CREATED)
