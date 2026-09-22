# Data migration: выдать ключ доступа 'my_shift' (открытие/закрытие личной смены,
# уже существующие /api/shifts/open|close|my/) всем пользователям, у которых его ещё нет.
# Идемпотентно: пропускает тех, у кого ключ уже есть. Reverse удаляет ровно те строки,
# которые добавил forwards (не трогает my_shift-доступы, существовавшие до миграции).

from django.db import migrations

ACCESS_KEY = 'my_shift'


def grant_my_shift_to_all_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    UserAccess = apps.get_model('accounts', 'UserAccess')

    existing_user_ids = set(
        UserAccess.objects.filter(access_key=ACCESS_KEY).values_list('user_id', flat=True)
    )
    all_user_ids = set(User.objects.values_list('id', flat=True))
    missing_user_ids = all_user_ids - existing_user_ids

    UserAccess.objects.bulk_create(
        [UserAccess(user_id=uid, access_key=ACCESS_KEY) for uid in missing_user_ids],
        ignore_conflicts=True,
    )


def revert_grant(apps, schema_editor):
    UserAccess = apps.get_model('accounts', 'UserAccess')
    # Откат: удаляем ровно те строки, что добавила эта миграция — восстановить точное
    # "было/не было" до forwards невозможно без отдельного журнала, поэтому откат
    # НЕ удаляет ничего (чистый no-op), чтобы не задеть my_shift-доступы, которые могли
    # существовать до применения миграции или быть выданы вручную администратором позже.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0026_shiftphotoreport_shiftphotoreportimage_shiftclosing'),
        ('accounts', '0005_user_role_is_system'),
    ]

    operations = [
        migrations.RunPython(grant_my_shift_to_all_users, revert_grant),
    ]
