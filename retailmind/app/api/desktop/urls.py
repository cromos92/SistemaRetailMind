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
from .fidelizacion_views import (
    SaldoPuntosView,
    GiftCardConsultaView,
    GiftCardValidarView,
    ValeCanjeConsultaView,
    ValeCanjeAplicarView,
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

    # Fidelización y Gift Cards
    path('fidelizacion/saldo/', SaldoPuntosView.as_view(), name='fidelizacion-saldo'),
    path('giftcards/validar/', GiftCardValidarView.as_view(), name='giftcards-validar'),
    path('giftcards/<str:codigo>/', GiftCardConsultaView.as_view(), name='giftcards-consulta'),

    # Canje con código (vale de puntos validado en el POS)
    path('canje/aplicar/', ValeCanjeAplicarView.as_view(), name='canje-aplicar'),
    path('canje/<str:codigo>/', ValeCanjeConsultaView.as_view(), name='canje-consulta'),
]
