"""
Throttles para los endpoints de autenticación desktop/móvil staff.

`desktop/login/` y `desktop/sucursales/` validan credenciales sin sesión, por
lo que sin throttle sirven de oráculo para fuerza bruta. La tasa se define en
la clase (no en settings) para no tocar configuración global.
"""

from rest_framework.throttling import AnonRateThrottle


class DesktopAuthRateThrottle(AnonRateThrottle):
    """Máx 10 intentos de autenticación por minuto por IP."""

    scope = "desktop_auth"
    rate = "10/min"
