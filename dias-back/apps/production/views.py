import logging
from decimal import Decimal
from datetime import date

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Sum, F

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from config.pagination import StandardResultsSetPagination
from .models import Line, LineHistory, Order, ProductionBatch, Shift
from .serializers import (
    LineSerializer, LineHistorySerializer, OrderSerializer,
    ProductionBatchSerializer, BatchListSerializer,
    ShiftSerializer, ShiftDetailSerializer, ShiftNoteSerializer,
)
from apps.recipes.models import RecipeComponent
from apps.materials.models import Incoming, MaterialWriteoff
from apps.chemistry.models import ChemistryStock
from apps.warehouse.models import WarehouseBatch

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'error': {'code': code, 'message': message}}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


class LineViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Line.objects.all()
    serializer_class = LineSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'lines'
    activity_section = 'Линии'
    activity_label = 'линия'
    filterset_fields = []
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    @action(detail=True, methods=['post'], url_path='open')
    def open_shift(self, request, pk=None):
        line = self.get_object()
        from django.utils import timezone
        now = timezone.now()
        LineHistory.objects.create(
            line=line, action=LineHistory.ACTION_OPEN,
            date=now.date(), time=now.time(), user=request.user,
        )
        Shift.objects.create(line=line, user=request.user, opened_at=now)
        return Response({'detail': 'Смена открыта', 'line': LineSerializer(line).data})

    @action(detail=True, methods=['post'], url_path='close')
    def close_shift(self, request, pk=None):
        line = self.get_object()
        from django.utils import timezone
        now = timezone.now()
        LineHistory.objects.create(
            line=line, action=LineHistory.ACTION_CLOSE,
            date=now.date(), time=now.time(), user=request.user,
        )
        # Закрываем последнюю открытую смену этого пользователя на этой линии
        open_shift = (
            Shift.objects.filter(line=line, user=request.user, closed_at__isnull=True)
            .order_by('-opened_at')
            .first()
        )
        if open_shift:
            comment = request.data.get('comment', '')
            open_shift.closed_at = now
            open_shift.comment = comment
            open_shift.save(update_fields=['closed_at', 'comment'])
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
    Рассчитывает себестоимость только на основе сырья (химия — переработка сырья).
    Возвращает (batch, None, None) при успехе или (None, Response, status_code) при ошибке.
    """
    if order.status not in (Order.STATUS_CREATED, Order.STATUS_IN_PROGRESS):
        return None, _err('bad_request', 'Заказ не в статусе Создан/В работе', http_status=400), None

    if not _line_shift_is_open(order.line):
        return None, _err('bad_request', f'Смена на линии «{order.line.name}» не открыта', http_status=400), None

    recipe = order.recipe
    missing = []
    total_cost = Decimal('0')

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

    if missing:
        return None, _err(
            'bad_request',
            'Недостаточно остатков для выпуска',
            errors=[{'field': m['component'], 'message': f"Требуется {m['required']} {m['unit']}, доступно {m['available']}"} for m in missing],
            http_status=400,
        ), None

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
                stock = ChemistryStock.objects.select_for_update().get(chemistry=comp.chemistry)
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


class OrderViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    GET /api/orders/ — query: page, page_size, status (created | in_progress | done).
    POST /api/orders/ — body: recipe_id, line_id, quantity (product и date подставятся из рецепта и сегодня).
    PATCH — то же тело.
    """
    queryset = Order.objects.select_related('recipe', 'line', 'operator').all()
    serializer_class = OrderSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'orders'
    activity_section = 'Заказы'
    activity_label = 'заказ'
    filterset_fields = ['status', 'recipe', 'line']
    ordering_fields = ['id', 'date', 'status']

    def get_serializer(self, *args, **kwargs):
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
                        raise ValidationError({'detail': 'Рецепт содержит хим. элемент с нулевым остатком.'})
        from django.utils import timezone
        product = serializer.validated_data.get('product') or (recipe and recipe.product) or ''
        order_date = serializer.validated_data.get('date') or timezone.now().date()
        serializer.save(product=product, date=order_date)

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
        Тело: { "quantity": 12 } — количество выпускаемой продукции.
        Списывает материалы, создаёт партию (batch) с otk_status=pending.
        """
        order = self.get_object()
        quantity_raw = request.data.get('quantity')

        if quantity_raw is None:
            return _err('validation_error', 'Укажите quantity',
                        errors=[{'field': 'quantity', 'message': 'Обязательное поле'}])
        try:
            quantity = Decimal(str(quantity_raw))
        except Exception:
            return _err('validation_error', 'Некорректное значение quantity',
                        errors=[{'field': 'quantity', 'message': 'Должно быть числом'}])

        if quantity <= 0:
            return _err('validation_error', 'quantity должно быть больше 0',
                        errors=[{'field': 'quantity', 'message': 'Должно быть больше 0'}])
        if quantity > order.quantity:
            return _err('validation_error',
                        f'quantity не может превышать объём заказа ({order.quantity})',
                        errors=[{'field': 'quantity', 'message': f'Максимум: {order.quantity}'}])

        batch, err_response, _ = _perform_release(order, quantity, getattr(request, 'user', None))
        if batch is None:
            return err_response
        return Response({
            'batch': ProductionBatchSerializer(batch).data,
            'order_id': order.id,
            'quantity': float(quantity),
        })


class ProductionOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Order.objects.filter(
        status__in=[Order.STATUS_CREATED, Order.STATUS_IN_PROGRESS]
    ).select_related('recipe', 'line')
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
    GET /api/batches/ — список партий с пагинацией.
    POST /api/batches/{id}/otk_accept/ — результат ОТК.
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
        if self.action in ('list', 'retrieve'):
            return BatchListSerializer
        return ProductionBatchSerializer

    @action(detail=True, methods=['post'], url_path='otk_accept')
    def otk_accept(self, request, pk=None):
        from apps.otk.models import OtkCheck
        from django.contrib.auth import get_user_model
        UserModel = get_user_model()

        batch = self.get_object()
        if batch.otk_status != ProductionBatch.OTK_PENDING:
            return _err('bad_request', 'Партия уже прошла ОТК-проверку')

        accepted_raw = request.data.get('otk_accepted') or request.data.get('accepted')
        rejected_raw = request.data.get('otk_defect') or request.data.get('rejected')
        defect_reason = request.data.get('otk_defect_reason') or request.data.get('rejectReason', '')
        comment = request.data.get('otk_comment') or ''
        inspector_name = request.data.get('otk_inspector')
        checked_at = request.data.get('otk_checked_at')

        errors = []
        if accepted_raw is None:
            errors.append({'field': 'otk_accepted', 'message': 'Обязательное поле'})
        if rejected_raw is None:
            errors.append({'field': 'otk_defect', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите otk_accepted и otk_defect', errors=errors)

        try:
            accepted = Decimal(str(accepted_raw))
            rejected = Decimal(str(rejected_raw))
        except Exception:
            return _err('validation_error', 'Некорректные значения otk_accepted/otk_defect',
                        errors=[{'field': 'otk_accepted', 'message': 'Должно быть числом'}])

        if accepted + rejected != batch.quantity:
            return _err('validation_error',
                        f'Принято + Брак должно равняться выпущенному количеству ({batch.quantity})',
                        errors=[{'field': 'otk_accepted', 'message': f'Сумма должна быть {batch.quantity}'}])

        if rejected > 0 and not str(defect_reason).strip():
            return _err('validation_error', 'Причина брака обязательна при наличии брака',
                        errors=[{'field': 'otk_defect_reason', 'message': 'Обязательное поле при браке'}])

        inspector = None
        if inspector_name and isinstance(inspector_name, str):
            inspector = UserModel.objects.filter(name=inspector_name).first()
        if inspector is None and request.data.get('otk_inspector_id'):
            inspector = UserModel.objects.filter(pk=request.data.get('otk_inspector_id')).first()
        if inspector is None:
            inspector = request.user

        if checked_at and isinstance(checked_at, str):
            from django.utils.dateparse import parse_datetime
            from django.utils import timezone as tz
            try:
                parsed = parse_datetime(checked_at)
                if parsed:
                    checked_at = tz.make_aware(parsed) if tz.is_naive(parsed) else parsed
                else:
                    checked_at = None
            except Exception:
                checked_at = None
        if checked_at is None:
            from django.utils import timezone as tz
            checked_at = tz.now()

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
            batch.otk_status = (
                ProductionBatch.OTK_REJECTED if rejected > 0 and accepted == 0
                else ProductionBatch.OTK_ACCEPTED
            )
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


class ShiftViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET  /api/shifts/         — список всех смен (фильтры: date_from, date_to, line, user).
    POST /api/shifts/open/    — открыть смену (body: line_id).
    POST /api/shifts/close/   — закрыть смену (body: line_id, comment?).
    """
    serializer_class = ShiftSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'my_shift'
    ordering_fields = ['id', 'opened_at']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ShiftDetailSerializer
        return ShiftSerializer

    def get_queryset(self):
        from django.db.models import Count, Q
        from datetime import datetime
        qs = Shift.objects.select_related('line', 'user').prefetch_related('notes').annotate(
            notes_count=Count('notes')
        ).order_by('-opened_at')

        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        line_id = self.request.query_params.get('line')
        user_id = self.request.query_params.get('user')

        if date_from:
            try:
                df = datetime.strptime(date_from, '%Y-%m-%d').date()
                qs = qs.filter(Q(closed_at__isnull=True) | Q(closed_at__date__gte=df))
            except ValueError:
                pass

        if date_to:
            try:
                dt = datetime.strptime(date_to, '%Y-%m-%d').date()
                qs = qs.filter(opened_at__date__lte=dt)
            except ValueError:
                pass

        if line_id:
            qs = qs.filter(line_id=line_id)
        if user_id:
            qs = qs.filter(user_id=user_id)

        return qs

    @action(detail=False, methods=['post'], url_path='open')
    def open(self, request):
        """
        POST /api/shifts/open/
        Тело: { "line_id": <int> } — необязательно.
        Открывает смену для текущего пользователя. line_id не требуется.
        """
        from django.utils import timezone
        from django.db.models import Count

        line_id = request.data.get('line_id')
        line = Line.objects.filter(pk=line_id).first() if line_id else None

        now = timezone.now()
        with transaction.atomic():
            if line:
                LineHistory.objects.create(
                    line=line, action=LineHistory.ACTION_OPEN,
                    date=now.date(), time=now.time(), user=request.user,
                )
            shift = Shift.objects.create(line=line, user=request.user, opened_at=now)

        shift = Shift.objects.annotate(notes_count=Count('notes')).get(pk=shift.pk)
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='close')
    def close(self, request):
        """
        POST /api/shifts/close/
        Тело: { "comment": "..." } — всё необязательно.
        Находит последнюю открытую смену текущего пользователя и закрывает её.
        line_id передавать не нужно.
        """
        from django.utils import timezone
        from django.db.models import Count

        open_shift = (
            Shift.objects.filter(user=request.user, closed_at__isnull=True)
            .order_by('-opened_at')
            .first()
        )
        if not open_shift:
            return _err('not_found', 'Нет открытой смены', http_status=404)

        now = timezone.now()
        comment = request.data.get('comment', '')
        with transaction.atomic():
            if open_shift.line_id:
                LineHistory.objects.create(
                    line=open_shift.line, action=LineHistory.ACTION_CLOSE,
                    date=now.date(), time=now.time(), user=request.user,
                )
            open_shift.closed_at = now
            open_shift.comment = comment
            open_shift.save(update_fields=['closed_at', 'comment'])

        shift = Shift.objects.annotate(notes_count=Count('notes')).get(pk=open_shift.pk)
        return Response(ShiftSerializer(shift).data)

    @action(detail=True, methods=['get'], url_path='notes')
    def shift_notes(self, request, pk=None):
        """GET /api/shifts/{id}/notes/ — заметки конкретной смены."""
        from .models import ShiftNote
        shift = self.get_object()
        notes = ShiftNote.objects.filter(shift=shift).order_by('-created_at')
        return Response({'items': ShiftNoteSerializer(notes, many=True).data})

    @action(detail=False, methods=['get'], url_path='my')
    def my(self, request):
        """
        GET /api/shifts/my/ — текущая открытая смена пользователя.
        Если смена открыта — возвращает объект смены.
        Если нет — возвращает {"shift": null}.
        """
        from django.db.models import Count

        open_shift = (
            Shift.objects.filter(user=request.user, closed_at__isnull=True)
            .select_related('line')
            .annotate(notes_count=Count('notes'))
            .order_by('-opened_at')
            .first()
        )
        if not open_shift:
            return Response({'shift': None})
        return Response({'shift': ShiftSerializer(open_shift).data})

    @action(detail=False, methods=['get', 'post'], url_path='notes')
    def notes(self, request):
        """
        GET  /api/shifts/notes/ — список заметок текущей открытой смены.
        POST /api/shifts/notes/ — добавить заметку (тело: { "note": "текст" }).
        """
        from .models import ShiftNote

        open_shift = (
            Shift.objects.filter(user=request.user, closed_at__isnull=True)
            .order_by('-opened_at')
            .first()
        )

        if request.method == 'GET':
            if not open_shift:
                return Response({'items': []})
            notes = ShiftNote.objects.filter(shift=open_shift).order_by('-created_at')
            return Response({
                'items': [
                    {'id': n.id, 'shift': open_shift.id, 'note': n.text, 'created_at': n.created_at}
                    for n in notes
                ]
            })

        # POST
        note_text = request.data.get('note', '').strip()
        if not note_text:
            return _err('validation_error', 'Укажите текст заметки',
                        errors=[{'field': 'note', 'message': 'Обязательное поле'}])
        if not open_shift:
            return _err('not_found', 'Нет открытой смены для добавления заметки', http_status=404)

        note = ShiftNote.objects.create(shift=open_shift, user=request.user, text=note_text)
        return Response({
            'id': note.id,
            'shift': open_shift.id,
            'note': note.text,
            'created_at': note.created_at,
        }, status=status.HTTP_201_CREATED)


class ShiftHistoryView(viewsets.ViewSet):
    """
    GET /api/shifts/history/ — история смен текущего пользователя (от новых к старым).
    Параметр: page_size.
    """

    def list(self, request):
        from django.db.models import Count
        qs = (
            Shift.objects.filter(user=request.user)
            .select_related('line')
            .annotate(notes_count=Count('notes'))
            .order_by('-opened_at')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ShiftSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ProductionReleaseView(viewsets.ViewSet):
    """POST /api/production/release/ — тело: { orderId, quantity }."""
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'production'

    def create(self, request):
        order_id = request.data.get('orderId')
        quantity_raw = request.data.get('quantity')

        errors = []
        if not order_id:
            errors.append({'field': 'orderId', 'message': 'Обязательное поле'})
        if quantity_raw is None:
            errors.append({'field': 'quantity', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите orderId и quantity', errors=errors)

        try:
            quantity = Decimal(str(quantity_raw))
        except Exception:
            return _err('validation_error', 'Некорректное значение quantity',
                        errors=[{'field': 'quantity', 'message': 'Должно быть числом'}])

        order = Order.objects.select_related('recipe', 'line').filter(pk=order_id).first()
        if not order:
            return _err('not_found', 'Заказ не найден', http_status=404)

        batch, err_response, _ = _perform_release(order, quantity, getattr(request, 'user', None))
        if batch is None:
            return err_response
        return Response(ProductionBatchSerializer(batch).data, status=status.HTTP_201_CREATED)
