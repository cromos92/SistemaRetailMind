"""
Autenticación para la API externa de RetailMind.

Soporta dos esquemas (ambos son equivalentes):
  - Header X-Api-Key: {key}          (legacy, primer formato implementado)
  - Header Authorization: Bearer {key}  (contrato oficial con AllConnected)

La clave se configura en .env → RETAILMIND_API_KEY.
"""

import hmac

from django.conf import settings
from django.contrib.auth.models import AnonymousUser

from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission


def _extract_key(request) -> str:
    """Extrae la API key de cualquiera de los dos headers soportados."""
    # Authorization: Bearer {key}
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[len('Bearer '):]

    # X-Api-Key: {key}  (fallback)
    return request.headers.get('X-Api-Key') or request.META.get('HTTP_X_API_KEY', '')


class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        key = _extract_key(request)
        expected = getattr(settings, 'RETAILMIND_API_KEY', '') or ''
        # Falla cerrado (sin clave configurada no autentica a nadie) y compara
        # en tiempo constante para no filtrar la clave por timing.
        if key and expected and hmac.compare_digest(str(key), str(expected)):
            return (AnonymousUser(), key)
        return None

    def authenticate_header(self, request):
        return 'Bearer'


class ApiKeyPermission(BasePermission):
    message = 'API key inválida o ausente.'

    def has_permission(self, request, view):
        return bool(request.auth)
