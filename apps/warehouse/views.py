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
