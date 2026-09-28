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
  - Cuadre en código (tallas = cantidad, cantidad × precio = importe, suma =
    total neto) y, si algo no cuadra o el lector dejó dudas, una segunda
    mirada independiente SOLO a esas líneas (las páginas ya cacheadas, así
    que cuesta una fracción de releer todo). Con `lecturas=3` se hacen dos
    lecturas completas y se comparan.

Nada de esto escribe en la base: el resultado se revisa en la vista previa de
cargar_productos_factura (o de la pantalla) antes de cargar.

Dónde se va el dinero (medido en prod, sesión #8 del 28-09-2026, 9 líneas):
cada vuelta de zoom es una petición completa (prefijo cacheado + imagen nueva
+ razonamiento) y la segunda lectura repetía todo sin caché. Por eso: (1) las
páginas van también en cuadrantes a resolución nativa (la API reduce toda
imagen a ~1,15 MP: una página entera queda a menos de la mitad de su
resolución y obliga a ampliar); (2) instrucciones y páginas llevan
cache_control explícito y la verificación las reutiliza; (3) el cuadre lo
hace Python, no el modelo; (4) las rotaciones vienen del /Rotate del PDF
cuando existe.

Configuración (variables de entorno):
  ANTHROPIC_API_KEY (settings) · CARGA_FACTURA_MODELO (claude-opus-5) ·
  CARGA_FACTURA_MODELO_RAPIDO (claude-sonnet-5, enderezar) ·
  CARGA_FACTURA_ESFUERZO (high) · CARGA_FACTURA_ESFUERZO_VERIFICACION (medium) ·
  CARGA_FACTURA_CUADRANTES (1; 0 = mandar solo la página entera, como antes).
"""
import base64
import io
import json
import logging
import os
import re
import threading
import zlib
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from app.models import AtributoOpcion, Categoria
from app.utils_anthropic import explicar_error_anthropic, opciones_cliente_anthropic

from .facturas import ErrorCarga
from .perfiles import perfil_para

logger = logging.getLogger('app')

MODELO = os.environ.get('CARGA_FACTURA_MODELO', 'claude-opus-5')
# Tareas triviales (¿cuántos grados está girada la página?) con un modelo más
# barato; la lectura propiamente tal sigue con MODELO.
MODELO_RAPIDO = os.environ.get('CARGA_FACTURA_MODELO_RAPIDO', 'claude-sonnet-5')
# Esfuerzo de razonamiento de la lectura y de la verificación dirigida.
ESFUERZO = os.environ.get('CARGA_FACTURA_ESFUERZO', 'high')
ESFUERZO_VERIFICACION = os.environ.get('CARGA_FACTURA_ESFUERZO_VERIFICACION', 'medium')
# Página escaneada: además de la vista completa se mandan 4 cuadrantes a
# resolución nativa (ver _bloques_pagina). 0 = solo la página entera.
CUADRANTES = os.environ.get('CARGA_FACTURA_CUADRANTES', '1').strip().lower() not in ('0', 'false', 'no', '')

# Precio de lista (USD por millón de tokens): entrada, salida, caché leída,
# caché escrita. Solo para mostrar cuánto costó cada paso; la cuenta real la
# lleva Anthropic. Se busca por prefijo del nombre del modelo.
PRECIOS_USD_POR_MILLON = {
    'claude-opus-5': (5.0, 25.0, 0.5, 6.25),
    'claude-opus-4': (5.0, 25.0, 0.5, 6.25),
    'claude-sonnet-5': (2.0, 10.0, 0.2, 2.5),
    'claude-sonnet-4': (3.0, 15.0, 0.3, 3.75),
    'claude-haiku-4': (1.0, 5.0, 0.1, 1.25),
}
PRECIO_BUSQUEDA_USD = 0.01     # web_search: US$10 por cada 1.000 búsquedas

# --- Contador de uso (tokens, búsquedas y costo estimado) por hilo: quien
# orquesta llama a uso_iniciar() antes y uso_actual() después; _pedir suma
# cada respuesta.
_USO = threading.local()
_CAMPOS_USO = ('llamadas', 'entrada', 'salida', 'cache_leida', 'cache_escrita', 'busquedas', 'costo_usd')


def uso_iniciar():
    _USO.datos = {k: 0 for k in _CAMPOS_USO}


def uso_actual():
    datos = dict(getattr(_USO, 'datos', None) or {})
    if 'costo_usd' in datos:
        datos['costo_usd'] = round(float(datos['costo_usd']), 4)
    return datos


def _precios(modelo):
    """(entrada, salida, caché leída, caché escrita) del modelo, por prefijo más largo."""
    nombre = str(modelo or '')
    mejor = ''
    for prefijo in PRECIOS_USD_POR_MILLON:
        if nombre.startswith(prefijo) and len(prefijo) > len(mejor):
            mejor = prefijo
    return PRECIOS_USD_POR_MILLON[mejor or 'claude-opus-5']


def costo_estimado(modelo, entrada=0, salida=0, cache_leida=0, cache_escrita=0, busquedas=0):
    """US$ de una respuesta según el precio de lista del modelo."""
    p_in, p_out, p_cr, p_cw = _precios(modelo)
    return ((entrada * p_in + salida * p_out + cache_leida * p_cr + cache_escrita * p_cw) / 1e6
            + busquedas * PRECIO_BUSQUEDA_USD)


def _registrar_uso(respuesta):
    datos = getattr(_USO, 'datos', None)
    u = getattr(respuesta, 'usage', None)
    if datos is None or u is None:
        return
    entrada = int(getattr(u, 'input_tokens', 0) or 0)
    salida = int(getattr(u, 'output_tokens', 0) or 0)
    leida = int(getattr(u, 'cache_read_input_tokens', 0) or 0)
    escrita = int(getattr(u, 'cache_creation_input_tokens', 0) or 0)
    servidor = getattr(u, 'server_tool_use', None)
    busquedas = int(getattr(servidor, 'web_search_requests', 0) or 0) if servidor else 0
    datos['llamadas'] += 1
    datos['entrada'] += entrada
    datos['salida'] += salida
    datos['cache_leida'] += leida
    datos['cache_escrita'] += escrita
    datos['busquedas'] += busquedas
    datos['costo_usd'] = float(datos.get('costo_usd') or 0) + costo_estimado(
        getattr(respuesta, 'model', None) or MODELO, entrada, salida, leida, escrita, busquedas)
_BETA_FALLBACK = 'server-side-fallback-2026-07-01'
# La API reduce toda imagen a ≤ 1568 px de lado mayor y ≈ 1,15 megapíxeles;
# mandar más grande solo cuesta ancho de banda. Una página escaneada (2200 ×
# 2900) queda así a menos de la mitad de su resolución: por eso los cuadrantes.
_LADO_MAX = 1568
_PIXELES_MAX = 1_150_000
_LADO_VISTA = 1000          # vista completa de la página cuando van cuadrantes (para ubicarse)
_SOLAPE = 0.08              # fracción de solape entre cuadrantes (que ninguna fila quede cortada)
_LADO_ZOOM = 1568           # lado mayor de un recorte ampliado
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


def _rotaciones_pdf(pdf):
    """/Rotate de cada objeto página (grados horarios), en el orden del archivo.

    Si el escáner dejó la imagen acostada pero marcó /Rotate 90, el visor la
    muestra derecha: con ese dato no hace falta preguntarle al modelo. Sin
    /Rotate (o en 0) no se sabe nada y se pregunta igual que antes."""
    giros = []
    for m in re.finditer(rb'/Type\s*/Page(?![a-zA-Z])', pdf):
        ini = pdf.rfind(b'obj', max(0, m.start() - 4000), m.start())
        fin = pdf.find(b'endobj', m.end())
        if ini < 0 or fin < 0:
            giros.append(None)
            continue
        r = re.search(rb'/Rotate\s+(-?\d+)', pdf[ini:fin])
        giros.append(int(r.group(1)) % 360 if r else None)
    return giros


def _ajustar_para_api(img):
    """Copia escalada a lo que la API muestra de verdad (lado ≤ 1568 px y
    ≈ 1,15 MP); más grande no aporta y la API lo reduce igual."""
    from PIL import Image

    ancho, alto = img.size
    factor = min(1.0, _LADO_MAX / max(ancho, alto), (_PIXELES_MAX / float(ancho * alto)) ** 0.5)
    if factor >= 1.0:
        return img
    return img.resize((max(1, int(ancho * factor)), max(1, int(alto * factor))), Image.LANCZOS)


def _jpeg_b64(img, lado_max=None):
    from PIL import Image

    img = img.copy()
    if lado_max:
        img.thumbnail((lado_max, lado_max), Image.LANCZOS)
    img = _ajustar_para_api(img)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


def _bloque_imagen(img, lado_max=None):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                        'data': _jpeg_b64(img, lado_max)}}


def _cuadrantes(img, solape=_SOLAPE):
    """[(nombre, recorte), ...]: 4 cuadrantes con una franja de solape, para
    que ninguna fila de la tabla quede partida entre arriba y abajo."""
    ancho, alto = img.size
    dx, dy = int(ancho * solape / 2), int(alto * solape / 2)
    mx, my = ancho // 2, alto // 2
    cajas = (
        ('superior izquierdo', (0, 0, mx + dx, my + dy)),
        ('superior derecho', (mx - dx, 0, ancho, my + dy)),
        ('inferior izquierdo', (0, my - dy, mx + dx, alto)),
        ('inferior derecho', (mx - dx, my - dy, ancho, alto)),
    )
    return [(nombre, img.crop(caja)) for nombre, caja in cajas]


def _bloques_pagina(n, img, cuadrantes=None):
    """Bloques (texto + imagen) con que se manda una página escaneada.

    Con cuadrantes: la página entera chica (para ubicar las zonas) y los 4
    cuadrantes casi a resolución nativa (un escaneo de 2209 × 2878 llega
    entero a 0,43× y por cuadrantes a ≈ 0,8×), así los dígitos de la grilla
    se leen sin pedir ampliaciones (cada ampliación es una vuelta completa a
    la API). Una página que ya cabe entera en lo que muestra la API va sola."""
    if cuadrantes is None:
        cuadrantes = CUADRANTES
    if not cuadrantes or img.width * img.height <= _PIXELES_MAX:
        return [{'type': 'text', 'text': f'Página {n}:'}, _bloque_imagen(img)]
    bloques = [{'type': 'text', 'text': f'Página {n} (vista completa, para ubicar las zonas):'},
               _bloque_imagen(img, _LADO_VISTA)]
    for nombre, recorte in _cuadrantes(img):
        bloques += [{'type': 'text', 'text': f'Página {n}, cuadrante {nombre} (resolución completa):'},
                    _bloque_imagen(recorte)]
    return bloques


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


CACHE = {'type': 'ephemeral'}


def _pedir(cliente, modelo=None, cachear=True, **kwargs):
    """Una respuesta de Claude (streaming, con respaldo de modelo si la rechaza).

    `cachear`: cache_control a nivel de petición (la API marca el último
    bloque): sirve cuando la conversación CRECE y la próxima petición repite
    esta entera (las vueltas de zoom). Para una petición suelta (enderezar,
    búsqueda) o cuando el final cambia en cada turno (chat) solo paga el
    recargo de escritura sin que nadie lo lea: ahí va False y los bloques
    estables llevan su propio cache_control (ver _una_lectura y chat)."""
    import anthropic

    if cachear:
        kwargs['cache_control'] = CACHE
    try:
        with cliente.beta.messages.stream(
            model=modelo or MODELO,
            betas=[_BETA_FALLBACK],
            extra_body={'fallbacks': 'default'},
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
    _registrar_uso(respuesta)
    if respuesta.stop_reason == 'refusal':
        detalle = getattr(respuesta, 'stop_details', None)
        raise ErrorLectura(f'Claude no quiso leer el documento ({getattr(detalle, "category", "")}).')
    if respuesta.stop_reason == 'max_tokens':
        raise ErrorLectura('La respuesta se cortó por largo; divide el PDF y vuelve a intentar.')
    return respuesta


def _texto(respuesta):
    """El ÚLTIMO bloque de texto: con herramientas del servidor (búsqueda web)
    puede haber texto intermedio y el JSON final va al cierre."""
    textos = [b.text for b in respuesta.content if getattr(b, 'type', '') == 'text']
    return textos[-1] if textos else ''


_ESQUEMA_ROTACION = {
    'type': 'object',
    'properties': {'giro_horario': {'type': 'integer', 'enum': [0, 90, 180, 270]}},
    'required': ['giro_horario'],
    'additionalProperties': False,
}


def _enderezar(cliente, img):
    """Imagen derecha: Claude dice cuántos grados girarla en sentido horario."""
    respuesta = _pedir(
        cliente, modelo=MODELO_RAPIDO, max_tokens=4000, cachear=False, output_config={
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


# La API compila el esquema a una gramática con dos techos: 16 campos con unión
# de tipos (anyOf / null) y un tamaño total. Por eso no hay anyOf: lo que no
# viene se marca con un centinela (-1 en números, "" en textos) y las listas
# grandes del sistema (colores) no van como enum: Claude las ve en el pedido y
# el valor se valida acá contra el catálogo (_limpiar_lectura). Menos esquema
# = menos tokens por vuelta de zoom.
# Los campos que casi nunca traen dato (descuentos, reparto a mano, marca de
# la línea, dudas…) son OPCIONALES (fuera de `required`, tope 24): así el
# modelo no escribe 60 tokens de centinelas por línea. _limpiar_lectura los
# rellena con None cuando faltan.
SIN_DATO_NUM = -1
_ENUM_MAXIMO = 80
_NUM_LINEA = ('cantidad', 'precio_unitario', 'descuento_pct', 'descuento_monto', 'importe',
              'precio_venta_a_mano', 'precio_venta_a_mano_alternativa')
_NUM_FACTURA = ('total_unidades', 'descuento_global_pct', 'descuento_global_monto', 'total_neto')
_OPCIONALES_LINEA = ('descuento_pct', 'descuento_monto', 'precio_venta_a_mano_alternativa',
                     'reparto_a_mano', 'marca', 'dudas')
_OPCIONALES_FACTURA = ('descuento_global_pct', 'descuento_global_monto', 'observaciones')


def _num(tipo):
    return {'type': tipo, 'description': f'{SIN_DATO_NUM} = no viene impreso / no se lee'}


def _txt(descripcion='"" = no viene / no se sabe'):
    return {'type': 'string', 'description': descripcion}


def _enum_o_texto(valores, descripcion):
    valores = [v for v in valores if v]
    if valores and len(valores) <= _ENUM_MAXIMO:
        return {'type': 'string', 'enum': [''] + list(valores), 'description': '"" = no se sabe'}
    return _txt(descripcion)


def _esquema(categorias, especialidades, colores=()):
    linea = {
        'type': 'object',
        'properties': {
            'articulo': {'type': 'string'},
            'descripcion': {'type': 'string'},
            'color': _txt('color de la línea: valor EXACTO de la lista de colores del sistema si calza; '
                          'si el color impreso no está en la lista, tal cual está impreso; "" si no se sabe'),
            'tallas': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'talla': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['talla', 'cantidad'], 'additionalProperties': False}},
            'cantidad': _num('integer'),
            'precio_unitario': _num('integer'),
            'descuento_pct': _num('number'),
            'descuento_monto': _num('integer'),
            'importe': _num('integer'),
            'precio_venta_a_mano': _num('integer'),
            'precio_venta_a_mano_alternativa': _num('integer'),
            'reparto_a_mano': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'tienda': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['tienda', 'cantidad'], 'additionalProperties': False}},
            'marca': _txt('"" si es la misma marca de la factura'),
            'genero': {'type': 'string', 'enum': [''] + list(GENEROS), 'description': '"" = no se sabe'},
            'categoria': _enum_o_texto(categorias, '"" o valor EXACTO de la lista de categorías'),
            'especialidades': {'type': 'array', 'items': {
                'type': 'string', 'description': 'valor EXACTO de la lista de especialidades'}},
            'confianza': {'type': 'string', 'enum': ['alta', 'media', 'baja']},
            'dudas': {'type': 'string'},
        },
        'additionalProperties': False,
    }
    linea['required'] = [k for k in linea['properties'] if k not in _OPCIONALES_LINEA]
    factura = {
        'type': 'object',
        'properties': {
            'tipo_documento': {'type': 'string'},
            'folio': {'type': 'integer'},
            'proveedor_nombre': {'type': 'string'},
            'proveedor_rut': {'type': 'string'},
            'fecha_emision': {'type': 'string', 'description': 'AAAA-MM-DD'},
            'marca': _txt('marca de la mercadería; "" si no se sabe'),
            'total_unidades': _num('integer'),
            'descuento_global_pct': _num('number'),
            'descuento_global_monto': _num('integer'),
            'total_neto': _num('integer'),
            'paginas': {'type': 'array', 'items': {'type': 'integer'}},
            'lineas': {'type': 'array', 'items': linea},
            'observaciones': {'type': 'string'},
        },
        'additionalProperties': False,
    }
    factura['required'] = [k for k in factura['properties'] if k not in _OPCIONALES_FACTURA]
    return {'type': 'object',
            'properties': {'facturas': {'type': 'array', 'items': factura}},
            'required': ['facturas'], 'additionalProperties': False}


def _esquema_verificacion(esquema):
    """Salida de la segunda mirada: solo folio + las líneas pedidas (mismo
    formato de línea que la lectura completa)."""
    linea = esquema['properties']['facturas']['items']['properties']['lineas']['items']
    factura = {
        'type': 'object',
        'properties': {'folio': {'type': 'integer'}, 'lineas': {'type': 'array', 'items': linea}},
        'required': ['folio', 'lineas'], 'additionalProperties': False,
    }
    return {'type': 'object',
            'properties': {'facturas': {'type': 'array', 'items': factura}},
            'required': ['facturas'], 'additionalProperties': False}


def _normalizar(valor, lista):
    """El valor del catálogo que coincide (sin mayúsculas ni espacios de más) o None."""
    clave = ' '.join(str(valor or '').split()).upper()
    if not clave:
        return None
    for v in lista or []:
        if ' '.join(str(v).split()).upper() == clave:
            return v
    return None


def _limpiar_lectura(lectura, categorias=(), especialidades=(), colores=()):
    """Centinelas → None y valores de lista validados contra el catálogo, para
    que el resto del módulo (cuadre, JSON de carga) vea lo de siempre."""
    def sin_dato(v):
        return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0)

    for f in lectura.get('facturas') or []:
        for k in _NUM_FACTURA:
            if sin_dato(f.get(k)):
                f[k] = None
        f['marca'] = str(f.get('marca') or '').strip() or None
        for l in f.get('lineas') or []:
            for k in _NUM_LINEA:
                if sin_dato(l.get(k)):
                    l[k] = None
            for k in ('marca', 'color', 'genero', 'categoria'):
                l[k] = str(l.get(k) or '').strip() or None
            if l.get('color') and colores:
                # Del catálogo si calza; si no, se deja tal cual (la vista previa avisa).
                l['color'] = _normalizar(l['color'], colores) or l['color']
            if l.get('categoria'):
                l['categoria'] = _normalizar(l['categoria'], categorias) if categorias else l['categoria']
            if l.get('genero'):
                l['genero'] = _normalizar(l['genero'], GENEROS)
            if especialidades:
                l['especialidades'] = [n for n in (_normalizar(e, especialidades)
                                                   for e in l.get('especialidades') or []) if n]
    return lectura


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


def _instrucciones(perfil, marca_hint, categorias, especialidades, pistas='', colores=(),
                   cuadrantes=None):
    from app.management.commands._data_recategorizacion_v12 import ESPECIALIDADES

    if cuadrantes is None:
        cuadrantes = CUADRANTES
    leyenda = '; '.join(f'{slug} = {ESPECIALIDADES[slug][1]}' for slug in especialidades
                        if slug in ESPECIALIDADES)
    indicaciones = (f'Indicaciones de la persona que sube la factura (mandan sobre lo demás): '
                    f'{pistas.strip()}' if pistas and pistas.strip() else '')
    como_mirar = (
        '- Cada página escaneada va entera (chica, para ubicar las zonas) y en 4 cuadrantes a\n'
        '  resolución completa: lee los datos en los cuadrantes. Las filas de la tabla siguen del\n'
        '  cuadrante izquierdo al derecho a la misma altura, y la franja de solape entre cuadrantes\n'
        '  aparece repetida (no dupliques esas filas).\n'
        '- Amplía con la herramienta "ampliar" solo lo que aun así no se lea con total seguridad\n'
        '  (lo escrito a mano, sellos, dígitos borrosos). No amplíes lo que ya se lee bien.'
        if cuadrantes else
        '- Amplía con la herramienta "ampliar" lo que no se lea con total seguridad: la grilla de\n'
        '  tallas (por bloques de varias líneas, no una por una), precios, totales y lo escrito a\n'
        '  mano. No adivines dígitos, pero tampoco amplíes lo que ya se lee bien.')
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

Datos que no vienen: {SIN_DATO_NUM} en los números y "" en los textos. Los campos opcionales
(descuentos, precio a mano alternativo, reparto a tiendas, marca de la línea, dudas,
observaciones) se incluyen SOLO cuando hay dato. Nunca inventes un valor.

Cómo leer bien:
{como_mirar}
- No hagas cuentas para cuadrar: el sistema comprueba que las tallas sumen la cantidad, que
  cantidad × precio_unitario (menos el descuento de la línea) dé el importe y que los importes
  (menos el descuento global) sumen el total neto, y si algo no calza te devuelve esa línea
  para que la mires de nuevo. Lo que sí debes mirar es si la tabla trae una columna de
  descuento (en % o en pesos) o el total un descuento global: es lo que más se pasa por alto.
  Nunca ajustes un número para que cuadre; transcribe lo impreso y anota la duda.

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


def _una_lectura(cliente, contenido, paginas, instrucciones, esquema, orden,
                 etiqueta='Leyendo el documento', progreso=None, pedido=None, esfuerzo=None):
    """Una lectura (con su propio bucle de zoom). Devuelve el dict del esquema.

    Las instrucciones van como `system` con cache_control y `contenido` (las
    páginas) trae su propio cache_control en el último bloque: la segunda
    lectura o la verificación dirigida reutilizan ese prefijo desde la caché
    y solo pagan el texto del pedido. `pedido` reemplaza el enfoque por
    defecto (p.ej. «vuelve a mirar solo estas líneas»). `progreso(texto)`
    (opcional) recibe en qué va: la pantalla lo muestra mientras espera."""
    if pedido:
        enfoque = pedido
    elif orden == 1:
        enfoque = 'Lee la tabla completa, línea por línea, y devuelve el JSON.'
    else:
        enfoque = ('Esta es una segunda lectura independiente: recorre la tabla línea por línea '
                   'y columna por columna, ampliando cada grilla de tallas que no se lea con claridad.')
    sistema = [{'type': 'text', 'text': instrucciones, 'cache_control': CACHE}]
    mensajes = [{'role': 'user', 'content': contenido + [{'type': 'text', 'text': enfoque}]}]
    herramientas = [_HERRAMIENTA_ZOOM] if paginas else []
    for turno in range(_MAX_TURNOS):
        if progreso:
            progreso(f'{etiqueta}…' if turno == 0 else f'{etiqueta}: ampliando detalles (vuelta {turno})…')
        respuesta = _pedir(cliente, max_tokens=64000, system=sistema, tools=herramientas,
                           messages=mensajes,
                           output_config={'effort': esfuerzo or ESFUERZO,
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


def _lineas_dudosas(lectura):
    """[(folio, articulo, motivo), ...]: lo que merece una segunda mirada tras
    la primera lectura (ya limpia): no cuadra, confianza media/baja, dudas
    anotadas, o precios / repartos escritos a mano (lo más fácil de leer mal).
    Si los totales de la factura no calzan y ninguna línea explica por qué, se
    piden todas las líneas de esa factura."""
    dudosas = []
    for f in lectura.get('facturas') or []:
        por_articulo, de_factura = {}, []
        for problema in revisar_cuadre(f):
            clave, sep, resto = problema.partition(': ')
            if sep and clave not in ('unidades', 'neto'):
                por_articulo.setdefault(clave, []).append(resto)
            else:
                de_factura.append(problema)
        lineas = f.get('lineas') or []
        marcadas = []
        for l in lineas:
            motivos = list(por_articulo.get(l.get('articulo'), []))
            if l.get('confianza') != 'alta':
                motivos.append(f'confianza {l.get("confianza") or "baja"}')
            if str(l.get('dudas') or '').strip():
                motivos.append(str(l['dudas']).strip())
            if l.get('precio_venta_a_mano') is not None:
                motivos.append('precio de venta escrito a mano: confirma cada dígito')
            if l.get('reparto_a_mano'):
                motivos.append('reparto a tiendas escrito a mano')
            if motivos:
                marcadas.append((f.get('folio'), l.get('articulo'), '; '.join(motivos)))
        if de_factura and not marcadas:
            motivo = 'los totales no calzan (' + '; '.join(de_factura) + '): revisa cantidad, precio e importe'
            marcadas = [(f.get('folio'), l.get('articulo'), motivo) for l in lineas]
        dudosas += marcadas
    return dudosas


def _con_dudas(lectura):
    """True si la primera lectura deja algo que merece una segunda pasada."""
    return bool(_lineas_dudosas(lectura))


def _pedido_verificacion(dudosas):
    lineas = '\n'.join(f'- Factura {folio}, línea {articulo}: {motivo}'
                       for folio, articulo, motivo in dudosas)
    return (f'Segunda mirada, independiente de cualquier lectura anterior, SOLO a estas '
            f'{len(dudosas)} línea(s); el resto de la factura ya está bien leído y no lo '
            f'devuelvas. Ubica cada una, amplía lo que haga falta y transcríbela completa de '
            f'nuevo (código, descripción, todas las tallas con su cantidad, cantidad, precio, '
            f'descuento si lo hay, importe y lo escrito a mano). Devuelve únicamente esas líneas, '
            f'agrupadas por folio, con el código tal como está impreso:\n{lineas}')


def _verificar_lineas(cliente, contenido, paginas, instrucciones, esquema, dudosas, progreso=None):
    """Segunda mirada dirigida: misma conversación base (instrucciones y páginas
    desde la caché), solo las líneas con dudas y un esquema chico. Devuelve un
    dict de lectura PARCIAL (marcado con _parcial) para combinar_lecturas."""
    otra = _una_lectura(cliente, contenido, paginas, instrucciones, _esquema_verificacion(esquema), 2,
                        etiqueta=f'Verificando {len(dudosas)} línea(s) con dudas', progreso=progreso,
                        pedido=_pedido_verificacion(dudosas), esfuerzo=ESFUERZO_VERIFICACION)
    otra['_parcial'] = True
    return otra


def leer_pdf(pdf_bytes, marca=None, lecturas=2, progreso=None, pistas=''):
    """Lee el PDF. Devuelve {'lecturas': [dict, ...], 'modo': 'escaneo'|'pdf',
    'segunda': por qué hubo (o no) segunda pasada, 'verificadas': n líneas}.

    `lecturas`: 1 = una sola; 2 = lectura + verificación dirigida de las líneas
    que dejan dudas (default); 3 = dos lecturas completas que se comparan.
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
        rotaciones = _rotaciones_pdf(pdf_bytes)
        if len(rotaciones) != len(imagenes):
            rotaciones = [None] * len(imagenes)
        paginas = []
        for n, (img, giro) in enumerate(zip(imagenes, rotaciones), start=1):
            if giro:
                # El PDF ya dice cómo se muestra derecha: no hay que preguntar.
                paginas.append(img.rotate(-giro, expand=True))
                continue
            avisar(f'Enderezando la página {n} de {len(imagenes)}…')
            paginas.append(_enderezar(cliente, img))
        contenido = []
        for n, img in enumerate(paginas, start=1):
            contenido += _bloques_pagina(n, img)
        modo = 'escaneo'
    else:
        if len(pdf_bytes) > 30 * 1024 * 1024:
            raise ErrorLectura('El PDF pesa más de 30 MB; envíalo en partes.')
        paginas = []
        contenido = [{'type': 'document', 'source': {
            'type': 'base64', 'media_type': 'application/pdf',
            'data': base64.standard_b64encode(pdf_bytes).decode('ascii')}}]
        modo = 'pdf'
    # Las páginas (lo caro) se cachean hasta el último bloque: la segunda
    # pasada las lee desde la caché y solo paga el texto de su pedido.
    contenido[-1]['cache_control'] = CACHE

    modo_lecturas = max(1, min(3, int(lecturas or 2)))
    primera = _una_lectura(cliente, contenido, paginas, instrucciones, esquema, 1,
                           etiqueta='Leyendo el documento', progreso=progreso)
    resultado = [_limpiar_lectura(primera, categorias, especialidades, colores)]
    segunda, verificadas = 'no pedida', 0
    if modo_lecturas == 3:
        otra = _una_lectura(cliente, contenido, paginas, instrucciones, esquema, 2,
                            etiqueta='Segunda lectura completa', progreso=progreso)
        resultado.append(_limpiar_lectura(otra, categorias, especialidades, colores))
        segunda = 'siempre'
    elif modo_lecturas == 2:
        dudosas = _lineas_dudosas(resultado[0])
        if dudosas:
            otra = _verificar_lineas(cliente, contenido, paginas, instrucciones, esquema, dudosas,
                                     progreso=progreso)
            resultado.append(_limpiar_lectura(otra, categorias, especialidades, colores))
            segunda, verificadas = 'verificación', len(dudosas)
        else:
            segunda = 'no hizo falta'
    return {'lecturas': resultado, 'modo': modo, 'segunda': segunda, 'verificadas': verificadas}


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

    Una lectura marcada `_parcial` (verificación dirigida) solo trae las
    líneas pedidas: lo que no trae no cuenta como «no lo vio».
    """
    base = json.loads(json.dumps(lecturas[0]))
    for otra in lecturas[1:]:
        parcial = bool(otra.get('_parcial'))
        por_folio = {f['folio']: f for f in otra.get('facturas', [])}
        for factura in base.get('facturas', []):
            gemela = por_folio.get(factura['folio'])
            if gemela is None:
                if not parcial:
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
                    if not parcial:
                        linea.setdefault('_revisar', []).append('la otra lectura no vio esta línea')
                    continue
                diferencias = []
                for campo in ('cantidad', 'precio_unitario', 'descuento_pct', 'importe',
                              'precio_venta_a_mano', 'color'):
                    if linea.get(campo) != par.get(campo):
                        diferencias.append(f'{campo}: {linea.get(campo)} / {par.get(campo)}')
                if _tallas_dict(linea) != _tallas_dict(par):
                    diferencias.append(f'tallas: {_tallas_dict(linea)} / {_tallas_dict(par)}')
                if not diferencias and parcial and par.get('confianza') == 'alta':
                    # La segunda mirada, independiente, leyó lo mismo con
                    # seguridad: la duda de la primera queda resuelta.
                    linea['confianza'] = 'alta'
                    linea['dudas'] = ''
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
                        ('la verificación no coincide (' if parcial else 'las lecturas no coinciden (')
                        + '; '.join(diferencias) + ')')
            if parcial:
                continue
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
