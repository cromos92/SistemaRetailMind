"""Utilidades de saneo de texto.

``limpiar_html`` es la única fuente de verdad para convertir HTML (típicamente de
CKEditor que viene en los catálogos de los ecommerce) a texto plano: quita las
etiquetas y desescapa las entidades (``&nbsp;``, ``&amp;``, ``<p>``, ...). Antes
esta lógica estaba inline en ``catalogo_cliente_service`` y solo se aplicaba a la
descripción, por eso nombres/marcas/categorías mostraban HTML crudo.
"""
from __future__ import annotations

import html as _html

from django.utils.html import strip_tags


def limpiar_html(texto) -> str:
    """HTML → texto plano. ``None``/'' → ''."""
    if not texto:
        return ''
    return _html.unescape(strip_tags(str(texto))).strip()
