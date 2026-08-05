"""
Parser de XML DTE del SII — documentos que RECIBIMOS (facturas de proveedor).

Contexto
--------
Este módulo es la puerta de ENTRADA. No tiene nada que ver con la EMISIÓN:
esa sigue haciéndose con el TXT de Acepta (`views_modulo_documentos.
generar_txt_*`), que funciona y no se toca.

El objetivo es leer el `EnvioDTE` que manda el proveedor y devolver el
**mismo shape** que `views_modulo_documentos.parsear_txt_acepta`, de modo que
todo lo de aguas abajo (validaciones, cuadratura, pantallas) se reutilice:

    {documento, emisor, receptor, transporte, totales,
     detalle[], descuentos_recargos[], referencias[], observaciones}

Sobre el shape del TXT se agregan claves que el TXT no lleva y el XML sí:

    detalle[i]['codigos']    -> [{'tipo': TpoCodigo, 'valor': VlrCodigo}]  (0..5)
    detalle[i]['nro_linea']  -> NroLinDet
    documento['tipo_documento_sistema'] -> string de `TIPO_DOCUMENTO_CHOICES`
    documento['fecha_emision_date']     -> datetime.date (o None)

Estructura del XML (SII)
------------------------
    EnvioDTE / SetDTE / DTE / Documento /
        Encabezado /
            IdDoc    { TipoDTE, Folio, FchEmis, FchVenc, FmaPago, ... }
            Emisor   { RUTEmisor, RznSoc,      GiroEmis,   Acteco, ... }
            Receptor { RUTRecep,  RznSocRecep, GiroRecep,  ... }
            Totales  { MntNeto, MntExe, TasaIVA, IVA, MntTotal }
        Detalle*      (MÁXIMO 60 por documento)
        DscRcgGlobal*
        Referencia*

Namespace único: ``http://www.sii.cl/SiiDte``.

TRAMPAS que este parser cubre (todas vistas en XMLs reales)
-----------------------------------------------------------
1. **Encoding ISO-8859-1.** El XML declara ``encoding="ISO-8859-1"``. Hay que
   entregar **BYTES** al parser: si se le pasa un `str` ya decodificado, tanto
   lxml como `xml.etree` revientan con *"Unicode strings with encoding
   declaration are not supported"*. Por eso los archivos se leen en ``'rb'``.
   Si igualmente llega un `str`, aquí se le quita la declaración y se re-encodea
   a UTF-8 antes de parsear (tolerante, pero se loguea).
2. **BOM.** UTF-8 BOM delante de una declaración ISO-8859-1 hace fallar a expat.
   Se elimina. También se soporta BOM UTF-16.
3. **Basura antes del prólogo** (líneas en blanco, cabeceras de correo, etc.):
   se descarta todo lo anterior al primer ``<``.
4. **Namespace faltante o distinto.** Se le quita el namespace a TODO el árbol
   y se trabaja por nombre local, así da igual si viene con `SiiDte`, con otro
   URI o sin namespace.
5. **Sobres de intercambio** que envuelven el `EnvioDTE` (SOAP, `RespuestaDTE`,
   sobres propios del proveedor): no se asume la ruta, se BUSCA cualquier nodo
   que tenga `Encabezado/IdDoc` a cualquier profundidad.

DECISIONES IMPORTANTES
----------------------
* ``QtyItem`` y ``PrcItem`` son **OPCIONALES** en el XSD del SII. Un XML
  perfectamente válido puede traer solo ``NmbItem`` + ``MontoItem``. Por eso el
  import NO puede ser ciego: las líneas sin cantidad quedan marcadas
  (`validar_dte_parseado` → `lineas_sin_cantidad`) y la pantalla de conciliación
  OBLIGA al usuario a completarlas. Una línea sin cantidad no puede convertirse
  en unidades.
* Todo lo monetario se maneja con ``Decimal``, nunca ``float``.
* ``CdgItem`` NO es confiable: ``TpoCodigo`` es texto libre que el SII no
  valida (cada proveedor pone lo que quiere: "INT1", "EAN", "SKU", "PROV"…).
  Se devuelven TODOS los códigos de la línea para que el motor de matching los
  pruebe uno a uno, y el aprendizaje real lo hace
  `ProveedorProductoEquivalencia`.

Dependencias: se usa ``defusedxml`` si está instalado; si no, el
``xml.etree.ElementTree`` de la stdlib. NO se agrega lxml (las dependencias
nuevas se preguntan primero — regla de CLAUDE.md).
"""
from __future__ import annotations

import codecs
import datetime
import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

logger = logging.getLogger('app')

# defusedxml protege contra billion-laughs / entidades externas. Si no está
# instalado se cae a la stdlib: el archivo lo sube un usuario autenticado con
# permiso de crear DTEs de compra, así que el riesgo es acotado, pero se deja
# el flag expuesto para poder avisarlo en la UI / logs.
try:  # pragma: no cover - depende del entorno
    from defusedxml.ElementTree import fromstring as _fromstring
    DEFUSEDXML_DISPONIBLE = True
except ImportError:  # pragma: no cover - depende del entorno
    from xml.etree.ElementTree import fromstring as _fromstring
    DEFUSEDXML_DISPONIBLE = False


class XmlDteError(Exception):
    """El archivo no es un XML DTE legible."""


# ---------------------------------------------------------------------------
# CONSTANTES DEL FORMATO SII
# ---------------------------------------------------------------------------

#: Tope de líneas de detalle por documento (XSD del SII).
MAX_DETALLE_POR_DOCUMENTO = 60
#: Tope de nodos `CdgItem` por línea.
MAX_CODIGOS_POR_ITEM = 5
#: Largos máximos (XSD). Se usan para avisar, no para bloquear.
MAX_LARGO_NMB_ITEM = 80
MAX_LARGO_TPO_CODIGO = 10
MAX_LARGO_VLR_CODIGO = 35

#: Tamaño máximo razonable de un XML DTE. Un `EnvioDTE` con cientos de
#: documentos rara vez pasa de 2 MB; el tope existe para no comerse la RAM
#: con un archivo cualquiera renombrado a .xml.
MAX_BYTES_ARCHIVO = 8 * 1024 * 1024

#: TipoDTE del SII → `tipo_documento` de `app.models.dte.TIPO_DOCUMENTO_CHOICES`.
TIPO_DTE_SII = {
    33: 'FACTURA ELECTRONICA',
    34: 'FACTURA EXENTA',
    39: 'BOLETA ELECTRONICA',
    41: 'BOLETA ELECTRONICA',   # boleta exenta: el modelo no distingue
    43: 'FACTURA ELECTRONICA',  # liquidación-factura
    46: 'FACTURA ELECTRONICA',  # factura de compra
    52: 'GUIA',
    56: 'NOTA DE DEBITO',
    61: 'NOTA DE CREDITO',
}

#: Tipos cuyo `MntTotal` es IVA-inclusive y NO traen MntNeto.
TIPOS_BOLETA = (39, 41)
#: Tipos que ajustan otro documento.
TIPOS_NOTA = (56, 61)

#: Nodos que pueden contener un documento tributario dentro del sobre.
_TAGS_DOCUMENTO = ('Documento', 'Liquidacion', 'Exportaciones')

_RE_DECL_BYTES = re.compile(rb'^\s*<\?xml[^>]*\?>', re.IGNORECASE)
_RE_DECL_STR = re.compile(r'^\s*<\?xml[^>]*\?>', re.IGNORECASE)


# ---------------------------------------------------------------------------
# HELPERS DE BAJO NIVEL
# ---------------------------------------------------------------------------

def _preparar_bytes(contenido):
    """Deja el contenido listo para expat: bytes, sin BOM y sin basura previa.

    Acepta `bytes`/`bytearray` (lo correcto) y también `str` por tolerancia:
    en ese caso se le quita la declaración de encoding — que es justamente lo
    que hace reventar al parser — y se re-encodea a UTF-8.
    """
    if contenido is None:
        raise XmlDteError('Archivo vacío.')

    if isinstance(contenido, str):
        logger.warning(
            'parsear_xml_dte recibió str en vez de bytes; se quita la '
            'declaración de encoding y se re-encodea a UTF-8. Lo correcto es '
            'abrir el archivo en modo binario ("rb").'
        )
        return _RE_DECL_STR.sub('', contenido, count=1).strip().encode('utf-8')

    datos = bytes(contenido)
    if not datos.strip():
        raise XmlDteError('Archivo vacío.')
    if len(datos) > MAX_BYTES_ARCHIVO:
        raise XmlDteError(
            f'El archivo pesa {len(datos) // 1024} KB y supera el máximo '
            f'admitido ({MAX_BYTES_ARCHIVO // 1024} KB).'
        )

    # BOM UTF-16: hay que convertir sí o sí, expat no lo combina con una
    # declaración ISO-8859-1.
    if datos[:2] in (b'\xff\xfe', b'\xfe\xff'):
        texto = datos.decode('utf-16', errors='replace')
        return _RE_DECL_STR.sub('', texto, count=1).strip().encode('utf-8')

    if datos.startswith(codecs.BOM_UTF8):
        datos = datos[len(codecs.BOM_UTF8):]

    # Basura previa al prólogo (saltos de línea, cabeceras de mail pegadas…).
    inicio = datos.find(b'<')
    if inicio > 0:
        datos = datos[inicio:]

    return datos.strip()


def _parsear_raiz(datos: bytes):
    """Parsea con reintento de recuperación para XML sucio."""
    try:
        return _fromstring(datos)
    except Exception as error_directo:  # ParseError, ValueError, defusedxml…
        # Reintento: iso-8859-1 nunca falla al decodificar, así que sirve de
        # "modo texto" universal. Se elimina la declaración (que ahora mentiría)
        # y se entrega UTF-8 limpio.
        try:
            texto = datos.decode('iso-8859-1')
            limpio = _RE_DECL_STR.sub('', texto, count=1).strip()
            return _fromstring(limpio.encode('utf-8'))
        except Exception:
            raise XmlDteError(
                f'No se pudo leer el XML: {error_directo}'
            ) from error_directo


def _quitar_namespaces(raiz):
    """Elimina el namespace de todo el árbol para trabajar por nombre local."""
    for elemento in raiz.iter():
        tag = elemento.tag
        if isinstance(tag, str) and '}' in tag:
            elemento.tag = tag.split('}', 1)[1]


def _hijo(nodo, *nombres):
    """Primer hijo DIRECTO cuyo tag esté en `nombres`, o None."""
    if nodo is None:
        return None
    for nombre in nombres:
        encontrado = nodo.find(nombre)
        if encontrado is not None:
            return encontrado
    return None


def _texto(nodo, *nombres):
    """Texto del primer hijo directo no vacío entre `nombres`, o None."""
    if nodo is None:
        return None
    for nombre in nombres:
        hijo = nodo.find(nombre)
        if hijo is not None and hijo.text is not None:
            valor = hijo.text.strip()
            if valor:
                return valor
    return None


def _dec(valor):
    """Texto → Decimal. None si no es numérico. NUNCA float."""
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip().replace(' ', '').replace('\xa0', '')
    if not texto:
        return None
    # El SII usa punto decimal, pero hay proveedores que mandan coma.
    if ',' in texto and '.' in texto:
        texto = texto.replace(',', '')       # la coma es separador de miles
    elif ',' in texto:
        texto = texto.replace(',', '.')      # la coma es separador decimal
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _ent(valor):
    """Texto/Decimal → int redondeando medio-arriba. None si no aplica."""
    numero = _dec(valor) if not isinstance(valor, Decimal) else valor
    if numero is None:
        return None
    return int(numero.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _fecha(texto):
    """'YYYY-MM-DD' → datetime.date. Tolera 'DD-MM-YYYY' y 'DD/MM/YYYY'."""
    if not texto:
        return None
    texto = str(texto).strip()[:10]
    for formato in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------

def parsear_xml_envio(contenido_bytes):
    """Parsea un archivo XML y devuelve la LISTA de documentos que contiene.

    Un `EnvioDTE` puede traer varios `DTE` en su `SetDTE`. Cada uno se parsea
    al shape de `parsear_txt_acepta`.

    Args:
        contenido_bytes: contenido crudo del archivo (léelo en modo ``'rb'``).

    Returns:
        list[dict]

    Raises:
        XmlDteError: si el archivo no es XML legible o no trae documentos.
    """
    datos = _preparar_bytes(contenido_bytes)
    raiz = _parsear_raiz(datos)
    _quitar_namespaces(raiz)

    nodos = _localizar_documentos(raiz)
    if not nodos:
        raise XmlDteError(
            'El XML no contiene ningún documento tributario '
            '(no se encontró <Documento> con <Encabezado>/<IdDoc>). '
            '¿Es un XML de DTE del SII?'
        )

    total = len(nodos)
    return [_parsear_documento(nodo, total) for nodo in nodos]


def parsear_xml_dte(contenido_bytes):
    """Parsea un XML DTE y devuelve el PRIMER documento del envío.

    Mismo shape que `views_modulo_documentos.parsear_txt_acepta`:
    ``{documento, emisor, receptor, transporte, totales, detalle,
    descuentos_recargos, referencias, observaciones}``.

    Si el envío trae más de un documento, `documento['documentos_en_envio']`
    lo indica; usa `parsear_xml_envio` para obtenerlos todos.
    """
    return parsear_xml_envio(contenido_bytes)[0]


def _localizar_documentos(raiz):
    """Nodos-documento a cualquier profundidad, tolerando sobres extraños."""
    encontrados = []
    vistos = set()

    for elemento in raiz.iter():
        if elemento.tag not in _TAGS_DOCUMENTO:
            continue
        if _hijo(elemento, 'Encabezado') is None:
            continue
        if id(elemento) in vistos:
            continue
        vistos.add(id(elemento))
        encontrados.append(elemento)

    if encontrados:
        return encontrados

    # Plan B: el sobre usa un nombre de nodo que no conocemos. Buscamos por
    # forma (tiene Encabezado con IdDoc dentro) en vez de por nombre.
    for elemento in raiz.iter():
        encabezado = _hijo(elemento, 'Encabezado')
        if encabezado is None or _hijo(encabezado, 'IdDoc') is None:
            continue
        if id(elemento) in vistos:
            continue
        vistos.add(id(elemento))
        encontrados.append(elemento)

    return encontrados


def _parsear_documento(doc, documentos_en_envio=1):
    """Convierte un nodo <Documento> al dict con shape de `parsear_txt_acepta`."""
    encabezado = _hijo(doc, 'Encabezado') or doc
    id_doc = _hijo(encabezado, 'IdDoc')
    nodo_emisor = _hijo(encabezado, 'Emisor')
    nodo_receptor = _hijo(encabezado, 'Receptor')
    nodo_totales = _hijo(encabezado, 'Totales')
    nodo_transporte = _hijo(encabezado, 'Transporte')

    tipo_dte = _ent(_texto(id_doc, 'TipoDTE'))
    fecha_emision_txt = _texto(id_doc, 'FchEmis')

    resultado = {
        'documento': {
            'tipo_documento': tipo_dte,
            'folio': _ent(_texto(id_doc, 'Folio')),
            'fecha_emision': fecha_emision_txt,
            'fecha_vencimiento': _texto(id_doc, 'FchVenc'),
            'forma_pago': _ent(_texto(id_doc, 'FmaPago')),
            'tipo_despacho': _ent(_texto(id_doc, 'TipoDespacho')),
            'ind_traslado': _ent(_texto(id_doc, 'IndTraslado')),
            # Extras propios del XML (no existen en el TXT):
            'tipo_documento_sistema': TIPO_DTE_SII.get(tipo_dte),
            'fecha_emision_date': _fecha(fecha_emision_txt),
            'documentos_en_envio': documentos_en_envio,
            'origen': 'XML',
        },
        'emisor': {
            'rut': _texto(nodo_emisor, 'RUTEmisor'),
            'razon_social': _texto(nodo_emisor, 'RznSoc', 'RznSocEmisor'),
            'giro': _texto(nodo_emisor, 'GiroEmis', 'GiroEmisor', 'GiroEmis'),
            'acteco': _texto(nodo_emisor, 'Acteco'),
            'direccion': _texto(nodo_emisor, 'DirOrigen'),
            'comuna': _texto(nodo_emisor, 'CmnaOrigen'),
        },
        'receptor': {
            'rut': _texto(nodo_receptor, 'RUTRecep'),
            'razon_social': _texto(nodo_receptor, 'RznSocRecep'),
            'giro': _texto(nodo_receptor, 'GiroRecep'),
            'direccion': _texto(nodo_receptor, 'DirRecep'),
            'comuna': _texto(nodo_receptor, 'CmnaRecep'),
        },
        'transporte': {
            'patente': _texto(nodo_transporte, 'Patente'),
            'rut_transportista': _texto(nodo_transporte, 'RUTTrans'),
        },
        'totales': {
            'monto_neto': _ent(_texto(nodo_totales, 'MntNeto')),
            'monto_exento': _ent(_texto(nodo_totales, 'MntExe')),
            'tasa_iva': _dec(_texto(nodo_totales, 'TasaIVA')),
            'iva': _ent(_texto(nodo_totales, 'IVA')),
            'monto_total': _ent(_texto(nodo_totales, 'MntTotal')),
        },
        'detalle': [],
        'descuentos_recargos': [],
        'referencias': [],
        'observaciones': _texto(id_doc, 'TermPagoGlosa') or '',
    }

    for indice, nodo in enumerate(doc.findall('Detalle'), start=1):
        resultado['detalle'].append(_parsear_linea_detalle(nodo, indice))

    for nodo in doc.findall('DscRcgGlobal'):
        resultado['descuentos_recargos'].append({
            'nro_linea': _ent(_texto(nodo, 'NroLinDR')),
            'tpo_mov': (_texto(nodo, 'TpoMov') or '').upper() or None,
            'glosa_dr': _texto(nodo, 'GlosaDR'),
            'tpo_valor': _texto(nodo, 'TpoValor'),
            'valor_dr': _dec(_texto(nodo, 'ValorDR')),
            'ind_exe_dr': _ent(_texto(nodo, 'IndExeDR')),
        })

    for nodo in doc.findall('Referencia'):
        resultado['referencias'].append({
            # Claves con el mismo nombre que en el TXT:
            'tipo_documento': _texto(nodo, 'TpoDocRef'),
            'folio': _texto(nodo, 'FolioRef'),
            'fecha': _texto(nodo, 'FchRef'),
            # Extras del XML:
            'nro_linea': _ent(_texto(nodo, 'NroLinRef')),
            'codigo_ref': _ent(_texto(nodo, 'CodRef')),
            'razon_ref': _texto(nodo, 'RazonRef'),
        })

    return resultado


def _parsear_linea_detalle(nodo, indice):
    """Un nodo <Detalle> → dict de línea (shape TXT factura + `codigos`)."""
    codigos = []
    for cdg in nodo.findall('CdgItem')[:MAX_CODIGOS_POR_ITEM]:
        valor = _texto(cdg, 'VlrCodigo')
        if not valor:
            continue
        codigos.append({
            'tipo': (_texto(cdg, 'TpoCodigo') or '')[:MAX_LARGO_TPO_CODIGO],
            'valor': valor[:MAX_LARGO_VLR_CODIGO],
        })

    # OJO: QtyItem y PrcItem son OPCIONALES en el XSD. `None` aquí significa
    # "el proveedor no lo informó", NO cero. La UI debe pedirlo.
    cantidad = _dec(_texto(nodo, 'QtyItem'))
    precio = _dec(_texto(nodo, 'PrcItem'))

    return {
        'nro_linea': _ent(_texto(nodo, 'NroLinDet')) or indice,
        'indicador_exencion': _ent(_texto(nodo, 'IndExe')),
        'nombre': _texto(nodo, 'NmbItem'),
        'descripcion': _texto(nodo, 'DscItem'),
        'cantidad': cantidad,
        'unidad': _texto(nodo, 'UnmdItem'),
        'precio_unitario': precio,
        'descuento_pct': _dec(_texto(nodo, 'DescuentoPct')),
        'monto_descuento': _dec(_texto(nodo, 'DescuentoMonto')),
        'monto_item': _ent(_texto(nodo, 'MontoItem')),
        # `codigo` (singular) existe para igualar el shape del TXT: es el
        # PRIMER CdgItem. `codigos` (plural) trae todos, que es lo que el
        # motor de matching necesita probar uno a uno.
        'codigo': codigos[0]['valor'] if codigos else None,
        'codigos': codigos,
    }


# ---------------------------------------------------------------------------
# VALIDACIÓN / CUADRATURA
# ---------------------------------------------------------------------------

def validar_dte_parseado(datos, tolerancia_cuadratura=Decimal('1')):
    """Valida un documento ya parseado. NO toca la BD.

    Returns:
        dict con:
          - ``ok``       : bool, no hay errores bloqueantes
          - ``errores``  : list[str]
          - ``warnings`` : list[str]
          - ``cuadratura``: dict {verificable, suma_items, ajuste_global,
                                  esperado, diferencia, cuadra, base}
          - ``lineas_sin_cantidad``: list[int] (NroLinDet)
          - ``lineas_sin_precio``  : list[int]
    """
    errores = []
    warnings = []

    documento = datos.get('documento') or {}
    emisor = datos.get('emisor') or {}
    detalle = datos.get('detalle') or []

    tipo = documento.get('tipo_documento')
    if not tipo:
        errores.append('El XML no trae TipoDTE en el encabezado.')
    elif tipo not in TIPO_DTE_SII:
        errores.append(
            f'TipoDTE {tipo} no está soportado por el sistema '
            f'(soportados: {", ".join(str(t) for t in sorted(TIPO_DTE_SII))}).'
        )
    if not documento.get('folio'):
        errores.append('El XML no trae Folio.')
    if not documento.get('fecha_emision_date'):
        errores.append(
            'Fecha de emisión ausente o ilegible '
            f'({documento.get("fecha_emision")!r}).'
        )
    if not emisor.get('rut'):
        errores.append('El XML no trae RUT del emisor (proveedor).')

    if not detalle:
        # Una factura sin detalle no es un error del XML (existen las facturas
        # "por concepto"), pero sí impide conciliar productos.
        warnings.append(
            'El documento no trae líneas de detalle: solo podrá registrarse '
            'como compra por concepto (cabecera sin productos).'
        )
    elif len(detalle) > MAX_DETALLE_POR_DOCUMENTO:
        errores.append(
            f'El documento trae {len(detalle)} líneas de detalle y el máximo '
            f'del SII es {MAX_DETALLE_POR_DOCUMENTO}. El archivo está mal '
            f'formado o no es un DTE válido.'
        )

    sin_cantidad = []
    sin_precio = []
    for linea in detalle:
        nro = linea.get('nro_linea')
        if not linea.get('nombre'):
            errores.append(f'Línea {nro}: falta NmbItem (obligatorio).')
        elif len(linea['nombre']) > MAX_LARGO_NMB_ITEM:
            warnings.append(
                f'Línea {nro}: NmbItem mide {len(linea["nombre"])} caracteres '
                f'(máximo SII {MAX_LARGO_NMB_ITEM}).'
            )
        if linea.get('monto_item') is None:
            errores.append(f'Línea {nro}: falta MontoItem (obligatorio).')
        cantidad = linea.get('cantidad')
        if cantidad is None or cantidad == 0:
            sin_cantidad.append(nro)
        if linea.get('precio_unitario') is None:
            sin_precio.append(nro)

    if sin_cantidad:
        warnings.append(
            f'{len(sin_cantidad)} línea(s) sin QtyItem '
            f'(líneas {", ".join(str(n) for n in sin_cantidad[:10])}'
            f'{"…" if len(sin_cantidad) > 10 else ""}). '
            'QtyItem es OPCIONAL en el SII: deberás completar la cantidad a '
            'mano antes de confirmar. Sin cantidad la línea no puede '
            'convertirse en unidades.'
        )
    if sin_precio:
        warnings.append(
            f'{len(sin_precio)} línea(s) sin PrcItem: el costo unitario se '
            'calculará como MontoItem / cantidad.'
        )

    cuadratura = calcular_cuadratura(datos, tolerancia_cuadratura)
    if cuadratura['verificable'] and not cuadratura['cuadra']:
        warnings.append(
            f'Descuadre: la suma de las líneas da {cuadratura["suma_ajustada"]} '
            f'y el documento declara {cuadratura["esperado"]} '
            f'({cuadratura["base"]}). Diferencia: {cuadratura["diferencia"]}.'
        )
    elif not cuadratura['verificable']:
        warnings.append(
            'No se pudo verificar la cuadratura: el documento no declara '
            'MntNeto/MntExe/MntTotal.'
        )

    return {
        'ok': not errores,
        'errores': errores,
        'warnings': warnings,
        'cuadratura': cuadratura,
        'lineas_sin_cantidad': sin_cantidad,
        'lineas_sin_precio': sin_precio,
    }


def calcular_cuadratura(datos, tolerancia=Decimal('1')):
    """Σ MontoItem (± DscRcgGlobal) contra los totales declarados.

    Base de comparación:
      * Boleta (39/41): `MntTotal` — los montos ya vienen con IVA.
      * Resto: `MntNeto + MntExe` — el detalle va neto.
    """
    documento = datos.get('documento') or {}
    totales = datos.get('totales') or {}
    detalle = datos.get('detalle') or []

    suma_items = Decimal(0)
    for linea in detalle:
        monto = linea.get('monto_item')
        if monto is not None:
            suma_items += Decimal(monto)

    ajuste = Decimal(0)
    for dr in datos.get('descuentos_recargos') or []:
        valor = dr.get('valor_dr')
        if valor is None:
            continue
        valor = Decimal(valor)
        if (dr.get('tpo_valor') or '').strip() == '%':
            valor = (suma_items * valor / Decimal('100')).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP)
        if (dr.get('tpo_mov') or '').upper() == 'D':
            ajuste -= valor
        else:
            ajuste += valor

    suma_ajustada = suma_items + ajuste

    tipo = documento.get('tipo_documento')
    if tipo in TIPOS_BOLETA:
        esperado = totales.get('monto_total')
        base = 'MntTotal'
    else:
        neto = totales.get('monto_neto')
        exento = totales.get('monto_exento')
        if neto is None and exento is None:
            esperado = None
        else:
            esperado = Decimal(neto or 0) + Decimal(exento or 0)
        base = 'MntNeto + MntExe'

    if esperado is None:
        return {
            'verificable': False,
            'suma_items': suma_items,
            'ajuste_global': ajuste,
            'suma_ajustada': suma_ajustada,
            'esperado': None,
            'diferencia': None,
            'cuadra': False,
            'base': base,
        }

    esperado = Decimal(esperado)
    diferencia = suma_ajustada - esperado
    return {
        'verificable': True,
        'suma_items': suma_items,
        'ajuste_global': ajuste,
        'suma_ajustada': suma_ajustada,
        'esperado': esperado,
        'diferencia': diferencia,
        'cuadra': abs(diferencia) <= Decimal(tolerancia),
        'base': base,
    }


# ---------------------------------------------------------------------------
# SERIALIZACIÓN
# ---------------------------------------------------------------------------

def a_json(valor):
    """Copia JSON-serializable: Decimal → str, date → 'YYYY-MM-DD'.

    Necesario porque el documento parseado viaja al navegador y vuelve
    firmado (`django.core.signing`) al confirmar.
    """
    if isinstance(valor, Decimal):
        # str() y no float(): el objetivo es no perder precisión.
        return str(valor)
    if isinstance(valor, (datetime.date, datetime.datetime)):
        return valor.isoformat()[:10]
    if isinstance(valor, dict):
        return {clave: a_json(sub) for clave, sub in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [a_json(sub) for sub in valor]
    return valor


# Reexportados para que quien consuma el parser no tenga que importar Decimal
# ni duplicar el parseo numérico tolerante.
texto_a_decimal = _dec
texto_a_entero = _ent
texto_a_fecha = _fecha
