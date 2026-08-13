from django.utils import timezone
from rest_framework import serializers

from config.api_numbers import api_decimal_str

from . import services
from .constants import GP_WAREHOUSE_LABEL, RAW_WAREHOUSE_LABEL
from .models import (
    FoamDensityGrade,
    FoamGpOperation,
    FoamGpStock,
    FoamProductionRun,
    FoamRawLot,
    FoamSale,
    FoamSaleLine,
)


class FoamDensityGradeSerializer(serializers.ModelSerializer):
    min_kg_m3 = serializers.DecimalField(max_digits=8, decimal_places=2, coerce_to_string=True)
    max_kg_m3 = serializers.DecimalField(max_digits=8, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = FoamDensityGrade
        fields = ('code', 'min_kg_m3', 'max_kg_m3')

    def validate(self, attrs):
        min_v = attrs.get('min_kg_m3')
        max_v = attrs.get('max_kg_m3')
        if min_v is not None and min_v <= 0:
            raise serializers.ValidationError({'min_kg_m3': 'Должно быть > 0'})
        if min_v is not None and max_v is not None and max_v < min_v:
            raise serializers.ValidationError({'max_kg_m3': 'Должно быть ≥ min_kg_m3'})
        return attrs


class FoamRawLotSerializer(serializers.ModelSerializer):
    bag_weight_kg = serializers.DecimalField(max_digits=12, decimal_places=1, coerce_to_string=True)
    received_kg = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    remaining_kg = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    warehouse = serializers.SerializerMethodField()

    class Meta:
        model = FoamRawLot
        fields = (
            'id',
            'lot_number',
            'material_name',
            'supplier',
            'bag_weight_kg',
            'received_kg',
            'remaining_kg',
            'received_at',
            'warehouse',
        )
        read_only_fields = ('id', 'lot_number', 'received_at')

    def get_warehouse(self, obj):
        return RAW_WAREHOUSE_LABEL

    def validate_bag_weight_kg(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError('Должно быть > 0')
        return value

    def create(self, validated_data):
        bag_weight_kg = validated_data['bag_weight_kg']
        return FoamRawLot.objects.create(
            lot_number=services.generate_lot_number(),
            material_name=validated_data['material_name'],
            supplier=validated_data.get('supplier') or '',
            bag_weight_kg=bag_weight_kg,
            received_kg=bag_weight_kg,
            remaining_kg=bag_weight_kg,
        )


class FoamProductionRunReadSerializer(serializers.ModelSerializer):
    lot_id = serializers.IntegerField(source='lot.id', read_only=True)
    lot_number = serializers.CharField(source='lot.lot_number', read_only=True)
    material_name = serializers.CharField(source='lot.material_name', read_only=True)
    grade_code = serializers.SerializerMethodField()
    input_kg = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    output_qty = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    operator = serializers.SerializerMethodField()

    class Meta:
        model = FoamProductionRun
        fields = (
            'id',
            'lot_id',
            'lot_number',
            'material_name',
            'grade_code',
            'input_kg',
            'output_format',
            'output_qty',
            'produced_at',
            'operator',
        )

    def get_grade_code(self, obj):
        return obj.grade.code if obj.grade_id else None

    def get_operator(self, obj):
        u = obj.operator
        if u is None:
            return None
        name = getattr(u, 'name', '') or ''
        return name or getattr(u, 'role_name', None) or str(u)


class FoamProductionRunCreateSerializer(serializers.Serializer):
    lot_id = serializers.IntegerField(required=True)
    input_kg = serializers.DecimalField(max_digits=12, decimal_places=1, required=True)
    output_format = serializers.ChoiceField(choices=('cube', 'granule'), required=True)
    grade_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_input_kg(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError('Должно быть > 0')
        return value


class FoamGpStockSerializer(serializers.ModelSerializer):
    grade_code = serializers.SerializerMethodField()
    qty = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    warehouse = serializers.SerializerMethodField()

    class Meta:
        model = FoamGpStock
        fields = ('id', 'output_format', 'grade_code', 'thickness_cm', 'qty', 'warehouse')

    def get_grade_code(self, obj):
        return obj.grade.code if obj.grade_id else None

    def get_warehouse(self, obj):
        return GP_WAREHOUSE_LABEL


class FoamGpStockCutSerializer(serializers.Serializer):
    cube_stock_id = serializers.IntegerField(required=True)
    thickness_cm = serializers.IntegerField(required=True, min_value=1)
    cubes_qty = serializers.DecimalField(max_digits=12, decimal_places=1, required=True)

    def validate_cubes_qty(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError('Должно быть > 0')
        return value


class FoamGpOperationSerializer(serializers.ModelSerializer):
    grade_code = serializers.SerializerMethodField()
    qty = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)

    class Meta:
        model = FoamGpOperation
        fields = ('id', 'kind', 'output_format', 'grade_code', 'thickness_cm', 'qty', 'created_at', 'ref')

    def get_grade_code(self, obj):
        return obj.grade.code if obj.grade_id else None


class FoamSaleLineReadSerializer(serializers.ModelSerializer):
    stock_id = serializers.IntegerField(source='stock.id', read_only=True)
    output_format = serializers.CharField(source='stock.output_format', read_only=True)
    grade_code = serializers.SerializerMethodField()
    thickness_cm = serializers.IntegerField(source='stock.thickness_cm', read_only=True)
    qty = serializers.DecimalField(max_digits=12, decimal_places=1, read_only=True, coerce_to_string=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, coerce_to_string=True)

    class Meta:
        model = FoamSaleLine
        fields = ('stock_id', 'output_format', 'grade_code', 'thickness_cm', 'qty', 'unit_price')

    def get_grade_code(self, obj):
        grade = obj.stock.grade
        return grade.code if grade else None


class FoamSaleReadSerializer(serializers.ModelSerializer):
    lines = FoamSaleLineReadSerializer(many=True, read_only=True)
    date = serializers.SerializerMethodField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, coerce_to_string=True)
    paid_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, coerce_to_string=True)
    debt_amount = serializers.SerializerMethodField()

    class Meta:
        model = FoamSale
        fields = (
            'id',
            'client',
            'date',
            'lines',
            'total_amount',
            'paid_amount',
            'debt_amount',
            'payment_status',
        )

    def get_date(self, obj):
        dt = timezone.make_aware(
            timezone.datetime.combine(obj.sale_date, timezone.datetime.min.time()),
            timezone.get_current_timezone(),
        )
        return dt.isoformat()

    def get_debt_amount(self, obj):
        return api_decimal_str(obj.debt_amount)


class FoamSaleLineWriteSerializer(serializers.Serializer):
    stock_id = serializers.IntegerField(required=True)
    qty = serializers.DecimalField(max_digits=12, decimal_places=1, required=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)


class FoamSaleCreateSerializer(serializers.Serializer):
    client = serializers.CharField(required=True, allow_blank=False)
    sale_date = serializers.DateField(required=True)
    lines = FoamSaleLineWriteSerializer(many=True, required=True)
    paid_amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=True)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError('Нужна минимум одна строка')
        return value

    def validate_paid_amount(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError('Должно быть ≥ 0')
        return value
