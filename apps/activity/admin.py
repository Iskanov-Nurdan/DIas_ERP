from django.contrib import admin

from .models import AuditOutbox, UserActivity


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'action',
        'section',
        'entity_type',
        'entity_id',
        'summary',
        'shift',
        'request_id',
        'created_at',
    )
    list_filter = ('action', 'section', 'entity_type', 'payload_version')
    search_fields = ('user__name', 'description', 'summary', 'request_id', 'entity_id')
    readonly_fields = (
        'user',
        'action',
        'section',
        'description',
        'summary',
        'created_at',
        'shift',
        'line',
        'session_open_event_id',
        'request_id',
        'entity_type',
        'entity_id',
        'payload_version',
        'payload',
        'actor_role_snapshot',
        'client_ip',
        'user_agent',
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditOutbox)
class AuditOutboxAdmin(admin.ModelAdmin):
    list_display = ('id', 'attempts', 'created_at', 'processed_at')
    readonly_fields = ('payload', 'last_error', 'attempts', 'created_at', 'processed_at')
    ordering = ('-created_at',)
