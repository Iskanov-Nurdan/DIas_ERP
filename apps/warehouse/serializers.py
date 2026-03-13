from rest_framework import serializers
from .models import WarehouseBatch


class WarehouseBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarehouseBatch
        fields = ('id', 'product', 'quantity', 'status', 'date', 'source_batch')
