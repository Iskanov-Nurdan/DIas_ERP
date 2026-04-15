import logging

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.activity.mixins import ActivityLoggingMixin
from apps.activity.audit_service import instance_to_snapshot, schedule_entity_audit
from config.openapi_common import DiasErrorSerializer
from config.permissions import IsAdminOrHasAccess
from config.throttling import LoginRateThrottle
from .models import Role
from .serializers import MeSerializer, RoleSerializer, UserAccessPatchSerializer, UserSerializer

LoginRequestSerializer = inline_serializer(
    name='LoginRequest',
    fields={
        'name': serializers.CharField(help_text='Имя пользователя (логин).'),
        'password': serializers.CharField(help_text='Пароль.'),
    },
)

LoginResponseSerializer = inline_serializer(
    name='LoginResponse',
    fields={
        'token': serializers.CharField(help_text='Access JWT для Authorization: Bearer …'),
        'refresh': serializers.CharField(help_text='Refresh для POST /api/auth/logout и обновления пары.'),
        'user': UserSerializer(),
    },
)

LogoutRequestSerializer = inline_serializer(
    name='LogoutRequest',
    fields={
        'refresh': serializers.CharField(
            required=False,
            allow_blank=True,
            help_text='Если передан — токен попадает в blacklist.',
        ),
    },
)

MeResponseSerializer = inline_serializer(
    name='MeResponse',
    fields={
        'user': MeSerializer(),
        'accesses': serializers.ListField(
            child=serializers.CharField(),
            help_text='Дублирует user.accesses; ключи из UserAccess.',
        ),
    },
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'code': code, 'error': message, 'detail': message}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


@extend_schema(
    tags=['auth'],
    summary='Вход (JWT)',
    request=LoginRequestSerializer,
    responses={
        200: LoginResponseSerializer,
        400: DiasErrorSerializer,
        401: DiasErrorSerializer,
        409: DiasErrorSerializer,
        429: DiasErrorSerializer,
    },
    auth=[],
    description='Лимит: throttle `login` 10/min. Ответ не использует camelCase.',
)
class LoginView(APIView):
    permission_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        name = request.data.get('name')
        password = request.data.get('password')

        errors = []
        if not name:
            errors.append({'field': 'name', 'message': 'Обязательное поле'})
        if not password:
            errors.append({'field': 'password', 'message': 'Обязательное поле'})
        if errors:
            return _err('validation_error', 'Укажите name и password', errors=errors, http_status=400)

        qs = User.objects.filter(name=name)
        if qs.count() > 1:
            logger.warning('login: ambiguous name=%s count=%s', name, qs.count())
            return _err('conflict', 'Найдено несколько пользователей с таким именем. Обратитесь к администратору.', http_status=409)

        user = qs.first()
        if not user or not user.check_password(password):
            return _err('unauthorized', 'Неверные имя или пароль', http_status=401)

        if not user.is_active:
            return _err('unauthorized', 'Пользователь деактивирован', http_status=401)

        user = User.objects.select_related('role').prefetch_related('user_accesses').get(pk=user.pk)
        refresh = RefreshToken.for_user(user)
        return Response({
            'token': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        })


@extend_schema(
    tags=['auth'],
    summary='Текущий пользователь и права',
    responses={200: MeResponseSerializer, 401: DiasErrorSerializer},
    description='Поле accesses — ключи из UserAccess (меню UI).',
)
class MeView(APIView):
    def get(self, request):
        user = User.objects.select_related('role').prefetch_related('user_accesses').get(pk=request.user.pk)
        data = MeSerializer(user).data
        return Response({'user': data, 'accesses': data['accesses']})


@extend_schema(
    tags=['auth'],
    summary='Выход (blacklist refresh)',
    request=LogoutRequestSerializer,
    responses={
        200: inline_serializer(
            name='LogoutOk',
            fields={'detail': serializers.CharField(default='OK')},
        ),
        401: DiasErrorSerializer,
    },
    description='Идемпотентно: при невалидном refresh просто возвращает OK.',
)
class LogoutView(APIView):
    def post(self, request):
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except TokenError:
                pass
        return Response({'detail': 'OK'})


class UserViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = User.objects.select_related('role').prefetch_related('user_accesses')
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'users'
    activity_section = 'Пользователи'
    activity_label = 'пользователь'
    filterset_fields = ['role', 'is_active']
    search_fields = ['name', 'email']
    ordering_fields = ['id', 'name', 'email', 'date_joined']

    def get_queryset(self):
        return super().get_queryset().filter(is_system=False)

    def get_object(self):
        pk = self.kwargs.get('pk')
        if pk is not None and User.objects.filter(pk=pk, is_system=True).exists():
            raise PermissionDenied('Системный пользователь защищён от доступа через API.')
        return super().get_object()

    @extend_schema(
        tags=['auth'],
        summary='Доступы к разделам (UserAccess)',
        request=UserAccessPatchSerializer,
        responses={200: UserSerializer},
        description='Тело: { "access_keys": [...] } — полная замена ключей вкладок только для этого пользователя.',
    )
    @action(detail=True, methods=['patch'], url_path='access')
    def update_access(self, request, pk=None):
        user = self.get_object()
        before = instance_to_snapshot(user)
        ser = UserAccessPatchSerializer(user, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        user = User.objects.select_related('role').prefetch_related('user_accesses').get(pk=user.pk)
        after = instance_to_snapshot(user)
        schedule_entity_audit(
            user=request.user,
            request=request,
            section='Пользователи',
            description=f'Изменены права доступа к разделам: {user.name}',
            action='update',
            model_cls=User,
            before=before,
            after=after,
            after_instance=user,
            payload_extra={'endpoint': 'PATCH /api/users/{id}/access/'},
        )
        return Response(UserSerializer(user).data)


class RoleViewSet(ActivityLoggingMixin, viewsets.ModelViewSet):
    queryset = Role.objects.prefetch_related('accesses').all()
    serializer_class = RoleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'users'
    activity_section = 'Пользователи'
    activity_label = 'роль'
    filterset_fields = []
    search_fields = ['name']
    ordering_fields = ['id', 'name']

    def get_queryset(self):
        return super().get_queryset().filter(is_system=False)

    def get_object(self):
        pk = self.kwargs.get('pk')
        if pk is not None and Role.objects.filter(pk=pk, is_system=True).exists():
            raise PermissionDenied('Системная роль защищена от доступа через API.')
        return super().get_object()
