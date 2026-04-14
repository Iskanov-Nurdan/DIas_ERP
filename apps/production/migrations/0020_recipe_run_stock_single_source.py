# Generated manually — единый источник списания: ProductionBatch (batch_stock).

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    from apps.chemistry.fifo import reverse_chemistry_deductions
    from apps.materials.fifo import reverse_stock_deductions
    from apps.production.batch_stock import apply_production_batch_stock_and_cost, reverse_production_batch_stock
    from apps.production.models import ProductionBatch, RecipeRun
    from apps.recipes.models import Recipe

    for run in RecipeRun.objects.filter(recipe_run_consumption_applied=True):
        reverse_stock_deductions('recipe_run', run.pk)
        reverse_chemistry_deductions('recipe_run', run.pk)
    RecipeRun.objects.filter(recipe_run_consumption_applied=True).update(recipe_run_consumption_applied=False)

    for run in RecipeRun.objects.exclude(production_batch_id__isnull=True):
        pb = ProductionBatch.objects.filter(pk=run.production_batch_id).select_related('recipe').first()
        if not pb or pb.otk_status != ProductionBatch.OTK_PENDING:
            continue
        rec = Recipe.objects.filter(pk=pb.recipe_id).first() if pb.recipe_id else None
        reverse_production_batch_stock(
            batch_id=pb.pk,
            recipe=rec,
            total_meters=Decimal(str(pb.total_meters)),
        )
        pb.refresh_from_db()
        apply_production_batch_stock_and_cost(pb)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0019_plastic_batch_and_shift_status'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
