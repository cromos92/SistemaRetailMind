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

# Estados de AC "ya despachado al cliente". Decisión de negocio (04-ago): si el
# paquete ya salió y este módulo no emitió boleta, la venta se documentó POR
# CONCEPTO fuera de acá → el pedido se cierra como FACTURADO/FACTURADO_EXTERNO
# (sale de la cola sin doble documento). En prod había ~90 así, de jun-jul.
ESTADOS_CANAL_DESPACHADOS = ('ENVIADO', 'EN_TRANSITO', 'ENTREGADO', 'COMPLETADO')

# Estados LOGÍSTICOS de AC en los que el pedido ya está preparado por la CENTRAL
# y espera al courier (o a que el cliente lo retire). NO se cierra —aún no
# salió— pero sale de la cola de picking de la tienda: no es trabajo suyo.
ESTADOS_CANAL_LISTO_CENTRAL = ('LISTO_ENVIO', 'LISTO_RETIRO')

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
    # SIN filtro de empresa a propósito: si se acotara al rut de la sesión,
    # los zombies de las otras cadenas no se limpiarían nunca (verificado en
    # prod 04-ago: 42 cancelados de PAOLA seguían PENDIENTES porque el sync
    # corría desde una sesión NICK). Es idempotente y barato (1 request/300).
    # Nunca tumba el pull: si falla, el resultado lo dice y el resto sigue.
    try:
        sync_estados = sincronizar_estados_pedidos()
    except Exception as exc:  # pragma: no cover — defensivo
        logger.exception('Sync de estados AllConnected falló')
        sync_estados = {'ok': False, 'error': str(exc)[:200]}

    # === Devolver a AllConnected los tickets asignados en ESTE pull ===
    # AC solo guardaba `numero_ticket_rm` en el camino PUSH; los pedidos que
    # entran por acá le quedaban con el ticket VACÍO, y eso rompe en silencio su
    # columna "Tienda (RM)" y el comando de cancelaciones (ambos matchean por
    # ticket). Best-effort: si falla, el pull igual fue exitoso.
    try:
        confirmacion = confirmar_tickets_en_allconnected(pedidos)
    except Exception as exc:  # pragma: no cover — defensivo
        logger.exception('Confirmación de tickets a AllConnected falló')
        confirmacion = {'ok': False, 'error': str(exc)[:200], 'actualizados': 0}

    detalle = f'{nuevos} nuevos, {ya_existian} ya existían, {len(errores)} con error.'
    if sync_estados.get('cancelados'):
        detalle += f" {sync_estados['cancelados']} pedido(s) cancelados en el canal fueron marcados CANCELADO."
    if sync_estados.get('cerrados_despachados'):
        detalle += (f" {sync_estados['cerrados_despachados']} ya despachados por el canal "
                    f"se cerraron como facturados por concepto.")
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
        'tickets_confirmados': confirmacion,
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


def _cerrar_facturado_externo(pedido, estado_canal):
    """Cierra un PENDIENTE que el canal reporta ya DESPACHADO al cliente.

    Regla de negocio (04-ago): si el paquete ya salió y este módulo nunca
    emitió boleta, la venta se documentó POR CONCEPTO fuera de acá. Queda
    FACTURADO/FACTURADO_EXTERNO (sin ticket ni DTE propios — el sub-estado
    es justamente el rastro de eso) y con historial.
    """
    from app.models import HistorialPedidoEcommerce

    estado_anterior = pedido.estado
    sub_estado_anterior = pedido.sub_estado
    pedido.estado = 'FACTURADO'
    pedido.sub_estado = 'FACTURADO_EXTERNO'
    pedido.save(update_fields=['estado', 'sub_estado'])
    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo='FACTURADO',
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo='FACTURADO_EXTERNO',
        tipo_evento='CAMBIO_ESTADO',
        motivo=(f'Sync de estados: el canal lo reporta {estado_canal} (ya despachado). '
                f'Se asume facturado por concepto fuera del módulo — sin DTE propio.'),
    )


def _aplicar_retencion_canal(pedido, tipo, motivo):
    """El canal tiene una incidencia ABIERTA que bloquea el ERP → sacar de la cola.

    AllConnected retiene pedidos (sin stock, cacho, datos de envío…) con
    `bloquea_retailmind=True`, pero eso nunca viajaba de vuelta: RM los seguía
    mostrando como trabajo pendiente y facturable (caso real 06-ago: dos pedidos
    con incidencia SIN_STOCK abierta hace días seguían en la cola de PAO4).

    Se reusa el sub-estado SIN_STOCK porque ya bloquea guía y facturación
    (`SUB_ESTADOS_BLOQUEADOS_PICKING`). El tipo real de la incidencia queda en
    `sin_stock_motivo` para no perderlo. Idempotente: si ya está marcado, no
    reescribe ni duplica historial.
    """
    from app.models import HistorialPedidoEcommerce

    detalle = f"[{tipo or 'RETENIDO'}] {motivo}".strip()[:255]
    if pedido.sub_estado == 'SIN_STOCK':
        if pedido.sin_stock_motivo != detalle or not pedido.sin_stock_avisado_ac:
            pedido.sin_stock_motivo = detalle
            pedido.sin_stock_avisado_ac = True   # la incidencia ES de AllConnected
            pedido.save(update_fields=['sin_stock_motivo', 'sin_stock_avisado_ac'])
        return False

    sub_estado_anterior = pedido.sub_estado
    pedido.sub_estado = 'SIN_STOCK'
    pedido.sin_stock_motivo = detalle
    pedido.sin_stock_avisado_ac = True
    pedido.save(update_fields=['sub_estado', 'sin_stock_motivo', 'sin_stock_avisado_ac'])
    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo='SIN_STOCK',
        tipo_evento='ERROR',
        motivo=f'Sync de estados: AllConnected lo tiene retenido — {detalle}',
    )
    return True


def _liberar_retencion_canal(pedido):
    """AllConnected ya cerró/liberó la incidencia → vuelve al flujo de picking.

    Sin esto un pedido retenido quedaría trabado para siempre en RM aunque
    central lo hubiera resuelto. Solo toca los que RM marcó por retención del
    canal (`sin_stock_avisado_ac=True`): si la tienda lo marcó a mano y todavía
    no hay incidencia en AC, se respeta su marca.
    """
    from app.models import HistorialPedidoEcommerce

    if pedido.sub_estado != 'SIN_STOCK' or not pedido.sin_stock_avisado_ac:
        return False
    pedido.sub_estado = 'ASIGNADO'
    pedido.sin_stock_motivo = ''
    pedido.sin_stock_avisado_ac = False
    pedido.save(update_fields=['sub_estado', 'sin_stock_motivo', 'sin_stock_avisado_ac'])
    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        sub_estado_anterior='SIN_STOCK',
        sub_estado_nuevo='ASIGNADO',
        tipo_evento='CAMBIO_ESTADO',
        motivo='Sync de estados: AllConnected liberó la incidencia — vuelve al flujo.',
    )
    return True


def sincronizar_estados_pedidos(dias: int = SYNC_ESTADOS_LOOKBACK_DIAS,
                                solo_retiros: bool = False) -> dict:
    """
    Pregunta a AllConnected el estado actual de TODOS los PENDIENTES locales
    del lookback (sin filtro de empresa: los zombies de todas las cadenas se
    limpian en la misma pasada) y:

      - guarda ``estado_canal`` + ``fecha_sync_estado_canal`` en cada pedido;
      - los que AC reporta CANCELADO/DEVUELTO/REEMBOLSADO pasan a CANCELADO
        local (con historial), saliendo de la cola de facturación;
      - los que AC reporta ENVIADO/EN_TRANSITO/ENTREGADO se cierran como
        FACTURADO_EXTERNO (ya despachados = facturados por concepto fuera del
        módulo; no deben facturarse de nuevo acá);
      - los que AC aún tiene PENDIENTE (sin pago) quedan marcados: la
        facturación los rechaza hasta que una sync posterior los confirme.

    Los FACTURADOS locales no se tocan (cancelado en el canal + boleta emitida
    = nota de crédito, decisión humana — mismo criterio que el endpoint push).
    Excepción acotada (fix 01-sep-2026): los retiros locales
    (``es_retiro_local=True``) FACTURADOS sí se CONSULTAN — el LISTO_RETIRO
    nace en AllConnected DESPUÉS de la boleta (su gate de liberación la
    exige), o sea siempre con el pedido local ya FACTURADO; sin esta rama el
    espejo ``estado_logistica_canal`` quedaba '' para siempre y la pantalla
    del mesón salía estructuralmente vacía. Para ellos se refresca SOLO el
    espejo (estado_canal / estado_logistica_canal / fecha_sync) y JAMÁS se
    ejecutan las ramas de transición (cancelar, cerrar, retener).

    Devuelve {ok, consultados, cancelados, cerrados_despachados, sin_pago,
    no_encontrados, lotes_caidos}.
    """
    from datetime import timedelta

    from django.utils import timezone

    from app.models import PedidoEcommerce

    cfg = _config()
    if not cfg['base_url']:
        return {'ok': True, 'configurado': False, 'consultados': 0,
                'cancelados': 0, 'cerrados_despachados': 0, 'sin_pago': 0, 'listos_central': 0,
                'retenidos_canal': 0, 'liberados_canal': 0,
                'no_encontrados': 0, 'lotes_caidos': 0}
    if not _REQUESTS_OK:
        return {'ok': False, 'error': "Falta el paquete 'requests'.", 'consultados': 0,
                'cancelados': 0, 'cerrados_despachados': 0, 'sin_pago': 0, 'listos_central': 0,
                'retenidos_canal': 0, 'liberados_canal': 0,
                'no_encontrados': 0, 'lotes_caidos': 0}

    from django.db.models import Q

    desde_dt = timezone.now() - timedelta(days=dias)
    # PENDIENTES (universo histórico del sync) + retiros locales FACTURADOS
    # aún no entregados: son los que reciben LISTO_RETIRO desde AC y de los
    # que vive la pantalla del mesón (ver docstring, "Excepción acotada").
    qs = PedidoEcommerce.objects.filter(
        Q(estado='PENDIENTE')
        | (Q(estado='FACTURADO', es_retiro_local=True)
           & ~Q(estado_logistica_canal='ENTREGADO')),
        fecha_recepcion__gte=desde_dt,
    ).only('id', 'numero_pedido_canal', 'canal_origen', 'estado', 'sub_estado',
           'estado_canal', 'numero_ticket_rm', 'es_retiro_local',
           'estado_logistica_canal')
    if solo_retiros:
        # Modo liviano para el mesón: refresca SOLO los pedidos de retiro
        # (la pantalla se abre con cliente al frente; no arrastra el universo
        # completo de PENDIENTES).
        qs = qs.filter(es_retiro_local=True)
    pendientes = list(qs)
    if not pendientes:
        return {'ok': True, 'configurado': True, 'consultados': 0,
                'cancelados': 0, 'cerrados_despachados': 0, 'sin_pago': 0, 'listos_central': 0,
                'retenidos_canal': 0, 'liberados_canal': 0,
                'no_encontrados': 0, 'lotes_caidos': 0}

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
    consultados = cancelados = cerrados_despachados = 0
    sin_pago = no_encontrados = lotes_caidos = listos_central = 0
    retenidos_canal = liberados_canal = 0

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
                    'cancelados': 0, 'cerrados_despachados': 0, 'sin_pago': 0, 'listos_central': 0,
                'retenidos_canal': 0, 'liberados_canal': 0,
                    'no_encontrados': 0, 'lotes_caidos': 0,
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
            estado_logistica = str(est.get('estado_logistica') or '')[:20]
            pedido.estado_canal = estado_canal
            pedido.estado_logistica_canal = estado_logistica
            pedido.fecha_sync_estado_canal = ahora
            pedido.save(update_fields=['estado_canal', 'estado_logistica_canal',
                                       'fecha_sync_estado_canal'])

            # Retiros locales ya FACTURADOS: SOLO el espejo de arriba. Las
            # ramas de abajo son transiciones de PENDIENTES (cancelar, cerrar
            # por despacho, retener): sobre un FACTURADO pisarían estado y
            # sub_estado violando la regla "los FACTURADOS locales no se
            # tocan" (nota de crédito = decisión humana).
            if pedido.estado != 'PENDIENTE':
                continue

            # `despachado` lo calcula AllConnected mirando SUS dos campos de
            # estado; el fallback local cubre respuestas de versiones viejas
            # del endpoint (que solo mandaban `estado`).
            despachado = bool(est.get('despachado')) or (
                estado_canal in ESTADOS_CANAL_DESPACHADOS
                or estado_logistica in ESTADOS_CANAL_DESPACHADOS
            )
            if est.get('cancelado') or estado_canal in ESTADOS_CANAL_CANCELADOS:
                _marcar_cancelado_por_canal(pedido, estado_canal)
                cancelados += 1
            elif despachado:
                _cerrar_facturado_externo(pedido, estado_logistica or estado_canal)
                cerrados_despachados += 1
            elif est.get('retenido'):
                # AllConnected lo tiene retenido (incidencia que bloquea el ERP):
                # sale del picking y de la facturación hasta que allá se libere.
                if _aplicar_retencion_canal(pedido, est.get('retencion_tipo'),
                                            est.get('retencion_motivo') or 'retenido en el canal'):
                    retenidos_canal += 1
            elif _liberar_retencion_canal(pedido):
                # Estaba retenido por el canal y allá ya lo liberaron.
                liberados_canal += 1
            elif est.get('listo_envio') or estado_logistica in ESTADOS_CANAL_LISTO_CENTRAL:
                # Lo preparó la CENTRAL y espera al courier (o al cliente): no
                # se cierra —todavía no sale— pero deja de ser trabajo de tienda.
                listos_central += 1
            elif estado_canal == 'PENDIENTE':
                sin_pago += 1

    logger.info(
        'Sync estados AllConnected: %s consultados, %s cancelados, %s cerrados '
        'por despacho, %s retenidos por el canal, %s liberados, %s listos en central, '
        '%s sin pago, %s no encontrados, %s lotes caídos',
        consultados, cancelados, cerrados_despachados, retenidos_canal, liberados_canal,
        listos_central, sin_pago, no_encontrados, lotes_caidos,
    )
    return {
        'ok': True, 'configurado': True,
        'consultados': consultados,
        'cancelados': cancelados,
        'cerrados_despachados': cerrados_despachados,
        'listos_central': listos_central,
        'retenidos_canal': retenidos_canal,
        'liberados_canal': liberados_canal,
        'sin_pago': sin_pago,
        'no_encontrados': no_encontrados,
        'lotes_caidos': lotes_caidos,
    }


# ---------------------------------------------------------------------------
# Confirmación de tickets asignados (RM → AllConnected)
# ---------------------------------------------------------------------------

_LOTE_TICKETS = 300


def confirmar_tickets_en_allconnected(pedidos_payload=None, pedidos_qs=None) -> dict:
    """
    Le informa a AllConnected el ``numero_ticket_rm`` que RM asignó a cada pedido.

    Necesario porque AC solo guarda el ticket cuando ÉL hace el push; los
    pedidos que RM trae por pull le quedaban con el ticket vacío, y eso rompe su
    columna "Tienda (RM)" (la task filtra los que no tienen ticket) y el comando
    `sincronizar_cancelaciones_rm`.

    Args:
        pedidos_payload: lista de dicts tal como vinieron del pull (se usan sus
            `canal_origen` + `numero_pedido_canal` para resolver el ticket local).
        pedidos_qs: alternativa — queryset/iterable de PedidoEcommerce ya
            resueltos (lo usa el comando de backfill).

    NUNCA lanza. Devuelve {ok, configurado, enviados, actualizados, ya_tenian,
    conflictos, no_encontrados, detalle}.
    """
    from app.models import PedidoEcommerce

    cfg = _config()
    path = getattr(settings, 'ALLCONNECTED_TICKETS_PATH', '') or '/app/pedidos/confirmar-tickets/'
    vacio = {'ok': True, 'configurado': bool(cfg['base_url']), 'enviados': 0,
             'actualizados': 0, 'ya_tenian': 0, 'conflictos': 0, 'no_encontrados': 0,
             'detalle': ''}

    if not cfg['base_url'] or not _REQUESTS_OK:
        vacio['ok'] = bool(cfg['base_url'])
        vacio['detalle'] = 'AllConnected no configurado en este entorno.'
        return vacio

    if pedidos_qs is not None:
        pedidos = list(pedidos_qs)
    else:
        claves = []
        for item in (pedidos_payload or []):
            if not isinstance(item, dict):
                continue
            numero = str(item.get('numero_pedido_canal') or '').strip()
            if numero:
                claves.append(numero)
        if not claves:
            return vacio
        pedidos = list(
            PedidoEcommerce.objects
            .filter(numero_pedido_canal__in=claves)
            .only('numero_pedido_canal', 'canal_origen', 'numero_ticket_rm')
        )

    tickets = [
        {'canal_origen': p.canal_origen,
         'numero_pedido_canal': (p.numero_pedido_canal or '').strip(),
         'numero_ticket_rm': p.numero_ticket_rm}
        for p in pedidos
        if (p.numero_pedido_canal or '').strip() and p.numero_ticket_rm
    ]
    if not tickets:
        return vacio

    url = f"{cfg['base_url']}{path}"
    headers = {
        cfg['header_name']: cfg['api_key'],
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'RetailMind-PedidosPull/1.0',
    }

    enviados = actualizados = ya_tenian = conflictos = no_encontrados = 0
    fallos = 0
    for i in range(0, len(tickets), _LOTE_TICKETS):
        lote = tickets[i:i + _LOTE_TICKETS]
        try:
            r = requests.post(url, json={'tickets': lote}, headers=headers,
                              timeout=TIMEOUT_AVISO_SEGUNDOS)
        except requests.RequestException as exc:
            logger.warning('confirmar_tickets_en_allconnected: lote sin respuesta: %s', exc)
            fallos += 1
            continue
        if r.status_code == 404:
            return {**vacio, 'ok': False, 'lotes_fallidos': 0, 'deploy_pendiente': True,
                    'detalle': 'AllConnected todavía no tiene el endpoint de tickets (deploy pendiente).'}
        if r.status_code != 200:
            fallos += 1
            continue
        try:
            data = r.json() or {}
        except ValueError:
            fallos += 1
            continue
        enviados += len(lote)
        actualizados += int(data.get('actualizados') or 0)
        ya_tenian += int(data.get('ya_tenian') or 0)
        conflictos += len(data.get('conflictos') or [])
        no_encontrados += len(data.get('no_encontrados') or [])

    if actualizados or conflictos:
        logger.info('Tickets confirmados a AllConnected: %s actualizados, %s ya tenían, '
                    '%s conflictos, %s no encontrados', actualizados, ya_tenian,
                    conflictos, no_encontrados)
    return {
        'ok': fallos == 0,
        'configurado': True,
        'enviados': enviados,
        'actualizados': actualizados,
        'ya_tenian': ya_tenian,
        'conflictos': conflictos,
        'no_encontrados': no_encontrados,
        'lotes_fallidos': fallos,
        'deploy_pendiente': False,
        'detalle': f'{fallos} lote(s) sin respuesta.' if fallos else '',
    }


# ---------------------------------------------------------------------------
# Aviso de quiebre de stock (RM → AllConnected)
# ---------------------------------------------------------------------------

# Timeout corto: el aviso va DENTRO del click de la tienda. Si AC no responde
# rápido, el pedido igual queda marcado en RM y el aviso se reintenta después.
TIMEOUT_AVISO_SEGUNDOS = 12


def reportar_sin_stock(pedido, motivo: str = '', items=None) -> dict:
    """
    Avisa a AllConnected que la tienda NO tiene stock para un pedido.

    AllConnected abre una ``PedidoIncidenciaOperativa`` tipo SIN_STOCK, que ya
    en ese sistema: bloquea el re-envío a RetailMind, la impresión y la
    etiqueta; deja bitácora "Problemas Inventario"; y muestra el pedido como
    RETENIDO/Sin stock en la grilla. Central decide qué hacer (reasignar,
    sustituir o cancelar con el cliente) — RM solo reporta el problema.

    NUNCA lanza: marcar el pedido en RM no puede depender de que AC conteste.
    Devuelve ``{'ok': bool, 'configurado': bool, 'detalle': str,
    'incidencia_id': int|None, 'ya_existia': bool}``.
    """
    cfg = _config()
    path = getattr(settings, 'ALLCONNECTED_SIN_STOCK_PATH', '') or '/app/pedidos/incidencia-sin-stock/'

    if not cfg['base_url']:
        return {'ok': False, 'configurado': False, 'incidencia_id': None, 'ya_existia': False,
                'detalle': 'AllConnected no está configurado en este entorno '
                           '(ALLCONNECTED_API_BASE_URL vacío): el pedido quedó marcado solo en RetailMind.'}
    if not _REQUESTS_OK:
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': "Falta el paquete 'requests' en este entorno."}

    payload = {
        'canal_origen': pedido.canal_origen,
        'numero_pedido_canal': (pedido.numero_pedido_canal or '').strip(),
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'motivo': (motivo or 'Sin stock en tienda').strip()[:500],
        'items': items or [],
        'sucursal': (
            (pedido.sucursal.nombre or pedido.sucursal.alias or '') if pedido.sucursal_id else ''
        ),
    }
    headers = {
        cfg['header_name']: cfg['api_key'],
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'RetailMind-PedidosPull/1.0',
    }
    url = f"{cfg['base_url']}{path}"

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_AVISO_SEGUNDOS)
    except requests.RequestException as exc:
        logger.warning('reportar_sin_stock: %s sin respuesta de AllConnected: %s',
                       pedido.numero_ticket_rm, exc)
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': f'No se pudo avisar a AllConnected: {exc}'[:300]}

    if r.status_code == 404:
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': 'AllConnected no encontró este pedido (¿se creó manualmente en RM?).'}
    if r.status_code == 501:
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': 'AllConnected todavía no tiene el endpoint de incidencias (deploy pendiente).'}
    if r.status_code != 200:
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': f'AllConnected respondió HTTP {r.status_code}.'}

    try:
        data = r.json() or {}
    except ValueError:
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': 'Respuesta de AllConnected ilegible.'}

    if not data.get('ok'):
        return {'ok': False, 'configurado': True, 'incidencia_id': None, 'ya_existia': False,
                'detalle': str(data.get('error') or 'AllConnected rechazó el aviso.')[:300]}

    ya_existia = bool(data.get('ya_existia'))
    return {
        'ok': True, 'configurado': True,
        'incidencia_id': data.get('incidencia_id'),
        'ya_existia': ya_existia,
        'detalle': ('AllConnected ya tenía la incidencia abierta.' if ya_existia
                    else 'AllConnected registró la incidencia de sin stock.'),
    }


# ---------------------------------------------------------------------------
# Retiro en tienda (POS RM → AllConnected)
# ---------------------------------------------------------------------------

# Timeout corto A PROPÓSITO: estas llamadas corren con el CLIENTE parado en el
# mesón. Si AllConnected no contesta en 3s, la pantalla debe decir "usar
# procedimiento manual de contingencia" — jamás un spinner infinito. El código
# de retiro vive SOLO en AllConnected (nunca se ingesta acá), por eso el POS
# valida en vivo contra él.
TIMEOUT_RETIRO_SEGUNDOS = 3

_MSG_RETIRO_NO_DISPONIBLE = (
    'Sistema de validación no disponible. Usar el procedimiento manual de '
    'contingencia (hoja del mesón) y registrar el retiro después.'
)


def _enmascarar_codigo(codigo):
    """Enmascara el código de retiro para logs/UI: '483920' → '****20'.

    El código completo NUNCA debe quedar en un log (es lo único que protege
    el pedido en el mesón).
    """
    c = str(codigo or '')
    if len(c) <= 2:
        return '*' * len(c)
    return '*' * (len(c) - 2) + c[-2:]


def _llamar_retiro(path, payload, codigo_para_log=''):
    """POST JSON a un endpoint de retiro de AllConnected. NUNCA lanza.

    Devuelve el dict de la respuesta remota (contrato: siempre trae ``ok``;
    en error trae ``code`` y ``message``) o un dict de error local con
    ``code='NO_DISPONIBLE'`` / ``'NO_CONFIGURADO'`` cuando el transporte falla.
    Se parsea el body JSON aunque el HTTP status no sea 200: el contrato
    devuelve errores de negocio (CODIGO_INVALIDO/LOCK/YA_RETIRADO) como JSON.
    """
    cfg = _config()
    if not cfg['base_url']:
        return {
            'ok': False, 'configurado': False, 'code': 'NO_CONFIGURADO',
            'message': 'AllConnected no está configurado en este entorno '
                       '(ALLCONNECTED_API_BASE_URL vacío).',
        }
    if not _REQUESTS_OK:
        return {'ok': False, 'configurado': True, 'code': 'NO_DISPONIBLE',
                'message': "Falta el paquete 'requests' en este entorno."}

    url = f"{cfg['base_url']}{path}"
    headers = {
        cfg['header_name']: cfg['api_key'],
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'RetailMind-RetiroPOS/1.0',
    }
    try:
        r = requests.post(url, json=payload, headers=headers,
                          timeout=TIMEOUT_RETIRO_SEGUNDOS)
    except requests.RequestException as exc:
        # Solo el código ENMASCARADO en el log — jamás el completo.
        logger.warning('retiro: %s sin respuesta (codigo %s): %s',
                       path, _enmascarar_codigo(codigo_para_log), exc)
        return {'ok': False, 'configurado': True, 'code': 'NO_DISPONIBLE',
                'message': _MSG_RETIRO_NO_DISPONIBLE}

    if r.status_code == 404:
        # Distinguir "endpoint no deployado" de un 404 de negocio: el contrato
        # de negocio siempre responde JSON con la clave 'ok'.
        try:
            data = r.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and 'ok' in data:
            data.setdefault('configurado', True)
            return data
        return {'ok': False, 'configurado': True, 'code': 'NO_DISPONIBLE',
                'message': 'AllConnected todavía no expone el endpoint de retiro '
                           '(deploy pendiente).'}

    try:
        data = r.json()
    except ValueError:
        logger.warning('retiro: respuesta ilegible de %s (HTTP %s)', path, r.status_code)
        return {'ok': False, 'configurado': True, 'code': 'NO_DISPONIBLE',
                'message': f'AllConnected respondió HTTP {r.status_code} sin JSON.'}

    if not isinstance(data, dict):
        return {'ok': False, 'configurado': True, 'code': 'NO_DISPONIBLE',
                'message': 'Respuesta de AllConnected con formato inesperado.'}
    data.setdefault('configurado', True)
    data.setdefault('ok', False)
    return data


def validar_retiro(numero_ticket_rm='', numero_pedido_canal='', codigo='') -> dict:
    """Pregunta a AllConnected si el código de retiro corresponde al pedido.

    Es un PREVIEW: no consume el código ni registra el retiro. Devuelve
    (contrato AllConnected):
      ok:    {ok: True, pedido: {...}, items: [...], advertencias: [...]}
      error: {ok: False, code: CODIGO_INVALIDO|NO_ENCONTRADO|LOCK|NO_DISPONIBLE,
              message: '...'}
    """
    path = (getattr(settings, 'ALLCONNECTED_RETIRO_VALIDAR_PATH', '')
            or '/system/api/retiro/validar/')
    payload = {'codigo': str(codigo or '').strip()}
    if numero_ticket_rm:
        payload['numero_ticket_rm'] = str(numero_ticket_rm).strip()
    if numero_pedido_canal:
        payload['numero_pedido_canal'] = str(numero_pedido_canal).strip()
    return _llamar_retiro(path, payload, codigo_para_log=codigo)


def confirmar_retiro(numero_ticket_rm='', numero_pedido_canal='', codigo='',
                     retirador_nombre='', retirador_documento='',
                     tipo_documento='RUT', es_titular=True,
                     usuario_pos='', sucursal='') -> dict:
    """Confirma el retiro en AllConnected (atómico e idempotente allá).

    AllConnected crea el ActaRetiro, marca el código USADO, escribe
    fecha_entrega + ENTREGADO y avisa al ecommerce. Devuelve
    {ok: True, acta_id, print_data: {...}} — el print_data va directo al
    formato RETIRO_ECOMMERCE de QZ Tray. Un 2º intento devuelve
    {ok: False, code: 'YA_RETIRADO', message: 'ya retirado el ... por ...'}.
    """
    path = (getattr(settings, 'ALLCONNECTED_RETIRO_CONFIRMAR_PATH', '')
            or '/system/api/retiro/confirmar/')
    payload = {
        'codigo': str(codigo or '').strip(),
        'retirador_nombre': str(retirador_nombre or '').strip()[:255],
        'retirador_documento': str(retirador_documento or '').strip()[:20],
        'tipo_documento': str(tipo_documento or 'RUT').strip().upper(),
        'es_titular': bool(es_titular),
        'origen': 'POS_RM',
        'usuario_pos': str(usuario_pos or '').strip()[:150],
        'sucursal': str(sucursal or '').strip()[:100],
    }
    if numero_ticket_rm:
        payload['numero_ticket_rm'] = str(numero_ticket_rm).strip()
    if numero_pedido_canal:
        payload['numero_pedido_canal'] = str(numero_pedido_canal).strip()
    resultado = _llamar_retiro(path, payload, codigo_para_log=codigo)
    if resultado.get('ok'):
        logger.info('Retiro confirmado en AllConnected: pedido %s, acta %s, '
                    'retirador %s (codigo %s)',
                    numero_ticket_rm or numero_pedido_canal,
                    resultado.get('acta_id'), payload['retirador_nombre'],
                    _enmascarar_codigo(codigo))
    return resultado
