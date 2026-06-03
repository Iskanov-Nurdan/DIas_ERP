"""Создание партии blank-production-run: только списание кг заготовки с цеха (без повторного списания сырья)."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.workshop.models import BlankProductionRun, WorkshopBlank
from apps.workshop.services import deduct_blank_from_workshop, _q_kg


@transaction.atomic
def create_run_deduct_workshop_only(
    *,
    blank: WorkshopBlank,
    product=None,
    validated: dict,
    production_batch=None,
    order_line=None,
    client_request=None,
) -> BlankProductionRun:
    """
    Сырьё по составу заготовки здесь не трогаем — оно уже списано при приготовлении (+бочка и т.д.).
    Только WorkshopPreparedState (бочки + extra_kg) по blank_used_in_production_kg.
    """
    used = _q_kg(Decimal(str(validated['blank_used_in_production_kg'])))
    blank_locked = WorkshopBlank.objects.select_for_update().get(pk=blank.pk)

    deduct_blank_from_workshop(blank_locked, used)

    from apps.workshop.otk_pool import add_otk_pool_intake

    run = BlankProductionRun.objects.create(
        blank=blank_locked,
        blank_name_snapshot=blank_locked.name,
        product=product,
        product_name_snapshot=(product.name if product else ''),
        blank_total_kg=validated['blank_total_kg'],
        blank_used_in_production_kg=used,
        vat_max_kg_demo=validated['vat_max_kg_demo'],
        weight_kg_per_piece=validated.get('weight_kg_per_piece'),
        status=BlankProductionRun.STATUS_IN_PRODUCTION,
        production_batch=production_batch,
        order_line=order_line,
        client_request=client_request,
    )
    add_otk_pool_intake(blank=blank_locked, kg=used, run=run)
    return run
