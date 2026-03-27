# Generated manually for audit log (field diff, shift, request_id, outbox)

import django.db.models.deletion
from django.db import migrations, models


def backfill_summary_from_description(apps, schema_editor):
    UserActivity = apps.get_model('activity', 'UserActivity')
    for row in UserActivity.objects.filter(summary='').iterator(chunk_size=500):
        row.summary = (row.description or '')[:500]
        row.save(update_fields=['summary'])


class Migration(migrations.Migration):

    dependencies = [
        ('activity', '0001_initial'),
        ('production', '0015_shift_complaint'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payload', models.JSONField(default=dict)),
                ('last_error', models.TextField(blank=True, default='')),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Очередь аудита',
                'verbose_name_plural': 'Очередь аудита',
                'db_table': 'audit_outbox',
                'ordering': ['created_at'],
            },
        ),
        migrations.AlterField(
            model_name='useractivity',
            name='action',
            field=models.CharField(
                choices=[
                    ('create', 'Создал'),
                    ('update', 'Изменил'),
                    ('delete', 'Удалил'),
                    ('restore', 'Восстановил'),
                ],
                max_length=10,
                verbose_name='Действие',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='summary',
            field=models.CharField(blank=True, default='', max_length=500, verbose_name='Краткое описание (список)'),
        ),
        migrations.RunPython(backfill_summary_from_description, migrations.RunPython.noop),
        migrations.AddField(
            model_name='useractivity',
            name='shift',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='audit_activities',
                to='production.shift',
                verbose_name='Смена',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='line',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='audit_activities',
                to='production.line',
                verbose_name='Линия',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='session_open_event_id',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='ID события открытия сессии (LineHistory)',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='request_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                max_length=64,
                verbose_name='Request / correlation id',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='entity_type',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                max_length=120,
                verbose_name='Тип сущности',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='entity_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                max_length=64,
                verbose_name='ID сущности',
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='payload_version',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Версия схемы payload'),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='payload',
            field=models.JSONField(blank=True, default=dict, verbose_name='Детализация (changes, snapshot)'),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='actor_role_snapshot',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='Роль (снимок)'),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='client_ip',
            field=models.CharField(blank=True, default='', max_length=45, verbose_name='IP клиента'),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='user_agent',
            field=models.TextField(blank=True, default='', verbose_name='User-Agent'),
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(fields=['shift', '-created_at'], name='user_activi_shift_i_idx'),
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(
                fields=['entity_type', 'entity_id', '-created_at'],
                name='user_activi_entity_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='useractivity',
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    request_id__gt='',
                    entity_type__gt='',
                    entity_id__gt='',
                ),
                fields=('request_id', 'entity_type', 'entity_id', 'action'),
                name='uniq_user_activity_idempotent_v1',
            ),
        ),
    ]
