from rest_framework import serializers
from .models import Line, LineHistory, Order, ProductionBatch


class LineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Line
        fields = ('id', 'name')


class LineHistorySerializer(serializers.ModelSerializer):
    line_name = serializers.CharField(source='line.name', read_only=True)
    date = serializers.DateField(format='%Y-%m-%d')
    time = serializers.TimeField(format='%H:%M')

    class Meta:
        model = LineHistory
        fields = ('id', 'line', 'line_name', 'action', 'date', 'time')


class OrderSerializer(serializers.ModelSerializer):
    """Контракт: GET — items с id, status, product, recipe, line, quantity, date, assigned_to (operator)."""
    recipe_name = serializers.CharField(source='recipe.recipe', read_only=True)
    line_name = serializers.CharField(source='line.name', read_only=True)
    assigned_to = serializers.PrimaryKeyRelatedField(source='operator', read_only=True, allow_null=True)

    class Meta:
        model = Order
        fields = (
            'id', 'status', 'recipe', 'recipe_name', 'line', 'line_name',
            'quantity', 'product', 'operator', 'assigned_to', 'date',
        )
        extra_kwargs = {
            'operator': {'allow_null': True},
            'recipe': {'required': True},
            'line': {'required': True},
            'quantity': {'required': True},
            'product': {'required': False},
            'date': {'required': False},
        }


class ProductionBatchSerializer(serializers.ModelSerializer):
    order_product = serializers.CharField(source='order.product', read_only=True)

    class Meta:
        model = ProductionBatch
        fields = ('id', 'order', 'order_product', 'product', 'quantity', 'operator', 'date', 'otk_status')
        extra_kwargs = {'operator': {'allow_null': True}}


class BatchListSerializer(serializers.ModelSerializer):
    """
    Контракт GET /api/batches/: id, order_name, product_name, quantity/released,
    operator_name, date, created_at, otk_status, otk_accepted, otk_defect,
    otk_defect_reason, otk_comment, otk_inspector, otk_checked_at.
    """
    order_name = serializers.CharField(source='order.product', read_only=True)
    product_name = serializers.CharField(source='product', read_only=True)
    released = serializers.DecimalField(source='quantity', max_digits=14, decimal_places=4, read_only=True)
    operator_name = serializers.SerializerMethodField()
    created_at = serializers.DateField(source='date', read_only=True)
    otk_accepted = serializers.SerializerMethodField()
    otk_defect = serializers.SerializerMethodField()
    otk_defect_reason = serializers.SerializerMethodField()
    otk_comment = serializers.SerializerMethodField()
    otk_inspector = serializers.SerializerMethodField()
    otk_checked_at = serializers.SerializerMethodField()

    class Meta:
        model = ProductionBatch
        fields = (
            'id', 'order', 'order_name', 'product', 'product_name', 'quantity', 'released',
            'operator', 'operator_name', 'date', 'created_at',
            'otk_status', 'otk_accepted', 'otk_defect', 'otk_defect_reason',
            'otk_comment', 'otk_inspector', 'otk_checked_at',
        )

    def _last_check(self, obj):
        if not hasattr(obj, '_last_otk_check'):
            obj._last_otk_check = obj.otk_checks.select_related('inspector').order_by('-checked_date').first()
        return obj._last_otk_check

    def get_operator_name(self, obj):
        return obj.operator.name if obj.operator_id else None

    def get_otk_accepted(self, obj):
        c = self._last_check(obj)
        return float(c.accepted) if c else None

    def get_otk_defect(self, obj):
        c = self._last_check(obj)
        return float(c.rejected) if c else None

    def get_otk_defect_reason(self, obj):
        c = self._last_check(obj)
        return c.reject_reason if c else None

    def get_otk_comment(self, obj):
        c = self._last_check(obj)
        return (c.comment or '') if c else None

    def get_otk_inspector(self, obj):
        c = self._last_check(obj)
        return c.inspector.name if c and c.inspector_id else None

    def get_otk_checked_at(self, obj):
        c = self._last_check(obj)
        if c and c.checked_date:
            return c.checked_date.isoformat()
        return None
