"""
URLs para API Sync - Sincronización Bidireccional
=================================================
"""

from django.urls import path
from .views import (
    ProductosSyncView,
    CategoriasSyncView,
    VendedoresSyncView,
    ConfiguracionSyncView,
    TicketsSyncView,
    CuadraturasSyncView,
    MovimientosCajaSyncView,
    SyncStatusDetailView,
)

app_name = 'sync'

urlpatterns = [
    # Descarga (Server → Desktop)
    path('productos/', ProductosSyncView.as_view(), name='productos'),
    path('categorias/', CategoriasSyncView.as_view(), name='categorias'),
    path('vendedores/', VendedoresSyncView.as_view(), name='vendedores'),
    path('configuracion/', ConfiguracionSyncView.as_view(), name='configuracion'),
    
    # Subida (Desktop → Server)
    path('tickets/', TicketsSyncView.as_view(), name='tickets'),
    path('cuadraturas/', CuadraturasSyncView.as_view(), name='cuadraturas'),
    path('movimientos-caja/', MovimientosCajaSyncView.as_view(), name='movimientos-caja'),
    
    # Estado
    path('status/detail/', SyncStatusDetailView.as_view(), name='status-detail'),
]
