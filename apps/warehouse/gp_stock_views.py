"""GET /api/warehouse/gp-stock/ — остатки ГП по профилю и заготовке."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_numbers import api_decimal_str
from config.permissions import IsAdminOrHasAccess

from apps.recipes.models import PlasticProfile
from apps.warehouse.models import WarehouseBatch


class GpStockView(APIView):
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'warehouse'

    def get(self, request):
        qs = (
            WarehouseBatch.objects.filter(
                status=WarehouseBatch.STATUS_AVAILABLE,
                quality=WarehouseBatch.QUALITY_GOOD,
                stock_bucket=WarehouseBatch.STOCK_BUCKET_STANDARD,
            )
            .select_related('profile', 'workshop_blank', 'blank_production_run__blank')
        )

        product_id = request.query_params.get('product_id')
        blank_id = request.query_params.get('blank_id')
        if product_id:
            qs = qs.filter(profile_id=product_id)
        if blank_id:
            qs = qs.filter(
                Q(workshop_blank_id=blank_id) | Q(blank_production_run__blank_id=blank_id)
            )

        agg: dict[tuple[int | None, int | None], dict] = defaultdict(
            lambda: {'pieces': Decimal('0'), 'kg': Decimal('0'), 'product_name': '', 'blank_name': ''}
        )
        profile_ids: set[int] = set()
        blank_ids: set[int] = set()

        for wb in qs:
            pid = wb.profile_id
            bid = wb.workshop_blank_id
            if bid is None and wb.blank_production_run_id:
                bid = wb.blank_production_run.blank_id
            profile_ids.add(pid)
            if bid:
                blank_ids.add(bid)
            key = (pid, bid)
            pcs = Decimal(str(wb.quantity or 0))
            row = agg[key]
            row['pieces'] += pcs
            row['product_name'] = wb.product or (wb.profile.name if wb.profile_id else '')
            if wb.workshop_blank_id:
                row['blank_name'] = wb.workshop_blank.name
            elif wb.blank_production_run_id and wb.blank_production_run.blank_id:
                row['blank_name'] = wb.blank_production_run.blank_name_snapshot or wb.blank_production_run.blank.name
            wpp = Decimal('0')
            if wb.profile_id and wb.profile.weight_kg_per_piece:
                wpp = Decimal(str(wb.profile.weight_kg_per_piece))
            row['kg'] += pcs * wpp

        profiles = {p.pk: p for p in PlasticProfile.objects.filter(pk__in=profile_ids)}
        from apps.recipes.profile_pricing import serialize_sale_unit_price
        from apps.workshop.models import WorkshopBlank

        blanks = {b.pk: b for b in WorkshopBlank.objects.filter(pk__in=blank_ids)}

        items = []
        for (pid, bid), row in sorted(agg.items(), key=lambda x: (x[0][0] or 0, x[0][1] or 0)):
            prof = profiles.get(pid) if pid else None
            blank = blanks.get(bid) if bid else None
            sale_price = serialize_sale_unit_price(prof) if prof else None
            items.append(
                {
                    'product_id': pid,
                    'product_name': row['product_name'] or (prof.name if prof else ''),
                    'blank_id': bid,
                    'blank_name': row['blank_name'] or (blank.name if blank else ''),
                    'pieces': int(row['pieces'].to_integral_value()),
                    'kg': api_decimal_str(row['kg'].quantize(Decimal('0.000001'))),
                    'unit_sale_price': sale_price,
                    'sale_unit_price': sale_price,
                }
            )
        return Response({'items': items})
