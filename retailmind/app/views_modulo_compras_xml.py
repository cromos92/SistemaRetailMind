"""
Importación de XML DTE de PROVEEDOR (entrada de facturas de compra).

Qué es esto
-----------
El proveedor nos manda el XML del DTE (EnvioDTE del SII). Hoy esa factura se
digita a mano en Gestión de DTEs de Compras. Este módulo la lee del XML y la
deja registrada con su detalle de líneas conciliado contra nuestro catálogo.

Qué NO es
---------
* **NO toca la EMISIÓN.** Emitir sigue siendo el TXT de Acepta
  (`views_modulo_documentos`). Aquí solo entra lo que nos mandan.
* **NO MUEVE STOCK.** Repetido a propósito porque es la decisión de diseño más
  importante del módulo: confirmar una factura crea `Dte` + `Dte_Productos` y
  nada más. Ni `Movimientos_Producto`, ni `LoteProducto`, ni
  `Producto_Talla.stock`, ni `Productos_Recepcionados`. El stock sigue entrando
  por donde siempre: creación de productos / recepción de la compra. Registrar
  la factura es un hecho CONTABLE; recibir la mercadería es un hecho FÍSICO, y
  meterlos en el mismo botón es exactamente cómo se duplica inventario.

Flujo (3 pasos, una sola pantalla)
----------------------------------
1. **Subir** uno o varios XML → `analizar_xml_dte`. Devuelve cabecera, aviso de
   duplicado (RUT emisor + tipo + folio) y cuadratura (Σ MontoItem vs totales).
2. **Conciliar** línea a línea con semáforo. Verde = match de confianza ALTA;
   amarillo = MEDIA (revísalo); rojo = SIN_MATCH (elígelo a mano o déjalo como
   línea solo-texto). Las líneas sin `QtyItem` traen la cantidad vacía y hay que
   completarla: **una línea sin cantidad no puede confirmarse**.
3. **Confirmar** → `confirmar_xml_dte`. Crea el DTE y GRABA LA EQUIVALENCIA de
   cada línea que el usuario resolvió a mano, para que la próxima factura de ese
   proveedor entre sola.

Integridad de los montos
------------------------
El documento parseado vuelve al navegador y regresa en el paso 3. Para que el
usuario no pueda alterar montos desde el DevTools, el documento viaja **firmado**
con `django.core.signing` (`TOKEN_SALT`). En `confirmar_xml_dte` los montos se
leen SIEMPRE del token firmado; del cliente solo se aceptan las decisiones
humanas: qué producto es cada línea y qué cantidad tiene si el XML no la traía.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .decorators import requiere_permiso
from .models import (
    Dte, Dte_Productos, Empresa, Producto, Producto_Talla,
    ProveedorProductoEquivalencia, Sucursal,
)
from .services.dte_xml_parser import (
    DEFUSEDXML_DISPONIBLE, MAX_DETALLE_POR_DOCUMENTO, TIPOS_BOLETA, TIPOS_NOTA,
    XmlDteError, a_json, calcular_cuadratura, parsear_xml_envio,
    validar_dte_parseado,
)
from .utils_folio_dte import normalizar_rut
from .utils_permisos import obtener_empresas_usuario, obtener_sucursales_usuario
from .utils_producto_match import (
    buscar_producto_por_identidad, normalizar_articulo, ordenar_por_reciente,
    producto_talla_por_sku,
)

logger = logging.getLogger('app')

# Permiso que gobierna todo el módulo. Es el mismo de Gestión de DTEs de
# Compras: quien puede crear una factura de compra a mano puede importarla.
PERMISO = 'gestion_dte_compras'

# --- Firma del documento parseado (ver docstring del módulo) ---
TOKEN_SALT = 'app.compras.xml_dte'
TOKEN_MAX_AGE = 60 * 60 * 4     # 4 h: dura una jornada de conciliación

# --- Topes defensivos ---
MAX_ARCHIVOS_POR_LOTE = 20
MAX_DOCUMENTOS_POR_ARCHIVO = 50
LIMITE_CANDIDATOS = 10
LIMITE_PREFILTRO_NOMBRE = 300
LIMITE_BUSQUEDA = 30

# --- Semáforo de la conciliación ---
CONFIANZA_ALTA = 'ALTA'          # verde  → se puede confirmar tal cual
CONFIANZA_MEDIA = 'MEDIA'        # amarillo → hay propuesta, revísala
SIN_MATCH = 'SIN_MATCH'          # rojo   → cola manual

Usuario = get_user_model()


# =====================================================================
# HELPERS DE RUT / EMPRESA
# =====================================================================

def _qs_empresas_por_rut(rut):
    """Empresas cuyo RUT normalizado coincide con `rut`.

    El RUT se guarda en `Empresa.rut` con formatos mezclados ('76.123.456-7'
    y '76123456-7' conviven en producción) y el XML siempre trae la versión sin
    puntos. Se normaliza EN LA BASE con `Replace` para que la comparación sea
    la misma que usa `utils_folio_dte.empresas_con_mismo_rut`.
    """
    rut_norm = normalizar_rut(rut)
    if not rut_norm:
        return Empresa.objects.none()
    return (
        Empresa.objects
        .annotate(_rut_norm=Replace(
            Replace('rut', Value('.'), Value('')), Value(' '), Value('')))
        .filter(_rut_norm__iexact=rut_norm)
    )


def _buscar_proveedor(rut):
    """Empresa proveedora emisora del XML, o None.

    Se prefiere la que está marcada `esProveedor` (un mismo RUT puede tener
    varias fichas). NO se crea la empresa: dar de alta un proveedor es una
    decisión del usuario y tiene su propia pantalla de importación.
    """
    qs = _qs_empresas_por_rut(rut)
    return qs.filter(esProveedor=True).order_by('-activo', 'id').first() or \
        qs.order_by('-activo', 'id').first()


def _buscar_empresa_receptora(rut, empresas_usuario):
    """La empresa del USUARIO que corresponde al RUTRecep del XML, o None.

    Es el chequeo de scoping: si la factura viene a nombre de una empresa que
    el usuario no administra, no puede cargarla. Se AVISA, no se revienta.
    """
    rut_norm = normalizar_rut(rut)
    if not rut_norm:
        return None
    for empresa in empresas_usuario:
        if normalizar_rut(empresa.rut) == rut_norm:
            return empresa
    return None


def buscar_dtes_duplicados(rut_emisor, tipo_documento, folio, limite=10):
    """DTEs ya cargados con el mismo (RUT emisor, tipo, folio).

    El folio es único por RUT emisor + tipo ante el SII, así que este es el
    criterio correcto de duplicado — NO (proveedor_id, folio), porque el mismo
    contribuyente puede tener más de una ficha `Empresa` en esta base
    (ver `utils_folio_dte.empresas_con_mismo_rut`).
    """
    if not (rut_emisor and tipo_documento and folio):
        return []
    emisor_ids = list(_qs_empresas_por_rut(rut_emisor).values_list('id', flat=True))
    if not emisor_ids:
        return []
    qs = (
        Dte.objects
        .filter(
            emisor_id__in=emisor_ids,
            tipo_documento=tipo_documento,
            numero_documento=folio,
            descartado=False,
        )
        .select_related('emisor', 'receptor', 'sucursal')
        .order_by('-id')[:limite]
    )
    return [
        {
            'dte_id': d.id,
            'numero_documento': d.numero_documento,
            'tipo_documento': d.tipo_documento,
            'emisor': getattr(d.emisor, 'nombre', '') or '',
            'receptor': getattr(d.receptor, 'nombre', '') or '',
            'sucursal': getattr(d.sucursal, 'alias', '') or 'SIN SUCURSAL',
            'fecha_emision': d.fecha_emision.strftime('%Y-%m-%d') if d.fecha_emision else None,
            'monto_con_iva': int(d.monto_con_iva or 0),
            'estado_dte': d.estado_dte,
            'tipo_transaccion': d.tipo_transaccion,
        }
        for d in qs
    ]


# =====================================================================
# MOTOR DE MATCHING  (cascada documentada)
# =====================================================================
#
#   (a) EQUIVALENCIA GUARDADA   (proveedor, codigo_externo)   → ALTA
#         Lo que el usuario ya resolvió a mano antes. Es la única fuente
#         que sabe traducir el código interno del proveedor.
#
#   (b) SKU                     código numérico → Producto_Talla.sku → ALTA
#         Solo si el código es numérico. Vale cuando el proveedor factura
#         con NUESTRO código (típico entre empresas del mismo grupo).
#         Si el SKU existe pero en OTRA sucursal, baja a MEDIA.
#
#   (c) IDENTIDAD POR NOMBRE    NmbItem normalizado vs Producto.articulo → MEDIA
#         Nunca ALTA: es la heurística que a Dynamics 365 se le cae apenas
#         el proveedor cambia una palabra de la descripción.
#
#   (d) SIN MATCH → cola manual (rojo). El usuario elige, y al confirmar la
#       elección se GRABA como equivalencia (a) para la próxima factura.
#
# =====================================================================

def _serializar_talla(pt):
    """Producto_Talla → dict plano para el front."""
    if pt is None:
        return None
    producto = pt.producto
    return {
        'producto_talla_id': pt.id,
        'producto_id': producto.id if producto else None,
        'sku': pt.sku,
        'talla': pt.talla,
        'articulo': producto.articulo if producto else '',
        'descripcion': producto.descripcion if producto else '',
        'stock': pt.stock,
        'costo': producto.costo if producto else 0,
        'sucursal_id': producto.sucursal_id if producto else None,
        'sucursal': (
            producto.sucursal.alias
            if producto and producto.sucursal_id and producto.sucursal else ''
        ),
    }


def _tallas_de(producto, limite=LIMITE_CANDIDATOS):
    return list(
        Producto_Talla.objects
        .filter(producto=producto)
        .select_related('producto', 'producto__sucursal')
        .order_by('talla', 'id')[:limite]
    )


def _codigos_de_linea(linea):
    """Códigos candidatos de la línea, sin repetir y en orden de preferencia."""
    codigos = []
    for item in (linea.get('codigos') or []):
        valor = (item.get('valor') or '').strip()
        if valor and valor not in codigos:
            codigos.append(valor)
    suelto = (linea.get('codigo') or '').strip()
    if suelto and suelto not in codigos:
        codigos.append(suelto)
    return codigos


def _match_por_equivalencia(codigos, empresa_proveedor_id):
    """(a) Equivalencia aprendida. Devuelve (Producto_Talla, codigo) o (None, None)."""
    if not (codigos and empresa_proveedor_id):
        return None, None
    equivalencias = {
        e.codigo_externo: e
        for e in ProveedorProductoEquivalencia.objects
        .filter(empresa_proveedor_id=empresa_proveedor_id, codigo_externo__in=codigos)
        .select_related('producto_talla', 'producto_talla__producto',
                        'producto_talla__producto__sucursal')
    }
    for codigo in codigos:
        equivalencia = equivalencias.get(codigo)
        if equivalencia and equivalencia.producto_talla_id:
            return equivalencia.producto_talla, codigo
    return None, None


def _match_por_sku(codigos, sucursal_id):
    """(b) Código numérico contra `Producto_Talla.sku`."""
    for codigo in codigos:
        limpio = codigo.strip().lstrip('0') or '0'
        if not limpio.isdigit() or len(limpio) > 18:
            continue
        pt = producto_talla_por_sku(
            int(limpio),
            sucursal_id=sucursal_id,
            select_related=('producto', 'producto__sucursal'),
        )
        if pt is not None:
            return pt, codigo
    return None, None


def _productos_por_nombre(nombre, sucursal_id):
    """(c) Productos cuyo `articulo` normalizado coincide con el NmbItem.

    Se hace en tres intentos, del más barato al más caro, y SIEMPRE acotado:
    `NmbItem` es una descripción, no un código, así que un `icontains` del
    primer token puede barrer decenas de miles de fichas.
    """
    objetivo = normalizar_articulo(nombre)
    if not objetivo or len(objetivo) < 3 or not sucursal_id:
        return []

    # 1. Identidad estricta (misma función que usa la creación de productos).
    producto = buscar_producto_por_identidad(
        objetivo, None, None, None, None, sucursal_id)
    if producto is not None:
        return [producto]

    # 2. Igualdad literal del artículo (barata, usa el índice de `articulo`).
    exactos = list(ordenar_por_reciente(
        Producto.objects
        .filter(sucursal_id=sucursal_id, articulo__iexact=(nombre or '').strip())
        .select_related('sucursal')
    )[:LIMITE_CANDIDATOS])
    if exactos:
        return exactos

    # 3. Prefiltro por el primer token + comparación normalizada en Python.
    token = objetivo.split(' ')[0]
    if len(token) < 3:
        return []
    candidatos = ordenar_por_reciente(
        Producto.objects
        .filter(sucursal_id=sucursal_id, articulo__icontains=token)
        .select_related('sucursal')
    )[:LIMITE_PREFILTRO_NOMBRE]
    return [p for p in candidatos if normalizar_articulo(p.articulo) == objetivo]


def matchear_linea(linea, empresa_proveedor_id, sucursal_id):
    """Resuelve UNA línea del detalle contra el catálogo.

    Returns:
        dict con ``propuesta`` (talla serializada o None), ``confianza``
        ('ALTA' | 'MEDIA' | 'SIN_MATCH'), ``motivo``, ``origen`` y
        ``candidatos`` (opciones cuando hay ambigüedad).
    """
    codigos = _codigos_de_linea(linea)
    nombre = linea.get('nombre') or ''

    # (a) EQUIVALENCIA APRENDIDA
    pt, codigo = _match_por_equivalencia(codigos, empresa_proveedor_id)
    if pt is not None:
        return {
            'propuesta': _serializar_talla(pt),
            'confianza': CONFIANZA_ALTA,
            'origen': 'EQUIVALENCIA',
            'motivo': f'Equivalencia guardada para el código «{codigo}» de este proveedor.',
            'candidatos': [],
            'codigo_usado': codigo,
        }

    # (b) SKU
    pt, codigo = _match_por_sku(codigos, sucursal_id)
    if pt is not None:
        misma_sucursal = (
            pt.producto is not None and pt.producto.sucursal_id == sucursal_id
        )
        return {
            'propuesta': _serializar_talla(pt),
            'confianza': CONFIANZA_ALTA if misma_sucursal else CONFIANZA_MEDIA,
            'origen': 'SKU',
            'motivo': (
                f'El código «{codigo}» coincide con el SKU {pt.sku}.'
                if misma_sucursal else
                f'El código «{codigo}» coincide con el SKU {pt.sku}, pero de '
                f'OTRA sucursal. Verifica antes de confirmar.'
            ),
            'candidatos': [],
            'codigo_usado': codigo,
        }

    # (c) IDENTIDAD POR NOMBRE
    productos = _productos_por_nombre(nombre, sucursal_id)
    if len(productos) == 1:
        tallas = _tallas_de(productos[0])
        if len(tallas) == 1:
            return {
                'propuesta': _serializar_talla(tallas[0]),
                'confianza': CONFIANZA_MEDIA,
                'origen': 'NOMBRE',
                'motivo': (
                    f'Coincidencia por descripción con «{productos[0].articulo}» '
                    f'(única talla). El nombre no es identificador fiable: revisa.'
                ),
                'candidatos': [],
                'codigo_usado': None,
            }
        if tallas:
            return {
                'propuesta': None,
                'confianza': CONFIANZA_MEDIA,
                'origen': 'NOMBRE',
                'motivo': (
                    f'Coincide el producto «{productos[0].articulo}» pero tiene '
                    f'{len(tallas)} tallas: elige cuál corresponde.'
                ),
                'candidatos': [_serializar_talla(t) for t in tallas],
                'codigo_usado': None,
            }

    if len(productos) > 1:
        candidatos = []
        for producto in productos[:LIMITE_CANDIDATOS]:
            candidatos.extend(_serializar_talla(t) for t in _tallas_de(producto, 5))
        return {
            'propuesta': None,
            'confianza': SIN_MATCH,
            'origen': 'NOMBRE',
            'motivo': (
                f'{len(productos)} fichas distintas coinciden con esa '
                f'descripción: hay que elegir a mano.'
            ),
            'candidatos': candidatos[:LIMITE_CANDIDATOS],
            'codigo_usado': None,
        }

    # (d) SIN MATCH
    return {
        'propuesta': None,
        'confianza': SIN_MATCH,
        'origen': None,
        'motivo': (
            'Sin coincidencias por código ni por descripción. '
            'Elige el producto (se guardará la equivalencia) o déjalo como '
            'línea solo-texto.'
        ),
        'candidatos': [],
        'codigo_usado': None,
    }


def matchear_detalle(detalle, empresa_proveedor_id, sucursal_id):
    """Aplica `matchear_linea` a todo el detalle y devuelve las líneas anotadas."""
    lineas = []
    for linea in detalle:
        anotada = dict(linea)
        anotada['match'] = matchear_linea(linea, empresa_proveedor_id, sucursal_id)
        lineas.append(anotada)
    return lineas


# =====================================================================
# VISTAS
# =====================================================================

@requiere_permiso(PERMISO, 'puede_crear')
def ver_importar_xml_dte(request):
    """Pantalla única del wizard (subir → conciliar → confirmar)."""
    empresas = list(obtener_empresas_usuario(request.user))
    sucursales = list(obtener_sucursales_usuario(request.user))
    sucursal_actual = _int_o_none(request.session.get('idSucursalActual'))
    empresa_actual = _int_o_none(request.session.get('idEmpresaActual'))

    return render(request, 'vistas/modulo_compras/importacion_xml_dte.html', {
        'empresas': empresas,
        'sucursales': sucursales,
        'sucursal_actual_id': sucursal_actual,
        'empresa_actual_id': empresa_actual,
        'max_lineas': MAX_DETALLE_POR_DOCUMENTO,
        'max_archivos': MAX_ARCHIVOS_POR_LOTE,
        'defusedxml_disponible': DEFUSEDXML_DISPONIBLE,
    })


@require_POST
@requiere_permiso(PERMISO, 'puede_crear')
def analizar_xml_dte(request):
    """Paso 1+2: parsea los XML subidos, valida y propone el match por línea.

    Solo LEE. No escribe nada en la base.
    """
    archivos = request.FILES.getlist('archivos') or request.FILES.getlist('archivo')
    if not archivos:
        return JsonResponse(
            {'success': False, 'error': 'No se recibió ningún archivo XML.'}, status=400)
    if len(archivos) > MAX_ARCHIVOS_POR_LOTE:
        return JsonResponse({
            'success': False,
            'error': f'Máximo {MAX_ARCHIVOS_POR_LOTE} archivos por lote '
                     f'(se recibieron {len(archivos)}).',
        }, status=400)

    sucursal_id = _resolver_sucursal(request)
    empresas_usuario = list(obtener_empresas_usuario(request.user))

    resultados = []
    for archivo in archivos:
        resultados.append(
            _analizar_archivo(archivo, sucursal_id, empresas_usuario))

    total_docs = sum(len(r.get('documentos', [])) for r in resultados)
    return JsonResponse({
        'success': True,
        'sucursal_id': sucursal_id,
        'archivos': resultados,
        'total_documentos': total_docs,
        'defusedxml_disponible': DEFUSEDXML_DISPONIBLE,
    })


def _analizar_archivo(archivo, sucursal_id, empresas_usuario):
    nombre = getattr(archivo, 'name', 'archivo.xml')
    try:
        # IMPORTANTE: BYTES. El XML declara ISO-8859-1 y pasarlo como str
        # revienta el parser ("Unicode strings with encoding declaration").
        contenido = archivo.read()
        documentos = parsear_xml_envio(contenido)
    except XmlDteError as error:
        return {'archivo': nombre, 'ok': False, 'error': str(error), 'documentos': []}
    except Exception as error:  # noqa: BLE001 - no queremos tumbar el lote
        logger.exception('Error inesperado parseando XML DTE %s', nombre)
        return {
            'archivo': nombre, 'ok': False,
            'error': f'Error inesperado leyendo el archivo: {error}',
            'documentos': [],
        }

    if len(documentos) > MAX_DOCUMENTOS_POR_ARCHIVO:
        return {
            'archivo': nombre, 'ok': False,
            'error': f'El envío trae {len(documentos)} documentos y el máximo '
                     f'por archivo es {MAX_DOCUMENTOS_POR_ARCHIVO}.',
            'documentos': [],
        }

    analizados = [
        _analizar_documento(datos, sucursal_id, empresas_usuario, nombre)
        for datos in documentos
    ]
    return {'archivo': nombre, 'ok': True, 'error': None, 'documentos': analizados}


def _analizar_documento(datos, sucursal_id, empresas_usuario, nombre_archivo):
    documento = datos.get('documento') or {}
    emisor = datos.get('emisor') or {}
    receptor = datos.get('receptor') or {}

    validacion = validar_dte_parseado(datos)
    bloqueos = list(validacion['errores'])

    # --- Proveedor (emisor del XML) ---
    proveedor = _buscar_proveedor(emisor.get('rut'))
    if proveedor is None:
        bloqueos.append(
            f'El proveedor con RUT {emisor.get("rut") or "?"} '
            f'({emisor.get("razon_social") or "sin razón social"}) no existe en '
            f'el sistema. Créalo antes de importar la factura.'
        )

    # --- Scoping: el receptor tiene que ser una empresa del usuario ---
    empresa_receptora = _buscar_empresa_receptora(receptor.get('rut'), empresas_usuario)
    receptor_reconocido = empresa_receptora is not None
    if not receptor_reconocido:
        bloqueos.append(
            f'La factura viene a nombre del RUT {receptor.get("rut") or "?"} '
            f'({receptor.get("razon_social") or "sin razón social"}), que no '
            f'corresponde a ninguna de tus empresas. Elige la empresa receptora '
            f'a mano si igualmente debes cargarla.'
        )

    # --- Duplicado (RUT emisor + tipo + folio) ---
    tipo_sistema = documento.get('tipo_documento_sistema')
    duplicados = buscar_dtes_duplicados(
        emisor.get('rut'), tipo_sistema, documento.get('folio'))
    if duplicados:
        bloqueos.append(
            f'Ya existe un {tipo_sistema} folio {documento.get("folio")} de este '
            f'proveedor (DTE #{duplicados[0]["dte_id"]}). Confirmar crearía un '
            f'documento duplicado.'
        )

    detalle = matchear_detalle(
        datos.get('detalle') or [],
        proveedor.id if proveedor else None,
        sucursal_id,
    )

    resumen = {'ALTA': 0, 'MEDIA': 0, 'SIN_MATCH': 0}
    for linea in detalle:
        resumen[linea['match']['confianza']] = resumen.get(
            linea['match']['confianza'], 0) + 1

    # El documento firmado es la fuente de verdad de los montos al confirmar.
    token = signing.dumps(a_json({
        'documento': datos.get('documento'),
        'emisor': datos.get('emisor'),
        'receptor': datos.get('receptor'),
        'totales': datos.get('totales'),
        'detalle': datos.get('detalle'),
        'descuentos_recargos': datos.get('descuentos_recargos'),
        'referencias': datos.get('referencias'),
        'archivo': nombre_archivo,
    }), salt=TOKEN_SALT, compress=True)

    return {
        'token': token,
        'archivo': nombre_archivo,
        'documento': a_json(documento),
        'emisor': a_json(emisor),
        'receptor': a_json(receptor),
        'totales': a_json(datos.get('totales') or {}),
        'referencias': a_json(datos.get('referencias') or []),
        'descuentos_recargos': a_json(datos.get('descuentos_recargos') or []),
        'detalle': a_json(detalle),
        'validacion': {
            'ok': validacion['ok'],
            'errores': validacion['errores'],
            'warnings': validacion['warnings'],
            'lineas_sin_cantidad': validacion['lineas_sin_cantidad'],
            'lineas_sin_precio': validacion['lineas_sin_precio'],
            'cuadratura': a_json(validacion['cuadratura']),
        },
        'proveedor': {
            'encontrado': proveedor is not None,
            'id': proveedor.id if proveedor else None,
            'nombre': proveedor.nombre if proveedor else None,
            'rut': emisor.get('rut'),
            'razon_social_xml': emisor.get('razon_social'),
        },
        'empresa_receptora': {
            'reconocida': receptor_reconocido,
            'id': empresa_receptora.id if empresa_receptora else None,
            'nombre': empresa_receptora.nombre if empresa_receptora else None,
            'rut': receptor.get('rut'),
            'razon_social_xml': receptor.get('razon_social'),
        },
        'duplicados': duplicados,
        'resumen_match': resumen,
        'bloqueos': bloqueos,
        'puede_confirmar': not bloqueos,
    }


@require_GET
@requiere_permiso(PERMISO, 'puede_crear')
def buscar_producto_xml_dte(request):
    """Buscador de la pantalla de conciliación (SKU / artículo / descripción)."""
    termino = (request.GET.get('q') or '').strip()
    if len(termino) < 2:
        return JsonResponse({'success': True, 'resultados': []})

    sucursal_id = _resolver_sucursal(request, request.GET.get('sucursal_id'))

    filtro = Q(producto__articulo__icontains=termino) | \
        Q(producto__descripcion__icontains=termino)
    if termino.isdigit() and len(termino) <= 18:
        filtro = filtro | Q(sku=int(termino))

    qs = (
        Producto_Talla.objects
        .filter(filtro)
        .select_related('producto', 'producto__sucursal')
    )
    if sucursal_id:
        # La sucursal del usuario primero; el resto queda accesible con
        # `todas=1` porque el proveedor a veces factura a otra bodega.
        if request.GET.get('todas') != '1':
            qs = qs.filter(producto__sucursal_id=sucursal_id)

    resultados = [_serializar_talla(pt) for pt in qs.order_by('producto__articulo', 'talla')[:LIMITE_BUSQUEDA]]
    return JsonResponse({'success': True, 'resultados': resultados})


@require_POST
@requiere_permiso(PERMISO, 'puede_crear')
def confirmar_xml_dte(request):
    """Paso 3: crea el `Dte` de COMPRA + sus `Dte_Productos` y APRENDE.

    NO MUEVE STOCK. Ver el docstring del módulo: registrar la factura es un
    hecho contable; el ingreso físico sigue siendo la recepción / creación de
    productos.
    """
    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    token = payload.get('token')
    if not token:
        return JsonResponse(
            {'success': False, 'error': 'Falta el token del documento.'}, status=400)

    try:
        datos = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        return JsonResponse({
            'success': False,
            'error': 'La sesión de conciliación expiró. Vuelve a subir el XML.',
        }, status=400)
    except signing.BadSignature:
        return JsonResponse({
            'success': False,
            'error': 'El documento fue alterado. Vuelve a subir el XML.',
        }, status=400)

    documento = datos.get('documento') or {}
    emisor = datos.get('emisor') or {}
    totales = datos.get('totales') or {}
    detalle = datos.get('detalle') or []

    # ---- Revalidación server-side (nunca confiar en el paso anterior) ----
    errores = []

    tipo_sistema = documento.get('tipo_documento_sistema')
    folio = _int_o_none(documento.get('folio'))
    fecha_emision = _fecha_desde(documento.get('fecha_emision_date')) or \
        _fecha_desde(documento.get('fecha_emision'))
    if not tipo_sistema:
        errores.append('El documento no tiene un tipo soportado por el sistema.')
    if not folio:
        errores.append('El documento no tiene folio.')
    if not fecha_emision:
        errores.append('El documento no tiene fecha de emisión legible.')
    if len(detalle) > MAX_DETALLE_POR_DOCUMENTO:
        errores.append(
            f'El documento trae {len(detalle)} líneas (máximo '
            f'{MAX_DETALLE_POR_DOCUMENTO}).')

    proveedor = _buscar_proveedor(emisor.get('rut'))
    if proveedor is None:
        errores.append(
            f'El proveedor con RUT {emisor.get("rut") or "?"} no existe en el sistema.')

    empresas_usuario = list(obtener_empresas_usuario(request.user))
    empresa_receptora = _buscar_empresa_receptora(
        (datos.get('receptor') or {}).get('rut'), empresas_usuario)
    if empresa_receptora is None:
        # El usuario puede forzar la empresa receptora, pero SOLO entre las
        # suyas: eso es el scoping.
        elegida_id = _int_o_none(payload.get('empresa_receptora_id'))
        empresa_receptora = next(
            (e for e in empresas_usuario if e.id == elegida_id), None)
        if empresa_receptora is None:
            errores.append(
                'El receptor del XML no es una empresa tuya. Selecciona la '
                'empresa receptora entre las que administras.')

    duplicados = buscar_dtes_duplicados(emisor.get('rut'), tipo_sistema, folio)
    if duplicados:
        errores.append(
            f'Ya existe un {tipo_sistema} folio {folio} de este proveedor '
            f'(DTE #{duplicados[0]["dte_id"]}).')

    cuadratura = calcular_cuadratura(_con_decimales(datos))
    if cuadratura['verificable'] and not cuadratura['cuadra'] \
            and not payload.get('aceptar_descuadre'):
        errores.append(
            f'El documento no cuadra: las líneas suman '
            f'{cuadratura["suma_ajustada"]} y declara {cuadratura["esperado"]} '
            f'({cuadratura["base"]}). Marca "aceptar descuadre" si aun así '
            f'quieres registrarlo.')

    sucursal_id = _resolver_sucursal(request, payload.get('sucursal_id'))
    sucursal = Sucursal.objects.filter(id=sucursal_id).first() if sucursal_id else None

    # ---- Resoluciones del usuario (lo ÚNICO que se acepta del cliente) ----
    resoluciones = {}
    for item in (payload.get('lineas') or []):
        nro = _int_o_none(item.get('nro_linea'))
        if nro is not None:
            resoluciones[nro] = item

    lineas_finales = []
    for linea in detalle:
        nro = _int_o_none(linea.get('nro_linea'))
        resolucion = resoluciones.get(nro, {})

        # Cantidad: la del XML manda; si el XML no la traía (QtyItem es
        # OPCIONAL en el SII) se usa la que escribió el usuario. Sin cantidad
        # la línea NO se confirma: una línea sin unidades no puede
        # representar mercadería.
        cantidad = _dec_o_none(linea.get('cantidad'))
        origen_cantidad = 'XML'
        if cantidad is None or cantidad <= 0:
            cantidad = _dec_o_none(resolucion.get('cantidad'))
            origen_cantidad = 'MANUAL'
        if cantidad is None or cantidad <= 0:
            errores.append(
                f'Línea {nro} ({linea.get("nombre") or "sin nombre"}): falta la '
                f'cantidad. El XML no la trae y no la completaste.')
            continue

        producto_talla = None
        pt_id = _int_o_none(resolucion.get('producto_talla_id'))
        if pt_id:
            producto_talla = (
                Producto_Talla.objects
                .select_related('producto')
                .filter(id=pt_id)
                .first()
            )
            if producto_talla is None:
                errores.append(f'Línea {nro}: el producto elegido no existe.')
                continue

        lineas_finales.append({
            'nro': nro,
            'linea': linea,
            'cantidad': cantidad,
            'origen_cantidad': origen_cantidad,
            'producto_talla': producto_talla,
            'guardar_equivalencia': bool(resolucion.get('guardar_equivalencia', True)),
            'origen_match': resolucion.get('origen_match'),
        })

    if errores:
        return JsonResponse({'success': False, 'error': errores[0], 'errores': errores},
                            status=400)

    # ---- Escritura ----
    responsable = request.session.get('nombreUsuario') or request.user.get_username()
    tipo_dte_sii = _int_o_none(documento.get('tipo_documento'))
    es_boleta = tipo_dte_sii in TIPOS_BOLETA

    monto_total = _dec_o_none(totales.get('monto_total')) or Decimal(0)
    monto_neto = _dec_o_none(totales.get('monto_neto'))
    monto_exento = _dec_o_none(totales.get('monto_exento')) or Decimal(0)
    if monto_neto is None:
        monto_neto = (
            (monto_total / Decimal('1.19')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            if es_boleta else monto_total - monto_exento
        )

    fecha_vencimiento = _fecha_desde(documento.get('fecha_vencimiento')) or fecha_emision
    dias_credito = max(0, (fecha_vencimiento - fecha_emision).days)
    unidades = sum(int(l['cantidad']) for l in lineas_finales)

    referencias_txt = _texto_referencias(datos.get('referencias') or [])

    with transaction.atomic():
        dte = Dte.objects.create(
            emisor=proveedor,                 # el proveedor emite la factura
            receptor=empresa_receptora,       # nosotros la recibimos
            numero_documento=folio,
            tipo_documento=tipo_sistema,
            monto_con_iva=monto_total,
            monto_neto=monto_neto,
            estado_pago=payload.get('estado_pago') or 'PENDIENTE',
            estado_dte='EMITIDO',
            responsable=responsable,
            fecha_emision=fecha_emision,
            fecha_vencimiento=fecha_vencimiento,
            diasCredito=dias_credito,
            bultos=0,
            unidades_productos=unidades,
            descuento=0,
            sucursal=sucursal,
            tipo_transaccion='COMPRA',
            referencias=referencias_txt or None,
            es_nota_credito=(tipo_dte_sii in TIPOS_NOTA),
            # Cargado desde un XML externo, no emitido por este sistema.
            es_manual=True,
            # Tiene líneas de detalle: NO es una compra "por concepto".
            es_por_concepto=False,
        )

        equivalencias_creadas = 0
        equivalencias_reutilizadas = 0
        objetos = []
        for item in lineas_finales:
            linea = item['linea']
            cantidad = item['cantidad']
            monto_item = _int_o_none(linea.get('monto_item')) or 0
            precio_unitario = _dec_o_none(linea.get('precio_unitario'))
            if precio_unitario is None and cantidad:
                # PrcItem también es opcional: se deriva del monto de la línea.
                precio_unitario = Decimal(monto_item) / cantidad
            precio_int = int((precio_unitario or Decimal(0)).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP))

            descripcion = (linea.get('nombre') or '').strip()
            if linea.get('descripcion'):
                descripcion = f"{descripcion} - {linea['descripcion']}".strip(' -')

            # `Dte_Productos.clean()` exige monto cuando hay porcentaje; como
            # aquí se usa bulk_create (que NO llama a clean) se normaliza a
            # mano para no dejar filas que el validador rechazaría después.
            descuento_pct = _dec_o_none(linea.get('descuento_pct'))
            descuento_monto = _dec_o_none(linea.get('monto_descuento'))
            if descuento_pct is not None and descuento_monto is None:
                descuento_monto = Decimal(0)

            objetos.append(Dte_Productos(
                dte=dte,
                productoTalla=item['producto_talla'],
                descripcion=descripcion[:255],
                costo=precio_int,
                sobreprecio=0,
                precio=precio_int,
                precio_unitario=precio_int,
                descuento_pct=descuento_pct,
                descuento_monto=descuento_monto,
                monto_item=monto_item,
                stock=int(cantidad),
                activo=True,
            ))

        Dte_Productos.objects.bulk_create(objetos)

        # ---- APRENDIZAJE: cada línea resuelta deja su equivalencia ----
        creadas, reutilizadas = _aprender_equivalencias(
            lineas_finales, proveedor, request.user)
        equivalencias_creadas += creadas
        equivalencias_reutilizadas += reutilizadas

    logger.info(
        'XML DTE importado: dte_id=%s folio=%s tipo=%s proveedor=%s lineas=%s '
        'equivalencias_nuevas=%s usuario=%s (SIN movimiento de stock)',
        dte.id, folio, tipo_sistema, proveedor.id, len(lineas_finales),
        equivalencias_creadas, request.user.get_username(),
    )

    return JsonResponse({
        'success': True,
        'dte_id': dte.id,
        'folio': folio,
        'tipo_documento': tipo_sistema,
        'lineas_creadas': len(lineas_finales),
        'lineas_con_producto': sum(1 for l in lineas_finales if l['producto_talla']),
        'equivalencias_creadas': equivalencias_creadas,
        'equivalencias_reutilizadas': equivalencias_reutilizadas,
        'stock_movido': False,
        'mensaje': (
            f'Factura {tipo_sistema} folio {folio} registrada con '
            f'{len(lineas_finales)} línea(s). NO se movió stock: la mercadería '
            f'entra por la recepción / creación de productos.'
        ),
    })


def _aprender_equivalencias(lineas_finales, proveedor, usuario):
    """Graba/actualiza la equivalencia (proveedor, código) → producto.

    Esta es la razón de ser del módulo: la primera factura de un proveedor se
    concilia a mano, y desde la segunda entra sola.

    Reglas:
      * Solo se graba si la línea quedó vinculada a un `Producto_Talla`.
      * Solo para códigos que el XML traía (`CdgItem`). Sin código no hay clave.
      * Si la equivalencia ya existía se le suma 1 a `veces_usada`; si apuntaba
        a OTRO producto se REAPUNTA al que acaba de elegir el usuario (la
        última decisión humana gana) y se deja rastro en el log.
    """
    creadas = 0
    reutilizadas = 0
    usuario_db = usuario if isinstance(usuario, Usuario) else None

    for item in lineas_finales:
        producto_talla = item['producto_talla']
        if producto_talla is None or not item['guardar_equivalencia']:
            continue

        linea = item['linea']
        codigos = (linea.get('codigos') or [])
        if not codigos and linea.get('codigo'):
            codigos = [{'tipo': '', 'valor': linea['codigo']}]

        for codigo in codigos:
            valor = (codigo.get('valor') or '').strip()[:50]
            if not valor:
                continue
            existente = ProveedorProductoEquivalencia.objects.filter(
                empresa_proveedor=proveedor, codigo_externo=valor).first()
            if existente is None:
                ProveedorProductoEquivalencia.objects.create(
                    empresa_proveedor=proveedor,
                    codigo_externo=valor,
                    tipo_codigo=(codigo.get('tipo') or '')[:10],
                    descripcion_externa=(linea.get('nombre') or '')[:255],
                    producto_talla=producto_talla,
                    creado_por=usuario_db,
                    veces_usada=0,
                )
                creadas += 1
                continue

            if existente.producto_talla_id != producto_talla.id:
                logger.warning(
                    'Equivalencia reapuntada: proveedor=%s codigo=%s '
                    'producto_talla %s → %s (decisión de %s)',
                    proveedor.id, valor, existente.producto_talla_id,
                    producto_talla.id, getattr(usuario, 'username', '?'),
                )
                existente.producto_talla = producto_talla
                existente.descripcion_externa = (linea.get('nombre') or '')[:255]
                existente.save(update_fields=['producto_talla', 'descripcion_externa'])
                creadas += 1
            else:
                ProveedorProductoEquivalencia.objects.filter(pk=existente.pk).update(
                    veces_usada=F('veces_usada') + 1)
                reutilizadas += 1

    return creadas, reutilizadas


# =====================================================================
# UTILIDADES INTERNAS
# =====================================================================

def _int_o_none(valor):
    if valor in (None, '', 'null', 'None'):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(valor)))
        except Exception:  # noqa: BLE001
            return None


def _dec_o_none(valor):
    if valor in (None, '', 'null', 'None'):
        return None
    try:
        return Decimal(str(valor))
    except Exception:  # noqa: BLE001
        return None


def _fecha_desde(valor):
    from .services.dte_xml_parser import texto_a_fecha
    if valor is None:
        return None
    if hasattr(valor, 'year') and hasattr(valor, 'month'):
        return valor
    return texto_a_fecha(valor)


def _con_decimales(datos):
    """Re-hidrata los numéricos del token firmado para `calcular_cuadratura`."""
    copia = {
        'documento': dict(datos.get('documento') or {}),
        'totales': {},
        'detalle': [],
        'descuentos_recargos': [],
    }
    totales = datos.get('totales') or {}
    for clave in ('monto_neto', 'monto_exento', 'iva', 'monto_total'):
        copia['totales'][clave] = _int_o_none(totales.get(clave))
    copia['totales']['tasa_iva'] = _dec_o_none(totales.get('tasa_iva'))

    for linea in datos.get('detalle') or []:
        copia['detalle'].append({
            'nro_linea': _int_o_none(linea.get('nro_linea')),
            'monto_item': _int_o_none(linea.get('monto_item')),
        })
    for dr in datos.get('descuentos_recargos') or []:
        copia['descuentos_recargos'].append({
            'tpo_mov': dr.get('tpo_mov'),
            'tpo_valor': dr.get('tpo_valor'),
            'valor_dr': _dec_o_none(dr.get('valor_dr')),
            'ind_exe_dr': _int_o_none(dr.get('ind_exe_dr')),
        })
    return copia


def _texto_referencias(referencias):
    partes = []
    for ref in referencias:
        tipo = ref.get('tipo_documento') or '?'
        folio = ref.get('folio') or '?'
        razon = ref.get('razon_ref') or ''
        partes.append(f'Ref {tipo} folio {folio}{" - " + razon if razon else ""}')
    return ' | '.join(partes)[:2000]


def _resolver_sucursal(request, sucursal_pedida=None):
    """Sucursal en cuyo catálogo se busca, acotada a las del usuario.

    Prioridad: la pedida (si es del usuario) → la de sesión → la primera suya.
    """
    permitidas = list(
        obtener_sucursales_usuario(request.user).values_list('id', flat=True))
    pedida = _int_o_none(sucursal_pedida)
    if pedida and pedida in permitidas:
        return pedida
    sesion = _int_o_none(request.session.get('idSucursalActual'))
    if sesion and sesion in permitidas:
        return sesion
    return permitidas[0] if permitidas else None
