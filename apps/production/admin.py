from django.contrib import admin
from .models import (
    Line,
    LineHistory,
    Order,
    ProductionBatch,
    RecipeRun,
    RecipeRunBatch,
    RecipeRunBatchComponent,
    ShiftComplaint,
)


@admin.register(ShiftComplaint)
class ShiftComplaintAdmin(admin.ModelAdmin):
    list_display = ('id', 'author', 'shift', 'created_at')
    list_filter = ('created_at',)
    raw_id_fields = ('author', 'shift')
    filter_horizontal = ('mentioned_users',)


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(LineHistory)
class LineHistoryAdmin(admin.ModelAdmin):
    list_display = ('line', 'action', 'date', 'time', 'height', 'width', 'angle_deg', 'user')
    list_filter = ('action', 'line')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'recipe', 'line', 'quantity', 'status', 'date')
    list_filter = ('status', 'line')


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'product', 'quantity', 'otk_status', 'date')
    list_filter = ('otk_status',)


class RecipeRunBatchInline(admin.TabularInline):
    model = RecipeRunBatch
    extra = 0


@admin.register(RecipeRunBatchComponent)
class RecipeRunBatchComponentAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch', 'raw_material', 'chemistry', 'quantity', 'unit')
    list_filter = ('batch__run',)


@admin.register(RecipeRun)
class RecipeRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipe', 'line', 'production_batch', 'created_at')
    list_filter = ('line',)
    inlines = [RecipeRunBatchInline]
