"""
Маппинг operational resource → access keys UI (UserAccess).
Суперпользователь получает все события; остальные — только по своим вкладкам.
"""
from __future__ import annotations

from django.conf import settings

# resource → один или несколько ACCESS_KEYS (достаточно любого)
RESOURCE_ACCESS_KEYS: dict[str, tuple[str, ...]] = {
    # Смены
    'shift': ('shifts', 'my_shift'),
    'shift_note': ('shifts', 'my_shift'),
    'shift_complaint': ('shifts', 'my_shift'),
    'activity': ('shifts', 'my_shift'),
    # Сырьё
    'raw_material': ('materials',),
    'incoming': ('materials',),
    'material_balance': ('materials',),
    'material_writeoff': ('materials',),
    'material_movement': ('materials',),
    # Заготовка / цех
    'workshop_blank': ('materials',),
    'prepared_blank': ('materials',),
    'blank_production_run': ('materials', 'production'),
    'workshop_run': ('materials', 'production'),
    'otk': ('materials', 'warehouse', 'production'),
    'plastic_profile': ('recipes', 'materials'),
    # Производство
    'order': ('client_orders', 'orders'),
    'orders': ('client_orders', 'orders'),
    'production_batch': ('production',),
    'batch': ('production',),
    'recipe_run': ('production',),
    # Склад
    'warehouse_batch': ('warehouse',),
    'warehouse_package': ('warehouse',),
    # Касса / клиенты
    'sale': ('sales',),
    'payment': ('payments',),
    'return': ('returns',),
    'client': ('clients',),
    # Рецепты / линии
    'recipe': ('recipes',),
    'recipes': ('recipes',),
    'line': ('lines',),
    'line_history': ('lines',),
    # Брак / доработка
    'defect_record': ('defects',),
    'rework_request': ('defects',),
    # Химия
    'chemistry': ('chemistry',),
    'chemistry_element': ('chemistry',),
    'chemistry_task': ('chemistry',),
    'chemistry_batch': ('chemistry',),
    'chemistry_balance': ('chemistry',),
    # Аналитика
    'other_expense': ('analytics',),
}


def user_may_receive_resource(
    user,
    resource: str,
    *,
    user_keys: set[str] | None = None,
    is_superuser: bool | None = None,
) -> bool:
    if not user or getattr(user, 'is_anonymous', True):
        return False
    if is_superuser if is_superuser is not None else getattr(user, 'is_superuser', False):
        return True
    keys = RESOURCE_ACCESS_KEYS.get(resource)
    if not keys:
        return True
    if user_keys is None:
        user_keys = set(user.get_access_keys())
    if not user_keys:
        return False
    return bool(user_keys.intersection(keys))
