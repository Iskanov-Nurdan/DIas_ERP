import logging
from decimal import Decimal
from html import escape

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse

from rest_framework import viewsets, status
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from .filters import ClientFilter, SaleFilter
from .models import Client, Sale, Shipment
from .serializers import ClientSerializer, SaleSerializer

logger = logging.getLogger(__name__)


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    """Единый стиль: строковые code / error / detail (для UI), опционально errors."""
    payload = {
        'code': code,
        'error': message,
        'detail': message,
    }
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


class ClientViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'clients'
    activity_section = 'Клиенты'
    activity_label = 'клиент'
    filterset_class = ClientFilter
    search_fields = ['name', 'inn', 'contact', 'email', 'messenger']
    ordering_fields = ['id', 'name']

    def get_queryset(self):
        return (
            Client.objects.annotate(
                sales_count=Count('sales', distinct=False),
                sales_total=Coalesce(
                    Sum('sales__revenue'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                ),
            )
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        sales_count = instance.sales.count()
        if sales_count:
            return Response(
                {
                    'code': 'CLIENT_IN_USE',
                    'error': 'Нельзя удалить клиента: есть связанные продажи.',
                    'detail': (
                        'Сначала удалите или переназначьте продажи, привязанные к этому клиенту '
                        '(или оставьте клиента в справочнике для истории).'
                    ),
                    'sales_count': sales_count,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        client = self.get_object()
        sales = Sale.objects.filter(client=client).order_by('-date')
        return Response({'items': SaleSerializer(sales, many=True).data})


class SaleViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Sale.objects.select_related(
        'client', 'warehouse_batch', 'warehouse_batch__profile',
    ).all()
    serializer_class = SaleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'sales'
    activity_section = 'Продажи'
    activity_label = 'продажа'
    filterset_class = SaleFilter
    ordering_fields = ['id', 'date']

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.warehouse_batch_id:
            if 'warehouse_batch' in serializer.validated_data:
                wb = serializer.validated_data['warehouse_batch']
                new_id = wb.pk if wb else None
                if new_id is None:
                    raise ValidationError(
                        {'warehouse_batch': 'Нельзя отвязать партию склада у продажи'},
                    )
                if new_id != instance.warehouse_batch_id:
                    raise ValidationError(
                        {'warehouse_batch': 'Нельзя сменить партию склада у существующей продажи'},
                    )
            if 'quantity' in serializer.validated_data:
                new_q = serializer.validated_data['quantity']
                if Decimal(str(new_q)) != Decimal(str(instance.quantity)):
                    raise ValidationError(
                        {'quantity': 'Нельзя изменить количество: создайте новую продажу или отмените эту'},
                    )
            if 'quantity_input' in serializer.validated_data:
                new_qi = serializer.validated_data.get('quantity_input')
                old_qi = instance.quantity_input
                if new_qi is None and old_qi is None:
                    pass
                elif new_qi is None or old_qi is None:
                    raise ValidationError(
                        {'quantity_input': 'Нельзя изменить quantity_input: создайте новую продажу или отмените эту'},
                    )
                elif Decimal(str(new_qi)) != Decimal(str(old_qi)):
                    raise ValidationError(
                        {'quantity_input': 'Нельзя изменить quantity_input: создайте новую продажу или отмените эту'},
                    )
            if 'stock_form' in serializer.validated_data or 'piece_pick' in serializer.validated_data:
                raise ValidationError(
                    {'stock_form': 'Нельзя менять stock_form / piece_pick после создания продажи со складом'},
                )
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        """Shipment.sale — PROTECT; иначе DELETE продажи даёт 500."""
        from django.db import transaction

        with transaction.atomic():
            Shipment.objects.filter(sale_id=instance.pk).delete()
            super().perform_destroy(instance)

    @staticmethod
    def _nakladnaya_http_response(sale):
        parts = [
            '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
            f'<title>Накладная {escape(sale.order_number)}</title>',
            '<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;line-height:1.45;}',
            'table{border-collapse:collapse;width:100%;}td,th{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;}</style>',
            '</head><body>',
            '<h1>Накладная (черновик)</h1>',
            f'<p><strong>№</strong> {escape(sale.order_number)} <strong>от</strong> {sale.date.isoformat()}</p>',
        ]
        if sale.client_id:
            cl = sale.client
            parts.append(f'<p><strong>Покупатель:</strong> {escape(cl.name)}</p>')
            if cl.inn:
                parts.append(f'<p>ИНН: {escape(cl.inn)}</p>')
            if cl.address:
                parts.append(f'<p>{escape(cl.address)}</p>')
            if cl.phone:
                parts.append(f'<p>Тел.: {escape(cl.phone)}</p>')
        else:
            parts.append('<p><strong>Покупатель:</strong> —</p>')
        parts.append('<table><thead><tr><th>Наименование</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>')
        qty = sale.quantity
        price = sale.price
        line_sum = ''
        if price is not None:
            line_sum = str((price * qty).quantize(Decimal('0.01')))
        parts.append(
            '<tr>'
            f'<td>{escape(sale.product)}</td>'
            f'<td>{escape(str(qty))}</td>'
            f'<td>{escape(str(price if price is not None else "—"))}</td>'
            f'<td>{escape(line_sum or "—")}</td>'
            '</tr>'
        )
        parts.append('</tbody></table>')
        if sale.comment:
            parts.append(f'<p><strong>Комментарий:</strong> {escape(sale.comment)}</p>')
        if sale.warehouse_batch_id:
            parts.append(f'<p><strong>Партия склада ГП:</strong> №{sale.warehouse_batch_id}</p>')
        parts.append('<p><em>Сформировано автоматически.</em></p></body></html>')
        html = ''.join(parts)
        resp = HttpResponse(html, content_type='text/html; charset=utf-8')
        resp['Content-Disposition'] = f'inline; filename="nakladnaya-{sale.id}.html"'
        return resp

    def _serve_nakladnaya(self, request, *args, **kwargs):
        sale = self.get_object()
        return SaleViewSet._nakladnaya_http_response(sale)

    @action(detail=True, methods=['get'], url_path='nakladnaya')
    def nakladnaya(self, request, pk=None):
        return self._serve_nakladnaya(request)

    @action(detail=True, methods=['get'], url_path='waybill')
    def waybill(self, request, pk=None):
        return self._serve_nakladnaya(request)

    @action(detail=True, methods=['get'], url_path='invoice')
    def invoice(self, request, pk=None):
        return self._serve_nakladnaya(request)
