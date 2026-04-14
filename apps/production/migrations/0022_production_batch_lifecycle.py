from django.db import migrations, models


def set_lifecycle_from_otk(apps, schema_editor):
    ProductionBatch = apps.get_model('production', 'ProductionBatch')
    for b in ProductionBatch.objects.all():
        if b.otk_status in ('accepted', 'rejected'):
            b.lifecycle_status = 'done'
            b.sent_to_otk = True
            b.in_otk_queue = False
        else:
            b.lifecycle_status = 'pending'
            b.sent_to_otk = False
            b.in_otk_queue = False
        b.save(
            update_fields=['lifecycle_status', 'sent_to_otk', 'in_otk_queue'],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0021_alter_reciperun_recipe_run_consumption_applied'),
    ]

    operations = [
        migrations.AddField(
            model_name='productionbatch',
            name='lifecycle_status',
            field=models.CharField(
                choices=[('pending', 'Производство'), ('otk', 'Очередь ОТК'), ('done', 'Завершено')],
                default='pending',
                max_length=20,
                verbose_name='Этап жизненного цикла',
            ),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='sent_to_otk',
            field=models.BooleanField(default=False, verbose_name='Отправлено в ОТК'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='in_otk_queue',
            field=models.BooleanField(default=False, verbose_name='В очереди ОТК'),
        ),
        migrations.AddField(
            model_name='productionbatch',
            name='otk_submitted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Отправлено в ОТК'),
        ),
        migrations.RunPython(set_lifecycle_from_otk, migrations.RunPython.noop),
    ]
