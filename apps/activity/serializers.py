from rest_framework import serializers

from .models import UserActivity


class UserActivitySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.name', read_only=True, default=None)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    module = serializers.CharField(source='section', read_only=True)
    occurred_at = serializers.DateTimeField(source='created_at', read_only=True)
    has_detail = serializers.SerializerMethodField()

    class Meta:
        model = UserActivity
        fields = (
            'id',
            'user',
            'user_name',
            'action',
            'action_display',
            'section',
            'module',
            'description',
            'summary',
            'created_at',
            'occurred_at',
            'shift_id',
            'line_id',
            'session_open_event_id',
            'request_id',
            'entity_type',
            'entity_id',
            'payload_version',
            'payload',
            'actor_role_snapshot',
            'client_ip',
            'user_agent',
            'has_detail',
        )

    def get_has_detail(self, obj):
        if getattr(obj, 'payload_version', 0) >= 1:
            return True
        pl = getattr(obj, 'payload', None) or {}
        return bool(pl.get('changes'))
