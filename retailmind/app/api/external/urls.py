from django.urls import path
from .views import (
    SkusPorEmpresaView,
    TallasPorArticuloView,
    StockMovimientosView,
    StockPorSkusView,
    StockGlobalView,
    HealthCheckExternalView,
    GuiasTallaExternalView,
    SucursalesPorEmpresaView,
    PreciosActualesView,
    NovedadesView,
)

urlpatterns = [
    # Contrato v1 — AllConnected
    path('skus/', SkusPorEmpresaView.as_view(), name='external-skus'),
    path('articulos/<str:articulo_codigo>/tallas/', TallasPorArticuloView.as_view(), name='external-tallas-articulo'),
    path('stock/movimientos/', StockMovimientosView.as_view(), name='external-stock-movimientos'),
    path('stock/por-skus/', StockPorSkusView.as_view(), name='external-stock-por-skus'),
    path('stock/global/', StockGlobalView.as_view(), name='external-stock-global'),
    path('guias-talla/', GuiasTallaExternalView.as_view(), name='external-guias-talla'),
    path('sucursales/', SucursalesPorEmpresaView.as_view(), name='external-sucursales'),
    path('precios-actuales/', PreciosActualesView.as_view(), name='external-precios-actuales'),
    path('novedades/', NovedadesView.as_view(), name='external-novedades'),
    path('health/', HealthCheckExternalView.as_view(), name='external-health'),
]
