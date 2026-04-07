from django.urls import path
from .views import (
    SkusPorEmpresaView,
    TallasPorArticuloView,
    StockMovimientosView,
    HealthCheckExternalView,
)

urlpatterns = [
    # Contrato v1 — AllConnected
    path('skus/', SkusPorEmpresaView.as_view(), name='external-skus'),
    path('articulos/<str:articulo_codigo>/tallas/', TallasPorArticuloView.as_view(), name='external-tallas-articulo'),
    path('stock/movimientos/', StockMovimientosView.as_view(), name='external-stock-movimientos'),
    path('health/', HealthCheckExternalView.as_view(), name='external-health'),
]
