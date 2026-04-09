# Generated manually for UserAccess split from RoleAccess

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('access_key', models.CharField(max_length=50, verbose_name='Ключ доступа')),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='user_accesses',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Доступ пользователя (доп. к роли)',
                'verbose_name_plural': 'Доступы пользователя',
                'db_table': 'user_access',
                'unique_together': {('user', 'access_key')},
            },
        ),
    ]
