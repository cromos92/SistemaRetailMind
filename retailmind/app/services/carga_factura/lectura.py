"""
Lectura de facturas de compra en PDF con Claude: del PDF (escaneo o PDF del
SII) al JSON de factura que usan planificador y aplicador.

Cómo lee:
  - Escaneo (una imagen por página): se extraen las imágenes, se enderezan
    (Claude dice cuántos grados girar) y se envían; Claude tiene además una
    herramienta "ampliar" para mirar con zoom números chicos, la grilla de
    tallas o lo escrito a mano (lo mismo que se hizo a mano con las facturas
    Nike del 24-09-2026).
  - PDF con texto (el del SII): se envía el PDF tal cual.
  - Salida estructurada (JSON Schema): proveedor, folio, fecha, totales y
    líneas con código, descripción, curva de tallas, cantidad, precio, importe,
    precio de venta escrito a mano, reparto a tiendas y una propuesta de
    género / categoría / especialidades tomada de las listas reales del sistema.
  - Varias lecturas independientes (default 2) que se comparan; lo que no
    coincide queda marcado para revisión. Se verifica el cuadre (tallas =
    cantidad, cantidad × precio = importe, suma = total neto).

Nada de esto escribe en la base: el resultado se revisa en la vista previa de
cargar_productos_factura (o de la pantalla) antes de cargar.

Configuración: ANTHROPIC_API_KEY (settings) y opcionalmente
CARGA_FACTURA_MODELO (default claude-opus-5).
"""
import base64
import io
import json
import logging
import os
import re
import zlib
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from app.models import AtributoOpcion, Categoria
from app.utils_anthropic import explicar_error_anthropic, opciones_cliente_anthropic

from .facturas import ErrorCarga
from .perfiles import perfil_para

logger = logging.getLogger('app')

MODELO = os.environ.get('CARGA_FACTURA_MODELO', 'claude-opus-5')
_BETA_FALLBACK = 'server-side-fallback-2026-07-01'
_LADO_VISTA = 1600          # lado mayor de la imagen de página que se envía
_LADO_ZOOM = 1600           # lado mayor de un recorte ampliado
_MAX_TURNOS = 40            # tope de idas y vueltas con la herramienta de zoom
_PX_PAGINA = 1_000_000      # una imagen de más de esto se considera página escaneada

GENEROS = ('HOMBRE', 'MUJER', 'UNISEX', 'NIÑO', 'NIÑA')


class ErrorLectura(ErrorCarga):
    """No se pudo leer la factura (mensaje para el usuario)."""


# --------------------------------------------------------------------- PDF


def _imagenes_de_pagina(pdf):
    """Imágenes de página de un PDF escaneado (PIL, orden del archivo), o None.

    Se reconocen escaneos simples: una imagen grande por página, comprimida
    con Flate (RGB / gris / CMYK de 8 bits, sin predictor) o JPEG. Cualquier
    otra cosa → None y el PDF se envía tal cual.
    """
    from PIL import Image

    paginas = len(re.findall(rb'/Type\s*/Page(?![a-zA-Z])', pdf))
    imagenes = []
    # [^>]*? antes de /Subtype acota la búsqueda al diccionario del objeto
    # (sin eso, en un PDF de 20 MB la expresión recorre el binario entero).
    for m in re.finditer(rb'<<([^>]*?/Subtype\s*/Image[^>]*?(?:<<[^>]*>>[^>]*?)*)>>\s*stream\r?\n', pdf):
        cab = m.group(1)
        try:
            ancho = int(re.search(rb'/Width\s+(\d+)', cab).group(1))
            alto = int(re.search(rb'/Height\s+(\d+)', cab).group(1))
        except AttributeError:
            continue
        if ancho * alto < _PX_PAGINA:
            continue
        fin = pdf.find(b'endstream', m.end())
        if fin < 0:
            return None
        crudo = pdf[m.end():fin].rstrip(b'\r\n')
        filtro = re.search(rb'/Filter\s*/(\w+)', cab)
        filtro = filtro.group(1) if filtro else b''
        try:
            if filtro == b'DCTDecode':
                img = Image.open(io.BytesIO(crudo))
                img.load()
            elif filtro == b'FlateDecode':
                if b'/DecodeParms' in cab or b'/BitsPerComponent 8' not in cab.replace(b'\n', b' '):
                    return None
                datos = zlib.decompress(crudo)
                canales = len(datos) // (ancho * alto)
                modo = {1: 'L', 3: 'RGB', 4: 'CMYK'}.get(canales)
                if modo is None:
                    return None
                img = Image.frombytes(modo, (ancho, alto), datos[:ancho * alto * canales])
            else:
                return None
        except Exception:  # imagen que no sabemos decodificar: que la lea Claude del PDF
            logger.info('lectura factura: imagen de PDF no decodificable, se envía el PDF')
            return None
        imagenes.append(img.convert('RGB'))
    if not imagenes or (paginas and len(imagenes) < paginas):
        return None
    return imagenes


def _jpeg_b64(img, lado_max):
    from PIL import Image

    img = img.copy()
    img.thumbnail((lado_max, lado_max), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


def _bloque_imagen(img, lado_max):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                        'data': _jpeg_b64(img, lado_max)}}


# ------------------------------------------------------------------ Claude


def _cliente():
    try:
        import anthropic
    except ImportError:
        raise ErrorLectura('Falta el paquete "anthropic" en este entorno (pip install anthropic).')
    clave = getattr(settings, 'ANTHROPIC_API_KEY', '') or None
    # Cabecera del workspace si la clave es de organización (ver utils_anthropic).
    extra = opciones_cliente_anthropic()
    return anthropic.Anthropic(api_key=clave, **extra) if clave else anthropic.Anthropic(**extra)


def _pedir(cliente, **kwargs):
    """Una respuesta de Claude (streaming, con respaldo de modelo si la rechaza)."""
    import anthropic

    try:
        with cliente.beta.messages.stream(
            model=MODELO,
            betas=[_BETA_FALLBACK],
            extra_body={'fallbacks': 'default'},
            cache_control={'type': 'ephemeral'},
            **kwargs,
        ) as stream:
            respuesta = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        # Errores de configuración (clave, workspace, permisos) con un mensaje
        # que diga qué arreglar; el resto sube tal cual.
        explicacion = explicar_error_anthropic(exc)
        if explicacion:
            raise ErrorLectura(explicacion) from exc
        raise
    if respuesta.stop_reason == 'refusal':
        detalle = getattr(respuesta, 'stop_details', None)
        raise ErrorLectura(f'Claude no quiso leer el documento ({getattr(detalle, "category", "")}).')
    if respuesta.stop_reason == 'max_tokens':
        raise ErrorLectura('La respuesta se cortó por largo; divide el PDF y vuelve a intentar.')
    return respuesta


def _texto(respuesta):
    return next((b.text for b in respuesta.content if b.type == 'text'), '')


_ESQUEMA_ROTACION = {
    'type': 'object',
    'properties': {'giro_horario': {'type': 'integer', 'enum': [0, 90, 180, 270]}},
    'required': ['giro_horario'],
    'additionalProperties': False,
}


def _enderezar(cliente, img):
    """Imagen derecha: Claude dice cuántos grados girarla en sentido horario."""
    respuesta = _pedir(
        cliente, max_tokens=4000, output_config={
            'effort': 'low', 'format': {'type': 'json_schema', 'schema': _ESQUEMA_ROTACION}},
        messages=[{'role': 'user', 'content': [
            _bloque_imagen(img, 1000),
            {'type': 'text', 'text': ('Es la página escaneada de un documento. ¿Cuántos grados '
                                      'hay que girarla en sentido horario para que el texto '
                                      'quede derecho y se lea de izquierda a derecha?')}]}])
    giro = json.loads(_texto(respuesta))['giro_horario']
    return img.rotate(-giro, expand=True) if giro else img


# ----------------------------------------------------------------- esquema


def _listas_del_sistema():
    """Categorías v1.2 ('Padre > Hija'), especialidades (slug) y colores existentes."""
    categorias = sorted(
        f'{c.padre.nombre} > {c.nombre}'
        for c in Categoria.objects.filter(padre__nombre__in=('Calzado', 'Ropa', 'Accesorios'))
        .select_related('padre'))
    especialidades = sorted(set(AtributoOpcion.objects.filter(
        atributo__nombre__iexact='Especialidad').values_list('valor', flat=True)))
    colores = sorted({str(v).strip().upper() for v in AtributoOpcion.objects.filter(
        atributo__nombre__iexact='Color').values_list('valor', flat=True) if str(v).strip()})
    return categorias, especialidades, colores


def _redondear(valor):
    """Entero en pesos, mitad hacia arriba (round() de Python va al par)."""
    return int(Decimal(str(valor)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _nulo(tipo):
    return {'anyOf': [{'type': tipo}, {'type': 'null'}]}


def _esquema(categorias, especialidades, colores=()):
    categoria = ({'anyOf': [{'type': 'string', 'enum': categorias}, {'type': 'null'}]}
                 if categorias else _nulo('string'))
    especialidad = ({'type': 'string', 'enum': especialidades} if especialidades
                    else {'type': 'string'})
    color = ({'anyOf': [{'type': 'string', 'enum': list(colores)}, {'type': 'null'}]}
             if colores else _nulo('string'))
    linea = {
        'type': 'object',
        'properties': {
            'articulo': {'type': 'string'},
            'descripcion': {'type': 'string'},
            'color': color,
            'tallas': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'talla': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['talla', 'cantidad'], 'additionalProperties': False}},
            'cantidad': _nulo('integer'),
            'precio_unitario': _nulo('integer'),
            'descuento_pct': _nulo('number'),
            'descuento_monto': _nulo('integer'),
            'importe': _nulo('integer'),
            'precio_venta_a_mano': _nulo('integer'),
            'precio_venta_a_mano_alternativa': _nulo('integer'),
            'reparto_a_mano': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'tienda': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['tienda', 'cantidad'], 'additionalProperties': False}},
            'marca': _nulo('string'),
            'genero': {'anyOf': [{'type': 'string', 'enum': list(GENEROS)}, {'type': 'null'}]},
            'categoria': categoria,
            'especialidades': {'type': 'array', 'items': especialidad},
            'confianza': {'type': 'string', 'enum': ['alta', 'media', 'baja']},
            'dudas': {'type': 'string'},
        },
        'required': ['articulo', 'descripcion', 'color', 'tallas', 'cantidad', 'precio_unitario',
                     'descuento_pct', 'descuento_monto', 'importe',
                     'precio_venta_a_mano', 'precio_venta_a_mano_alternativa', 'reparto_a_mano',
                     'marca', 'genero', 'categoria', 'especialidades', 'confianza', 'dudas'],
        'additionalProperties': False,
    }
    factura = {
        'type': 'object',
        'properties': {
            'tipo_documento': {'type': 'string'},
            'folio': {'type': 'integer'},
            'proveedor_nombre': {'type': 'string'},
            'proveedor_rut': {'type': 'string'},
            'fecha_emision': {'type': 'string', 'format': 'date'},
            'marca': _nulo('string'),
            'total_unidades': _nulo('integer'),
            'descuento_global_pct': _nulo('number'),
            'descuento_global_monto': _nulo('integer'),
            'total_neto': _nulo('integer'),
            'paginas': {'type': 'array', 'items': {'type': 'integer'}},
            'lineas': {'type': 'array', 'items': linea},
            'observaciones': {'type': 'string'},
        },
        'required': ['tipo_documento', 'folio', 'proveedor_nombre', 'proveedor_rut',
                     'fecha_emision', 'marca', 'total_unidades', 'descuento_global_pct',
                     'descuento_global_monto', 'total_neto', 'paginas', 'lineas', 'observaciones'],
        'additionalProperties': False,
    }
    return {'type': 'object',
            'properties': {'facturas': {'type': 'array', 'items': factura}},
            'required': ['facturas'], 'additionalProperties': False}


_HERRAMIENTA_ZOOM = {
    'name': 'ampliar',
    'description': (
        'Devuelve un recorte AMPLIADO de una página ya derecha, para leer con detalle '
        'números pequeños, la grilla de tallas o lo escrito a mano. Coordenadas en '
        'milésimas del ancho (x) y del alto (y) de la página: 0,0 es la esquina '
        'superior izquierda y 1000,1000 la inferior derecha. Úsala cada vez que un '
        'dígito no se lea con total seguridad.'),
    'strict': True,
    'eager_input_streaming': True,
    'input_schema': {
        'type': 'object',
        'properties': {
            'pagina': {'type': 'integer', 'description': 'Número de página, desde 1'},
            'x0': {'type': 'integer'}, 'y0': {'type': 'integer'},
            'x1': {'type': 'integer'}, 'y1': {'type': 'integer'},
        },
        'required': ['pagina', 'x0', 'y0', 'x1', 'y1'],
        'additionalProperties': False,
    },
}


def _instrucciones(perfil, marca_hint, categorias, especialidades, pistas='', colores=()):
    from app.management.commands._data_recategorizacion_v12 import ESPECIALIDADES

    leyenda = '; '.join(f'{slug} = {ESPECIALIDADES[slug][1]}' for slug in especialidades
                        if slug in ESPECIALIDADES)
    indicaciones = (f'Indicaciones de la persona que sube la factura (mandan sobre lo demás): '
                    f'{pistas.strip()}' if pistas and pistas.strip() else '')
    return f"""Transcribe esta(s) factura(s) de compra de un proveedor de calzado y ropa deportiva
para cargar la mercadería en el sistema de una cadena de tiendas en Chile.

Qué devolver: una entrada en "facturas" por cada factura distinta del documento (un PDF
puede traer varias, una por página). Para cada factura: tipo de documento, folio (N° de
factura), nombre y RUT del emisor (el proveedor, con guion y dígito verificador), fecha de
emisión, subtotal / total NETO (sin IVA) y cantidad total de unidades si la trae, y las
páginas donde está.

Cada línea de producto:
- articulo: el código del producto tal como está impreso (no lo inventes ni lo acortes).
- descripcion: la descripción impresa.
- tallas: TODAS las celdas talla/cantidad de la línea, en el orden de la grilla. La talla
  va como está impresa (usa punto decimal: 8.5) y la cantidad como entero.
- color: el color de la línea si la descripción o una columna lo dice (NEGRO, PLATA, "V BLACK"
  es BLACK…), elegido de la lista de colores del sistema; null si no se sabe. En marcas
  donde el color va dentro del código (Nike: HQ6034-001) la descripción no lo dice: null.
- cantidad, precio_unitario (neto, entero en pesos), importe: los de las columnas impresas.
- descuento_pct / descuento_monto: si la línea tiene columna de descuento (en % o en pesos).
  En ese caso precio_unitario es el precio de LISTA (antes del descuento) e importe es el
  neto de la línea DESPUÉS del descuento, tal como está impreso. Si el descuento es uno
  solo aplicado al total de la factura (no por línea), ponlo en descuento_global_pct o
  descuento_global_monto de la factura y deja los importes de línea como están impresos.
- precio_venta_a_mano: si alguien escribió a mano un precio de venta junto a esa línea;
  si un dígito admite dos lecturas, pon la más probable y la otra en
  precio_venta_a_mano_alternativa (si no, null).
- reparto_a_mano: si hay columnas escritas a mano con tiendas (p.ej. NICK1, NICK2) y
  unidades por tienda. Ojo: a veces en esas columnas escriben un precio en vez de una
  cantidad; un número que termina en 990 es un precio, no un reparto.
- marca, genero, categoria y especialidades: tu propuesta según el producto (la persona
  la revisa). categoria y especialidades SOLO de las listas permitidas.
- confianza: alta / media / baja, y en "dudas" cualquier celda que no hayas podido leer
  con seguridad (vacío si nada).

Cómo leer bien:
- Amplía con la herramienta "ampliar" todo lo que no se lea con total seguridad: la
  grilla de tallas, precios, totales y todo lo escrito a mano. No adivines dígitos.
- Antes de entregar, comprueba cada línea: la suma de cantidades por talla = cantidad, y
  cantidad × precio_unitario (menos el descuento de la línea, si lo hay) = importe, con
  diferencia de pocos pesos por redondeo; y que la suma de importes (menos el descuento
  global, si lo hay) = total neto. Si algo no cuadra, vuelve a mirar con zoom antes de
  responder: casi siempre es una columna de descuento que no habías visto. Si aun así no
  cuadra, transcribe lo que ves y explícalo en "dudas"; nunca ajustes un número para que cuadre.

Listas permitidas:
- genero: {', '.join(GENEROS)}
- categoria: {'; '.join(categorias) if categorias else '(sin lista: usa null)'}
- especialidades: {leyenda or ', '.join(especialidades) or '(sin lista: deja vacío)'}
- color: {', '.join(colores) if colores else '(sin lista: usa null)'}

{('Marca esperada: ' + marca_hint + '.') if marca_hint else ''}
{perfil.pistas_lectura}
{indicaciones}
""".strip()


# ------------------------------------------------------------------ lectura


def _ejecutar_zoom(entrada, paginas):
    """Recorte ampliado pedido por Claude, o mensaje de error para el tool_result."""
    from PIL import Image

    try:
        pagina = int(entrada['pagina'])
        x0, y0, x1, y1 = (int(entrada[k]) for k in ('x0', 'y0', 'x1', 'y1'))
    except (KeyError, TypeError, ValueError):
        return None, 'Entrada inválida: faltan pagina, x0, y0, x1, y1 (enteros).'
    if not 1 <= pagina <= len(paginas):
        return None, f'La página {pagina} no existe (hay {len(paginas)}).'
    x0, x1 = sorted((max(0, min(1000, x0)), max(0, min(1000, x1))))
    y0, y1 = sorted((max(0, min(1000, y0)), max(0, min(1000, y1))))
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None, 'El recorte es demasiado chico: usa al menos 10 milésimas por lado.'
    img = paginas[pagina - 1]
    ancho, alto = img.size
    recorte = img.crop((ancho * x0 // 1000, alto * y0 // 1000, ancho * x1 // 1000, alto * y1 // 1000))
    lado = max(recorte.size)
    if lado < _LADO_ZOOM:  # ampliar lo chico para que se lea
        factor = _LADO_ZOOM / lado
        recorte = recorte.resize((int(recorte.width * factor), int(recorte.height * factor)), Image.LANCZOS)
    return _bloque_imagen(recorte, _LADO_ZOOM), None


def _una_lectura(cliente, contenido, paginas, instrucciones, esquema, orden, total=1,
                 progreso=None):
    """Una lectura completa (con su propio bucle de zoom). Devuelve el dict del esquema.

    `progreso(texto)` (opcional) recibe en qué va: la pantalla lo muestra
    mientras espera."""
    enfoque = ('Lee primero la tabla completa y después verifica.' if orden == 1 else
               'Esta es una segunda lectura independiente: recorre la tabla línea por línea '
               'y columna por columna, ampliando cada grilla de tallas.')
    mensajes = [{'role': 'user', 'content': contenido + [
        {'type': 'text', 'text': f'{instrucciones}\n\n{enfoque}'}]}]
    herramientas = [_HERRAMIENTA_ZOOM] if paginas else []
    for turno in range(_MAX_TURNOS):
        if progreso:
            progreso(f'Lectura {orden} de {total}: ' + (
                'leyendo el documento…' if turno == 0 else f'ampliando detalles (vuelta {turno})…'))
        respuesta = _pedir(cliente, max_tokens=64000, tools=herramientas, messages=mensajes,
                           output_config={'effort': 'high',
                                          'format': {'type': 'json_schema', 'schema': esquema}})
        if respuesta.stop_reason != 'tool_use':
            return json.loads(_texto(respuesta))
        mensajes.append({'role': 'assistant', 'content': respuesta.content})
        resultados = []
        for bloque in respuesta.content:
            if bloque.type != 'tool_use':
                continue
            imagen, error = _ejecutar_zoom(bloque.input if isinstance(bloque.input, dict) else {},
                                           paginas)
            resultados.append({'type': 'tool_result', 'tool_use_id': bloque.id,
                               **({'content': [imagen]} if imagen else
                                  {'content': error, 'is_error': True})})
        mensajes.append({'role': 'user', 'content': resultados})
    raise ErrorLectura(f'La lectura no terminó después de {_MAX_TURNOS} vueltas.')


def leer_pdf(pdf_bytes, marca=None, lecturas=2, progreso=None, pistas=''):
    """Lee el PDF. Devuelve {'lecturas': [dict, ...], 'modo': 'escaneo'|'pdf'}.

    `progreso(texto)` (opcional) recibe cada paso, para mostrarlo mientras se
    espera. `pistas`: indicaciones libres de la persona (marca, tipo de talla,
    cómo leer algo) que se suman a las del perfil."""
    cliente = _cliente()
    perfil = perfil_para(marca)
    categorias, especialidades, colores = _listas_del_sistema()
    esquema = _esquema(categorias, especialidades, colores)
    instrucciones = _instrucciones(perfil, marca, categorias, especialidades, pistas, colores)
    avisar = progreso or (lambda texto: None)

    avisar('Revisando el PDF…')
    imagenes = _imagenes_de_pagina(pdf_bytes)
    if imagenes:
        paginas = []
        for n, img in enumerate(imagenes, start=1):
            avisar(f'Enderezando la página {n} de {len(imagenes)}…')
            paginas.append(_enderezar(cliente, img))
        contenido = []
        for n, img in enumerate(paginas, start=1):
            contenido += [{'type': 'text', 'text': f'Página {n}:'}, _bloque_imagen(img, _LADO_VISTA)]
        modo = 'escaneo'
    else:
        if len(pdf_bytes) > 30 * 1024 * 1024:
            raise ErrorLectura('El PDF pesa más de 30 MB; envíalo en partes.')
        paginas = []
        contenido = [{'type': 'document', 'source': {
            'type': 'base64', 'media_type': 'application/pdf',
            'data': base64.standard_b64encode(pdf_bytes).decode('ascii')}}]
        modo = 'pdf'

    total = max(1, lecturas)
    resultado = [_una_lectura(cliente, contenido, paginas, instrucciones, esquema, orden,
                              total=total, progreso=progreso)
                 for orden in range(1, total + 1)]
    return {'lecturas': resultado, 'modo': modo}


# ------------------------------------------------------ comparar y convertir


def _tallas_dict(linea):
    tallas = {}
    for t in linea.get('tallas') or []:
        clave = str(t['talla']).strip().upper().replace(',', '.')
        tallas[clave] = tallas.get(clave, 0) + int(t['cantidad'])
    return tallas


def _importe_esperado(linea):
    """cantidad × precio de lista, menos el descuento de la línea (None si falta un dato)."""
    cant, precio = linea.get('cantidad'), linea.get('precio_unitario')
    if cant is None or precio is None:
        return None
    bruto = Decimal(cant) * Decimal(precio)
    if linea.get('descuento_pct'):
        return bruto * (1 - Decimal(str(linea['descuento_pct'])) / 100)
    if linea.get('descuento_monto'):
        return bruto - Decimal(linea['descuento_monto'])
    return bruto


def _tolerancia(unidades):
    """Pesos de diferencia que se aceptan por redondeo: $1 por unidad, mínimo $2."""
    return max(2, int(unidades or 0))


def _cuadra(linea):
    tallas = sum(_tallas_dict(linea).values())
    cant, importe = linea.get('cantidad'), linea.get('importe')
    esperado = _importe_esperado(linea)
    return ((cant is None or tallas == cant)
            and (esperado is None or importe is None
                 or abs(esperado - Decimal(importe)) <= _tolerancia(cant)))


def _clave_linea(linea):
    return re.sub(r'\s+', '', str(linea.get('articulo') or '').upper())


def combinar_lecturas(lecturas):
    """Una lectura consolidada + diferencias entre lecturas.

    Por factura (folio) y línea (código): si las lecturas coinciden, listo; si
    no, se queda la que cuadra (tallas = cantidad y cantidad × precio = importe)
    y la diferencia se anota en la línea para que la persona la revise.
    """
    base = json.loads(json.dumps(lecturas[0]))
    for otra in lecturas[1:]:
        por_folio = {f['folio']: f for f in otra.get('facturas', [])}
        for factura in base.get('facturas', []):
            gemela = por_folio.get(factura['folio'])
            if gemela is None:
                factura.setdefault('_revisar', []).append(
                    'otra lectura no encontró esta factura')
                continue
            por_codigo = {_clave_linea(l): l for l in gemela.get('lineas', [])}
            vistos = set()
            for linea in factura.get('lineas', []):
                clave = _clave_linea(linea)
                vistos.add(clave)
                par = por_codigo.get(clave)
                if par is None:
                    linea.setdefault('_revisar', []).append('la otra lectura no vio esta línea')
                    continue
                diferencias = []
                for campo in ('cantidad', 'precio_unitario', 'descuento_pct', 'importe',
                              'precio_venta_a_mano', 'color'):
                    if linea.get(campo) != par.get(campo):
                        diferencias.append(f'{campo}: {linea.get(campo)} / {par.get(campo)}')
                if _tallas_dict(linea) != _tallas_dict(par):
                    diferencias.append(f'tallas: {_tallas_dict(linea)} / {_tallas_dict(par)}')
                if diferencias:
                    if not _cuadra(linea) and _cuadra(par):
                        linea.update({k: par[k] for k in ('tallas', 'cantidad', 'precio_unitario',
                                                          'descuento_pct', 'descuento_monto',
                                                          'importe')})
                    if (linea.get('precio_venta_a_mano') != par.get('precio_venta_a_mano')
                            and par.get('precio_venta_a_mano')
                            and not linea.get('precio_venta_a_mano_alternativa')):
                        linea['precio_venta_a_mano_alternativa'] = par['precio_venta_a_mano']
                    linea.setdefault('_revisar', []).append(
                        'las lecturas no coinciden (' + '; '.join(diferencias) + ')')
            for clave, par in por_codigo.items():
                if clave not in vistos:
                    factura.setdefault('_revisar', []).append(
                        f'otra lectura vio una línea {par.get("articulo")} que esta no tiene')
    return base


def revisar_cuadre(factura):
    """Problemas de cuadre de una factura leída (lista de textos)."""
    problemas = []
    unidades, neto = 0, Decimal(0)
    for l in factura.get('lineas', []):
        suma = sum(_tallas_dict(l).values())
        unidades += suma
        if l.get('cantidad') is not None and suma != l['cantidad']:
            problemas.append(f'{l["articulo"]}: tallas suman {suma}, la línea dice {l["cantidad"]}')
        esperado = _importe_esperado(l)
        if esperado is not None and l.get('importe') is not None:
            if abs(esperado - Decimal(l['importe'])) > _tolerancia(l.get('cantidad')):
                desc = (f' − {l["descuento_pct"]:g}%' if l.get('descuento_pct') else
                        f' − ${l["descuento_monto"]}' if l.get('descuento_monto') else '')
                problemas.append(f'{l["articulo"]}: {l["cantidad"]} × {l["precio_unitario"]}{desc} '
                                 f'≠ importe {l["importe"]}')
        if l.get('importe') is not None:
            neto += Decimal(l['importe'])
    if factura.get('descuento_global_pct'):
        neto *= 1 - Decimal(str(factura['descuento_global_pct'])) / 100
    elif factura.get('descuento_global_monto'):
        neto -= Decimal(factura['descuento_global_monto'])
    if factura.get('total_unidades') is not None and unidades != factura['total_unidades']:
        problemas.append(f'unidades: líneas suman {unidades}, la factura dice {factura["total_unidades"]}')
    if (factura.get('total_neto') is not None
            and abs(neto - Decimal(factura['total_neto'])) > _tolerancia(len(factura.get('lineas', [])))):
        problemas.append(f'neto: líneas suman {_redondear(neto)}, la factura dice {factura["total_neto"]}')
    return problemas


def a_json_de_carga(factura, sucursal, marca=None, fuente=''):
    """Factura leída → JSON de carga (el formato de compras/facturas/*.json)."""
    marca_final = (marca or factura.get('marca') or '').strip().upper()
    perfil = perfil_para(marca_final)
    # Descuento aplicado al total de la factura: se reparte a prorrata en el
    # costo de cada línea (el sistema registra el costo neto real).
    bruto_total = sum(Decimal(l['importe']) for l in factura.get('lineas', []) if l.get('importe'))
    g_pct, g_monto = factura.get('descuento_global_pct') or 0, factura.get('descuento_global_monto') or 0
    if g_pct:
        factor_global = 1 - Decimal(str(g_pct)) / 100
    elif g_monto and bruto_total:
        factor_global = (bruto_total - Decimal(g_monto)) / bruto_total
    else:
        factor_global = Decimal(1)
    lineas = []
    for l in factura.get('lineas', []):
        tallas = _tallas_dict(l)
        cantidad = l.get('cantidad') or sum(tallas.values())
        lista, importe = l.get('precio_unitario'), l.get('importe')
        pct, monto = l.get('descuento_pct') or 0, l.get('descuento_monto') or 0
        if (pct or monto) and importe and cantidad:
            # Descuento por línea: el importe impreso ya es neto.
            costo, importe_neto = _redondear(Decimal(importe) / cantidad), importe
            descuento = f'{pct:g}%' if pct else f'${monto}'
        elif factor_global != 1 and lista is not None:
            costo = _redondear(Decimal(lista) * factor_global)
            importe_neto = _redondear(Decimal(importe) * factor_global) if importe is not None else None
            descuento = f'{g_pct:g}% global' if g_pct else f'${g_monto} global'
        else:
            costo, importe_neto, descuento = lista, importe, ''
        linea = {
            'articulo': l['articulo'].strip().upper(),
            'descripcion': l['descripcion'].strip(),
            'genero': l.get('genero') or 'UNISEX',
            'categoria': l.get('categoria'),
            'especialidades': l.get('especialidades') or [],
            'costo': costo,
            'cantidad': l.get('cantidad'),
            'importe': importe_neto,
            'precioventa': l.get('precio_venta_a_mano'),
            'tallas': tallas,
        }
        if descuento and lista is not None:
            linea['precio_lista'] = lista
            linea['descuento'] = descuento
        if l.get('color'):
            linea['color'] = str(l['color']).strip().upper()
        if l.get('marca') and l['marca'].strip().upper() != marca_final:
            linea['marca'] = l['marca'].strip().upper()
        if l.get('precio_venta_a_mano') and l.get('precio_venta_a_mano_alternativa'):
            linea['_precio_duda'] = (f'{l["precio_venta_a_mano"]} (otra lectura: '
                                     f'{l["precio_venta_a_mano_alternativa"]})')
        if l.get('reparto_a_mano'):
            linea['_reparto'] = {r['tienda']: r['cantidad'] for r in l['reparto_a_mano']}
        notas = [d for d in [l.get('dudas')] if d] + list(l.get('_revisar') or [])
        if notas or l.get('confianza') != 'alta':
            linea['_revisar'] = f'confianza {l.get("confianza")}' + (': ' + ' · '.join(notas) if notas else '')
        lineas.append(linea)
    con_mano = any(l['precioventa'] for l in lineas)
    return {
        '_fuente': fuente or f'Leída con {MODELO}',
        '_revisar': list(factura.get('_revisar') or []) + revisar_cuadre(factura),
        'proveedor_rut': factura['proveedor_rut'],
        'proveedor_nombre': factura.get('proveedor_nombre'),
        'folio': factura['folio'],
        'fecha_emision': factura['fecha_emision'],
        'sucursal': sucursal,
        **({'fuente_precioventa': 'precio escrito a mano en la factura'} if con_mano else {}),
        'marca': marca_final,
        'color': perfil.color_defecto,
        'total_unidades': factura.get('total_unidades'),
        'total_neto': factura.get('total_neto'),
        **({'descuento_global': f'{g_pct:g}%' if g_pct else f'${g_monto}'} if (g_pct or g_monto) else {}),
        'lineas': lineas,
    }
