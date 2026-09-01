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
import hashlib
import hmac
import io
import json
import logging
import os
import time
import uuid

import requests
from django.db import transaction
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
    """Error de negocio/comunicación con mensaje apto para mostrar al cajero."""

    def __init__(self, mensaje, detalle=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle


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


def _request(config, metodo, path, json_body=None, idempotency_key=None, params=None):
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
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error(f"MP: error de red en {metodo} {path}: {e}")
        raise MercadoPagoError('No se pudo contactar a Mercado Pago. Reintente.', detalle=str(e))
    return resp


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
        logger.error(f"MP: {contexto} falló ({resp.status_code}): {json.dumps(data)[:800]}")
        raise MercadoPagoError(f'Mercado Pago rechazó la operación: {mensaje_api}', detalle=data)
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
    if not cuenta.mp_user_id and data.get('id'):
        cuenta.mp_user_id = str(data['id'])[:30]
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
    if status_s != 200:
        raise MercadoPagoError(f'No se pudieron listar las sucursales MP (HTTP {status_s}).',
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
    """Máquinas Point de la cuenta (GET /point/integration-api/devices):
    id del device y su operating_mode (PDV = esclava del sistema,
    STANDALONE = cobra sola desde su pantalla)."""
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    try:
        resp = requests.get(
            MP_API_BASE + '/point/integration-api/devices',
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
    return [{
        'device_id': d.get('id'),
        'operating_mode': d.get('operating_mode'),
        'pos_id': d.get('pos_id'),
        'store_id': d.get('store_id'),
    } for d in (data.get('devices') or [])]


def cambiar_modo_device(cuenta, device_id, modo):
    """Cambia el operating_mode de una Point (PATCH
    /point/integration-api/devices/{id}). PDV la deja esclava del sistema
    (no acepta cobros digitados en su pantalla); STANDALONE la libera."""
    if modo not in ('PDV', 'STANDALONE'):
        raise MercadoPagoError('Modo inválido (PDV o STANDALONE).')
    token = cuenta.get_access_token()
    if not token:
        raise MercadoPagoError('La cuenta no tiene access token guardado.')
    try:
        resp = requests.patch(
            MP_API_BASE + f'/point/integration-api/devices/{device_id}',
            headers={'Authorization': f'Bearer {token}',
                     'Content-Type': 'application/json'},
            json={'operating_mode': modo}, timeout=REQUEST_TIMEOUT,
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
    return data.get('operating_mode') or modo


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

def crear_orden(config, correlativo, monto, descripcion='', canal='QR', usuario=None):
    """Crea la orden en MP (Orders API, processing_mode automatic) y la
    TransaccionMercadoPago local en PENDIENTE. Devuelve (transaccion, qr_data).
    """
    monto = int(monto)
    if monto <= 0:
        raise MercadoPagoError('El monto a cobrar debe ser mayor que cero.')
    if canal != 'QR':
        raise MercadoPagoError('Canal Point aún no habilitado (Fase 5 del plan).')
    if not config.external_pos_id:
        raise MercadoPagoError('La configuración MP de la sucursal no tiene caja (external_pos_id).')

    external_reference = f"RM-{config.sucursal_id}-{correlativo}-{uuid.uuid4().hex[:8]}"
    # Payload mínimo del create-order QR. OJO: la Orders API presencial
    # rechaza propiedades extra con 'unsupported_properties' (processing_mode,
    # p.ej., es de pagos online y NO va aquí — comprobado contra prod CL).
    body = {
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

    def _crear(cuerpo, sufijo=''):
        resp = _request(config, 'POST', '/v1/orders', json_body=cuerpo,
                        idempotency_key=external_reference + sufijo)
        return _json_o_error(resp, f'crear orden QR {external_reference}')

    try:
        data = _crear(body)
    except MercadoPagoError as e:
        # Auto-corrección: si MP rechaza propiedades puntuales, quitarlas y
        # reintentar UNA vez (la API cambia el contrato entre sitios/versiones).
        detalle = e.detalle if isinstance(e.detalle, dict) else {}
        props = []
        for err in (detalle.get('errors') or []):
            if isinstance(err, dict) and err.get('code') == 'unsupported_properties':
                for d in (err.get('details') or []):
                    props.append(str(d).lstrip('$.').split('.')[0].strip())
        props = [p for p in props if p and p in body and p not in ('type', 'transactions', 'config')]
        if not props:
            raise
        logger.warning(f"MP: reintento de orden sin propiedades no soportadas: {props}")
        body_min = {k: v for k, v in body.items() if k not in props}
        data = _crear(body_min, sufijo='-r')

    qr_data = (data.get('type_response') or {}).get('qr_data') or data.get('qr_data')
    if not qr_data:
        logger.error(f"MP: orden creada sin qr_data: {json.dumps(data)[:500]}")
        raise MercadoPagoError('Mercado Pago no devolvió el QR. Reintente.', detalle=data)

    transaccion = TransaccionMercadoPago.objects.create(
        config=config,
        sucursal_id=config.sucursal_id,
        correlativo_ticket=str(correlativo),
        tipo='VENTA',
        canal='QR',
        external_reference=external_reference,
        order_id=str(data.get('id') or ''),
        monto=monto,
        estado='PENDIENTE',
        raw_response=data,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
    )
    logger.info(f"MP: orden {transaccion.order_id} creada ({external_reference}, ${monto})")
    return transaccion, qr_data


def _extraer_payment_de_orden(data_orden):
    """La orden trae transactions.payments[]; devuelve el primero con datos."""
    pagos = ((data_orden.get('transactions') or {}).get('payments')) or []
    return pagos[0] if pagos else {}


def _aplicar_estado(transaccion, estado_nuevo, detalle='', payment=None,
                    raw=None, via_webhook=False):
    """Transición de estado con protecciones: una APROBADA solo puede pasar a
    DEVUELTA/CONTRACARGO; los estados finales no retroceden a PENDIENTE."""
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
        transaccion.payment_id = str(payment.get('id') or payment.get('payment_id') or transaccion.payment_id or '')
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
        campos += ['payment_id', 'metodo_pago_mp', 'ultimos_4_digitos',
                   'codigo_autorizacion', 'installments', 'monto_neto', 'fee_mp',
                   'money_release_date']

    if raw is not None:
        transaccion.raw_response = raw
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
        return transaccion
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
    if estado_local == 'PENDIENTE' and edad > QR_TIMEOUT_SEGUNDOS + 30:
        # La orden debió expirar; si MP no lo dice aún, la cerramos localmente
        estado_local = 'EXPIRADA'
    return _aplicar_estado(transaccion, estado_local,
                           detalle=data.get('status_detail') or estado_mp,
                           payment=payment or None, raw=data)


def cancelar(transaccion):
    if transaccion.estado == 'APROBADA':
        raise MercadoPagoError('El pago ya fue aprobado: corresponde devolución, no cancelación.')
    if transaccion.estado in ESTADOS_FINALES_MP:
        return transaccion
    if transaccion.order_id:
        try:
            resp = _request(transaccion.config, 'POST', f'/v1/orders/{transaccion.order_id}/cancel',
                            idempotency_key=f'{transaccion.external_reference}-cancel')
            _json_o_error(resp, f'cancelar orden {transaccion.order_id}')
        except MercadoPagoError as e:
            # Si MP dice que ya está pagada, el próximo polling la aprobará
            logger.warning(f"MP: cancelar {transaccion.external_reference} falló: {e.mensaje}")
            actualizada = consultar_estado(transaccion, forzar=True)
            if actualizada.estado == 'APROBADA':
                raise MercadoPagoError('El cliente alcanzó a pagar: el cobro quedó APROBADO.')
            raise
    return _aplicar_estado(transaccion, 'CANCELADA', detalle='Cancelada desde el POS')


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
        # refund de un pago nuestro: el refund comparte external_reference base
        if ext_ref:
            base = TransaccionMercadoPago.objects.filter(external_reference__startswith=ext_ref[:40]).first()
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
        elif topic in ('order', 'merchant_order', 'topic_merchant_order_wh'):
            transaccion = TransaccionMercadoPago.objects.filter(order_id=str(data_id)).first()
            if transaccion:
                transaccion = consultar_estado(transaccion, forzar=True)
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
