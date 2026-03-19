from django.contrib import admin
from .models import WarehouseBatch


@admin.register(WarehouseBatch)
class WarehouseBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'quantity', 'status', 'date')
    list_filter = ('status',)
