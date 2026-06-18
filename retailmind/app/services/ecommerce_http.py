"""
Capa HTTP resiliente hacia los ecommerce (Realsport/Paola).

Objetivo: que un pico de usuarios en la app NO tumbe ni a RetailMind ni a los
ecommerce. Tres protecciones:

1. **Pool de conexiones** (requests.Session compartida): reusa sockets en vez de
   abrir uno nuevo por request bajo carga.
2. **Timeouts cortos**: un ecommerce lento no debe bloquear workers de RetailMind
   indefinidamente (gunicorn tiene N workers; si todos se quedan colgados en una
   llamada lenta, RetailMind deja de responder a TODO).
3. **Circuit breaker por tienda**: si un ecommerce falla repetidamente, se ABRE el
   circuito por un cooldown y RetailMind deja de martillarlo (fail-fast). Así el
   ecommerce caído puede recuperarse y RetailMind responde rápido (sirviendo cache
   stale en lectura) en vez de acumular requests colgados.

Es thread-safe a nivel "suficiente": el estado del breaker es un dict de proceso;
una carrera benigna solo deja pasar uno o dos requests de más.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger('app')

try:
    import requests  # type: ignore
    from requests.adapters import HTTPAdapter  # type: ignore
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover
    requests = None  # type: ignore
    HTTPAdapter = None  # type: ignore
    _REQUESTS_OK = False

# --- Circuit breaker ---
CB_THRESHOLD = 5        # fallos consecutivos (timeout/conexión/5xx) para abrir
CB_COOLDOWN_SEG = 30    # tiempo que el circuito queda abierto

_session = None
_session_lock = threading.Lock()
_cb: dict[str, dict] = {}   # {tienda: {'fails': int, 'open_until': float}}


class EcommerceHTTPError(Exception):
    """Fallo de red/HTTP hacia el ecommerce (timeout, conexión, 5xx)."""


class CircuitOpen(EcommerceHTTPError):
    """El circuito de esa tienda está abierto: no se intenta la llamada."""


def _get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                # max_retries=0: NO reintentar (un reintento bajo carga multiplica
                # la presión sobre un ecommerce ya en problemas).
                adapter = HTTPAdapter(
                    pool_connections=10, pool_maxsize=20, max_retries=0)
                s.mount('http://', adapter)
                s.mount('https://', adapter)
                _session = s
    return _session


def circuito_abierto(tienda: str) -> bool:
    c = _cb.get(tienda)
    return bool(c and c.get('open_until', 0) > time.monotonic())


def _registrar(tienda: str, ok: bool) -> None:
    c = _cb.setdefault(tienda, {'fails': 0, 'open_until': 0.0})
    if ok:
        c['fails'] = 0
        c['open_until'] = 0.0
    else:
        c['fails'] += 1
        if c['fails'] >= CB_THRESHOLD:
            c['open_until'] = time.monotonic() + CB_COOLDOWN_SEG
            logger.warning('Circuito ABIERTO para ecommerce %s por %ss',
                           tienda, CB_COOLDOWN_SEG)


def request(cred, method, path, *, params=None, json=None, timeout=15):
    """
    Llama al ecommerce de `cred` con pooling + circuito. Devuelve la Response.
    Lanza CircuitOpen si el circuito está abierto, EcommerceHTTPError si falla la
    red. Un 4xx NO abre el circuito (es problema del request, no del ecommerce);
    un 5xx/timeout/conexión SÍ.
    """
    if not _REQUESTS_OK:
        raise EcommerceHTTPError("Falta el paquete 'requests' en este entorno.")
    tienda = getattr(cred, 'codigo', '') or ''
    if circuito_abierto(tienda):
        raise CircuitOpen(f'Circuito abierto para la tienda {tienda}.')

    url = f"{cred.url_api.rstrip('/')}{path}"
    headers = {
        cred.header_name or 'X-AllConnected-Key': cred.api_key,
        'Accept': 'application/json',
        'User-Agent': 'RetailMind-App/1.0',
    }
    if json is not None:
        headers['Content-Type'] = 'application/json'
    try:
        r = _get_session().request(
            method, url, params=params, json=json, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        _registrar(tienda, False)
        raise EcommerceHTTPError(str(exc)[:200])
    _registrar(tienda, r.status_code < 500)
    return r
