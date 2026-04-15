from django.contrib import admin
from .models import WarehouseBatch


@admin.register(WarehouseBatch)
class WarehouseBatchAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product', 'quantity', 'quality', 'inventory_form', 'status', 'date', 'source_batch',
        'packages_count', 'otk_status',
    )
    list_filter = ('status', 'inventory_form', 'quality')
    readonly_fields = ('quality', 'defect_reason')
