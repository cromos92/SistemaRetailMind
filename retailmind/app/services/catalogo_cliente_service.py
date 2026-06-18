"""
Service proxy de CATÁLOGO para la app móvil de fidelización.

La app móvil NO puede llevar la API key del ecommerce (un secreto en el cliente
es extraíble). Por eso RetailMind actúa de **proxy**: la app pide el catálogo a
``/api/v1/cliente/catalogo/``, este service reenvía la consulta a la API del
ecommerce destino (Realsport o Paola) usando las credenciales de
``CredencialesEcommerce`` (header ``X-AllConnected-Key``), normaliza la respuesta
y la cachea.

Resiliencia bajo carga (que un pico de usuarios no tumbe nada):
- Las llamadas al ecommerce pasan por ``ecommerce_http`` (pool de conexiones,
  timeouts cortos y circuit breaker por tienda).
- Cache dedicado ``catalogo`` (TTL corto) + copia "última buena" (TTL largo):
  si el ecommerce cae o el circuito está abierto, se sirve la última versión
  buena (STALE) en vez de propagar el error. Así el catálogo sigue arriba.
"""
from __future__ import annotations

import html as _html
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.core.cache import caches
from django.db.models import Q
from django.utils.html import strip_tags

from app.models import CredencialesEcommerce
from app.services import ecommerce_http

logger = logging.getLogger('app')

_cache = caches['catalogo']

TIMEOUT_SEGUNDOS = 12
CACHE_TTL = 300            # 5 min: ventana "fresca"
CACHE_TTL_STALE = 6 * 3600  # 6 h: última versión buena para servir si el ecommerce cae
PRODUCTS_PATH = '/api/v1/products/'
CATEGORIES_PATH = '/api/v1/categories/'
STATUS_VENDIBLE = 'activo'  # Product.Status.ACTIVO en el ecommerce


class CatalogoError(Exception):
    """Error de catálogo presentable al cliente (tienda no configurada, 404…)."""


class CatalogoNoDisponible(CatalogoError):
    """El ecommerce no respondió (red/timeout/5xx/circuito): candidato a stale."""


def _resolver_credencial(tienda: str) -> CredencialesEcommerce:
    """CredencialesEcommerce activa de la tienda (por ``codigo`` o ``tipo``)."""
    clave = (tienda or '').strip().lower()
    if not clave:
        raise CatalogoError('Debes indicar la tienda.')
    cred = (
        CredencialesEcommerce.objects
        .filter(activo=True)
        .filter(Q(codigo=clave) | Q(tipo=clave))
        .order_by('-prioridad')
        .first()
    )
    if cred is None:
        raise CatalogoError(f'Tienda no disponible: {tienda}.')
    return cred


def _clp(value) -> Optional[int]:
    """Decimal/str de pesos → int CLP (sin decimales). None/'' → None."""
    if value in (None, ''):
        return None
    try:
        return int(Decimal(str(value)).quantize(Decimal('1')))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalizar_variante(v: dict) -> dict:
    return {
        'sku': v.get('sku') or '',
        'nombre': v.get('name') or '',
        'atributos': v.get('attributes') or {},
        'stock': v.get('stock') or 0,
        'precio': _clp(v.get('price_override')),  # None → hereda el del producto
        'activa': bool(v.get('is_active', True)),
    }


def _normalizar_producto(p: dict) -> dict:
    """ProductSerializer del ecommerce → shape plano para la app."""
    imagenes = []
    portada = None
    for img in (p.get('images') or []):
        url = img.get('thumbnail_url') or img.get('url') or ''
        if not url:
            continue
        imagenes.append(url)
        if portada is None and img.get('is_primary'):
            portada = url
    if portada is None and imagenes:
        portada = imagenes[0]

    return {
        'sku': p.get('sku') or '',
        'nombre': p.get('name') or '',
        # La descripción es HTML (CKEditor); la app la muestra como texto plano,
        # así que limpiamos las etiquetas y desescapamos las entidades.
        'descripcion': _html.unescape(strip_tags(p.get('description') or '')).strip(),
        'marca': p.get('brand') or '',
        # Disponibilidad canónica del ecommerce (cubre track_inventory=False y el
        # stock agregado de variantes); cae a stock>0 si el campo no viene.
        'disponible': p.get('is_available'),
        'precio': _clp(p.get('price')),
        # compare_price_active = el comparativo VIGENTE (respeta la ventana de
        # fechas); si la promo no está activa viene null y la app no marca oferta.
        'precio_comparacion': _clp(p.get('compare_price_active')),
        'stock': p.get('stock') or 0,
        'imagen': portada,
        'imagenes': imagenes,
        'variantes': [_normalizar_variante(v) for v in (p.get('variants') or [])],
    }


def _get(cred: CredencialesEcommerce, path: str, params: dict) -> dict:
    """GET resiliente a la API del ecommerce. Devuelve el JSON.

    Lanza CatalogoNoDisponible ante red/timeout/5xx/circuito (candidato a stale)
    y CatalogoError ante 404 / 4xx / respuesta inválida.
    """
    try:
        r = ecommerce_http.request(cred, 'GET', path, params=params,
                                  timeout=TIMEOUT_SEGUNDOS)
    except ecommerce_http.CircuitOpen:
        raise CatalogoNoDisponible('La tienda no está disponible en este momento.')
    except ecommerce_http.EcommerceHTTPError as exc:
        logger.warning('catalogo: fallo de red en %s: %s', path, exc)
        raise CatalogoNoDisponible('No se pudo conectar con la tienda.')

    if r.status_code == 404:
        raise CatalogoError('Producto no encontrado.')
    if r.status_code >= 500:
        raise CatalogoNoDisponible('La tienda no está disponible en este momento.')
    if r.status_code != 200:
        logger.warning('catalogo: %s respondió HTTP %s', path, r.status_code)
        raise CatalogoError('La tienda no está disponible en este momento.')
    try:
        return r.json()
    except ValueError:
        raise CatalogoError('Respuesta inválida de la tienda.')


def _cacheado(key: str, fetch, *, ttl: int = CACHE_TTL):
    """
    Cache con fallback STALE: devuelve lo fresco si está; si no, hace ``fetch``;
    si ``fetch`` falla por indisponibilidad del ecommerce y hay una última copia
    buena, la devuelve (STALE) en vez de propagar el error.
    """
    fresh = _cache.get(key)
    if fresh is not None:
        return fresh
    try:
        val = fetch()
    except CatalogoNoDisponible:
        last = _cache.get(key + ':last')
        if last is not None:
            logger.warning('Catálogo STALE servido para %s (ecommerce no disponible)', key)
            return last
        raise
    _cache.set(key, val, ttl)
    _cache.set(key + ':last', val, CACHE_TTL_STALE)
    return val


def listar_productos(tienda: str, *, page: int = 1, page_size: int = 20,
                     categoria: Optional[str] = None, q: Optional[str] = None,
                     destacados: bool = False) -> dict:
    """
    Lista paginada de productos vendibles de la tienda, con filtros opcionales:
    ``categoria`` (slug), ``q`` (búsqueda) y ``destacados`` (is_featured).
    """
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 20)))
    categoria = (categoria or '').strip()
    q = (q or '').strip()
    cred = _resolver_credencial(tienda)

    cache_key = (f'cat:{cred.codigo}:list:{page}:{page_size}'
                 f':{categoria}:{q}:{int(destacados)}')

    def fetch():
        params = {'status': STATUS_VENDIBLE, 'page': page, 'page_size': page_size}
        if categoria:
            params['category'] = categoria
        if q:
            params['search'] = q
        if destacados:
            params['is_featured'] = 'true'
        data = _get(cred, PRODUCTS_PATH, params)
        resultados = [_normalizar_producto(p) for p in (data.get('results') or [])]
        return {
            'tienda': cred.codigo,
            'count': data.get('count', len(resultados)),
            'page': page,
            'tiene_mas': bool(data.get('next')),
            'results': resultados,
        }

    return _cacheado(cache_key, fetch)


def listar_categorias(tienda: str) -> dict:
    """Categorías de la tienda (proxy de ``/api/v1/categories/``)."""
    cred = _resolver_credencial(tienda)
    cache_key = f'cat:{cred.codigo}:categorias'

    def fetch():
        data = _get(cred, CATEGORIES_PATH, {})
        categorias = [
            {
                'id': str(c.get('id') or ''),
                'nombre': c.get('name') or '',
                'slug': c.get('slug') or '',
            }
            for c in (data.get('results') or [])
            if (c.get('slug') or '') and (c.get('name') or '')
        ]
        return {'tienda': cred.codigo, 'results': categorias}

    return _cacheado(cache_key, fetch, ttl=CACHE_TTL_STALE)  # categorías cambian poco


def detalle_producto(tienda: str, sku: str) -> dict:
    """Detalle de un producto por SKU (solo vendible). Devuelve el normalizado."""
    cred = _resolver_credencial(tienda)
    sku = (sku or '').strip()
    if not sku:
        raise CatalogoError('SKU inválido.')

    cache_key = f'cat:{cred.codigo}:sku:{sku}'

    def fetch():
        data = _get(cred, f'{PRODUCTS_PATH}{sku}/', {})
        # Un SKU no vendible (borrador/inactivo/descontinuado) no debe ser
        # accesible/agregable por deep link. Usamos `is_active` del ecommerce
        # (= status in ACTIVO/PREVENTA), así no rompemos los productos en preventa.
        if not data.get('is_active', True):
            raise CatalogoError('Producto no disponible.')
        return _normalizar_producto(data)

    return _cacheado(cache_key, fetch)
