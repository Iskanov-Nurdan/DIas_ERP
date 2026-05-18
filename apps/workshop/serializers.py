"""Сериализаторы API цеха заготовки."""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from django.db import transaction

from apps.materials.models import RawMaterial
from apps.recipes.models import PlasticProfile

from apps.workshop.models import (
    BlankProductionRun,
    WorkshopBlank,
    WorkshopBlankCompositionLine,
    WorkshopPreparedState,
)

DEC_KG = Decimal('0.000001')


def _quantity_kg_str(value) -> str:
    from config.api_numbers import api_decimal_str

    s = api_decimal_str(value)
    return s if s is not None else '0'


class WorkshopBlankCompositionSerializer(serializers.ModelSerializer):
    """Строка состава для API: quantity_kg строкой."""

    raw_material_id = serializers.IntegerField(read_only=True)
    quantity_kg = serializers.SerializerMethodField()
    raw_material_name = serializers.CharField(source='raw_material.name', read_only=True)

    class Meta:
        model = WorkshopBlankCompositionLine
        fields = ('raw_material_id', 'quantity_kg', 'raw_material_name')

    def get_quantity_kg(self, obj):
        return _quantity_kg_str(obj.quantity_kg)


class WorkshopBlankReadSerializer(serializers.ModelSerializer):
    composition = serializers.SerializerMethodField()

    class Meta:
        model = WorkshopBlank
        fields = ('id', 'name', 'recipe_kg_per_barrel', 'plastic_profile', 'is_active', 'composition')

    def get_composition(self, obj: WorkshopBlank):
        lines = obj.composition_lines.all()
        return WorkshopBlankCompositionSerializer(lines, many=True).data


class CompositionLineInputSerializer(serializers.Serializer):
    raw_material_id = serializers.IntegerField(min_value=1)
    quantity_kg = serializers.DecimalField(max_digits=14, decimal_places=6)

    def validate_quantity_kg(self, value):
        if Decimal(str(value)) <= 0:
            raise serializers.ValidationError('Количество должно быть больше нуля.')
        return value


def _normalize_composition_lines(lines: list[dict]) -> list[dict]:
    """Проверка дубликатов сырья и существования RawMaterial; возвращает список dict с Decimal."""
    if not lines:
        raise serializers.ValidationError({'composition': 'Состав не может быть пустым.'})
    seen = set()
    out = []
    for row in lines:
        rid = int(row['raw_material_id'])
        if rid in seen:
            raise serializers.ValidationError({'composition': 'В составе не должно быть дубликатов сырья.'})
        seen.add(rid)
        qty = Decimal(str(row['quantity_kg'])).quantize(DEC_KG)
        if qty <= 0:
            raise serializers.ValidationError({'composition': 'Каждое количество должно быть больше нуля.'})
        out.append({'raw_material_id': rid, 'quantity_kg': qty})
    existing = set(
        RawMaterial.objects.filter(pk__in=seen).values_list('pk', flat=True)
    )
    missing = seen - existing
    if missing:
        raise serializers.ValidationError(
            {'composition': f'Неизвестное сырьё: {sorted(missing)}'}
        )
    return out


class WorkshopBlankCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    composition = CompositionLineInputSerializer(many=True)
    recipe_kg_per_barrel = serializers.DecimalField(
        max_digits=14, decimal_places=6, required=False, write_only=True
    )

    def validate_name(self, value):
        s = (value or '').strip()
        if not s:
            raise serializers.ValidationError('Укажите непустое наименование.')
        return s

    def validate_composition(self, value):
        return _normalize_composition_lines(value)

    def validate(self, attrs):
        attrs.pop('recipe_kg_per_barrel', None)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data['composition']
        recipe_kg = sum(x['quantity_kg'] for x in lines).quantize(DEC_KG)
        blank = WorkshopBlank.objects.create(
            name=validated_data['name'],
            recipe_kg_per_barrel=recipe_kg,
        )
        bulk = [
            WorkshopBlankCompositionLine(
                blank=blank,
                raw_material_id=x['raw_material_id'],
                quantity_kg=x['quantity_kg'],
            )
            for x in lines
        ]
        WorkshopBlankCompositionLine.objects.bulk_create(bulk)
        return blank


class WorkshopBlankPartialSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    composition = CompositionLineInputSerializer(many=True, required=False)
    recipe_kg_per_barrel = serializers.DecimalField(
        max_digits=14, decimal_places=6, required=False, write_only=True
    )

    def validate_name(self, value):
        if value is None:
            return value
        s = (value or '').strip()
        if not s:
            raise serializers.ValidationError('Укажите непустое наименование.')
        return s

    def validate_composition(self, value):
        if value is None:
            return value
        return _normalize_composition_lines(value)

    def validate(self, attrs):
        attrs.pop('recipe_kg_per_barrel', None)
        if self.partial:
            if not attrs:
                raise serializers.ValidationError('Укажите поле name и/или composition.')
        else:
            if 'name' not in attrs or 'composition' not in attrs:
                raise serializers.ValidationError('Укажите name и composition.')
        return attrs

    @transaction.atomic
    def update(self, instance: WorkshopBlank, validated_data):
        if 'name' in validated_data:
            instance.name = validated_data['name']
        if 'composition' in validated_data:
            lines = validated_data['composition']
            instance.composition_lines.all().delete()
            recipe_kg = sum(x['quantity_kg'] for x in lines).quantize(DEC_KG)
            instance.recipe_kg_per_barrel = recipe_kg
            bulk = [
                WorkshopBlankCompositionLine(
                    blank=instance,
                    raw_material_id=x['raw_material_id'],
                    quantity_kg=x['quantity_kg'],
                )
                for x in lines
            ]
            WorkshopBlankCompositionLine.objects.bulk_create(bulk)
        instance.save()
        return instance


class WorkshopPreparedAggregateSerializer(serializers.ModelSerializer):
    """Агрегат по строке каталога WorkshopBlank."""

    blank_id = serializers.IntegerField(source='pk', read_only=True)
    blank_name = serializers.CharField(source='name', read_only=True)
    barrels = serializers.SerializerMethodField()
    extra_kg = serializers.SerializerMethodField()
    total_kg = serializers.SerializerMethodField()
    from_machine_remainder_kg = serializers.SerializerMethodField()
    from_defect_kg = serializers.SerializerMethodField()
    pure_kg = serializers.SerializerMethodField()

    class Meta:
        model = WorkshopBlank
        fields = (
            'blank_id',
            'blank_name',
            'recipe_kg_per_barrel',
            'barrels',
            'extra_kg',
            'total_kg',
            'from_machine_remainder_kg',
            'from_defect_kg',
            'pure_kg',
        )

    def _prepared_or_default(self, obj: WorkshopBlank):
        try:
            ps = obj.prepared_state
        except WorkshopPreparedState.DoesNotExist:
            return 0, Decimal('0')
        return ps.barrels, ps.extra_kg

    def _dec(self, value) -> Decimal:
        if value is None:
            return Decimal('0')
        return Decimal(str(value)).quantize(DEC_KG)

    def get_barrels(self, obj):
        barrels, _ = self._prepared_or_default(obj)
        return barrels

    def get_extra_kg(self, obj):
        _, extra = self._prepared_or_default(obj)
        return extra

    def get_total_kg(self, obj):
        from apps.workshop.services import total_on_workshop

        barrels, extra = self._prepared_or_default(obj)
        return total_on_workshop(barrels, extra, Decimal(str(obj.recipe_kg_per_barrel)))

    def get_from_machine_remainder_kg(self, obj):
        return self._dec(getattr(obj, '_agg_machine_return_kg', None))

    def get_from_defect_kg(self, obj):
        return self._dec(getattr(obj, '_agg_defect_return_kg', None))

    def get_pure_kg(self, obj):
        total = Decimal(str(self.get_total_kg(obj)))
        fm = self.get_from_machine_remainder_kg(obj)
        fd = self.get_from_defect_kg(obj)
        raw = total - fm - fd
        if raw < 0:
            return Decimal('0')
        return raw.quantize(DEC_KG)


class BlankProductionRunSerializer(serializers.ModelSerializer):
    blank_id = serializers.IntegerField(read_only=True)
    blank_name = serializers.CharField(read_only=True, source='blank_name_snapshot')
    product_id = serializers.IntegerField(read_only=True)
    product_name = serializers.CharField(read_only=True, source='product_name_snapshot')
    packed_pieces = serializers.IntegerField(read_only=True, required=False, allow_null=True)
    unpacked_pieces = serializers.SerializerMethodField()
    unpacked_kg = serializers.SerializerMethodField()

    class Meta:
        model = BlankProductionRun
        fields = (
            'id',
            'created_at',
            'blank_id',
            'blank_name',
            'product_id',
            'product_name',
            'blank_total_kg',
            'blank_used_in_production_kg',
            'vat_max_kg_demo',
            'weight_kg_per_piece',
            'defect_kg',
            'good_kg',
            'good_pieces',
            'otk_recorded_at',
            'gp_accepted_at',
            'gp_accepted_pieces',
            'gp_accepted_kg',
            'gp_machine_remainder_kg',
            'status',
            'packed_pieces',
            'unpacked_pieces',
            'unpacked_kg',
        )

    def get_unpacked_pieces(self, obj):
        acc = obj.gp_accepted_pieces
        if acc is None:
            return None
        packed = int(getattr(obj, 'packed_pieces', 0) or 0)
        return max(0, int(acc) - packed)

    def get_unpacked_kg(self, obj):
        up = self.get_unpacked_pieces(obj)
        if up is None:
            return None
        if up <= 0:
            return 0.0
        wpp = Decimal(str(obj.weight_kg_per_piece))
        kg = (Decimal(up) * wpp).quantize(Decimal('0.000001'))
        return float(kg)


class BlankProductionRunCreateSerializer(serializers.Serializer):
    blank_id = serializers.PrimaryKeyRelatedField(
        queryset=WorkshopBlank.objects.all(), write_only=True, source='blank'
    )
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=PlasticProfile.objects.all(), write_only=True, source='product'
    )
    blank_total_kg = serializers.DecimalField(max_digits=14, decimal_places=6)
    blank_used_in_production_kg = serializers.DecimalField(max_digits=14, decimal_places=6)
    vat_max_kg_demo = serializers.DecimalField(max_digits=14, decimal_places=6)
    weight_kg_per_piece = serializers.DecimalField(max_digits=14, decimal_places=6)

    def validate_blank_used_in_production_kg(self, value):
        if Decimal(str(value)) <= 0:
            raise serializers.ValidationError('Масса списания с цеха должна быть положительной.')
        return value

    def validate_weight_kg_per_piece(self, value):
        if Decimal(str(value)) <= 0:
            raise serializers.ValidationError('Вес одной штуки должен быть больше нуля.')
        return value


class OtkDefectSerializer(serializers.Serializer):
    defect_kg = serializers.DecimalField(max_digits=14, decimal_places=6)

    def validate_defect_kg(self, value):
        if Decimal(str(value)) < 0:
            raise serializers.ValidationError('Брак не может быть отрицательным.')
        return value


class AcceptGpSerializer(serializers.Serializer):
    accepted_pieces = serializers.IntegerField(required=False, allow_null=True, min_value=0)
