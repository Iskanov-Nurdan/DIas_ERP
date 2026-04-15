from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_useraccess_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='is_system',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Системная'),
        ),
        migrations.AddField(
            model_name='user',
            name='is_system',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Системный пользователь'),
        ),
        migrations.AddConstraint(
            model_name='role',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_system=True),
                fields=('is_system',),
                name='role_single_system_flag',
            ),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_system=True),
                fields=('is_system',),
                name='user_single_system_flag',
            ),
        ),
    ]
