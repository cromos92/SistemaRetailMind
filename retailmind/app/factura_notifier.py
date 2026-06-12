"""
Notificador de facturación hacia AllConnected (VicentAllEcommercesConected).

Cuando RetailMind emite un documento de venta (boleta/factura) o una nota de
crédito que anula uno, este módulo envía un POST al webhook de AllConnected
en un thread daemon para no bloquear la caja. Complementa el pull diario de
``GET /api/ventas/`` (conciliación): el webhook es push en tiempo real.

Configuración en settings / .env (reusa la config saliente del pull de pedidos):
    ALLCONNECTED_API_BASE_URL      = "https://ecommerce.webappsolutions.cl"
    ALLCONNECTED_API_KEY           = "<key compartida>"
    ALLCONNECTED_API_HEADER_NAME   = "X-AllConnected-Key"   (default)

Propias de este webhook (solo .env, leídas con os.environ):
    ALLCONNECTED_WEBHOOK_FACTURA_ENABLED = "true"   ← OBLIGATORIO para activar.
        Deshabilitado por default para que los commands de migración legacy
        que crean Dte masivamente no disparen miles de webhooks.
    ALLCONNECTED_WEBHOOK_FACTURA_PATH = "/system/webhooks/retailmind/factura/" (default)

Contrato del webhook (idempotente: AllConnected deduplica por numero_documento):
    POST {base_url}{path}
    Header: {ALLCONNECTED_API_HEADER_NAME}: {ALLCONNECTED_API_KEY}
    {
        "evento": "boleta_emitida" | "boleta_anulada",
        "rut_empresa": "76104936-4",
        "numero_documento": "12345",
        "tipo_documento": "BOLETA_ELECTRONICA",
        "fecha_emision": "2026-06-11T10:42:05-04:00",
        "monto_total": 109990,
        "origen": "ecommerce" | "pos",
        "numero_ticket_rm": "RM-A3F7C2E1" | null,
        "referencia_externa": "<numero_pedido_canal>" | null,
        "folio_despacho": "RE30005376" | null,
        "canal_origen": "PARIS" | null,
        "nota_credito": "871" | null,
        "sucursal": "<alias>",
        "usuario_emisor": "caja1"
    }

Reintentos: 3 intentos con backoff (5s, 30s) dentro del thread daemon.
Si todos fallan solo se loguea — la conciliación diaria por pull cubre el gap.
"""
from __future__ import annotations

import logging
import os
import threading
import time as time_mod
from datetime import datetime, time as dt_time

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('app')

# Espera entre intentos fallidos (el primer intento es inmediato).
_BACKOFF_SEGUNDOS = (5, 30)
_TIMEOUT_SEGUNDOS = 10

# Tipos de documento de venta que se notifican (mismo criterio que
# GET /api/ventas/ en app/api/external/views.py — VentasView).
TIPOS_VENTA = (
    'BOLETA ELECTRONICA',
    'BOLETA PAPEL',
    'FACTURA ELECTRONICA',
    'FACTURA EXENTA',
)
TIPOS_TRANSACCION_VENTA = ('VENTA', 'VENTA_PUBLICO')


def _habilitado() -> bool:
    return os.environ.get('ALLCONNECTED_WEBHOOK_FACTURA_ENABLED', '').lower() == 'true'


def _get_config() -> tuple[str, dict]:
    """Retorna (url, headers). url vacía = webhook deshabilitado."""
    base = (getattr(settings, 'ALLCONNECTED_API_BASE_URL', '') or '').rstrip('/')
    key = getattr(settings, 'ALLCONNECTED_API_KEY', '') or ''
    if not base or not key:
        return '', {}
    path = os.environ.get(
        'ALLCONNECTED_WEBHOOK_FACTURA_PATH', '/system/webhooks/retailmind/factura/'
    )
    header_name = getattr(settings, 'ALLCONNECTED_API_HEADER_NAME', 'X-AllConnected-Key')
    headers = {
        header_name: key,
        'Content-Type': 'application/json',
        'User-Agent': 'RetailMind-FacturaWebhook/1.0',
    }
    return f'{base}{path}', headers


def _es_documento_venta(dte) -> bool:
    return (
        not dte.es_nota_credito
        and dte.tipo_documento in TIPOS_VENTA
        and dte.tipo_transaccion in TIPOS_TRANSACCION_VENTA
        and not dte.descartado
    )


def _serializar_documento(dte, evento: str, nc_folio=None) -> dict:
    """Construye el payload del webhook para un documento de venta.

    `origen` usa la MISMA definición de venta internet que GET /api/ventas/
    (VentasView) y el Reporte de Ventas Internet: pedido ecommerce, ticket
    con modulo_origen='ECOMMERCE', o pago "Venta por Internet" (boletas
    emitidas a mano por el POS / data migrada de Laravel).
    """
    from app.models import PedidoEcommerce, Ticket, TicketDetallePago
    from app.models.dte import Dte_Detalle_Pago
    from app.utils_ventas import canal_desde_plataforma_pago, es_metodo_pago_internet

    pedido = (
        PedidoEcommerce.objects
        .filter(dte_id=dte.id)
        .values(
            'numero_ticket_rm', 'numero_pedido_canal',
            'numero_pedido_origen', 'correlativo', 'canal_origen',
        )
        .first()
    )
    ticket = None
    if dte.numero_documento:
        ticket = (
            Ticket.objects
            .filter(
                folio_dte=dte.numero_documento,
                sucursal_id=dte.sucursal_id,
                dte_generado=True,
            )
            .values('id', 'modulo_origen', 'referencia_folio')
            .first()
        )

    # Pago "Venta por Internet": primero el detalle del DTE; respaldo en el
    # pago del Ticket (cubre detalle no copiado o editado tras emitir).
    pago_net = None
    for p in (
        Dte_Detalle_Pago.objects
        .filter(dte_id=dte.id)
        .values('metodo_pago', 'tipo_tarjeta', 'voucher')
    ):
        if es_metodo_pago_internet(p['metodo_pago']):
            pago_net = p
            break
    if pago_net is None and ticket:
        pago_net = (
            TicketDetallePago.objects
            .filter(ticket_id=ticket['id'], metodo_pago='VENTA_INTERNET')
            .values('tipo_tarjeta', 'voucher')
            .first()
        )

    via_ecommerce = bool(pedido) or (
        ticket is not None and ticket['modulo_origen'] == 'ECOMMERCE'
    )
    es_ecommerce = via_ecommerce or pago_net is not None

    referencia_externa = None
    if pedido:
        referencia_externa = pedido['numero_pedido_canal'] or None
    elif ticket and ticket['referencia_folio']:
        referencia_externa = ticket['referencia_folio']
    elif pago_net and (pago_net.get('voucher') or '').strip():
        # Boleta POS: el voucher del pago internet guarda el N° de pedido
        # del marketplace.
        referencia_externa = pago_net['voucher'].strip()

    plataforma_pago = (pago_net.get('tipo_tarjeta') or '').strip() if pago_net else ''
    canal_origen = pedido['canal_origen'] if pedido else None
    if not canal_origen and plataforma_pago:
        canal_origen = canal_desde_plataforma_pago(plataforma_pago)

    fecha_dt = timezone.make_aware(
        datetime.combine(dte.fecha_emision, dte.hora or dt_time.min),
        timezone.get_current_timezone(),
    )

    return {
        'evento': evento,
        'rut_empresa': dte.emisor.rut if dte.emisor_id else None,
        'numero_documento': str(dte.numero_documento),
        'tipo_documento': (dte.tipo_documento or '').replace(' ', '_'),
        'fecha_emision': fecha_dt.isoformat(),
        'monto_total': int(dte.monto_con_iva or 0),
        'origen': 'ecommerce' if es_ecommerce else 'pos',
        'via_emision': 'ecommerce' if via_ecommerce else 'pos',
        'plataforma_pago': plataforma_pago or None,
        'numero_ticket_rm': pedido['numero_ticket_rm'] if pedido else None,
        'referencia_externa': referencia_externa,
        'folio_despacho': (pedido['correlativo'] or None) if pedido else None,
        'canal_origen': canal_origen,
        'nota_credito': str(nc_folio) if nc_folio else None,
        'sucursal': dte.sucursal.alias if dte.sucursal_id else None,
        'usuario_emisor': dte.responsable or None,
    }


def _do_post_con_reintentos(url: str, headers: dict, payload: dict) -> None:
    """POST con 3 intentos y backoff. Corre en thread daemon, nunca lanza."""
    doc = payload.get('numero_documento')
    evento = payload.get('evento')
    for intento in range(1 + len(_BACKOFF_SEGUNDOS)):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT_SEGUNDOS)
            if resp.ok:
                logger.info(
                    "Webhook factura OK → %s doc=%s (intento %d)", evento, doc, intento + 1
                )
                return
            logger.warning(
                "Webhook factura HTTP %s → %s doc=%s (intento %d): %s",
                resp.status_code, evento, doc, intento + 1, resp.text[:300],
            )
        except Exception as exc:
            logger.warning(
                "Webhook factura error → %s doc=%s (intento %d): %s",
                evento, doc, intento + 1, exc,
            )
        if intento < len(_BACKOFF_SEGUNDOS):
            time_mod.sleep(_BACKOFF_SEGUNDOS[intento])
    logger.error(
        "Webhook factura agotó reintentos → %s doc=%s. "
        "La conciliación diaria por GET /api/ventas/ lo cubrirá.", evento, doc,
    )


def _notificar(dte_id: int, evento: str, nc_id=None) -> None:
    """Serializa post-commit y despacha el POST en thread daemon."""
    from app.models import Dte

    try:
        dte = (
            Dte.objects
            .select_related('emisor', 'sucursal')
            .filter(id=dte_id)
            .first()
        )
        if not dte or not _es_documento_venta(dte):
            return

        nc_folio = None
        if nc_id:
            nc_folio = (
                Dte.objects.filter(id=nc_id).values_list('numero_documento', flat=True).first()
            )

        url, headers = _get_config()
        if not url:
            return
        payload = _serializar_documento(dte, evento, nc_folio=nc_folio)

        threading.Thread(
            target=_do_post_con_reintentos,
            args=(url, headers, payload),
            daemon=True,
            name=f'factura-webhook-{dte_id}',
        ).start()
    except Exception:
        # Nunca romper la emisión por un fallo de notificación.
        logger.exception("Error preparando webhook factura para DTE %s", dte_id)


def programar_notificacion_factura(dte) -> None:
    """
    Punto de entrada desde el signal post_save de Dte (solo created=True).

    - Documento de venta nuevo               → evento 'boleta_emitida'
    - Nota de crédito nueva con afectado     → evento 'boleta_anulada'
      (el payload describe el documento ORIGINAL; nota_credito = folio NC)

    El trabajo real queda diferido a transaction.on_commit para que los FKs
    poblados después de crear el Dte (ej. PedidoEcommerce.dte) ya estén
    persistidos al serializar.
    """
    if not _habilitado():
        return

    es_nc = dte.es_nota_credito or dte.tipo_documento == 'NOTA DE CREDITO'
    if es_nc:
        if not dte.documento_afectado_id:
            return
        afectado_id, nc_id, evento = dte.documento_afectado_id, dte.id, 'boleta_anulada'
    else:
        if dte.tipo_documento not in TIPOS_VENTA:
            return
        afectado_id, nc_id, evento = dte.id, None, 'boleta_emitida'

    transaction.on_commit(lambda: _notificar(afectado_id, evento, nc_id=nc_id))
