from rest_framework import serializers
from .models import OtkCheck


class OtkCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtkCheck
        fields = ('id', 'batch', 'accepted', 'rejected', 'reject_reason', 'inspector', 'checked_date')
