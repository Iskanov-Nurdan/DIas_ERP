from django.contrib import admin

from .models import (
    ChemistryCatalog,
    ChemistryRecipe,
    ChemistryTask,
    ChemistryTaskElement,
    ChemistryBatch,
    ChemistryStockDeduction,
)


class ChemistryRecipeInline(admin.TabularInline):
    model = ChemistryRecipe
    extra = 0
    raw_id_fields = ('raw_material',)


@admin.register(ChemistryCatalog)
class ChemistryCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'min_balance', 'is_active', 'comment')
    list_filter = ('is_active',)
    inlines = [ChemistryRecipeInline]


@admin.register(ChemistryBatch)
class ChemistryBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'chemistry', 'quantity_remaining', 'cost_total', 'cost_per_unit')
    list_filter = ('chemistry',)
    raw_id_fields = ('chemistry', 'produced_by', 'source_task')
    readonly_fields = ('cost_total', 'cost_per_unit', 'created_at')


@admin.register(ChemistryStockDeduction)
class ChemistryStockDeductionAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'batch', 'quantity', 'line_total', 'reason', 'reference_id')
    list_filter = ('reason',)
    raw_id_fields = ('batch',)


@admin.register(ChemistryTask)
class ChemistryTaskAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'chemistry', 'quantity', 'deadline')


@admin.register(ChemistryTaskElement)
class ChemistryTaskElementAdmin(admin.ModelAdmin):
    list_display = ('task', 'chemistry', 'quantity')
