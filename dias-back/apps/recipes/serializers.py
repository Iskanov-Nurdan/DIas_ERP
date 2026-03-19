from rest_framework import serializers
from .models import Recipe, RecipeComponent


class RecipeComponentSerializer(serializers.ModelSerializer):
    raw_material_name = serializers.SerializerMethodField()
    chemistry_name = serializers.SerializerMethodField()

    def get_raw_material_name(self, obj):
        return obj.raw_material.name if obj.raw_material_id else None

    def get_chemistry_name(self, obj):
        return obj.chemistry.name if obj.chemistry_id else None

    class Meta:
        model = RecipeComponent
        fields = ('id', 'type', 'raw_material', 'raw_material_name', 'chemistry', 'chemistry_name', 'quantity', 'unit')


class RecipeSerializer(serializers.ModelSerializer):
    components = RecipeComponentSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = ('id', 'recipe', 'product', 'components')



class RecipeAvailabilitySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    missing = serializers.ListField(child=serializers.DictField(), required=False)
