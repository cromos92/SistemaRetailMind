"""
URLs para API móvil (JWT).

Prefijo real: /api/v1/mobile/ (retailmind/urls.py -> app/api/urls.py).
"""

from django.urls import path
from .views import (
    CodigoAutorizacionActualView,
    AjusteStockRapidoView,
    ProductoActualizarView,
    ProductoBuscarView,
    ProductoCatalogoView,
    ProductoVerificarEtiquetaView,
)

app_name = "mobile"

urlpatterns = [
    path("codigo-autorizacion/actual/", CodigoAutorizacionActualView.as_view(), name="codigo-autorizacion-actual"),
    path("ajuste-stock-rapido/", AjusteStockRapidoView.as_view(), name="ajuste-stock-rapido"),

    # Verificador de etiquetas (appNexoStaff)
    path("producto/buscar/", ProductoBuscarView.as_view(), name="producto-buscar"),
    path("producto/catalogo/", ProductoCatalogoView.as_view(), name="producto-catalogo"),
    path("producto/verificar-etiqueta/", ProductoVerificarEtiquetaView.as_view(), name="producto-verificar-etiqueta"),
    path("producto/actualizar/", ProductoActualizarView.as_view(), name="producto-actualizar"),
]
