from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from config.api_numbers import api_decimal_str

from apps.activity.mixins import ActivityLoggingMixin
from apps.chemistry.fifo import chemistry_stock_kg
from apps.materials.fifo import material_stock_kg
from config.pagination import RecipeResultsSetPagination
from config.permissions import IsAdminOrHasAccess
from .models import PlasticProfile, Recipe, RecipeComponent
from .profile_policy import plastic_profile_deletable
from .recipe_policy import recipe_deletable
from .serializers import (
    PlasticProfileSerializer,
    PlasticProfileListSerializer,
    RecipeSerializer,
    RecipeListSerializer,
)
from apps.production.models import Order, ProductionBatch, RecipeRun


class PlasticProfileViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = PlasticProfile.objects.all()
    serializer_class = PlasticProfileSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'recipes'
    activity_section = 'Рецепты'
    activity_label = 'профиль'
    activity_entity_model = PlasticProfile
    search_fields = ['name', 'code', 'comment']
    ordering_fields = ['id', 'name', '-id']
    filterset_fields = ['is_active']

    def get_serializer_class(self):
        if self.action == 'list':
            return PlasticProfileListSerializer
        return PlasticProfileSerializer

    def get_queryset(self):
        has_pb = Exists(ProductionBatch.objects.filter(profile_id=OuterRef('pk')))
        qs = PlasticProfile.objects.annotate(
            recipes_count=Count('recipes', distinct=True),
            _has_pb=has_pb,
        )
        if self.action == 'list':
            qs = qs.prefetch_related(
                Prefetch(
                    'recipes',
                    queryset=Recipe.objects.order_by('recipe', 'id').only('id', 'recipe'),
                )
            )
        return qs.order_by('-id')

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not plastic_profile_deletable(instance):
            msg = (
                'Нельзя удалить профиль: есть рецепты или партии производства. '
                'Деактивируйте профиль (is_active: false).'
            )
            return Response(
                {'code': 'PROFILE_IN_USE', 'error': msg, 'detail': msg},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


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
    pagination_class = RecipeResultsSetPagination
    filterset_fields = ['is_active', 'profile', 'profile_id']
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
        qp = request.query_params
        mode = (qp.get('mode') or 'per_meter').strip().lower()
        if mode not in ('per_meter', 'for_production'):
            raise ValidationError({'mode': 'Допустимо: per_meter, for_production'})

        tm_raw = qp.get('total_meters')
        pieces_raw = qp.get('pieces')
        len_raw = qp.get('length_per_piece')
        has_prod = tm_raw not in (None, '') or (
            pieces_raw not in (None, '') and len_raw not in (None, '')
        )
        if mode == 'for_production' and not has_prod:
            raise ValidationError({'detail': 'Укажите total_meters или pieces и length_per_piece'})
        if has_prod:
            if tm_raw not in (None, ''):
                total_meters = Decimal(str(tm_raw))
            else:
                total_meters = (Decimal(str(pieces_raw)) * Decimal(str(len_raw))).quantize(Decimal('0.0001'))
            eff_mode = 'for_production'
        else:
            total_meters = Decimal('1')
            eff_mode = 'per_meter'

        if total_meters <= 0:
            raise ValidationError({'total_meters': 'Должно быть > 0'})

        q_step = Decimal('0.0001')
        components_out = []
        all_ok = True
        for comp in recipe.components.select_related('raw_material', 'chemistry').order_by('id'):
            qpm = Decimal(str(comp.quantity_per_meter or 0))
            need = (qpm * total_meters).quantize(q_step)
            if comp.type == RecipeComponent.TYPE_RAW and comp.raw_material_id:
                rm = comp.raw_material
                avail = material_stock_kg(comp.raw_material_id)
                unit = rm.unit or 'kg'
                name = rm.name
                cid = comp.id
                ctype = 'raw_material'
                mid = comp.raw_material_id
                chid = None
            elif comp.type == RecipeComponent.TYPE_CHEM and comp.chemistry_id:
                ch = comp.chemistry
                avail = chemistry_stock_kg(comp.chemistry_id)
                unit = ch.unit or 'kg'
                name = ch.name
                cid = comp.id
                ctype = 'chemistry'
                mid = None
                chid = comp.chemistry_id
            else:
                continue
            shortage = (need - avail).quantize(q_step) if need > avail else Decimal('0')
            ok = avail >= need
            all_ok = all_ok and ok
            components_out.append({
                'id': cid,
                'component_type': ctype,
                'material_id': mid,
                'chemistry_id': chid,
                'name': name,
                'unit': unit,
                'norm_per_meter_kg': api_decimal_str(qpm),
                'required_total_kg': api_decimal_str(need),
                'available_kg': api_decimal_str(avail),
                'shortage_kg': api_decimal_str(shortage),
                'sufficient': ok,
            })

        return Response({
            'mode': eff_mode,
            'total_meters': api_decimal_str(total_meters),
            'all_sufficient': all_ok,
            'components': components_out,
        })
