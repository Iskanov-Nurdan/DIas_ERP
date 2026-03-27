from rest_framework import serializers
from .models import (
    ChemistryCatalog, ChemistryComposition, ChemistryTask,
    ChemistryStock,
)


class ChemistryCompositionSerializer(serializers.ModelSerializer):
    material_id = serializers.IntegerField(source='raw_material_id', read_only=True)
    material_name = serializers.CharField(source='raw_material.name', read_only=True)
    raw_material_name = serializers.CharField(source='raw_material.name', read_only=True)

    class Meta:
        model = ChemistryComposition
        fields = (
            'id', 'raw_material', 'material_id', 'material_name',
            'raw_material_name', 'quantity_per_unit',
        )


class ChemistryCatalogSerializer(serializers.ModelSerializer):
    """GET /chemistry/elements/: id, name, unit (строка, например \"л\")."""
    compositions = ChemistryCompositionSerializer(many=True, read_only=True)
    unit = serializers.CharField(max_length=50, required=False, default='кг')

    class Meta:
        model = ChemistryCatalog
        fields = ('id', 'name', 'unit', 'compositions')

    def create(self, validated_data):
        request = self.context.get('request')
        compositions_data = (request.data.get('compositions', []) if request else [])
        catalog = ChemistryCatalog.objects.create(**validated_data)
        for item in compositions_data:
            if item.get('raw_material_id') is not None:
                ChemistryComposition.objects.create(
                    chemistry=catalog,
                    raw_material_id=item['raw_material_id'],
                    quantity_per_unit=item.get('quantity_per_unit', 0),
                )
        ChemistryStock.objects.get_or_create(chemistry=catalog, defaults={'quantity': 0, 'unit': catalog.unit})
        return catalog


class ChemistryTaskSerializer(serializers.ModelSerializer):
    """
    Один элемент и одно количество на задание.
    Контракт: name, chemistry (ID), quantity (число), deadline (YYYY-MM-DD).
    Для нескольких элементов — несколько POST (по одному на строку).
    """
    chemistry_name = serializers.CharField(source='chemistry.name', read_only=True)

    class Meta:
        model = ChemistryTask
        fields = ('id', 'name', 'status', 'deadline', 'chemistry', 'chemistry_name', 'quantity', 'unit', 'created_at')

    def validate_chemistry(self, value):
        if isinstance(value, (list, tuple)):
            raise serializers.ValidationError(
                'Ожидается один ID хим. элемента (число). Для нескольких элементов отправьте отдельный POST на каждый.'
            )
        return value

    def validate_quantity(self, value):
        if isinstance(value, (list, tuple)):
            raise serializers.ValidationError(
                'Ожидается одно число (количество). Для нескольких элементов отправьте отдельный POST на каждый.'
            )
        return value


class ChemistryStockSerializer(serializers.ModelSerializer):
    chemistry_name = serializers.CharField(source='chemistry.name', read_only=True)

    class Meta:
        model = ChemistryStock
        fields = ('id', 'chemistry', 'chemistry_name', 'quantity', 'unit', 'updated_at')


class ChemistryBalanceSerializer(serializers.ModelSerializer):
    """Формат для GET /api/chemistry/balances/: element_name, unit, balance, date, task_id, task_name."""
    element_name = serializers.CharField(source='chemistry.name', read_only=True)
    balance = serializers.DecimalField(source='quantity', max_digits=14, decimal_places=4, read_only=True)
    date = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField(read_only=True)
    task_id = serializers.IntegerField(source='last_task_id', read_only=True, allow_null=True)
    task_name = serializers.SerializerMethodField()

    def get_task_name(self, obj):
        return obj.last_task.name if obj.last_task_id else None

    class Meta:
        model = ChemistryStock
        fields = ('element_name', 'unit', 'balance', 'date', 'updated_at', 'task_id', 'task_name')

    def get_date(self, obj):
        """Дата в формате YYYY-MM-DD (для колонки ДАТА). Берётся из updated_at."""
        if obj.updated_at:
            return obj.updated_at.strftime('%Y-%m-%d')
        return None
