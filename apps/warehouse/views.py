import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess
from apps.activity.audit_service import instance_to_snapshot, schedule_entity_audit
from .filters import WarehouseBatchFilter
from .models import WarehouseBatch
from .packaging import effective_unit_meters
from .serializers import WarehouseBatchSerializer

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


class WarehouseBatchViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = WarehouseBatchSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'
    filterset_class = WarehouseBatchFilter
    ordering_fields = ['id', 'date']

    def get_queryset(self):
        qs = WarehouseBatch.objects.select_related('source_batch', 'source_batch__order', 'source_batch__order__line').all()
        debug = str(self.request.query_params.get('debug', '')).lower() in ('1', 'true', 'yes')
        if not debug:
            qs = qs.exclude(Q(product__iexact='test') | Q(product__iexact='тест'))
        return qs

    @extend_schema(
        summary='Резерв партии склада',
        request=inline_serializer(
            name='WarehouseReserveRequest',
            fields={
                'batch_id': serializers.IntegerField(help_text='ID партии (канон); принимается и batchId.'),
                'batchId': serializers.IntegerField(
                    required=False, help_text='Устаревший алиас; предпочтительно batch_id.',
                ),
                'quantity': serializers.DecimalField(max_digits=24, decimal_places=8),
                'sale_id': serializers.IntegerField(
                    required=False,
                    allow_null=True,
                    help_text='Опционально: id продажи для контекста/аудита (не меняет бизнес-логику резерва).',
                ),
            },
        ),
        responses={
            200: WarehouseBatchSerializer,
            400: DiasErrorSerializer,
            404: DiasErrorSerializer,
        },
    )
    @action(detail=False, methods=['post'], url_path='reserve')
    def reserve(self, request):
        batch_id = request.data.get('batch_id') or request.data.get('batchId')
        quantity_raw = request.data.get('quantity')
        sale_id = request.data.get('sale_id')

        errors = []
        if not batch_id:
            errors.append({'field': 'batch_id', 'message': 'Обязательное поле'})
        if quantity_raw is None:
            errors.append({'field': 'quantity', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите batch_id и quantity', errors=errors)

        batch = WarehouseBatch.objects.filter(pk=batch_id).first()
        if not batch:
            return _err('not_found', 'Партия не найдена', http_status=404)

        if batch.status != WarehouseBatch.STATUS_AVAILABLE:
            return _err('bad_request', 'Партия недоступна для резервирования')

        try:
            q = Decimal(str(quantity_raw))
        except (InvalidOperation, TypeError, ValueError):
            return _err('validation_error', 'Некорректное значение quantity',
                        errors=[{'field': 'quantity', 'message': 'Должно быть числом'}])

        if q <= 0:
            return _err('validation_error', 'quantity должно быть больше 0',
                        errors=[{'field': 'quantity', 'message': 'Должно быть больше 0'}])

        if q > batch.quantity:
            return _err('bad_request',
                        f'Количество превышает доступный остаток ({batch.quantity})',
                        errors=[{'field': 'quantity', 'message': f'Максимум: {batch.quantity}'}])

        if q != batch.quantity:
            return _err(
                'validation_error',
                'Резерв выполняется только на полный остаток строки склада: передайте quantity, равное доступному количеству.',
                errors=[
                    {
                        'field': 'quantity',
                        'message': f'Ожидается quantity={batch.quantity} (вся строка переходит в статус «зарезервировано»).',
                    },
                ],
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        before = instance_to_snapshot(batch)
        batch.status = WarehouseBatch.STATUS_RESERVED
        batch.save(update_fields=['status'])
        batch.refresh_from_db()
        after = instance_to_snapshot(batch)
        extra = {
            'endpoint': 'POST /api/warehouse/batches/reserve/',
            'quantity': str(q),
        }
        if sale_id is not None and str(sale_id).strip() != '':
            extra['sale_id'] = sale_id
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='Склад',
            description=f'Резерв партии склада #{batch.pk}, quantity={q}',
            action='update',
            model_cls=WarehouseBatch,
            before=before,
            after=after,
            after_instance=batch,
            payload_extra=extra,
        )
        return Response(WarehouseBatchSerializer(batch).data)

    @action(detail=False, methods=['post'], url_path='package')
    def package(self, request):
        """
        POST /api/warehouse/batches/package/

        Тело: warehouse_batch_id (обяз.), pieces_per_package, packages_count, comment (опц.).
        Длина штуки / м на ед. берётся из строки склада (unit_meters / смена исходной партии / length_per_piece).
        Качество новой строки = quality исходной строки; смешивание партий и смена качества через API запрещены.
        """
        d = request.data

        wb_id = d.get('warehouse_batch_id') if d.get('warehouse_batch_id') not in (None, '') else d.get('batchId')
        if wb_id in (None, ''):
            return _err(
                'validation_error',
                'Обязательное поле warehouse_batch_id',
                errors=[{'field': 'warehouse_batch_id', 'message': 'Обязательное поле'}],
            )
        try:
            wb_id = int(wb_id)
        except (TypeError, ValueError):
            return _err(
                'validation_error',
                'Некорректный warehouse_batch_id',
                errors=[{'field': 'warehouse_batch_id', 'message': 'Целое число'}],
            )

        pc_raw = d.get('packages_count')
        if pc_raw is None or pc_raw == '':
            return _err(
                'validation_error',
                'Обязательное поле packages_count',
                errors=[{'field': 'packages_count', 'message': 'Обязательное поле'}],
            )
        try:
            packages_count = int(Decimal(str(pc_raw)))
        except (InvalidOperation, TypeError, ValueError):
            return _err(
                'validation_error',
                'packages_count должно быть целым числом ≥ 1',
                errors=[{'field': 'packages_count', 'message': 'Целое число'}],
            )
        if packages_count < 1:
            return _err(
                'validation_error',
                'packages_count должно быть ≥ 1',
                errors=[{'field': 'packages_count', 'message': 'Минимум 1'}],
            )

        ppp_raw = d.get('pieces_per_package')
        if ppp_raw is None or ppp_raw == '':
            return _err(
                'validation_error',
                'Обязательное поле pieces_per_package',
                errors=[{'field': 'pieces_per_package', 'message': 'Обязательное поле'}],
            )
        try:
            pieces_int = int(Decimal(str(ppp_raw)))
        except (InvalidOperation, TypeError, ValueError):
            return _err(
                'validation_error',
                'pieces_per_package — целое число ≥ 1',
                errors=[{'field': 'pieces_per_package', 'message': 'Целое число'}],
            )
        if pieces_int < 1:
            return _err(
                'validation_error',
                'pieces_per_package должно быть ≥ 1',
                errors=[{'field': 'pieces_per_package', 'message': 'Минимум 1'}],
            )
        pieces_per_package = Decimal(pieces_int)

        extra_comment = (d.get('comment') or '').strip()

        created = []
        with transaction.atomic():
            row = (
                WarehouseBatch.objects.select_for_update()
                .select_related('source_batch')
                .filter(pk=wb_id)
                .first()
            )
            if row is None:
                return _err('not_found', 'Строка склада не найдена', http_status=status.HTTP_404_NOT_FOUND)
            if row.inventory_form != WarehouseBatch.INVENTORY_UNPACKED:
                return _err(
                    'bad_request',
                    'Упаковка только для строк в форме «не упаковано»',
                    errors=[{'field': 'warehouse_batch_id', 'message': 'Неверная форма учёта'}],
                )
            if row.status != WarehouseBatch.STATUS_AVAILABLE:
                return _err(
                    'bad_request',
                    'Строка недоступна для упаковки (не в статусе «доступна»)',
                    errors=[{'field': 'warehouse_batch_id', 'message': 'Недоступна'}],
                )

            unit_m = effective_unit_meters(row)
            if unit_m is None or unit_m <= 0:
                if row.length_per_piece is not None:
                    unit_m = Decimal(str(row.length_per_piece))
            if unit_m is None or unit_m <= 0:
                return _err(
                    'validation_error',
                    'У строки нет длины штуки (м) для расчёта упаковки',
                    errors=[{'field': 'warehouse_batch_id', 'message': 'Заполните unit_meters / length_per_piece у партии'}],
                )

            need = (pieces_per_package * Decimal(packages_count)).quantize(Decimal('0.0001'))
            row_qty = Decimal(str(row.quantity))
            if need > row_qty:
                return _err(
                    'conflict',
                    f'Недостаточно штук на строке (нужно {need}, доступно {row_qty})',
                    http_status=status.HTTP_409_CONFLICT,
                )

            package_total_meters = (pieces_per_package * unit_m).quantize(Decimal('0.0001'))

            row.quantity = row_qty - need
            if row.quantity <= 0:
                row.delete()
            else:
                row.save(update_fields=['quantity'])

            pb = row.source_batch
            check = None
            if pb is not None:
                check = pb.otk_checks.order_by('-checked_date', '-id').first()
            otk_acc = row.otk_accepted if row.otk_accepted is not None else (check.accepted if check else None)
            otk_def = row.otk_defect if row.otk_defect is not None else (check.rejected if check else None)
            ins_name = row.otk_inspector_name or ''
            if not ins_name and check and check.inspector_id:
                ins_name = (getattr(check.inspector, 'name', None) or '')[:255]
            chk_at = row.otk_checked_at or (check.checked_date if check else None)
            otk_st = (row.otk_status or '') or (pb.otk_status if pb else '')
            reason = row.otk_defect_reason or (check.reject_reason if check else '') or ''
            base_comment = row.otk_comment or (check.comment if check else '') or ''
            if extra_comment:
                merged_comment = (base_comment + ('\n' if base_comment else '') + extra_comment).strip()
            else:
                merged_comment = base_comment

            wb = WarehouseBatch.objects.create(
                profile_id=row.profile_id,
                product=row.product,
                length_per_piece=row.length_per_piece,
                cost_per_piece=row.cost_per_piece,
                cost_per_meter=row.cost_per_meter,
                quantity=need,
                quality=row.quality,
                defect_reason=row.defect_reason or '',
                status=WarehouseBatch.STATUS_AVAILABLE,
                date=date.today(),
                source_batch=pb,
                inventory_form=WarehouseBatch.INVENTORY_PACKED,
                unit_meters=unit_m,
                package_total_meters=package_total_meters,
                pieces_per_package=pieces_per_package,
                packages_count=Decimal(packages_count),
                otk_accepted=otk_acc,
                otk_defect=otk_def,
                otk_defect_reason=reason,
                otk_comment=merged_comment,
                otk_inspector_name=ins_name,
                otk_checked_at=chk_at,
                otk_status=(otk_st or '')[:20],
            )
            created.append(wb)

        for wb in created:
            schedule_entity_audit(
                user=request.user,
                request=request,
                section='Склад',
                description=f'Упаковка: партия склада #{wb.pk}, product={wb.product}, quantity={wb.quantity}',
                action='create',
                model_cls=WarehouseBatch,
                after_instance=wb,
                payload_extra={
                    'endpoint': 'POST /api/warehouse/batches/package/',
                    'warehouse_batch_id': wb_id,
                    'packages_count': packages_count,
                },
            )

        return Response(
            {'items': WarehouseBatchSerializer(created, many=True).data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'], url_path='trace')
    def trace(self, request, pk=None):
        """
        GET /api/warehouse/batches/{id}/trace/

        Полная трассировка партии склада ГП:
        производство → ОТК → склад → продажи → возвраты → брак → переделка.
        """
        wb = self.get_object()
        result: dict = {
            'warehouse_batch_id': wb.pk,
            'product': wb.product,
            'quality': wb.quality,
            'status': wb.status,
            'date': wb.date.isoformat() if wb.date else None,
        }

        # Производственная партия-источник
        if wb.source_batch_id:
            pb = wb.source_batch
            result['production_batch'] = {
                'id': pb.pk,
                'product': pb.product,
                'date': pb.date.isoformat() if pb.date else None,
                'otk_status': pb.otk_status,
                'lifecycle_status': pb.lifecycle_status,
                'total_meters': str(pb.total_meters) if pb.total_meters else None,
                'material_cost_total': str(pb.material_cost_total) if pb.material_cost_total else None,
                'cost_per_meter': str(pb.cost_per_meter) if pb.cost_per_meter else None,
                'order_id': pb.order_id,
                'line_id': pb.line_id,
            }
            # ОТК
            otk_checks = list(pb.otk_checks.select_related('inspector').order_by('checked_date', 'id'))
            result['otk_checks'] = [
                {
                    'id': c.pk,
                    'check_status': c.check_status,
                    'accepted': str(c.accepted) if c.accepted else None,
                    'rejected': str(c.rejected) if c.rejected else None,
                    'reject_reason': c.reject_reason or '',
                    'inspector_name': c.inspector_name or '',
                    'checked_date': c.checked_date.isoformat() if c.checked_date else None,
                }
                for c in otk_checks
            ]
        else:
            result['production_batch'] = None
            result['otk_checks'] = []

        # Продажи из этой партии
        from apps.sales.models import SaleLine, ReturnLine, DefectRecord, ReworkRequest, OrderReservation
        sale_lines = (
            SaleLine.objects.filter(warehouse_batch=wb)
            .select_related('sale', 'sale__client', 'order_line__order')
            .order_by('sale__date', 'id')
        )
        result['sale_lines'] = [
            {
                'sale_line_id': sl.pk,
                'sale_id': sl.sale_id,
                'sale_order_number': sl.sale.order_number,
                'sale_date': sl.sale.date.isoformat() if sl.sale.date else None,
                'client_name': sl.sale.client.name if sl.sale.client_id else '—',
                'quantity': str(sl.quantity),
                'unit_price': str(sl.unit_price) if sl.unit_price else None,
                'line_total': str(sl.line_total),
                'cost': str(sl.cost),
                'profit': str(sl.profit),
                'order_number': sl.order_line.order.order_number if sl.order_line_id else None,
            }
            for sl in sale_lines
        ]

        # Возвраты по продажам из этой партии
        sale_ids = [sl.sale_id for sl in sale_lines]
        return_lines = (
            ReturnLine.objects.filter(sale_line__warehouse_batch=wb)
            .select_related('return_doc', 'sale_line__sale')
            .order_by('return_doc__date', 'id')
        )
        result['return_lines'] = [
            {
                'return_line_id': rl.pk,
                'return_doc_id': rl.return_doc_id,
                'return_number': rl.return_doc.return_number,
                'return_date': rl.return_doc.date.isoformat() if rl.return_doc.date else None,
                'quantity': str(rl.quantity),
                'return_target': rl.return_target,
                'condition_type': rl.condition_type,
            }
            for rl in return_lines
        ]

        # Записи брака
        defects = DefectRecord.objects.filter(source_type='return').filter(
            source_id__in=[rl.pk for rl in return_lines]
        )
        result['defect_records'] = [
            {
                'defect_id': d.pk,
                'status': d.status,
                'quantity_pcs': str(d.quantity_pcs),
                'quantity_kg': str(d.quantity_kg) if d.quantity_kg else None,
                'defect_reason': d.defect_reason,
            }
            for d in defects
        ]

        # Переделки
        defect_ids = [d.pk for d in defects]
        reworks = ReworkRequest.objects.filter(defect_record_id__in=defect_ids).select_related(
            'result_warehouse_batch'
        )
        result['rework_requests'] = [
            {
                'rework_id': r.pk,
                'rework_number': r.rework_number,
                'status': r.status,
                'quantity_kg': str(r.quantity_kg),
                'output_quantity_kg': str(r.output_quantity_kg) if r.output_quantity_kg else None,
                'loss_kg': str(r.loss_kg) if r.loss_kg else None,
                'result_batch_id': r.result_warehouse_batch_id,
            }
            for r in reworks
        ]

        # Активные резервы
        active_reservations = (
            OrderReservation.objects.filter(
                warehouse_batch=wb, status='active',
            ).select_related('order_line__order')
        )
        result['active_reservations'] = [
            {
                'reservation_id': res.pk,
                'order_line_id': res.order_line_id,
                'order_number': res.order_line.order.order_number if res.order_line_id else None,
                'quantity': str(res.quantity),
            }
            for res in active_reservations
        ]

        return Response(result)
