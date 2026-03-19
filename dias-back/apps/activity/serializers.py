from rest_framework import serializers
from .models import UserActivity


class UserActivitySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.name', read_only=True, default=None)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = UserActivity
        fields = ('id', 'user', 'user_name', 'action', 'action_display', 'section', 'description', 'created_at')
