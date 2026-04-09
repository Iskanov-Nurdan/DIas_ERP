# Меню UI: только UserAccess. Сливаем старые «дельты» с ключами роли в полный список на пользователя.

from django.db import migrations


def merge_legacy_accesses(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    UserAccess = apps.get_model('accounts', 'UserAccess')
    RoleAccess = apps.get_model('accounts', 'RoleAccess')

    for user in User.objects.all().iterator():
        role_keys = set()
        if user.role_id:
            role_keys = set(
                RoleAccess.objects.filter(role_id=user.role_id).values_list('access_key', flat=True)
            )
        user_keys = set(
            UserAccess.objects.filter(user_id=user.pk).values_list('access_key', flat=True)
        )
        effective = role_keys | user_keys
        UserAccess.objects.filter(user_id=user.pk).delete()
        for k in sorted(effective):
            UserAccess.objects.create(user_id=user.pk, access_key=k)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_useraccess'),
    ]

    operations = [
        migrations.RunPython(merge_legacy_accesses, noop_reverse),
    ]
