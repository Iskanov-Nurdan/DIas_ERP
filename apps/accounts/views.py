from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from config.permissions import IsAdminOrHasAccess
from .models import Role
from .serializers import RoleSerializer, UserSerializer, UserAccessSerializer, MeSerializer

User = get_user_model()


class LoginView(APIView):
    permission_classes = []

    def post(self, request):
        name = request.data.get('name')
        password = request.data.get('password')
        if not name or not password:
            return Response({
                'error': 'Укажите name и password',
                'code': 'VALIDATION_ERROR',
                'details': {},
            }, status=status.HTTP_400_BAD_REQUEST)
        qs = User.objects.filter(name=name)
        if qs.count() > 1:
            return Response({
                'error': 'Найдено несколько пользователей с таким именем. Обратитесь к администратору.',
                'code': 'AMBIGUOUS_LOGIN',
                'details': {},
            }, status=status.HTTP_409_CONFLICT)
        user = qs.first()
        if not user or not user.check_password(password):
            return Response({
                'error': 'Неверные имя или пароль',
                'code': 'INVALID_CREDENTIALS',
                'details': {},
            }, status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            return Response({
                'error': 'Пользователь отключен',
                'code': 'INACTIVE_USER',
                'details': {},
            }, status=status.HTTP_401_UNAUTHORIZED)
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
        # При использовании JWT клиент просто удаляет токен; при refresh можно blacklist
        return Response({'detail': 'OK'})


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.select_related('role').all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'users'
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


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.prefetch_related('accesses').all()
    serializer_class = RoleSerializer
    permission_classes = [IsAdminOrHasAccess]
    required_access_key = 'users'
    filterset_fields = []
    search_fields = ['name']
    ordering_fields = ['id', 'name']
