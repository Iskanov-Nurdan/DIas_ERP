# Заполнение снимков наименований по живой строке рецепта (если FK на партии уже NULL).

from django.db import migrations


def forwards(apps, schema_editor):
    RRC = apps.get_model('production', 'RecipeRunBatchComponent')
    RecipeComponent = apps.get_model('recipes', 'RecipeComponent')
    RawMaterial = apps.get_model('materials', 'RawMaterial')
    ChemistryCatalog = apps.get_model('chemistry', 'ChemistryCatalog')

    for comp in RRC.objects.filter(recipe_component_id__isnull=False).iterator(chunk_size=300):
        need_m = not (comp.material_name_snapshot or '').strip()
        need_ch = not (comp.chemistry_name_snapshot or '').strip()
        if not need_m and not need_ch:
            continue
        try:
            rc = RecipeComponent.objects.get(pk=comp.recipe_component_id)
        except RecipeComponent.DoesNotExist:
            continue
        updates = {}
        if need_m and rc.type == 'raw' and rc.raw_material_id:
            try:
                m = RawMaterial.objects.get(pk=rc.raw_material_id)
                updates['material_name_snapshot'] = (m.name or '')[:255]
            except RawMaterial.DoesNotExist:
                pass
        if need_ch and rc.type == 'chem' and rc.chemistry_id:
            try:
                ch = ChemistryCatalog.objects.get(pk=rc.chemistry_id)
                updates['chemistry_name_snapshot'] = (ch.name or '')[:255]
            except ChemistryCatalog.DoesNotExist:
                pass
        if updates:
            RRC.objects.filter(pk=comp.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0011_batch_component_name_snapshots'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
