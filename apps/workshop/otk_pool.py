"""Пул ОТК: приход с производства и учёт (профили + брак + склад)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.db.models import F
from rest_framework import serializers

from apps.accounts.models import User
from apps.recipes.models import PlasticProfile
from apps.warehouse.models import WarehouseBatch
from apps.workshop.exceptions import WorkshopConflict
from apps.workshop.models import (
    BlankProductionRun,
    OtkAccountBlankAllocation,
    OtkAccountLine,
    OtkAccountSession,
    OtkBlankIntake,
    OtkBlankPool,
    WorkshopBlank,
)
from apps.workshop.otk_errors import (
    OtkPoolInsufficient,
    OtkProfileBlankMismatch,
    OtkProfileBlankMissing,
)
from apps.workshop.otk_shift import link_otk_session_to_shift
from apps.workshop.services import _q_kg, append_kg_to_workshop_prepared
from apps.workshop.unit_cost import profile_cost_price_from_blank

POOL_EPSILON = Decimal('0.001')


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


def _parse_defect_kg(raw) -> Decimal:
    if raw in (None, ''):
        return Decimal('0')
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise serializers.ValidationError({'defect_kg': 'defect_kg должен быть числом ≥ 0.'}) from exc
    if value < 0:
        raise serializers.ValidationError({'defect_kg': 'defect_kg не может быть отрицательным.'})
    return _q_kg(value)


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


def _users_by_ids(user_ids: list[int]) -> list[User]:
    if not user_ids:
        return []
    uniq = list(dict.fromkeys(user_ids))
    found = {u.pk: u for u in User.objects.filter(pk__in=uniq, is_active=True)}
    missing = [uid for uid in uniq if uid not in found]
    if missing:
        raise serializers.ValidationError({'packer_ids': f'Пользователи не найдены: {missing}'})
    return [found[uid] for uid in uniq]


def _deduct_pool(*, blank_id: int, kg: Decimal) -> OtkBlankPool:
    try:
        pool = OtkBlankPool.objects.select_for_update().get(blank_id=blank_id)
    except OtkBlankPool.DoesNotExist as exc:
        raise serializers.ValidationError(
            {'blank_id': blank_id, 'error': 'Пул ОТК для этой заготовки не найден.'}
        ) from exc
    remaining = _q_kg(pool.remaining_kg)
    if kg > remaining + POOL_EPSILON:
        raise OtkPoolInsufficient(blank_id=blank_id, remaining_kg=remaining, required_kg=kg)
    expected_version = pool.version
    updated = OtkBlankPool.objects.filter(pk=pool.pk, version=expected_version).update(
        remaining_kg=_q_kg(remaining - kg),
        version=F('version') + 1,
    )
    if updated == 0:
        raise WorkshopConflict(detail='Параллельный учёт ОТК: повторите операцию.')
    pool.refresh_from_db()
    return pool


@transaction.atomic
def account_otk_v2(
    *,
    lines: list[dict[str, Any]],
    defect_kg: Decimal | str | None = None,
    defect_blank_id: int | None = None,
    shift_period: str,
    operator_id: int | None = None,
    chemist_id: int | None = None,
    packer_ids: list[int] | None = None,
    comment: str = '',
    enforce_blank_id: int | None = None,
) -> OtkAccountSession:
    """Учёт ОТК v2: multi-blank, defect_kg, shift_period, packer_ids."""
    if shift_period not in (OtkAccountSession.SHIFT_DAY, OtkAccountSession.SHIFT_NIGHT):
        raise serializers.ValidationError({'shift_period': 'shift_period: day | night'})

    profile_ids = [int(ln['profile_id']) for ln in lines]
    profiles = {
        p.pk: p
        for p in PlasticProfile.objects.filter(pk__in=profile_ids, is_active=True).select_related('blank')
    }
    if len(profiles) != len(set(profile_ids)):
        raise serializers.ValidationError({'lines': 'Один или несколько профилей не найдены или неактивны.'})

    defect_kg_val = _parse_defect_kg(defect_kg)
    line_rows: list[tuple[PlasticProfile, int, Decimal, int]] = []
    consumed_by_blank: dict[int, Decimal] = defaultdict(lambda: Decimal('0'))
    primary_blank_id: int | None = None

    for ln in lines:
        pieces = int(ln['pieces'])
        if pieces <= 0:
            raise serializers.ValidationError({'lines': 'Количество штук должно быть > 0.'})
        prof = profiles[int(ln['profile_id'])]
        if not prof.blank_id:
            raise OtkProfileBlankMissing(profile_id=prof.pk)
        if enforce_blank_id is not None and prof.blank_id != enforce_blank_id:
            raise OtkProfileBlankMismatch(profile_id=prof.pk, expected_blank_id=enforce_blank_id)
        blank_id = prof.blank_id
        if primary_blank_id is None:
            primary_blank_id = blank_id
        w = _profile_weight_kg(prof)
        kg = _q_kg(Decimal(pieces) * w)
        consumed_by_blank[blank_id] = _q_kg(consumed_by_blank[blank_id] + kg)
        line_rows.append((prof, pieces, kg, blank_id))

    if primary_blank_id is None:
        raise serializers.ValidationError({'lines': 'Укажите хотя бы одну строку профиля.'})

    defect_blank: WorkshopBlank | None = None
    if defect_kg_val > 0:
        dbid = defect_blank_id if defect_blank_id not in (None, '') else primary_blank_id
        try:
            defect_blank = WorkshopBlank.objects.get(pk=int(dbid), is_active=True)
        except (WorkshopBlank.DoesNotExist, TypeError, ValueError) as exc:
            raise serializers.ValidationError({'defect_blank_id': 'Заготовка для брака не найдена.'}) from exc
        consumed_by_blank[defect_blank.pk] = _q_kg(consumed_by_blank[defect_blank.pk] + defect_kg_val)

    pool_after: dict[int, Decimal] = {}
    for blank_id, need_kg in sorted(consumed_by_blank.items()):
        pool = _deduct_pool(blank_id=blank_id, kg=need_kg)
        pool_after[blank_id] = pool.remaining_kg

    primary_blank = WorkshopBlank.objects.get(pk=primary_blank_id)
    packer_users = _users_by_ids(list(packer_ids or []))
    first_packer = packer_users[0] if packer_users else None

    total_consumed = _q_kg(sum(consumed_by_blank.values(), Decimal('0')))
    session = OtkAccountSession.objects.create(
        blank=primary_blank,
        consumed_kg=total_consumed,
        defect_kg=defect_kg_val,
        defect_blank=defect_blank,
        remaining_kg_after=pool_after.get(primary_blank_id, Decimal('0')),
        shift_period=shift_period,
        operator=_user_or_none(operator_id),
        chemist=_user_or_none(chemist_id),
        packer=first_packer,
        comment=(comment or '')[:2000],
    )
    if packer_users:
        session.packers.set(packer_users)

    for blank_id, kg in consumed_by_blank.items():
        OtkAccountBlankAllocation.objects.create(
            session=session,
            blank_id=blank_id,
            consumed_kg=kg,
            remaining_kg_after=pool_after[blank_id],
        )

    blanks_by_id = {b.pk: b for b in WorkshopBlank.objects.filter(pk__in=consumed_by_blank.keys())}
    for prof, pieces, kg, blank_id in line_rows:
        blank = blanks_by_id[blank_id]
        prof = _apply_profile_cost_from_blank(prof, blank)
        OtkAccountLine.objects.create(
            session=session,
            profile=prof,
            profile_name_snapshot=prof.name,
            pieces=pieces,
            kg=kg,
        )
        _post_warehouse_gp_from_otk(profile=prof, pieces=pieces, blank=blank, session=session)

    if defect_kg_val > 0 and defect_blank is not None:
        blank_locked = WorkshopBlank.objects.select_for_update().get(pk=defect_blank.pk)
        append_kg_to_workshop_prepared(blank_locked, defect_kg_val)

    staff_ids = [operator_id, chemist_id, *[u.pk for u in packer_users]]
    link_otk_session_to_shift(session, shift_period=shift_period, user_ids=staff_ids)

    return session


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
    shift_period: str = OtkAccountSession.SHIFT_DAY,
) -> OtkAccountSession:
    """Legacy: учёт по одной заготовке в URL."""
    unit = (defect.get('unit') or 'kg').strip().lower()
    value = Decimal(str(defect.get('value') or '0'))
    defect_kg = Decimal('0')
    if value > 0:
        if unit == 'kg':
            defect_kg = _q_kg(value)
        elif unit == 'pieces':
            profile_id = defect.get('profile_id')
            if not profile_id:
                raise serializers.ValidationError({'defect': 'Для брака в штуках укажите profile_id.'})
            prof = PlasticProfile.objects.filter(pk=int(profile_id), is_active=True).first()
            if prof is None:
                raise serializers.ValidationError({'defect': 'Профиль брака не найден.'})
            defect_kg = _q_kg(value * _profile_weight_kg(prof))
        else:
            raise serializers.ValidationError({'defect': 'unit должен быть kg или pieces.'})

    packer_ids = [packer_id] if packer_id else []
    return account_otk_v2(
        lines=lines,
        defect_kg=defect_kg,
        defect_blank_id=blank_id if defect_kg > 0 else None,
        shift_period=shift_period or OtkAccountSession.SHIFT_DAY,
        operator_id=operator_id,
        chemist_id=chemist_id,
        packer_ids=packer_ids,
        comment=comment,
        enforce_blank_id=blank_id,
    )
