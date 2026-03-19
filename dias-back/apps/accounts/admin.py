from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Role, RoleAccess


class RoleAccessInline(admin.TabularInline):
    model = RoleAccess
    extra = 0


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    inlines = [RoleAccessInline]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'name', 'role', 'is_active', 'is_staff')
    list_filter = ('is_active', 'is_staff', 'role')
    search_fields = ('email', 'name')
    ordering = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Профиль', {'fields': ('name', 'role')}),
        ('Права', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'name', 'password1', 'password2')}),
    )
