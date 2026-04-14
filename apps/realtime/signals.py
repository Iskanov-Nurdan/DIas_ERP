"""
События только по операционным доменам (MVP). on_commit — после успешной транзакции.
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.activity.models import UserActivity
from apps.chemistry.models import ChemistryBatch, ChemistryCatalog, ChemistryStockDeduction, ChemistryTask
from apps.materials.models import MaterialBatch, MaterialStockDeduction, RawMaterial
from apps.production.models import (
    Line,
    LineHistory,
    ProductionBatch,
    RecipeRun,
    RecipeRunBatch,
    RecipeRunBatchComponent,
    Shift,
    ShiftComplaint,
    ShiftNote,
)
from apps.recipes.models import PlasticProfile, Recipe, RecipeComponent
from apps.sales.models import Sale
from apps.warehouse.models import WarehouseBatch

from .broadcast import schedule_push


def _act(created: bool) -> str:
    return 'created' if created else 'updated'


@receiver(post_save, sender=Shift)
def shift_saved(sender, instance, created, **kwargs):
    schedule_push(resource='shift', action=_act(created), entity_id=instance.pk, extra={'line_id': instance.line_id})


@receiver(post_delete, sender=Shift)
def shift_deleted(sender, instance, **kwargs):
    schedule_push(resource='shift', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=ShiftNote)
def shift_note_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='shift_note',
        action=_act(created),
        entity_id=instance.pk,
        extra={'shift_id': instance.shift_id},
    )


@receiver(post_delete, sender=ShiftNote)
def shift_note_deleted(sender, instance, **kwargs):
    schedule_push(resource='shift_note', action='deleted', entity_id=instance.pk, extra={'shift_id': instance.shift_id})


@receiver(post_save, sender=ShiftComplaint)
def shift_complaint_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='shift_complaint',
        action=_act(created),
        entity_id=instance.pk,
        extra={'shift_id': instance.shift_id},
    )


@receiver(post_delete, sender=ShiftComplaint)
def shift_complaint_deleted(sender, instance, **kwargs):
    schedule_push(
        resource='shift_complaint',
        action='deleted',
        entity_id=instance.pk,
        extra={'shift_id': instance.shift_id},
    )


@receiver(post_save, sender=Line)
def line_saved(sender, instance, created, **kwargs):
    schedule_push(resource='line', action=_act(created), entity_id=instance.pk)


@receiver(post_delete, sender=Line)
def line_deleted(sender, instance, **kwargs):
    schedule_push(resource='line', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=LineHistory)
def line_history_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='line_history',
        action=_act(created),
        entity_id=instance.pk,
        extra={'line_id': instance.line_id, 'action': instance.action},
    )


@receiver(post_delete, sender=LineHistory)
def line_history_deleted(sender, instance, **kwargs):
    schedule_push(
        resource='line_history',
        action='deleted',
        entity_id=instance.pk,
        extra={'line_id': instance.line_id},
    )


@receiver(post_save, sender=RecipeRun)
def recipe_run_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='recipe_run',
        action=_act(created),
        entity_id=instance.pk,
        extra={'line_id': instance.line_id, 'production_batch_id': instance.production_batch_id},
    )


@receiver(post_delete, sender=RecipeRun)
def recipe_run_deleted(sender, instance, **kwargs):
    schedule_push(resource='recipe_run', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=RecipeRunBatch)
def recipe_run_batch_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='recipe_run',
        action='updated',
        entity_id=instance.run_id,
        extra={'batch_id': instance.pk},
    )


@receiver(post_delete, sender=RecipeRunBatch)
def recipe_run_batch_deleted(sender, instance, **kwargs):
    schedule_push(resource='recipe_run', action='updated', entity_id=instance.run_id, extra={'batch_id': instance.pk})


@receiver(post_save, sender=RecipeRunBatchComponent)
def recipe_run_component_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='recipe_run',
        action='updated',
        entity_id=instance.batch.run_id,
        extra={'batch_id': instance.batch_id, 'component_id': instance.pk},
    )


@receiver(post_delete, sender=RecipeRunBatchComponent)
def recipe_run_component_deleted(sender, instance, **kwargs):
    rid = (
        RecipeRunBatch.objects.filter(pk=instance.batch_id)
        .values_list('run_id', flat=True)
        .first()
    )
    if rid is None:
        return
    schedule_push(
        resource='recipe_run',
        action='updated',
        entity_id=rid,
        extra={'batch_id': instance.batch_id},
    )


@receiver(post_save, sender=ProductionBatch)
def production_batch_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='production_batch',
        action=_act(created),
        entity_id=instance.pk,
        extra={'order_id': instance.order_id, 'otk_status': instance.otk_status},
    )
    schedule_push(
        resource='batch',
        action=_act(created),
        entity_id=instance.pk,
        extra={
            'profile_id': instance.profile_id,
            'recipe_id': instance.recipe_id,
            'otk_status': instance.otk_status,
        },
    )


@receiver(post_delete, sender=ProductionBatch)
def production_batch_deleted(sender, instance, **kwargs):
    schedule_push(resource='production_batch', action='deleted', entity_id=instance.pk)
    schedule_push(resource='batch', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=MaterialBatch)
def material_batch_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='incoming',
        action=_act(created),
        entity_id=instance.pk,
        extra={'material_id': instance.material_id},
    )
    schedule_push(resource='material_balance', action='changed', extra={'material_id': instance.material_id})
    schedule_push(
        resource='material_movement',
        action=_act(created),
        entity_id=instance.pk,
        extra={'material_id': instance.material_id, 'batch_id': instance.pk},
    )


@receiver(post_delete, sender=MaterialBatch)
def material_batch_deleted(sender, instance, **kwargs):
    schedule_push(resource='incoming', action='deleted', entity_id=instance.pk, extra={'material_id': instance.material_id})
    schedule_push(resource='material_balance', action='changed', extra={'material_id': instance.material_id})
    schedule_push(
        resource='material_movement',
        action='deleted',
        entity_id=instance.pk,
        extra={'material_id': instance.material_id, 'batch_id': instance.pk},
    )


@receiver(post_save, sender=MaterialStockDeduction)
def material_deduction_saved(sender, instance, created, **kwargs):
    mid = instance.batch.material_id if instance.batch_id else None
    schedule_push(
        resource='material_writeoff',
        action=_act(created),
        entity_id=instance.pk,
        extra={'material_id': mid, 'reason': instance.reason or ''},
    )
    if mid is not None:
        schedule_push(resource='material_balance', action='changed', extra={'material_id': mid})
    schedule_push(
        resource='material_movement',
        action=_act(created),
        entity_id=instance.pk,
        extra={
            'material_id': mid,
            'batch_id': instance.batch_id,
            'reason': instance.reason or '',
        },
    )


@receiver(post_delete, sender=MaterialStockDeduction)
def material_deduction_deleted(sender, instance, **kwargs):
    mid = instance.batch.material_id if instance.batch_id else None
    schedule_push(resource='material_writeoff', action='deleted', entity_id=instance.pk, extra={'material_id': mid})
    if mid is not None:
        schedule_push(resource='material_balance', action='changed', extra={'material_id': mid})
    schedule_push(
        resource='material_movement',
        action='deleted',
        entity_id=instance.pk,
        extra={'material_id': mid, 'batch_id': instance.batch_id},
    )


@receiver(post_save, sender=RawMaterial)
def raw_material_saved(sender, instance, created, **kwargs):
    schedule_push(resource='raw_material', action=_act(created), entity_id=instance.pk)


@receiver(post_delete, sender=RawMaterial)
def raw_material_deleted(sender, instance, **kwargs):
    schedule_push(resource='raw_material', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=ChemistryCatalog)
def chemistry_catalog_saved(sender, instance, created, **kwargs):
    schedule_push(resource='chemistry_element', action=_act(created), entity_id=instance.pk)
    schedule_push(resource='chemistry', action=_act(created), entity_id=instance.pk)
    schedule_push(resource='chemistry_balance', action='changed', extra={'chemistry_id': instance.pk})


@receiver(post_delete, sender=ChemistryCatalog)
def chemistry_catalog_deleted(sender, instance, **kwargs):
    schedule_push(resource='chemistry_element', action='deleted', entity_id=instance.pk)
    schedule_push(resource='chemistry', action='deleted', entity_id=instance.pk)
    schedule_push(resource='chemistry_balance', action='changed', extra={'chemistry_id': instance.pk})


@receiver(post_save, sender=ChemistryTask)
def chemistry_task_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='chemistry_task',
        action=_act(created),
        entity_id=instance.pk,
        extra={'chemistry_id': instance.chemistry_id, 'status': instance.status},
    )


@receiver(post_delete, sender=ChemistryTask)
def chemistry_task_deleted(sender, instance, **kwargs):
    schedule_push(resource='chemistry_task', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=ChemistryBatch)
def chemistry_batch_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='chemistry_batch',
        action=_act(created),
        entity_id=instance.pk,
        extra={'chemistry_id': instance.chemistry_id},
    )
    schedule_push(
        resource='chemistry_balance',
        action='changed',
        entity_id=instance.pk,
        extra={'chemistry_id': instance.chemistry_id},
    )


@receiver(post_delete, sender=ChemistryBatch)
def chemistry_batch_deleted(sender, instance, **kwargs):
    schedule_push(
        resource='chemistry_batch',
        action='deleted',
        entity_id=instance.pk,
        extra={'chemistry_id': instance.chemistry_id},
    )
    schedule_push(
        resource='chemistry_balance',
        action='deleted',
        entity_id=instance.pk,
        extra={'chemistry_id': instance.chemistry_id},
    )


@receiver(post_save, sender=ChemistryStockDeduction)
def chemistry_deduction_saved(sender, instance, created, **kwargs):
    cid = instance.batch.chemistry_id if instance.batch_id else None
    schedule_push(resource='chemistry_balance', action='changed', extra={'chemistry_id': cid})


@receiver(post_delete, sender=ChemistryStockDeduction)
def chemistry_deduction_deleted(sender, instance, **kwargs):
    cid = instance.batch.chemistry_id if instance.batch_id else None
    schedule_push(resource='chemistry_balance', action='changed', extra={'chemistry_id': cid})


@receiver(post_save, sender=PlasticProfile)
def plastic_profile_saved(sender, instance, created, **kwargs):
    schedule_push(resource='plastic_profile', action=_act(created), entity_id=instance.pk)


@receiver(post_delete, sender=PlasticProfile)
def plastic_profile_deleted(sender, instance, **kwargs):
    schedule_push(resource='plastic_profile', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=Recipe)
def recipe_saved(sender, instance, created, **kwargs):
    schedule_push(resource='recipe', action=_act(created), entity_id=instance.pk)
    schedule_push(resource='recipes', action='changed', extra={'recipe_id': instance.pk})


@receiver(post_delete, sender=Recipe)
def recipe_deleted(sender, instance, **kwargs):
    schedule_push(resource='recipe', action='deleted', entity_id=instance.pk)
    schedule_push(resource='recipes', action='changed', extra={'recipe_id': instance.pk})


@receiver(post_save, sender=RecipeComponent)
def recipe_component_saved(sender, instance, created, **kwargs):
    schedule_push(resource='recipe', action='updated', entity_id=instance.recipe_id)
    schedule_push(resource='recipes', action='changed', extra={'recipe_id': instance.recipe_id})


@receiver(post_delete, sender=RecipeComponent)
def recipe_component_deleted(sender, instance, **kwargs):
    rid = instance.recipe_id
    schedule_push(resource='recipe', action='updated', entity_id=rid)
    schedule_push(resource='recipes', action='changed', extra={'recipe_id': rid})


@receiver(post_save, sender=WarehouseBatch)
def warehouse_batch_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='warehouse_batch',
        action=_act(created),
        entity_id=instance.pk,
        extra={'status': instance.status, 'source_batch_id': instance.source_batch_id},
    )


@receiver(post_delete, sender=WarehouseBatch)
def warehouse_batch_deleted(sender, instance, **kwargs):
    schedule_push(resource='warehouse_batch', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=Sale)
def sale_saved(sender, instance, created, **kwargs):
    schedule_push(
        resource='sale',
        action=_act(created),
        entity_id=instance.pk,
        extra={'client_id': instance.client_id, 'warehouse_batch_id': instance.warehouse_batch_id},
    )


@receiver(post_delete, sender=Sale)
def sale_deleted(sender, instance, **kwargs):
    schedule_push(resource='sale', action='deleted', entity_id=instance.pk)


@receiver(post_save, sender=UserActivity)
def user_activity_saved(sender, instance, created, **kwargs):
    if not created:
        return
    schedule_push(
        resource='activity',
        action='created',
        entity_id=instance.pk,
        extra={
            'section': instance.section,
            'entity_type': instance.entity_type or '',
            'entity_id': instance.entity_id or '',
            'shift_id': instance.shift_id,
        },
    )
