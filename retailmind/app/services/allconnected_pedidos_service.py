"""
Service para TRAER (pull) pedidos pendientes desde AllConnected
(VicentAllEcommercesConected) hacia RetailMind.

A diferencia del flujo normal (AllConnected hace ``POST`` de cada pedido a
``/api/ecommerce/pedidos/``), este service permite que RetailMind salga
activamente a consultar los pedidos pendientes y los ingrese, reutilizando la
MISMA lógica de ingesta (``app.views_ecommerce._ingestar_pedido_dict``): stock,
sub_estado, historial, métrica e idempotencia.

Configuración en settings / .env:
    ALLCONNECTED_API_BASE_URL    = "https://<allconnected-host>"
    ALLCONNECTED_API_KEY         = "<key de auth saliente>"
    ALLCONNECTED_API_HEADER_NAME = "X-AllConnected-Key"        (default)
    ALLCONNECTED_PEDIDOS_PATH    = "/api/pedidos/pendientes/"  (default)

Si ``ALLCONNECTED_API_BASE_URL`` está vacía, el pull está deshabilitado y se
devuelve ``{ok: True, configurado: False}`` (la UI solo refresca la tabla con
los pedidos ya recibidos por push).

Contrato esperado del endpoint remoto:
    GET <base><path>?estado=PENDIENTE[&rut_empresa=XX-X]
    Header: <header_name>: <api_key>
    Respuesta 200 JSON:
        [ {pedido}, ... ]   ó   {"pedidos": [ {pedido}, ... ]}
    donde {pedido} = MISMO shape que el body del POST push
    (numero_pedido_canal, canal_origen, sucursal_id, cliente_nombre,
     items[...], subtotal/descuento/costo_envio/total, rut_empresa).
"""
from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

# ``requests`` se importa lazy: si el venv aún no lo tiene, Django igual arranca
# y solo el pull falla con un mensaje claro (mismo patrón que
# realsport_imagenes_service).
try:
    import requests  # type: ignore
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover
    requests = None  # type: ignore
    _REQUESTS_OK = False

logger = logging.getLogger('app')

TIMEOUT_SEGUNDOS = 30


def _config() -> dict:
    return {
        'base_url': (getattr(settings, 'ALLCONNECTED_API_BASE_URL', '') or '').strip().rstrip('/'),
        'api_key': getattr(settings, 'ALLCONNECTED_API_KEY', '') or '',
        'header_name': getattr(settings, 'ALLCONNECTED_API_HEADER_NAME', '') or 'X-AllConnected-Key',
        'pedidos_path': getattr(settings, 'ALLCONNECTED_PEDIDOS_PATH', '') or '/api/pedidos/pendientes/',
    }


def _extraer_lista(data) -> list:
    """Acepta una lista directa o un dict ``{'pedidos': [...]}``. Devuelve lista."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        pedidos = data.get('pedidos')
        if isinstance(pedidos, list):
            return pedidos
    return []


def traer_pedidos_pendientes(rut_empresa: Optional[str] = None) -> dict:
    """
    Consulta AllConnected e ingesta cada pedido vía ``_ingestar_pedido_dict``.

    Devuelve un dict listo para ``JsonResponse``:
        {ok, configurado, total, nuevos, ya_existian, errores: [...], detalle}
    Nunca lanza.
    """
    cfg = _config()

    if not cfg['base_url']:
        return {
            'ok': True,
            'configurado': False,
            'total': 0,
            'nuevos': 0,
            'ya_existian': 0,
            'errores': [],
            'detalle': 'Pull no configurado (ALLCONNECTED_API_BASE_URL vacío). '
                       'Se muestran los pedidos ya recibidos por push.',
        }

    if not _REQUESTS_OK:
        return {
            'ok': False,
            'configurado': True,
            'total': 0, 'nuevos': 0, 'ya_existian': 0, 'errores': [],
            'error': "Falta el paquete 'requests' en este entorno.",
        }

    url = f"{cfg['base_url']}{cfg['pedidos_path']}"
    headers = {
        cfg['header_name']: cfg['api_key'],
        'Accept': 'application/json',
        'User-Agent': 'RetailMind-PedidosPull/1.0',
    }
    params = {'estado': 'PENDIENTE'}
    if rut_empresa:
        params['rut_empresa'] = rut_empresa

    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT_SEGUNDOS)
    except requests.RequestException as exc:
        logger.warning('traer_pedidos_pendientes: conexión fallida a %s: %s', url, exc)
        return {
            'ok': False, 'configurado': True,
            'total': 0, 'nuevos': 0, 'ya_existian': 0, 'errores': [],
            'error': f'No se pudo conectar a AllConnected: {exc}'[:300],
        }

    if r.status_code != 200:
        return {
            'ok': False, 'configurado': True,
            'total': 0, 'nuevos': 0, 'ya_existian': 0, 'errores': [],
            'error': f'AllConnected respondió HTTP {r.status_code}: {(r.text or "")[:200]}',
        }

    try:
        payload = r.json()
    except ValueError as exc:
        return {
            'ok': False, 'configurado': True,
            'total': 0, 'nuevos': 0, 'ya_existian': 0, 'errores': [],
            'error': f'Respuesta de AllConnected no es JSON: {exc}',
        }

    pedidos = _extraer_lista(payload)

    # Import lazy para evitar import circular con views_ecommerce.
    from app.views_ecommerce import _ingestar_pedido_dict

    nuevos = 0
    ya_existian = 0
    errores = []
    for idx, pedido in enumerate(pedidos):
        if not isinstance(pedido, dict):
            errores.append({'indice': idx, 'error': 'el item no es un objeto JSON'})
            continue
        resultado = _ingestar_pedido_dict(pedido)
        if not resultado.get('ok'):
            errores.append({
                'indice': idx,
                'numero_pedido_canal': pedido.get('numero_pedido_canal'),
                'error': resultado.get('error', 'error desconocido'),
            })
        elif resultado.get('ya_existia'):
            ya_existian += 1
        else:
            nuevos += 1

    logger.info(
        'Pull AllConnected: %s pedidos (%s nuevos, %s existentes, %s errores)',
        len(pedidos), nuevos, ya_existian, len(errores),
    )

    return {
        'ok': True,
        'configurado': True,
        'total': len(pedidos),
        'nuevos': nuevos,
        'ya_existian': ya_existian,
        'errores': errores,
        'detalle': f'{nuevos} nuevos, {ya_existian} ya existían, {len(errores)} con error.',
    }
