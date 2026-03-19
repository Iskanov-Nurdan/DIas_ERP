from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from .models import Recipe, RecipeComponent
from .serializers import RecipeSerializer
from apps.materials.models import Incoming, MaterialWriteoff
from apps.chemistry.models import ChemistryStock


class RecipeViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Recipe.objects.prefetch_related('components').all()
    serializer_class = RecipeSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'recipes'
    activity_section = 'Рецепты'
    activity_label = 'рецепт'
    filterset_fields = []
    search_fields = ['recipe', 'product']
    ordering_fields = ['id', 'recipe', 'product']

    def get_serializer(self, *args, **kwargs):
        """Поддержка контракта: name (алиас recipe), product, components с material_id/chemistry_id."""
        if kwargs.get('data') is not None:
            data = self._normalize_recipe_data(kwargs['data'])
            kwargs = dict(kwargs)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)

    def _normalize_recipe_data(self, data):
        """Принимаем name (алиас recipe) и product из контракта API."""
        if not data:
            return data
        data = dict(data)
        if 'name' in data and 'recipe' not in data:
            data['recipe'] = data.get('name')
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
