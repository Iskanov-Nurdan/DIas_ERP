"""GET /api/warehouse/operations/ — лента движений склада ГП."""
from __future__ import annotations

from datetime import date, datetime

from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsAdminOrHasAccess

from .operations_ledger import build_warehouse_operations


def _parse_int(raw) -> int | None:
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_date(raw) -> date | None:
    if raw in (None, ''):
        return None
    try:
        return datetime.strptime(str(raw).strip()[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


class WarehouseOperationsView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'

    def get(self, request):
        page = max(1, _parse_int(request.query_params.get('page')) or 1)
        page_size = min(500, max(1, _parse_int(request.query_params.get('page_size')) or 50))
        product_id = _parse_int(request.query_params.get('product_id'))
        blank_id = _parse_int(request.query_params.get('blank_id'))
        date_from = _parse_date(request.query_params.get('date_from'))
        date_to = _parse_date(request.query_params.get('date_to'))
        kinds = request.query_params.get('kind') or request.query_params.get('kinds')

        rows = build_warehouse_operations(
            date_from=date_from,
            date_to=date_to,
            product_id=product_id,
            blank_id=blank_id,
            kinds=kinds,
        )
        total = len(rows)
        offset = (page - 1) * page_size
        page_rows = rows[offset: offset + page_size]
        items = [r.to_item() for r in page_rows]
        return Response(
            {
                'items': items,
                'results': items,
                'meta': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                },
            }
        )
