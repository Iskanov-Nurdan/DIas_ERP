from rest_framework import serializers
from .models import RawMaterial, Incoming


class RawMaterialSerializer(serializers.ModelSerializer):
    """GET /raw-materials/: id, name, unit (строка, например \"кг\")."""
    unit = serializers.CharField(max_length=50, required=False, default='кг')

    class Meta:
        model = RawMaterial
        fields = ('id', 'name', 'unit')


class IncomingSerializer(serializers.ModelSerializer):
    name = serializers.CharField(write_only=True, required=True)
    material_name = serializers.CharField(source='material.name', read_only=True)

    class Meta:
        model = Incoming
        fields = ('id', 'name', 'material_name', 'unit', 'quantity', 'price_per_unit', 'supplier', 'date')
        extra_kwargs = {
            'supplier': {'required': False, 'allow_blank': True},
        }

    def to_representation(self, instance):
        """Возвращаем name вместо material_name"""
        ret = super().to_representation(instance)
        ret['name'] = instance.material.name
        ret.pop('material_name', None)
        return ret

    def create(self, validated_data):
        name = validated_data.pop('name')
        # Ищем или создаем материал
        material, _ = RawMaterial.objects.get_or_create(
            name=name,
            defaults={'unit': validated_data.get('unit', 'кг')}
        )
        validated_data['material'] = material
        return super().create(validated_data)
