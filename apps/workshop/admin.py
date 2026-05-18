from django.contrib import admin

from apps.workshop.models import (
    BlankProductionRun,
    WorkshopBlank,
    WorkshopBlankCompositionLine,
    WorkshopPreparedState,
)


class WorkshopBlankCompositionInline(admin.TabularInline):
    model = WorkshopBlankCompositionLine
    extra = 0
    raw_id_fields = ('raw_material',)


@admin.register(WorkshopBlank)
class WorkshopBlankAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'recipe_kg_per_barrel', 'chemistry_id')
    search_fields = ('name',)
    inlines = (WorkshopBlankCompositionInline,)


@admin.register(WorkshopPreparedState)
class WorkshopPreparedStateAdmin(admin.ModelAdmin):
    list_display = ('blank_id', 'barrels', 'extra_kg')


@admin.register(BlankProductionRun)
class BlankProductionRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'blank_name_snapshot', 'product_name_snapshot', 'status')
    list_filter = ('status',)
