from rest_framework import serializers
from django.utils import timezone
from .models import Client, Sale, Shipment


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ('id', 'name', 'contact', 'phone', 'inn', 'address')


class SaleSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True)
    order_number = serializers.CharField(required=False, allow_blank=True)
    date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Sale
        fields = ('id', 'order_number', 'client', 'client_name', 'product', 'quantity', 'price', 'date', 'comment')

    def create(self, validated_data):
        # Автоматическая генерация order_number, если не указан
        if not validated_data.get('order_number'):
            today = timezone.now().date()
            year = today.year
            # Получаем последний номер заказа за текущий год
            last_sale = Sale.objects.filter(
                order_number__startswith=f'ORD-{year}-'
            ).order_by('-order_number').first()
            
            if last_sale:
                try:
                    last_number = int(last_sale.order_number.split('-')[-1])
                    new_number = last_number + 1
                except (ValueError, IndexError):
                    new_number = 1
            else:
                new_number = 1
            
            validated_data['order_number'] = f'ORD-{year}-{new_number:03d}'
        
        # Автоматическая установка даты, если не указана
        if not validated_data.get('date'):
            validated_data['date'] = timezone.now().date()
        
        return super().create(validated_data)


class ShipmentSerializer(serializers.ModelSerializer):
    sale_id = serializers.IntegerField(write_only=True, required=True)
    client_name = serializers.CharField(source='sale.client.name', read_only=True)
    product_name = serializers.CharField(source='sale.product', read_only=True)

    class Meta:
        model = Shipment
        fields = ('id', 'sale_id', 'client_name', 'product_name', 'quantity', 'status',
                  'shipment_date', 'delivery_date', 'address', 'comment')
        extra_kwargs = {
            'address': {'required': False, 'allow_blank': True},
            'comment': {'required': False, 'allow_blank': True},
        }

    def to_representation(self, instance):
        """Возвращаем sale_id в ответе"""
        ret = super().to_representation(instance)
        ret['sale_id'] = instance.sale_id
        return ret

    def create(self, validated_data):
        sale_id = validated_data.pop('sale_id')
        validated_data['sale_id'] = sale_id
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # sale_id не меняется при обновлении
        validated_data.pop('sale_id', None)
        return super().update(instance, validated_data)
