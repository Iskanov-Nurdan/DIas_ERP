# Generated manually — снимки наименований для строк партий и догонка снимков запуска из заказа ОТК.

from django.db import migrations, models


def backfill_component_snapshots(apps, schema_editor):
    RecipeRunBatchComponent = apps.get_model('production', 'RecipeRunBatchComponent')
    RawMaterial = apps.get_model('materials', 'RawMaterial')
    ChemistryCatalog = apps.get_model('chemistry', 'ChemistryCatalog')

    for comp in RecipeRunBatchComponent.objects.filter(raw_material_id__isnull=False).iterator(chunk_size=500):
        if (comp.material_name_snapshot or '').strip():
            continue
        try:
            m = RawMaterial.objects.get(pk=comp.raw_material_id)
            RecipeRunBatchComponent.objects.filter(pk=comp.pk).update(
                material_name_snapshot=(m.name or '')[:255],
            )
        except RawMaterial.DoesNotExist:
            pass

    for comp in RecipeRunBatchComponent.objects.filter(chemistry_id__isnull=False).iterator(chunk_size=500):
        if (comp.chemistry_name_snapshot or '').strip():
            continue
        try:
            ch = ChemistryCatalog.objects.get(pk=comp.chemistry_id)
            RecipeRunBatchComponent.objects.filter(pk=comp.pk).update(
                chemistry_name_snapshot=(ch.name or '')[:255],
            )
        except ChemistryCatalog.DoesNotExist:
            pass


def backfill_recipe_run_from_order(apps, schema_editor):
    RecipeRun = apps.get_model('production', 'RecipeRun')
    ProductionBatch = apps.get_model('production', 'ProductionBatch')

    for run in RecipeRun.objects.filter(production_batch_id__isnull=False).iterator(chunk_size=200):
        need_r = not run.recipe_id and not (run.recipe_name_snapshot or '').strip()
        need_l = not run.line_id and not (run.line_name_snapshot or '').strip()
        if not need_r and not need_l:
            continue
        try:
            batch = ProductionBatch.objects.select_related('order', 'order__line').get(pk=run.production_batch_id)
        except ProductionBatch.DoesNotExist:
            continue
        order = batch.order
        updates = {}
        if need_r:
            snap = (getattr(order, 'recipe_name_snapshot', None) or '').strip()
            if snap:
                updates['recipe_name_snapshot'] = snap[:255]
            elif (order.product or '').strip():
                updates['recipe_name_snapshot'] = (order.product or '')[:255]
        if need_l:
            lsnap = (getattr(order, 'line_name_snapshot', None) or '').strip()
            if lsnap:
                updates['line_name_snapshot'] = lsnap[:255]
            elif getattr(order, 'line_id', None):
                try:
                    ln = order.line
                    updates['line_name_snapshot'] = (ln.name or '')[:255]
                except Exception:
                    pass
        if updates:
            RecipeRun.objects.filter(pk=run.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0010_recipe_snapshots_set_null'),
    ]

    operations = [
        migrations.AddField(
            model_name='reciperunbatchcomponent',
            name='material_name_snapshot',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Сырьё (снимок наименования)',
            ),
        ),
        migrations.AddField(
            model_name='reciperunbatchcomponent',
            name='chemistry_name_snapshot',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
                verbose_name='Хим. элемент (снимок наименования)',
            ),
        ),
        migrations.RunPython(backfill_component_snapshots, migrations.RunPython.noop),
        migrations.RunPython(backfill_recipe_run_from_order, migrations.RunPython.noop),
    ]
