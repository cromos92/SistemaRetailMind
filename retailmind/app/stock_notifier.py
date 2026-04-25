"""
Notificador de stock hacia AllConnected (VicentAllEcommercesConected).

Cuando RetailMind descuenta stock (venta, devolución, ajuste), este módulo
envía un POST al webhook de AllConnected en un thread daemon para no bloquear
la caja.

Configuración en settings / .env:
    ALLCONNECTED_WEBHOOK_URL = "http://<allconnected-host>/app/sincronizacion-stock/"
    ALLCONNECTED_CANAL_ORIGEN_ID = <ID del canal RetailMind en AllConnected>

Si ALLCONNECTED_WEBHOOK_URL está vacío, no se hace nada (deshabilitado).

El webhook de AllConnected espera:
    POST /app/sincronizacion-stock/
    {
        "productos": [{"sku": "4810070", "new_stock": 12}],
        "idCanalOrigen": 28
    }

AllConnected actualiza VariacionMaster.stock_disponible y empuja a los
canales destino vinculados (Shopify, Paris, Ripley, etc.).
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger('app.stock_notifier')


def _get_config() -> tuple[str, int]:
    """Retorna (webhook_url, canal_origen_id). Si url vacía, notificación deshabilitada."""
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


def notificar_cambio_stock(sku: str, nuevo_stock: int, canal_origen_id: Optional[int] = None) -> None:
    """
    Envía la actualización de UN SKU a AllConnected.
    Si hay varios cambios simultáneos (ej. venta con múltiples items),
    usar `notificar_cambios_stock_batch()`.
    """
    notificar_cambios_stock_batch(
        cambios=[{'sku': str(sku), 'new_stock': int(nuevo_stock)}],
        canal_origen_id=canal_origen_id,
    )


def notificar_cambios_stock_batch(
    cambios: List[Dict[str, int | str]],
    canal_origen_id: Optional[int] = None,
) -> None:
    """
    Envía un lote de cambios de stock a AllConnected en un thread daemon.
    No bloquea el caller. Si la URL no está configurada, no hace nada.

    Args:
        cambios: [{"sku": "4810070", "new_stock": 12}, ...]
        canal_origen_id: override del ID de canal. Si None, usa el de settings.
    """
    url, default_canal = _get_config()
    if not url:
        return  # Notificación deshabilitada

    canal = canal_origen_id if canal_origen_id is not None else default_canal
    if not canal:
        logger.warning(
            "Stock push ignorado: ALLCONNECTED_CANAL_ORIGEN_ID no configurado."
        )
        return

    payload = {
        'productos': [
            {'sku': str(c.get('sku', '')), 'new_stock': int(c.get('new_stock', 0))}
            for c in cambios
            if c.get('sku')
        ],
        'idCanalOrigen': int(canal),
    }

    if not payload['productos']:
        return

    thread = threading.Thread(
        target=_do_post,
        args=(url, payload),
        daemon=True,
        name=f"stock-push-{len(payload['productos'])}skus",
    )
    thread.start()
