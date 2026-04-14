from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.activity.mixins import ActivityLoggingMixin
from apps.chemistry.fifo import chemistry_stock_kg
from apps.materials.fifo import material_stock_kg
from config.permissions import IsAdminOrHasAccess
from .models import PlasticProfile, Recipe, RecipeComponent
from .recipe_policy import recipe_deletable
from .serializers import PlasticProfileSerializer, RecipeSerializer, RecipeListSerializer
from apps.production.models import Order, ProductionBatch, RecipeRun


class PlasticProfileViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = PlasticProfile.objects.all()
    serializer_class = PlasticProfileSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'recipes'
    activity_section = 'Рецепты'
    activity_label = 'профиль'
    activity_entity_model = PlasticProfile
    search_fields = ['name', 'code']
    ordering_fields = ['id', 'name']


class RecipeViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    """
    Рецепт — справочник норм на 1 м; сохранение не списывает склад.
    """

    queryset = Recipe.objects.all()
    serializer_class = RecipeSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'recipes'
    activity_section = 'Рецепты'
    activity_label = 'рецепт'
    filterset_fields = ['is_active', 'profile']
    search_fields = ['recipe', 'product', 'comment']
    ordering_fields = ['id', 'recipe', 'product', 'components_count', 'output_quantity']

    def get_serializer_class(self):
        if self.action == 'list':
            return RecipeListSerializer
        return RecipeSerializer

    def get_queryset(self):
        block_pb = Exists(ProductionBatch.objects.filter(recipe_id=OuterRef('pk')))
        block_ord = Exists(Order.objects.filter(recipe_id=OuterRef('pk')))
        block_rr = Exists(RecipeRun.objects.filter(recipe_id=OuterRef('pk')))
        qs = (
            Recipe.objects.select_related('profile')
            .annotate(
                components_count=Count('components', distinct=True),
                _block_pb=block_pb,
                _block_ord=block_ord,
                _block_rr=block_rr,
            )
        )
        if self.action == 'list':
            return qs.order_by('recipe', 'id')
        return qs.prefetch_related(
            Prefetch(
                'components',
                queryset=RecipeComponent.objects.select_related('raw_material', 'chemistry'),
            )
        ).order_by('recipe', 'id')

    def get_serializer(self, *args, **kwargs):
        if kwargs.get('data') is not None:
            data = self._normalize_recipe_data(kwargs['data'])
            kwargs = dict(kwargs)
            kwargs['data'] = data
        return super().get_serializer(*args, **kwargs)

    def _normalize_recipe_data(self, data):
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
        data.pop('yield_quantity', None)
        data.pop('output_measure', None)
        if getattr(self, 'action', None) == 'create':
            if data.get('base_unit') is None or data.get('base_unit') == '':
                data['base_unit'] = Recipe.BASE_UNIT_PER_METER
        return data

    def _normalize_component(self, c):
        raw_type = (c.get('type') or 'raw').lower()
        if raw_type in ('raw_material', 'raw'):
            comp_type = RecipeComponent.TYPE_RAW
        elif raw_type in ('chemistry', 'chem'):
            comp_type = RecipeComponent.TYPE_CHEM
        else:
            comp_type = (
                raw_type
                if raw_type in (RecipeComponent.TYPE_RAW, RecipeComponent.TYPE_CHEM)
                else RecipeComponent.TYPE_RAW
            )
        material_id = c.get('material_id') or c.get('raw_material_id')
        chemistry_id = c.get('chemistry_id')
        qpm = c.get('quantity_per_meter', c.get('quantity', 0))
        qd = Decimal(str(qpm if qpm is not None else 0))
        if qd < 0:
            raise serializers.ValidationError({'components': 'quantity_per_meter должно быть ≥ 0'})
        return {
            'type': comp_type,
            'raw_material_id': material_id if comp_type == RecipeComponent.TYPE_RAW else None,
            'chemistry_id': chemistry_id if comp_type == RecipeComponent.TYPE_CHEM else None,
            'quantity_per_meter': qd,
            'unit': 'kg',
        }

    @transaction.atomic
    def perform_create(self, serializer):
        recipe = serializer.save()
        comps = self.request.data.get('components')
        if comps is None:
            comps = []
        if not isinstance(comps, list):
            raise serializers.ValidationError({'components': 'Ожидается массив'})
        for c in comps:
            nc = self._normalize_component(c)
            if nc['raw_material_id'] and nc['chemistry_id']:
                raise serializers.ValidationError(
                    {'components': 'Только material_id или chemistry_id, не оба'}
                )
            if not nc['raw_material_id'] and not nc['chemistry_id']:
                raise serializers.ValidationError(
                    {'components': 'Укажите material_id или chemistry_id'}
                )
            RecipeComponent.objects.create(
                recipe=recipe,
                type=nc['type'],
                raw_material_id=nc['raw_material_id'],
                chemistry_id=nc['chemistry_id'],
                quantity_per_meter=nc['quantity_per_meter'],
                unit=nc['unit'],
            )

    @transaction.atomic
    def perform_update(self, serializer):
        recipe = serializer.save()
        if 'components' not in self.request.data:
            return
        comps = self.request.data.get('components')
        if not isinstance(comps, list):
            raise serializers.ValidationError({'components': 'Ожидается массив'})
        recipe.components.all().delete()
        for c in comps:
            nc = self._normalize_component(c)
            if nc['raw_material_id'] and nc['chemistry_id']:
                raise serializers.ValidationError(
                    {'components': 'Только material_id или chemistry_id, не оба'}
                )
            if not nc['raw_material_id'] and not nc['chemistry_id']:
                raise serializers.ValidationError(
                    {'components': 'Укажите material_id или chemistry_id'}
                )
            RecipeComponent.objects.create(
                recipe=recipe,
                type=nc['type'],
                raw_material_id=nc['raw_material_id'],
                chemistry_id=nc['chemistry_id'],
                quantity_per_meter=nc['quantity_per_meter'],
                unit=nc['unit'],
            )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not recipe_deletable(instance):
            msg = (
                'Нельзя удалить рецепт: есть партии производства, заказы или замесы. '
                'Деактивируйте рецепт (is_active: false).'
            )
            return Response(
                {
                    'code': 'RECIPE_IN_USE',
                    'error': msg,
                    'detail': msg,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='availability')
    def availability(self, request, pk=None):
        recipe = self.get_object()
        lines = []
        all_ok = True
        qs = recipe.components.select_related('raw_material', 'chemistry').all()
        for comp in qs:
            line_ok = True
            if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
                req = float(comp.quantity_per_meter)
                avail = float(material_stock_kg(comp.raw_material_id))
                line_ok = avail + 1e-9 >= req
                lines.append({
                    'type': 'raw_material',
                    'component_id': comp.id,
                    'material_id': comp.raw_material_id,
                    'name': comp.raw_material.name,
                    'quantity_per_meter_kg': req,
                    'available_kg': avail,
                    'ok': line_ok,
                })
            elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
                req = float(comp.quantity_per_meter)
                avail = float(chemistry_stock_kg(comp.chemistry_id))
                line_ok = avail + 1e-9 >= req
                lines.append({
                    'type': 'chemistry',
                    'component_id': comp.id,
                    'chemistry_id': comp.chemistry_id,
                    'name': comp.chemistry.name,
                    'quantity_per_meter_kg': req,
                    'available_kg': avail,
                    'ok': line_ok,
                })
            all_ok = all_ok and line_ok
        n_ok = sum(1 for ln in lines if ln.get('ok'))
        return Response({
            'ok': all_ok,
            'per_meter': {'all_ok': all_ok, 'unit': 'kg'},
            'lines': lines,
            'summary': {
                'lines_total': len(lines),
                'lines_ok': n_ok,
                'lines_missing': len(lines) - n_ok,
            },
        })
