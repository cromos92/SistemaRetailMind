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
    CotizarCanjeView,
    ReservarPuntosView,
    IniciarCheckoutView,
    CheckoutEstadoView,
    CancelarCheckoutView,
    GenerarValeCanjeView,
    MisCanjesView,
    CanjeDetalleView,
    AnularCanjeView,
    GiftCardsView,
    PerfilView,
    CarnetView,
    CatalogoListView,
    CategoriasView,
    CatalogoDetalleView,
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
    path('puntos/cotizar/', CotizarCanjeView.as_view(), name='puntos-cotizar'),
    path('puntos/reservar/', ReservarPuntosView.as_view(), name='puntos-reservar'),
    path('checkout/iniciar/', IniciarCheckoutView.as_view(), name='checkout-iniciar'),
    path('checkout/estado/<int:reserva_id>/', CheckoutEstadoView.as_view(), name='checkout-estado'),
    path('checkout/cancelar/<int:reserva_id>/', CancelarCheckoutView.as_view(), name='checkout-cancelar'),

    # Canje con código (vale para tienda física)
    path('canje/generar/', GenerarValeCanjeView.as_view(), name='canje-generar'),
    path('canje/mis-canjes/', MisCanjesView.as_view(), name='canje-mis-canjes'),
    path('canje/<str:codigo>/', CanjeDetalleView.as_view(), name='canje-detalle'),
    path('canje/<str:codigo>/anular/', AnularCanjeView.as_view(), name='canje-anular'),

    path('giftcards/', GiftCardsView.as_view(), name='giftcards'),
    path('perfil/', PerfilView.as_view(), name='perfil'),
    path('carnet/', CarnetView.as_view(), name='carnet'),

    # Tienda / catálogo (proxy del ecommerce)
    path('catalogo/', CatalogoListView.as_view(), name='catalogo'),
    path('catalogo/categorias/', CategoriasView.as_view(), name='catalogo-categorias'),
    path('catalogo/<str:sku>/', CatalogoDetalleView.as_view(), name='catalogo-detalle'),
]
