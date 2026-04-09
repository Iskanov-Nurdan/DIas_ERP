from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Role, User
from .user_access import (
    copy_role_template_to_user_if_empty,
    ensure_role_tab_access_keys,
    ensure_user_role_for_tab_accesses,
)


@receiver(post_save, sender=User)
def assign_default_role_for_new_user(sender, instance, created, raw, **kwargs):
    """
    Новый User: роль по умолчанию, шаблон RoleAccess для роли, однократное
    заполнение UserAccess с копии шаблона (не затрагивает других пользователей).
    """
    if not created or raw:
        return

    if ensure_user_role_for_tab_accesses(instance):
        User.objects.filter(pk=instance.pk).update(role_id=instance.role_id)

    if instance.role_id:
        role = Role.objects.prefetch_related('accesses').get(pk=instance.role_id)
        ensure_role_tab_access_keys(role)
        copy_role_template_to_user_if_empty(instance)
