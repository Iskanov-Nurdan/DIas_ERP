from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts.views import UserViewSet, RoleViewSet
from apps.materials.views import RawMaterialViewSet, IncomingViewSet, MaterialsBalancesView
from apps.chemistry.views import ChemistryCatalogViewSet, ChemistryTaskViewSet, ChemistryStockViewSet
from apps.recipes.views import RecipeViewSet
from apps.production.views import (
    LineViewSet,
    BatchViewSet,
    RecipeRunViewSet,
    ShiftViewSet, ShiftHistoryView, ShiftComplaintViewSet,
)
from apps.warehouse.views import WarehouseBatchViewSet
from apps.sales.views import ClientViewSet, SaleViewSet
from apps.otk.views import OtkPendingView
from apps.analytics.views import (
    AnalyticsSummaryView,
    AnalyticsRevenueDetailsView,
    AnalyticsExpenseDetailsView,
    AnalyticsWriteoffDetailsView,
)
from apps.activity.views import (
    ActivityMyView,
    ActivityMyRetrieveView,
    ActivityAdminView,
    ActivityAdminRetrieveView,
)

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'lines', LineViewSet, basename='line')
router.register(r'raw-materials', RawMaterialViewSet, basename='rawmaterial')
router.register(r'incoming', IncomingViewSet, basename='incoming')
router.register(r'materials/balances', MaterialsBalancesView, basename='materials-balances')
router.register(r'chemistry/elements', ChemistryCatalogViewSet, basename='chemistry-elements')
router.register(r'chemistry/tasks', ChemistryTaskViewSet, basename='chemistry-tasks')
router.register(r'chemistry/balances', ChemistryStockViewSet, basename='chemistry-balances')
router.register(r'chemistry/recipe-runs', RecipeRunViewSet, basename='chemistry-recipe-run')
router.register(r'recipes', RecipeViewSet, basename='recipe')
router.register(r'batches', BatchViewSet, basename='batch')
router.register(r'warehouse/batches', WarehouseBatchViewSet, basename='warehouse-batch')
router.register(r'clients', ClientViewSet, basename='client')
router.register(r'sales', SaleViewSet, basename='sale')
router.register(r'otk/pending', OtkPendingView, basename='otk-pending')
router.register(r'analytics/summary', AnalyticsSummaryView, basename='analytics-summary')
router.register(r'analytics/revenue-details', AnalyticsRevenueDetailsView, basename='analytics-revenue-details')
router.register(r'analytics/expense-details', AnalyticsExpenseDetailsView, basename='analytics-expense-details')
router.register(r'analytics/writeoff-details', AnalyticsWriteoffDetailsView, basename='analytics-writeoff-details')
router.register(r'shifts', ShiftViewSet, basename='shift')

# Фиксированные пути регистрируются ДО роутера, чтобы не конфликтовать с <pk>
urlpatterns = [
    path(
        'shifts/complaints/',
        ShiftComplaintViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shift-complaints',
    ),
    path('shifts/history/', ShiftHistoryView.as_view({'get': 'list'}), name='shift-history'),
    path('activity/my/<int:pk>/', ActivityMyRetrieveView.as_view(), name='activity-my-detail'),
    path('activity/my/', ActivityMyView.as_view({'get': 'list'}), name='activity-my'),
    path('activity/<int:pk>/', ActivityAdminRetrieveView.as_view(), name='activity-detail'),
    path('activity/', ActivityAdminView.as_view({'get': 'list'}), name='activity'),
    path('', include(router.urls)),
]
