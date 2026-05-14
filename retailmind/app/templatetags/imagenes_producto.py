"""
Template tags para mostrar la foto de portada de un producto.

Uso en templates:

    {% load imagenes_producto %}
    <img src="{% foto_portada producto %}" loading="lazy" class="producto-portada-img">

    {# Con tamaño hint para clase CSS #}
    {% foto_portada producto size='thumb' as url %}
    <img src="{{ url }}" loading="lazy">

La resolución es por el campo string ``producto.articulo`` (NO por el row de
Producto, que se repite por sucursal). Preferencia:

  1. Foto de la misma Empresa que la sucursal del producto.
  2. Fallback: cualquier ecommerce activo, ordenado por prioridad.

Resultado cacheado en el backend ``default`` con TTL 1h. Si no hay foto,
devuelve el placeholder SVG estático.
"""
from __future__ import annotations

import logging

from django import template
from django.templatetags.static import static

from app.services.realsport_imagenes_service import resolver_foto_portada_url

logger = logging.getLogger('app')
register = template.Library()

PLACEHOLDER_PATH = 'images/producto_placeholder.svg'


def _placeholder() -> str:
    try:
        return static(PLACEHOLDER_PATH)
    except Exception:
        return ''


@register.simple_tag
def foto_portada(producto, size: str = 'thumb') -> str:
    """Devuelve la URL de la portada de ``producto`` o un placeholder."""
    if not producto:
        return _placeholder()

    articulo = getattr(producto, 'articulo', None)
    if not articulo:
        return _placeholder()

    empresa_id = None
    sucursal = getattr(producto, 'sucursal', None)
    if sucursal is not None:
        empresa_id = getattr(sucursal, 'empresa_id', None)

    try:
        url = resolver_foto_portada_url(articulo, empresa_id)
    except Exception as exc:
        logger.warning('foto_portada falló para %s: %s', articulo, exc)
        return _placeholder()

    return url or _placeholder()


@register.simple_tag
def foto_portada_por_articulo(articulo: str, empresa_id: int | None = None) -> str:
    """Variante cuando sólo tenés el string del articulo."""
    if not articulo:
        return _placeholder()
    try:
        url = resolver_foto_portada_url(articulo, empresa_id)
    except Exception as exc:
        logger.warning('foto_portada_por_articulo falló para %s: %s', articulo, exc)
        return _placeholder()
    return url or _placeholder()
