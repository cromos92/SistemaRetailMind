"""Servicio Mercado Pago presencial (QR dinámico / Point) vía Orders API.

Arquitectura server-side: el navegador NUNCA ve el access token ni habla con
MP. El POS pide a Django crear la orden, muestra el QR y hace polling a
Django; Django resuelve el estado por webhook firmado (camino rápido) o
consultando la API (fallback en el mismo polling).

Credenciales: los modelos guardan el NOMBRE de la variable de entorno
(token_env / webhook_secret_env), nunca el valor. Cada empresa/RUT tiene su
propia cuenta MP.

Lección heredada de transbank_simple_service: los tickets se buscan SIEMPRE
por (sucursal, correlativo), nunca por PK.
"""
import base64
import datetime as _dt
import hashlib
import hmac
import io
import json
import logging
import os
import time
import uuid

import requests
from contextlib import contextmanager

from django.db import IntegrityError, connection, transaction
from django.db.utils import OperationalError
from django.utils import timezone

from app.models import (
    ESTADOS_FINALES_MP,
    MercadoPagoConfig,
    MercadoPagoCuenta,
    MercadoPagoWebhookEvento,
    TransaccionMercadoPago,
)

logger = logging.getLogger('app')

MP_API_BASE = 'https://api.mercadopago.com'
# (connect, read) — el webhook debe responder en <22s, nunca colgarse esperando
REQUEST_TIMEOUT = (5, 10)
QR_TIMEOUT_SEGUNDOS = int(os.environ.get('MP_QR_TIMEOUT_SEGUNDOS', '120'))
# Tolerancia anti-replay para el ts del x-signature
WEBHOOK_TS_TOLERANCIA_SEG = 300
# Edad mínima antes de que el polling consulte directo a MP (deja actuar al webhook)
POLL_CONSULTA_DIRECTA_SEG = 5

# ==================== COBRO INCIERTO (anti doble cobro por corte de red) ====
#
# Incidente 13-09-2026: con internet intermitente, el POST /v1/orders llegaba a
# MP pero la respuesta no volvía dentro del read timeout. La fila local se
# escribía DESPUÉS del POST, así que no quedaba rastro: el guard no veía nada,
# el cajero reintentaba y, como el external_reference (= X-Idempotency-Key)
# llevaba un uuid4 nuevo, MP creaba una SEGUNDA orden. El cliente pagaba dos
# veces.
#
# El arreglo es invertir el orden y hacer la referencia determinista, de modo
# que un reintento sea un REPLAY de la misma orden en vez de una orden nueva.
# La fila pre-creada pasa por dos sub-fases, ambas dentro del estado 'CREADA'
# que ya existe (sin migración), guardadas en raw_response['_rm']:
#
#   RESERVADA → la fila existe pero el POST TODAVÍA NO SALIÓ. Por construcción
#               no puede existir en MP: se cierra sin gastar red y NUNCA
#               bloquea una venta.
#   ENVIADA   → el POST salió y no volvió una respuesta usable. PUEDE existir
#               en MP y estar cobrando en la pantalla del terminal: bloquea un
#               cobro MP nuevo del mismo ticket hasta resolverse o caducar.
#
# Regla de oro: el commit de ENVIADA va ANTES del socket. Si el worker muere en
# el medio queda una falsa ENVIADA (cuesta una consulta resolverla), nunca un
# POST enviado bajo una fila RESERVADA (que se cerraría a ciegas y habilitaría
# el segundo cobro).

# Una RESERVADA más vieja que esto es un request que murió antes de postear.
MP_RESERVA_MAX_SEG = int(os.environ.get('MP_RESERVA_MAX_SEG', '30'))
# Desde cuándo una incierta deja de bloquear el CIERRE de la venta (no el cobro).
MP_INCIERTA_BLOQUEO_CIERRE_SEG = int(os.environ.get('MP_INCIERTA_BLOQUEO_CIERRE_SEG', '180'))
# Edad a la que una incierta de canal POINT se cierra localmente.
MP_INCIERTA_TTL_SEG = int(os.environ.get('MP_INCIERTA_TTL_SEG', '900'))
# Piso entre dos intentos de resolver la MISMA fila (evita martillar a MP).
MP_RESOLVER_MIN_SEG = int(os.environ.get('MP_RESOLVER_MIN_SEG', '8'))
# Ventana en la que un re-POST con la misma X-Idempotency-Key es un REPLAY
# seguro. Pasada esta edad MP puede tratar la clave como nueva y CREAR otra
# orden, así que la recuperación se limita a consultar (payments/search).
MP_REPLAY_MAX_EDAD_SEG = int(os.environ.get('MP_REPLAY_MAX_EDAD_SEG', '180'))
# Circuit breaker: tras N fallos de red seguidos, no se sale a la red por M seg.
MP_BREAKER_FALLOS = int(os.environ.get('MP_BREAKER_FALLOS', '2'))
MP_BREAKER_SEG = int(os.environ.get('MP_BREAKER_SEG', '30'))
# Espera máxima por el candado de cobro de un ticket.
MP_LOCK_ESPERA_SEG = int(os.environ.get('MP_LOCK_ESPERA_SEG', '3'))
# Timeouts recortados para las llamadas de RECUPERACIÓN (se encadenan varias).
MP_RECUPERACION_TIMEOUT = (3, 7)
# Presupuesto de red de un request de creación (gunicorn corta a los 60s).
MP_PRESUPUESTO_CREAR_SEG = int(os.environ.get('MP_PRESUPUESTO_CREAR_SEG', '35'))
# Tope de intentos por (sucursal, correlativo). 99 y no más: 'i100' volvería a
# hacer que 'i10' sea prefijo de otra referencia (ver _resolver_transaccion_por_payment).
MP_MAX_INTENTOS_TICKET = 99
# Largo máximo del correlativo admitido (external_reference es unique/80).
MP_CORRELATIVO_MAX_LEN = 45

FASE_RESERVADA = 'RESERVADA'
FASE_ENVIADA = 'ENVIADA'
FASE_RESUELTA = 'RESUELTA'

DET_RESERVADA = 'Preparando el cobro (aún no enviado a Mercado Pago)'
DET_INCIERTA = 'SIN CONFIRMAR: Mercado Pago no respondió al crear el cobro'

# Mapeo estado del recurso *payment* de MP -> estado local
_ESTADO_DESDE_PAYMENT = {
    'approved': 'APROBADA',
    'rejected': 'RECHAZADA',
    'cancelled': 'CANCELADA',
    'refunded': 'DEVUELTA',
    'charged_back': 'CONTRACARGO',
    'in_process': 'PENDIENTE',
    'pending': 'PENDIENTE',
    'authorized': 'PENDIENTE',
    'in_mediation': 'CONTRACARGO',
}

# Mapeo estado de la *orden* (Orders API) -> estado local
_ESTADO_DESDE_ORDEN = {
    'processed': 'APROBADA',
    'refunded': 'DEVUELTA',
    'partially_refunded': 'APROBADA',
    'canceled': 'CANCELADA',
    'cancelled': 'CANCELADA',
    'expired': 'EXPIRADA',
    'failed': 'RECHAZADA',
    'created': 'PENDIENTE',
    'processing': 'PENDIENTE',
    'action_required': 'PENDIENTE',
    'at_terminal': 'PENDIENTE',
}


class MercadoPagoError(Exception):
    """Error de negocio/comunicación con mensaje apto para mostrar al cajero.

    `red=True` marca los fallos de TRANSPORTE (timeout, conexión cortada,
    breaker abierto): son los únicos en los que NO se sabe si MP recibió la
    operación. Todo el diseño anti doble cobro cuelga de esa distinción — un
    4xx de MP significa "no se creó nada"; un timeout significa "no sé".
    """

    def __init__(self, mensaje, detalle=None, red=False):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle
        self.red = red


class CobroEnCursoError(MercadoPagoError):
    """Ya hay un cobro vivo/incierto para ese ticket. Subclase a propósito: los
    tres llamadores hacen `except MercadoPagoError` y siguen funcionando."""

    def __init__(self, mensaje, detalle=None, transaccion=None):
        super().__init__(mensaje, detalle=detalle)
        self.transaccion = transaccion


# ==================== CREDENCIALES / HTTP ====================

def _cuenta_de(config):
    """Cuenta MP (una por empresa/RUT, credenciales cifradas en BD) de una
    config: el FK explícito si está, o la cuenta de la empresa de la sucursal."""
    if config.cuenta_id and config.cuenta.activo:
        return config.cuenta
    return MercadoPagoCuenta.objects.filter(
        empresa_id=config.sucursal.empresa_id, activo=True
    ).first()


def _token(config):
    """Access token de la config. Orden: BD (MercadoPagoCuenta, cifrado en
    reposo) → fallback legacy por env var (token_env)."""
    cuenta = _cuenta_de(config)
    if cuenta:
        token = cuenta.get_access_token()
        if token:
            return token
    nombre = (config.token_env or '').strip()
    token = os.environ.get(nombre) if nombre else None
    if not token:
        raise MercadoPagoError(
            'Mercado Pago no está configurado en el servidor (falta el access token).',
            detalle=('Sin MercadoPagoCuenta activa para la empresa de la sucursal '
                     f'{config.sucursal_id} y sin fallback env '
                     f'({nombre or "token_env vacío"})'),
        )
    return token


# Circuit breaker POR PROCESO (cada worker de gunicorn tiene el suyo). Con MP
# caído, cada llamada cuesta hasta 15s y el guard puede encadenar varias: sin
# esto un corte largo mata workers por el --timeout 60 del Dockerfile y la caja
# no puede ni cobrar en efectivo.
_mp_fallos_red = 0
_mp_breaker_hasta = 0.0


def _breaker_abierto():
    return time.monotonic() < _mp_breaker_hasta


def _breaker_registrar_fallo():
    global _mp_fallos_red, _mp_breaker_hasta
    _mp_fallos_red += 1
    if _mp_fallos_red >= MP_BREAKER_FALLOS:
        _mp_breaker_hasta = time.monotonic() + MP_BREAKER_SEG
        logger.warning(
            "MP: circuit breaker ABIERTO por %ss tras %s fallos de red seguidos",
            MP_BREAKER_SEG, _mp_fallos_red,
        )


def _breaker_registrar_exito():
    global _mp_fallos_red, _mp_breaker_hasta
    _mp_fallos_red = 0
    _mp_breaker_hasta = 0.0


def _request(config, metodo, path, json_body=None, idempotency_key=None, params=None,
             timeout=None):
    if _breaker_abierto():
        # No se envió NADA: es seguro decirlo, no hay incertidumbre que resolver.
        raise MercadoPagoError(
            'Mercado Pago no está respondiendo. No se envió el cobro.',
            detalle='circuit breaker abierto', red=True,
        )
    headers = {
        'Authorization': f'Bearer {_token(config)}',
        'Content-Type': 'application/json',
    }
    if idempotency_key:
        headers['X-Idempotency-Key'] = idempotency_key
    try:
        resp = requests.request(
            metodo, MP_API_BASE + path,
            headers=headers, json=json_body, params=params,
            timeout=timeout or REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error(f"MP: error de red en {metodo} {path}: {e}")
        _breaker_registrar_fallo()
        # OJO: sin la palabra "Reintente". Ese texto es literalmente la
        # instrucción que produjo el doble cobro del 13-09: ante un timeout NO
        # se sabe si MP recibió la orden, así que reintentar a ciegas es lo peor
        # que puede hacer el cajero.
        raise MercadoPagoError(
            'Mercado Pago no respondió a tiempo.', detalle=str(e), red=True,
        )
    # Una respuesta HTTP (aunque sea 4xx/5xx) prueba que hay camino hasta MP.
    _breaker_registrar_exito()
    return resp


# Códigos de error de Mercado Pago que tienen una acción concreta del lado de
# la tienda. Sin esto el cajero ve el código en inglés y no sabe qué hacer;
# con esto el mensaje dice qué apretar. Se buscan como subcadena en el cuerpo
# crudo de la respuesta, que es donde MP los pone (code, message o errors[]).
_PISTAS_ERROR_MP = (
    ('already_queued_order_on_terminal',
     'La máquina ya tiene una operación esperando en su pantalla (un cobro sin terminar o '
     'una impresión anterior) y Mercado Pago admite solo una a la vez: termínala o cancélala '
     'EN LA MÁQUINA (botón rojo o «atrás») y vuelve a intentar.'),
    ('cannot_cancel_order',
     'El cobro ya está en la pantalla de la máquina y Mercado Pago no deja cancelarlo desde '
     'el sistema: hay que cancelarlo EN LA MÁQUINA.'),
    ('invalid terminal_id',
     'Esa máquina no aparece en esta cuenta de Mercado Pago: revisa a qué cuenta y sucursal '
     'está asignada en «Máquinas POS y su sucursal».'),
    ('terminal_not_found',
     'Esa máquina no aparece en esta cuenta de Mercado Pago: revisa a qué cuenta y sucursal '
     'está asignada en «Máquinas POS y su sucursal».'),
)


def _pista_error_mp(data):
    """Qué hacer ante un error conocido de MP, en castellano. '' si no se reconoce."""
    try:
        crudo = json.dumps(data, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        crudo = str(data).lower()
    for codigo, texto in _PISTAS_ERROR_MP:
        if codigo in crudo:
            return texto
    return ''


def _json_o_error(resp, contexto):
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        # MP entrega el motivo en distintas formas según el endpoint: message,
        # error, o una lista errors[]. Juntar todo para que el admin vea el
        # porqué real y no un "HTTP 400" mudo.
        partes = []
        if data.get('message'):
            partes.append(str(data['message']))
        if data.get('error') and str(data.get('error')) not in partes:
            partes.append(str(data['error']))
        for err in (data.get('errors') or [])[:3]:
            if isinstance(err, dict):
                texto = err.get('message') or err.get('code') or ''
                if err.get('code') and err.get('message'):
                    texto = f"{err['code']}: {err['message']}"
                if texto:
                    partes.append(texto)
            elif err:
                partes.append(str(err))
        if data.get('cause'):
            partes.append(str(data['cause'])[:200])
        mensaje_api = ' | '.join(partes) or f'HTTP {resp.status_code}'
        pista = _pista_error_mp(data)
        logger.error(f"MP: {contexto} falló ({resp.status_code}): {json.dumps(data)[:800]}")
        # Un 4xx de validación prueba que MP NO hizo nada. Un 5xx/429/408 NO
        # prueba nada: la operación pudo haberse ejecutado del otro lado y
        # perderse la respuesta — exactamente la misma incertidumbre que un
        # timeout. Marcarlos red=True es lo que evita que un 502 del edge de MP
        # cierre el cobro como "rechazado" y habilite un segundo cobro.
        incierto = resp.status_code >= 500 or resp.status_code in (408, 429)
        raise MercadoPagoError(
            f'Mercado Pago rechazó la operación: {mensaje_api}' + (f' — {pista}' if pista else ''),
            detalle=data, red=incierto)
    return data


# ==================== CONFIG ====================

def obtener_config(sucursal_id, requerir_habilitada=True):
    qs = MercadoPagoConfig.objects.filter(sucursal_id=sucursal_id)
    if requerir_habilitada:
        qs = qs.filter(habilitado=True)
    config = qs.order_by('-es_principal', 'id').first()
    if not config and requerir_habilitada:
        raise MercadoPagoError('Mercado Pago no está habilitado para esta sucursal.')
    return config


def probar_cuenta(cuenta):
    """Prueba el access token de una cuenta contra /users/me. Devuelve datos
    básicos del vendedor; si mp_user_id estaba vacío, lo completa solo."""
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    try:
        resp = requests.get(
            MP_API_BASE + '/users/me',
            headers={'Authorization': f'Bearer {token}'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise MercadoPagoError('No se pudo contactar a Mercado Pago.', detalle=str(e))
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        raise MercadoPagoError(
            f'Token inválido o vencido (HTTP {resp.status_code}).',
            detalle=data,
        )
    # El user_id lo dicta el TOKEN, no lo que se tipeó en el formulario: si
    # difiere se corrige. Con un id ajeno, /users/{id}/stores/search responde
    # 403 (visto en vivo 15-09-2026 con la cuenta de Paola).
    real_id = str(data.get('id') or '')[:30]
    if real_id and cuenta.mp_user_id != real_id:
        if cuenta.mp_user_id:
            logger.warning("MP: mp_user_id de la cuenta de empresa %s corregido %s -> %s (según el token)",
                           cuenta.empresa_id, cuenta.mp_user_id, real_id)
        cuenta.mp_user_id = real_id
        cuenta.save(update_fields=['mp_user_id', 'actualizado_en'])
    return {
        'user_id': data.get('id'),
        'nickname': data.get('nickname'),
        'email': data.get('email'),
        'site': data.get('site_id'),
    }


def listar_cajas(cuenta):
    """Sucursales (stores) y cajas (pos) YA CREADAS en la cuenta MP, para que
    el admin asocie con un clic en vez de tipear external_ids a mano."""
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    if not cuenta.mp_user_id:
        probar_cuenta(cuenta)  # completa mp_user_id desde /users/me
        cuenta.refresh_from_db()
    headers = {'Authorization': f'Bearer {token}'}

    def _get(path, params=None):
        try:
            resp = requests.get(MP_API_BASE + path, headers=headers,
                                params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise MercadoPagoError('No se pudo contactar a Mercado Pago.', detalle=str(e))
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {}

    status_s, data_s = _get(f'/users/{cuenta.mp_user_id}/stores/search', {'limit': 50})
    if status_s in (401, 403, 404):
        # 403 típico: el user_id guardado no es el dueño del token (se tipeó
        # otro a mano). Se corrige contra /users/me y se reintenta UNA vez.
        anterior = cuenta.mp_user_id
        probar_cuenta(cuenta)
        cuenta.refresh_from_db()
        if cuenta.mp_user_id != anterior:
            logger.info("MP: listar_cajas reintenta con el user_id real %s (antes %s)",
                        cuenta.mp_user_id, anterior)
            status_s, data_s = _get(f'/users/{cuenta.mp_user_id}/stores/search', {'limit': 50})
    if status_s != 200:
        pista = ''
        if status_s in (401, 403):
            pista = (' El token no tiene permiso para leer las sucursales/cajas de esta cuenta: '
                     'revisa que sea el access token de PRODUCCIÓN de la aplicación (no el de prueba) '
                     'y que la cuenta sea la dueña de la máquina.')
        raise MercadoPagoError(f'No se pudieron listar las sucursales MP (HTTP {status_s}).{pista}',
                               detalle=data_s)
    stores = {str(s.get('id')): s for s in (data_s.get('results') or [])}

    status_p, data_p = _get('/pos', {'limit': 100})
    if status_p != 200:
        raise MercadoPagoError(f'No se pudieron listar las cajas MP (HTTP {status_p}).',
                               detalle=data_p)

    cajas = []
    for pos in (data_p.get('results') or []):
        store = stores.get(str(pos.get('store_id')), {})
        cajas.append({
            'pos_id': pos.get('id'),
            'store_id': pos.get('store_id'),
            'caja_nombre': pos.get('name') or '',
            'external_pos_id': pos.get('external_id') or '',
            'store_nombre': store.get('name') or '',
            'external_store_id': store.get('external_id') or '',
        })
    return cajas


def asignar_external_ids(cuenta, pos_id, store_id, external_store_id, external_pos_id):
    """Asigna external_id a una caja (y su sucursal) YA CREADAS en MP.

    Las cajas creadas desde el panel web de MP suelen quedar SIN external_id,
    y la Orders API lo exige para emitir el QR. PUT /pos/{id} y
    PUT /users/{uid}/stores/{id} lo aceptan. MP rechaza duplicados.
    """
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def _put(path, body, contexto):
        try:
            resp = requests.put(MP_API_BASE + path, headers=headers,
                                json=body, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise MercadoPagoError('No se pudo contactar a Mercado Pago.', detalle=str(e))
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            mensaje = data.get('message') or f'HTTP {resp.status_code}'
            raise MercadoPagoError(f'MP rechazó {contexto}: {mensaje}', detalle=data)
        return data

    if external_store_id and store_id:
        _put(f'/users/{cuenta.mp_user_id}/stores/{store_id}',
             {'external_id': external_store_id}, 'el ID de la sucursal')
    if external_pos_id and pos_id:
        _put(f'/pos/{pos_id}', {'external_id': external_pos_id}, 'el ID de la caja')
    logger.info(
        "MP: external_ids asignados (store %s -> %s, pos %s -> %s)",
        store_id, external_store_id, pos_id, external_pos_id,
    )
    return True


def listar_devices_point(cuenta):
    """Máquinas Point de la cuenta vía la API NUEVA de terminales
    (GET /terminals/v1/list — la legacy point/integration-api devuelve 403
    'site id is not valid' para Chile; comprobado en vivo). Devuelve id,
    operating_mode (PDV = esclava del sistema / STANDALONE = cobra sola),
    y la caja/sucursal a la que está vinculada."""
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    try:
        resp = requests.get(
            MP_API_BASE + '/terminals/v1/list',
            headers={'Authorization': f'Bearer {token}'},
            params={'limit': 50}, timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise MercadoPagoError('No se pudo contactar a Mercado Pago.', detalle=str(e))
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        raise MercadoPagoError(
            f'No se pudieron listar las máquinas Point (HTTP {resp.status_code}).',
            detalle=data,
        )
    terminales = ((data.get('data') or {}).get('terminals')) or []
    return [{
        'device_id': t.get('id'),
        'operating_mode': t.get('operating_mode'),
        'pos_id': t.get('pos_id'),
        'store_id': t.get('store_id'),
        'external_pos_id': t.get('external_pos_id') or '',
    } for t in terminales]


def cambiar_modo_device(cuenta, device_id, modo):
    """Cambia el operating_mode de una Point vía la API nueva
    (PATCH /terminals/v1/setup — la legacy no soporta Chile). PDV la deja
    esclava del sistema; STANDALONE la libera para cobrar sola."""
    if modo not in ('PDV', 'STANDALONE'):
        raise MercadoPagoError('Modo inválido (PDV o STANDALONE).')
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    try:
        resp = requests.patch(
            MP_API_BASE + '/terminals/v1/setup',
            headers={'Authorization': f'Bearer {token}',
                     'Content-Type': 'application/json'},
            json={'terminals': [{'id': device_id, 'operating_mode': modo}]},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise MercadoPagoError('No se pudo contactar a Mercado Pago.', detalle=str(e))
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        mensaje = data.get('message') or f'HTTP {resp.status_code}'
        raise MercadoPagoError(f'MP rechazó el cambio de modo: {mensaje}', detalle=data)
    logger.info(f"MP: device {device_id} -> modo {modo}")
    resultado = ((data.get('data') or {}).get('terminals')) or []
    if resultado and resultado[0].get('operating_mode'):
        return resultado[0]['operating_mode']
    return modo


def imprimir_en_terminal(config, contenido, external_reference):
    """Imprime contenido en la impresora de la Point (API de Impresiones,
    POST /terminals/v1/actions). Formato del contenido DESCUBIERTO EN VIVO:
    etiquetas {center} {w} (destacado) {s} (normal) {br} (salto); content en
    la raíz y subtype dentro de config.point. external_reference ≤64 chars."""
    if not config.device_id:
        raise MercadoPagoError('La caja no tiene máquina Point asociada.')
    resp = _request(config, 'POST', '/terminals/v1/actions', json_body={
        'type': 'print',
        'external_reference': external_reference[:64],
        'content': contenido,
        'config': {'point': {'terminal_id': config.device_id, 'subtype': 'custom'}},
    }, idempotency_key=external_reference[:64])
    data = _json_o_error(resp, f'imprimir en terminal {config.device_id}')
    logger.info(f"MP: impresión {data.get('id')} enviada a {config.device_id}")
    return data


# Medio de pago que reporta MP (payment_type_id) → etiqueta para cierres
_MEDIO_MP_LABEL = {
    'debit_card': 'DEBITO',
    'credit_card': 'CREDITO',
    'prepaid_card': 'PREPAGO',
    'account_money': 'DINERO EN CUENTA',
    'bank_transfer': 'TRANSFERENCIA',
    'ticket': 'EFECTIVO (PAGO FACIL)',
}


def etiqueta_medio_mp(medio):
    m = (medio or '').strip().lower()
    return _MEDIO_MP_LABEL.get(m, m.upper() or 'SIN DATO')


# ==================== CONTROL CONTRA LA API DE MERCADO PAGO ====================
#
# El cierre por caja se armaba SOLO con lo que el ERP registró. Si Mercado Pago
# cobró $100.000 y el sistema tiene $90.000 (un cobro que no se alcanzó a
# registrar, o uno hecho desde la app de MP), el papel salía cuadrado igual.
# Acá se le pregunta a MP qué cobró de verdad ese día y se compara medio por
# medio (débito / crédito / prepago / dinero en cuenta).

# Orden de presentación de los medios en el cierre
MEDIOS_MP_ORDEN = ('debit_card', 'credit_card', 'prepaid_card',
                   'account_money', 'bank_transfer', 'ticket')


def _rango_iso_dia(fecha):
    """(desde, hasta) en ISO-8601 con el offset REAL de la zona del proyecto.

    Hardcodear `-04:00` (como hacía la conciliación) se rompe en el horario de
    verano chileno: media hora de cobros del día quedaría fuera del rango.
    """
    if isinstance(fecha, str):
        fecha = _dt.datetime.strptime(fecha, '%Y-%m-%d').date()
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(_dt.datetime.combine(fecha, _dt.time.min), tz)
    offset = inicio.strftime('%z')            # '-0400'
    offset = f'{offset[:3]}:{offset[3:]}'     # '-04:00'
    return (f"{fecha:%Y-%m-%d}T00:00:00.000{offset}",
            f"{fecha:%Y-%m-%d}T23:59:59.999{offset}")


def buscar_pagos_dia(config, fecha, max_paginas=20):
    """Todos los pagos de la CUENTA MP en ese día (paginado, 100 por página).

    Es por cuenta, no por caja: MP no permite filtrar por terminal. La
    atribución a la caja se hace después, con el external_reference.
    """
    desde, hasta = _rango_iso_dia(fecha)
    pagos, offset = [], 0
    for _ in range(max_paginas):
        resp = _request(config, 'GET', '/v1/payments/search', params={
            'range': 'date_created',
            'begin_date': desde,
            'end_date': hasta,
            'limit': 100,
            'offset': offset,
            'sort': 'date_created',
            'criteria': 'asc',
        })
        data = _json_o_error(resp, 'payments/search')
        lote = data.get('results') or []
        pagos.extend(lote)
        total = int((data.get('paging') or {}).get('total') or 0)
        offset += len(lote)
        if not lote or offset >= total:
            break
    return pagos


def _sucursal_de_referencia(external_reference):
    """El external_reference propio es `RM-{sucursal_id}-{correlativo}-{uuid}`.

    Permite atribuir un cobro a su sucursal aunque la fila local se haya
    perdido — que es justo el caso que se quiere detectar.
    """
    partes = str(external_reference or '').split('-')
    if len(partes) >= 3 and partes[0] == 'RM' and partes[1].isdigit():
        return int(partes[1])
    return None


def _pago_es_de_la_caja(pago, config, ids_propios=None, ids_ajenos=None):
    """True / False / None (desconocido) según los ids de caja que traiga MP.

    `ids_propios` / `ids_ajenos` son los (pos_id, store_id) APRENDIDOS en la
    misma corrida desde los pagos que sí calzaron por external_reference: MP
    devuelve esos ids en cada pago, pero `MercadoPagoConfig.pos_id/store_id`
    casi nunca están cargados (el formulario de gestión no los pide). Sin este
    aprendizaje, un cobro sin referencia de la OTRA tienda de la misma cuenta
    aparecería como diferencia de esta caja.

    Solo decide cuando hay dato de ambos lados; si no, devuelve None y el
    llamador se queda con la duda (que se reporta, no se esconde).
    """
    for campo in ('pos_id', 'store_id'):
        ajeno = pago.get(campo)
        if ajeno in (None, ''):
            continue
        ajeno = str(ajeno)
        if ids_propios and ajeno in ids_propios:
            return True
        if ids_ajenos and ajeno in ids_ajenos:
            return False
        propio = (getattr(config, campo, '') or '').strip()
        if propio:
            return ajeno == propio
    return None


def _imputacion_en_ventas(config, fecha, transacciones):
    """¿Cada cobro de MP quedó registrado como Mercado Pago EN LA VENTA?

    Es la pregunta que el cruce MP-vs-MP no puede responder: un cobro pasado
    por la máquina y anotado a mano como Transbank calza perfecto contra la
    API (la plata existe en los dos lados) pero la cuadratura lo imputa a
    VISA-MC-AMEX. Fue exactamente lo que pasó en NICK1 y NICK2 el 05-09-2026.

    Devuelve (lista_de_desviados, total_desviado).
    """
    from app.models import Ticket

    # Los cobros directos de la pestaña de gestión no tienen venta a propósito.
    candidatas = [t for t in transacciones
                  if not str(t.correlativo_ticket or '').startswith('DIRECTO-')]
    correlativos = [t.correlativo_ticket for t in candidatas
                    if str(t.correlativo_ticket or '').isdigit()]
    tickets = {
        str(tk.correlativo): tk
        for tk in Ticket.objects.filter(
            sucursal_id=config.sucursal_id, correlativo__in=correlativos,
        ).prefetch_related('pagos')
    }

    desviados = []
    for t in candidatas:
        detalle = t.detalle_pago
        if detalle is not None and str(detalle.metodo_pago or '').startswith('MP_'):
            continue                       # imputado correctamente
        ticket = tickets.get(str(t.correlativo_ticket))
        if ticket is None:
            metodo_venta, estado_venta = 'venta no encontrada', ''
        else:
            estado_venta = ticket.estado
            # El pago del mismo monto es el que se anotó en vez del de MP
            iguales = [p for p in ticket.pagos.all() if int(p.monto or 0) == t.monto]
            otros = iguales or list(ticket.pagos.all())
            metodo_venta = ', '.join(
                f'{p.metodo_pago} ${p.monto:,}'.replace(',', '.') for p in otros
            ) or 'sin pagos'
        desviados.append({
            'monto': t.monto,
            'correlativo_ticket': t.correlativo_ticket,
            'medio_mp': etiqueta_medio_mp(t.metodo_pago_mp),
            'metodo_venta': metodo_venta,
            'estado_venta': estado_venta,
            'hora': timezone.localtime(t.creado_en).strftime('%H:%M'),
            'payment_id': t.payment_id,
        })
    return desviados, sum(d['monto'] for d in desviados)


def conciliar_cierre_mp(config, fecha, pagos=None):
    """Compara lo que el sistema registró para esta caja con lo que MP cobró.

    `pagos` permite reutilizar una búsqueda ya hecha (varias cajas comparten
    cuenta: sin esto se consultaría la misma lista una vez por caja).

    Devuelve un dict con `ok`, la tabla `medios` (sistema / mp / diferencia por
    payment_type_id), los totales, y el detalle de lo que no calza:
      - `sin_registro`:  cobrado en MP, sin pago registrado en el sistema;
      - `sin_confirmar`: registrado como aprobado, MP no lo reporta;
      - `medio_distinto`: mismo cobro, distinto medio (débito/crédito) — hace
        que la cuadratura lo impute al sub-bucket equivocado.
    """
    if pagos is None:
        try:
            pagos = buscar_pagos_dia(config, fecha)
        except MercadoPagoError as e:
            return {'ok': False, 'error': e.mensaje}

    # ── Lado SISTEMA: lo mismo que ya imprime el cierre de esta caja ────────
    locales_caja = list(
        TransaccionMercadoPago.objects
        .filter(config=config, creado_en__date=fecha, tipo='VENTA', estado='APROBADA')
        .exclude(correlativo_ticket__startswith='PRUEBA-')
        .select_related('detalle_pago')
    )
    sistema = {}
    for t in locales_caja:
        b = sistema.setdefault(t.metodo_pago_mp or '', {'monto': 0, 'cobros': 0})
        b['monto'] += t.monto
        b['cobros'] += 1

    # Las filas locales se buscan por external_reference (no por fecha): un
    # cobro de las 23:59 puede tener la fila creada el día anterior.
    refs = [str(p.get('external_reference') or '') for p in pagos]
    locales = {
        t.external_reference: t
        for t in TransaccionMercadoPago.objects.filter(
            external_reference__in=[r for r in refs if r]).select_related('config')
        # Las filas CREADA (reserva / cobro sin confirmar) NO cuentan como
        # registro local: si un pago real de MP calzara con una de ellas
        # desaparecería de `sin_registro` y el cierre reportaría una diferencia
        # sin ninguna fila que la explique. Justo al revés de lo que se necesita.
        if t.estado != 'CREADA'
    }

    # PASADA 1: de los pagos que calzan por external_reference se aprenden los
    # ids que MP le pone a ESTA caja y a las otras de la misma cuenta.
    ids_propios, ids_ajenos = set(), set()
    for pago in pagos:
        trx = locales.get(str(pago.get('external_reference') or ''))
        if trx is None:
            continue
        destino = ids_propios if trx.config_id == config.id else ids_ajenos
        for campo in ('pos_id', 'store_id'):
            valor = pago.get(campo)
            if valor not in (None, ''):
                destino.add(str(valor))
    ids_ajenos -= ids_propios

    real = {}
    sin_registro, medio_distinto = [], []
    devoluciones_mp = 0
    vistos = set()

    # PASADA 2: clasificar cada pago
    for pago in pagos:
        estado = str(pago.get('status') or '')
        ext = str(pago.get('external_reference') or '')
        monto = int(round(float(pago.get('transaction_amount') or 0)))
        medio = str(pago.get('payment_type_id') or '')
        if estado == 'refunded':
            # Devuelto entero: el sistema tampoco lo cuenta como cobro, así
            # que queda fuera de la comparación y se informa aparte.
            trx = locales.get(ext)
            if trx is None or trx.config_id == config.id:
                devoluciones_mp += monto
            continue
        if estado != 'approved':
            continue

        trx = locales.get(ext)
        if trx is not None:
            if trx.config_id != config.id:
                continue                      # cobro de otra caja de la cuenta
            vistos.add(ext)
            if (trx.metodo_pago_mp or '') != medio and medio:
                medio_distinto.append({
                    'payment_id': str(pago.get('id') or ''),
                    'external_reference': ext,
                    'monto': monto,
                    'medio_sistema': etiqueta_medio_mp(trx.metodo_pago_mp),
                    'medio_mp': etiqueta_medio_mp(medio),
                })
        else:
            # Sin fila local: ¿es de esta caja?
            de_la_caja = _pago_es_de_la_caja(pago, config, ids_propios, ids_ajenos)
            sucursal_ref = _sucursal_de_referencia(ext)
            if de_la_caja is False:
                continue
            if sucursal_ref is not None and sucursal_ref != config.sucursal_id:
                continue                      # de otra sucursal de la cuenta
            sin_registro.append({
                'payment_id': str(pago.get('id') or ''),
                'external_reference': ext,
                'monto': monto,
                'medio': etiqueta_medio_mp(medio),
                'hora': str(pago.get('date_created') or '')[11:16],
                'descripcion': str(pago.get('description') or '')[:60],
                # None = MP no dio datos para atribuirlo a una caja
                'atribuible': bool(de_la_caja) or sucursal_ref is not None,
            })

        b = real.setdefault(medio, {'monto': 0, 'cobros': 0})
        b['monto'] += monto
        b['cobros'] += 1

    sin_confirmar = [
        {'external_reference': t.external_reference, 'monto': t.monto,
         'payment_id': t.payment_id, 'medio': etiqueta_medio_mp(t.metodo_pago_mp),
         'correlativo_ticket': t.correlativo_ticket}
        for t in locales_caja if t.external_reference not in vistos
    ]

    claves = list(dict.fromkeys(
        list(MEDIOS_MP_ORDEN) + sorted(set(sistema) | set(real))))
    medios = []
    for clave in claves:
        s = sistema.get(clave, {})
        m = real.get(clave, {})
        if not s.get('monto') and not m.get('monto'):
            continue
        medios.append({
            'medio': clave,
            'etiqueta': etiqueta_medio_mp(clave),
            'sistema': s.get('monto', 0),
            'mp': m.get('monto', 0),
            'diferencia': m.get('monto', 0) - s.get('monto', 0),
            'cobros_sistema': s.get('cobros', 0),
            'cobros_mp': m.get('cobros', 0),
        })

    sistema_total = sum(v['monto'] for v in sistema.values())
    mp_total = sum(v['monto'] for v in real.values())

    # ── Tercera pata: ¿la VENTA lo registró como Mercado Pago? ──────────────
    # Sin esto el control es ciego al caso que originó todo: un cobro pasado
    # por la máquina y anotado a mano como tarjeta calza perfecto MP-vs-MP
    # (la plata está en los dos lados) y aun así la cuadratura lo muestra en
    # VISA-MC-AMEX.
    otro_medio, otro_medio_total = _imputacion_en_ventas(config, fecha, locales_caja)

    # Cajas que comparten la MISMA cuenta MP. `cuenta` puede venir NULL (las
    # credenciales se resuelven por la empresa de la sucursal), así que contar
    # por `cuenta_id` daba 1 siempre y el aviso de "puede ser de otra tienda"
    # no salía nunca.
    if config.cuenta_id:
        cajas_cuenta = MercadoPagoConfig.objects.filter(cuenta_id=config.cuenta_id).count()
    else:
        cajas_cuenta = MercadoPagoConfig.objects.filter(
            cuenta__isnull=True,
            sucursal__empresa_id=config.sucursal.empresa_id,
        ).count()

    return {
        'ok': True,
        'fecha': str(fecha),
        'medios': medios,
        'sistema_total': sistema_total,
        'mp_total': mp_total,
        'diferencia': mp_total - sistema_total,
        'cuadra': mp_total == sistema_total,
        'sin_registro': sin_registro,
        'sin_confirmar': sin_confirmar,
        'medio_distinto': medio_distinto,
        'devoluciones_mp': devoluciones_mp,
        # Cobrado por MP pero registrado en la venta con otro medio de pago
        'otro_medio': otro_medio,
        'otro_medio_total': otro_medio_total,
        'ventas_total': sistema_total - otro_medio_total,
        'todo_ok': mp_total == sistema_total and not otro_medio_total,
        # Cobros que MP no permitió atribuir a una caja concreta: si la cuenta
        # tiene más de una, la diferencia puede venir de la otra tienda.
        'hay_sin_atribuir': any(not d['atribuible'] for d in sin_registro),
        'cajas_en_la_cuenta': cajas_cuenta,
    }


# Etiquetas de 8 caracteres para la tabla del ticket térmico (30 columnas)
_ETIQUETA_CORTA_MP = {
    'DINERO EN CUENTA': 'D.CUENTA',
    'TRANSFERENCIA': 'TRANSFER',
    'EFECTIVO (PAGO FACIL)': 'EFECTIVO',
}


def _bloque_control_mp(control, plata, linea):
    """Sección 'CONTROL vs MERCADO PAGO' del ticket térmico (32 columnas).

    `control` es lo que devuelve `conciliar_cierre_mp`. Si la consulta a MP
    falló, se imprime el aviso: un cierre que no pudo verificarse NO puede
    parecer verificado.
    """
    if not control:
        return []
    partes = ['{center}{w}CONTROL vs MERCADO PAGO{br}']
    if not control.get('ok'):
        partes.append(linea('No se pudo consultar MP:'))
        partes.append(linea(f"  {str(control.get('error') or '')[:28]}"))
        partes.append(linea('Cierre SIN verificar.'))
        partes.append(linea('------------------------------'))
        return partes

    def monto_corto(v):
        return f"{int(v or 0):,}".replace(',', '.')

    def fila(etiqueta, sistema, mp_monto, marca=''):
        # 8 + 1 + 9 + 1 + 9 + 2 = 30 columnas, el mismo ancho que las líneas
        # separadoras del ticket. Más que eso, la Point corta o envuelve.
        return linea(f"{_ETIQUETA_CORTA_MP.get(etiqueta, etiqueta)[:8]:<8} "
                     f"{monto_corto(sistema):>9} {monto_corto(mp_monto):>9}{marca}")

    partes.append(linea('MEDIO     SISTEMA        MP'))
    for m in control.get('medios') or []:
        partes.append(fila(m['etiqueta'], m['sistema'], m['mp'],
                           ' *' if m['diferencia'] else ''))
    partes.append(linea('------------------------------'))
    partes.append(fila('TOTAL', control.get('sistema_total'),
                       control.get('mp_total')))

    # Lo que la VENTA imputó a Mercado Pago: puede ser menos que lo cobrado si
    # un cobro de la máquina se anotó a mano con otro medio de pago.
    desviado = int(control.get('otro_medio_total') or 0)
    if desviado:
        partes.append(linea('------------------------------'))
        partes.append(linea(
            f"{'EN VENTAS':<8} {monto_corto(control.get('ventas_total')):>9}"))
        partes.append('{center}{w}OJO: ' + plata(desviado) + '{br}')
        partes.append(linea('cobrado por Mercado Pago pero'))
        partes.append(linea('registrado con OTRO medio:'))
        for d in (control.get('otro_medio') or [])[:6]:
            partes.append(linea(
                f"  {d['hora']} {plata(d['monto'])} {d['medio_mp'][:7]}"))
            partes.append(linea(f"    venta {str(d['correlativo_ticket'])[:8]}: "
                                f"{d['metodo_venta'].split(' $')[0][:14]}"))
        if len(control.get('otro_medio') or []) > 6:
            partes.append(linea(f"  ...y {len(control['otro_medio']) - 6} mas"))

    diferencia = int(control.get('diferencia') or 0)
    if not diferencia and not desviado:
        partes.append('{center}{w}CUADRA CON MERCADO PAGO{br}')
    elif not diferencia:
        pass                      # el desvío ya se detalló arriba
    elif diferencia > 0:
        partes.append('{center}{w}FALTAN ' + plata(diferencia) + '{br}')
        partes.append(linea('Mercado Pago cobro mas de lo'))
        partes.append(linea('que el sistema registro.'))
    else:
        partes.append('{center}{w}SOBRAN ' + plata(-diferencia) + '{br}')
        partes.append(linea('El sistema registro mas de lo'))
        partes.append(linea('que Mercado Pago confirma.'))

    for d in (control.get('sin_registro') or [])[:8]:
        partes.append(linea(
            f"  {d['hora']} {plata(d['monto'])} {d['medio'][:8]}"))
        partes.append(linea(f"    pago {d['payment_id'][:18]}"))
    if len(control.get('sin_registro') or []) > 8:
        partes.append(linea(f"  ...y {len(control['sin_registro']) - 8} mas"))
    for d in (control.get('sin_confirmar') or [])[:5]:
        partes.append(linea(f"  MP no confirma {plata(d['monto'])}"))
        partes.append(linea(f"    ticket {str(d['correlativo_ticket'])[:16]}"))
    for d in (control.get('medio_distinto') or [])[:5]:
        partes.append(linea(f"  {plata(d['monto'])}: sistema dice"))
        partes.append(linea(f"    {d['medio_sistema'][:12]}, MP {d['medio_mp'][:12]}"))
    if control.get('hay_sin_atribuir') and (control.get('cajas_en_la_cuenta') or 1) > 1:
        partes.append(linea('Ojo: la cuenta MP tiene varias'))
        partes.append(linea('cajas; algun cobro sin ID puede'))
        partes.append(linea('ser de otra tienda.'))
    if control.get('devoluciones_mp'):
        partes.append(linea(f"Devuelto en MP: {plata(control['devoluciones_mp'])}"))
    partes.append(linea('------------------------------'))
    return partes


def contenido_cierre_terminal(caja, fecha):
    """Arma el texto etiquetado del cierre de una caja para la impresora de la
    Point. `caja` es un dict del resumen por terminal (QR/POINT/total_*)."""
    def linea(texto=''):
        return '{s}' + texto + '{br}'

    def plata(v):
        return f"${int(v or 0):,}".replace(',', '.')

    partes = [
        '{center}{w}CIERRE MERCADO PAGO{br}',
        '{center}{s}' + f"{caja.get('sucursal', '')} - {caja.get('caja', '')}" + '{br}',
        '{center}{s}' + f"Fecha: {fecha}" + '{br}',
        linea('------------------------------'),
    ]
    for nombre, canal in (('QR', caja.get('QR') or {}), ('MAQUINA POINT', caja.get('POINT') or {})):
        if not (canal.get('cobros') or canal.get('devoluciones')):
            continue
        partes.append(linea(f"{nombre}:"))
        partes.append(linea(f"  Cobros: {canal.get('cobros', 0)}  {plata(canal.get('monto'))}"))
        if canal.get('devoluciones'):
            partes.append(linea(f"  Devol.: {canal.get('devoluciones', 0)}  -{plata(canal.get('monto_devuelto'))}"))
        if canal.get('comisiones'):
            partes.append(linea(f"  Comisiones MP: {plata(canal.get('comisiones'))}"))
    medios = caja.get('medios') or {}
    if medios:
        partes.append(linea('MP POR MEDIO DE PAGO:'))
        for etiqueta in sorted(medios):
            m = medios[etiqueta]
            partes.append(linea(f"  {etiqueta}: {m.get('cobros', 0)}  {plata(m.get('monto'))}"))
    partes.append(linea('------------------------------'))
    partes.append('{center}{w}' + f"NETO MP: {plata(caja.get('total_neto'))}" + '{br}')
    partes.append(linea('------------------------------'))

    # ── Control contra la API de Mercado Pago ───────────────────────────────
    # Lo que MP dice que cobró de verdad, medio por medio, contra lo que el
    # sistema registró. Sin esto el cierre cuadra siempre consigo mismo.
    partes.extend(_bloque_control_mp(caja.get('control_mp'), plata, linea))

    # Venta del día de la sucursal: todos los medios + total global (misma
    # fuente que la cuadratura del arqueo, para que el papel siempre calce)
    dia = caja.get('dia_sucursal') or []
    if dia:
        partes.append('{center}{w}VENTA DEL DIA - TODOS' + '{br}')
        partes.append('{center}{w}LOS MEDIOS DE PAGO' + '{br}')
        for nombre, monto in dia:
            partes.append(linea(f"  {nombre}: {plata(monto)}"))
        if caja.get('dia_nc'):
            partes.append(linea(f"  NOTAS DE CREDITO: -{plata(caja['dia_nc'])}"))
        partes.append(linea('------------------------------'))
        partes.append('{center}{w}' + f"TOTAL GLOBAL: {plata(caja.get('dia_total_global'))}" + '{br}')
        partes.append(linea('------------------------------'))
    partes.append(linea('MP liquida solo (sin cierre'))
    partes.append(linea('de lote). Resumen NEXO.'))
    if caja.get('responsable'):
        partes.append(linea(f"Responsable: {caja['responsable']}"))
    partes.append(linea(f"Fecha/hora: {timezone.localtime():%d/%m/%Y %H:%M:%S}"))
    return ''.join(partes)


# ==================== QR (imagen) ====================

def qr_png_base64(qr_data):
    """PNG base64 del QR generado server-side (lib ``qrcode``, opcional).

    Si la librería no está instalada devuelve None y el frontend muestra el
    qr_data crudo con instrucciones — el cobro no se cae por esto.
    """
    try:
        import qrcode  # dependencia opcional aprobable: pip install qrcode
    except ImportError:
        logger.warning("MP: librería 'qrcode' no instalada — se enviará qr_data sin imagen")
        return None
    try:
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image()
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception as e:
        logger.error(f"MP: no se pudo renderizar QR: {e}")
        return None


# ==================== CREACIÓN / CONSULTA / CANCELACIÓN ====================

# ==================== FASES, PRESUPUESTO Y CANDADO ====================

def _rm(trx):
    """Metadatos de máquina de la fila (fase, intento, idem key, body enviado).

    Viven en raw_response['_rm'] para no agregar columnas: raw_response ya es
    un JSONField y ya se reescribe en cada transición.
    """
    datos = trx.raw_response if isinstance(trx.raw_response, dict) else {}
    meta = datos.get('_rm')
    return meta if isinstance(meta, dict) else {}


def _marcar_rm(trx, detalle=None, **kv):
    """Merge sobre raw_response['_rm'] + espejo legible en estado_detalle."""
    datos = dict(trx.raw_response) if isinstance(trx.raw_response, dict) else {}
    meta = dict(_rm(trx))
    meta.update(kv)
    datos['_rm'] = meta
    trx.raw_response = datos
    campos = ['raw_response', 'actualizado_en']
    if detalle is not None:
        trx.estado_detalle = detalle[:120]
        campos.append('estado_detalle')
    trx.save(update_fields=campos)
    return trx


def fase_cobro(trx):
    """RESERVADA / ENVIADA / RESUELTA / '' — solo significa algo en 'CREADA'."""
    return _rm(trx).get('fase') or ''


def es_incierta(trx):
    """El POST salió y no volvió respuesta usable: PUEDE existir en MP.

    NO se ata al estado 'CREADA'. Un pago no final que ya movió la fila a
    PENDIENTE (pending / in_process / authorized, sea por payments/search o por
    el webhook) sigue sin order_id y sigue sin poder cancelarse en MP: es el
    caso en que MÁS peligroso sería darla por muerta, porque es justo cuando MP
    ya confirmó que hay un pago en vuelo.
    """
    return (trx.estado not in ESTADOS_FINALES_MP
            and trx.estado != 'APROBADA'
            and fase_cobro(trx) == FASE_ENVIADA
            and not trx.order_id)


def es_reserva(trx):
    """La fila existe pero el POST no salió: por construcción NO existe en MP."""
    return trx.estado == 'CREADA' and fase_cobro(trx) == FASE_RESERVADA and not trx.order_id


def _edad_seg(trx):
    return (timezone.now() - trx.creado_en).total_seconds()


def ttl_incierta(trx):
    """Cuándo se cierra localmente una incierta que nadie pudo resolver.

    En QR la orden lleva expiration_time, así que pasado ese plazo MP ya no la
    puede cobrar. En Point no hay expiración por API (el cobro vive en la
    pantalla del terminal), por eso el plazo es mucho más largo.
    """
    if trx.canal == 'QR':
        return QR_TIMEOUT_SEGUNDOS + 60
    return MP_INCIERTA_TTL_SEG


class _Presupuesto:
    """Techo de segundos de red por request, bien debajo del --timeout 60 de
    gunicorn. Sin esto, tres cobros en vuelo alcanzan para matar el worker en
    medio del cierre de una venta — y un worker muerto es la forma más brutal
    de caja bloqueada (el cajero ve un 502 y no sabe si la venta se grabó)."""

    def __init__(self, segundos):
        self.fin = time.monotonic() + segundos

    def queda(self):
        return max(0.0, self.fin - time.monotonic())

    def hay(self, minimo=6):
        return self.queda() >= minimo


def _clave_lock_cobro(sucursal_id, correlativo):
    """Clave int64 estable para el advisory lock.

    hashlib y JAMÁS hash(): PYTHONHASHSEED es distinto en cada worker de
    gunicorn, así que hash() daría claves distintas para el mismo ticket en
    cada proceso y el candado no serializaría absolutamente nada.
    """
    semilla = f'mp:cobro:{int(sucursal_id)}:{str(correlativo).strip().upper()}'
    digest = hashlib.blake2b(semilla.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big', signed=True)


@contextmanager
def _lock_cobro(sucursal_id, correlativo, espera_seg=None):
    """Candado por (sucursal, correlativo) para leer-decidir-insertar.

    REGLA DURA: adentro NO puede haber ni una llamada HTTP. El tramo lento (el
    POST a MP, hasta 15s) no lo protege este candado sino la propia fila
    pre-creada, que es un mutex persistente: sobrevive al timeout, al F5, a la
    segunda pestaña y al worker que se muera.

    Es `xact` y no de sesión porque settings usa conn_max_age>0: la conexión se
    reusa entre requests y un lock de sesión que no se libere dejaría ese worker
    bloqueando el ticket para siempre. El xact se suelta en COMMIT/ROLLBACK
    pase lo que pase.

    Es advisory y no select_for_update porque en el PRIMER cobro de un ticket
    NO EXISTE fila que bloquear: un FOR UPDATE sobre un queryset vacío no
    bloquea nada y los dos POST simultáneos pasan los dos (el bug de hoy).
    """
    espera = MP_LOCK_ESPERA_SEG if espera_seg is None else espera_seg
    if connection.vendor != 'postgresql':
        # SQLite (tests) / otros motores: degradación limpia, sin candado.
        with transaction.atomic():
            yield True
        return
    clave = _clave_lock_cobro(sucursal_id, correlativo)
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = %s", [f'{int(espera * 1000)}ms'])
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [clave])
            yield True
    except OperationalError as e:
        # 55P03 lock_not_available: otro request está creando este mismo cobro.
        logger.warning("MP: no se obtuvo el candado de cobro (%s/%s): %s",
                       sucursal_id, correlativo, e)
        raise CobroEnCursoError(
            'Ya se está iniciando un cobro para este ticket. Espera unos segundos.')


def _ref_cobro(config, correlativo, intento):
    """external_reference determinista = X-Idempotency-Key.

    Formato: RM-{sucursal_id}-{correlativo}-c{config_id}i{intento:02d}

    - La cabeza 'RM-{sucursal_id}-' NO se toca: _sucursal_de_referencia() es lo
      único que permite atribuir a una caja un cobro que MP reporta y del que no
      hay fila local.
    - c{config_id} separa dos cajas de la MISMA sucursal cobrando el mismo
      correlativo (el unique de external_reference es global).
    - i{intento:02d} con padding: sin él 'i1' sería prefijo literal de 'i10' y
      _resolver_transaccion_por_payment aplicaría el webhook del intento 1 sobre
      la fila del intento 10.
    """
    return f'RM-{config.sucursal_id}-{correlativo}-c{config.id}i{int(intento):02d}'


def _armar_body_orden(config, correlativo, monto, descripcion, canal, external_reference):
    """Body del create-order. Extraído a propósito: la recuperación tiene que
    re-postear el body EXACTAMENTE igual con la MISMA idempotency key. Si se
    armara en dos lugares distintos, algún día divergen y MP responde 400 por
    conflicto de idempotencia justo en el momento en que más falta hace."""
    if canal == 'POINT':
        # PROBADO EN VIVO contra una Point Smart 2 CL: total_amount en la raíz
        # NO va (unsupported_properties) — el monto vive solo en
        # transactions.payments.
        return {
            'type': 'point',
            'external_reference': external_reference,
            'description': (descripcion or f'Venta {correlativo}')[:120],
            'config': {
                'point': {
                    'terminal_id': config.device_id,
                }
            },
            'transactions': {
                'payments': [{'amount': str(monto)}],
            },
        }
    return {
        'type': 'qr',
        'external_reference': external_reference,
        'description': (descripcion or f'Venta {correlativo}')[:120],
        'expiration_time': f'PT{QR_TIMEOUT_SEGUNDOS}S',
        'total_amount': str(monto),
        'config': {
            'qr': {
                'external_pos_id': config.external_pos_id,
                'mode': 'dynamic',
            }
        },
        'transactions': {
            'payments': [{'amount': str(monto)}],
        },
    }


def _props_no_soportadas(error, body):
    """Propiedades de primer nivel que MP rechazó con unsupported_properties."""
    detalle = error.detalle if isinstance(error.detalle, dict) else {}
    props = []
    import re as _re
    for err in (detalle.get('errors') or []):
        if isinstance(err, dict) and err.get('code') == 'unsupported_properties':
            for d in (err.get('details') or []):
                texto = str(d)
                # Dos formatos reales: "campo" a secas, o
                # "additionalProperties '$.campo' not allowed"
                encontrados = _re.findall(r'\$\.(\w+)', texto)
                if encontrados:
                    props.extend(encontrados)
                else:
                    props.append(texto.split('.')[0].strip())
    return [p for p in props
            if p and p in body and p not in ('type', 'transactions', 'config')]


def _resolver_previos_antes_de_cobrar(config, correlativo, presupuesto):
    """Antes de emitir una orden nueva, cerrar o denunciar lo que haya vivo.

    Hace RED (consultas y cancelaciones a MP), así que corre FUERA del candado.
    `cobros_vivos_de_ticket(refrescar=True)` ya resuelve las inciertas, cierra
    las reservas fantasma y caduca lo vencido; lo que sobrevive acá es un cobro
    que de verdad puede terminar en plata.

    Devuelve los ids que ya quedaron atendidos, para que la revisión de adentro
    del candado solo bloquee por filas que aparecieron DESPUÉS (que es su
    verdadero trabajo: atrapar al otro request que está creando en paralelo).
    """
    atendidos = set()
    for previa in cobros_vivos_de_ticket(config.sucursal_id, correlativo,
                                         refrescar=True, presupuesto=presupuesto):
        atendidos.add(previa.id)
        if previa.estado == 'APROBADA':
            raise CobroEnCursoError(
                f'Ya hay un cobro APROBADO de ${previa.monto:,} en Mercado Pago para '
                f'este ticket (pago {previa.payment_id_mp or previa.payment_id or previa.external_reference}). '
                'Regístralo como pago Mercado Pago o devuélvelo; no cobres de nuevo.'
                .replace(',', '.'), transaccion=previa)
        if es_reserva(previa):
            # Reserva fresca: otro request está creando este mismo cobro AHORA.
            raise CobroEnCursoError(
                'Ya se está iniciando un cobro para este ticket. Espera unos segundos.',
                transaccion=previa)
        if es_incierta(previa):
            # No se pudo confirmar con MP si esa orden existe. Cancelarla a
            # ciegas la sacaría de "vivos" y habilitaría una segunda orden:
            # exactamente el doble cobro del 13-09. Se prefiere no cobrar.
            raise CobroEnCursoError(
                f'Hay un cobro SIN CONFIRMAR de ${previa.monto:,} para este ticket: '
                'Mercado Pago no respondió al crearlo y puede estar en la pantalla de '
                'la máquina. NO cobres de nuevo por Mercado Pago — revisa el terminal, '
                'cobra por otro medio o espera unos minutos.'.replace(',', '.'),
                transaccion=previa)
        try:
            cancelar(previa)
        except MercadoPagoError as e:
            raise CobroEnCursoError(
                f'Hay un cobro anterior de ${previa.monto:,} todavía en curso para este '
                f'ticket. {e.mensaje}'.replace(',', '.'), transaccion=previa)
    return atendidos


def _reservar_fila(config, correlativo, monto, canal, usuario, permitir_en_curso,
                   ids_atendidos=None):
    """Inserta la fila RESERVADA bajo candado corto. CERO llamadas HTTP acá.

    La fila reservada es el mutex del tramo lento: mientras este request postea
    a MP (hasta 15s), cualquier otro la ve y se retira. A diferencia de un lock
    en memoria, sobrevive al timeout, al F5, a la segunda pestaña y al worker
    que se muera.
    """
    ultimo_integrity = None
    for _ in range(MP_MAX_INTENTOS_TICKET + 1):
        try:
            with _lock_cobro(config.sucursal_id, correlativo):
                if not permitir_en_curso:
                    # Relectura DENTRO del candado: es la que serializa de
                    # verdad los dos POST simultáneos. Solo miran las filas que
                    # NO atendió la fase anterior — si no, bloquearía por la
                    # misma fila que acaba de cancelar.
                    vivos = [t for t in cobros_vivos_de_ticket(
                                config.sucursal_id, correlativo, refrescar=False)
                             if t.id not in (ids_atendidos or set())]
                    if vivos:
                        raise CobroEnCursoError(
                            'Ya se está iniciando un cobro para este ticket. '
                            'Espera unos segundos.', transaccion=vivos[0])
                intento = 1 + TransaccionMercadoPago.objects.filter(
                    sucursal_id=config.sucursal_id,
                    config_id=config.id,
                    correlativo_ticket=correlativo,
                    tipo='VENTA',
                ).count()
                if intento > MP_MAX_INTENTOS_TICKET:
                    raise MercadoPagoError(
                        f'Demasiados intentos de cobro Mercado Pago para este ticket '
                        f'({MP_MAX_INTENTOS_TICKET}). Revisa Conciliación Mercado Pago '
                        'antes de seguir cobrando.')
                meta = {'fase': FASE_RESERVADA, 'intento': intento}
                if canal == 'POINT':
                    # A qué máquina se manda. La caja puede cambiar de máquina
                    # después (reasignación): sin esto, una orden vieja que
                    # sigue encolada en el terminal no se puede atribuir
                    # (ver ordenes_en_terminal / liberar_terminal).
                    meta['device_id'] = config.device_id
                return TransaccionMercadoPago.objects.create(
                    config=config,
                    sucursal_id=config.sucursal_id,
                    correlativo_ticket=correlativo,
                    tipo='VENTA',
                    canal=canal,
                    external_reference=_ref_cobro(config, correlativo, intento),
                    order_id='',
                    monto=monto,
                    estado='CREADA',
                    estado_detalle=DET_RESERVADA,
                    raw_response={'_rm': meta},
                    usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
                )
        except IntegrityError as e:
            # Colisión del unique de external_reference: otro request ganó la
            # carrera con este mismo número de intento. Se reintenta con el
            # siguiente libre. El atomic del candado ya hizo rollback al salir.
            ultimo_integrity = e
            continue
    raise MercadoPagoError(
        'No se pudo reservar el cobro en Mercado Pago (colisión de referencia).',
        detalle=str(ultimo_integrity))


def _enviar_orden(transaccion, body, sufijo='', _reintento=False):
    """Marca la fila ENVIADA y postea. Devuelve (transaccion, qr_data).

    NO lanza ante un fallo de RED: devuelve la fila incierta para que el POS la
    vigile. Un timeout NO significa "no se creó" — significa "no sé", y la
    única respuesta segura a "no sé" es no volver a cobrar.
    """
    # Breaker abierto = el POST corta ANTES del socket, o sea la orden
    # PROBADAMENTE no existe en MP. Cerrarla acá es seguro (no habilita un
    # segundo cobro porque no hubo primero) y evita trabar el cierre de la venta
    # 180 s por un cobro que nunca salió — y, peor, que el replay posterior la
    # crease de verdad.
    if _breaker_abierto():
        _aplicar_estado(transaccion, 'ERROR',
                        detalle='No se envió a Mercado Pago (servicio sin responder)')
        raise MercadoPagoError(
            'Mercado Pago no está respondiendo. No se envió el cobro: '
            'cobra por otro medio.', detalle='circuit breaker abierto')

    # El commit de ENVIADA va ANTES del socket. La asimetría es deliberada: una
    # falsa ENVIADA cuesta una consulta de más; un POST enviado bajo una fila
    # RESERVADA se cerraría a ciegas y habilitaría el segundo cobro.
    _marcar_rm(transaccion, detalle=DET_INCIERTA, fase=FASE_ENVIADA,
               sufijo=sufijo, body=body,
               enviada_en=timezone.now().isoformat())

    idem = transaccion.external_reference + sufijo
    try:
        resp = _request(transaccion.config, 'POST', '/v1/orders',
                        json_body=body, idempotency_key=idem)
        data = _json_o_error(resp, f'crear orden {transaccion.external_reference}')
    except MercadoPagoError as e:
        if e.red:
            # ── EL CASO DEL 13-09 ──────────────────────────────────────────
            # MP puede haber recibido la orden. La fila queda ENVIADA (incierta)
            # y el caller decide: el POS la vigila, el guard la respeta y
            # resolver_incierta la cierra o la recupera más tarde.
            logger.warning(
                "MP: cobro INCIERTO %s ($%s, %s): %s",
                transaccion.external_reference, transaccion.monto,
                transaccion.canal, e.mensaje,
            )
            return transaccion, None
        # Auto-corrección: MP rechaza propiedades puntuales según sitio/versión.
        props = _props_no_soportadas(e, body) if not _reintento else []
        if props:
            logger.warning(f"MP: reintento de orden sin propiedades no soportadas: {props}")
            body_min = {k: v for k, v in body.items() if k not in props}
            # El sufijo y el body minimizado se PERSISTEN antes de postear: sin
            # esto, la recuperación re-postearía el body original con la key
            # original mientras MP tiene la orden bajo la key '-r' → 2ª orden.
            return _enviar_orden(transaccion, body_min, sufijo='-r', _reintento=True)
        # 4xx determinista: MP rechazó, la orden NUNCA se creó. Cerrar la fila
        # para no dejarla bloqueando el ticket, y relanzar con el detalle crudo.
        _aplicar_estado(transaccion, 'ERROR',
                        detalle=f'Mercado Pago rechazó la orden: {e.mensaje[:80]}')
        raise

    order_id = str(data.get('id') or '')
    qr_data = (data.get('type_response') or {}).get('qr_data') or data.get('qr_data')
    if transaccion.canal == 'POINT':
        qr_data = None  # el cobro está EN la máquina, no hay QR que mostrar

    if order_id:
        transaccion.order_id = order_id
        transaccion.save(update_fields=['order_id', 'actualizado_en'])
    _marcar_rm(transaccion, fase=FASE_RESUELTA)

    if transaccion.canal == 'QR' and not qr_data:
        # La orden EXISTE en MP: la fila se queda (con su order_id) para poder
        # cancelarla. Antes se perdía y quedaba cobrable sin rastro local.
        logger.error(f"MP: orden creada sin qr_data: {json.dumps(data)[:500]}")
        _aplicar_estado(transaccion, 'PENDIENTE', detalle='Orden sin QR', raw=data)
        raise MercadoPagoError('Mercado Pago no devolvió el QR. Revisa el cobro antes '
                               'de reintentar.', detalle=data)

    _aplicar_estado(transaccion, 'PENDIENTE', detalle='Orden creada', raw=data)
    logger.info(f"MP: orden {transaccion.order_id} creada "
                f"({transaccion.external_reference}, ${transaccion.monto})")
    return transaccion, qr_data


def crear_orden(config, correlativo, monto, descripcion='', canal='QR', usuario=None,
                permitir_en_curso=False):
    """Crea el cobro en MP (Orders API) y su TransaccionMercadoPago local.

    Devuelve **(transaccion, qr_data)**, igual que siempre. Dos diferencias
    importantes respecto de la versión anterior:

    1. La fila local se escribe ANTES del POST (estado 'CREADA', fase
       RESERVADA) y el external_reference es DETERMINISTA, así que es a la vez
       la X-Idempotency-Key: un reintento del mismo intento es un replay de la
       misma orden, no una orden nueva.
    2. Ante un fallo de RED al postear NO se lanza excepción: se devuelve
       `(transaccion_incierta, None)`. El caller debe preguntar `es_incierta(trx)`
       antes de tratar el resultado como éxito. Lanzar ahí era lo que empujaba
       al cajero a reintentar a ciegas y cobrar dos veces.

    `permitir_en_curso=True` salta el guard de cobro previo. NOTA: ningún
    llamador lo usa; los cobros PRUEBA-/DIRECTO- de la pestaña de gestión se
    saltan el guard por PREFIJOS_CORRELATIVO_SIN_TICKET, no por este flag.
    """
    # ── FASE 0: validación pura. Sin fila, sin candado, sin red ─────────────
    # Cualquier error de CONFIGURACIÓN tiene que reventar acá: si dejara fila,
    # bloquearía el cierre de la venta con MP_COBRO_EN_CURSO sin que jamás haya
    # existido un cobro.
    monto = int(monto)
    if monto <= 0:
        raise MercadoPagoError('El monto a cobrar debe ser mayor que cero.')
    canal = (canal or 'QR').upper()
    if canal not in ('QR', 'POINT'):
        raise MercadoPagoError('Canal inválido (QR o POINT).')
    if canal == 'QR' and not config.external_pos_id:
        raise MercadoPagoError('La configuración MP de la sucursal no tiene caja (external_pos_id).')
    if canal == 'POINT' and not config.device_id:
        raise MercadoPagoError('La caja no tiene una máquina Point asociada (device). '
                               'Asóciala en la pestaña Mercado Pago (requiere la máquina en modo PDV).')
    correlativo = str(correlativo)
    if len(correlativo) > MP_CORRELATIVO_MAX_LEN:
        raise MercadoPagoError('El identificador del ticket es demasiado largo para Mercado Pago.')
    _token(config)  # token mal configurado → error acá, sin fila fantasma

    presupuesto = _Presupuesto(MP_PRESUPUESTO_CREAR_SEG)

    # ── FASE 1: resolver lo previo (hace red, va FUERA del candado) ─────────
    ids_atendidos = set()
    if not permitir_en_curso:
        ids_atendidos = _resolver_previos_antes_de_cobrar(config, correlativo, presupuesto)

    # ── FASE 2: reservar la fila (candado corto, sin red) ──────────────────
    transaccion = _reservar_fila(config, correlativo, monto, canal, usuario,
                                 permitir_en_curso, ids_atendidos)

    # ── FASE 3: enviar ─────────────────────────────────────────────────────
    body = _armar_body_orden(config, correlativo, monto, descripcion, canal,
                             transaccion.external_reference)
    return _enviar_orden(transaccion, body)


def _extraer_payment_de_orden(data_orden):
    """La orden trae transactions.payments[]; devuelve el primero con datos."""
    pagos = ((data_orden.get('transactions') or {}).get('payments')) or []
    return pagos[0] if pagos else {}


def _aplicar_estado(transaccion, estado_nuevo, detalle='', payment=None,
                    raw=None, via_webhook=False):
    """Transición de estado con protecciones: una APROBADA solo puede pasar a
    DEVUELTA/CONTRACARGO; los estados finales no retroceden a PENDIENTE.

    Embudo ÚNICO de todas las transiciones (webhook, polling, recuperación,
    cancelación, cron), así que el guard anti-downgrade vive acá y tiene que
    leer el estado AUTORITATIVO de la base, no el de la instancia en memoria:
    `resolver_incierta` sostiene su instancia hasta 20 s mientras hace red, y en
    esa ventana el webhook puede escribir APROBADA. Sin releer, el hilo lento
    pisaba esa APROBADA con EXPIRADA/ERROR, la fila salía de los cobros vivos y
    el siguiente intento emitía una segunda orden cobrable. Reproducido.

    El bloqueo de fila es corto y adentro NO hay ninguna llamada HTTP.
    """
    with transaction.atomic():
        if transaccion.pk:
            fila = (TransaccionMercadoPago.objects.select_for_update()
                    .filter(pk=transaccion.pk)
                    .values('estado', 'raw_response').first())
            if fila:
                # La BD manda: si otro worker la movió, esa es la verdad.
                transaccion.estado = fila['estado']
                if isinstance(fila.get('raw_response'), dict):
                    # Y su raw_response también, o el merge de _rm borraría el
                    # payload del pago que acaba de guardar el webhook.
                    transaccion.raw_response = fila['raw_response']
        return _aplicar_estado_locked(transaccion, estado_nuevo, detalle,
                                      payment, raw, via_webhook)


def _aplicar_estado_locked(transaccion, estado_nuevo, detalle='', payment=None,
                           raw=None, via_webhook=False):
    """Cuerpo de _aplicar_estado, ya con la fila bloqueada y releída."""
    if transaccion.estado == 'APROBADA' and estado_nuevo not in ('DEVUELTA', 'CONTRACARGO', 'APROBADA'):
        logger.warning(
            f"MP: se ignoró downgrade {transaccion.estado} -> {estado_nuevo} en {transaccion.external_reference}"
        )
        return transaccion
    if transaccion.estado in ESTADOS_FINALES_MP and estado_nuevo == 'PENDIENTE':
        return transaccion

    campos = ['estado', 'estado_detalle', 'actualizado_en']
    transaccion.estado = estado_nuevo
    transaccion.estado_detalle = (detalle or '')[:120]

    if payment:
        # Mercado Pago identifica el MISMO cobro con dos ids según de dónde
        # venga: la Orders API devuelve un ULID (`PAY01M1S0Y7D2...`) y el
        # webhook de topic=payment / `payments/search` devuelven el número que
        # se ve en el panel y la app (`177422093000`). Antes se escribían los
        # dos sobre `payment_id`, así que el último en llegar borraba al otro.
        _id_pago = str(payment.get('id') or payment.get('payment_id') or '')
        if _id_pago:
            if _id_pago.isdigit():
                transaccion.payment_id_mp = _id_pago[:40]
                if not transaccion.payment_id:
                    transaccion.payment_id = _id_pago[:60]
            else:
                transaccion.payment_id = _id_pago[:60]
        transaccion.metodo_pago_mp = str(payment.get('payment_type_id') or payment.get('payment_method_id')
                                         or (payment.get('payment_method') or {}).get('type')
                                         or (payment.get('payment_method') or {}).get('id')
                                         or transaccion.metodo_pago_mp or '')[:40]
        card = payment.get('card') or {}
        if card.get('last_four_digits'):
            transaccion.ultimos_4_digitos = str(card['last_four_digits'])[:4]
        if payment.get('authorization_code'):
            transaccion.codigo_autorizacion = str(payment['authorization_code'])[:30]
        if payment.get('installments'):
            transaccion.installments = int(payment['installments'])
        det = payment.get('transaction_details') or {}
        if det.get('net_received_amount') is not None:
            # CLP: enteros; MP puede traer decimales — half-up, NUNCA int() directo
            transaccion.monto_neto = int(round(float(det['net_received_amount'])))
            transaccion.fee_mp = transaccion.monto - transaccion.monto_neto
        if payment.get('money_release_date'):
            try:
                from django.utils.dateparse import parse_datetime
                fecha = parse_datetime(payment['money_release_date'])
                if fecha:
                    transaccion.money_release_date = fecha
            except Exception:
                pass
        campos += ['payment_id', 'payment_id_mp', 'metodo_pago_mp',
                   'ultimos_4_digitos', 'codigo_autorizacion', 'installments',
                   'monto_neto', 'fee_mp', 'money_release_date']

    if raw is not None:
        # Preservar los metadatos de máquina (_rm): son la auditoría de cómo se
        # creó y se recuperó el cobro, y la fase que usan los guards.
        meta = _rm(transaccion)
        nuevo_raw = dict(raw) if isinstance(raw, dict) else {'data': raw}
        if meta:
            nuevo_raw['_rm'] = meta
        transaccion.raw_response = nuevo_raw
        campos.append('raw_response')
    if via_webhook:
        transaccion.webhook_recibido_en = timezone.now()
        campos.append('webhook_recibido_en')

    transaccion.save(update_fields=list(set(campos)))
    logger.info(f"MP: {transaccion.external_reference} -> {estado_nuevo} ({detalle or 's/detalle'})")
    return transaccion


def consultar_estado(transaccion, forzar=False):
    """Estado para el polling del POS. Primero BD (webhook ya procesado); si
    sigue PENDIENTE y pasó el margen, consulta la orden directo en MP."""
    if transaccion.estado != 'PENDIENTE' and transaccion.estado != 'CREADA':
        return transaccion
    edad = (timezone.now() - transaccion.creado_en).total_seconds()
    if not forzar and edad < POLL_CONSULTA_DIRECTA_SEG:
        return transaccion
    if not transaccion.order_id:
        # Sin order_id no hay orden que consultar... pero puede existir en MP
        # igual (el POST salió y no volvió respuesta). Esta línea es el punto de
        # apalancamiento del diseño: convierte en resolvedores al polling del
        # POS, a cobros_vivos_de_ticket(refrescar=True), a cancelar(), al
        # webhook topic=order y al comando sincronizar_transacciones_mp, sin
        # tocar ninguno de ellos. Antes devolvía la fila intacta y la dejaba
        # colgada para siempre.
        return resolver_incierta(transaccion)
    try:
        resp = _request(transaccion.config, 'GET', f'/v1/orders/{transaccion.order_id}')
        data = _json_o_error(resp, f'consultar orden {transaccion.order_id}')
    except MercadoPagoError:
        # El polling no debe romper el cobro por un error transitorio de red
        return transaccion
    estado_mp = str(data.get('status') or '').lower()
    estado_local = _ESTADO_DESDE_ORDEN.get(estado_mp)
    if not estado_local:
        logger.warning(f"MP: estado de orden desconocido '{estado_mp}' en {transaccion.external_reference}")
        return transaccion
    payment = _extraer_payment_de_orden(data)
    if (estado_local == 'PENDIENTE' and transaccion.canal == 'QR'
            and edad > QR_TIMEOUT_SEGUNDOS + 30):
        # SOLO QR: su orden lleva expiration_time PT{QR_TIMEOUT}S, así que
        # pasado el plazo ya no puede cobrarse y cerrarla local es fiel.
        # En POINT el body NO lleva expiración y 'at_terminal' mapea a
        # PENDIENTE: cerrarla por edad marcaba EXPIRADA (estado FINAL) un cobro
        # que MP reporta VIVO en la pantalla del terminal — el guard dejaba de
        # verlo y el POS ofrecía "Reintentar". Es el cobro que demora más de
        # 2,5 min (cliente buscando la tarjeta, PIN malo, el "REINTENTE" del
        # incidente NICK2). Un cobro Point solo se cierra por su propio TTL de
        # incierta o porque MP lo diga.
        estado_local = 'EXPIRADA'
    return _aplicar_estado(transaccion, estado_local,
                           detalle=data.get('status_detail') or estado_mp,
                           payment=payment or None, raw=data)


def _cerrar_incierta_si_vencida(trx):
    """Cierra localmente una incierta que ya nadie va a poder resolver.

    Cerrar NO es perder plata: `_aplicar_estado` solo bloquea APROBADA→otro y
    FINAL→PENDIENTE, así que ERROR/EXPIRADA → APROBADA SÍ pasa. Si el cliente
    paga 20 minutos después, el webhook topic=payment resucita la fila (matchea
    por external_reference exacto) y la conciliación la muestra. Esa
    reversibilidad es lo que hace aceptable "fallar abierto" y desbloquear la
    caja en vez de dejarla trabada para siempre.
    """
    if _edad_seg(trx) <= ttl_incierta(trx):
        return trx
    minutos = int(_edad_seg(trx) // 60)
    destino = 'EXPIRADA' if trx.canal == 'QR' else 'ERROR'
    logger.warning(
        "MP: cerrando cobro incierto %s ($%s, %s) como %s tras %s min sin confirmación",
        trx.external_reference, trx.monto, trx.canal, destino, minutos,
    )
    return _aplicar_estado(
        trx, destino,
        detalle=f'Sin confirmación de Mercado Pago tras {minutos} min')


def resolver_incierta(trx, presupuesto=None, permitir_replay=True):
    """Averigua qué pasó de verdad con un cobro que quedó sin confirmar.

    Es la contraparte obligatoria de pre-crear la fila: sin esto el arreglo
    cambiaría "no queda rastro y se cobra dos veces" por "queda una fila zombi
    que bloquea el ticket para siempre", que es peor.

    Orden de los intentos, y por qué:
      1. RESERVADA → el POST nunca salió. Se cierra sin gastar un byte de red.
      2. REPLAY del POST con la MISMA X-Idempotency-Key → es lo ÚNICO que
         recupera el order_id cuando todavía nadie pagó, y el order_id es lo que
         desbloquea toda la maquinaria existente (consultar, cancelar, webhook
         topic=order, sincronizar_transacciones_mp).
      3. payments/search por external_reference → cubre el caso caro: el
         cliente YA pagó.
      4. TTL → si nada de lo anterior contestó, se cierra local (reversible).

    `permitir_replay=False` para los barridos batch: la idempotencia de MP tiene
    ventana, y un re-POST viejo puede CREAR una orden nueva en vez de devolver
    la original. Un cron que cobre de nuevo sería peor que el bug original.
    """
    # Sin order_id y no cerrada: sigue siendo resoluble. Incluye PENDIENTE, que
    # es donde queda una incierta cuyo pago MP reportó pending/in_process.
    if trx.order_id or trx.estado in ESTADOS_FINALES_MP or trx.estado == 'APROBADA':
        return trx

    fase = fase_cobro(trx)
    edad = _edad_seg(trx)

    # ── (1) RESERVADA: por construcción no existe en MP ────────────────────
    if fase == FASE_RESERVADA:
        if edad > MP_RESERVA_MAX_SEG:
            return _aplicar_estado(trx, 'ERROR',
                                   detalle='No se alcanzó a enviar a Mercado Pago')
        return trx  # request en vuelo ahora mismo: no tocar
    if fase != FASE_ENVIADA:
        return trx

    # ── (2) piso entre resoluciones: no martillar a MP desde cada poll ─────
    meta = _rm(trx)
    ultimo = meta.get('ultimo_resolver')
    if ultimo:
        from django.utils.dateparse import parse_datetime
        try:
            previo = parse_datetime(ultimo)
            if previo and (timezone.now() - previo).total_seconds() < MP_RESOLVER_MIN_SEG:
                return trx
        except (TypeError, ValueError):
            pass
    if presupuesto is not None and not presupuesto.hay(4):
        return _cerrar_incierta_si_vencida(trx)
    _marcar_rm(trx, ultimo_resolver=timezone.now().isoformat(),
               resoluciones=int(meta.get('resoluciones') or 0) + 1)

    rechazo_determinista = False

    # ── (3) REPLAY con la misma idempotency key ────────────────────────────
    body = meta.get('body')
    sufijo = meta.get('sufijo') or ''
    if permitir_replay and body and edad <= MP_REPLAY_MAX_EDAD_SEG:
        try:
            resp = _request(trx.config, 'POST', '/v1/orders', json_body=body,
                            idempotency_key=trx.external_reference + sufijo,
                            timeout=MP_RECUPERACION_TIMEOUT)
        except MercadoPagoError:
            resp = None
        if resp is not None:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code < 400 and data.get('id'):
                trx.order_id = str(data['id'])
                trx.save(update_fields=['order_id', 'actualizado_en'])
                _marcar_rm(trx, fase=FASE_RESUELTA, recuperada='replay')
                logger.warning("MP: cobro incierto %s RECUPERADO por replay → orden %s",
                               trx.external_reference, trx.order_id)
                return consultar_estado(trx, forzar=True)
            if resp.status_code >= 400:
                # Solo un 4xx de VALIDACIÓN con el MISMO body prueba que el POST
                # original también fue rechazado y que la orden nunca existió.
                # Un 5xx o un 429 no prueban nada (la orden pudo crearse y
                # perderse la respuesta) y un 404/409 menos todavía: esos dejan
                # la fila incierta hasta el TTL, que es el lado seguro.
                texto = json.dumps(data).lower()
                if (400 <= resp.status_code < 500
                        and resp.status_code not in (404, 408, 409, 423, 425, 429)
                        and not any(p in texto for p in ('idempotenc', 'conflict'))):
                    rechazo_determinista = True
                else:
                    logger.warning(
                        "MP: replay de %s devolvió %s — no prueba rechazo, la fila "
                        "sigue sin confirmar", trx.external_reference, resp.status_code)

    # ── (4) ¿hay plata? payments/search por referencia exacta ──────────────
    # UNA sola página acotada. NUNCA buscar_pagos_dia acá: pagina hasta 20
    # páginas × 15s = 300s contra los 60s de gunicorn.
    try:
        resp = _request(trx.config, 'GET', '/v1/payments/search',
                        params={'external_reference': trx.external_reference, 'limit': 5},
                        timeout=MP_RECUPERACION_TIMEOUT)
        datos = resp.json() if resp.status_code < 400 else {}
    except (MercadoPagoError, ValueError):
        datos = {}
    for pago in (datos.get('results') or []):
        # Verificar en Python: si MP ignorara el filtro, un pago ajeno acá sería
        # imputarle a este ticket plata de otra venta.
        if str(pago.get('external_reference') or '') != trx.external_reference:
            continue
        estado_local = _ESTADO_DESDE_PAYMENT.get(str(pago.get('status') or '').lower())
        if not estado_local:
            continue
        _marcar_rm(trx, recuperada='payments_search')
        logger.warning("MP: cobro incierto %s RESUELTO por payments/search → %s",
                       trx.external_reference, estado_local)
        return _aplicar_estado(trx, estado_local,
                               detalle=pago.get('status_detail') or 'Recuperado de Mercado Pago',
                               payment=pago, raw=pago)

    # ── (5)/(6) nada confirmó: cerrar si corresponde, si no seguir incierta ─
    if rechazo_determinista:
        return _aplicar_estado(
            trx, 'ERROR',
            detalle='Mercado Pago rechazó la orden: nunca se creó el cobro')
    return _cerrar_incierta_si_vencida(trx)


def cancelar(transaccion):
    """Cancela el cobro. NUNCA debe fallar hacia el cajero salvo un caso: que
    el cliente haya alcanzado a pagar (ahí corresponde devolución). Cualquier
    otro rechazo de MP (orden ya expirada/cancelada/inexistente) se resuelve
    cerrando la transacción local — la orden en MP expira sola igual."""
    if transaccion.estado == 'APROBADA':
        raise MercadoPagoError('El pago ya fue aprobado: corresponde devolución, no cancelación.')
    if transaccion.estado in ESTADOS_FINALES_MP:
        return transaccion

    # ── Fila sin order_id: JAMÁS cerrarla a ciegas ─────────────────────────
    # Este era el agujero que habría dejado el arreglo sin efecto. CANCELADA es
    # un estado FINAL: saca la fila de cobros_vivos_de_ticket y con eso el guard
    # de crear_orden la da por muerta y emite una orden nueva. O sea, el doble
    # cobro del 13-09 reintroducido por la puerta de atrás.
    if not transaccion.order_id:
        if es_reserva(transaccion):
            # Nunca se envió a MP: cerrarla es seguro y no cuesta red.
            return _aplicar_estado(transaccion, 'CANCELADA',
                                   detalle='Nunca se envió a Mercado Pago')
        if es_incierta(transaccion):
            transaccion = resolver_incierta(transaccion)
            if transaccion.estado == 'APROBADA':
                raise MercadoPagoError('El cliente alcanzó a pagar: el cobro quedó APROBADO.')
            if transaccion.estado in ESTADOS_FINALES_MP:
                return transaccion
            # Se recuperó el order_id: sigue por el camino normal de abajo.
        if not transaccion.order_id:
            # DEFAULT del bloque, a propósito: cualquier fila sin order_id que
            # no sea una reserva probada se NIEGA a cerrarse. Si esto fuera un
            # `if` por estado, un estado nuevo en el futuro volvería a caer en
            # el `_aplicar_estado(..., 'CANCELADA')` del final y reabriría el
            # doble cobro. El POS traduce este error a vigilancia del cobro.
            raise MercadoPagoError(
                'No pudimos confirmar con Mercado Pago si este cobro existe. '
                'NO cobres de nuevo por Mercado Pago: revisa la pantalla de la '
                'máquina, usa otro medio de pago o espera la confirmación.')

    if transaccion.order_id:
        try:
            resp = _request(transaccion.config, 'POST', f'/v1/orders/{transaccion.order_id}/cancel',
                            idempotency_key=f'{transaccion.external_reference}-cancel')
            _json_o_error(resp, f'cancelar orden {transaccion.order_id}')
        except MercadoPagoError as e:
            logger.warning(f"MP: cancelar {transaccion.external_reference} rechazado: {e.mensaje}")
            # Point en pantalla: MP NO permite cancelar por API mientras la
            # orden está 'at_terminal' (probado en vivo, 409 cannot_cancel).
            # Se cancela EN la máquina; no marcar cancelada local mientras
            # la pantalla siga mostrando el cobro.
            if transaccion.canal == 'POINT' and 'cannot_cancel' in str(e.detalle):
                raise MercadoPagoError(
                    'Cancela el cobro EN LA MÁQUINA (botón atrás/cancelar del '
                    'terminal): mientras está en su pantalla, MP no permite '
                    'cancelarlo remoto.')
            # ¿Alcanzó a pagar? Es lo ÚNICO que justifica no cancelar.
            try:
                actualizada = consultar_estado(transaccion, forzar=True)
            except Exception:  # noqa: BLE001 — sin red igual cerramos local
                actualizada = transaccion
            if actualizada.estado == 'APROBADA':
                raise MercadoPagoError('El cliente alcanzó a pagar: el cobro quedó APROBADO.')
            if actualizada.estado in ESTADOS_FINALES_MP:
                return actualizada
            # MP no dejó cancelar pero tampoco está pagada (p.ej. ya expiró en
            # MP y el cancel devuelve 4xx): cerrar local y dejar que expire.
            return _aplicar_estado(transaccion, 'CANCELADA',
                                   detalle=f'Cancelada local ({e.mensaje[:80]})')
    return _aplicar_estado(transaccion, 'CANCELADA', detalle='Cancelada desde el POS')


# ==================== LIBERAR TERMINAL (lo que la máquina tiene encolado) ====
#
# Mercado Pago admite UNA operación encolada por terminal Point
# (already_queued_order_on_terminal). Una orden que nadie terminó ni canceló
# —aunque el sistema la haya cerrado como ERROR/EXPIRADA/CANCELADA, o la haya
# mandado OTRA caja que después perdió la máquina— sigue ocupando esa ranura y
# bloquea todos los cobros nuevos. Estas funciones preguntan a MP orden por
# orden qué sigue vivo en ESA máquina y cancelan lo que la API deja cancelar.

# Status de orden que MP considera vivos (ocupan la ranura del terminal).
ORDENES_NO_FINALES_MP = ('created', 'processing', 'action_required', 'at_terminal')
TERMINAL_DESCONOCIDO = 'desconocido'


def _terminal_de_orden(data, trx):
    """Terminal al que MP asocia la orden.

    Fuente 1: ``config.point.terminal_id`` del GET /v1/orders/{id} (la
    referencia oficial de la Orders API lo documenta en la respuesta, junto
    con print_on_terminal). Fuente 2: el device_id con que se creó la fila
    (metadatos ``_rm``, filas creadas desde el 21-09). Si no hay ninguna de
    las dos: 'desconocido' (se lista, no se cancela sola).
    """
    terminal = ''
    if isinstance(data, dict):
        punto = (data.get('config') or {}).get('point') or {}
        terminal = str(punto.get('terminal_id') or '') if isinstance(punto, dict) else ''
    return terminal or str(_rm(trx).get('device_id') or '') or TERMINAL_DESCONOCIDO


def _clave_cuenta(config, cache):
    """Identidad de la cuenta MP con la que cobra una config (cacheada por
    config_id: _cuenta_de puede costar una consulta). Sin cuenta en BD (token
    legacy por env) se agrupa por empresa de la sucursal."""
    if config.id in cache:
        return cache[config.id]
    cuenta = _cuenta_de(config)
    clave = ('cuenta', cuenta.id) if cuenta else ('empresa', config.sucursal.empresa_id)
    cache[config.id] = clave
    return clave


def _dict_orden_terminal(trx, status_mp, terminal_id, coincide):
    sucursal = trx.config.sucursal
    return {
        'transaccion_id': trx.id,
        'order_id': trx.order_id,
        'external_reference': trx.external_reference,
        'status_mp': status_mp,
        'terminal_id': terminal_id,
        'terminal_coincide': bool(coincide),
        'sucursal': sucursal.alias or sucursal.nombre or f'Sucursal {sucursal.id}',
        'caja': trx.config.nombre,
        'correlativo': trx.correlativo_ticket or '',
        'monto': int(trx.monto or 0),
        'creado_en': timezone.localtime(trx.creado_en).strftime('%d-%m %H:%M'),
        'estado_local': trx.estado,
        'cancelable': bool(status_mp == 'created' and coincide),
    }


def ordenes_en_terminal(config, dias=10, max_ordenes=60, informe=None):
    """Órdenes Point de este sistema que MP reporta VIVAS en la máquina de
    ``config`` (o de terminal desconocido). Lista de dicts, más nueva primero.

    Candidatas: filas POINT con order_id de los últimos ``dias`` días, de
    CUALQUIER caja que cobre con la misma cuenta MP (la máquina pertenece a la
    cuenta; la caja que la usaba antes pudo ser de otra sucursal) y en
    CUALQUIER estado local (una fila cerrada como ERROR/EXPIRADA/CANCELADA
    puede seguir viva en MP). Se consulta cada una con GET /v1/orders/{id}
    y timeout corto; un fallo de red no corta el barrido.

    Efecto lateral, y deliberado: si MP dice ``processed`` para una fila que
    localmente NO está APROBADA, se aplica APROBADA (es plata real que tiene
    que aparecer en Dineros / huérfanos). Nunca se reabre una fila final a
    PENDIENTE.

    Si se pasa ``informe`` (dict), se le anotan ``errores``,
    ``pagadas_detectadas`` y ``consultadas``.
    """
    informe = informe if isinstance(informe, dict) else {}
    errores = informe.setdefault('errores', [])
    pagadas = informe.setdefault('pagadas_detectadas', [])
    dias = max(1, int(dias or 10))
    device_id = config.device_id or ''
    cache_cuentas = {}
    clave_propia = _clave_cuenta(config, cache_cuentas)

    qs = (TransaccionMercadoPago.objects
          .filter(canal='POINT', tipo='VENTA',
                  creado_en__gte=timezone.now() - _dt.timedelta(days=dias))
          .exclude(order_id='')
          .select_related('config', 'config__cuenta', 'config__sucursal', 'sucursal')
          .order_by('-creado_en'))
    candidatas = []
    for trx in qs.iterator():
        if _clave_cuenta(trx.config, cache_cuentas) != clave_propia:
            continue
        candidatas.append(trx)
        if len(candidatas) >= max_ordenes:
            break

    encontradas = []
    for trx in candidatas:
        try:
            resp = _request(trx.config, 'GET', f'/v1/orders/{trx.order_id}',
                            timeout=MP_RECUPERACION_TIMEOUT)
            data = _json_o_error(resp, f'liberar terminal: consultar orden {trx.order_id}')
        except MercadoPagoError as e:
            errores.append({'transaccion_id': trx.id, 'order_id': trx.order_id,
                            'correlativo': trx.correlativo_ticket or '', 'error': e.mensaje})
            if e.red and _breaker_abierto():
                # Sin red no tiene sentido martillar las que faltan.
                errores.append({'transaccion_id': None, 'order_id': '',
                                'correlativo': '', 'error': 'Mercado Pago no responde: '
                                'se dejó de consultar el resto de las órdenes.'})
                break
            continue
        informe['consultadas'] = informe.get('consultadas', 0) + 1
        status_mp = str(data.get('status') or '').lower()
        terminal_id = _terminal_de_orden(data, trx)

        if status_mp == 'processed' and trx.estado != 'APROBADA':
            estado_antes = trx.estado
            payment = _extraer_payment_de_orden(data)
            _aplicar_estado(trx, 'APROBADA',
                            detalle=f"{data.get('status_detail') or 'processed'} "
                                    '(detectado al liberar terminal)',
                            payment=payment or None, raw=data)
            logger.warning("MP: liberar terminal %s: la orden %s (%s, $%s) figura PAGADA en MP "
                           "y localmente estaba %s -> APROBADA",
                           device_id, trx.order_id, trx.external_reference, trx.monto,
                           estado_antes)
            pagadas.append({**_dict_orden_terminal(trx, status_mp, terminal_id,
                                                   terminal_id == device_id),
                            'estado_anterior': estado_antes, 'cancelable': False})
            continue

        if status_mp not in ORDENES_NO_FINALES_MP:
            continue
        coincide = bool(device_id) and terminal_id == device_id
        if not coincide and terminal_id != TERMINAL_DESCONOCIDO:
            continue  # vive en OTRA máquina de la cuenta: no es problema de esta
        encontradas.append(_dict_orden_terminal(trx, status_mp, terminal_id, coincide))
    return encontradas


def liberar_terminal(config, dias=10, aplicar=False, usuario=None):
    """Informe de lo que MP tiene encolado en la máquina de ``config`` y, con
    ``aplicar=True``, cancelación de lo cancelable (status ``created`` y
    terminal coincidente) vía POST /v1/orders/{id}/cancel.

    Devuelve dict: device_id, encontradas, canceladas, no_cancelables
    (at_terminal/processing/action_required o terminal desconocido: hay que
    cancelarlas EN la máquina o apretar Actualizar en su pantalla), errores,
    pagadas_detectadas, aplicado. JAMÁS crea órdenes ni cobra.
    """
    if not config.device_id:
        raise MercadoPagoError('Esa caja no tiene máquina Point asociada.')
    informe = {
        'device_id': config.device_id,
        'dias': max(1, int(dias or 10)),
        'encontradas': [],
        'canceladas': [],
        'no_cancelables': [],
        'errores': [],
        'pagadas_detectadas': [],
        'aplicado': bool(aplicar),
    }
    encontradas = ordenes_en_terminal(config, dias=informe['dias'], informe=informe)
    informe['encontradas'] = encontradas
    quien = getattr(usuario, 'username', None) or 'sistema'

    for orden in encontradas:
        if not orden['cancelable']:
            informe['no_cancelables'].append(orden)
            continue
        if not aplicar:
            continue
        trx = (TransaccionMercadoPago.objects.select_related('config', 'config__sucursal')
               .filter(pk=orden['transaccion_id']).first())
        if trx is None or trx.estado == 'APROBADA':
            informe['errores'].append({**orden, 'error': 'La fila local figura APROBADA: '
                                       'no se cancela (corresponde devolución).'})
            continue
        try:
            resp = _request(trx.config, 'POST', f'/v1/orders/{trx.order_id}/cancel',
                            idempotency_key=f'{trx.external_reference}-cancel',
                            timeout=MP_RECUPERACION_TIMEOUT)
        except MercadoPagoError as e:
            informe['errores'].append({**orden, 'error': e.mensaje})
            continue
        try:
            _json_o_error(resp, f'liberar terminal: cancelar orden {trx.order_id}')
        except MercadoPagoError as e:
            if resp.status_code == 409 or 'cannot_cancel' in str(e.detalle):
                informe['no_cancelables'].append({
                    **orden, 'cancelable': False,
                    'motivo': 'Mercado Pago no la deja cancelar por API: ya está en la '
                              'pantalla de la máquina.'})
            else:
                informe['errores'].append({**orden, 'error': e.mensaje})
            continue
        _aplicar_estado(trx, 'CANCELADA', detalle=f'Liberada del terminal por {quien}'[:120])
        logger.warning("MP: liberar terminal %s: orden %s (%s, ticket %s, $%s, estaba %s) "
                       "CANCELADA en MP por %s",
                       config.device_id, trx.order_id, trx.external_reference,
                       orden['correlativo'] or '-', orden['monto'], orden['estado_local'], quien)
        informe['canceladas'].append({**orden, 'estado_local': 'CANCELADA'})
    return informe


# ==================== DEVOLUCIONES ====================

def reembolsar(transaccion, monto=None, usuario=None):
    """Refund total (monto=None) o parcial vía /v1/payments/{id}/refunds.
    Crea la fila DEVOLUCION vinculada y marca la venta DEVUELTA si fue total.
    """
    if transaccion.tipo != 'VENTA' or transaccion.estado not in ('APROBADA', 'DEVUELTA'):
        raise MercadoPagoError('Solo se puede devolver una venta MP aprobada.')
    if not transaccion.payment_id:
        # La orden puede tener el payment adentro — refrescar antes de rendirse
        consultar_estado(transaccion, forzar=True)
        transaccion.refresh_from_db()
        if not transaccion.payment_id:
            raise MercadoPagoError('La transacción no tiene payment_id: no se puede devolver por API.')

    body = {}
    if monto is not None:
        monto = int(monto)
        if monto <= 0 or monto > transaccion.monto:
            raise MercadoPagoError('Monto de devolución inválido.')
        body['amount'] = monto
    ref_devolucion = f"{transaccion.external_reference}-REF-{uuid.uuid4().hex[:6]}"
    resp = _request(transaccion.config, 'POST',
                    f'/v1/payments/{transaccion.payment_id}/refunds',
                    json_body=body or None, idempotency_key=ref_devolucion)
    data = _json_o_error(resp, f'refund payment {transaccion.payment_id}')

    monto_devuelto = int(round(float(data.get('amount') or monto or transaccion.monto)))
    devolucion = TransaccionMercadoPago.objects.create(
        config=transaccion.config,
        sucursal_id=transaccion.sucursal_id,
        ticket=transaccion.ticket,
        correlativo_ticket=transaccion.correlativo_ticket,
        tipo='DEVOLUCION',
        canal=transaccion.canal,
        transaccion_origen=transaccion,
        external_reference=ref_devolucion,
        order_id=transaccion.order_id,
        payment_id=str(data.get('id') or ''),
        monto=monto_devuelto,
        estado='DEVUELTA',
        estado_detalle='Refund vía API',
        raw_response=data,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )
    total_devuelto = sum(d.monto for d in transaccion.devoluciones.all())
    if total_devuelto >= transaccion.monto:
        _aplicar_estado(transaccion, 'DEVUELTA', detalle='Devolución total')
    logger.info(f"MP: refund {ref_devolucion} por ${monto_devuelto} sobre {transaccion.external_reference}")
    return devolucion


def reembolsar_pagos_de_ticket(ticket, usuario=None):
    """Devuelve TODOS los cobros MP aprobados/consumidos de un ticket (para
    anulación de ticket). Devuelve lista de devoluciones; lanza MercadoPagoError
    si alguna falla (el caller decide si bloquear la anulación)."""
    ventas = TransaccionMercadoPago.objects.filter(
        ticket=ticket, tipo='VENTA', estado='APROBADA'
    )
    devoluciones = []
    for venta in ventas:
        devoluciones.append(reembolsar(venta, usuario=usuario))
    return devoluciones


def transacciones_mp_de_dte(dte):
    """Cobros MP (VENTA) asociados a la venta original de un DTE: por el
    ticket cuyo folio_dte == numero_documento en la misma sucursal."""
    from app.models import Ticket
    ticket = Ticket.objects.filter(
        sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento,
    ).order_by('-id').first()
    if not ticket:
        return []
    from django.db.models import Q
    return list(
        TransaccionMercadoPago.objects.filter(tipo='VENTA')
        .filter(Q(ticket=ticket) | Q(sucursal_id=dte.sucursal_id,
                                     correlativo_ticket=str(ticket.correlativo)))
        .filter(estado__in=('APROBADA', 'DEVUELTA'))
        .distinct().order_by('-monto')
    )


def _disponible_trx(trx):
    """Monto aún devolvible de una venta MP (monto − devoluciones hechas)."""
    devuelto = sum(d.monto for d in trx.devoluciones.all())
    return max(trx.monto - devuelto, 0)


def resumen_pagos_mp_de_dte(dte):
    """Para la acción "Verificar por Mercado Pago" de gestión-DTE."""
    filas = []
    total_disponible = 0
    for trx in transacciones_mp_de_dte(dte):
        disponible = _disponible_trx(trx)
        total_disponible += disponible
        filas.append({
            'external_reference': trx.external_reference,
            'canal': trx.canal,
            'medio': etiqueta_medio_mp(trx.metodo_pago_mp),
            'ultimos_4': trx.ultimos_4_digitos,
            'payment_id': trx.payment_id,
            'estado': trx.estado,
            'monto': trx.monto,
            'devuelto': trx.monto - disponible,
            'disponible': disponible,
            'fecha': timezone.localtime(trx.creado_en).strftime('%d/%m/%Y %H:%M'),
        })
    return {'transacciones': filas, 'total_disponible': total_disponible}


def devolver_por_nc(dte, monto, usuario=None):
    """Devuelve `monto` CLP a la(s) tarjeta(s) de los cobros MP de la venta
    original de `dte` (refund vía API — la plata vuelve al mismo medio).

    Se ejecuta ANTES de crear la NC: si acá falla, la NC no se emite y el
    operador puede elegir otro método. Reparte sobre las transacciones con
    saldo devolvible (mayor primero). Lanza MercadoPagoError si el saldo MP
    disponible no cubre el monto (evita doble devolución)."""
    monto = int(monto)
    if monto <= 0:
        raise MercadoPagoError('Monto de devolución inválido.')
    trxs = transacciones_mp_de_dte(dte)
    if not trxs:
        raise MercadoPagoError(
            'La venta original no tiene cobros Mercado Pago asociados '
            '(¿se pagó por otro medio o es anterior a la integración?).')
    disponibles = [(t, _disponible_trx(t)) for t in trxs]
    total_disp = sum(d for _t, d in disponibles)
    if total_disp < monto:
        raise MercadoPagoError(
            f'El saldo devolvible en Mercado Pago es ${total_disp:,} y la NC '
            f'pide ${monto:,} — probablemente ya se devolvió parte.'.replace(',', '.'))

    restante = monto
    devoluciones = []
    canal = trxs[0].canal
    # Medio real (debit_card / credit_card / …) del primer cobro devuelto:
    # la NC lo guarda en `tipo_tarjeta` para que la cuadratura reste la
    # devolución del sub-bucket MP correcto (débito / crédito / otros).
    medio = ''
    for trx, disp in disponibles:
        if restante <= 0:
            break
        if disp <= 0:
            continue
        tomar = min(restante, disp)
        devoluciones.append(reembolsar(trx, monto=tomar, usuario=usuario))
        if not medio:
            medio = trx.metodo_pago_mp or ''
        restante -= tomar
    logger.warning(
        "MP: devolución por NC de $%s sobre DTE %s (%s refund/s) por %s",
        monto, dte.numero_documento, len(devoluciones),
        getattr(usuario, 'username', 'sistema'),
    )
    return {'devoluciones': devoluciones, 'canal': canal, 'total': monto,
            'medio': medio}


# ==================== GUARD SERVER-SIDE ====================

def consumir_transaccion_aprobada(sucursal_id, correlativo, monto, detalle_pago=None):
    """Guard de registrar_pagos_ticket: busca una VENTA APROBADA no consumida
    para (sucursal, correlativo) con monto suficiente y la marca consumida
    atómicamente. Devuelve la transacción o None si no existe."""
    with transaction.atomic():
        transaccion = (
            TransaccionMercadoPago.objects.select_for_update()
            .filter(
                sucursal_id=sucursal_id,
                correlativo_ticket=str(correlativo),
                tipo='VENTA',
                estado='APROBADA',
                consumida=False,
                monto__gte=int(monto),
            )
            .order_by('creado_en')
            .first()
        )
        if not transaccion:
            return None
        transaccion.consumida = True
        if detalle_pago is not None:
            transaccion.detalle_pago = detalle_pago
            if detalle_pago.ticket_id:
                transaccion.ticket_id = detalle_pago.ticket_id
        transaccion.save(update_fields=['consumida', 'detalle_pago', 'ticket', 'actualizado_en'])
        return transaccion


# ==================== COBROS VIVOS (anti "se cobró dos veces") ====================
#
# Caso real (NICK2, 05-09-2026): el cobro se mandó a la Point, la máquina pidió
# REINTENTE, el cajero cerró la ventana de espera y el cliente igual pasó la
# tarjeta en el terminal. La venta se cerró con un CRÉDITO MANUAL: la plata
# entró por Mercado Pago pero la cuadratura la muestra en VISA-MC-AMEX (un
# Transbank que nunca va a depositar) y el cobro MP quedó huérfano. Estos
# helpers son la red que evita repetirlo: antes de cerrar la venta se pregunta
# a MP si ese cobro sigue vivo o ya se aprobó.

# Estados en los que el cobro todavía puede terminar en plata cobrada.
ESTADOS_EN_VUELO_MP = ('CREADA', 'PENDIENTE')

# Correlativos que no son tickets del POS (prueba de la pestaña de gestión y
# cobro directo en terminal): nunca bloquean una venta.
PREFIJOS_CORRELATIVO_SIN_TICKET = ('PRUEBA-', 'DIRECTO-')


def metodo_pago_ticket_de(transaccion):
    """Método de `METODO_PAGO_TICKET_CHOICES` que corresponde a este cobro.

    Mismo mapeo que aplica el POS al empujar el pago (debit → DÉBITO,
    credit → CRÉDITO, resto genérico), para que un pago reparado a mano caiga
    en el mismo sub-bucket de la cuadratura que uno registrado normalmente.
    """
    if transaccion.canal != 'POINT':
        return 'MP_QR'
    medio = (transaccion.metodo_pago_mp or '').lower()
    if 'debit' in medio:
        return 'MP_POINT_DEBITO'
    if 'credit' in medio:
        return 'MP_POINT_CREDITO'
    return 'MP_POINT'


def cobros_vivos_de_ticket(sucursal_id, correlativo, refrescar=False, presupuesto=None):
    """Cobros MP que aún pueden convertirse en (o ya son) plata de este ticket.

    Devuelve las transacciones EN VUELO (CREADA/PENDIENTE: el cobro sigue en
    la pantalla de la máquina o del QR) y las APROBADAS SIN CONSUMIR (el
    cliente ya pagó y ningún pago del ticket las respalda).

    `refrescar=True` consulta el estado real en MP antes de decidir: es lo que
    convierte "se cerró la ventana y el cliente pagó igual" en un dato conocido
    ANTES de cerrar la venta con otro medio.
    """
    correlativo = str(correlativo)
    if correlativo.startswith(PREFIJOS_CORRELATIVO_SIN_TICKET):
        return []
    candidatas = (
        TransaccionMercadoPago.objects
        .filter(sucursal_id=sucursal_id, correlativo_ticket=correlativo, tipo='VENTA')
        .exclude(estado__in=list(ESTADOS_FINALES_MP))
        .select_related('config')
        .order_by('creado_en')
    )
    vivos = []
    for trx in candidatas:
        # ── Reserva: la fila existe pero el POST no salió ───────────────────
        # Una reserva FRESCA sí cuenta como viva: es lo que serializa dos
        # cobros simultáneos del mismo ticket (dos pestañas, doble click). Una
        # reserva vieja es un request que murió antes de postear: no existe en
        # MP y se cierra sin gastar red.
        if es_reserva(trx):
            if _edad_seg(trx) > MP_RESERVA_MAX_SEG:
                _aplicar_estado(trx, 'ERROR',
                                detalle='No se alcanzó a enviar a Mercado Pago')
                continue
            vivos.append(trx)
            continue

        if refrescar and trx.estado in ESTADOS_EN_VUELO_MP:
            if presupuesto is not None and not presupuesto.hay():
                logger.warning(
                    "MP: presupuesto de red agotado, se decide con la BD para %s",
                    trx.external_reference,
                )
            else:
                try:
                    trx = consultar_estado(trx, forzar=True)
                except Exception:  # noqa: BLE001 — sin red se decide con lo que hay en BD
                    logger.warning(
                        "MP: no se pudo refrescar %s al revisar cobros vivos",
                        trx.external_reference,
                    )

        # Incierta que nadie pudo resolver y ya venció: se cierra local para no
        # dejar el ticket bloqueado para siempre (es reversible: si el cliente
        # paga después, el webhook la resucita).
        if es_incierta(trx):
            trx = _cerrar_incierta_si_vencida(trx)

        if trx.estado in ESTADOS_FINALES_MP:
            continue
        if trx.estado == 'APROBADA':
            if trx.consumida:
                continue
        elif trx.estado not in ESTADOS_EN_VUELO_MP:
            # CONTRACARGO y cualquier estado futuro: no es un cobro pendiente
            # de usar, se resuelve por conciliación y no debe frenar la venta.
            continue
        vivos.append(trx)
    return vivos


def resumen_cobro(trx):
    """Dict serializable de un cobro vivo (POS, mensajes de error, comandos)."""
    return {
        'id': trx.id,
        'estado': trx.estado,
        'estado_detalle': trx.estado_detalle,
        'aprobada': trx.estado == 'APROBADA',
        'canal': trx.canal,
        'monto': trx.monto,
        'payment_id': trx.payment_id,
        'external_reference': trx.external_reference,
        'metodo_pago_mp': trx.metodo_pago_mp,
        'medio': etiqueta_medio_mp(trx.metodo_pago_mp),
        'ultimos_4_digitos': trx.ultimos_4_digitos,
        'codigo_autorizacion': trx.codigo_autorizacion,
        'metodo_pago_ticket': metodo_pago_ticket_de(trx),
        'creado_en': timezone.localtime(trx.creado_en).strftime('%d-%m %H:%M'),
        'edad_segundos': int((timezone.now() - trx.creado_en).total_seconds()),
        # Aditivas: el front las ignora si no las conoce. NO renombrar las de
        # arriba — las consumen verificarCobrosMPPendientes y los dos modales
        # del guard de cierre.
        'incierto': es_incierta(trx),
        'fase': fase_cobro(trx),
        'bloquea_cierre': not (es_incierta(trx)
                               and _edad_seg(trx) > MP_INCIERTA_BLOQUEO_CIERRE_SEG),
    }


def cobros_no_respaldados(sucursal_id, correlativo, montos_mp, refrescar=True,
                          presupuesto=None):
    """Cobros vivos que los pagos MP del payload NO explican.

    `montos_mp` son los montos de los pagos MP integrados que el POS quiere
    registrar. El match es el mismo greedy de la pre-validación (al pago más
    grande, la transacción aprobada más chica que lo cubra); lo que sobra es
    exactamente lo que quedaría huérfano si la venta se cierra así.
    """
    vivos = cobros_vivos_de_ticket(sucursal_id, correlativo, refrescar=refrescar,
                                   presupuesto=presupuesto)
    if not vivos:
        return []
    aprobados = sorted([t for t in vivos if t.estado == 'APROBADA'],
                       key=lambda t: t.monto)
    en_vuelo = [t for t in vivos if t.estado != 'APROBADA']

    # ── Dos riesgos distintos, dos umbrales distintos ──────────────────────
    # Bloquear un COBRO MP nuevo del mismo ticket (riesgo: doble cobro, caro e
    # irreversible) se mantiene estricto hasta el TTL.
    # Bloquear el CIERRE de la venta (riesgo: caja trabada con cola de clientes)
    # se suelta a los 3 minutos. OJO: este guard corre en TODA venta, incluso
    # 100% efectivo, así que una incierta colgada trabaría la caja entera.
    # El riesgo residual —que el cliente pague después— cae en la maquinaria que
    # YA existe: webhook → APROBADA sin consumir → pantalla Dineros MP,
    # conciliar_mercadopago y reparar_cobro_mp_huerfano. Es detectable el mismo
    # día y reparable; una caja trabada no.
    sueltas = [t for t in en_vuelo
               if es_incierta(t) and _edad_seg(t) > MP_INCIERTA_BLOQUEO_CIERRE_SEG]
    if sueltas:
        for t in sueltas:
            logger.warning(
                "MP: cobro incierto %s ($%s, %s min) deja de bloquear el cierre del "
                "ticket %s — revisar en Conciliación Mercado Pago",
                t.external_reference, t.monto, int(_edad_seg(t) // 60), correlativo,
            )
        ids_sueltas = {t.id for t in sueltas}
        en_vuelo = [t for t in en_vuelo if t.id not in ids_sueltas]
    for monto in sorted((int(m) for m in montos_mp), reverse=True):
        idx = next((i for i, t in enumerate(aprobados) if t.monto >= monto), None)
        if idx is not None:
            aprobados.pop(idx)
    return sorted(aprobados + en_vuelo, key=lambda t: t.creado_en)


# ==================== WEBHOOK ====================

def _secrets_configurados():
    """Secrets de firma de todas las cuentas: BD (cifrados) primero, luego
    los fallbacks legacy por env var declarados en las configs."""
    secrets = []
    for cuenta in MercadoPagoCuenta.objects.filter(activo=True):
        valor = cuenta.get_webhook_secret()
        if valor and valor not in secrets:
            secrets.append(valor)
    nombres = (
        MercadoPagoConfig.objects.exclude(webhook_secret_env='')
        .values_list('webhook_secret_env', flat=True).distinct()
    )
    for nombre in nombres:
        valor = os.environ.get(nombre)
        if valor and valor not in secrets:
            secrets.append(valor)
    return secrets


def validar_firma(headers, data_id):
    """Valida x-signature contra los secrets configurados.

    Manifest EXACTO de MP: ``id:{data.id};request-id:{x-request-id};ts:{ts};``
    (data.id alfanumérico va en minúsculas). Comparación timing-safe +
    tolerancia anti-replay sobre ts.
    """
    x_signature = headers.get('x-signature') or headers.get('X-Signature') or ''
    x_request_id = headers.get('x-request-id') or headers.get('X-Request-Id') or ''
    if not x_signature or not x_request_id:
        return False
    ts, v1 = None, None
    for parte in x_signature.split(','):
        clave, _, valor = parte.strip().partition('=')
        if clave == 'ts':
            ts = valor.strip()
        elif clave == 'v1':
            v1 = valor.strip()
    if not ts or not v1:
        return False
    try:
        ts_num = float(ts)
        if ts_num > 1e12:  # milisegundos
            ts_num /= 1000.0
        if abs(time.time() - ts_num) > WEBHOOK_TS_TOLERANCIA_SEG:
            logger.warning('MP webhook: ts fuera de tolerancia (posible replay)')
            return False
    except ValueError:
        return False
    manifest = f"id:{str(data_id).lower()};request-id:{x_request_id};ts:{ts};"
    for secret in _secrets_configurados():
        esperado = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(esperado, v1):
            return True
    return False


def _resolver_transaccion_por_payment(data_id):
    """Con solo el payment id (webhook topic=payment) no sabemos la cuenta:
    probamos el GET con el token de cada config hasta que responda 200 y
    matcheamos por external_reference."""
    tokens_probados = set()
    for config in MercadoPagoConfig.objects.select_related('sucursal', 'cuenta').all():
        try:
            token = _token(config)
        except MercadoPagoError:
            continue
        if token in tokens_probados:
            continue  # una consulta por cuenta MP, no por sucursal
        tokens_probados.add(token)
        try:
            resp = _request(config, 'GET', f'/v1/payments/{data_id}')
        except MercadoPagoError:
            continue
        if resp.status_code != 200:
            continue
        try:
            payment = resp.json()
        except ValueError:
            continue
        ext_ref = payment.get('external_reference') or ''
        transaccion = TransaccionMercadoPago.objects.filter(external_reference=ext_ref).first()
        if transaccion:
            return transaccion, payment
        # refund de un pago nuestro: el refund comparte external_reference base.
        # Anclado al '-REF-' real (ver reembolsar): con el sufijo de intento
        # determinista, un startswith suelto haría que 'RM-7-100-c3i01' matchee
        # también a 'RM-7-100-c3i01x' y el webhook se aplicaría a la fila
        # equivocada, escribiendo payment_id/monto_neto donde no corresponde.
        if ext_ref:
            base = TransaccionMercadoPago.objects.filter(
                external_reference__startswith=ext_ref + '-REF-').first()
            if base:
                return base, payment
    return None, None


def procesar_notificacion(request_id, topic, data_id, payload, headers):
    """Procesa un webhook. SIEMPRE debe terminar rápido y sin excepción hacia
    la vista (la vista responde 200 pase lo que pase; el polling es la red)."""
    evento, creado = MercadoPagoWebhookEvento.objects.get_or_create(
        request_id=request_id or f'sin-request-id-{uuid.uuid4().hex[:10]}',
        defaults={'topic': (topic or '')[:40], 'data_id': str(data_id or '')[:60],
                  'payload': payload},
    )
    if not creado and evento.procesado:
        return evento  # re-entrega ya procesada: idempotencia

    firma_ok = validar_firma(headers, data_id)
    evento.firma_valida = firma_ok
    if not firma_ok:
        evento.error = 'Firma x-signature inválida o ausente'
        evento.save(update_fields=['firma_valida', 'error'])
        logger.warning(f"MP webhook: firma inválida (request-id={request_id}, topic={topic})")
        return evento

    try:
        transaccion, payment = None, None
        if topic in ('payment', 'payment.updated', 'payment.created'):
            transaccion, payment = _resolver_transaccion_por_payment(data_id)
        elif topic in ('order', 'merchant_order', 'topic_merchant_order_wh') and data_id:
            # `and data_id`: sin eso, un webhook con data.id vacío hacía
            # filter(order_id='') y enganchaba la fila SIN order_id más reciente
            # (ahora hay muchas: las reservas/inciertas), estampándole
            # webhook_recibido_en y dando el evento por procesado.
            transaccion = TransaccionMercadoPago.objects.filter(order_id=str(data_id)).first()
            if transaccion:
                transaccion = consultar_estado(transaccion, forzar=True)
                # Estampar que la novedad llegó por webhook (consultar_estado
                # no lo hace: es la misma función que usa el polling)
                if transaccion and not transaccion.webhook_recibido_en:
                    transaccion.webhook_recibido_en = timezone.now()
                    transaccion.save(update_fields=['webhook_recibido_en', 'actualizado_en'])
        if transaccion and payment:
            estado_local = _ESTADO_DESDE_PAYMENT.get(str(payment.get('status') or '').lower())
            if estado_local:
                _aplicar_estado(transaccion, estado_local,
                                detalle=payment.get('status_detail') or '',
                                payment=payment, raw=payment, via_webhook=True)
        if not transaccion:
            evento.error = 'Sin transacción local asociada'
        evento.procesado = True
        evento.save(update_fields=['firma_valida', 'procesado', 'error'])
    except Exception as e:  # noqa: BLE001 — el webhook jamás propaga
        evento.error = str(e)[:2000]
        evento.save(update_fields=['firma_valida', 'error'])
        logger.error(f"MP webhook: error procesando {request_id}: {e}")
    return evento
