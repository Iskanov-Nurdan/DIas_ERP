"""Пул ОТК: приход с производства и учёт (профили + брак + склад)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.recipes.models import PlasticProfile
from apps.warehouse.models import WarehouseBatch
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import (
    BlankProductionRun,
    OtkAccountLine,
    OtkAccountSession,
    OtkBlankIntake,
    OtkBlankPool,
    WorkshopBlank,
)
from apps.workshop.services import _q_kg, append_kg_to_workshop_prepared
from apps.workshop.unit_cost import profile_cost_price_from_blank

POOL_EPSILON = Decimal('0.001')
DEC_KG = Decimal('0.000001')


def _profile_weight_kg(profile: PlasticProfile) -> Decimal:
    w = profile.weight_kg_per_piece
    if w is None or Decimal(str(w)) <= 0:
        raise serializers.ValidationError(
            {'lines': f'У профиля «{profile.name}» не задан weight_kg_per_piece.'}
        )
    return _q_kg(Decimal(str(w)))


@transaction.atomic
def add_otk_pool_intake(*, blank: WorkshopBlank, kg: Decimal, run: BlankProductionRun | None) -> OtkBlankPool:
    kg = _q_kg(kg)
    pool, _ = OtkBlankPool.objects.select_for_update().get_or_create(
        blank_id=blank.pk,
        defaults={'remaining_kg': Decimal('0'), 'total_intake_kg': Decimal('0')},
    )
    pool = OtkBlankPool.objects.select_for_update().get(pk=pool.pk)
    pool.remaining_kg = _q_kg(pool.remaining_kg + kg)
    pool.total_intake_kg = _q_kg(pool.total_intake_kg + kg)
    pool.version = F('version') + 1
    pool.save(update_fields=['remaining_kg', 'total_intake_kg', 'version'])
    pool.refresh_from_db()
    OtkBlankIntake.objects.create(blank_id=blank.pk, run=run, kg=kg)
    return pool


def _resolve_defect_kg(defect: dict, profiles_by_id: dict[int, PlasticProfile]) -> Decimal:
    unit = (defect.get('unit') or 'kg').strip().lower()
    value = Decimal(str(defect.get('value') or '0'))
    if value <= 0:
        return Decimal('0')
    if unit == 'kg':
        return _q_kg(value)
    if unit == 'pieces':
        profile_id = defect.get('profile_id')
        if not profile_id:
            raise serializers.ValidationError({'defect': 'Для брака в штуках укажите profile_id.'})
        prof = profiles_by_id.get(int(profile_id))
        if prof is None:
            raise serializers.ValidationError({'defect': 'Профиль брака не найден.'})
        return _q_kg(value * _profile_weight_kg(prof))
    raise serializers.ValidationError({'defect': 'unit должен быть kg или pieces.'})


def _unit_cost_for_profile(profile: PlasticProfile) -> Decimal:
    if profile.cost_price is not None:
        cpp = Decimal(str(profile.cost_price))
        if cpp > 0:
            return cpp.quantize(Decimal('0.0001'))
    return Decimal('0')


def _apply_profile_cost_from_blank(profile: PlasticProfile, blank: WorkshopBlank) -> PlasticProfile:
    new_price = profile_cost_price_from_blank(profile=profile, blank=blank)
    if new_price is not None and new_price > 0:
        PlasticProfile.objects.filter(pk=profile.pk).update(cost_price=new_price)
        profile.cost_price = new_price
    return profile


def _post_warehouse_gp_from_otk(
    *,
    profile: PlasticProfile,
    pieces: int,
    blank: WorkshopBlank,
    session: OtkAccountSession,
) -> WarehouseBatch | None:
    if pieces <= 0:
        return None
    from django.utils import timezone as tz

    return WarehouseBatch.objects.create(
        profile=profile,
        product=profile.name,
        quantity=_q_kg(Decimal(pieces)),
        cost_per_piece=_unit_cost_for_profile(profile),
        date=tz.now().date(),
        status=WarehouseBatch.STATUS_AVAILABLE,
        inventory_form=WarehouseBatch.INVENTORY_UNPACKED,
        quality=WarehouseBatch.QUALITY_GOOD,
        otk_checked_at=session.created_at,
        workshop_blank=blank,
        otk_account_session=session,
    )


def _user_or_none(user_id: int | None) -> User | None:
    if not user_id:
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


@transaction.atomic
def account_otk_blank(
    *,
    blank_id: int,
    lines: list[dict[str, Any]],
    defect: dict,
    operator_id: int | None,
    chemist_id: int | None,
    packer_id: int | None,
    comment: str,
) -> OtkAccountSession:
    try:
        blank = WorkshopBlank.objects.get(pk=blank_id, is_active=True)
    except WorkshopBlank.DoesNotExist as exc:
        raise serializers.ValidationError({'blank_id': 'Заготовка не найдена.'}) from exc

    profile_ids = [int(ln['profile_id']) for ln in lines]
    profiles = {
        p.pk: p
        for p in PlasticProfile.objects.filter(pk__in=profile_ids, is_active=True)
    }
    if len(profiles) != len(set(profile_ids)):
        raise serializers.ValidationError({'lines': 'Один или несколько профилей не найдены или неактивны.'})

    line_rows: list[tuple[PlasticProfile, int, Decimal]] = []
    consumed_kg = Decimal('0')
    for ln in lines:
        pieces = int(ln['pieces'])
        if pieces <= 0:
            raise serializers.ValidationError({'lines': 'Количество штук должно быть > 0.'})
        prof = profiles[int(ln['profile_id'])]
        w = _profile_weight_kg(prof)
        kg = _q_kg(Decimal(pieces) * w)
        consumed_kg += kg
        line_rows.append((prof, pieces, kg))

    defect_kg = _resolve_defect_kg(defect, profiles)
    consumed_kg = _q_kg(consumed_kg + defect_kg)

    try:
        pool = OtkBlankPool.objects.select_for_update().get(blank_id=blank_id)
    except OtkBlankPool.DoesNotExist as exc:
        raise serializers.ValidationError({'detail': 'Пул ОТК для этой заготовки не найден.'}) from exc

    remaining = _q_kg(pool.remaining_kg)
    if consumed_kg > remaining + POOL_EPSILON:
        raise serializers.ValidationError(
            {'detail': f'Списание {consumed_kg} кг превышает остаток пула {remaining} кг.'}
        )

    expected_version = pool.version
    updated = OtkBlankPool.objects.filter(pk=pool.pk, version=expected_version).update(
        remaining_kg=_q_kg(remaining - consumed_kg),
        version=F('version') + 1,
    )
    if updated == 0:
        raise WorkshopConflict(detail='Параллельный учёт ОТК: повторите операцию.')

    pool.refresh_from_db()
    session = OtkAccountSession.objects.create(
        blank=blank,
        consumed_kg=consumed_kg,
        defect_kg=defect_kg,
        remaining_kg_after=pool.remaining_kg,
        operator=_user_or_none(operator_id),
        chemist=_user_or_none(chemist_id),
        packer=_user_or_none(packer_id),
        comment=(comment or '')[:2000],
    )

    for prof, pieces, kg in line_rows:
        prof = _apply_profile_cost_from_blank(prof, blank)
        OtkAccountLine.objects.create(
            session=session,
            profile=prof,
            profile_name_snapshot=prof.name,
            pieces=pieces,
            kg=kg,
        )
        _post_warehouse_gp_from_otk(profile=prof, pieces=pieces, blank=blank, session=session)

    if defect_kg > 0:
        blank_locked = WorkshopBlank.objects.select_for_update().get(pk=blank.pk)
        append_kg_to_workshop_prepared(blank_locked, defect_kg)

    return session
