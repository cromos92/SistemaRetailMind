"""
Service para iniciar el CHECKOUT de la app en el ecommerce destino.

Tras reservar puntos y crear el cupón, RetailMind llama al endpoint
``/api/v1/app-checkout/`` del ecommerce (server-to-server) con los items del
carrito, el cupón de puntos, el contacto y el **RUT** del cliente (que solo
RetailMind conoce). El ecommerce guarda esa intención y devuelve una URL de
checkout que la app abre en un WebView.

Usa ``ecommerce_http`` (pool + timeouts + circuit breaker). Nunca lanza.
"""
from __future__ import annotations

import logging

from app.services import ecommerce_http
from app.services.ecommerce_cupon_service import resolver_credencial

logger = logging.getLogger('app')

TIMEOUT_SEGUNDOS = 15
APP_CHECKOUT_PATH = '/api/v1/app-checkout/'


def iniciar_checkout(*, tienda, items, coupon_code, email, first_name,
                     last_name, phone, rut):
    """
    Pide al ecommerce una URL de checkout con el carrito + cupón + RUT precargados.
    `items` = lista de {'sku', 'quantity'}. Devuelve {ok, checkout_url} o
    {ok: False, error}. Nunca lanza.
    """
    cred = resolver_credencial(tienda)
    if cred is None:
        return {'ok': False, 'error': f'Tienda no configurada: {tienda}.'}

    payload = {
        'items': items,
        'coupon_code': coupon_code or '',
        'email': email or '',
        'first_name': first_name or '',
        'last_name': last_name or '',
        'phone': phone or '',
        'rut': rut or '',
    }
    try:
        r = ecommerce_http.request(cred, 'POST', APP_CHECKOUT_PATH, json=payload,
                                  timeout=TIMEOUT_SEGUNDOS)
    except ecommerce_http.CircuitOpen:
        return {'ok': False, 'error': 'La tienda no está disponible en este momento.'}
    except ecommerce_http.EcommerceHTTPError as exc:
        logger.warning('iniciar_checkout: fallo de red: %s', exc)
        return {'ok': False, 'error': f'No se pudo conectar con la tienda: {exc}'[:200]}

    if r.status_code in (200, 201):
        try:
            data = r.json()
        except ValueError:
            return {'ok': False, 'error': 'Respuesta inválida de la tienda.'}
        checkout_url = data.get('checkout_url')
        if not checkout_url:
            return {'ok': False, 'error': 'La tienda no devolvió URL de checkout.'}
        return {'ok': True, 'checkout_url': checkout_url}

    logger.warning('iniciar_checkout respondió HTTP %s: %s',
                   r.status_code, (r.text or '')[:200])
    return {'ok': False,
            'error': f'La tienda no pudo iniciar el checkout (HTTP {r.status_code}).'}
