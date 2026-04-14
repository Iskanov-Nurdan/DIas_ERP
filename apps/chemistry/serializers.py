from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.materials.models import RawMaterial
from apps.materials.serializers import kg_to_display_unit

from .fifo import chemistry_stock_kg
from .models import (
    ChemistryCatalog,
    ChemistryRecipe,
    ChemistryTask,
    ChemistryBatch,
)


class ChemistryRecipeLineSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True)
    material_name = serializers.CharField(source='raw_material.name', read_only=True)
    raw_material_name = serializers.CharField(source='raw_material.name', read_only=True)

    class Meta:
        model = ChemistryRecipe
        fields = (
            'id',
            'raw_material',
            'material_id',
            'material_name',
            'raw_material_name',
            'quantity_per_unit',
        )


class ChemistryCatalogListSerializer(serializers.ModelSerializer):
    """Список справочника: без полного состава, с флагами для UI."""

    has_batches = serializers.SerializerMethodField()
    batches_count = serializers.IntegerField(read_only=True)
    balance = serializers.SerializerMethodField()
    deletable = serializers.SerializerMethodField()

    class Meta:
        model = ChemistryCatalog
        fields = (
            'id',
            'name',
            'unit',
            'min_balance',
            'is_active',
            'comment',
            'has_batches',
            'batches_count',
            'balance',
            'deletable',
        )

    def get_has_batches(self, obj):
        return (getattr(obj, 'batches_count', 0) or 0) > 0

    def get_balance(self, obj):
        b = getattr(obj, 'balance', None)
        if b is None:
            b = chemistry_stock_kg(obj.pk)
        else:
            b = Decimal(str(b))
        return float(kg_to_display_unit(b, obj.unit))

    def get_deletable(self, obj):
        if (getattr(obj, 'batches_count', 0) or 0) > 0:
            return False
        if getattr(obj, '_has_recipe_ref', False):
            return False
        if getattr(obj, '_has_run_ref', False):
            return False
        return True


class ChemistryCatalogSerializer(serializers.ModelSerializer):
    """Справочник химии + состав (на 1 кг выпуска); PATCH карточки без recipe_lines."""

    recipe_lines = ChemistryRecipeLineSerializer(many=True, read_only=True)
    unit = serializers.CharField(max_length=50, required=False, default='kg')

    class Meta:
        model = ChemistryCatalog
        fields = ('id', 'name', 'unit', 'min_balance', 'is_active', 'comment', 'recipe_lines')

    def validate_unit(self, value):
        u = (value or 'kg').strip().lower()
        if u in ('кг', 'kg'):
            return 'kg'
        if u in ('г', 'g'):
            return 'g'
        raise serializers.ValidationError('Допустимы kg или g')

    def validate_min_balance(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('min_balance ≥ 0')
        return value

    def _lines_from_request(self):
        request = self.context.get('request')
        if not request:
            return None
        body = getattr(request, 'data', None) or {}
        if 'recipe_lines' in body:
            return body.get('recipe_lines')
        if 'compositions' in body:
            return body.get('compositions')
        return None

    def _validate_and_build_lines(self, lines_data):
        if not isinstance(lines_data, list):
            raise serializers.ValidationError({'recipe_lines': 'Ожидается массив'})
        out = []
        for item in lines_data:
            rid = item.get('raw_material_id') or item.get('material_id')
            if rid is None:
                continue
            if not RawMaterial.objects.filter(pk=rid).exists():
                raise serializers.ValidationError(
                    {'recipe_lines': f'Сырьё id={rid} не найдено'}
                )
            qpu = item.get('quantity_per_unit')
            if qpu is None:
                raise serializers.ValidationError(
                    {'recipe_lines': 'Укажите quantity_per_unit для каждой строки'}
                )
            qd = Decimal(str(qpu))
            if qd < 0:
                raise serializers.ValidationError(
                    {'recipe_lines': 'quantity_per_unit должно быть ≥ 0'}
                )
            out.append((rid, qd))
        return out

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        body = getattr(request, 'data', None) or {} if request else {}
        lines_data = body.get('recipe_lines')
        if lines_data is None:
            lines_data = body.get('compositions') or []
        catalog = ChemistryCatalog.objects.create(**validated_data)
        pairs = self._validate_and_build_lines(lines_data)
        for rid, qd in pairs:
            ChemistryRecipe.objects.create(
                chemistry=catalog,
                raw_material_id=rid,
                quantity_per_unit=qd,
            )
        return catalog

    @transaction.atomic
    def update(self, instance, validated_data):
        request = self.context.get('request')
        if not request:
            return super().update(instance, validated_data)
        body = getattr(request, 'data', None) or {}
        keys = set(body.keys())

        if keys == {'recipe_lines'}:
            pairs = self._validate_and_build_lines(body.get('recipe_lines') or [])
            instance.recipe_lines.all().delete()
            for rid, qd in pairs:
                ChemistryRecipe.objects.create(
                    chemistry=instance,
                    raw_material_id=rid,
                    quantity_per_unit=qd,
                )
            return instance

        if 'recipe_lines' in keys:
            raise serializers.ValidationError(
                {
                    'recipe_lines': 'Состав обновляется отдельным PATCH только с полем recipe_lines',
                }
            )

        return super().update(instance, validated_data)


class ChemistryProduceSerializer(serializers.Serializer):
    """POST .../produce/ — выпуск химии (quantity в единицах карточки)."""

    chemistry_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=4)
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_quantity(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError('Количество должно быть > 0')
        return value


class ChemistryTaskSerializer(serializers.ModelSerializer):
    chemistry_name = serializers.CharField(source='chemistry.name', read_only=True)

    class Meta:
        model = ChemistryTask
        fields = ('id', 'name', 'status', 'deadline', 'chemistry', 'chemistry_name', 'quantity', 'unit', 'created_at')

    def validate_chemistry(self, value):
        if isinstance(value, (list, tuple)):
            raise serializers.ValidationError('Ожидается один ID химии')
        return value

    def validate_quantity(self, value):
        if isinstance(value, (list, tuple)):
            raise serializers.ValidationError('Ожидается одно число')
        return value


class ChemistryBatchSerializer(serializers.ModelSerializer):
    chemistry_id = serializers.IntegerField(read_only=True)
    chemistry_name = serializers.CharField(source='chemistry.name', read_only=True)
    unit = serializers.CharField(source='chemistry.unit', read_only=True)
    produced_by_name = serializers.SerializerMethodField()
    produced_at = serializers.DateTimeField(source='created_at', read_only=True)
    unit_cost = serializers.DecimalField(
        source='cost_per_unit', max_digits=16, decimal_places=4, read_only=True, coerce_to_string=False
    )
    total_cost = serializers.DecimalField(
        source='cost_total', max_digits=16, decimal_places=2, read_only=True, coerce_to_string=False
    )

    class Meta:
        model = ChemistryBatch
        fields = (
            'id',
            'chemistry_id',
            'chemistry_name',
            'quantity_produced',
            'quantity_remaining',
            'unit',
            'cost_total',
            'cost_per_unit',
            'unit_cost',
            'total_cost',
            'created_at',
            'produced_at',
            'produced_by',
            'produced_by_name',
            'comment',
            'source_task',
        )
        read_only_fields = fields

    def get_produced_by_name(self, obj):
        u = obj.produced_by
        if u is None:
            return None
        return (getattr(u, 'get_full_name', lambda: '')() or '').strip() or getattr(u, 'username', None)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        mat = instance.chemistry
        qp = kg_to_display_unit(Decimal(str(instance.quantity_produced)), mat.unit)
        qr = kg_to_display_unit(Decimal(str(instance.quantity_remaining)), mat.unit)
        ret['quantity_produced'] = float(qp)
        ret['quantity_remaining'] = float(qr)
        return ret
