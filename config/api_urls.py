from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts.views import UserViewSet, RoleViewSet
from apps.materials.views import (
    RawMaterialViewSet,
    IncomingViewSet,
    MaterialsBalancesView,
    MaterialsMovementsView,
)
from apps.chemistry.views import (
    ChemistryCatalogViewSet,
    ChemistryTaskViewSet,
    ChemistryBalancesView,
    ChemistryBatchViewSet,
)
from apps.recipes.views import PlasticProfileViewSet, RecipeViewSet
from apps.production.views import (
    LineViewSet,
    BatchViewSet,
    ProductionRequestViewSet,
    RecipeRunViewSet,
    ShiftViewSet, ShiftHistoryView, ShiftComplaintViewSet,
)
from apps.warehouse.gp_packaging_views import GpPackageViewSet, GpUnpackedBalanceView
from apps.warehouse.operations_views import WarehouseOperationsView
from apps.warehouse.views import WarehouseBatchViewSet
from apps.sales.views import (
    ClientViewSet,
    ClientFinancialSummaryView,
    ClientPriceViewSet,
    DefectRecordViewSet,
    OrderReservationViewSet,
    OrderViewSet,
    PaymentViewSet,
    PriceListViewSet,
    ReturnViewSet,
    ReworkRequestViewSet,
    SaleViewSet,
)
from apps.otk.views import OtkPendingView
from apps.analytics.other_expenses_views import AnalyticsOtherExpenseViewSet
from apps.analytics.views import (
    AnalyticsSummaryView,
    AnalyticsRevenueDetailsView,
    AnalyticsSalesCostDetailsView,
    AnalyticsProductUnitCostsView,
    AnalyticsProductionCostDetailsView,
    AnalyticsPurchaseDetailsView,
    AnalyticsProfitDetailsView,
    AnalyticsDebtDetailsView,
    AnalyticsOtkDetailsView,
    AnalyticsWriteoffDetailsView,
    AnalyticsDefectView,
    AnalyticsReworkView,
    AnalyticsClientProfitabilityView,
    AnalyticsReceivablesView,
)
from apps.activity.views import (
    ActivityMyView,
    ActivityMyRetrieveView,
    ActivityAdminView,
    ActivityAdminRetrieveView,
)
from apps.workshop.views import BlankProductionRunViewSet, PreparedBlankViewSet, WorkshopBlankViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'lines', LineViewSet, basename='line')
router.register(r'raw-materials', RawMaterialViewSet, basename='rawmaterial')
router.register(r'incoming', IncomingViewSet, basename='incoming')
router.register(r'materials/balances', MaterialsBalancesView, basename='materials-balances')
router.register(r'materials/movements', MaterialsMovementsView, basename='materials-movements')
router.register(r'chemistry/elements', ChemistryCatalogViewSet, basename='chemistry-elements')
router.register(r'chemistry/tasks', ChemistryTaskViewSet, basename='chemistry-tasks')
router.register(r'chemistry/balances', ChemistryBalancesView, basename='chemistry-balances')
router.register(r'chemistry/batches', ChemistryBatchViewSet, basename='chemistry-batches')
router.register(r'production/recipe-runs', RecipeRunViewSet, basename='production-recipe-run')
router.register(r'plastic-profiles', PlasticProfileViewSet, basename='plastic-profile')
router.register(r'recipes', RecipeViewSet, basename='recipe')
router.register(r'batches', BatchViewSet, basename='batch')
router.register(r'production/requests', ProductionRequestViewSet, basename='production-request')
router.register(r'warehouse/batches', WarehouseBatchViewSet, basename='warehouse-batch')
router.register(r'warehouse/gp-packages', GpPackageViewSet, basename='warehouse-gp-package')
router.register(r'clients', ClientViewSet, basename='client')
router.register(r'sales', SaleViewSet, basename='sale')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'returns', ReturnViewSet, basename='return')
router.register(r'defects', DefectRecordViewSet, basename='defect')
router.register(r'rework-requests', ReworkRequestViewSet, basename='rework-request')
router.register(r'otk/pending', OtkPendingView, basename='otk-pending')
router.register(r'analytics/summary', AnalyticsSummaryView, basename='analytics-summary')
router.register(r'analytics/revenue-details', AnalyticsRevenueDetailsView, basename='analytics-revenue-details')
router.register(r'analytics/sales-cost-details', AnalyticsSalesCostDetailsView, basename='analytics-sales-cost-details')
router.register(
    r'analytics/product-unit-costs',
    AnalyticsProductUnitCostsView,
    basename='analytics-product-unit-costs',
)
router.register(
    r'analytics/other-expenses',
    AnalyticsOtherExpenseViewSet,
    basename='analytics-other-expense',
)
router.register(
    r'analytics/production-cost-details',
    AnalyticsProductionCostDetailsView,
    basename='analytics-production-cost-details',
)
router.register(r'analytics/purchase-details', AnalyticsPurchaseDetailsView, basename='analytics-purchase-details')
router.register(r'analytics/profit-details', AnalyticsProfitDetailsView, basename='analytics-profit-details')
router.register(r'analytics/debt-details', AnalyticsDebtDetailsView, basename='analytics-debt-details')
router.register(r'analytics/otk-details', AnalyticsOtkDetailsView, basename='analytics-otk-details')
router.register(r'analytics/writeoff-details', AnalyticsWriteoffDetailsView, basename='analytics-writeoff-details')
router.register(r'analytics/defect-analytics', AnalyticsDefectView, basename='analytics-defect')
router.register(r'analytics/rework-analytics', AnalyticsReworkView, basename='analytics-rework')
router.register(r'analytics/client-profitability', AnalyticsClientProfitabilityView, basename='analytics-client-profitability')
router.register(r'analytics/receivables', AnalyticsReceivablesView, basename='analytics-receivables')
router.register(r'price-lists', PriceListViewSet, basename='price-list')
router.register(r'client-prices', ClientPriceViewSet, basename='client-price')
router.register(r'order-reservations', OrderReservationViewSet, basename='order-reservation')
router.register(r'client-financial-summary', ClientFinancialSummaryView, basename='client-financial-summary')
router.register(r'shifts', ShiftViewSet, basename='shift')
router.register(r'workshop/blanks', WorkshopBlankViewSet, basename='workshop-blank')
router.register(r'workshop/prepared-blanks', PreparedBlankViewSet, basename='workshop-prepared-blank')
router.register(
    r'workshop/blank-production-runs',
    BlankProductionRunViewSet,
    basename='workshop-blank-production-run',
)

# Фиксированные пути регистрируются ДО роутера, чтобы не конфликтовать с <pk>
urlpatterns = [
    path('warehouse/gp-unpacked-balance/', GpUnpackedBalanceView.as_view(), name='warehouse-gp-unpacked-balance'),
    path('warehouse/operations/', WarehouseOperationsView.as_view(), name='warehouse-operations'),
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
