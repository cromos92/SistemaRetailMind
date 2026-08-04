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
    ALLCONNECTED_PEDIDOS_PATH    = "/app/pedidos/pendientes/"  (default)

Si ``ALLCONNECTED_API_BASE_URL`` está vacía, el pull está deshabilitado y se
devuelve ``{ok: True, configurado: False}`` (la UI solo refresca la tabla con
los pedidos ya recibidos por push).

Contrato esperado del endpoint remoto:
    GET <base><path>[?rut_empresa=XX-X&desde=YYYY-MM-DD&hasta=YYYY-MM-DD]
    Header: <header_name>: <api_key>
    Respuesta 200 JSON:
        [ {pedido}, ... ]   ó   {"pedidos": [ {pedido}, ... ]}
    donde {pedido} = MISMO shape que el body del POST push
    (numero_pedido_canal, canal_origen, sucursal_id, cliente_nombre,
     items[...], subtotal/descuento/impuestos/costo_envio/total, rut_empresa).
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

TIMEOUT_SEGUNDOS = 90  # el endpoint remoto tarda ~34s en el mes completo; antes 30


def _config() -> dict:
    return {
        'base_url': (getattr(settings, 'ALLCONNECTED_API_BASE_URL', '') or '').strip().rstrip('/'),
        'api_key': getattr(settings, 'ALLCONNECTED_API_KEY', '') or '',
        'header_name': getattr(settings, 'ALLCONNECTED_API_HEADER_NAME', '') or 'X-AllConnected-Key',
        'pedidos_path': getattr(settings, 'ALLCONNECTED_PEDIDOS_PATH', '') or '/app/pedidos/pendientes/',
        'estados_path': getattr(settings, 'ALLCONNECTED_ESTADOS_PATH', '') or '/app/pedidos/estados/',
    }


# Estados de AC que terminan el pedido sin venta: en RM el pedido debe quedar
# CANCELADO (fuera de la cola de facturación). Espejo de ESTADOS_CANCELADOS_PULL
# del lado AllConnected (system/orders/retailmind_connector.py).
ESTADOS_CANAL_CANCELADOS = ('CANCELADO', 'DEVUELTO', 'REEMBOLSADO')

# Lookback de la sincronización de estados: qué tan atrás mirar los PENDIENTES
# locales. 120 días cubre con holgura la cola zombie observada.
SYNC_ESTADOS_LOOKBACK_DIAS = 120
_LOTE_ESTADOS = 300


def _extraer_lista(data) -> list:
    """Acepta una lista directa o un dict ``{'pedidos': [...]}``. Devuelve lista."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        pedidos = data.get('pedidos')
        if isinstance(pedidos, list):
            return pedidos
    return []


def traer_pedidos_pendientes(rut_empresa: Optional[str] = None,
                             desde: Optional[str] = None,
                             hasta: Optional[str] = None) -> dict:
    """
    Consulta AllConnected e ingesta cada pedido vía ``_ingestar_pedido_dict``.

    ``desde`` / ``hasta`` (YYYY-MM-DD, opcionales) acotan el rango de fechas que
    AllConnected devuelve. Si se omiten, AllConnected usa el mes actual.

    Devuelve un dict listo para ``JsonResponse``:
        {ok, configurado, total, nuevos, ya_existian, errores: [...], desde, hasta, detalle}
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
    params = {}
    if rut_empresa:
        params['rut_empresa'] = rut_empresa
    if desde:
        params['desde'] = desde
    if hasta:
        params['hasta'] = hasta

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

    # === Sincronización de ESTADOS de los pendientes locales ===
    # El pull de arriba solo INGRESA pedidos nuevos; los ya existentes quedan
    # como estaban. Si un pedido se canceló/devolvió en el canal después de
    # ingresado, acá seguía PENDIENTE para siempre (zombie facturable). Este
    # paso pregunta a AC el estado real y marca CANCELADO lo que corresponda.
    # Nunca tumba el pull: si falla, el resultado lo dice y el resto sigue.
    try:
        sync_estados = sincronizar_estados_pedidos(rut_empresa=rut_empresa)
    except Exception as exc:  # pragma: no cover — defensivo
        logger.exception('Sync de estados AllConnected falló')
        sync_estados = {'ok': False, 'error': str(exc)[:200]}

    detalle = f'{nuevos} nuevos, {ya_existian} ya existían, {len(errores)} con error.'
    if sync_estados.get('cancelados'):
        detalle += f" {sync_estados['cancelados']} pedido(s) cancelados en el canal fueron marcados CANCELADO."
    if sync_estados.get('sin_pago'):
        detalle += f" {sync_estados['sin_pago']} sin pago confirmado en el canal (no facturables por ahora)."

    return {
        'ok': True,
        'configurado': True,
        'total': len(pedidos),
        'nuevos': nuevos,
        'ya_existian': ya_existian,
        'errores': errores,
        'sync_estados': sync_estados,
        'desde': (payload.get('desde') if isinstance(payload, dict) else None) or desde,
        'hasta': (payload.get('hasta') if isinstance(payload, dict) else None) or hasta,
        'detalle': detalle,
    }


def _marcar_cancelado_por_canal(pedido, estado_canal):
    """Marca CANCELADO un PENDIENTE local que el canal dio por terminado.

    Misma transición y rastro que el endpoint oficial
    ``api_cancelar_pedido_ecommerce`` (push de AC), para que el historial
    quede indistinguible de una cancelación avisada en vivo.
    """
    from app.models import HistorialPedidoEcommerce

    estado_anterior = pedido.estado
    sub_estado_anterior = pedido.sub_estado
    pedido.estado = 'CANCELADO'
    pedido.sub_estado = 'CANCELADO_CLIENTE'
    pedido.save(update_fields=['estado', 'sub_estado'])
    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo='CANCELADO',
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo='CANCELADO_CLIENTE',
        tipo_evento='CAMBIO_ESTADO',
        motivo=f'Sync de estados: el canal lo reporta {estado_canal} (AllConnected)',
    )


def sincronizar_estados_pedidos(rut_empresa: Optional[str] = None,
                                dias: int = SYNC_ESTADOS_LOOKBACK_DIAS) -> dict:
    """
    Pregunta a AllConnected el estado actual de los PENDIENTES locales y:

      - guarda ``estado_canal`` + ``fecha_sync_estado_canal`` en cada pedido;
      - los que AC reporta CANCELADO/DEVUELTO/REEMBOLSADO pasan a CANCELADO
        local (con historial), saliendo de la cola de facturación;
      - los que AC aún tiene PENDIENTE (sin pago) quedan marcados: la
        facturación los rechaza hasta que una sync posterior los confirme.

    Los FACTURADOS locales no se tocan (cancelado en el canal + boleta emitida
    = nota de crédito, decisión humana — mismo criterio que el endpoint push).

    Devuelve {ok, consultados, cancelados, sin_pago, no_encontrados, lotes_caidos}.
    """
    from datetime import timedelta

    from django.utils import timezone

    from app.models import PedidoEcommerce

    cfg = _config()
    if not cfg['base_url']:
        return {'ok': True, 'configurado': False, 'consultados': 0,
                'cancelados': 0, 'sin_pago': 0, 'no_encontrados': 0, 'lotes_caidos': 0}
    if not _REQUESTS_OK:
        return {'ok': False, 'error': "Falta el paquete 'requests'.", 'consultados': 0,
                'cancelados': 0, 'sin_pago': 0, 'no_encontrados': 0, 'lotes_caidos': 0}

    desde_dt = timezone.now() - timedelta(days=dias)
    qs = PedidoEcommerce.objects.filter(
        estado='PENDIENTE', fecha_recepcion__gte=desde_dt,
    ).only('id', 'numero_pedido_canal', 'canal_origen', 'estado', 'sub_estado',
           'estado_canal', 'numero_ticket_rm', 'rut_empresa')
    pendientes = list(qs)
    if rut_empresa:
        # Comparación normalizada en Python (el rut de sesión puede venir con
        # puntos y el del pedido sin): un mismatch de formato en un .filter()
        # exacto dejaría el sync silenciosamente en cero. Los pedidos sin rut
        # (legacy) se incluyen, igual que en el scope del listado.
        def _norm(r):
            return (r or '').replace('.', '').replace(' ', '').upper().strip()
        objetivo = _norm(rut_empresa)
        pendientes = [p for p in pendientes
                      if not (p.rut_empresa or '').strip() or _norm(p.rut_empresa) == objetivo]
    if not pendientes:
        return {'ok': True, 'configurado': True, 'consultados': 0,
                'cancelados': 0, 'sin_pago': 0, 'no_encontrados': 0, 'lotes_caidos': 0}

    url = f"{cfg['base_url']}{cfg['estados_path']}"
    headers = {
        cfg['header_name']: cfg['api_key'],
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'RetailMind-PedidosPull/1.0',
    }

    # Índice local por (canal, numero) para aplicar las respuestas.
    por_clave = {}
    for p in pendientes:
        por_clave[(p.canal_origen, (p.numero_pedido_canal or '').strip())] = p

    ahora = timezone.now()
    consultados = cancelados = sin_pago = no_encontrados = lotes_caidos = 0

    for i in range(0, len(pendientes), _LOTE_ESTADOS):
        lote = pendientes[i:i + _LOTE_ESTADOS]
        payload = {'pedidos': [
            {'canal_origen': p.canal_origen,
             'numero_pedido_canal': (p.numero_pedido_canal or '').strip()}
            for p in lote
        ]}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SEGUNDOS)
        except requests.RequestException as exc:
            logger.warning('sincronizar_estados_pedidos: lote %s sin respuesta: %s',
                           i // _LOTE_ESTADOS + 1, exc)
            lotes_caidos += 1
            continue
        if r.status_code == 404:
            # AC aún no tiene deployado el endpoint: no es un error del operador.
            return {'ok': True, 'configurado': True, 'consultados': 0,
                    'cancelados': 0, 'sin_pago': 0, 'no_encontrados': 0,
                    'lotes_caidos': 0,
                    'detalle': 'AllConnected aún no expone /app/pedidos/estados/ (deploy pendiente).'}
        if r.status_code != 200:
            logger.warning('sincronizar_estados_pedidos: HTTP %s: %s',
                           r.status_code, (r.text or '')[:150])
            lotes_caidos += 1
            continue
        try:
            data = r.json()
        except ValueError:
            lotes_caidos += 1
            continue

        no_encontrados += len(data.get('no_encontrados') or [])
        for est in (data.get('estados') or []):
            clave = (str(est.get('canal_origen') or '').strip().upper(),
                     str(est.get('numero_pedido_canal') or '').strip())
            pedido = por_clave.get(clave)
            if pedido is None:
                continue
            consultados += 1
            estado_canal = str(est.get('estado') or '')[:20]
            pedido.estado_canal = estado_canal
            pedido.fecha_sync_estado_canal = ahora
            pedido.save(update_fields=['estado_canal', 'fecha_sync_estado_canal'])

            if est.get('cancelado') or estado_canal in ESTADOS_CANAL_CANCELADOS:
                _marcar_cancelado_por_canal(pedido, estado_canal)
                cancelados += 1
            elif estado_canal == 'PENDIENTE':
                sin_pago += 1

    logger.info(
        'Sync estados AllConnected: %s consultados, %s cancelados, %s sin pago, '
        '%s no encontrados, %s lotes caídos',
        consultados, cancelados, sin_pago, no_encontrados, lotes_caidos,
    )
    return {
        'ok': True, 'configurado': True,
        'consultados': consultados,
        'cancelados': cancelados,
        'sin_pago': sin_pago,
        'no_encontrados': no_encontrados,
        'lotes_caidos': lotes_caidos,
    }
