"""
Búsqueda en internet de un artículo de la factura: qué producto es y su
color predominante, para clasificarlo (color / categoría / género) cuando la
factura no lo dice. Se pide desde el chat («busca en internet la línea 3»,
«busca los colores de todas») o con el botón de la tarjeta; corre en segundo
plano (web.investigar_en_segundo_plano) y lo encontrado queda aprendido
(ProductoAprendido) para la próxima factura con el mismo código.

Usa la herramienta de búsqueda web del servidor de Anthropic
(web_search_20260209): no hay nada que ejecutar de este lado. Cada búsqueda
tiene costo aparte de los tokens, por eso el tope de búsquedas por artículo
es bajo y se usa un modelo más barato que el de la lectura
(CARGA_FACTURA_MODELO_BUSQUEDA, default claude-sonnet-5).
"""
import json
import logging
import os

from . import lectura as svc_lectura
from .facturas import ErrorCarga

logger = logging.getLogger('app')

MODELO_BUSQUEDA = os.environ.get('CARGA_FACTURA_MODELO_BUSQUEDA', 'claude-sonnet-5')
MAX_BUSQUEDAS_POR_ARTICULO = 4
_MAX_VUELTAS = 4          # continuaciones si la API pausa el turno (pause_turn)
_HERRAMIENTA = {'type': 'web_search_20260209', 'name': 'web_search',
                'max_uses': MAX_BUSQUEDAS_POR_ARTICULO}


class ErrorBusqueda(ErrorCarga):
    """No se pudo buscar (mensaje para la persona)."""


def _esquema(generos):
    return {
        'type': 'object',
        'properties': {
            'encontrado': {'type': 'boolean'},
            'nombre': {'type': 'string', 'description': 'Nombre comercial del producto; "" si no se encontró'},
            'que_es': {'type': 'string', 'description': 'Qué es, en una frase: tipo de producto, uso, material; "" si no'},
            'color_primario': {'type': 'string', 'description': 'Color predominante, EXACTO de la lista de colores del sistema; "" si no se sabe'},
            'colores_vistos': {'type': 'string', 'description': 'Colores tal como los describe el fabricante ("Black/White"); "" si no'},
            'genero': {'type': 'string', 'enum': [''] + list(generos)},
            'categoria': {'type': 'string', 'description': '"" o valor EXACTO de la lista de categorías del sistema'},
            'fuente_url': {'type': 'string', 'description': 'URL de la página de donde salió; "" si no'},
            'confianza': {'type': 'string', 'enum': ['alta', 'media', 'baja']},
        },
        'required': ['encontrado', 'nombre', 'que_es', 'color_primario', 'colores_vistos', 'genero',
                     'categoria', 'fuente_url', 'confianza'],
        'additionalProperties': False,
    }


def _instrucciones(marca, articulo, descripcion, catalogo):
    return f"""Busca en internet el producto con código «{articulo}»{f' de la marca {marca}' if marca else ''}
(descripción en la factura del proveedor: «{descripcion or ''}»). Es para clasificarlo en el sistema de
una cadena de tiendas de calzado y ropa deportiva en Chile: hay que saber QUÉ es y su COLOR predominante.

Cómo buscar: como máximo {MAX_BUSQUEDAS_POR_ARTICULO} búsquedas. Primero «{articulo} {marca or ''}»;
si no aparece, prueba el código sin el sufijo de color (lo que va antes del guion) o con palabras de
la descripción. Prefiere la página del fabricante o de tiendas conocidas; un resultado de otro
producto no sirve.

Reglas de la respuesta (solo el JSON pedido):
- color_primario: uno de la lista de colores del sistema, el más cercano al color predominante de
  las fotos o del nombre del colorway (p. ej. "Black/White" → BLACK; "Wolf Grey" → GRIS si existe).
  Si ningún color de la lista calza o no está claro, "".
- categoria: solo si estás seguro, con el valor EXACTO de la lista; si no, "".
- Si el producto no aparece o no es claramente ese código: encontrado = false y todo vacío. No inventes.

Lista de colores del sistema: {', '.join(catalogo.get('colores') or []) or '(sin lista)'}
Lista de categorías del sistema: {'; '.join(catalogo.get('categorias') or []) or '(sin lista)'}
Lista de géneros: {', '.join(catalogo.get('generos') or [])}"""


def _normalizar(valor, lista):
    clave = ' '.join(str(valor or '').split()).upper()
    if not clave:
        return ''
    for v in lista or []:
        if ' '.join(str(v).split()).upper() == clave:
            return v
    return ''


def investigar_articulo(marca, articulo, descripcion, catalogo):
    """Un artículo → dict {encontrado, nombre, que_es, color_primario, colores_vistos,
    genero, categoria, fuente_url, confianza}. Los valores de lista vienen ya
    validados contra el catálogo ("" si no calzan)."""
    import anthropic

    cliente = svc_lectura._cliente()
    esquema = _esquema(catalogo.get('generos') or [])
    mensajes = [{'role': 'user', 'content': [
        {'type': 'text', 'text': _instrucciones(marca, articulo, descripcion, catalogo)}]}]
    respuesta = None
    con_formato = True
    for _vuelta in range(_MAX_VUELTAS):
        try:
            respuesta = svc_lectura._pedir(
                cliente, modelo=MODELO_BUSQUEDA, max_tokens=6000, tools=[_HERRAMIENTA],
                messages=mensajes,
                output_config=({'effort': 'medium', 'format': {'type': 'json_schema', 'schema': esquema}}
                               if con_formato else {'effort': 'medium'}))
        except anthropic.BadRequestError as exc:
            # Por si esta combinación (búsqueda web + salida estructurada) no
            # está disponible en la cuenta: se repite sin formato y se parsea.
            if con_formato and 'format' in str(exc).lower():
                con_formato = False
                mensajes[0]['content'][0]['text'] += '\n\nResponde ÚNICAMENTE con el objeto JSON (sin texto alrededor).'
                continue
            raise
        if respuesta.stop_reason == 'pause_turn':
            mensajes.append({'role': 'assistant', 'content': respuesta.content})
            continue
        break
    if respuesta is None:
        raise ErrorBusqueda('La búsqueda no devolvió respuesta.')
    texto = svc_lectura._texto(respuesta)
    try:
        datos = json.loads(texto if con_formato else texto[texto.find('{'):texto.rfind('}') + 1])
    except (ValueError, TypeError):
        raise ErrorBusqueda('No pude interpretar el resultado de la búsqueda.')
    datos['color_primario'] = _normalizar(datos.get('color_primario'), catalogo.get('colores'))
    datos['categoria'] = _normalizar(datos.get('categoria'), catalogo.get('categorias'))
    datos['genero'] = _normalizar(datos.get('genero'), catalogo.get('generos'))
    for k in ('nombre', 'que_es', 'colores_vistos', 'fuente_url'):
        datos[k] = str(datos.get(k) or '').strip()
    datos['encontrado'] = bool(datos.get('encontrado')) and bool(datos['nombre'] or datos['color_primario'])
    return datos
