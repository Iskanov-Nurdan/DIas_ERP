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
from .packaging import compute_pieces_per_package, plan_fifo_pack
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

        Контракт фронта: приоритет объёма = pieces_per_package × packages_count (шт задаёт пользователь).
        Геометрия: shift_height (или unit_meters), shift_width или width_meters, angle_deg — обязательны.
        product_id — ключ продукта (= поле product на строке склада).

        Если задан package_total_meters — проверяется согласованность с pieces_per_package × unit_meters (допуск 1 мм).
        Без pieces_per_package: обратная совместимость — расчёт штук из package_total_meters / unit_meters (floor).
        """
        d = request.data

        def _req_dec(key):
            v = d.get(key)
            if v is None or v == '':
                raise KeyError(key)
            return Decimal(str(v))

        product_id = d.get('product_id')
        if product_id is None or str(product_id).strip() == '':
            return _err(
                'validation_error',
                'Обязательное поле product_id (ключ продукта, как в поле product строки склада)',
                errors=[{'field': 'product_id', 'message': 'Обязательное поле'}],
            )
        product_key = str(product_id).strip()

        try:
            if d.get('shift_height') not in (None, ''):
                unit_meters = Decimal(str(d['shift_height']))
            elif d.get('unit_meters') not in (None, ''):
                unit_meters = Decimal(str(d['unit_meters']))
            else:
                return _err(
                    'validation_error',
                    'Укажите shift_height или unit_meters (высота одной штуки, м)',
                    errors=[{'field': 'shift_height', 'message': 'Обязательное поле'}],
                )
            if d.get('shift_width') not in (None, ''):
                width_req = Decimal(str(d['shift_width']))
            elif d.get('width_meters') not in (None, ''):
                width_req = Decimal(str(d['width_meters']))
            else:
                return _err(
                    'validation_error',
                    'Укажите shift_width или width_meters',
                    errors=[{'field': 'shift_width', 'message': 'Обязательное поле'}],
                )
            if d.get('angle_deg') in (None, ''):
                return _err(
                    'validation_error',
                    'Обязательное поле angle_deg',
                    errors=[{'field': 'angle_deg', 'message': 'Обязательное поле'}],
                )
            angle_req = Decimal(str(d['angle_deg']))
        except (InvalidOperation, TypeError, ValueError):
            return _err(
                'validation_error',
                'Некорректные числовые значения геометрии',
                errors=[{'field': 'body', 'message': 'Должны быть числами'}],
            )

        if unit_meters <= 0:
            return _err(
                'validation_error',
                'shift_height / unit_meters должно быть > 0',
                errors=[{'field': 'shift_height', 'message': '> 0'}],
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
        package_total_meters = None
        if ppp_raw not in (None, ''):
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
            expected_m = pieces_per_package * unit_meters
            pkg_in = d.get('package_total_meters')
            if pkg_in not in (None, ''):
                try:
                    package_total_meters = Decimal(str(pkg_in))
                except (InvalidOperation, TypeError, ValueError):
                    return _err(
                        'validation_error',
                        'Некорректное package_total_meters',
                        errors=[{'field': 'package_total_meters', 'message': 'Число'}],
                    )
                if (package_total_meters - expected_m).copy_abs() > Decimal('0.0001'):
                    return _err(
                        'validation_error',
                        'package_total_meters не согласован с pieces_per_package × shift_height (допуск 0,0001 м)',
                        errors=[{'field': 'package_total_meters', 'message': 'Пересчитайте или уберите поле'}],
                    )
            else:
                package_total_meters = expected_m.quantize(Decimal('0.0001'))
        else:
            try:
                package_total_meters = _req_dec('package_total_meters')
            except KeyError:
                return _err(
                    'validation_error',
                    'Укажите pieces_per_package или (для старого сценария) package_total_meters',
                    errors=[{'field': 'pieces_per_package', 'message': 'Обязательное поле'}],
                )
            except (InvalidOperation, TypeError, ValueError):
                return _err(
                    'validation_error',
                    'Некорректное package_total_meters',
                    errors=[{'field': 'package_total_meters', 'message': 'Число'}],
                )
            try:
                pieces_int = compute_pieces_per_package(unit_meters, package_total_meters)
            except ValueError as e:
                code = str(e)
                if code == 'pkg_lt_unit':
                    msg = 'package_total_meters не может быть меньше unit_meters'
                elif code == 'ppp_lt_1':
                    msg = 'Из метража следует меньше 1 штуки в упаковке'
                else:
                    msg = 'Некорректные параметры длины/метража'
                return _err('validation_error', msg, errors=[{'field': 'package_total_meters', 'message': msg}])
            pieces_per_package = Decimal(pieces_int)

        need = (pieces_per_package * Decimal(packages_count)).quantize(Decimal('0.0001'))

        base_ids = list(
            WarehouseBatch.objects.filter(
                inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
                status=WarehouseBatch.STATUS_AVAILABLE,
                product=product_key,
            )
            .order_by('id')
            .values_list('id', flat=True)
        )

        if not base_ids:
            return _err(
                'conflict',
                'Нет строк «не упаковано» с указанным продуктом',
                http_status=status.HTTP_409_CONFLICT,
            )

        created = []
        with transaction.atomic():
            rows = list(
                WarehouseBatch.objects.select_for_update()
                .select_related('source_batch')
                .filter(pk__in=base_ids)
                .order_by('id')
            )

            takes, err = plan_fifo_pack(rows, need, unit_meters, width_req, angle_req)
            if err == 'no_matching_lines':
                return _err(
                    'conflict',
                    'Нет строк с формой «не упаковано», совпадающих по длине штуки и (если заданы) ширине/углу смены',
                    http_status=status.HTTP_409_CONFLICT,
                )
            if err == 'insufficient':
                return _err(
                    'conflict',
                    f'Недостаточно подходящего остатка для упаковки (нужно {need} шт.)',
                    http_status=status.HTTP_409_CONFLICT,
                )

            first_row = None
            for pt in takes:
                row = next(r for r in rows if r.pk == pt.row_id)
                if first_row is None:
                    first_row = row
                row.quantity -= pt.take
                if row.quantity <= 0:
                    row.delete()
                else:
                    row.save(update_fields=['quantity'])

            pb = first_row.source_batch if first_row else None
            check = None
            if pb is not None:
                check = pb.otk_checks.order_by('-checked_date', '-id').first()
            otk_acc = first_row.otk_accepted if first_row and first_row.otk_accepted is not None else (check.accepted if check else None)
            otk_def = first_row.otk_defect if first_row and first_row.otk_defect is not None else (check.rejected if check else None)
            ins_name = (first_row.otk_inspector_name or '') if first_row else ''
            if not ins_name and check and check.inspector_id:
                ins_name = (getattr(check.inspector, 'name', None) or '')[:255]
            chk_at = (first_row.otk_checked_at if first_row else None) or (check.checked_date if check else None)
            otk_st = (first_row.otk_status if first_row else '') or (pb.otk_status if pb else '')
            reason = (first_row.otk_defect_reason if first_row else '') or (check.reject_reason if check else '') or ''
            comment = (first_row.otk_comment if first_row else '') or (check.comment if check else '') or ''

            wb = WarehouseBatch.objects.create(
                product=product_key,
                quantity=need,
                status=WarehouseBatch.STATUS_AVAILABLE,
                date=date.today(),
                source_batch=pb,
                inventory_form=WarehouseBatch.INVENTORY_PACKED,
                unit_meters=unit_meters,
                package_total_meters=package_total_meters,
                pieces_per_package=pieces_per_package,
                packages_count=Decimal(packages_count),
                otk_accepted=otk_acc,
                otk_defect=otk_def,
                otk_defect_reason=reason,
                otk_comment=comment,
                otk_inspector_name=ins_name,
                otk_checked_at=chk_at,
                otk_status=otk_st or '',
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
                    'product_id': product_key,
                    'packages_count': packages_count,
                },
            )

        return Response(
            {'items': WarehouseBatchSerializer(created, many=True).data},
            status=status.HTTP_201_CREATED,
        )
