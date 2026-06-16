"""
Permisos para la API de clientes finales.
"""
from rest_framework.permissions import BasePermission

from app.models import CuentaClienteApp


class IsClienteApp(BasePermission):
    """
    Permite solo a una CuentaClienteApp autenticada (refuerza el aislamiento
    sobre `ClienteJWTAuthentication`: un `request.user` que no sea cliente — p. ej.
    una sesión de staff colada por SessionAuthentication — queda fuera).
    """

    message = 'Se requiere una cuenta de cliente.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and isinstance(request.user, CuentaClienteApp)
            and request.user.is_authenticated
        )
