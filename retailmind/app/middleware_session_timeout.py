"""
Middleware de timeout por inactividad de sesión.

Cierra la sesión del usuario si no ha interactuado en más de
SESSION_INACTIVITY_TIMEOUT segundos (configurable en settings).

Para no generar un UPDATE a django_session en cada request (costoso en
modo POS táctil con cientos de peticiones AJAX por turno), el timestamp
se actualiza como máximo cada SESSION_ACTIVITY_THROTTLE segundos.
"""
import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.http import JsonResponse

_LAST_ACTIVITY_KEY = '_last_activity'


class SessionTimeoutMiddleware:
    """Expira la sesión tras un periodo de inactividad."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = getattr(settings, 'SESSION_INACTIVITY_TIMEOUT', 3600)
        # Throttle: solo actualizar el timestamp cada N segundos (default 5 min)
        self.throttle = getattr(settings, 'SESSION_ACTIVITY_THROTTLE', 300)

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        now = time.time()
        last = request.session.get(_LAST_ACTIVITY_KEY)

        if last is not None and (now - last) > self.timeout:
            logout(request)
            return self._expired_response(request)

        # Throttle: solo escribir si pasó más de self.throttle desde el último save
        if last is None or (now - last) > self.throttle:
            request.session[_LAST_ACTIVITY_KEY] = now

        return self.get_response(request)

    @staticmethod
    def _expired_response(request):
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.content_type == 'application/json'
            or request.headers.get('Accept', '').startswith('application/json')
        )
        if is_ajax:
            return JsonResponse(
                {
                    'success': False,
                    'error': True,
                    'session_expired': True,
                    'mensaje': 'Tu sesión ha expirado por inactividad. '
                               'Por favor, inicia sesión nuevamente.',
                },
                status=401,
            )
        return redirect(settings.LOGIN_URL)
