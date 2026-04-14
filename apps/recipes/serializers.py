from rest_framework import serializers

from config.fields import CleanDecimalField
from .models import PlasticProfile, Recipe, RecipeComponent


class _NullableRecipeModelSerializer(serializers.ModelSerializer):
    """Вложенный рецепт при FK=NULL не должен ронять to_representation."""

    def to_representation(self, instance):
        if instance is None:
            return None
        return super().to_representation(instance)


class PlasticProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlasticProfile
        fields = ('id', 'name', 'code')


class RecipeComponentSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True, allow_null=True)
    chemistry_id = serializers.IntegerField(read_only=True, allow_null=True)
    quantity_per_meter = CleanDecimalField(
        max_digits=14, decimal_places=6, read_only=True, coerce_to_string=True,
    )
    material_name = serializers.SerializerMethodField()
    element_name = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    raw_material_name = serializers.SerializerMethodField()
    chemistry_name = serializers.SerializerMethodField()

    def get_type(self, obj):
        if obj.type == RecipeComponent.TYPE_RAW:
            return 'raw_material'
        return 'chemistry'

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
            'quantity_per_meter', 'unit',
            'raw_material_name', 'chemistry_name',
        )


class RecipeListSerializer(serializers.ModelSerializer):
    """Список: без полного состава."""

    name = serializers.SerializerMethodField()
    recipe = serializers.CharField(read_only=True)
    profile = PlasticProfileSerializer(read_only=True)
    profile_name = serializers.SerializerMethodField()
    profile_id = serializers.IntegerField(read_only=True, allow_null=True)
    components_count = serializers.IntegerField(read_only=True)
    deletable = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = (
            'id',
            'name',
            'recipe',
            'profile_id',
            'profile',
            'profile_name',
            'base_unit',
            'components_count',
            'is_active',
            'comment',
            'deletable',
        )

    def get_name(self, obj):
        return obj.recipe

    def get_profile_name(self, obj):
        if obj.profile_id and obj.profile:
            return obj.profile.name
        return None

    def get_deletable(self, obj):
        if getattr(obj, '_block_pb', False):
            return False
        if getattr(obj, '_block_ord', False):
            return False
        if getattr(obj, '_block_rr', False):
            return False
        return True


class RecipeSerializer(_NullableRecipeModelSerializer):
    """Карточка + состав (чтение); components не пишутся через тело сериализатора."""

    components = RecipeComponentSerializer(many=True, read_only=True)
    name = serializers.SerializerMethodField()
    recipe = serializers.CharField(required=False, allow_blank=True, max_length=255)
    profile_id = serializers.PrimaryKeyRelatedField(
        queryset=PlasticProfile.objects.all(),
        source='profile',
        required=False,
        allow_null=True,
    )
    profile = PlasticProfileSerializer(read_only=True)
    output_quantity = CleanDecimalField(
        max_digits=14,
        decimal_places=4,
        required=False,
        allow_null=True,
        coerce_to_string=True,
    )
    output_unit_kind = serializers.ChoiceField(
        choices=[
            ('naming', 'Наименование'),
            ('pieces', 'Штуки'),
            ('amount', 'Количество'),
        ],
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
    deletable = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = (
            'id',
            'name',
            'recipe',
            'product',
            'profile_id',
            'profile',
            'base_unit',
            'components',
            'output_quantity',
            'output_unit_kind',
            'yield_quantity',
            'output_measure',
            'comment',
            'is_active',
            'deletable',
        )

    def get_name(self, obj):
        return obj.recipe

    def get_deletable(self, obj):
        if getattr(obj, '_block_pb', False):
            return False
        if getattr(obj, '_block_ord', False):
            return False
        if getattr(obj, '_block_rr', False):
            return False
        return True

    def validate(self, attrs):
        req = self.context.get('request')
        if req and req.method == 'POST':
            if not attrs.get('profile'):
                raise serializers.ValidationError({'profile_id': 'Укажите профиль'})
        return attrs
