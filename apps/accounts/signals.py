from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User
from .user_access import ensure_user_role_for_tab_accesses, seed_full_user_access_if_empty


@receiver(post_save, sender=User)
def assign_default_role_for_new_user(sender, instance, created, raw, **kwargs):
    """
    Новый User: при необходимости роль по умолчанию (справочник);
    вкладки — полный ACCESS_KEYS в UserAccess, без привязки к роли.
    """
    if not created or raw:
        return
    if getattr(instance, 'is_system', False):
        return

    if ensure_user_role_for_tab_accesses(instance):
        User.objects.filter(pk=instance.pk).update(role_id=instance.role_id)

    seed_full_user_access_if_empty(instance)
