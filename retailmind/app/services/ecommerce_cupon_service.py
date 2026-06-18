"""
Service para crear/desactivar CUPONES en el ecommerce destino (Realsport/Paola).

Cada reserva de puntos de la app se materializa como un cupón de **monto fijo** y
**un solo uso** en el ecommerce. Así el checkout web descuenta los puntos como
pesos y la pasarela (Webpay/Mercado Pago) cobra solo la parte en dinero.

Usa la capa ``ecommerce_http`` (pool de conexiones + timeouts + circuit breaker)
para no apilar workers de RetailMind si el ecommerce está lento/caído. Nunca lanza.
"""
from __future__ import annotations

import logging

from django.db.models import Q

from app.models import CredencialesEcommerce
from app.services import ecommerce_http

logger = logging.getLogger('app')

TIMEOUT_SEGUNDOS = 12
COUPONS_PATH = '/api/v1/coupons/'


def resolver_credencial(tienda):
    """CredencialesEcommerce activa de la tienda (por `codigo` o `tipo`), o None."""
    clave = (tienda or '').strip().lower()
    if not clave:
        return None
    return (
        CredencialesEcommerce.objects
        .filter(activo=True)
        .filter(Q(codigo=clave) | Q(tipo=clave))
        .order_by('-prioridad')
        .first()
    )


def crear_cupon_puntos(tienda, codigo, monto, expira_en):
    """
    Crea (o reusa, idempotente) un cupón de monto fijo `monto` (pesos) con código
    `codigo`, válido hasta `expira_en`. Devuelve {ok, status, ...}. Nunca lanza.
    """
    cred = resolver_credencial(tienda)
    if cred is None:
        return {'ok': False, 'error': f'Tienda no configurada: {tienda}.'}

    payload = {
        'code': codigo,
        'amount': int(monto),
        'max_uses': 1,
        'valid_until': expira_en.isoformat(),
    }
    try:
        r = ecommerce_http.request(cred, 'POST', COUPONS_PATH, json=payload,
                                  timeout=TIMEOUT_SEGUNDOS)
    except ecommerce_http.CircuitOpen:
        return {'ok': False, 'error': 'La tienda no está disponible en este momento.'}
    except ecommerce_http.EcommerceHTTPError as exc:
        logger.warning('crear_cupon_puntos: fallo de red: %s', exc)
        return {'ok': False, 'error': f'No se pudo conectar con la tienda: {exc}'[:200]}

    if r.status_code in (200, 201):
        return {'ok': True, 'status': r.status_code}
    logger.warning('crear_cupon_puntos respondió HTTP %s: %s',
                   r.status_code, (r.text or '')[:200])
    return {
        'ok': False,
        'status': r.status_code,
        'error': f'La tienda rechazó el cupón (HTTP {r.status_code}).',
    }


def desactivar_cupon(tienda, codigo):
    """
    Desactiva un cupón en el ecommerce (PATCH is_active=False) cuando la reserva
    expira o se libera. Devuelve {ok, status}. Nunca lanza. Un 404 se considera ok
    (el cupón ya no existe → nada que desactivar).
    """
    cred = resolver_credencial(tienda)
    if cred is None:
        return {'ok': False, 'error': f'Tienda no configurada: {tienda}.'}

    try:
        r = ecommerce_http.request(cred, 'PATCH', f'{COUPONS_PATH}{codigo}/',
                                  json={'is_active': False}, timeout=TIMEOUT_SEGUNDOS)
    except ecommerce_http.CircuitOpen:
        return {'ok': False, 'error': 'circuito abierto'}
    except ecommerce_http.EcommerceHTTPError as exc:
        logger.warning('desactivar_cupon: fallo de red: %s', exc)
        return {'ok': False, 'error': str(exc)[:200]}
    return {'ok': r.status_code in (200, 404), 'status': r.status_code}
