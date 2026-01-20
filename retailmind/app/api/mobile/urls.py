"""
URLs para API móvil (JWT).
"""

from django.urls import path
from .views import CodigoAutorizacionActualView, AjusteStockRapidoView

app_name = "mobile"

urlpatterns = [
    path("codigo-autorizacion/actual/", CodigoAutorizacionActualView.as_view(), name="codigo-autorizacion-actual"),
    path("ajuste-stock-rapido/", AjusteStockRapidoView.as_view(), name="ajuste-stock-rapido"),
]
