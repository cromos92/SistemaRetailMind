"""Filtros de texto para templates.

Uso:
    {% load texto_filters %}
    {{ producto.descripcion|limpiar_html }}

Quita etiquetas HTML y desescapa entidades (CKEditor) para evitar que se vea
``<p>...</p>`` / ``&nbsp;`` literal cuando el texto viene de un ecommerce.
"""
from django import template

from app.utils_texto import limpiar_html as _limpiar_html

register = template.Library()


@register.filter(name='limpiar_html')
def limpiar_html(value):
    return _limpiar_html(value)
