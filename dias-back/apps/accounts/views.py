import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth import get_user_model

from apps.activity.mixins import ActivityLoggingMixin
from config.permissions import IsAdminOrHasAccess
from config.throttling import LoginRateThrottle
from .models import Role
from .serializers import RoleSerializer, UserSerializer, UserAccessSerializer, MeSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


def _err(code: str, message: str, errors: list = None, http_status: int = 400) -> Response:
    payload = {'error': {'code': code, 'message': message}}
    if errors:
        payload['errors'] = errors
    return Response(payload, status=http_status)


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

        refresh = RefreshToken.for_user(user)
        return Response({
            'token': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        })


class MeView(APIView):
    def get(self, request):
        data = MeSerializer(request.user).data
        return Response({'user': data, 'accesses': data['accesses']})


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
    queryset = User.objects.select_related('role').all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'users'
    activity_section = 'Пользователи'
    activity_label = 'пользователь'
    filterset_fields = ['role', 'is_active']
    search_fields = ['name', 'email']
    ordering_fields = ['id', 'name', 'email', 'date_joined']

    @action(detail=True, methods=['patch'], url_path='access')
    def update_access(self, request, pk=None):
        user = self.get_object()
        ser = UserAccessSerializer(user, data=request.data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
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
