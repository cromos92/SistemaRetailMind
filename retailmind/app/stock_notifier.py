"""
Notificador de stock hacia AllConnected (VicentAllEcommercesConected).

Cuando RetailMind descuenta stock (venta, devolución, ajuste), este módulo
envía un POST al webhook de AllConnected en un thread daemon para no bloquear
la caja.

Configuración en settings / .env:
    ALLCONNECTED_WEBHOOK_URL = "http://<allconnected-host>/app/sincronizacion-stock/"

Opcional (legacy, fallback):
    ALLCONNECTED_CANAL_ORIGEN_ID = <ID del canal RetailMind en AllConnected>

Si ``ALLCONNECTED_WEBHOOK_URL`` está vacía, no se hace nada (deshabilitado).

**Identificación del canal origen**

El payload prioriza ``rut_empresa`` sobre ``idCanalOrigen``: AllConnected
resuelve el canal por ``(tipo_marketplace=RETAILMIND, rut_empresa, estado=ACTIVO)``.
Esto permite que un mismo RetailMind atienda a varias empresas (multi-tenant)
sin tener que hardcodear un ID por instancia. ``idCanalOrigen`` queda como
fallback para no romper integraciones viejas.

Contrato del webhook:
    POST /app/sincronizacion-stock/
    {
        "productos": [{"sku": "4810070", "new_stock": 12}],
        "rut_empresa": "76104936-4",   # nuevo, preferido
        "idCanalOrigen": 28              # legacy, opcional
    }

AllConnected actualiza ``SkuRetailMind`` y/o ``VariacionMaster`` y empuja a
los canales destino vinculados (Shopify, Paris, Ripley, realsport.cl, etc.).
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger('app.stock_notifier')


def _get_config() -> tuple[str, int]:
    """Retorna (webhook_url, canal_origen_id_legacy). Si url vacía, notificación deshabilitada."""
    url = getattr(settings, 'ALLCONNECTED_WEBHOOK_URL', '') or ''
    canal_id = getattr(settings, 'ALLCONNECTED_CANAL_ORIGEN_ID', 0) or 0
    return url.rstrip('/'), int(canal_id)


def _do_post(url: str, payload: dict, timeout: int = 5) -> None:
    """POST fire-and-forget en thread. Loguea resultado, nunca lanza."""
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=timeout,
        )
        if resp.ok:
            logger.info(
                "Stock push OK → %s (%d SKUs)",
                url, len(payload.get('productos', [])),
            )
        else:
            logger.warning(
                "Stock push HTTP %s → %s: %s",
                resp.status_code, url, resp.text[:300],
            )
    except requests.exceptions.Timeout:
        logger.warning("Stock push timeout → %s", url)
    except requests.exceptions.ConnectionError as exc:
        logger.warning("Stock push connection error → %s: %s", url, exc)
    except Exception as exc:
        logger.warning("Stock push error → %s: %s", url, exc)


def notificar_cambio_stock(
    sku: str,
    nuevo_stock: int,
    rut_empresa: Optional[str] = None,
    canal_origen_id: Optional[int] = None,
) -> None:
    """
    Envía la actualización de UN SKU a AllConnected.
    Si hay varios cambios simultáneos (ej. venta con múltiples items),
    usar ``notificar_cambios_stock_batch()``.

    Args:
        sku: código del SKU.
        nuevo_stock: stock total resultante.
        rut_empresa: RUT de la empresa dueña del SKU (preferido — AllConnected
            resuelve el Canal por (tipo_marketplace=RETAILMIND, rut, ACTIVO)).
        canal_origen_id: legacy. Si no se pasa rut_empresa y este queda en
            None, se usa el de settings (``ALLCONNECTED_CANAL_ORIGEN_ID``).
    """
    notificar_cambios_stock_batch(
        cambios=[{'sku': str(sku), 'new_stock': int(nuevo_stock)}],
        rut_empresa=rut_empresa,
        canal_origen_id=canal_origen_id,
    )


def notificar_cambios_stock_batch(
    cambios: List[Dict[str, int | str]],
    rut_empresa: Optional[str] = None,
    canal_origen_id: Optional[int] = None,
) -> None:
    """
    Envía un lote de cambios de stock a AllConnected en un thread daemon.
    No bloquea el caller. Si la URL no está configurada, no hace nada.

    Args:
        cambios: ``[{"sku": "4810070", "new_stock": 12}, ...]``
        rut_empresa: identificación preferida del canal origen — AllConnected
            resuelve por ``(tipo_marketplace=RETAILMIND, rut_empresa, ACTIVO)``.
        canal_origen_id: legacy. Solo se manda si ``rut_empresa`` falta.
            Si ambos faltan se usa ``ALLCONNECTED_CANAL_ORIGEN_ID`` de settings.
    """
    url, default_canal = _get_config()
    if not url:
        return  # Notificación deshabilitada

    rut = (rut_empresa or '').strip()
    canal = canal_origen_id if canal_origen_id is not None else default_canal

    # Necesitamos al menos uno de los dos identificadores.
    if not rut and not canal:
        logger.warning(
            "Stock push ignorado: ni rut_empresa (preferido) ni "
            "ALLCONNECTED_CANAL_ORIGEN_ID (legacy) configurados."
        )
        return

    payload: Dict[str, object] = {
        'productos': [
            {'sku': str(c.get('sku', '')), 'new_stock': int(c.get('new_stock', 0))}
            for c in cambios
            if c.get('sku')
        ],
    }
    if rut:
        payload['rut_empresa'] = rut
    if canal:
        # Lo enviamos siempre que esté disponible: si AllConnected no encuentra
        # canal por rut, cae al lookup por id como fallback.
        payload['idCanalOrigen'] = int(canal)

    if not payload['productos']:
        return

    thread = threading.Thread(
        target=_do_post,
        args=(url, payload),
        daemon=True,
        name=f"stock-push-{len(payload['productos'])}skus",
    )
    thread.start()
