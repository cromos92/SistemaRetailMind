"""
Reportes de control de despacho vs recepción.

Dos reportes que leen datos que YA existen en la base y que hasta ahora ningún
reporte mostraba:

1. **Diferencias despachado vs recepcionado** (`Productos_Recepcionados`).
   El modelo guarda por línea `cantidad_esperada`, `stockArribado`,
   `cantidad_faltante`, `cantidad_danada`, `cantidad_sobrante`, el estado de la
   recepción, quién la hizo y cuándo. `cantidad_danada` y `cantidad_sobrante`
   no aparecían en NINGÚN reporte del sistema: la merma por daño y el sobrante
   se registraban en la pantalla de recepción y ahí morían.

2. **Mercadería en tránsito consolidada** (kardex).
   Equivalente multi-sucursal de `_historial_despachos_reales`
   (`views_modulo_existencias_nuevo.py`), que sólo mira la sucursal de sesión y
   vive escondido dentro de la pantalla de despacho. Aquí se consolida sobre
   TODAS las sucursales de las empresas del usuario, con antigüedad en días,
   valorización de lo pendiente y drill-down por SKU.

Ambos son 100% de LECTURA: no escriben ni migran nada.

Reglas transversales:

* **Alcance por empresa.** Nunca se confía en lo que venga en la query string ni
  en la sucursal de sesión: el universo se acota a las empresas donde el usuario
  tiene `EmpresaUser` vigente (`obtener_empresas_usuario`). Un documento entra
  si alguna de esas empresas está en cualquiera de sus tres extremos: emisor
  (yo despaché), receptor (yo recibí) o dueña de la sucursal destino.
* **Documentos muertos fuera.** Se excluyen los DTE con `descartado=True` y los
  que quedaron en ANULADO / CANCELADO / RECHAZADO: perseguir faltantes de un
  documento anulado es ruido puro.
* **Agregación en la base.** Los KPI y las agrupaciones salen de
  `aggregate()`/`annotate()`; el detalle se pagina con LIMIT/OFFSET real. No se
  materializan querysets grandes en memoria para sumarlos en Python.
"""
import logging
from datetime import datetime, timedelta

from django.core.paginator import Paginator
from django.db.models import Count, F, IntegerField, Min, Q, Sum, Value
from django.db.models.functions import Abs, Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .constants_kardex import CONCEPTOS_TRASPASO_ENTRADA
from .decorators import requiere_permiso
from .models import (
    ESTADO_RECEPCION_PRODUCTO_CHOICES,
    Dte,
    Dte_Productos,
    Movimientos_Producto,
    Productos_Recepcionados,
    Sucursal,
)
from .utils_permisos import obtener_empresas_usuario, obtener_sucursales_usuario

logger = logging.getLogger('app')


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Códigos propios de OpcionMenu (sembrados por `inicializar_permisos`). Tienen
# que coincidir con los del menú: si el menú muestra la opción con un código y
# la vista exige otro, el usuario ve el enlace y recibe un 403 al entrar.
PERMISO_REPORTE = 'reporte_diferencias_recepcion'
PERMISO_TRANSITO = 'reporte_mercaderia_transito'

# DTE que no vale la pena perseguir: anulados, rechazados o descartados.
ESTADOS_DTE_EXCLUIDOS = ('ANULADO', 'CANCELADO', 'RECHAZADO')

# Días que se consideran "tránsito normal" antes de marcar un despacho como no
# recibido. Mismo umbral que usa la pantalla de despacho para no dar dos
# lecturas distintas del mismo documento.
DIAS_TRANSITO_NORMAL = 3

# Movimientos que SUMAN stock en el destino (para netear devoluciones por NC).
TIPOS_MOVIMIENTO_ENTRADA = ('INGRESO', 'DEVOLUCION')

# Conceptos que también INGRESAN unidades al destino contra el MISMO DTE del
# despacho, fuera del traspaso normal: el botón "Llegó todo"
# (`anular_regularizacion_dte`, views.py) escribe ANULACION_REGULARIZACION y
# las correcciones manuales escriben CORRECCION_STOCK. Sin contarlos, un
# documento cerrado por esos flujos quedaba SIN_RECIBIR para siempre (falso
# positivo permanente — auditoría ago-2026, P1.4: folio 17098). Se exigen
# tipo INGRESO + estado COMPLETADO porque ambos conceptos existen también en
# dirección de salida (la reversa de "cancelar regularización" es un EGRESO
# con el mismo concepto ANULACION_REGULARIZACION).
CONCEPTOS_CIERRE_RECEPCION = ('ANULACION_REGULARIZACION', 'CORRECCION_STOCK')

DIAS_DEFAULT = 30
DIAS_MAX = 730

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

TRANSITO_DIAS_DEFAULT = 90
TRANSITO_DIAS_MAX = 365
TRANSITO_MAX_DOCUMENTOS = 2000
TRANSITO_PAGE_SIZE = 25

# Tope de filas por agrupación (sucursal / proveedor): son vistas de resumen,
# no listados; sin tope una empresa con cientos de proveedores devolvería un
# JSON inmanejable.
TOPE_AGRUPACION = 100

ESTADOS_RECEPCION_LABEL = dict(ESTADO_RECEPCION_PRODUCTO_CHOICES)

TIPOS_DIFERENCIA = ('todas', 'con_diferencia', 'faltante', 'danado', 'sobrante', 'ok')


# ---------------------------------------------------------------------------
# Helpers de entrada (la query string es entrada del usuario: nunca debe dar 500)
# ---------------------------------------------------------------------------

def _entero(valor, default, minimo=None, maximo=None):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return default
    if minimo is not None and n < minimo:
        return minimo
    if maximo is not None and n > maximo:
        return maximo
    return n


def _fecha(valor, default):
    """Parsea 'YYYY-MM-DD'; cualquier basura cae al default."""
    if not valor:
        return default
    try:
        return datetime.strptime(str(valor).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def _rango_fechas(request, dias_default=DIAS_DEFAULT):
    """(desde, hasta) saneado. `hasta` nunca queda antes de `desde`."""
    hoy = timezone.localdate()
    hasta = _fecha(request.GET.get('fecha_hasta'), hoy)
    desde = _fecha(request.GET.get('fecha_desde'), hasta - timedelta(days=dias_default))
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > DIAS_MAX:
        desde = hasta - timedelta(days=DIAS_MAX)
    return desde, hasta


# ---------------------------------------------------------------------------
# Alcance por empresa (scoping)
# ---------------------------------------------------------------------------

def _empresa_ids(request):
    """IDs de las empresas del usuario. Se cachea por request."""
    cache = getattr(request, '_rep_dif_empresa_ids', None)
    if cache is not None:
        return cache
    ids = list(obtener_empresas_usuario(request.user).values_list('id', flat=True))
    request._rep_dif_empresa_ids = ids
    return ids


def _sucursal_ids(request):
    """
    IDs de sucursal del usuario (todas las de sus empresas, activas o no).

    Se incluyen las inactivas a propósito: un despacho que salió hace tres meses
    desde una bodega que después se cerró sigue siendo mercadería sin recibir y
    tiene que aparecer.
    """
    cache = getattr(request, '_rep_dif_sucursal_ids', None)
    if cache is not None:
        return cache
    ids = list(
        Sucursal.objects.filter(empresa_id__in=_empresa_ids(request))
        .values_list('id', flat=True)
    )
    request._rep_dif_sucursal_ids = ids
    return ids


def _sin_acceso(mensaje='No tienes acceso a este dato: pertenece a otra empresa.'):
    return JsonResponse({'success': False, 'error': mensaje}, status=403)


# ---------------------------------------------------------------------------
# TAREA 1 — Diferencias despachado vs recepcionado
# ---------------------------------------------------------------------------

def _queryset_recepciones(request):
    """
    Universo base de líneas recepcionadas visibles para el usuario.

    Sólo recepciones con DTE: son las que tienen documento (folio, tipo,
    emisor) contra el cual comparar. Las recepciones de compra sin DTE
    (`compra_producto_talla`, típicas de las compras históricas vinculadas
    retroactivamente) quedan fuera porque no tienen un despacho documentado con
    el que contrastar.
    """
    empresa_ids = _empresa_ids(request)
    return (
        Productos_Recepcionados.objects
        .filter(dte__isnull=False)
        .filter(
            Q(dte__emisor_id__in=empresa_ids)
            | Q(dte__receptor_id__in=empresa_ids)
            | Q(sucursal_destino__empresa_id__in=empresa_ids)
        )
        .exclude(dte__descartado=True)
        .exclude(dte__estado_dte__in=ESTADOS_DTE_EXCLUIDOS)
    )


def _filtro_fecha_recepcion(desde, hasta):
    """
    Corte por fecha tolerante a `fecha_recepcion` nula.

    `fecha_recepcion` (DateTime) es el dato bueno, pero las filas antiguas lo
    tienen en NULL; para ésas se cae a `fecha` (DateField con auto_now). Sin el
    fallback, filtrar por período borraba del reporte toda la historia legacy.
    """
    return (
        Q(fecha_recepcion__date__gte=desde, fecha_recepcion__date__lte=hasta)
        | (Q(fecha_recepcion__isnull=True) & Q(fecha__gte=desde, fecha__lte=hasta))
    )


def _filtro_tipo_diferencia(tipo):
    """Q() correspondiente al filtro 'tipo de diferencia'."""
    if tipo == 'faltante':
        return Q(cantidad_faltante__gt=0)
    if tipo == 'danado':
        return Q(cantidad_danada__gt=0)
    if tipo == 'sobrante':
        return Q(cantidad_sobrante__gt=0)
    if tipo == 'con_diferencia':
        return Q(cantidad_faltante__gt=0) | Q(cantidad_danada__gt=0) | Q(cantidad_sobrante__gt=0)
    if tipo == 'ok':
        return Q(cantidad_faltante=0, cantidad_danada=0, cantidad_sobrante=0)
    return Q()


def _costo_linea():
    """Costo unitario de la línea del DTE asociada (0 si la línea no existe)."""
    return Coalesce(F('dte_producto__costo'), Value(0), output_field=IntegerField())


def _suma_valorizada(campo_cantidad):
    """Sum(cantidad * costo_de_la_linea) resuelto en la base."""
    return Coalesce(
        Sum(F(campo_cantidad) * _costo_linea(), output_field=IntegerField()),
        Value(0),
        output_field=IntegerField(),
    )


def _suma(campo):
    return Coalesce(Sum(campo), Value(0), output_field=IntegerField())


_AGREGADOS_DIFERENCIAS = {
    'esperadas': _suma('cantidad_esperada'),
    'recibidas': _suma('stockArribado'),
    'faltantes': _suma('cantidad_faltante'),
    'danadas': _suma('cantidad_danada'),
    'sobrantes': _suma('cantidad_sobrante'),
    'valor_esperado': _suma_valorizada('cantidad_esperada'),
    'valor_recibido': _suma_valorizada('stockArribado'),
    'valor_faltante': _suma_valorizada('cantidad_faltante'),
    'valor_danado': _suma_valorizada('cantidad_danada'),
    'valor_sobrante': _suma_valorizada('cantidad_sobrante'),
}


@requiere_permiso(PERMISO_REPORTE)
@require_GET
def ver_reporte_diferencias_recepcion(request):
    """Pantalla del reporte de diferencias despachado vs recepcionado."""
    sucursales = [
        {'id': s.id, 'alias': s.alias or f'Sucursal {s.id}'}
        for s in obtener_sucursales_usuario(request.user)
    ]
    hoy = timezone.localdate()
    return render(request, 'vistas/modulo_reportes/reporte_diferencias_recepcion.html', {
        'sucursales': sucursales,
        'fecha_desde': (hoy - timedelta(days=DIAS_DEFAULT)).strftime('%Y-%m-%d'),
        'fecha_hasta': hoy.strftime('%Y-%m-%d'),
    })


@requiere_permiso(PERMISO_REPORTE)
@require_GET
def api_reporte_diferencias_recepcion(request):
    """
    JSON del reporte de diferencias.

    Filtros: fecha_desde, fecha_hasta, sucursal_id (destino), proveedor_id
    (emisor del DTE), tipo (todas|con_diferencia|faltante|danado|sobrante|ok),
    page, page_size.

    Devuelve: `resumen` (KPI de unidades y valorización), `datos` (una fila por
    línea recepcionada, paginada en la base), `por_sucursal`, `por_proveedor` y
    `proveedores` (opciones para el combo, calculadas sin aplicar el filtro de
    proveedor para que no se auto-vacíe).
    """
    try:
        desde, hasta = _rango_fechas(request)
        tipo = (request.GET.get('tipo') or 'todas').strip().lower()
        if tipo not in TIPOS_DIFERENCIA:
            tipo = 'todas'
        sucursal_id = _entero(request.GET.get('sucursal_id'), None)
        proveedor_id = _entero(request.GET.get('proveedor_id'), None)
        page_num = _entero(request.GET.get('page'), 1, 1)
        page_size = _entero(request.GET.get('page_size'), PAGE_SIZE_DEFAULT, 10, PAGE_SIZE_MAX)

        base = _queryset_recepciones(request).filter(_filtro_fecha_recepcion(desde, hasta))

        # El combo de proveedor se arma ANTES de aplicar su propio filtro: si se
        # calculara después, al elegir un proveedor el select se quedaría con esa
        # única opción y no habría forma de volver atrás.
        proveedores = [
            {'id': row['dte__emisor_id'], 'nombre': row['dte__emisor__nombre'] or '—'}
            for row in (
                base.exclude(dte__emisor__isnull=True)
                .values('dte__emisor_id', 'dte__emisor__nombre')
                .distinct()
                .order_by('dte__emisor__nombre')[:TOPE_AGRUPACION * 3]
            )
        ]

        if sucursal_id:
            if sucursal_id not in _sucursal_ids(request):
                return _sin_acceso('Esa sucursal no pertenece a una empresa habilitada para ti.')
            base = base.filter(sucursal_destino_id=sucursal_id)
        if proveedor_id:
            base = base.filter(dte__emisor_id=proveedor_id)

        base = base.filter(_filtro_tipo_diferencia(tipo))

        # --- KPI: una sola consulta agregada sobre TODO el universo filtrado ---
        resumen = base.aggregate(**_AGREGADOS_DIFERENCIAS)
        resumen['lineas'] = base.count()
        resumen['documentos'] = base.values('dte_id').distinct().count()
        esperadas = resumen['esperadas'] or 0
        problema = (resumen['faltantes'] or 0) + (resumen['danadas'] or 0)
        resumen['pct_diferencia'] = round(100 * problema / esperadas, 2) if esperadas else 0
        resumen['valor_problema'] = (resumen['valor_faltante'] or 0) + (resumen['valor_danado'] or 0)
        resumen['fecha_desde'] = desde.strftime('%Y-%m-%d')
        resumen['fecha_hasta'] = hasta.strftime('%Y-%m-%d')

        # --- Agrupaciones (GROUP BY en la base) ---
        por_sucursal = [
            {
                'sucursal_id': row['sucursal_destino_id'],
                'sucursal': row['sucursal_destino__alias'] or 'Sin asignar',
                'esperadas': row['esperadas'],
                'recibidas': row['recibidas'],
                'faltantes': row['faltantes'],
                'danadas': row['danadas'],
                'sobrantes': row['sobrantes'],
                'valor_problema': (row['valor_faltante'] or 0) + (row['valor_danado'] or 0),
                'pct_diferencia': (
                    round(100 * ((row['faltantes'] or 0) + (row['danadas'] or 0)) / row['esperadas'], 2)
                    if row['esperadas'] else 0
                ),
            }
            for row in (
                base.values('sucursal_destino_id', 'sucursal_destino__alias')
                .annotate(**_AGREGADOS_DIFERENCIAS)
                .order_by('-faltantes', '-danadas')[:TOPE_AGRUPACION]
            )
        ]

        por_proveedor = [
            {
                'proveedor_id': row['dte__emisor_id'],
                'proveedor': row['dte__emisor__nombre'] or 'Sin emisor',
                'rut': row['dte__emisor__rut'] or '',
                'esperadas': row['esperadas'],
                'recibidas': row['recibidas'],
                'faltantes': row['faltantes'],
                'danadas': row['danadas'],
                'sobrantes': row['sobrantes'],
                'valor_problema': (row['valor_faltante'] or 0) + (row['valor_danado'] or 0),
                'pct_diferencia': (
                    round(100 * ((row['faltantes'] or 0) + (row['danadas'] or 0)) / row['esperadas'], 2)
                    if row['esperadas'] else 0
                ),
            }
            for row in (
                base.values('dte__emisor_id', 'dte__emisor__nombre', 'dte__emisor__rut')
                .annotate(**_AGREGADOS_DIFERENCIAS)
                .order_by('-faltantes', '-danadas')[:TOPE_AGRUPACION]
            )
        ]

        # --- Detalle paginado (LIMIT/OFFSET real) ---
        detalle_qs = (
            base
            .annotate(costo_unitario=_costo_linea())
            .values(
                'id', 'dte_id',
                'dte__numero_documento', 'dte__tipo_documento', 'dte__estado_dte',
                'dte__fecha_emision', 'dte__emisor__nombre',
                'sucursal_destino_id', 'sucursal_destino__alias',
                'producto_talla__sku', 'producto_talla__talla',
                'producto_talla__producto__articulo', 'producto_talla__producto__descripcion',
                'cantidad_esperada', 'stockArribado', 'cantidad_faltante',
                'cantidad_danada', 'cantidad_sobrante', 'estado',
                'recepcionado_por', 'fecha_recepcion', 'fecha', 'observaciones',
                'costo_unitario',
            )
            .order_by(F('fecha_recepcion').desc(nulls_last=True), '-fecha', '-id')
        )

        paginator = Paginator(detalle_qs, page_size)
        page = paginator.get_page(page_num)

        datos = []
        for row in page.object_list:
            costo = row['costo_unitario'] or 0
            faltante = row['cantidad_faltante'] or 0
            danada = row['cantidad_danada'] or 0
            sobrante = row['cantidad_sobrante'] or 0
            fecha_rec = row['fecha_recepcion']
            datos.append({
                'id': row['id'],
                'dte_id': row['dte_id'],
                'folio': row['dte__numero_documento'],
                'tipo_documento': row['dte__tipo_documento'] or '',
                'estado_dte': row['dte__estado_dte'] or '',
                'fecha_emision': row['dte__fecha_emision'].strftime('%d/%m/%Y') if row['dte__fecha_emision'] else '',
                'proveedor': row['dte__emisor__nombre'] or '—',
                'sucursal_id': row['sucursal_destino_id'],
                'sucursal': row['sucursal_destino__alias'] or 'Sin asignar',
                'sku': str(row['producto_talla__sku']) if row['producto_talla__sku'] is not None else '—',
                'articulo': row['producto_talla__producto__articulo'] or '—',
                'descripcion': row['producto_talla__producto__descripcion'] or '',
                'talla': row['producto_talla__talla'] or '—',
                'esperado': row['cantidad_esperada'] or 0,
                'recibido': row['stockArribado'] or 0,
                'faltante': faltante,
                'danado': danada,
                'sobrante': sobrante,
                'estado': row['estado'] or '',
                'estado_label': ESTADOS_RECEPCION_LABEL.get(row['estado'], row['estado'] or ''),
                'recepcionado_por': row['recepcionado_por'] or '—',
                'fecha_recepcion': (
                    timezone.localtime(fecha_rec).strftime('%d/%m/%Y %H:%M')
                    if fecha_rec else (row['fecha'].strftime('%d/%m/%Y') if row['fecha'] else '—')
                ),
                'costo_unitario': costo,
                'valor_problema': (faltante + danada) * costo,
                'observaciones': (row['observaciones'] or '').strip(),
                'tiene_diferencia': bool(faltante or danada or sobrante),
            })

        return JsonResponse({
            'success': True,
            'resumen': resumen,
            'datos': datos,
            'por_sucursal': por_sucursal,
            'por_proveedor': por_proveedor,
            'proveedores': proveedores,
            'pagina_actual': page.number,
            'total_paginas': paginator.num_pages,
            'total_registros': paginator.count,
            'page_size': page_size,
        })

    except Exception:
        logger.exception('Error en reporte de diferencias despachado vs recepcionado')
        return JsonResponse(
            {'success': False, 'error': 'Error al generar el reporte de diferencias.'},
            status=500,
        )


# ---------------------------------------------------------------------------
# TAREA 2 — Mercadería en tránsito consolidada
# ---------------------------------------------------------------------------
#
# Reconstruida desde el kardex, igual que `_historial_despachos_reales`: el
# circuito diario NO crea filas en `Traspaso`. Se emite un DTE desde la bodega
# origen (escribe TRASPASO_SALIDA por SKU) y el destino lo ingresa desde
# Recepción de Documentos (escribe TRASPASO_ENTRADA contra el MISMO dte), así
# que el historial se agrupa por DTE.
#
#   pendientes = enviadas − recibidas − devueltas por NC
#
# Diferencias con el original: consolida TODAS las sucursales de las empresas
# del usuario (no sólo la de sesión), valoriza lo pendiente y permite abrir el
# detalle por SKU de lo que falta.


def _filtro_entradas_dte():
    """
    Q() de los movimientos que cuentan como RECIBIDO en el destino de un DTE:
    el traspaso normal (`CONCEPTOS_TRASPASO_ENTRADA`) más los cierres
    administrativos (`CONCEPTOS_CIERRE_RECEPCION`), estos últimos acotados a
    INGRESO COMPLETADO para no sumar sus reversas (EGRESO) ni movimientos
    anulados. Lo usan el listado y el detalle por SKU: los dos tienen que
    contar EXACTAMENTE las mismas entradas o dan pendientes distintos para el
    mismo documento.
    """
    return (
        Q(concepto__in=CONCEPTOS_TRASPASO_ENTRADA)
        | Q(concepto__in=CONCEPTOS_CIERRE_RECEPCION,
            tipo_movimiento='INGRESO', estado='COMPLETADO')
    )


def _clasificar_situacion(enviadas, recibidas, pendientes, dias):
    if recibidas > enviadas:
        # El destino ingresó más unidades de las que salieron: no es un
        # pendiente, es una descuadratura que hay que revisar.
        return 'SOBRE_RECIBIDO'
    if pendientes <= 0:
        return 'RECIBIDO'
    if dias <= DIAS_TRANSITO_NORMAL:
        return 'EN_TRANSITO'
    return 'SIN_RECIBIR'


def _costo_unitario_por_dte(dte_ids):
    """
    Costo unitario promedio de cada DTE = Σ(costo·unidades) / Σ(unidades).

    Una sola consulta agrupada para todos los documentos. Se usa para valorizar
    las unidades pendientes, que por definición no tienen línea de recepción
    contra la cual costear.
    """
    if not dte_ids:
        return {}
    costos = {}
    filas = (
        Dte_Productos.objects.filter(dte_id__in=dte_ids, activo=True)
        .values('dte_id')
        .annotate(
            valor=Coalesce(Sum(F('costo') * F('stock'), output_field=IntegerField()),
                           Value(0), output_field=IntegerField()),
            uds=Coalesce(Sum('stock'), Value(0), output_field=IntegerField()),
        )
        .order_by()
    )
    for fila in filas:
        uds = fila['uds'] or 0
        costos[fila['dte_id']] = int((fila['valor'] or 0) / uds) if uds else 0
    return costos


def _documentos_en_transito(request, dias, sucursal_origen_id=None, sucursal_destino_id=None):
    """
    Devuelve (documentos, hoy) con un dict por DTE despachado en la ventana.

    Todas las sumas se resuelven con GROUP BY en la base; el bucle de Python
    recorre DOCUMENTOS ya agregados (tope `TRANSITO_MAX_DOCUMENTOS`), no filas
    de kardex.
    """
    hoy = timezone.localdate()
    desde = hoy - timedelta(days=dias)
    suc_ids = _sucursal_ids(request)

    origen_filtro = [sucursal_origen_id] if sucursal_origen_id else suc_ids

    dte_ids = list(
        Movimientos_Producto.objects
        .filter(concepto='TRASPASO_SALIDA', dte__isnull=False,
                sucursal_origen_id__in=origen_filtro, fecha__gte=desde)
        .exclude(dte__descartado=True)
        .exclude(dte__estado_dte__in=ESTADOS_DTE_EXCLUIDOS)
        .order_by().values_list('dte_id', flat=True).distinct()[:TRANSITO_MAX_DOCUMENTOS]
    )
    if not dte_ids:
        return [], hoy, False

    movs_salida = Movimientos_Producto.objects.filter(
        concepto='TRASPASO_SALIDA', dte_id__in=dte_ids,
        sucursal_origen_id__in=origen_filtro,
    )

    # El corte por fecha elige los DOCUMENTOS; los totales se suman completos.
    # Un mismo DTE se despacha por partes en varios días: sumar sólo los
    # movimientos dentro de la ventana daba "enviadas" mutiladas.
    #
    # Se agrupa SÓLO por documento (los campos del DTE son dependientes
    # funcionales del id). Meter origen/destino en el mismo GROUP BY partía en
    # dos el documento que sale a más de un destino, y como las recibidas se
    # cuentan por DTE, cada mitad se llevaba el total completo de recibidas.
    salidas = (
        movs_salida
        .values('dte_id', 'dte__tipo_documento', 'dte__numero_documento', 'dte__estado_dte')
        .annotate(
            enviadas=Coalesce(Sum(Abs('cantidad')), Value(0), output_field=IntegerField()),
            items=Count('id'),
            fecha_salida=Min('fecha'),
        )
        .order_by('-fecha_salida')
    )

    # Ruta (origen → destino) de cada documento. Si por lo que sea tuviera más
    # de una, se muestra la que concentra más unidades y se marca `multi_ruta`.
    rutas_por_dte = {}
    destinos_por_dte = {}
    for r in (movs_salida
              .values('dte_id', 'sucursal_origen_id', 'sucursal_origen__alias',
                      'sucursal_destino_id', 'sucursal_destino__alias')
              .annotate(u=Coalesce(Sum(Abs('cantidad')), Value(0), output_field=IntegerField()))
              .order_by()):
        destinos_por_dte.setdefault(r['dte_id'], set()).add(r['sucursal_destino_id'])
        actual = rutas_por_dte.get(r['dte_id'])
        if actual is None or (r['u'] or 0) > actual['u']:
            rutas_por_dte[r['dte_id']] = r

    recibidas_por_dte = dict(
        Movimientos_Producto.objects
        .filter(_filtro_entradas_dte(), dte_id__in=dte_ids)
        .values_list('dte_id')
        .annotate(u=Sum(Abs('cantidad')))
        .order_by()
    )

    # Notas de crédito emitidas contra esos documentos: sus movimientos
    # reingresan la mercadería al origen, así que cierran la diferencia. Sin
    # descontarlas el panel alertaría por faltantes ya resueltos.
    nc_de_original = dict(
        Dte.objects.filter(documento_afectado_id__in=dte_ids)
        .values_list('id', 'documento_afectado_id')
    )
    devueltas_por_dte = {}
    if nc_de_original:
        for fila in (Movimientos_Producto.objects
                     .filter(dte_id__in=list(nc_de_original.keys()),
                             tipo_movimiento__in=TIPOS_MOVIMIENTO_ENTRADA)
                     .values('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()):
            original = nc_de_original.get(fila['dte_id'])
            if original:
                devueltas_por_dte[original] = devueltas_por_dte.get(original, 0) + (fila['u'] or 0)

    costos = _costo_unitario_por_dte(dte_ids)

    documentos = []
    for s in salidas:
        destinos = destinos_por_dte.get(s['dte_id'], set())
        if sucursal_destino_id and sucursal_destino_id not in destinos:
            continue
        ruta = rutas_por_dte.get(s['dte_id'], {})
        enviadas = s['enviadas'] or 0
        recibidas = recibidas_por_dte.get(s['dte_id'], 0) or 0
        devueltas = devueltas_por_dte.get(s['dte_id'], 0) or 0
        pendientes = max(0, enviadas - recibidas - devueltas)
        fecha_salida = s['fecha_salida']
        dias_transcurridos = (hoy - fecha_salida).days if fecha_salida else 0
        situacion = _clasificar_situacion(enviadas, recibidas, pendientes, dias_transcurridos)
        costo_unit = costos.get(s['dte_id'], 0)

        documentos.append({
            'dte_id': s['dte_id'],
            'tipo_documento': s['dte__tipo_documento'] or '',
            'folio': s['dte__numero_documento'],
            'estado_dte': s['dte__estado_dte'] or '',
            'origen_id': ruta.get('sucursal_origen_id'),
            'origen': ruta.get('sucursal_origen__alias') or '—',
            'destino_id': ruta.get('sucursal_destino_id'),
            'destino': ruta.get('sucursal_destino__alias') or '—',
            'multi_ruta': len(destinos) > 1,
            'fecha': fecha_salida.strftime('%d/%m/%Y') if fecha_salida else '',
            'fecha_iso': fecha_salida.strftime('%Y-%m-%d') if fecha_salida else '',
            'dias': dias_transcurridos,
            'items': s['items'],
            'enviadas': enviadas,
            'recibidas': recibidas,
            'devueltas_nc': devueltas,
            'pendientes': pendientes,
            'sobre_recibidas': max(0, recibidas - enviadas),
            'costo_unitario': costo_unit,
            'valor_pendiente': pendientes * costo_unit,
            'situacion': situacion,
        })

    truncado = len(dte_ids) >= TRANSITO_MAX_DOCUMENTOS
    return documentos, hoy, truncado


@requiere_permiso(PERMISO_TRANSITO)
@require_GET
def ver_reporte_mercaderia_transito(request):
    """Pantalla consolidada de mercadería en tránsito / sin recibir."""
    sucursales = [
        {'id': s.id, 'alias': s.alias or f'Sucursal {s.id}'}
        for s in obtener_sucursales_usuario(request.user)
    ]
    return render(request, 'vistas/modulo_reportes/reporte_mercaderia_transito.html', {
        'sucursales': sucursales,
        'umbral_transito': DIAS_TRANSITO_NORMAL,
        'dias_default': TRANSITO_DIAS_DEFAULT,
    })


@requiere_permiso(PERMISO_TRANSITO)
@require_GET
def api_reporte_mercaderia_transito(request):
    """
    JSON consolidado de mercadería en tránsito (todas las sucursales del usuario).

    Filtros: dias, origen_id, destino_id, situacion
    (EN_TRANSITO|SIN_RECIBIR|RECIBIDO|SOBRE_RECIBIDO|PENDIENTE), page.
    """
    try:
        dias = _entero(request.GET.get('dias'), TRANSITO_DIAS_DEFAULT, 1, TRANSITO_DIAS_MAX)
        situacion = (request.GET.get('situacion') or '').strip().upper()
        page_num = _entero(request.GET.get('page'), 1, 1)
        origen_id = _entero(request.GET.get('origen_id'), None)
        destino_id = _entero(request.GET.get('destino_id'), None)

        suc_ids = _sucursal_ids(request)
        if origen_id and origen_id not in suc_ids:
            return _sin_acceso('Esa sucursal de origen no pertenece a una empresa habilitada para ti.')
        if destino_id and destino_id not in suc_ids:
            return _sin_acceso('Esa sucursal de destino no pertenece a una empresa habilitada para ti.')

        documentos, _hoy, truncado = _documentos_en_transito(
            request, dias, sucursal_origen_id=origen_id, sucursal_destino_id=destino_id,
        )

        resumen = {
            'documentos': 0, 'unidades_enviadas': 0, 'unidades_recibidas': 0,
            'unidades_pendientes': 0, 'valor_pendiente': 0,
            'docs_sin_recibir': 0, 'unidades_sin_recibir': 0, 'valor_sin_recibir': 0,
            'docs_en_transito': 0, 'unidades_en_transito': 0,
            'docs_recibidos': 0,
            'docs_sobre_recibidos': 0, 'unidades_sobre_recibidas': 0,
            'antiguedad_max_dias': 0,
        }
        # El resumen se calcula sobre TODOS los documentos del período, no sólo
        # sobre los que quedan tras el filtro de situación: si al filtrar
        # "sin recibir" el KPI de enviadas también cambiara, no habría contra
        # qué comparar.
        for d in documentos:
            resumen['documentos'] += 1
            resumen['unidades_enviadas'] += d['enviadas']
            resumen['unidades_recibidas'] += d['recibidas']
            resumen['unidades_pendientes'] += d['pendientes']
            resumen['valor_pendiente'] += d['valor_pendiente']
            if d['situacion'] == 'SIN_RECIBIR':
                resumen['docs_sin_recibir'] += 1
                resumen['unidades_sin_recibir'] += d['pendientes']
                resumen['valor_sin_recibir'] += d['valor_pendiente']
                resumen['antiguedad_max_dias'] = max(resumen['antiguedad_max_dias'], d['dias'])
            elif d['situacion'] == 'EN_TRANSITO':
                resumen['docs_en_transito'] += 1
                resumen['unidades_en_transito'] += d['pendientes']
            elif d['situacion'] == 'SOBRE_RECIBIDO':
                resumen['docs_sobre_recibidos'] += 1
                resumen['unidades_sobre_recibidas'] += d['sobre_recibidas']
            else:
                resumen['docs_recibidos'] += 1

        # Consolidado por sucursal destino: dónde se está quedando la mercadería.
        por_destino = {}
        for d in documentos:
            clave = d['destino_id']
            bucket = por_destino.setdefault(clave, {
                'destino_id': clave, 'destino': d['destino'],
                'documentos': 0, 'enviadas': 0, 'recibidas': 0,
                'pendientes': 0, 'valor_pendiente': 0, 'docs_sin_recibir': 0,
                'antiguedad_max_dias': 0,
            })
            bucket['documentos'] += 1
            bucket['enviadas'] += d['enviadas']
            bucket['recibidas'] += d['recibidas']
            bucket['pendientes'] += d['pendientes']
            bucket['valor_pendiente'] += d['valor_pendiente']
            if d['situacion'] == 'SIN_RECIBIR':
                bucket['docs_sin_recibir'] += 1
                bucket['antiguedad_max_dias'] = max(bucket['antiguedad_max_dias'], d['dias'])
        por_destino = sorted(por_destino.values(), key=lambda b: -b['pendientes'])

        if situacion in ('SIN_RECIBIR', 'EN_TRANSITO', 'RECIBIDO', 'SOBRE_RECIBIDO'):
            filtrados = [d for d in documentos if d['situacion'] == situacion]
        elif situacion == 'PENDIENTE':
            filtrados = [d for d in documentos if d['pendientes'] > 0]
        else:
            filtrados = documentos

        # Lo que no llegó primero: es lo que hay que perseguir.
        if situacion in ('', 'PENDIENTE', 'SIN_RECIBIR'):
            filtrados = sorted(
                filtrados,
                key=lambda d: (0 if d['situacion'] == 'SIN_RECIBIR' else 1,
                               -d['pendientes'], -d['dias']),
            )

        paginator = Paginator(filtrados, TRANSITO_PAGE_SIZE)
        page = paginator.get_page(page_num)

        return JsonResponse({
            'success': True,
            'dias': dias,
            'umbral_transito': DIAS_TRANSITO_NORMAL,
            'despachos': list(page),
            'resumen': resumen,
            'por_destino': por_destino[:TOPE_AGRUPACION],
            'pagina_actual': page.number,
            'total_paginas': paginator.num_pages,
            'total_despachos': paginator.count,
            'truncado': truncado,
        })

    except Exception:
        logger.exception('Error en reporte consolidado de mercadería en tránsito')
        return JsonResponse(
            {'success': False, 'error': 'Error al generar el reporte de mercadería en tránsito.'},
            status=500,
        )


@requiere_permiso(PERMISO_TRANSITO)
@require_GET
def api_detalle_mercaderia_transito(request):
    """
    Detalle por SKU de un despacho: qué salió, qué entró y qué falta.

    El cruce va por SKU y NO por `Producto_Talla`: cada sucursal tiene sus
    propias filas de `Producto`/`Producto_Talla`, así que la salida del origen y
    la entrada del destino apuntan a IDs distintos aunque sean el mismo
    producto. El SKU es lo único común entre ambas puntas.
    """
    try:
        dte_id = _entero(request.GET.get('dte_id'), None)
        if not dte_id:
            return JsonResponse({'success': False, 'error': 'Falta dte_id.'}, status=400)

        suc_ids = _sucursal_ids(request)
        salidas_qs = Movimientos_Producto.objects.filter(
            concepto='TRASPASO_SALIDA', dte_id=dte_id,
        )
        # Alcance: el documento tiene que haber salido de (o llegado a) una
        # sucursal del usuario. Sin esto, cambiando el dte_id a mano se leía el
        # detalle de los despachos de otra empresa.
        if not salidas_qs.filter(
            Q(sucursal_origen_id__in=suc_ids) | Q(sucursal_destino_id__in=suc_ids)
        ).exists():
            return _sin_acceso()

        enviadas = (
            salidas_qs
            .values('ProductoTalla__sku', 'ProductoTalla__talla',
                    'ProductoTalla__producto__articulo',
                    'ProductoTalla__producto__descripcion')
            .annotate(u=Coalesce(Sum(Abs('cantidad')), Value(0), output_field=IntegerField()))
            .order_by()
        )

        recibidas = dict(
            Movimientos_Producto.objects
            .filter(_filtro_entradas_dte(), dte_id=dte_id)
            .values_list('ProductoTalla__sku')
            .annotate(u=Sum(Abs('cantidad')))
            .order_by()
        )

        nc_ids = list(
            Dte.objects.filter(documento_afectado_id=dte_id).values_list('id', flat=True)
        )
        devueltas = {}
        if nc_ids:
            devueltas = dict(
                Movimientos_Producto.objects
                .filter(dte_id__in=nc_ids, tipo_movimiento__in=TIPOS_MOVIMIENTO_ENTRADA)
                .values_list('ProductoTalla__sku')
                .annotate(u=Sum(Abs('cantidad')))
                .order_by()
            )

        # Costo unitario: el MISMO promedio del documento que usa el listado
        # (`_costo_unitario_por_dte`). Antes el detalle tomaba Max(costo) por
        # SKU y el listado el promedio, así que el mismo despacho se valorizaba
        # distinto según la pantalla (-12% medido en la auditoría ago-2026).
        # Un solo criterio ⇒ listado y detalle suman los mismos pesos.
        costo_doc = _costo_unitario_por_dte([dte_id]).get(dte_id, 0)

        filas = []
        totales = {'enviadas': 0, 'recibidas': 0, 'devueltas_nc': 0,
                   'pendientes': 0, 'valor_pendiente': 0}
        for row in enviadas:
            sku = row['ProductoTalla__sku']
            env = row['u'] or 0
            rec = recibidas.get(sku, 0) or 0
            dev = devueltas.get(sku, 0) or 0
            pend = max(0, env - rec - dev)
            costo = costo_doc
            filas.append({
                'sku': str(sku) if sku is not None else '—',
                'articulo': row['ProductoTalla__producto__articulo'] or '—',
                'descripcion': row['ProductoTalla__producto__descripcion'] or '',
                'talla': row['ProductoTalla__talla'] or '—',
                'enviadas': env,
                'recibidas': rec,
                'devueltas_nc': dev,
                'pendientes': pend,
                'sobre_recibidas': max(0, rec - env),
                'costo_unitario': costo,
                'valor_pendiente': pend * costo,
            })
            totales['enviadas'] += env
            totales['recibidas'] += rec
            totales['devueltas_nc'] += dev
            totales['pendientes'] += pend
            totales['valor_pendiente'] += pend * costo

        filas.sort(key=lambda f: (-f['pendientes'], f['articulo']))

        dte = Dte.objects.filter(id=dte_id).values(
            'numero_documento', 'tipo_documento', 'estado_dte', 'fecha_emision',
        ).first() or {}

        return JsonResponse({
            'success': True,
            'documento': {
                'dte_id': dte_id,
                'folio': dte.get('numero_documento'),
                'tipo_documento': dte.get('tipo_documento') or '',
                'estado_dte': dte.get('estado_dte') or '',
                'fecha_emision': (
                    dte['fecha_emision'].strftime('%d/%m/%Y') if dte.get('fecha_emision') else ''
                ),
            },
            'detalle': filas,
            'totales': totales,
        })

    except Exception:
        logger.exception('Error en detalle de mercadería en tránsito')
        return JsonResponse(
            {'success': False, 'error': 'Error al obtener el detalle del despacho.'},
            status=500,
        )
