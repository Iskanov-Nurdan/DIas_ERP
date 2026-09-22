from decimal import Decimal

from rest_framework import serializers

from config.fields import CleanDecimalField
from .models import PlasticProfile, Recipe, RecipeComponent
from .profile_cost import serialize_profile_cost_price
from .profile_pricing import (
    EXTRA_FIELD_NAMES,
    READ_ONLY_PRICING_FIELDS,
    serialize_other_expenses_total,
    serialize_sale_unit_price,
)
from .profile_policy import generate_plastic_profile_code, plastic_profile_deletable

from apps.workshop.models import WorkshopBlank


def _profile_extra_fields_meta():
    return tuple(EXTRA_FIELD_NAMES)


class _NullableRecipeModelSerializer(serializers.ModelSerializer):
    """Вложенный рецепт при FK=NULL не должен ронять to_representation."""

    def to_representation(self, instance):
        if instance is None:
            return None
        return super().to_representation(instance)


class _PlasticProfilePricingMixin:
    """Валидация blank_id и extra_* (поля объявлены на ModelSerializer)."""

    def _validate_extra_non_negative(self, attrs):
        for name in EXTRA_FIELD_NAMES:
            if name not in attrs:
                continue
            if Decimal(str(attrs[name])) < 0:
                raise serializers.ValidationError({name: 'Значение должно быть ≥ 0.'})

    def _validate_blank(self, attrs, *, required: bool):
        blank = attrs.get('blank')
        instance = getattr(self, 'instance', None)
        if blank is None and 'blank' not in attrs:
            if required and instance is None:
                raise serializers.ValidationError({'blank_id': 'Укажите заготовку (blank_id).'})
            return
        if blank is None:
            raise serializers.ValidationError({'blank_id': 'Укажите активную заготовку.'})
        if not blank.is_active:
            raise serializers.ValidationError({'blank_id': 'Заготовка не найдена или неактивна.'})


class PlasticProfileSerializer(_PlasticProfilePricingMixin, serializers.ModelSerializer):
    deletable = serializers.SerializerMethodField()
    code = serializers.CharField(max_length=100, required=False, allow_blank=True)
    weight_kg_per_piece = serializers.DecimalField(
        max_digits=14, decimal_places=6, required=False, allow_null=True, coerce_to_string=False
    )
    cost_price = serializers.SerializerMethodField()
    markup_amount = serializers.DecimalField(
        max_digits=16, decimal_places=4, required=False, coerce_to_string=False
    )
    blank_id = serializers.PrimaryKeyRelatedField(
        source='blank',
        queryset=WorkshopBlank.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    blank_name = serializers.SerializerMethodField()
    other_expenses_total = serializers.SerializerMethodField()
    sale_unit_price = serializers.SerializerMethodField()

    _WRITE_FIELDS = frozenset({
        'name', 'code', 'comment', 'is_active', 'weight_kg_per_piece', 'markup_amount',
        'blank_id', *_profile_extra_fields_meta(),
    })

    class Meta:
        model = PlasticProfile
        fields = (
            'id', 'name', 'code', 'comment', 'is_active', 'deletable',
            'weight_kg_per_piece', 'cost_price', 'markup_amount',
            'blank_id', 'blank_name',
            *_profile_extra_fields_meta(),
            'other_expenses_total', 'sale_unit_price',
        )

    def __init__(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is not None and hasattr(data, 'keys'):
            kwargs['data'] = {
                k: data[k]
                for k in data.keys()
                if k in self._WRITE_FIELDS and k not in READ_ONLY_PRICING_FIELDS
            }
        super().__init__(*args, **kwargs)

    def get_deletable(self, obj):
        return plastic_profile_deletable(obj)

    def get_cost_price(self, obj):
        return serialize_profile_cost_price(obj.cost_price)

    def get_blank_name(self, obj):
        if obj.blank_id:
            try:
                return obj.blank.name
            except Exception:
                pass
        return None

    def get_other_expenses_total(self, obj):
        return serialize_other_expenses_total(obj)

    def get_sale_unit_price(self, obj):
        return serialize_sale_unit_price(obj)

    def validate_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Укажите название')
        return (value or '').strip()

    def validate_weight_kg_per_piece(self, value):
        if value is None:
            return value
        if Decimal(str(value)) <= 0:
            raise serializers.ValidationError('Вес одной штуки должен быть больше нуля.')
        return value

    def validate(self, attrs):
        instance = getattr(self, 'instance', None)
        self._validate_extra_non_negative(attrs)
        self._validate_blank(attrs, required=self.instance is None)
        if 'code' in attrs:
            raw = attrs['code']
            if raw is None:
                s = ''
            else:
                s = str(raw).strip()
            if not s:
                if instance is not None:
                    raise serializers.ValidationError({'code': 'Код не может быть пустым'})
                attrs.pop('code', None)
            else:
                attrs['code'] = s
                qs = PlasticProfile.objects.filter(code=s)
                if instance is not None:
                    qs = qs.exclude(pk=instance.pk)
                if qs.exists():
                    raise serializers.ValidationError({'code': 'Код уже занят'})
        return attrs

    def create(self, validated_data):
        if 'code' not in validated_data or not (validated_data.get('code') or '').strip():
            validated_data['code'] = generate_plastic_profile_code()
        return super().create(validated_data)


class PlasticProfileListSerializer(_PlasticProfilePricingMixin, serializers.ModelSerializer):
    recipes_count = serializers.IntegerField(read_only=True)
    has_recipe = serializers.SerializerMethodField()
    recipes = serializers.SerializerMethodField()
    deletable = serializers.SerializerMethodField()
    cost_price = serializers.SerializerMethodField()
    blank_name = serializers.SerializerMethodField()
    other_expenses_total = serializers.SerializerMethodField()
    sale_unit_price = serializers.SerializerMethodField()

    class Meta:
        model = PlasticProfile
        fields = (
            'id',
            'name',
            'code',
            'comment',
            'is_active',
            'weight_kg_per_piece',
            'cost_price',
            'markup_amount',
            'blank_id',
            'blank_name',
            *_profile_extra_fields_meta(),
            'other_expenses_total',
            'sale_unit_price',
            'recipes_count',
            'has_recipe',
            'recipes',
            'deletable',
        )

    def get_cost_price(self, obj):
        return serialize_profile_cost_price(obj.cost_price)

    def get_blank_name(self, obj):
        if obj.blank_id:
            try:
                return obj.blank.name
            except Exception:
                pass
        return None

    def get_other_expenses_total(self, obj):
        return serialize_other_expenses_total(obj)

    def get_sale_unit_price(self, obj):
        return serialize_sale_unit_price(obj)

    def get_has_recipe(self, obj):
        return (getattr(obj, 'recipes_count', 0) or 0) > 0

    def get_recipes(self, obj):
        return [{'id': r.id, 'name': r.recipe} for r in obj.recipes.all()]

    def get_deletable(self, obj):
        if (getattr(obj, 'recipes_count', 0) or 0) > 0:
            return False
        if getattr(obj, '_has_pb', False):
            return False
        return True


class PlasticProfileNestedSerializer(serializers.ModelSerializer):
    """Вложение в рецепт (без deletable и списка рецептов)."""

    cost_price = serializers.SerializerMethodField()

    class Meta:
        model = PlasticProfile
        fields = ('id', 'name', 'code', 'comment', 'is_active', 'weight_kg_per_piece', 'cost_price', 'markup_amount')

    def get_cost_price(self, obj):
        return serialize_profile_cost_price(obj.cost_price)


class RecipeComponentSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True, allow_null=True)
    chemistry_id = serializers.IntegerField(read_only=True, allow_null=True)
    rework_warehouse_batch_id = serializers.IntegerField(read_only=True, allow_null=True)
    warehouse_batch_id = serializers.SerializerMethodField()
    quantity_per_meter = CleanDecimalField(
        max_digits=14, decimal_places=6, read_only=True, coerce_to_string=True,
    )
    name = serializers.SerializerMethodField()
    rework_batch_display = serializers.SerializerMethodField()
    batch_display = serializers.SerializerMethodField()
    warehouse_batch_display = serializers.SerializerMethodField()

    def get_type(self, obj):
        if obj.type == RecipeComponent.TYPE_RAW:
            return 'raw_material'
        if obj.type == RecipeComponent.TYPE_CHEM:
            return 'chemistry'
        if obj.type == RecipeComponent.TYPE_REWORK_STOCK:
            return 'rework_stock'
        return obj.type

    def get_warehouse_batch_id(self, obj):
        return obj.rework_warehouse_batch_id

    def get_name(self, obj):
        if obj.raw_material_id:
            return obj.raw_material.name
        if obj.chemistry_id:
            return obj.chemistry.name
        if obj.rework_warehouse_batch_id:
            b = obj.rework_warehouse_batch
            return (b.product or '').strip() or None
        return None

    def _rework_display_parts(self, obj):
        if not obj.rework_warehouse_batch_id:
            return None, None
        b = obj.rework_warehouse_batch
        prof = ''
        if b.profile_id:
            prof = (b.profile.name or '').strip()
        prod = (b.product or '').strip()
        line = ' · '.join(x for x in (prof, prod) if x)
        short = line or f'партия #{b.pk}'
        long_label = f'{short} · партия #{b.pk}' if line else f'партия #{b.pk}'
        return short, long_label

    def get_rework_batch_display(self, obj):
        _s, long_label = self._rework_display_parts(obj)
        return long_label

    def get_batch_display(self, obj):
        short, _ = self._rework_display_parts(obj)
        return short

    def get_warehouse_batch_display(self, obj):
        short, _ = self._rework_display_parts(obj)
        return short

    class Meta:
        model = RecipeComponent
        fields = (
            'id',
            'type',
            'material_id',
            'chemistry_id',
            'rework_warehouse_batch_id',
            'warehouse_batch_id',
            'name',
            'rework_batch_display',
            'batch_display',
            'warehouse_batch_display',
            'quantity_per_meter',
            'unit',
        )


class RecipeListSerializer(serializers.ModelSerializer):
    """Список: без полного состава."""

    name = serializers.SerializerMethodField()
    recipe = serializers.CharField(read_only=True)
    profile = PlasticProfileNestedSerializer(read_only=True)
    profile_name = serializers.SerializerMethodField()
    profile_id = serializers.IntegerField(read_only=True)
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
        required=True,
    )
    profile = PlasticProfileNestedSerializer(read_only=True)
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
