from rest_framework import serializers

from config.fields import CleanDecimalField
from .models import Recipe, RecipeComponent


class _NullableRecipeModelSerializer(serializers.ModelSerializer):
    """Вложенный рецепт при FK=NULL не должен ронять to_representation."""

    def to_representation(self, instance):
        if instance is None:
            return None
        return super().to_representation(instance)


class RecipeComponentSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True, allow_null=True)
    chemistry_id = serializers.IntegerField(read_only=True, allow_null=True)
    quantity = CleanDecimalField(
        max_digits=14, decimal_places=4, read_only=True, coerce_to_string=True,
    )
    material_name = serializers.SerializerMethodField()
    element_name = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    raw_material_name = serializers.SerializerMethodField()
    chemistry_name = serializers.SerializerMethodField()

    def get_material_name(self, obj):
        return obj.raw_material.name if obj.raw_material_id else None

    def get_element_name(self, obj):
        return obj.chemistry.name if obj.chemistry_id else None

    def get_name(self, obj):
        if obj.raw_material_id:
            return obj.raw_material.name
        if obj.chemistry_id:
            return obj.chemistry.name
        return None

    def get_raw_material_name(self, obj):
        return self.get_material_name(obj)

    def get_chemistry_name(self, obj):
        return self.get_element_name(obj)

    class Meta:
        model = RecipeComponent
        fields = (
            'id', 'type',
            'material_id', 'material_name',
            'chemistry_id', 'element_name',
            'name',
            'quantity', 'unit',
            'raw_material_name', 'chemistry_name',
        )


class RecipeSerializer(_NullableRecipeModelSerializer):
    components = RecipeComponentSerializer(many=True, read_only=True)
    output_quantity = CleanDecimalField(
        max_digits=14,
        decimal_places=4,
        required=False,
        allow_null=True,
        coerce_to_string=True,
    )
    output_unit_kind = serializers.ChoiceField(
        choices=Recipe.OUTPUT_KIND_CHOICES,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    yield_quantity = CleanDecimalField(
        max_digits=14,
        decimal_places=4,
        read_only=True,
        allow_null=True,
        coerce_to_string=True,
        source='output_quantity',
    )
    output_measure = serializers.CharField(
        read_only=True,
        allow_null=True,
        allow_blank=True,
        source='output_unit_kind',
    )

    class Meta:
        model = Recipe
        fields = (
            'id', 'recipe', 'product', 'components',
            'output_quantity', 'output_unit_kind',
            'yield_quantity', 'output_measure',
        )
