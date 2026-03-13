from django.contrib import admin
from .models import Line, LineHistory, Order, ProductionBatch


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(LineHistory)
class LineHistoryAdmin(admin.ModelAdmin):
    list_display = ('line', 'action', 'date', 'time', 'user')
    list_filter = ('action', 'line')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'recipe', 'line', 'quantity', 'status', 'date')
    list_filter = ('status', 'line')


@admin.register(ProductionBatch)
class ProductionBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'product', 'quantity', 'otk_status', 'date')
    list_filter = ('otk_status',)
