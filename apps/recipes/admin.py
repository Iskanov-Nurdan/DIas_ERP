from django.contrib import admin
from .models import PlasticProfile, Recipe, RecipeComponent


@admin.register(PlasticProfile)
class PlasticProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'code')


class RecipeComponentInline(admin.TabularInline):
    model = RecipeComponent
    extra = 0


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ('recipe', 'product', 'profile', 'base_unit', 'is_active', 'comment', 'output_quantity', 'output_unit_kind')
    inlines = [RecipeComponentInline]
