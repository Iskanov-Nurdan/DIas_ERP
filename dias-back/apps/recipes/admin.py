from django.contrib import admin
from .models import Recipe, RecipeComponent


class RecipeComponentInline(admin.TabularInline):
    model = RecipeComponent
    extra = 0


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ('recipe', 'product')
    inlines = [RecipeComponentInline]
