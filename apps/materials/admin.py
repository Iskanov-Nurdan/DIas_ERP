from django.contrib import admin

from .models import MaterialBatch, MaterialStockDeduction, RawMaterial


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'min_balance', 'is_active', 'comment')
    list_filter = ('is_active',)


@admin.register(MaterialBatch)
class MaterialBatchAdmin(admin.ModelAdmin):
    list_display = (
        'received_at',
        'created_at',
        'material',
        'quantity_initial',
        'quantity_remaining',
        'unit_price',
        'supplier_name',
    )
    list_filter = ('material',)
    search_fields = ('supplier_name', 'comment', 'supplier_batch_number')
    raw_id_fields = ('material',)
    readonly_fields = ('created_at', 'total_price')


@admin.register(MaterialStockDeduction)
class MaterialStockDeductionAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'batch', 'quantity', 'line_total', 'reason', 'reference_id')
    list_filter = ('reason',)
    raw_id_fields = ('batch',)
