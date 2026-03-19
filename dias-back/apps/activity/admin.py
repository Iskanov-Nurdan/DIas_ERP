from django.contrib import admin
from .models import UserActivity


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'section', 'description', 'created_at')
    list_filter = ('action', 'section')
    search_fields = ('user__name', 'description')
    readonly_fields = ('user', 'action', 'section', 'description', 'created_at')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
