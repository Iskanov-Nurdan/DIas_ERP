from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.accounts.views import LoginView, MeView, LogoutView, UserViewSet
from apps.warehouse.views import WarehouseBatchViewSet

# Совместимость с axios: путь с ведущим «/» уходит на корень хоста без /api/ — дублируем ключевые маршруты.
_user_detail = UserViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update', 'put': 'update', 'delete': 'destroy'})
_user_access_patch = UserViewSet.as_view({'patch': 'update_access'})
_pack_from_otk = WarehouseBatchViewSet.as_view({'post': 'package'})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/openapi.json', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='schema-swagger-ui'),
    path('swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name='schema-swagger-ui-root'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='schema-redoc'),
    path('api/auth/login', LoginView.as_view(), name='auth-login'),
    path('api/me', MeView.as_view(), name='me'),
    path('api/auth/logout', LogoutView.as_view(), name='auth-logout'),
    path('api/', include('config.api_urls')),
    path('users/<int:pk>/', _user_detail, name='user-detail-root-alias'),
    path('users/<int:pk>/access/', _user_access_patch, name='user-access-root-alias'),
    path('warehouse/pack-from-otk/', _pack_from_otk, name='warehouse-pack-from-otk-alias'),
    path('warehouse/pack/', _pack_from_otk, name='warehouse-pack-alias'),
    path('batches/pack_from_otk/', _pack_from_otk, name='batches-pack-from-otk-alias'),
]
