from django.db.models import Prefetch, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from .models import Recipe, RecipeComponent
from .serializers import RecipeSerializer
from apps.materials.models import Incoming, MaterialWriteoff
from apps.chemistry.models import ChemistryStock
from apps.production.models import Order, RecipeRun


class RecipeViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    Рецепт — справочник: сохранение не списывает склад и не создаёт приходы
    (движения только при производстве и т.п.).
    """
    queryset = Recipe.objects.prefetch_related(
        Prefetch(
            'components',
            queryset=RecipeComponent.objects.select_related('raw_material', 'chemistry'),
        )
    ).all()
    serializer_class = RecipeSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'recipes'
    activity_section = 'Рецепты'
    activity_label = 'рецепт'
    filterset_fields = []
    search_fields = ['recipe', 'product']
    ordering_fields = ['id', 'recipe', 'product', 'output_quantity']

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if RecipeRun.objects.filter(recipe=instance).exists():
            return Response(
                {
                    'code': 'RECIPE_IN_USE',
                    'error': 'Нельзя удалить рецепт: есть связанные замесы (recipe-runs).',
                    'detail': 'Удалите или архивируйте замесы, затем повторите попытку.',
                },
                status=status.HTTP_409_CONFLICT,
            )
        if Order.objects.filter(recipe=instance).exists():
            return Response(
                {
                    'code': 'RECIPE_IN_USE',
                    'error': 'Нельзя удалить рецепт: есть заказы производства с этим рецептом.',
                    'detail': 'Сначала завершите или удалите связанные заказы.',
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    def get_serializer(self, *args, **kwargs):
        """Поддержка контракта: name (алиас recipe), product, components с material_id/chemistry_id."""
        if kwargs.get('data') is not None:
            data = self._normalize_recipe_data(kwargs['data'])
            kwargs = dict(kwargs)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)

    def _normalize_recipe_data(self, data):
        """Принимаем name (алиас recipe); product дублирует recipe, если не передан.
        Выпуск: output_quantity / output_unit_kind или алиасы yield_quantity / output_measure."""
        if not data:
            return data
        data = dict(data)
        if 'name' in data and 'recipe' not in data:
            data['recipe'] = data.get('name')
        if data.get('recipe') is not None and not data.get('product'):
            data['product'] = data['recipe']
        if 'output_quantity' not in data and 'yield_quantity' in data:
            data['output_quantity'] = data.get('yield_quantity')
        if 'output_unit_kind' not in data and 'output_measure' in data:
            data['output_unit_kind'] = data.get('output_measure')
        uk = data.get('output_unit_kind')
        if isinstance(uk, str):
            u = uk.strip()
            if not u:
                data['output_unit_kind'] = None
            else:
                low = u.lower()
                data['output_unit_kind'] = low if low in ('naming', 'pieces', 'amount') else u
        # Алиасы уже слиты в canonical; убираем, чтобы не попадали в валидацию как лишние поля.
        data.pop('yield_quantity', None)
        data.pop('output_measure', None)
        return data

    def _normalize_component(self, c):
        """Контракт: type raw_material|chemistry, material_id|chemistry_id, quantity, unit."""
        raw_type = (c.get('type') or 'raw').lower()
        if raw_type == 'raw_material':
            comp_type = RecipeComponent.TYPE_RAW
        elif raw_type == 'chemistry':
            comp_type = RecipeComponent.TYPE_CHEM
        else:
            comp_type = raw_type if raw_type in (RecipeComponent.TYPE_RAW, RecipeComponent.TYPE_CHEM) else RecipeComponent.TYPE_RAW
        material_id = c.get('material_id') or c.get('raw_material_id')
        chemistry_id = c.get('chemistry_id')
        unit = c.get('unit') or 'кг'
        if isinstance(unit, dict):
            unit = unit.get('code') or unit.get('name') or 'кг'
        return {
            'type': comp_type,
            'raw_material_id': material_id if comp_type == RecipeComponent.TYPE_RAW else None,
            'chemistry_id': chemistry_id if comp_type == RecipeComponent.TYPE_CHEM else None,
            'quantity': c.get('quantity', 0),
            'unit': str(unit),
        }

    def perform_create(self, serializer):
        recipe = serializer.save()
        comps = self.request.data.get('components', [])
        for c in comps:
            nc = self._normalize_component(c)
            RecipeComponent.objects.create(
                recipe=recipe,
                type=nc['type'],
                raw_material_id=nc['raw_material_id'],
                chemistry_id=nc['chemistry_id'],
                quantity=nc['quantity'],
                unit=nc['unit'],
            )

    def perform_update(self, serializer):
        recipe = serializer.save()
        comps = self.request.data.get('components')
        if comps is not None:
            recipe.components.all().delete()
            for c in comps:
                nc = self._normalize_component(c)
                RecipeComponent.objects.create(
                    recipe=recipe,
                    type=nc['type'],
                    raw_material_id=nc['raw_material_id'],
                    chemistry_id=nc['chemistry_id'],
                    quantity=nc['quantity'],
                    unit=nc['unit'],
                )

    @action(detail=True, methods=['get'], url_path='availability')
    def availability(self, request, pk=None):
        recipe = self.get_object()
        missing = []
        for comp in recipe.components.all():
            if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
                inc = Incoming.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
                woff = MaterialWriteoff.objects.filter(material=comp.raw_material).aggregate(s=Sum('quantity'))['s'] or 0
                avail = float(inc - woff)
                if avail < float(comp.quantity):
                    missing.append({
                        'component': comp.raw_material.name,
                        'required': float(comp.quantity),
                        'available': avail,
                        'unit': comp.unit,
                    })
            elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
                stock = ChemistryStock.objects.filter(chemistry=comp.chemistry).first()
                avail = float(stock.quantity or 0) if stock else 0
                if avail < float(comp.quantity):
                    missing.append({
                        'component': comp.chemistry.name,
                        'required': float(comp.quantity),
                        'available': avail,
                        'unit': comp.unit,
                    })
        return Response({
            'available': len(missing) == 0,
            'missing': missing,
        })
