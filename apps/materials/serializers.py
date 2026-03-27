from rest_framework import serializers

from .models import RawMaterial, Incoming


class RawMaterialSerializer(serializers.ModelSerializer):
    """GET/POST/PATCH /raw-materials/: id, name, unit, min_balance (null — без порога)."""

    unit = serializers.CharField(max_length=50, required=False, default='кг')
    min_balance = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False, allow_null=True
    )

    class Meta:
        model = RawMaterial
        fields = ('id', 'name', 'unit', 'min_balance')

    def validate_min_balance(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('min_balance должен быть ≥ 0')
        return value


class IncomingSerializer(serializers.ModelSerializer):
    """POST: name или material_id; опционально min_balance для карточки сырья."""

    material_id = serializers.PrimaryKeyRelatedField(
        queryset=RawMaterial.objects.all(),
        source='material',
        write_only=True,
        required=False,
    )
    name = serializers.CharField(write_only=True, required=False, allow_blank=False)
    min_balance = serializers.DecimalField(
        max_digits=14, decimal_places=4, write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Incoming
        fields = (
            'id',
            'material_id',
            'name',
            'unit',
            'quantity',
            'price_per_unit',
            'supplier',
            'date',
            'min_balance',
        )
        extra_kwargs = {
            'supplier': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        if self.instance is not None:
            return attrs
        name = attrs.get('name')
        material = attrs.get('material')
        if not name and material is None:
            raise serializers.ValidationError('Укажите name или material_id')
        return attrs

    def validate_min_balance(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('min_balance должен быть ≥ 0')
        return value

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['material_id'] = instance.material_id
        ret['name'] = instance.material.name
        return ret

    def create(self, validated_data):
        name = validated_data.pop('name', None)
        material = validated_data.pop('material', None)
        min_balance = validated_data.pop('min_balance', serializers.empty)

        if material is None:
            defaults = {'unit': validated_data.get('unit', 'кг')}
            material, _ = RawMaterial.objects.get_or_create(name=name, defaults=defaults)

        if min_balance is not serializers.empty:
            material.min_balance = min_balance
            material.save(update_fields=['min_balance'])

        validated_data['material'] = material
        return super().create(validated_data)
