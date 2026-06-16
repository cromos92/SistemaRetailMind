"""
URLs de la API de clientes finales (app móvil de fidelización).
Montadas en /api/v1/cliente/.
"""
from django.urls import path

from .views import (
    SolicitarOTPView,
    VincularCuentaView,
    VerificarOTPView,
    RefreshView,
    LogoutView,
    SaldoPuntosView,
    MovimientosPuntosView,
    GiftCardsView,
    PerfilView,
    CarnetView,
)

app_name = 'cliente'

urlpatterns = [
    # Auth (sin token)
    path('auth/solicitar-otp/', SolicitarOTPView.as_view(), name='solicitar-otp'),
    path('auth/vincular/', VincularCuentaView.as_view(), name='vincular'),
    path('auth/verificar-otp/', VerificarOTPView.as_view(), name='verificar-otp'),
    path('auth/refresh/', RefreshView.as_view(), name='refresh'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),

    # Datos del cliente (requieren token de cliente)
    path('puntos/saldo/', SaldoPuntosView.as_view(), name='puntos-saldo'),
    path('puntos/movimientos/', MovimientosPuntosView.as_view(), name='puntos-movimientos'),
    path('giftcards/', GiftCardsView.as_view(), name='giftcards'),
    path('perfil/', PerfilView.as_view(), name='perfil'),
    path('carnet/', CarnetView.as_view(), name='carnet'),
]
