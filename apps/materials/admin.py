from django.contrib import admin
from .models import RawMaterial, Incoming, MaterialWriteoff


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'min_balance')


@admin.register(Incoming)
class IncomingAdmin(admin.ModelAdmin):
    list_display = ('date', 'material', 'quantity', 'unit', 'batch', 'supplier')
    list_filter = ('material', 'date')


@admin.register(MaterialWriteoff)
class MaterialWriteoffAdmin(admin.ModelAdmin):
    list_display = ('material', 'quantity', 'unit', 'reason', 'reference_id', 'created_at')
    list_filter = ('reason',)
