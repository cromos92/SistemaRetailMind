from django.urls import path
from .views import (
    SkusPorEmpresaView,
    TallasPorArticuloView,
    StockMovimientosView,
    StockPorSkusView,
    HealthCheckExternalView,
    GuiasTallaExternalView,
    SucursalesPorEmpresaView,
)

urlpatterns = [
    # Contrato v1 — AllConnected
    path('skus/', SkusPorEmpresaView.as_view(), name='external-skus'),
    path('articulos/<str:articulo_codigo>/tallas/', TallasPorArticuloView.as_view(), name='external-tallas-articulo'),
    path('stock/movimientos/', StockMovimientosView.as_view(), name='external-stock-movimientos'),
    path('stock/por-skus/', StockPorSkusView.as_view(), name='external-stock-por-skus'),
    path('guias-talla/', GuiasTallaExternalView.as_view(), name='external-guias-talla'),
    path('sucursales/', SucursalesPorEmpresaView.as_view(), name='external-sucursales'),
    path('health/', HealthCheckExternalView.as_view(), name='external-health'),
]
