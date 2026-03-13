from django.contrib import admin
from .models import ChemistryCatalog, ChemistryComposition, ChemistryTask, ChemistryTaskElement, ChemistryStock


class ChemistryCompositionInline(admin.TabularInline):
    model = ChemistryComposition
    extra = 0


@admin.register(ChemistryCatalog)
class ChemistryCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit')
    inlines = [ChemistryCompositionInline]


class ChemistryTaskElementInline(admin.TabularInline):
    model = ChemistryTaskElement
    extra = 0


@admin.register(ChemistryTask)
class ChemistryTaskAdmin(admin.ModelAdmin):
    list_display = ('name', 'chemistry', 'quantity', 'status', 'deadline', 'created_at')
    list_filter = ('status', 'chemistry')
    inlines = [ChemistryTaskElementInline]


@admin.register(ChemistryStock)
class ChemistryStockAdmin(admin.ModelAdmin):
    list_display = ('chemistry', 'quantity', 'unit', 'updated_at')
