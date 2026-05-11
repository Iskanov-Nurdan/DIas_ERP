from rest_framework import serializers
from .models import OtkCheck


class OtkCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtkCheck
        fields = (
            'id', 'batch', 'profile', 'pieces', 'length_per_piece', 'total_meters',
            'check_status', 'accepted', 'rejected', 'reject_reason', 'comment',
            'inspector', 'checked_date',
        )
