from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Role, RoleAccess, User, UserAccess
from .user_access import ensure_role_tab_access_keys, ensure_user_role_for_tab_accesses


class RoleAccessInline(admin.TabularInline):
    model = RoleAccess
    extra = 0


class UserAccessInline(admin.TabularInline):
    model = UserAccess
    extra = 0


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    inlines = [RoleAccessInline]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_system=False)

    def has_change_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'is_system', False):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'is_system', False):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        ensure_role_tab_access_keys(obj)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'name', 'role', 'is_active', 'is_staff')
    list_filter = ('is_active', 'is_staff', 'role')
    search_fields = ('email', 'name')
    ordering = ('email',)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_system=False)

    def has_change_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'is_system', False):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and getattr(obj, 'is_system', False):
            return False
        return super().has_delete_permission(request, obj)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Профиль', {'fields': ('name', 'role')}),
        ('Права', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('email', 'name', 'role', 'password1', 'password2')}),
    )
    inlines = [UserAccessInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # post_save мог назначить роль по умолчанию через ensure_user_role_for_tab_accesses
        obj.refresh_from_db()
        if not obj.role_id and ensure_user_role_for_tab_accesses(obj):
            obj.save(update_fields=['role'])
            obj.refresh_from_db()
