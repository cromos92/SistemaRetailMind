"""
URLs para API Desktop - Autenticación y Configuración
=====================================================
"""

from django.urls import path
from .views import (
    DesktopLoginView,
    DesktopRefreshView,
    DesktopLogoutView,
    SucursalConfigView,
    SyncStatusView,
    HealthCheckView,
    SucursalesDisponiblesView,
)

app_name = 'desktop'

urlpatterns = [
    # Autenticación
    path('login/', DesktopLoginView.as_view(), name='login'),
    path('refresh/', DesktopRefreshView.as_view(), name='refresh'),
    path('logout/', DesktopLogoutView.as_view(), name='logout'),
    
    # Sucursales (antes del login para mostrar selector)
    path('sucursales/', SucursalesDisponiblesView.as_view(), name='sucursales-disponibles'),
    
    # Configuración
    path('sucursal/', SucursalConfigView.as_view(), name='sucursal-config'),
]
