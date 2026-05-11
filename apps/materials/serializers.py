from decimal import Decimal

from rest_framework import serializers

from .models import MaterialBatch, RawMaterial


def normalize_material_unit(u: str) -> str:
    """Канон: kg | g"""
    s = (u or '').strip().lower()
    if s in ('кг', 'kg'):
        return 'kg'
    if s in ('г', 'g', 'гр'):
        return 'g'
    return s or 'kg'


def quantity_to_storage_kg(q: Decimal, material_unit: str) -> Decimal:
    """Количество прихода в единицах справочника (kg/g) → кг в партии."""
    u = normalize_material_unit(material_unit)
    q = Decimal(str(q))
    if u == 'g':
        return (q / Decimal('1000')).quantize(Decimal('0.0001'))
    return q.quantize(Decimal('0.0001'))


def kg_to_display_unit(q_kg: Decimal, material_unit: str) -> Decimal:
    """Кг (хранение партий) → количество в единице справочника сырья."""
    u = normalize_material_unit(material_unit)
    q = Decimal(str(q_kg))
    if u == 'g':
        return (q * Decimal('1000')).quantize(Decimal('0.0001'))
    return q.quantize(Decimal('0.0001'))


class RawMaterialSerializer(serializers.ModelSerializer):
    """Только справочник: POST/PATCH /raw-materials/ — без цены и поставщика."""

    unit = serializers.CharField(max_length=50, required=True)
    min_balance = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False, allow_null=True
    )

    class Meta:
        model = RawMaterial
        fields = ('id', 'name', 'unit', 'min_balance', 'is_active', 'comment')

    def validate_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Укажите название')
        return (value or '').strip()

    def validate_unit(self, value):
        u = normalize_material_unit(value)
        if u not in ('kg', 'g'):
            raise serializers.ValidationError('Допустимы только kg или g')
        return u

    def validate_min_balance(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('min_balance должен быть ≥ 0')
        return value


class MaterialBatchSerializer(serializers.ModelSerializer):
    """
    Партия прихода (Пополнить): POST /api/incoming/
    Обязательно: material_id, quantity, unit_price, received_at.
    Количество трактуется в единицах справочника сырья (kg или g).
    """

    material_id = serializers.PrimaryKeyRelatedField(
        queryset=RawMaterial.objects.filter(is_active=True),
        source='material',
        required=True,
    )
    quantity = serializers.DecimalField(
        write_only=True,
        max_digits=14,
        decimal_places=4,
        required=True,
    )
    document_number = serializers.CharField(
        source='supplier_batch_number',
        max_length=100,
        required=False,
        allow_blank=True,
    )

    class Meta:
        model = MaterialBatch
        fields = (
            'id',
            'material_id',
            'quantity_initial',
            'quantity_remaining',
            'unit',
            'unit_price',
            'total_price',
            'supplier_name',
            'document_number',
            'comment',
            'received_at',
            'created_at',
            'quantity',
        )
        extra_kwargs = {
            'supplier_name': {'required': False, 'allow_blank': True},
            'comment': {'required': False, 'allow_blank': True},
            'total_price': {'read_only': True},
            'quantity_initial': {'read_only': True},
            'quantity_remaining': {'read_only': True},
            'created_at': {'read_only': True},
            'unit': {'read_only': True},
            'received_at': {'required': True},
            'unit_price': {'required': True},
        }

    def validate(self, attrs):
        if self.instance is not None:
            return attrs
        q = attrs.get('quantity')
        if q is None or q <= 0:
            raise serializers.ValidationError({'quantity': 'Количество должно быть > 0'})
        up = attrs.get('unit_price')
        if up is not None and up < 0:
            raise serializers.ValidationError({'unit_price': 'Цена должна быть ≥ 0'})
        if attrs.get('received_at') is None:
            raise serializers.ValidationError({'received_at': 'Укажите дату прихода'})
        return attrs

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        mat = instance.material
        u = normalize_material_unit(mat.unit)
        ret['material_id'] = instance.material_id
        ret['material_name'] = mat.name
        ret['name'] = mat.name
        qi = Decimal(str(instance.quantity_initial))
        qr = Decimal(str(instance.quantity_remaining))
        ret['quantity'] = float(kg_to_display_unit(qi, mat.unit))
        ret['quantity_remaining'] = float(kg_to_display_unit(qr, mat.unit))
        ret['unit'] = u
        ret['document_number'] = instance.supplier_batch_number or ''
        ret['total_price'] = instance.total_price
        ret.pop('quantity_initial', None)
        return ret

    def create(self, validated_data):
        qty_in = validated_data.pop('quantity')
        material = validated_data['material']
        q_kg = quantity_to_storage_kg(qty_in, material.unit)
        validated_data['quantity_initial'] = q_kg
        validated_data['quantity_remaining'] = q_kg
        validated_data['unit'] = 'kg'
        return super().create(validated_data)
