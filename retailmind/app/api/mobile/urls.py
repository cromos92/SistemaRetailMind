"""
URLs para API móvil (JWT).

Prefijo real: /api/v1/mobile/ (retailmind/urls.py -> app/api/urls.py).
"""

from django.urls import path
from .views import (
    CodigoAutorizacionActualView,
    AjusteStockRapidoView,
    MobileLoginView,
    MobileReenviarPinView,
    MobileVerificarPinView,
    ProductoActualizarView,
    ProductoBuscarView,
    ProductoCatalogoView,
    ProductoVerificarEtiquetaView,
)

app_name = "mobile"

urlpatterns = [
    # Login de la app de staff: SOLO PIN por correo, sin contraseña.
    # `login/` recibe usuario-o-correo + device_id y despacha el código;
    # los tokens salen EXCLUSIVAMENTE de `login/verificar-pin/`.
    # NO reutiliza /api/v1/desktop/login/ porque ese endpoint lo usa el POS Tauri:
    # quitarle la contraseña o meterle PIN dejaría a cada caja esperando un
    # correo para poder vender.
    path("login/", MobileLoginView.as_view(), name="login"),
    path("login/verificar-pin/", MobileVerificarPinView.as_view(), name="login-verificar-pin"),
    path("login/reenviar-pin/", MobileReenviarPinView.as_view(), name="login-reenviar-pin"),

    path("codigo-autorizacion/actual/", CodigoAutorizacionActualView.as_view(), name="codigo-autorizacion-actual"),
    path("ajuste-stock-rapido/", AjusteStockRapidoView.as_view(), name="ajuste-stock-rapido"),

    # Verificador de etiquetas (appNexoStaff)
    path("producto/buscar/", ProductoBuscarView.as_view(), name="producto-buscar"),
    path("producto/catalogo/", ProductoCatalogoView.as_view(), name="producto-catalogo"),
    path("producto/verificar-etiqueta/", ProductoVerificarEtiquetaView.as_view(), name="producto-verificar-etiqueta"),
    path("producto/actualizar/", ProductoActualizarView.as_view(), name="producto-actualizar"),
]
