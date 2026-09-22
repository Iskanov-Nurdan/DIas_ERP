from django.contrib import admin

from .models import GpPackOperation, GpPackRunAllocation, GpPackUnit, WarehouseBatch


@admin.register(WarehouseBatch)
class WarehouseBatchAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product', 'quantity', 'quality', 'inventory_form', 'status', 'date', 'source_batch',
        'packages_count', 'otk_status',
    )
    list_filter = ('status', 'inventory_form', 'quality')
    readonly_fields = ('quality', 'defect_reason')


class GpPackUnitInline(admin.TabularInline):
    model = GpPackUnit
    extra = 0


class GpPackRunAllocationInline(admin.TabularInline):
    model = GpPackRunAllocation
    extra = 0
    raw_id_fields = ('blank_production_run',)


@admin.register(GpPackOperation)
class GpPackOperationAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'blank', 'kind', 'label', 'total_pieces', 'split_mode', 'created_at')
    list_filter = ('kind', 'split_mode')
    inlines = (GpPackUnitInline, GpPackRunAllocationInline)
    raw_id_fields = ('product', 'blank', 'created_by')
