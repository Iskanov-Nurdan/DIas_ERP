from django.contrib import admin
from django.urls import path, include
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from apps.accounts.views import LoginView, MeView, LogoutView

schema_view = get_schema_view(
    openapi.Info(
        title='DIAS API',
        default_version='v1',
        description='REST API системы учёта производства пластика DIAS',
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/openapi.json', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('api/docs/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui-root'),
    path('api/redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('api/auth/login', LoginView.as_view(), name='auth-login'),
    path('api/me', MeView.as_view(), name='me'),
    path('api/auth/logout', LogoutView.as_view(), name='auth-logout'),
    path('api/', include('config.api_urls')),
]
