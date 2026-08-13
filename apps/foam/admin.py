from django.contrib import admin

from .models import (
    FoamDensityGrade,
    FoamGpOperation,
    FoamGpStock,
    FoamProductionRun,
    FoamRawLot,
    FoamSale,
    FoamSaleLine,
)


@admin.register(FoamDensityGrade)
class FoamDensityGradeAdmin(admin.ModelAdmin):
    list_display = ('code', 'min_kg_m3', 'max_kg_m3')
    search_fields = ('code',)


@admin.register(FoamRawLot)
class FoamRawLotAdmin(admin.ModelAdmin):
    list_display = ('lot_number', 'material_name', 'supplier', 'bag_weight_kg', 'remaining_kg', 'received_at')
    search_fields = ('lot_number', 'material_name', 'supplier')
    list_filter = ('received_at',)


@admin.register(FoamProductionRun)
class FoamProductionRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'lot', 'output_format', 'grade', 'input_kg', 'output_qty', 'produced_at', 'operator')
    list_filter = ('output_format', 'produced_at')
    autocomplete_fields = ('lot',)


@admin.register(FoamGpStock)
class FoamGpStockAdmin(admin.ModelAdmin):
    list_display = ('id', 'output_format', 'grade', 'thickness_cm', 'qty')
    list_filter = ('output_format',)


@admin.register(FoamGpOperation)
class FoamGpOperationAdmin(admin.ModelAdmin):
    list_display = ('id', 'kind', 'output_format', 'grade', 'thickness_cm', 'qty', 'created_at', 'ref')
    list_filter = ('kind', 'output_format', 'created_at')


class FoamSaleLineInline(admin.TabularInline):
    model = FoamSaleLine
    extra = 0


@admin.register(FoamSale)
class FoamSaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'client', 'sale_date', 'total_amount', 'paid_amount', 'payment_status')
    list_filter = ('payment_status', 'sale_date')
    search_fields = ('client',)
    inlines = [FoamSaleLineInline]
