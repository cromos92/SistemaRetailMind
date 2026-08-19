"""
Vistas del módulo Existencias — funcionalidades nuevas:
  1. Tarjeta de Movimiento por Producto
  2. Despacho a Todas Sucursales
  3. Trazabilidad Completa de Producto
  4. Modificación de Precios y Costos
"""
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.db import transaction
from django.db.models import (
    Q, F, Sum, Count, Case, When, Value, CharField, IntegerField,
    DecimalField, Prefetch, Subquery, OuterRef,
)
from django.db.models.functions import Abs, Coalesce, TruncDate
from django.utils import timezone
from django.core.paginator import Paginator

from .models import (
    Producto, Producto_Talla, Movimientos_Producto, LoteProducto,
    Sucursal, EmpresaUser, Traspaso, Traspaso_Detalle,
    PendienteDespacho, HistorialCambioPrecio,
    Dte, Dte_Productos, Productos_Recepcionados,
    Compras, Compras_Producto, Compras_Producto_Talla,
    CONCEPTO_MOVIMIENTO_CHOICES, TIPO_MOVIMIENTO_CHOICES,
)
from .utils_tallas import clave_orden_talla

logger = logging.getLogger('app')


# =====================================================
# 0. ALCANCE POR EMPRESA (scoping)
# =====================================================
#
# Varios endpoints de este módulo reciben un identificador del cliente (sku,
# producto_id, producto_talla_id, sucursal_destino_id) y lo resolvían contra
# TODA la base: con solo cambiar el número se veía o se movía mercadería de
# otra empresa. El alcance real de un usuario son las sucursales de las
# empresas donde tiene un EmpresaUser vigente (status=True); nunca lo que
# venga en la request ni lo que haya quedado en la sesión.


def _sucursales_usuario(request):
    """
    IDs de sucursal a los que el usuario tiene acceso (todas las de sus
    empresas con EmpresaUser.status=True). Se cachea por request porque
    varios endpoints lo consultan más de una vez.
    """
    cache = getattr(request, '_suc_ids_usuario_cache', None)
    if cache is not None:
        return cache

    empresa_ids = EmpresaUser.objects.filter(
        user=request.user, status=True
    ).values_list('empresa_id', flat=True)
    suc_ids = list(
        Sucursal.objects.filter(empresa_id__in=empresa_ids).values_list('id', flat=True)
    )
    request._suc_ids_usuario_cache = suc_ids
    return suc_ids


def _sin_acceso(mensaje='No tienes acceso a este dato: pertenece a otra empresa.'):
    return JsonResponse({'success': False, 'error': mensaje}, status=403)


# =====================================================
# 1. TARJETA DE MOVIMIENTO POR PRODUCTO
# =====================================================

@login_required
@require_GET
def tarjeta_movimiento_producto(request):
    """Vista principal: tarjeta de movimiento (vida completa del producto)."""
    sucursal_id = request.session.get('idSucursalActual')
    return render(request, 'vistas/modulo_existencias/tarjeta_movimiento_producto.html', {
        'sucursal_id': sucursal_id,
    })


# Signo del delta para el kardex, según tipo_movimiento.
# INGRESO/DEVOLUCION suman; EGRESO/PERDIDA restan. AJUSTE/DONACION/otros
# respetan el signo con que se guardó la cantidad.
#
# OJO: los traspasos NO llegan con tipo_movimiento='TRASPASO'. El save() del
# modelo autoclasifica TRASPASO_SALIDA→EGRESO y TRASPASO_ENTRADA→INGRESO, y
# son DOS movimientos sobre DOS Producto_Talla distintos (SKU origen y SKU
# destino), cada uno en su bodega. Por eso el saldo por serie (bodega, talla)
# es correcto vía las ramas ENTRADA/SALIDA. Para clasificar visualmente un
# movimiento COMO traspaso hay que mirar el CONCEPTO, no el tipo.
_TIPOS_ENTRADA = ('INGRESO', 'DEVOLUCION')
_TIPOS_SALIDA = ('EGRESO', 'PERDIDA')


def _es_traspaso(concepto):
    """True si el concepto corresponde a un movimiento de traspaso entre bodegas."""
    return concepto.startswith('TRASPASO') or concepto == 'REGULARIZACION_TRASPASO'


def _delta_kardex(movimiento):
    """
    Unidades con las que un movimiento afecta el saldo de su serie.

    Es la MISMA regla que usa la tarjeta de movimiento: INGRESO/DEVOLUCION
    suman, EGRESO/PERDIDA restan y el resto respeta el signo con que se guardó
    la cantidad. Se aísla en una función para que el saldo acumulado se calcule
    igual en trazabilidad, en la tarjeta y en las agregaciones SQL.
    """
    cantidad = movimiento.cantidad or 0
    if movimiento.tipo_movimiento in _TIPOS_ENTRADA:
        return abs(cantidad)
    if movimiento.tipo_movimiento in _TIPOS_SALIDA:
        return -abs(cantidad)
    return cantidad


# Versión SQL de `_delta_kardex`, para sumar saldos sin traer las filas.
DELTA_KARDEX_SQL = Case(
    When(tipo_movimiento__in=_TIPOS_ENTRADA, then=Abs('cantidad')),
    When(tipo_movimiento__in=_TIPOS_SALIDA, then=Abs('cantidad') * Value(-1)),
    default=F('cantidad'),
    output_field=IntegerField(),
)


def _empresa_ids_producto(producto, suc_ids_permitidos=None):
    """
    IDs de sucursal de la empresa dueña del producto (para acotar la búsqueda).

    Si se pasa `suc_ids_permitidos`, el resultado se intersecta con el alcance
    del usuario: un artículo puede existir en varias empresas del holding y
    solo deben verse las bodegas a las que el usuario tiene acceso.
    """
    empresa_id = producto.sucursal.empresa_id if producto.sucursal else None
    if empresa_id is None:
        sucursal_ids = [producto.sucursal_id] if producto.sucursal_id else []
    else:
        sucursal_ids = list(
            Sucursal.objects.filter(empresa_id=empresa_id).values_list('id', flat=True)
        )
    if suc_ids_permitidos is not None:
        permitidos = set(suc_ids_permitidos)
        sucursal_ids = [s for s in sucursal_ids if s in permitidos]
    return empresa_id, sucursal_ids


@login_required
@require_GET
def api_tarjeta_movimiento(request):
    """
    API: vida completa de un producto en TODAS las bodegas de su empresa.

    Un mismo artículo (código) existe como registros Producto distintos por
    sucursal, cada uno con sus Producto_Talla (SKUs) propios. Aquí se agrupa
    por artículo y se reconstruye:
      - Distribución de stock actual por bodega y por talla.
      - Kardex por (bodega, talla) con saldo acumulado correcto por serie.
      - Timeline unificada (nacimiento → traspasos → ventas → hoy).

    Parámetros GET: sku (SKU o código de artículo), fecha_desde, fecha_hasta.
    """
    sku = request.GET.get('sku', '').strip()
    fecha_desde = request.GET.get('fecha_desde', '')
    fecha_hasta = request.GET.get('fecha_hasta', '')
    sucursal_actual_id = request.session.get('idSucursalActual')

    if not sku:
        return JsonResponse({'success': False, 'error': 'Debe ingresar un SKU o código de artículo.'}, status=400)

    suc_ids_usuario = _sucursales_usuario(request)
    if not suc_ids_usuario:
        return _sin_acceso('Tu usuario no tiene empresas asignadas.')

    # 1) Resolver el artículo (código) a partir del SKU o del código directo,
    #    SIEMPRE dentro de las bodegas del usuario. Sin este filtro bastaba
    #    conocer un SKU ajeno para leer el kardex completo de otra empresa.
    def _buscar_pt(suc_ids):
        qs = Producto_Talla.objects.select_related('producto', 'producto__sucursal')
        if suc_ids is not None:
            qs = qs.filter(producto__sucursal_id__in=suc_ids)
        pt = qs.filter(sku=int(sku)).first() if sku.isdigit() else None
        if pt is None:
            pt = qs.filter(producto__articulo__iexact=sku).first()
        return pt

    pt_ref = _buscar_pt(suc_ids_usuario)
    if pt_ref is None:
        # Distinguir "no existe" de "existe pero es de otra empresa".
        if _buscar_pt(None) is not None:
            logger.warning(
                "Tarjeta de movimiento denegada: %s pidió el SKU/código «%s» fuera de su alcance",
                request.user.username, sku,
            )
            return _sin_acceso('Ese SKU pertenece a una empresa a la que no tienes acceso.')
        return JsonResponse({'success': False, 'error': f'SKU o código «{sku}» no encontrado.'}, status=404)

    articulo = pt_ref.producto.articulo
    empresa_id, sucursal_ids = _empresa_ids_producto(pt_ref.producto, suc_ids_usuario)

    # 2) Todas las variantes (talla × sucursal) de este artículo en la empresa.
    productos_talla = list(
        Producto_Talla.objects
        .select_related(
            'producto', 'producto__sucursal',
            'producto__atributo1', 'producto__atributo2', 'producto__categoria',
        )
        .filter(producto__articulo=articulo, producto__sucursal_id__in=sucursal_ids)
        .order_by('producto__sucursal__alias', 'talla')
    )
    pt_ids = [pt.id for pt in productos_talla]

    # 3) Movimientos de todas esas variantes.
    movimientos_qs = (
        Movimientos_Producto.objects
        .filter(ProductoTalla_id__in=pt_ids)
        .select_related(
            'ProductoTalla', 'ProductoTalla__producto', 'ProductoTalla__producto__sucursal',
            'sucursal_origen', 'sucursal_destino', 'dte', 'dte__emisor', 'ticket',
        )
    )
    if fecha_desde:
        movimientos_qs = movimientos_qs.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        movimientos_qs = movimientos_qs.filter(fecha__lte=fecha_hasta)

    movimientos = list(movimientos_qs.order_by('fecha', 'hora', 'id'))

    # 3.b) SALDO DE APERTURA por serie (bodega·talla).
    #      Con filtro "Desde" el kardex arrancaba en 0: lo anterior al rango
    #      existe, solo que no se mostraba, así que el saldo final nunca cuadraba
    #      con el stock. Se suma en SQL todo lo anterior a la fecha inicial y esa
    #      cifra es el punto de partida de cada serie.
    saldos_por_serie = {}
    aperturas = []
    if fecha_desde:
        alias_por_sucursal = {
            pt.producto.sucursal_id: (pt.producto.sucursal.alias if pt.producto.sucursal else '-')
            for pt in productos_talla
        }
        sku_por_serie = {
            (pt.producto.sucursal_id, pt.talla): str(pt.sku) for pt in productos_talla
        }
        previos = (
            Movimientos_Producto.objects
            .filter(ProductoTalla_id__in=pt_ids, fecha__lt=fecha_desde)
            .values('ProductoTalla__producto__sucursal_id', 'ProductoTalla__talla')
            .annotate(saldo=Sum(DELTA_KARDEX_SQL))
            .order_by()
        )
        for fila in previos:
            suc_id = fila['ProductoTalla__producto__sucursal_id']
            talla_prev = fila['ProductoTalla__talla']
            saldo_previo = fila['saldo'] or 0
            serie_prev = (suc_id, talla_prev)
            saldos_por_serie[serie_prev] = saldo_previo
            aperturas.append({
                'bodega_id': suc_id,
                'bodega': alias_por_sucursal.get(suc_id, '-'),
                'talla': talla_prev,
                'sku': sku_por_serie.get(serie_prev, ''),
                'saldo': saldo_previo,
            })
        aperturas.sort(key=lambda a: (a['bodega'], a['talla']))

    # 4) Kardex por serie (bodega dueña del SKU + talla) con saldo correcto.
    #    Cada Producto_Talla vive en UNA sucursal (producto.sucursal): esa es la
    #    "bodega" del saldo. En un TRASPASO, el movimiento con cantidad negativa
    #    es una salida de esa bodega; el positivo, una entrada.
    movimientos_data = []
    for m in movimientos:
        pt = m.ProductoTalla
        bodega = pt.producto.sucursal
        bodega_alias = bodega.alias if bodega else '-'
        talla = pt.talla
        serie = (pt.producto.sucursal_id, talla)

        delta = _delta_kardex(m)
        saldos_por_serie[serie] = saldos_por_serie.get(serie, 0) + delta

        es_entrada = delta > 0
        movimientos_data.append({
            'id': m.id,
            'sku': str(pt.sku),
            'talla': talla,
            'bodega': bodega_alias,
            'bodega_id': pt.producto.sucursal_id,
            'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
            'fecha_iso': m.fecha.strftime('%Y-%m-%d') if m.fecha else '',
            'hora': m.hora.strftime('%H:%M') if m.hora else '',
            'tipo_movimiento': m.tipo_movimiento,
            'concepto': m.concepto,
            'concepto_display': m.get_concepto_display(),
            'cantidad': delta,
            'es_entrada': es_entrada,
            'es_traspaso': _es_traspaso(m.concepto),
            'costo': m.costo,
            'precio': m.precio,
            'saldo': saldos_por_serie[serie],
            'sucursal_origen': m.sucursal_origen.alias if m.sucursal_origen else '-',
            'sucursal_destino': m.sucursal_destino.alias if m.sucursal_destino else '-',
            'responsable': m.responsable,
            'observaciones': m.observaciones or '',
            'referencia': m.referencia_externa or '',
            'dte_folio': (
                getattr(m.dte, 'folio', None) or getattr(m.dte, 'numero_documento', None)
                if m.dte else None
            ),
            'ticket_correlativo': m.ticket.correlativo if m.ticket else None,
            # Proveedor solo en documentos de COMPRA: en un traspaso o una venta
            # el emisor es la propia empresa y ponerlo como "origen" confunde.
            'proveedor': (
                m.dte.emisor.nombre
                if m.dte and m.dte.tipo_transaccion == 'COMPRA' and m.dte.emisor else ''
            ),
        })

    # 5) Info consolidada del producto (usa la variante de referencia para atributos).
    producto = pt_ref.producto
    bodegas_presentes = sorted({
        pt.producto.sucursal.alias for pt in productos_talla if pt.producto.sucursal
    })
    stock_total = sum(pt.stock or 0 for pt in productos_talla)

    producto_info = {
        'articulo': articulo,
        'descripcion': producto.descripcion,
        'marca': producto.atributo1.valor if producto.atributo1 else '-',
        'color': producto.atributo2.valor if producto.atributo2 else '-',
        'categoria': producto.categoria.nombre if producto.categoria else '-',
        'costo': producto.costo,
        'precio_venta': producto.precioventa,
        'stock_total': stock_total,
        'num_bodegas': len(bodegas_presentes),
        'num_skus': len(productos_talla),
        'bodegas': bodegas_presentes,
    }

    # 6) Distribución de stock actual por bodega (con desglose por talla).
    distribucion = {}
    for pt in productos_talla:
        bodega = pt.producto.sucursal
        if not bodega:
            continue
        key = bodega.id
        if key not in distribucion:
            distribucion[key] = {
                'bodega_id': bodega.id,
                'bodega': bodega.alias,
                'tipo': bodega.get_tipo_sucursal_display(),
                'es_cd': bodega.es_compradora,
                'stock_total': 0,
                'tallas': [],
            }
        distribucion[key]['stock_total'] += pt.stock or 0
        distribucion[key]['tallas'].append({
            'sku': str(pt.sku),
            'talla': pt.talla,
            'stock': pt.stock or 0,
        })
    # Tiendas primero y, dentro de cada grupo, las que más stock tienen: en la
    # matriz talla × sucursal se lee de arriba hacia abajo "dónde hay".
    distribucion_list = sorted(
        distribucion.values(), key=lambda d: (d['es_cd'], -d['stock_total'], d['bodega'])
    )
    for d in distribucion_list:
        # Orden natural de tallas ('7,5' antes que '38', y las letras al final).
        d['tallas'].sort(key=lambda t: clave_orden_talla(t['talla']))

    # 7) Tallas y bodegas disponibles para poblar los filtros del frontend.
    tallas_disponibles = sorted({pt.talla for pt in productos_talla}, key=clave_orden_talla)
    bodegas_disponibles = [
        {'id': d['bodega_id'], 'alias': d['bodega']} for d in distribucion_list
    ]

    # 8) Timeline: hitos clave del recorrido (nacimiento, traspasos, ventas, ajustes).
    #    Si hay muchísimos, se envían solo los más recientes (los últimos son los
    #    más relevantes para "la vida reciente"); el frontend avisa cuántos se omiten.
    timeline_full = _construir_timeline(movimientos_data)
    timeline_total = len(timeline_full)
    timeline = timeline_full[-_TIMELINE_MAX:] if timeline_total > _TIMELINE_MAX else timeline_full
    timeline_omitidos = timeline_total - len(timeline)

    # 8.b) LLEGADAS: cada vez que entró mercadería a una bodega, agrupada por
    #      evento (fecha + bodega + documento) con su desglose de tallas. Es la
    #      pregunta que más se hace en tienda: "¿cuándo llegó y qué tallas vinieron?".
    llegadas = _construir_llegadas(movimientos_data)

    # 9) Resumen global.
    #    Entradas/salidas EXCLUYEN traspasos: un traspaso interno es una salida
    #    en una bodega + una entrada en otra por la misma unidad; contarlo aquí
    #    inflaría ambos totales por movimiento de mercadería que no entra ni sale
    #    de la empresa. Los traspasos se cuentan aparte.
    total_entradas = sum(
        m['cantidad'] for m in movimientos_data
        if m['cantidad'] > 0 and not m['es_traspaso']
    )
    total_salidas = sum(
        -m['cantidad'] for m in movimientos_data
        if m['cantidad'] < 0 and not m['es_traspaso']
    )
    total_vendido = sum(
        -m['cantidad'] for m in movimientos_data
        if m['concepto'].startswith('VENTA')
    )
    # Un traspaso = par (salida origen + entrada destino) el mismo día. Se
    # deduplica por (fecha, origen, destino, cantidad) para no contarlo doble.
    traspasos_count = len({
        (m['fecha'], m['sucursal_origen'], m['sucursal_destino'], abs(m['cantidad']))
        for m in movimientos_data if m['es_traspaso']
    })

    saldo_apertura_total = sum(a['saldo'] for a in aperturas)
    saldo_final_total = sum(saldos_por_serie.values())

    # Última llegada y última venta: los dos datos que resumen si el artículo
    # sigue vivo (llega mercadería) y si rota (se vende).
    hoy = timezone.localdate()
    ultima_llegada = llegadas[0] if llegadas else None
    ventas = [m for m in movimientos_data if m['concepto'].startswith('VENTA')]
    ultima_venta = ventas[-1] if ventas else None

    resumen = {
        'total_movimientos': len(movimientos_data),
        'total_entradas': total_entradas,
        'total_salidas': total_salidas,
        'total_vendido': total_vendido,
        'traspasos': traspasos_count,
        'stock_actual': stock_total,
        'saldo_apertura': saldo_apertura_total,
        'saldo_final': saldo_final_total,
        'primer_movimiento': movimientos_data[0]['fecha'] if movimientos_data else '-',
        'ultimo_movimiento': movimientos_data[-1]['fecha'] if movimientos_data else '-',
        'num_llegadas': len(llegadas),
        'unidades_llegadas': sum(l['unidades'] for l in llegadas),
        'ultima_llegada': ultima_llegada['fecha'] if ultima_llegada else '',
        'ultima_llegada_dias': _dias_desde(ultima_llegada['fecha_iso'], hoy) if ultima_llegada else None,
        'ultima_llegada_bodega': ultima_llegada['bodega'] if ultima_llegada else '',
        'ultima_venta': ultima_venta['fecha'] if ultima_venta else '',
        'ultima_venta_dias': _dias_desde(ultima_venta['fecha_iso'], hoy) if ultima_venta else None,
    }

    return JsonResponse({
        'success': True,
        'producto': producto_info,
        'movimientos': movimientos_data,
        'aperturas': aperturas,
        'distribucion': distribucion_list,
        'llegadas': llegadas,
        'timeline': timeline,
        'timeline_omitidos': timeline_omitidos,
        'resumen': resumen,
        'rango': {'desde': fecha_desde or '', 'hasta': fecha_hasta or ''},
        'filtros': {
            'tallas': tallas_disponibles,
            'bodegas': bodegas_disponibles,
        },
        'sucursal_actual_id': sucursal_actual_id,
    })


def _dias_desde(fecha_iso, hoy):
    """Días transcurridos entre `fecha_iso` (YYYY-MM-DD) y hoy. None si no parsea."""
    if not fecha_iso:
        return None
    try:
        fecha = datetime.strptime(fecha_iso, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None
    return (hoy - fecha).days


# Cómo se presenta cada tipo de llegada. Clave = concepto del movimiento.
# (tipo para el filtro, título, icono, color)
_LLEGADAS_PRESENTACION = {
    'RECEPCION_COMPRA':        ('COMPRA', 'Recepción de compra', 'ri-inbox-archive-line', '#0ab39c'),
    'REPOSICION_STOCK':        ('COMPRA', 'Reposición de stock', 'ri-refresh-line', '#0ab39c'),
    'INGRESO_INICIAL':         ('INICIAL', 'Carga inicial del sistema', 'ri-seedling-line', '#405189'),
    'INGRESO_MANUAL':          ('MANUAL', 'Ingreso manual', 'ri-edit-box-line', '#405189'),
    'DEVOLUCION_CLIENTE':      ('DEVOLUCION', 'Devolución de cliente', 'ri-arrow-go-back-line', '#299cdb'),
    'DEVOLUCION_NC':           ('DEVOLUCION', 'Devolución por nota de crédito', 'ri-arrow-go-back-line', '#299cdb'),
    'DEVOLUCION_NC_POST_RECEPCION': ('DEVOLUCION', 'Devolución NC tras recepción', 'ri-arrow-go-back-line', '#299cdb'),
    'CAMBIO_PRODUCTO_ENTRADA': ('DEVOLUCION', 'Cambio de producto (entrada)', 'ri-swap-line', '#299cdb'),
    'AJUSTE_POSITIVO':         ('AJUSTE', 'Ajuste positivo', 'ri-scales-3-line', '#f7b84b'),
    'AJUSTE_INVENTARIO_ENTRADA': ('AJUSTE', 'Sobrante de inventario', 'ri-scales-3-line', '#f7b84b'),
    'CORRECCION_STOCK':        ('AJUSTE', 'Corrección de stock', 'ri-scales-3-line', '#f7b84b'),
    'SOBRANTE_INGRESO':        ('AJUSTE', 'Sobrante aceptado', 'ri-scales-3-line', '#f7b84b'),
    'RECEPCION_SOBRANTE':      ('AJUSTE', 'Sobrante en recepción', 'ri-scales-3-line', '#f7b84b'),
    'DONACION_RECIBIDA':       ('OTRO', 'Donación recibida', 'ri-gift-line', '#299cdb'),
}
_LLEGADA_TRASPASO = ('TRASPASO', 'Traspaso recibido', 'ri-truck-line', '#f7b84b')
_LLEGADA_DEFAULT = ('OTRO', '', 'ri-add-circle-line', '#299cdb')


def _construir_llegadas(movimientos_data):
    """
    "Llegadas": cada vez que ENTRÓ mercadería a una bodega, como un solo evento.

    Un movimiento por talla no sirve para responder "¿cuándo llegó?": una
    recepción de 40 pares genera 8 movimientos. Aquí se agrupan por
    (fecha · bodega · concepto · documento · origen) y se devuelve el desglose
    por talla dentro del evento, ordenado del más reciente al más antiguo.

    Se excluyen las ventas (nunca son llegadas) y los movimientos con cantidad
    0 (registros documentales como REASIGNACION_DESTINO).
    """
    hoy = timezone.localdate()
    grupos = {}

    for m in movimientos_data:
        if m['cantidad'] <= 0 or m['concepto'].startswith('VENTA'):
            continue

        if m['es_traspaso']:
            tipo, titulo, icono, color = _LLEGADA_TRASPASO
        else:
            tipo, titulo, icono, color = _LLEGADAS_PRESENTACION.get(
                m['concepto'], _LLEGADA_DEFAULT
            )
        titulo = titulo or m['concepto_display']

        documento = (
            f"DTE #{m['dte_folio']}" if m['dte_folio']
            else f"Ticket #{m['ticket_correlativo']}" if m['ticket_correlativo']
            else (m['referencia'] or '')
        )
        # De dónde vino: proveedor en las compras, sucursal emisora en traspasos.
        origen = m['proveedor'] or (
            m['sucursal_origen'] if m['sucursal_origen'] not in ('-', '') else ''
        )

        clave = (m['fecha_iso'], m['bodega_id'], m['concepto'], documento, origen)
        grupo = grupos.get(clave)
        if grupo is None:
            grupo = grupos[clave] = {
                'fecha': m['fecha'],
                'fecha_iso': m['fecha_iso'],
                'hora': m['hora'],
                'dias': _dias_desde(m['fecha_iso'], hoy),
                'tipo': tipo,
                'titulo': titulo,
                'icono': icono,
                'color': color,
                'bodega': m['bodega'],
                'bodega_id': m['bodega_id'],
                'origen': origen,
                'documento': documento,
                'responsable': m['responsable'],
                'unidades': 0,
                'costo_total': 0,
                'tallas': {},
            }
        grupo['unidades'] += m['cantidad']
        grupo['costo_total'] += (m['costo'] or 0) * m['cantidad']
        grupo['tallas'][m['talla']] = grupo['tallas'].get(m['talla'], 0) + m['cantidad']

    llegadas = []
    for g in grupos.values():
        tallas = sorted(g.pop('tallas').items(), key=lambda par: clave_orden_talla(par[0]))
        costo_total = g.pop('costo_total')
        g['tallas'] = [{'talla': t, 'cantidad': c} for t, c in tallas]
        g['num_tallas'] = len(tallas)
        g['costo_unitario'] = round(costo_total / g['unidades']) if g['unidades'] else 0
        llegadas.append(g)

    # Más reciente primero: en tienda se pregunta por lo último que llegó.
    llegadas.sort(key=lambda l: (l['fecha_iso'], l['hora']), reverse=True)
    return llegadas


def _construir_timeline(movimientos_data):
    """
    Línea de tiempo de HITOS del producto (no un volcado de cada movimiento).

    - Nacimiento, recepciones e ingresos manuales → un hito cada uno.
    - Traspasos → un hito por par (salida + entrada del mismo día).
    - Ventas → NO se listan una a una (pueden ser miles). Se AGRUPAN por
      mes + bodega en un solo hito con el total vendido y el nº de operaciones.
    - Ajustes / pérdidas / devoluciones → un hito cada uno.

    Devuelve la lista ordenada cronológicamente (ascendente).
    """
    hitos = []
    traspasos_vistos = set()
    ventas_agrupadas = {}  # (mes, bodega, tipo) -> acumulador

    for m in movimientos_data:
        concepto = m['concepto']
        tipo = m['tipo_movimiento']

        # --- Traspasos (detectados por concepto; van como INGRESO/EGRESO) ---
        if m['es_traspaso']:
            clave = (m['fecha'], m['sucursal_origen'], m['sucursal_destino'], abs(m['cantidad']))
            if clave in traspasos_vistos:
                continue  # el par salida/entrada ya generó su hito
            traspasos_vistos.add(clave)
            hitos.append({
                'fecha': m['fecha'], 'fecha_iso': m['fecha_iso'], 'hora': m['hora'],
                'tipo': 'traspaso', 'icono': 'ri-truck-line', 'color': '#FFC107',
                'titulo': 'Traspaso entre bodegas',
                'detalle': f"{m['sucursal_origen']} → {m['sucursal_destino']} · {abs(m['cantidad'])} u · Talla {m['talla']}",
                'orden': m['fecha_iso'] + (m['hora'] or ''),
                'responsable': m['responsable'],
            })
            continue

        # --- Ventas: agrupar por mes + bodega (pueden ser miles) ---
        if concepto.startswith('VENTA'):
            mes = m['fecha_iso'][:7] if m['fecha_iso'] else m['fecha']
            gkey = (mes, m['bodega'], m['concepto_display'])
            g = ventas_agrupadas.get(gkey)
            if g is None:
                ventas_agrupadas[gkey] = {
                    'mes': mes, 'bodega': m['bodega'], 'concepto': m['concepto_display'],
                    'unidades': abs(m['cantidad']), 'operaciones': 1,
                    'primera_fecha': m['fecha'], 'fecha_iso': m['fecha_iso'],
                }
            else:
                g['unidades'] += abs(m['cantidad'])
                g['operaciones'] += 1
            continue

        # --- Nacimiento / ingresos (recepción, reposición, manual) ---
        if tipo == 'INGRESO' or concepto.startswith(('INGRESO', 'RECEPCION', 'REPOSICION')):
            if concepto in ('INGRESO_INICIAL', 'INGRESO_MANUAL'):
                titulo, icono = 'Nacimiento del producto', 'ri-seedling-line'
            elif concepto == 'RECEPCION_COMPRA':
                titulo, icono = 'Recepción de compra', 'ri-inbox-archive-line'
            else:
                titulo, icono = m['concepto_display'], 'ri-add-circle-line'
            hitos.append({
                'fecha': m['fecha'], 'fecha_iso': m['fecha_iso'], 'hora': m['hora'],
                'tipo': 'ingreso', 'icono': icono, 'color': '#00D4AA',
                'titulo': titulo,
                'detalle': f"+{abs(m['cantidad'])} u · {m['bodega']} · Talla {m['talla']}",
                'orden': m['fecha_iso'] + (m['hora'] or ''),
                'responsable': m['responsable'],
            })
            continue

        # --- Ajustes / pérdidas / devoluciones / otros ---
        color = '#0066FF' if m['es_entrada'] else '#FF6B6B'
        signo = '+' if m['es_entrada'] else '−'
        hitos.append({
            'fecha': m['fecha'], 'fecha_iso': m['fecha_iso'], 'hora': m['hora'],
            'tipo': 'ajuste', 'icono': 'ri-edit-2-line', 'color': color,
            'titulo': m['concepto_display'],
            'detalle': f"{signo}{abs(m['cantidad'])} u · {m['bodega']} · Talla {m['talla']}",
            'orden': m['fecha_iso'] + (m['hora'] or ''),
            'responsable': m['responsable'],
        })

    # Volcar las ventas agrupadas como hitos mensuales.
    for g in ventas_agrupadas.values():
        etiqueta_mes = g['mes'] if len(g['mes']) == 7 else g['primera_fecha']
        hitos.append({
            'fecha': etiqueta_mes, 'fecha_iso': g['fecha_iso'], 'hora': '',
            'tipo': 'venta', 'icono': 'ri-shopping-bag-3-line', 'color': '#FF6B6B',
            'titulo': f"{g['concepto']} · {g['bodega']}",
            'detalle': f"−{g['unidades']} u en {g['operaciones']} venta(s)",
            'orden': (g['fecha_iso'][:7] if g['fecha_iso'] else '') + '~',  # fin de mes
            'responsable': '',
        })

    hitos.sort(key=lambda h: h.get('orden', ''))
    for h in hitos:
        h.pop('orden', None)
    return hitos


# Tope de hitos que se envían al frontend para mantener la timeline legible.
_TIMELINE_MAX = 150


@login_required
@require_GET
def api_buscar_productos_tarjeta_movimiento(request):
    """
    Autocomplete: sugiere artículos (agrupados por código) por SKU, código o
    descripción. Un artículo puede existir en varias bodegas; se muestra una
    fila por código con el total de bodegas donde aparece.
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'success': True, 'productos': []})

    suc_ids_usuario = _sucursales_usuario(request)
    if not suc_ids_usuario:
        return JsonResponse({'success': True, 'productos': []})

    filtro = (
        Q(producto__articulo__icontains=q) |
        Q(producto__descripcion__icontains=q)
    )
    if q.isdigit():
        filtro |= Q(sku__icontains=q)

    productos_talla = (
        Producto_Talla.objects
        .filter(producto__sucursal_id__in=suc_ids_usuario)
        .filter(filtro)
        .select_related('producto', 'producto__atributo1', 'producto__sucursal')
        .order_by('producto__articulo', 'talla')[:60]
    )

    # Agrupar por código de artículo para no repetir la misma prenda por bodega.
    agrupados = {}
    for pt in productos_talla:
        art = pt.producto.articulo
        if art not in agrupados:
            agrupados[art] = {
                'sku': str(pt.sku),  # SKU de muestra para lanzar la búsqueda
                'articulo': art,
                'descripcion': pt.producto.descripcion,
                'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '-',
                'bodegas': set(),
                'stock': 0,
            }
        if pt.producto.sucursal:
            agrupados[art]['bodegas'].add(pt.producto.sucursal.alias)
        agrupados[art]['stock'] += pt.stock or 0

    resultados = []
    for a in list(agrupados.values())[:15]:
        resultados.append({
            'sku': a['sku'],
            'articulo': a['articulo'],
            'descripcion': a['descripcion'],
            'marca': a['marca'],
            'num_bodegas': len(a['bodegas']),
            'stock': a['stock'],
        })

    return JsonResponse({'success': True, 'productos': resultados})


# =====================================================
# 2. DESPACHO A TODAS SUCURSALES
# =====================================================

@login_required
@require_GET
def despacho_todas_sucursales(request):
    """Vista principal: despacho masivo a todas las sucursales."""
    sucursal_id = request.session.get('idSucursalActual')
    empresa_id = request.session.get('idEmpresaActual')
    return render(request, 'vistas/modulo_existencias/despacho_sucursales.html', {
        'sucursal_id': sucursal_id,
        'empresa_id': empresa_id,
    })


@login_required
@require_GET
def api_obtener_sucursales_despacho(request):
    """Devuelve las sucursales destino disponibles para despacho."""
    empresa_id = request.session.get('idEmpresaActual')
    sucursal_actual = request.session.get('idSucursalActual')

    # La empresa activa viene de la sesión: se acota igual al alcance real del
    # usuario para no listar bodegas de una empresa que ya no tiene asignada.
    sucursales = Sucursal.objects.filter(
        empresa_id=empresa_id,
        id__in=_sucursales_usuario(request),
    ).exclude(id=sucursal_actual).values('id', 'alias', 'direccion', 'ciudad')

    return JsonResponse({
        'success': True,
        'sucursales': list(sucursales),
    })


def _serializar_producto_despacho(pt):
    return {
        'producto_talla_id': pt.id,
        'sku': str(pt.sku),
        'articulo': pt.producto.articulo,
        'descripcion': pt.producto.descripcion,
        'talla': pt.talla,
        'stock': pt.stock,
        'costo': pt.producto.costo,
        'sobreprecio': pt.producto.sobreprecio,
        'precio_venta': pt.producto.precioventa,
        'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '-',
    }


_DESPACHO_LIMITE_TRAER_TODO = 2000
_PENDIENTES_ITEMS_POR_SUCURSAL = 25


@login_required
@require_GET
def api_productos_disponibles_despacho(request):
    """
    Busca productos disponibles para despacho desde la sucursal actual.
    Parámetros: q (texto búsqueda), marca_id, categoria_id, page, traer_todo (1/0).
    """
    sucursal_id = request.session.get('idSucursalActual')
    q = request.GET.get('q', '').strip()
    marca_id = request.GET.get('marca_id') or None
    categoria_id = request.GET.get('categoria_id') or None
    traer_todo = request.GET.get('traer_todo') == '1'
    page_num = _entero(request.GET.get('page'), 1, 1)

    productos_qs = Producto_Talla.objects.filter(
        producto__sucursal_id=sucursal_id,
        stock__gt=0,
    ).select_related('producto', 'producto__atributo1', 'producto__categoria')

    if q:
        productos_qs = productos_qs.filter(
            Q(sku__icontains=q) |
            Q(producto__articulo__icontains=q) |
            Q(producto__descripcion__icontains=q)
        )
    if marca_id:
        productos_qs = productos_qs.filter(producto__atributo1_id=marca_id)
    if categoria_id:
        productos_qs = productos_qs.filter(producto__categoria_id=categoria_id)

    productos_qs = productos_qs.order_by('producto__articulo', 'talla')

    if traer_todo:
        productos_lista = list(productos_qs[:_DESPACHO_LIMITE_TRAER_TODO + 1])
        truncado = len(productos_lista) > _DESPACHO_LIMITE_TRAER_TODO
        productos_lista = productos_lista[:_DESPACHO_LIMITE_TRAER_TODO]
        return JsonResponse({
            'success': True,
            'productos': [_serializar_producto_despacho(pt) for pt in productos_lista],
            'total_productos': len(productos_lista),
            'truncado': truncado,
            'modo': 'completo',
        })

    paginator = Paginator(productos_qs, 50)
    page = paginator.get_page(page_num)

    return JsonResponse({
        'success': True,
        'productos': [_serializar_producto_despacho(pt) for pt in page],
        'pagina_actual': page.number,
        'total_paginas': paginator.num_pages,
        'total_productos': paginator.count,
        'modo': 'paginado',
    })


@login_required
@require_GET
def api_marcas_disponibles_despacho(request):
    """Marcas (atributo1) con stock disponible en la sucursal actual, para poblar el filtro."""
    sucursal_id = request.session.get('idSucursalActual')

    marcas = (
        Producto_Talla.objects
        .filter(producto__sucursal_id=sucursal_id, stock__gt=0, producto__atributo1__isnull=False)
        .values('producto__atributo1_id', 'producto__atributo1__valor')
        .annotate(total=Count('id'))
        .order_by('producto__atributo1__valor')
    )

    return JsonResponse({
        'success': True,
        'marcas': [
            {
                'id': m['producto__atributo1_id'],
                'nombre': m['producto__atributo1__valor'],
                'productos': m['total'],
            }
            for m in marcas
        ],
    })


@login_required
@require_GET
def api_pendientes_despacho_sucursal(request):
    """
    Pendientes de despacho (mercadería comprada para otra sucursal que sigue en
    la bodega) agrupados por sucursal destino.

    Se devuelve como máximo `_PENDIENTES_ITEMS_POR_SUCURSAL` líneas por destino
    —hay más de mil abiertas en producción y volcarlas todas en la tarjeta
    dejaba la pantalla inservible—, ordenadas de la más antigua a la más nueva,
    que es el orden en que hay que despacharlas.
    """
    sucursal_id = request.session.get('idSucursalActual')
    if not sucursal_id or sucursal_id not in _sucursales_usuario(request):
        return _sin_acceso('Tu sucursal activa no pertenece a una empresa habilitada para ti.')

    hoy = timezone.localdate()
    pendientes = PendienteDespacho.objects.filter(
        sucursal_origen_id=sucursal_id,
        estado__in=['PENDIENTE', 'PARCIAL'],
    ).select_related(
        'producto_talla', 'producto_talla__producto',
        'producto_talla__producto__atributo1',
        'sucursal_destino', 'dte_origen',
    ).order_by('created_at')

    por_sucursal = {}
    total_unidades = 0
    total_lineas = 0
    for p in pendientes:
        suc_alias = p.sucursal_destino.alias if p.sucursal_destino_id else '—'
        grupo = por_sucursal.get(suc_alias)
        if grupo is None:
            grupo = por_sucursal[suc_alias] = {
                'sucursal_id': p.sucursal_destino_id,
                'alias': suc_alias,
                'items': [],
                'total_unidades': 0,
                'total_lineas': 0,
                'dias_mas_antiguo': 0,
                'truncado': False,
            }
        restante = p.cantidad_restante
        dias = (hoy - timezone.localtime(p.created_at).date()).days if p.created_at else 0

        if len(grupo['items']) < _PENDIENTES_ITEMS_POR_SUCURSAL:
            grupo['items'].append({
                'pendiente_id': p.id,
                'sku': str(p.producto_talla.sku),
                'articulo': p.producto_talla.producto.articulo,
                'talla': p.producto_talla.talla,
                'cantidad_total': p.cantidad,
                'cantidad_despachada': p.cantidad_despachada,
                'cantidad_restante': restante,
                'dias': dias,
                'documento': (str(p.dte_origen.numero_documento) if p.dte_origen_id else ''),
                'marca': p.producto_talla.producto.atributo1.valor if p.producto_talla.producto.atributo1 else '-',
            })
        else:
            grupo['truncado'] = True

        grupo['total_unidades'] += restante
        grupo['total_lineas'] += 1
        grupo['dias_mas_antiguo'] = max(grupo['dias_mas_antiguo'], dias)
        total_unidades += restante
        total_lineas += 1

    grupos = sorted(por_sucursal.values(), key=lambda g: -g['total_unidades'])

    return JsonResponse({
        'success': True,
        'pendientes_por_sucursal': grupos,
        'total_unidades': total_unidades,
        'total_lineas': total_lineas,
        'items_por_sucursal': _PENDIENTES_ITEMS_POR_SUCURSAL,
    })


@login_required
@require_POST
def api_crear_despacho_masivo(request):
    """
    Crea traspasos (despachos) a múltiples sucursales en una sola operación.
    Body JSON: { despachos: [{ sucursal_destino_id, items: [{producto_talla_id, cantidad}] }], observaciones }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    despachos = data.get('despachos', [])
    observaciones = data.get('observaciones', '')
    sucursal_origen_id = request.session.get('idSucursalActual')

    if not despachos:
        return JsonResponse({'success': False, 'error': 'No hay despachos a procesar.'}, status=400)

    # El origen sale de la sesión, pero el destino y los SKUs llegan en el body:
    # hay que validarlos contra las bodegas del usuario o se podría mover
    # mercadería de/hacia otra empresa.
    suc_ids_usuario = _sucursales_usuario(request)
    if sucursal_origen_id not in suc_ids_usuario:
        return _sin_acceso('Tu sucursal activa no pertenece a una empresa habilitada para ti.')

    sucursal_origen = get_object_or_404(Sucursal, id=sucursal_origen_id)
    usuario = request.user
    traspasos_creados = []

    try:
        with transaction.atomic():
            for despacho in despachos:
                suc_destino_id = despacho.get('sucursal_destino_id')
                items = despacho.get('items', [])
                if not items or not suc_destino_id:
                    continue

                try:
                    suc_destino_id = int(suc_destino_id)
                except (TypeError, ValueError):
                    raise ValueError(f'Sucursal destino inválida: {suc_destino_id!r}.')
                if suc_destino_id not in suc_ids_usuario:
                    raise PermissionError(
                        f'La sucursal destino {suc_destino_id} no pertenece a tus empresas.'
                    )

                sucursal_destino = get_object_or_404(Sucursal, id=suc_destino_id)

                ultimo_num = Traspaso.objects.filter(
                    sucursal_origen=sucursal_origen,
                ).order_by('-numero_traspaso').values_list('numero_traspaso', flat=True).first() or 0

                traspaso = Traspaso.objects.create(
                    sucursal_origen=sucursal_origen,
                    sucursal_destino=sucursal_destino,
                    numero_traspaso=ultimo_num + 1,
                    estado='PENDIENTE',
                    solicitante=f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username,
                    observaciones_solicitud=observaciones,
                )

                for item in items:
                    pt_id = item.get('producto_talla_id')
                    cantidad = int(item.get('cantidad', 0))
                    if cantidad <= 0:
                        continue

                    pt = get_object_or_404(Producto_Talla, id=pt_id)

                    # El SKU debe vivir en la bodega desde la que se despacha:
                    # si no, se estaría descontando stock de otra sucursal
                    # (potencialmente de otra empresa) con un id arbitrario.
                    if pt.producto.sucursal_id != sucursal_origen.id:
                        raise PermissionError(
                            f'El SKU {pt.sku} no pertenece a la bodega de origen '
                            f'{sucursal_origen.alias}.'
                        )

                    if pt.stock < cantidad:
                        raise ValueError(
                            f'Stock insuficiente para SKU {pt.sku} '
                            f'(disponible: {pt.stock}, solicitado: {cantidad})'
                        )

                    Traspaso_Detalle.objects.create(
                        traspaso=traspaso,
                        producto_talla=pt,
                        cantidad_solicitada=cantidad,
                        costo=pt.producto.costo,
                        sobreprecio=pt.producto.sobreprecio,
                        costo_destino=pt.producto.costo + pt.producto.sobreprecio,
                        precio_venta=pt.producto.precioventa,
                    )

                    Movimientos_Producto.objects.create(
                        ProductoTalla=pt,
                        sucursal_origen=sucursal_origen,
                        sucursal_destino=sucursal_destino,
                        cantidad=-cantidad,
                        costo=pt.producto.costo,
                        precio=pt.producto.precioventa,
                        concepto='TRASPASO_SALIDA',
                        tipo_movimiento='EGRESO',
                        estado='COMPLETADO',
                        responsable=f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username,
                        observaciones=f"Despacho masivo #{traspaso.numero_traspaso} → {sucursal_destino.alias}",
                    )

                    # Consumir lotes FIFO disponibles (mejor esfuerzo) para mantener
                    # LoteProducto alineado con el stock plano, que sigue siendo la
                    # fuente de verdad. No bloquea el despacho si no hay lotes.
                    from .views_edicion_productos import _consumir_lotes_fifo_ajuste
                    try:
                        _consumir_lotes_fifo_ajuste(pt, cantidad)
                    except Exception:
                        logger.exception(
                            "No se pudieron bajar lotes FIFO en despacho masivo, sku=%s", pt.sku
                        )

                    pt.stock = F('stock') - cantidad
                    pt.save(update_fields=['stock'])

                traspasos_creados.append({
                    'id': traspaso.id,
                    'numero': traspaso.numero_traspaso,
                    'destino': sucursal_destino.alias,
                    'items': len(items),
                })

    except PermissionError as e:
        logger.warning(
            "Despacho masivo denegado a %s: %s", request.user.username, e
        )
        return _sin_acceso(str(e))
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'mensaje': f'Se crearon {len(traspasos_creados)} despachos exitosamente.',
        'traspasos': traspasos_creados,
    })


_ESTADOS_TRASPASO_VALIDOS = {'PENDIENTE', 'APROBADO', 'EN_TRANSITO', 'RECIBIDO', 'RECHAZADO', 'ANULADO'}

# Días que se consideran "tránsito normal" antes de marcar un despacho como
# no recibido. Por encima de este umbral la mercadería salió de la bodega,
# ya no está en el stock del origen y nadie la ingresó en el destino.
_DIAS_TRANSITO_NORMAL = 3
_HISTORIAL_DIAS_DEFAULT = 90
_HISTORIAL_DIAS_MAX = 365
_HISTORIAL_MAX_DOCUMENTOS = 1500


def _entero(valor, default, minimo=None, maximo=None):
    """int() tolerante: la query string es entrada del usuario, no debe dar 500."""
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return default
    if minimo is not None and n < minimo:
        return minimo
    if maximo is not None and n > maximo:
        return maximo
    return n


def _historial_despachos_reales(request, sucursal_id):
    """
    Historial REAL de despachos a sucursales, reconstruido desde el kardex.

    El circuito que se usa a diario no crea filas en `Traspaso`: se emite un
    DTE (guía o factura) desde la bodega origen — que escribe TRASPASO_SALIDA
    por cada SKU — y el destino lo ingresa desde Recepción de Documentos, que
    escribe TRASPASO_ENTRADA contra el MISMO dte. Por eso el historial se
    agrupa por DTE y no por Traspaso.

    Unidades pendientes = enviadas − recibidas − devueltas por NC.
    (Una NC sobre el documento devuelve físicamente la diferencia al origen
    con concepto DEVOLUCION_NC: si no se descuenta, el panel alertaría por
    faltantes que ya se resolvieron.)
    """
    from django.db.models import Min as _Min
    from .models import Dte
    from .constants_kardex import CONCEPTOS_TRASPASO_ENTRADA

    dias = _entero(request.GET.get('dias'), _HISTORIAL_DIAS_DEFAULT, 1, _HISTORIAL_DIAS_MAX)
    filtro = (request.GET.get('filtro') or '').strip().upper()
    page_num = _entero(request.GET.get('page'), 1, 1)
    hoy = timezone.localdate()
    desde = hoy - timedelta(days=dias)

    # 1) Qué documentos entran en la ventana. 2) Sus totales completos.
    #    El corte por fecha se aplica SOLO para elegir los documentos: un mismo
    #    DTE tiene movimientos repartidos en varios días (se despacha por
    #    partes) y sumar únicamente los que caen dentro de la ventana daba
    #    "enviadas" mutiladas — llegaba a mostrar más recibidas que enviadas.
    dte_ids = list(
        Movimientos_Producto.objects
        .filter(concepto='TRASPASO_SALIDA', dte__isnull=False,
                sucursal_origen_id=sucursal_id, fecha__gte=desde)
        .order_by().values_list('dte_id', flat=True).distinct()[:_HISTORIAL_MAX_DOCUMENTOS]
    )

    salidas = list(
        Movimientos_Producto.objects
        .filter(concepto='TRASPASO_SALIDA', dte_id__in=dte_ids,
                sucursal_origen_id=sucursal_id)
        .values('dte_id', 'sucursal_destino_id', 'sucursal_destino__alias',
                'dte__tipo_documento', 'dte__numero_documento', 'dte__estado_dte')
        .annotate(enviadas=Sum(Abs('cantidad')), items=Count('id'), fecha_salida=_Min('fecha'))
        .order_by('-fecha_salida')
    )

    recibidas_por_dte = dict(
        Movimientos_Producto.objects
        .filter(concepto__in=CONCEPTOS_TRASPASO_ENTRADA, dte_id__in=dte_ids)
        .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()
    )

    # Notas de crédito emitidas contra esos documentos: sus movimientos
    # reingresan la mercadería al origen, así que cierran la diferencia.
    nc_de_original = dict(
        Dte.objects.filter(documento_afectado_id__in=dte_ids)
        .values_list('id', 'documento_afectado_id')
    )
    devueltas_por_dte = {}
    if nc_de_original:
        for fila in (Movimientos_Producto.objects
                     .filter(dte_id__in=list(nc_de_original.keys()),
                             tipo_movimiento__in=_TIPOS_ENTRADA)
                     .values('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()):
            original = nc_de_original.get(fila['dte_id'])
            if original:
                devueltas_por_dte[original] = devueltas_por_dte.get(original, 0) + (fila['u'] or 0)

    datos = []
    resumen = {
        'documentos': 0, 'unidades_enviadas': 0, 'unidades_recibidas': 0,
        'unidades_pendientes': 0, 'docs_sin_recibir': 0, 'unidades_sin_recibir': 0,
        'docs_en_transito': 0, 'unidades_en_transito': 0, 'docs_recibidos': 0,
        'docs_sobre_recibidos': 0, 'unidades_sobre_recibidas': 0,
    }

    for s in salidas:
        enviadas = s['enviadas'] or 0
        recibidas = recibidas_por_dte.get(s['dte_id'], 0) or 0
        devueltas = devueltas_por_dte.get(s['dte_id'], 0) or 0
        pendientes = max(0, enviadas - recibidas - devueltas)
        fecha_salida = s['fecha_salida']
        dias_transcurridos = (hoy - fecha_salida).days if fecha_salida else 0

        if recibidas > enviadas:
            # El destino ingresó más unidades de las que salieron de la bodega:
            # no es un pendiente, es una descuadratura que hay que revisar.
            situacion = 'SOBRE_RECIBIDO'
        elif pendientes <= 0:
            situacion = 'RECIBIDO'
        elif dias_transcurridos <= _DIAS_TRANSITO_NORMAL:
            situacion = 'EN_TRANSITO'
        else:
            situacion = 'SIN_RECIBIR'

        resumen['documentos'] += 1
        resumen['unidades_enviadas'] += enviadas
        resumen['unidades_recibidas'] += recibidas
        resumen['unidades_pendientes'] += pendientes
        if situacion == 'SIN_RECIBIR':
            resumen['docs_sin_recibir'] += 1
            resumen['unidades_sin_recibir'] += pendientes
        elif situacion == 'EN_TRANSITO':
            resumen['docs_en_transito'] += 1
            resumen['unidades_en_transito'] += pendientes
        elif situacion == 'SOBRE_RECIBIDO':
            resumen['docs_sobre_recibidos'] += 1
            resumen['unidades_sobre_recibidas'] += (recibidas - enviadas)
        else:
            resumen['docs_recibidos'] += 1

        datos.append({
            'dte_id': s['dte_id'],
            'tipo_documento': s['dte__tipo_documento'] or '',
            'numero_documento': s['dte__numero_documento'],
            'destino': s['sucursal_destino__alias'] or '—',
            'fecha': fecha_salida.strftime('%d/%m/%Y') if fecha_salida else '',
            'dias': dias_transcurridos,
            'items': s['items'],
            'enviadas': enviadas,
            'recibidas': recibidas,
            'devueltas_nc': devueltas,
            'pendientes': pendientes,
            'estado_dte': s['dte__estado_dte'] or '',
            'situacion': situacion,
        })

    if filtro in ('SIN_RECIBIR', 'EN_TRANSITO', 'RECIBIDO', 'SOBRE_RECIBIDO'):
        datos_filtrados = [d for d in datos if d['situacion'] == filtro]
    elif filtro == 'PENDIENTE':  # todo lo que salió y aún no está completo
        datos_filtrados = [d for d in datos if d['pendientes'] > 0]
    else:
        datos_filtrados = datos

    # Lo que no llegó primero: es lo que hay que perseguir.
    if filtro in ('', 'PENDIENTE', 'SIN_RECIBIR'):
        datos_filtrados = sorted(
            datos_filtrados,
            key=lambda d: (0 if d['situacion'] == 'SIN_RECIBIR' else 1, -d['pendientes'], -d['dias']),
        )

    paginator = Paginator(datos_filtrados, 20)
    page = paginator.get_page(page_num)

    return JsonResponse({
        'success': True,
        'modo': 'dte',
        'dias': dias,
        'umbral_transito': _DIAS_TRANSITO_NORMAL,
        'despachos': list(page),
        'resumen': resumen,
        'pagina_actual': page.number,
        'total_paginas': paginator.num_pages,
        'total_despachos': paginator.count,
        'truncado': len(salidas) >= _HISTORIAL_MAX_DOCUMENTOS,
    })


@login_required
@require_GET
def api_historial_despachos(request):
    """
    Historial de despachos desde la sucursal actual.

    - `vista=dte` (por defecto): despachos reales reconstruidos desde el kardex
      (DTE de traspaso), con unidades enviadas / recibidas / pendientes.
    - `vista=traspaso`: filas del modelo `Traspaso` (flujo interno del botón
      "Enviar despachos" de esta pantalla).
    Parámetros vista=traspaso: estado, fecha_desde, fecha_hasta, page.
    """
    sucursal_id = request.session.get('idSucursalActual')

    # El id de sucursal viene de la sesión, pero igual se acota al alcance real
    # del usuario: si cambió de empresa, no debe seguir viendo la bodega vieja.
    if not sucursal_id or sucursal_id not in _sucursales_usuario(request):
        return _sin_acceso('Tu sucursal activa no pertenece a una empresa habilitada para ti.')

    if (request.GET.get('vista') or 'dte').lower() != 'traspaso':
        return _historial_despachos_reales(request, sucursal_id)

    estado = request.GET.get('estado', '').strip().upper()
    fecha_desde = request.GET.get('fecha_desde', '')
    fecha_hasta = request.GET.get('fecha_hasta', '')
    page_num = _entero(request.GET.get('page'), 1, 1)

    traspasos_qs = Traspaso.objects.filter(
        sucursal_origen_id=sucursal_id,
    ).select_related('sucursal_destino')

    if estado in _ESTADOS_TRASPASO_VALIDOS:
        traspasos_qs = traspasos_qs.filter(estado=estado)
    if fecha_desde:
        traspasos_qs = traspasos_qs.filter(fecha_solicitud__gte=fecha_desde)
    if fecha_hasta:
        traspasos_qs = traspasos_qs.filter(fecha_solicitud__lte=fecha_hasta)

    traspasos_qs = traspasos_qs.annotate(
        total_items=Count('detalles'),
        total_unidades=Sum('detalles__cantidad_solicitada'),
    ).order_by('-fecha_solicitud', '-numero_traspaso')

    paginator = Paginator(traspasos_qs, 20)
    page = paginator.get_page(page_num)

    datos = [{
        'id': t.id,
        'numero_traspaso': t.numero_traspaso,
        'destino': t.sucursal_destino.alias,
        'estado': t.estado,
        'estado_display': t.get_estado_display(),
        'fecha_solicitud': t.fecha_solicitud.strftime('%d/%m/%Y') if t.fecha_solicitud else '',
        'solicitante': t.solicitante,
        'total_items': t.total_items,
        'total_unidades': t.total_unidades or 0,
        'observaciones': t.observaciones_solicitud or '',
    } for t in page]

    conteos = dict(
        Traspaso.objects.filter(sucursal_origen_id=sucursal_id)
        .values_list('estado').annotate(c=Count('id')).order_by()
    )

    return JsonResponse({
        'success': True,
        'modo': 'traspaso',
        'despachos': datos,
        'pagina_actual': page.number,
        'total_paginas': paginator.num_pages,
        'total_despachos': paginator.count,
        'conteos_por_estado': conteos,
    })


# =====================================================
# 3. TRAZABILIDAD COMPLETA DE PRODUCTO
# =====================================================

@login_required
@require_GET
def trazabilidad_producto(request):
    """Vista principal: trazabilidad completa de un producto."""
    return render(request, 'vistas/modulo_existencias/trazabilidad_producto.html')


# Topes de la trazabilidad. El corte se INFORMA al frontend (antes se cortaba en
# silencio y la pantalla daba a entender que el producto no tenía más historia).
_TRAZA_MAX_MOVIMIENTOS = 200
_TRAZA_MAX_TRASPASOS = 100
_TRAZA_MAX_TIMELINE = 100
_TRAZA_MAX_LOTES = 50
_TRAZA_MAX_RECEPCIONES = 100

# Único estado de `Movimientos_Producto` que efectivamente movió stock.
#
# `ESTADO_MOVIMIENTO_CHOICES` (app/models/ventas.py) admite PENDIENTE,
# PENDIENTE_RECEPCION, APROBADO, RECHAZADO, ANULADO, COMPLETADO y CANCELADO.
# El kardex de esta ficha los sumaba TODOS: un movimiento ANULADO o un despacho
# aún PENDIENTE inflaba el saldo acumulado y hacía que el chequeo
# `cuadra`/`diferencia` marcara descuadres que no existen. El saldo se calcula
# ahora solo con COMPLETADO.
#
# DECISIÓN: los movimientos no completados NO se esconden — se siguen listando
# (ocultarlos sería el error opuesto: el usuario no vería que hay un despacho
# pendiente sobre ese SKU), pero no avanzan el saldo, se marcan en pantalla y
# se cuentan aparte en `movimientos_meta.no_computados`.
_ESTADO_MOVIMIENTO_KARDEX = 'COMPLETADO'


def _fmt_local(valor, formato='%d/%m/%Y'):
    """
    Formatea un datetime/date en hora local (America/Santiago).

    Con `USE_TZ=True` los DateTimeField llegan en UTC: hacerles strftime directo
    corría la hora (y de madrugada, la fecha) de recepciones y lotes.
    """
    if not valor:
        return ''
    if hasattr(valor, 'tzinfo') and timezone.is_aware(valor):
        valor = timezone.localtime(valor)
    return valor.strftime(formato)


def _traspasos_producto_talla(producto_talla, limite=_TRAZA_MAX_TRASPASOS):
    """
    Traspasos de un SKU, con la fuente de la que salieron.

    `Traspaso_Detalle` está VACÍA en producción (los despachos históricos nunca
    la poblaron), así que leerla sola hacía que la pestaña saliera siempre vacía
    y pareciera que el producto jamás se movió entre bodegas. Si no hay filas
    formales, se reconstruyen los traspasos desde los movimientos reales
    (conceptos TRASPASO_* / REGULARIZACION_TRASPASO), que son el dato que sí
    existe. La respuesta indica qué fuente se usó para poder decirlo en pantalla.

    Devuelve (filas, meta) con filas normalizadas para una sola tabla.
    """
    bodega_id = producto_talla.producto.sucursal_id

    detalles = list(
        Traspaso_Detalle.objects
        .filter(producto_talla=producto_talla)
        .select_related('traspaso', 'traspaso__sucursal_origen', 'traspaso__sucursal_destino')
        .order_by('-traspaso__fecha_solicitud')[:limite]
    )
    if detalles:
        total = Traspaso_Detalle.objects.filter(producto_talla=producto_talla).count()
        filas = []
        for td in detalles:
            t = td.traspaso
            filas.append({
                'numero': t.numero_traspaso,
                'fecha': t.fecha_solicitud.strftime('%d/%m/%Y') if t.fecha_solicitud else '',
                'hora': '',
                'origen': t.sucursal_origen.alias if t.sucursal_origen else '-',
                'destino': t.sucursal_destino.alias if t.sucursal_destino else '-',
                'sentido': 'SALIDA' if t.sucursal_origen_id == bodega_id else 'ENTRADA',
                'estado': t.estado,
                'cantidad_solicitada': td.cantidad_solicitada,
                'cantidad_recibida': td.cantidad_recibida,
                'costo': td.costo,
                'precio_venta': td.precio_venta,
                'referencia': '',
            })
        return filas, {
            'fuente': 'traspaso_detalle',
            'total': total,
            'mostrados': len(filas),
            'omitidos': max(total - len(filas), 0),
        }

    movimientos_qs = (
        Movimientos_Producto.objects
        .filter(ProductoTalla=producto_talla)
        .filter(Q(concepto__startswith='TRASPASO') | Q(concepto='REGULARIZACION_TRASPASO'))
    )
    total = movimientos_qs.count()
    movimientos = list(
        movimientos_qs
        .select_related('sucursal_origen', 'sucursal_destino')
        .order_by('-fecha', '-hora', '-id')[:limite]
    )

    filas = []
    for m in movimientos:
        delta = _delta_kardex(m)
        filas.append({
            'numero': '—',
            'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
            'hora': m.hora.strftime('%H:%M') if m.hora else '',
            'origen': m.sucursal_origen.alias if m.sucursal_origen else '-',
            'destino': m.sucursal_destino.alias if m.sucursal_destino else '-',
            'sentido': 'ENTRADA' if delta > 0 else 'SALIDA',
            'estado': m.estado,
            'cantidad_solicitada': abs(delta),
            'cantidad_recibida': None,
            'costo': m.costo,
            'precio_venta': m.precio,
            'referencia': m.observaciones or m.referencia_externa or '',
        })

    return filas, {
        'fuente': 'movimientos',
        'total': total,
        'mostrados': len(filas),
        'omitidos': max(total - len(filas), 0),
    }


def _recepciones_producto_talla(producto_talla, limite=_TRAZA_MAX_RECEPCIONES):
    """
    Recepciones de un SKU: la pata de ORIGEN (compras) de la trazabilidad.

    `Productos_Recepcionados` es donde queda lo que REALMENTE llegó contra lo que
    se esperaba (faltante / dañado / sobrante), de qué documento vino y quién lo
    recibió. La ficha no la leía, así que la pantalla oficial de trazabilidad no
    contestaba la pregunta más básica del origen: de qué compra y de qué
    proveedor entró este stock.

    Se busca por DOS caminos porque la FK directa `producto_talla` es nullable y
    en las recepciones de compra antiguas quedó en NULL: ahí el vínculo vive en
    `compra_producto_talla.producto_talla`. Son FKs a-uno, así que el OR no
    multiplica filas.

    Devuelve (filas, meta). `meta` trae los totales de TODA la historia (no solo
    de la ventana mostrada) y cuántas recepciones no tienen movimiento de stock
    vinculado — el eslabón que la auditoría midió como roto.
    """
    qs = Productos_Recepcionados.objects.filter(
        Q(producto_talla=producto_talla)
        | Q(compra_producto_talla__producto_talla=producto_talla)
    )

    total = qs.count()
    sin_movimiento = qs.filter(movimiento_ingreso__isnull=True).count()
    totales = qs.aggregate(
        esperado=Sum('cantidad_esperada'),
        recibido=Sum('stockArribado'),
        faltante=Sum('cantidad_faltante'),
        danado=Sum('cantidad_danada'),
        sobrante=Sum('cantidad_sobrante'),
    )

    recepciones = list(
        qs.select_related(
            'dte', 'dte__emisor', 'sucursal_destino', 'movimiento_ingreso',
            'compra_producto_talla',
            'compra_producto_talla__compra_producto',
            'compra_producto_talla__compra_producto__compras',
            'compra_producto_talla__compra_producto__compras__empresa',
        )
        # `fecha_recepcion` es la fecha real pero es nullable (las recepciones
        # legacy no la tienen): NULLS LAST para que no encabecen el listado.
        .order_by(F('fecha_recepcion').desc(nulls_last=True), '-fecha', '-id')[:limite]
    )

    filas = []
    for r in recepciones:
        cpt = r.compra_producto_talla
        compra = cpt.compra_producto.compras if (cpt and cpt.compra_producto_id) else None

        # El proveedor es el EMISOR del documento de compra. Si la recepción no
        # tiene DTE (compras cargadas sin documento), se cae a la empresa de la
        # orden de compra, que en `Compras` es justamente el proveedor.
        proveedor = ''
        if r.dte and r.dte.emisor:
            proveedor = r.dte.emisor.nombre
        elif compra and compra.empresa:
            proveedor = compra.empresa.nombre

        esperado = r.cantidad_esperada or 0
        recibido = r.stockArribado or 0

        filas.append({
            'id': r.id,
            'fecha': _fmt_local(r.fecha_recepcion) or (r.fecha.strftime('%d/%m/%Y') if r.fecha else ''),
            'hora': _fmt_local(r.fecha_recepcion, '%H:%M'),
            'origen': 'COMPRA' if cpt else ('DOCUMENTO' if r.dte_id else 'SIN ORIGEN'),
            'documento': (
                f"{r.dte.get_tipo_documento_display()} #{r.dte.numero_documento}"
                if r.dte else '—'
            ),
            'dte_id': r.dte_id,
            'dte_url': f'/app/detalle_dte/{r.dte_id}/' if r.dte_id else None,
            'proveedor': proveedor or '—',
            'orden_compra': f"{compra.nombre} (#{compra.correlativo})" if compra else '—',
            'compra_id': compra.id if compra else None,
            'esperado': esperado,
            'recibido': recibido,
            'faltante': r.cantidad_faltante or 0,
            'danado': r.cantidad_danada or 0,
            'sobrante': r.cantidad_sobrante or 0,
            'diferencia': recibido - esperado,
            'estado': r.estado,
            'estado_display': r.get_estado_display(),
            'tiene_problemas': r.tiene_problemas,
            'recepcionado_por': r.recepcionado_por or '—',
            'destino': r.sucursal_destino.alias if r.sucursal_destino else '-',
            'movimiento_id': r.movimiento_ingreso_id,
            'sin_movimiento': r.movimiento_ingreso_id is None,
            'es_historica': r.es_historica,
            'observaciones': r.observaciones or '',
        })

    meta = {
        'total': total,
        'mostrados': len(filas),
        'omitidos': max(total - len(filas), 0),
        'truncado': total > len(filas),
        'limite': limite,
        'total_esperado': totales['esperado'] or 0,
        'total_recibido': totales['recibido'] or 0,
        'total_faltante': totales['faltante'] or 0,
        'total_danado': totales['danado'] or 0,
        'total_sobrante': totales['sobrante'] or 0,
        'sin_movimiento': sin_movimiento,
    }
    return filas, meta


@login_required
@require_GET
def api_trazabilidad_producto(request):
    """
    API: trazabilidad completa de un producto.
    Incluye: movimientos, traspasos, lotes FIFO, cambios de precio, tomas de inventario.
    Parámetros GET: sku
    """
    sku = request.GET.get('sku', '').strip()
    if not sku:
        return JsonResponse({'success': False, 'error': 'Debe ingresar un SKU.'}, status=400)

    suc_ids_usuario = _sucursales_usuario(request)
    if not suc_ids_usuario:
        return _sin_acceso('Tu usuario no tiene empresas asignadas.')

    # OJO: `sku` NO es único en la BD (dato legacy: hay SKUs repetidos). Antes
    # se usaba .get(sku=) y reventaba con MultipleObjectsReturned → la
    # trazabilidad no se mostraba. Se resuelve tolerando duplicados: se prefiere
    # la talla de la sucursal activa y se avisa que el SKU está duplicado.
    # El filtro por bodegas del usuario evita además leer la trazabilidad
    # (costos, precios, lotes) de un SKU de otra empresa.
    _qs_pt = Producto_Talla.objects.select_related(
        'producto', 'producto__atributo1', 'producto__atributo2',
        'producto__categoria', 'producto__sucursal',
    ).filter(sku=sku, producto__sucursal_id__in=suc_ids_usuario)
    _n_sku = _qs_pt.count()
    if _n_sku == 0:
        if Producto_Talla.objects.filter(sku=sku).exists():
            logger.warning(
                "Trazabilidad denegada: %s pidió el SKU %s fuera de su alcance",
                request.user.username, sku,
            )
            return _sin_acceso('Ese SKU pertenece a una empresa a la que no tienes acceso.')
        return JsonResponse({'success': False, 'error': f'SKU {sku} no encontrado.'}, status=404)
    producto_talla = None
    if _n_sku > 1:
        _suc_id = request.session.get('idSucursalActual')
        if _suc_id:
            producto_talla = _qs_pt.filter(producto__sucursal_id=_suc_id).first()
    if producto_talla is None:
        producto_talla = _qs_pt.first()

    producto = producto_talla.producto

    producto_info = {
        'id': producto.id,
        'articulo': producto.articulo,
        'descripcion': producto.descripcion,
        'sku': str(producto_talla.sku),
        'talla': producto_talla.talla,
        'stock_actual': producto_talla.stock,
        'costo': producto.costo,
        'sobreprecio': producto.sobreprecio,
        'precio_venta': producto.precioventa,
        'marca': producto.atributo1.valor if producto.atributo1 else '-',
        'color': producto.atributo2.valor if producto.atributo2 else '-',
        'categoria': producto.categoria.nombre if producto.categoria else '-',
        'sucursal': producto.sucursal.alias if producto.sucursal else '-',
        'fecha_creacion': producto.fecha_creacion.strftime('%d/%m/%Y %H:%M') if producto.fecha_creacion else '-',
        'sku_duplicado': _n_sku > 1,
        'sku_ocurrencias': _n_sku,
    }

    # --- Movimientos (kardex del SKU, con saldo acumulado) ---
    #
    # La trazabilidad es de UN Producto_Talla, o sea de UNA serie
    # (bodega dueña · talla): el saldo acumulado se lee directo, sin agrupar.
    # Se muestran los últimos `_TRAZA_MAX_MOVIMIENTOS`, pero el saldo NO puede
    # arrancar en 0 o no cuadraría nunca con el stock: se calcula en SQL el
    # saldo de toda la historia y se descuenta lo mostrado para obtener el
    # saldo de apertura de la ventana.
    #
    # El saldo suma SOLO los movimientos COMPLETADO (ver
    # `_ESTADO_MOVIMIENTO_KARDEX`): antes entraban también ANULADO y PENDIENTE,
    # que nunca tocaron stock, y el chequeo `cuadra` reportaba descuadres falsos.
    # Los no completados se siguen listando, marcados y sin avanzar el saldo.
    movimientos_qs = Movimientos_Producto.objects.filter(ProductoTalla=producto_talla)
    movimientos_kardex_qs = movimientos_qs.filter(estado=_ESTADO_MOVIMIENTO_KARDEX)
    total_movimientos = movimientos_qs.count()
    total_computados = movimientos_kardex_qs.count()
    saldo_final = movimientos_kardex_qs.aggregate(t=Sum(DELTA_KARDEX_SQL))['t'] or 0

    movimientos = list(
        movimientos_qs.select_related(
            'sucursal_origen', 'sucursal_destino', 'dte', 'ticket',
        ).order_by('-fecha', '-hora', '-id')[:_TRAZA_MAX_MOVIMIENTOS]
    )

    # `deltas` es informativo (se muestra la cantidad de TODOS los movimientos);
    # `computa` decide cuáles empujan el saldo acumulado.
    deltas = {m.id: _delta_kardex(m) for m in movimientos}
    computa = {m.id: m.estado == _ESTADO_MOVIMIENTO_KARDEX for m in movimientos}
    saldo_apertura = saldo_final - sum(d for mid, d in deltas.items() if computa[mid])

    # Recorrido cronológico ascendente para acumular (la lista viene descendente).
    saldos = {}
    saldo_corriente = saldo_apertura
    for m in reversed(movimientos):
        if computa[m.id]:
            saldo_corriente += deltas[m.id]
            saldos[m.id] = saldo_corriente
        else:
            saldos[m.id] = None  # no movió stock: el saldo no avanza

    movimientos_data = [{
        'id': m.id,
        'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
        'hora': m.hora.strftime('%H:%M') if m.hora else '',
        'tipo': m.tipo_movimiento,
        'concepto': m.get_concepto_display(),
        'concepto_codigo': m.concepto,
        'es_traspaso': _es_traspaso(m.concepto),
        'cantidad': deltas[m.id],
        'saldo': saldos[m.id],
        'computa_saldo': computa[m.id],
        'costo': m.costo,
        'precio': m.precio,
        'origen': m.sucursal_origen.alias if m.sucursal_origen else '-',
        'destino': m.sucursal_destino.alias if m.sucursal_destino else '-',
        'responsable': m.responsable,
        'dte_folio': (
            getattr(m.dte, 'folio', None) or getattr(m.dte, 'numero_documento', None)
            if m.dte else None
        ),
        'ticket_correlativo': m.ticket.correlativo if m.ticket else None,
        'observaciones': m.observaciones or '',
        'referencia': m.referencia_externa or '',
        'estado': m.estado,
    } for m in movimientos]

    stock_actual = producto_talla.stock or 0
    movimientos_meta = {
        'total': total_movimientos,
        'mostrados': len(movimientos_data),
        'omitidos': max(total_movimientos - len(movimientos_data), 0),
        'truncado': total_movimientos > len(movimientos_data),
        'limite': _TRAZA_MAX_MOVIMIENTOS,
        'saldo_apertura': saldo_apertura,
        'saldo_final': saldo_final,
        'stock_actual': stock_actual,
        'diferencia': stock_actual - saldo_final,
        'cuadra': stock_actual == saldo_final,
        # Desglose por estado: cuántos movimientos entran al saldo y cuántos no
        # (ANULADO, PENDIENTE, RECHAZADO...). Se informa en pantalla para que el
        # usuario sepa por qué la tabla tiene filas sin saldo.
        'estado_computado': _ESTADO_MOVIMIENTO_KARDEX,
        'total_computados': total_computados,
        'no_computados': max(total_movimientos - total_computados, 0),
        'no_computados_en_ventana': sum(1 for v in computa.values() if not v),
    }

    # --- Lotes FIFO ---
    #
    # Cada lote nace de una entrada concreta y `LoteProducto` guarda las FKs
    # `dte` y `movimiento` con esa entrada. La ficha las ignoraba: mostraba
    # costo y fecha pero NO de qué factura de proveedor venía el lote, que es
    # justo el dato que hace trazable una unidad. `select_related` evita el N+1
    # que provocaría tocar `l.dte.emisor` lote por lote.
    lotes_qs = LoteProducto.objects.filter(producto_talla=producto_talla)
    total_lotes = lotes_qs.count()
    lotes = lotes_qs.select_related('dte', 'dte__emisor', 'movimiento').order_by(
        '-fecha_ingreso'
    )[:_TRAZA_MAX_LOTES]

    lotes_data = [{
        'id': l.id,
        'fecha_ingreso': _fmt_local(l.fecha_ingreso),
        'cantidad_inicial': l.cantidad_inicial,
        'cantidad_disponible': l.cantidad_disponible,
        'costo_unitario': l.costo_unitario,
        'precio_venta': l.precio_venta_unitario,
        'activo': l.activo,
        'agotado': l.agotado,
        'numero_lote': l.numero_lote or '-',
        'porcentaje_consumido': round(l.porcentaje_consumido, 1),
        # --- Origen del lote (DTE + proveedor) ---
        'dte_id': l.dte_id,
        'dte_folio': l.dte.numero_documento if l.dte else None,
        'dte_tipo': l.dte.get_tipo_documento_display() if l.dte else '',
        'dte_fecha': l.dte.fecha_emision.strftime('%d/%m/%Y') if (l.dte and l.dte.fecha_emision) else '',
        'dte_url': f'/app/detalle_dte/{l.dte_id}/' if l.dte_id else None,
        'proveedor': l.dte.emisor.nombre if (l.dte and l.dte.emisor) else '',
        'proveedor_rut': l.dte.emisor.rut if (l.dte and l.dte.emisor) else '',
        'movimiento_id': l.movimiento_id,
        'movimiento_concepto': l.movimiento.get_concepto_display() if l.movimiento else '',
    } for l in lotes]

    lotes_meta = {
        'total': total_lotes,
        'mostrados': len(lotes_data),
        'omitidos': max(total_lotes - len(lotes_data), 0),
        'truncado': total_lotes > len(lotes_data),
        'limite': _TRAZA_MAX_LOTES,
        'sin_origen': sum(1 for l in lotes_data if not l['dte_id']),
    }

    # --- Traspasos ---
    traspasos_data, traspasos_meta = _traspasos_producto_talla(producto_talla)

    # --- Origen / Compras (recepciones) ---
    recepciones_data, recepciones_meta = _recepciones_producto_talla(producto_talla)

    # --- Cambios de precio ---
    historial_precios = HistorialCambioPrecio.objects.filter(
        producto_id=producto.id,
    ).order_by('-fecha_cambio')[:50]

    precios_data = [{
        'fecha': hp.fecha_cambio.strftime('%d/%m/%Y %H:%M') if hp.fecha_cambio else '',
        'tipo': hp.get_tipo_cambio_display() if hasattr(hp, 'get_tipo_cambio_display') else hp.tipo_cambio,
        'valor_anterior': hp.precio_anterior,
        'valor_nuevo': hp.precio_nuevo,
        'usuario': hp.usuario.get_full_name() if hasattr(hp, 'usuario') and hp.usuario else '-',
        'motivo': hp.motivo if hasattr(hp, 'motivo') else '',
    } for hp in historial_precios]

    # --- Pendientes de despacho ---
    pendientes = PendienteDespacho.objects.filter(
        producto_talla=producto_talla,
    ).select_related('sucursal_origen', 'sucursal_destino').order_by('-created_at')[:20]

    pendientes_data = [{
        'id': p.id,
        'origen': p.sucursal_origen.alias if p.sucursal_origen else '-',
        'destino': p.sucursal_destino.alias if p.sucursal_destino else '-',
        'cantidad': p.cantidad,
        'despachada': p.cantidad_despachada,
        'restante': p.cantidad_restante,
        'estado': p.get_estado_display(),
        'fecha': p.created_at.strftime('%d/%m/%Y') if p.created_at else '',
    } for p in pendientes]

    # --- Timeline unificada ---
    timeline = []
    for m in movimientos_data:
        # Los movimientos que no computan al saldo se marcan en el evento: si no,
        # la timeline daba a entender que un ANULADO movió mercadería.
        detalle = f"Cantidad: {m['cantidad']} | {m['origen']} → {m['destino']}"
        if not m['computa_saldo']:
            detalle += f" | {m['estado']} (no afecta el saldo)"
        timeline.append({
            'fecha': m['fecha'],
            'hora': m['hora'],
            'tipo': 'movimiento',
            'icono': 'ri-arrow-left-right-line',
            'color': (
                '#adb5bd' if not m['computa_saldo']
                else '#00D4AA' if m['tipo'] == 'INGRESO'
                else '#FF6B6B' if m['tipo'] == 'EGRESO' else '#0066FF'
            ),
            'titulo': m['concepto'],
            'detalle': detalle,
            'responsable': m['responsable'],
        })
    # Recepciones SIN movimiento de stock vinculado: son el eslabón roto de la
    # cadena (llegó mercadería pero el kardex no lo sabe). Las que sí tienen
    # movimiento ya están en la timeline como movimiento, no se duplican.
    for r in recepciones_data:
        if not r['sin_movimiento']:
            continue
        timeline.append({
            'fecha': r['fecha'],
            'hora': r['hora'] or '00:00',
            'tipo': 'recepcion',
            'icono': 'ri-inbox-archive-line',
            'color': '#FF9F43',
            'titulo': f"Recepción de compra ({r['estado_display']}) — sin movimiento de stock vinculado",
            'detalle': (
                f"{r['documento']} · {r['proveedor']} | "
                f"Esperado: {r['esperado']} · Recibido: {r['recibido']}"
            ),
            'responsable': r['recepcionado_por'],
        })
    # Los traspasos reconstruidos desde movimientos YA están en la timeline
    # (son movimientos): solo se agregan aparte cuando vienen de la tabla formal
    # Traspaso_Detalle, o se verían duplicados.
    if traspasos_meta['fuente'] == 'traspaso_detalle':
        for t in traspasos_data:
            timeline.append({
                'fecha': t['fecha'],
                'hora': t.get('hora') or '00:00',
                'tipo': 'traspaso',
                'icono': 'ri-truck-line',
                'color': '#FFC107',
                'titulo': f"Traspaso #{t['numero']} ({t['estado']})",
                'detalle': f"{t['origen']} → {t['destino']} | Cant: {t['cantidad_solicitada']}",
                'responsable': '',
            })
    for p in precios_data:
        timeline.append({
            'fecha': p['fecha'].split(' ')[0] if p['fecha'] else '',
            'hora': p['fecha'].split(' ')[1] if p['fecha'] and ' ' in p['fecha'] else '00:00',
            'tipo': 'precio',
            'icono': 'ri-price-tag-3-line',
            'color': '#9C27B0',
            'titulo': f"Cambio de precio: {p.get('tipo', '')}",
            'detalle': f"${p['valor_anterior']} → ${p['valor_nuevo']}",
            'responsable': p.get('usuario', ''),
        })

    # Las fechas de la timeline son strings 'dd/mm/YYYY': ordenarlas como texto
    # agrupaba por DÍA (30/01/2024 quedaba antes que 05/12/2026) y el corte
    # posterior descartaba eventos arbitrarios. Se ordena por fecha real.
    def _clave_orden(evento):
        from datetime import datetime as _dt
        fecha = (evento.get('fecha') or '').strip()
        hora = (evento.get('hora') or '00:00').strip()
        for formato in ('%d/%m/%Y %H:%M', '%d/%m/%Y'):
            try:
                return _dt.strptime(f"{fecha} {hora}".strip(), formato)
            except ValueError:
                continue
        return _dt.min  # sin fecha reconocible: al final del listado

    timeline.sort(key=_clave_orden, reverse=True)

    return JsonResponse({
        'success': True,
        'producto': producto_info,
        'movimientos': movimientos_data,
        'movimientos_meta': movimientos_meta,
        'lotes': lotes_data,
        'lotes_meta': lotes_meta,
        'traspasos': traspasos_data,
        'traspasos_meta': traspasos_meta,
        'recepciones': recepciones_data,
        'recepciones_meta': recepciones_meta,
        'historial_precios': precios_data,
        'pendientes_despacho': pendientes_data,
        'timeline': timeline[:_TRAZA_MAX_TIMELINE],
        'timeline_total': len(timeline),
    })


# =====================================================
# 4. MODIFICACIÓN DE PRECIOS Y COSTOS
# =====================================================

@login_required
@require_GET
def modificacion_precios_costos(request):
    """Vista principal: modificación de precios y costos."""
    sucursal_id = request.session.get('idSucursalActual')
    return render(request, 'vistas/modulo_existencias/modificacion_precios_costos.html', {
        'sucursal_id': sucursal_id,
    })


@login_required
@require_GET
def api_buscar_productos_precios(request):
    """Busca productos para modificar precios/costos."""
    sucursal_id = request.session.get('idSucursalActual')
    q = request.GET.get('q', '').strip()
    page_num = int(request.GET.get('page', 1))

    if not q or len(q) < 2:
        return JsonResponse({'success': True, 'productos': [], 'total': 0})

    productos_qs = Producto.objects.filter(
        sucursal_id=sucursal_id,
    ).select_related('atributo1', 'categoria', 'sucursal')

    productos_qs = productos_qs.filter(
        Q(articulo__icontains=q) |
        Q(descripcion__icontains=q) |
        Q(producto_talla__sku__icontains=q)
    ).distinct()

    productos_qs = productos_qs.order_by('articulo')
    paginator = Paginator(productos_qs, 30)
    page = paginator.get_page(page_num)

    datos = []
    for p in page:
        tallas = list(p.producto_talla.all().values('id', 'sku', 'talla', 'stock'))
        datos.append({
            'id': p.id,
            'articulo': p.articulo,
            'descripcion': p.descripcion,
            'costo': p.costo,
            'sobreprecio': p.sobreprecio,
            'precio_venta': p.precioventa,
            'precio_sugerido': p.precioSugerido,
            'marca': p.atributo1.valor if p.atributo1 else '-',
            'categoria': p.categoria.nombre if p.categoria else '-',
            'tallas': tallas,
            'margen_porcentaje': round(((p.precioventa - p.costo) / p.costo * 100), 1) if p.costo > 0 else 0,
        })

    return JsonResponse({
        'success': True,
        'productos': datos,
        'pagina_actual': page.number,
        'total_paginas': paginator.num_pages,
        'total': paginator.count,
    })


@login_required
@require_POST
def api_modificar_precio_costo(request):
    """
    Modifica precio y/o costo de un producto.
    Body JSON: { producto_id, costo, sobreprecio, precio_venta, motivo, aplicar_todas_sucursales }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    nuevo_costo = data.get('costo')
    nuevo_sobreprecio = data.get('sobreprecio')
    nuevo_precio = data.get('precio_venta')
    motivo = data.get('motivo', '')
    aplicar_todas = data.get('aplicar_todas_sucursales', False)

    if not producto_id:
        return JsonResponse({'success': False, 'error': 'Producto no especificado.'}, status=400)

    suc_ids_usuario = _sucursales_usuario(request)
    producto = get_object_or_404(Producto, id=producto_id)
    if producto.sucursal_id not in suc_ids_usuario:
        logger.warning(
            "Cambio de precio denegado a %s: producto %s de bodega ajena",
            request.user.username, producto.id,
        )
        return _sin_acceso('No tienes acceso a la bodega de este producto.')

    usuario = request.user
    cambios = []

    try:
        with transaction.atomic():
            if nuevo_costo is not None and int(nuevo_costo) != producto.costo:
                cambios.append(f"Costo: ${producto.costo} → ${nuevo_costo}")
                _registrar_cambio_precio(
                    producto, 'COSTO', producto.costo, int(nuevo_costo), usuario, motivo
                )
                producto.costo = int(nuevo_costo)

            if nuevo_sobreprecio is not None and int(nuevo_sobreprecio) != producto.sobreprecio:
                cambios.append(f"Sobreprecio: ${producto.sobreprecio} → ${nuevo_sobreprecio}")
                _registrar_cambio_precio(
                    producto, 'SOBREPRECIO', producto.sobreprecio, int(nuevo_sobreprecio), usuario, motivo
                )
                producto.sobreprecio = int(nuevo_sobreprecio)

            if nuevo_precio is not None and int(nuevo_precio) != producto.precioventa:
                cambios.append(f"Precio venta: ${producto.precioventa} → ${nuevo_precio}")
                _registrar_cambio_precio(
                    producto, 'PRECIO_VENTA', producto.precioventa, int(nuevo_precio), usuario, motivo
                )
                producto.precioventa = int(nuevo_precio)

            if cambios:
                producto.save()

                if aplicar_todas:
                    # "Todas las sucursales" = todas las del usuario, no las de
                    # la BD completa (el mismo artículo existe en otras empresas).
                    productos_mismos = Producto.objects.filter(
                        articulo=producto.articulo,
                        atributo1=producto.atributo1,
                        sucursal_id__in=suc_ids_usuario,
                    ).exclude(id=producto.id)

                    count_actualizados = 0
                    for p in productos_mismos:
                        if nuevo_costo is not None:
                            p.costo = int(nuevo_costo)
                        if nuevo_sobreprecio is not None:
                            p.sobreprecio = int(nuevo_sobreprecio)
                        if nuevo_precio is not None:
                            p.precioventa = int(nuevo_precio)
                        p.save()
                        count_actualizados += 1

                    cambios.append(f"Aplicado a {count_actualizados} sucursales adicionales")

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    if not cambios:
        return JsonResponse({'success': True, 'mensaje': 'No hubo cambios que aplicar.'})

    return JsonResponse({
        'success': True,
        'mensaje': f'Cambios aplicados correctamente.',
        'cambios': cambios,
    })


@login_required
@require_POST
def api_modificar_precios_masivo(request):
    """
    Modificación masiva de precios/costos.
    Body JSON: { productos: [{producto_id, costo, sobreprecio, precio_venta}], motivo }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    productos_data = data.get('productos', [])
    motivo = data.get('motivo', 'Modificación masiva')
    usuario = request.user

    if not productos_data:
        return JsonResponse({'success': False, 'error': 'No hay productos a modificar.'}, status=400)

    resultados = {'exitosos': 0, 'errores': []}
    suc_ids_usuario = _sucursales_usuario(request)

    try:
        with transaction.atomic():
            for item in productos_data:
                try:
                    producto = Producto.objects.get(
                        id=item.get('producto_id'), sucursal_id__in=suc_ids_usuario
                    )
                    nuevo_costo = item.get('costo')
                    nuevo_sobreprecio = item.get('sobreprecio')
                    nuevo_precio = item.get('precio_venta')

                    if nuevo_costo is not None and int(nuevo_costo) != producto.costo:
                        _registrar_cambio_precio(producto, 'COSTO', producto.costo, int(nuevo_costo), usuario, motivo)
                        producto.costo = int(nuevo_costo)

                    if nuevo_sobreprecio is not None and int(nuevo_sobreprecio) != producto.sobreprecio:
                        _registrar_cambio_precio(producto, 'SOBREPRECIO', producto.sobreprecio, int(nuevo_sobreprecio), usuario, motivo)
                        producto.sobreprecio = int(nuevo_sobreprecio)

                    if nuevo_precio is not None and int(nuevo_precio) != producto.precioventa:
                        _registrar_cambio_precio(producto, 'PRECIO_VENTA', producto.precioventa, int(nuevo_precio), usuario, motivo)
                        producto.precioventa = int(nuevo_precio)

                    producto.save()
                    resultados['exitosos'] += 1

                except Producto.DoesNotExist:
                    # Incluye los productos de bodegas fuera del alcance del
                    # usuario: no se distinguen para no filtrar su existencia.
                    resultados['errores'].append(
                        f"Producto ID {item.get('producto_id')} no encontrado o fuera de tu alcance"
                    )
                except Exception as e:
                    resultados['errores'].append(f"Error en producto {item.get('producto_id')}: {str(e)}")

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    return JsonResponse({
        'success': True,
        'mensaje': f'{resultados["exitosos"]} productos actualizados.',
        'resultados': resultados,
    })


def _registrar_cambio_precio(producto, tipo_cambio, valor_anterior, valor_nuevo, usuario, motivo):
    """Registra un cambio de precio en el historial."""
    try:
        diferencia = valor_nuevo - valor_anterior
        porcentaje = round((diferencia / valor_anterior * 100), 2) if valor_anterior else 0
        HistorialCambioPrecio.objects.create(
            producto=producto,
            precio_anterior=valor_anterior,
            precio_nuevo=valor_nuevo,
            diferencia=diferencia,
            porcentaje_cambio=porcentaje,
            tipo_cambio='MANUAL',
            usuario=usuario,
            motivo=f"[{tipo_cambio}] {motivo}" if motivo else f"Cambio de {tipo_cambio}",
        )
    except Exception:
        pass


# =====================================================
# 5. CORRECCIÓN DE TALLA GLOBAL (todas las bodegas)
# =====================================================

@login_required
@require_POST
def api_editar_talla_producto_global(request):
    """
    Renombra una talla mal registrada (ej. '12' que debió ser '12K') en TODAS
    las bodegas donde exista el MISMO producto (identidad: código normalizado +
    marca + color + género + categoría). El SKU de cada talla NO cambia — solo
    la etiqueta — así que ventas, lotes FIFO y etiquetas siguen funcionando.

    Body JSON: { producto_id, talla_actual, talla_nueva }

    Si en alguna bodega el producto YA tiene una fila con talla_nueva, esa
    bodega se salta y se reporta como conflicto (fusionar stock/lotes de dos
    Producto_Talla es una operación de Fusión de Duplicados, no de renombre).
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    talla_actual = str(data.get('talla_actual') or '').strip()
    talla_nueva = str(data.get('talla_nueva') or '').strip()

    if not producto_id or not talla_actual or not talla_nueva:
        return JsonResponse({'success': False, 'error': 'Faltan datos: producto, talla actual y talla nueva.'}, status=400)
    if talla_actual == talla_nueva:
        return JsonResponse({'success': False, 'error': 'La talla nueva es igual a la actual.'}, status=400)
    if len(talla_nueva) > 50:
        return JsonResponse({'success': False, 'error': 'La talla nueva supera los 50 caracteres.'}, status=400)

    producto = get_object_or_404(Producto, id=producto_id)

    # Alcance: solo las sucursales de las empresas del usuario. Además el
    # producto de referencia debe estar dentro de ese alcance.
    emp_ids = EmpresaUser.objects.filter(user=request.user, status=True).values_list('empresa_id', flat=True)
    suc_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids).values_list('id', flat=True))
    if producto.sucursal_id not in suc_ids:
        return JsonResponse({'success': False, 'error': 'No tienes acceso a la bodega de este producto.'}, status=403)

    from .utils_producto_match import productos_por_identidad_sucursales
    productos = productos_por_identidad_sucursales(
        producto.articulo, producto.atributo1_id, producto.atributo2_id,
        producto.atributo3_id, producto.categoria_id, suc_ids,
    )
    if producto.id not in {p.id for p in productos}:
        productos.append(producto)

    filas_actualizadas = 0
    bodegas = []      # [{sucursal, filas, skus}]
    conflictos = []   # [{sucursal, motivo}]

    with transaction.atomic():
        for p in productos:
            qs = Producto_Talla.objects.filter(producto=p, talla=talla_actual)
            skus = list(qs.values_list('sku', flat=True))
            if not skus:
                continue
            alias = p.sucursal.alias if p.sucursal else f'Sucursal {p.sucursal_id}'
            if Producto_Talla.objects.filter(producto=p, talla=talla_nueva).exists():
                conflictos.append({
                    'sucursal': alias,
                    'motivo': f'ya existe la talla "{talla_nueva}" en ese producto (fusión manual requerida)',
                })
                continue
            # .update() no dispara auto_now: fijar updated_at explícito.
            n = qs.update(talla=talla_nueva, updated_at=timezone.now())
            filas_actualizadas += n
            bodegas.append({'sucursal': alias, 'filas': n, 'skus': skus})

    logger.info(
        "Talla renombrada globalmente por %s: articulo=%s producto_ref=%s '%s' -> '%s' filas=%s bodegas=%s conflictos=%s",
        request.user.username, producto.articulo, producto.id,
        talla_actual, talla_nueva, filas_actualizadas,
        [b['sucursal'] for b in bodegas], [c['sucursal'] for c in conflictos],
    )

    if not filas_actualizadas and not conflictos:
        return JsonResponse({'success': False, 'error': f'No se encontró la talla "{talla_actual}" en ninguna bodega.'}, status=404)

    return JsonResponse({
        'success': True,
        'talla_actual': talla_actual,
        'talla_nueva': talla_nueva,
        'filas_actualizadas': filas_actualizadas,
        'bodegas': bodegas,
        'conflictos': conflictos,
    })


# =====================================================
# 6. CORRECCIÓN DE CATEGORÍA GLOBAL (todas las bodegas)
# =====================================================

@login_required
@require_POST
def api_editar_categoria_producto_global(request):
    """
    Cambia la categoría de un producto (misma VARIANTE: código normalizado +
    marca + color + género) en TODAS las bodegas del usuario.

    La categoría es parte de la identidad con la que se detectan duplicados:
    corregirla en una sola bodega dejaría fichas de la misma variante con
    identidades mezcladas (y el matching seguiría fallando en las demás),
    por eso replica. SKUs, tallas y stock no se tocan.

    Body JSON: { producto_id, categoria_id }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    categoria_id = data.get('categoria_id')
    if not producto_id or not categoria_id:
        return JsonResponse({'success': False, 'error': 'Faltan datos: producto y categoría nueva.'}, status=400)

    from .models import Categoria
    producto = get_object_or_404(Producto, id=producto_id)
    categoria = get_object_or_404(Categoria, id=categoria_id)

    emp_ids = EmpresaUser.objects.filter(user=request.user, status=True).values_list('empresa_id', flat=True)
    suc_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids).values_list('id', flat=True))
    if producto.sucursal_id not in suc_ids:
        return JsonResponse({'success': False, 'error': 'No tienes acceso a la bodega de este producto.'}, status=403)

    # Misma variante en todas las bodegas, con CUALQUIER categoría (cada ficha
    # puede tener una distinta — justamente eso es lo que se corrige).
    from .utils_producto_match import normalizar_articulo
    objetivo = normalizar_articulo(producto.articulo)
    token = objetivo.split(' ')[0] if objetivo else ''
    if not token:
        return JsonResponse({'success': False, 'error': 'El producto no tiene código válido.'}, status=400)
    candidatos = Producto.objects.filter(
        sucursal_id__in=suc_ids, articulo__icontains=token,
        atributo1_id=producto.atributo1_id, atributo2_id=producto.atributo2_id,
        atributo3_id=producto.atributo3_id,
    ).select_related('sucursal', 'categoria')
    afectados = [p for p in candidatos if normalizar_articulo(p.articulo) == objetivo]

    bodegas = []
    with transaction.atomic():
        for p in afectados:
            if p.categoria_id == categoria.id:
                continue
            bodegas.append({
                'sucursal': p.sucursal.alias if p.sucursal else f'Sucursal {p.sucursal_id}',
                'categoria_anterior': p.categoria.nombre if p.categoria else '-',
            })
            p.categoria = categoria
            p.save(update_fields=['categoria', 'fecha_actualizacion'])

    logger.info(
        "Categoría corregida globalmente por %s: articulo=%s producto_ref=%s -> '%s' fichas=%s bodegas=%s",
        request.user.username, producto.articulo, producto.id, categoria.nombre,
        len(bodegas), [b['sucursal'] for b in bodegas],
    )

    if not bodegas:
        return JsonResponse({'success': False, 'error': 'Ninguna ficha necesitaba cambio: ya tienen esa categoría.'}, status=400)

    return JsonResponse({
        'success': True,
        'categoria_nueva': categoria.nombre,
        'fichas_actualizadas': len(bodegas),
        'bodegas': bodegas,
    })


# =====================================================
# 6-bis. ESPECIALIDAD GLOBAL (todas las bodegas)
# =====================================================

@login_required
@require_POST
def api_editar_especialidad_producto_global(request):
    """
    Aplica especialidades (atributo transversal v1.2) a un producto en TODAS las
    bodegas del usuario, sobre la misma VARIANTE (código normalizado + marca +
    color + género). Espejo de `api_editar_categoria_producto_global`.

    Existe porque la especialidad es una propiedad del PRODUCTO (deporte/uso),
    no de la bodega: al crear/recepcionar solo se escribía en la ficha de la
    sucursal activa y las demás quedaban sin etiquetar, invisibles para los
    filtros y dashboards por especialidad.

    Body JSON: { producto_id, especialidad_ids: [...], modo: 'reemplazar'|'agregar' }
      · reemplazar → deja EXACTAMENTE las enviadas (lista vacía = las limpia)
      · agregar    → suma las enviadas y conserva las que ya tenía
    SKUs, tallas, stock, precios y categoría no se tocan.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    modo = (data.get('modo') or 'reemplazar').lower()
    especialidad_ids = [e for e in (data.get('especialidad_ids') or []) if e]
    if not producto_id:
        return JsonResponse({'success': False, 'error': 'Falta el producto.'}, status=400)
    if modo not in ('reemplazar', 'agregar'):
        return JsonResponse({'success': False, 'error': 'Modo inválido.'}, status=400)
    if modo == 'agregar' and not especialidad_ids:
        return JsonResponse({'success': False, 'error': 'Selecciona al menos una especialidad.'}, status=400)

    from .models import AtributoOpcion, Productos_Atributos, ProductoAtributoValor
    from .utils_producto_match import normalizar_articulo

    producto = get_object_or_404(Producto, id=producto_id)

    emp_ids = EmpresaUser.objects.filter(user=request.user, status=True).values_list('empresa_id', flat=True)
    suc_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids).values_list('id', flat=True))
    if producto.sucursal_id not in suc_ids:
        return JsonResponse({'success': False, 'error': 'No tienes acceso a la bodega de este producto.'}, status=403)

    attr_esp = Productos_Atributos.objects.filter(nombre__iexact='Especialidad').first()
    if attr_esp is None:
        return JsonResponse({'success': False, 'error': 'El atributo "Especialidad" no está creado.'}, status=400)

    opciones = list(AtributoOpcion.objects.filter(atributo=attr_esp, id__in=especialidad_ids))
    if especialidad_ids and len(opciones) != len(set(str(e) for e in especialidad_ids)):
        return JsonResponse({'success': False, 'error': 'Alguna especialidad enviada no existe.'}, status=400)

    # Misma variante en todas las bodegas, con CUALQUIER categoría (una ficha
    # mal categorizada igual debe recibir la especialidad).
    objetivo = normalizar_articulo(producto.articulo)
    token = objetivo.split(' ')[0] if objetivo else ''
    if not token:
        return JsonResponse({'success': False, 'error': 'El producto no tiene código válido.'}, status=400)
    candidatos = Producto.objects.filter(
        sucursal_id__in=suc_ids, articulo__icontains=token,
        atributo1_id=producto.atributo1_id, atributo2_id=producto.atributo2_id,
        atributo3_id=producto.atributo3_id,
    ).select_related('sucursal')
    afectados = [p for p in candidatos if normalizar_articulo(p.articulo) == objetivo]
    if not afectados:
        return JsonResponse({'success': False, 'error': 'No se encontraron fichas de esta variante.'}, status=400)

    ids_ok = {o.id for o in opciones}
    bodegas = []
    with transaction.atomic():
        for p in afectados:
            previas = set(ProductoAtributoValor.objects
                          .filter(producto=p, atributo=attr_esp)
                          .values_list('opcion_id', flat=True))
            objetivo_ids = ids_ok if modo == 'reemplazar' else (previas | ids_ok)
            if previas == objetivo_ids:
                continue
            ProductoAtributoValor.objects.filter(
                producto=p, atributo=attr_esp).exclude(opcion_id__in=objetivo_ids).delete()
            for opcion_id in objetivo_ids - previas:
                ProductoAtributoValor.objects.get_or_create(
                    producto=p, atributo=attr_esp, opcion_id=opcion_id)
            bodegas.append({
                'sucursal': p.sucursal.alias if p.sucursal else f'Sucursal {p.sucursal_id}',
                'producto_id': p.id,
                'antes': len(previas),
                'ahora': len(objetivo_ids),
            })

    etiquetas = [o.valor for o in opciones]
    logger.info(
        "Especialidad aplicada globalmente por %s: articulo=%s modo=%s esp=%s fichas=%s bodegas=%s",
        request.user.username, producto.articulo, modo, etiquetas,
        len(bodegas), [b['sucursal'] for b in bodegas],
    )

    if not bodegas:
        return JsonResponse({'success': False,
                             'error': 'Ninguna ficha necesitaba cambio: ya tienen esas especialidades.'}, status=400)

    return JsonResponse({
        'success': True,
        'modo': modo,
        'especialidades': etiquetas,
        'fichas_actualizadas': len(bodegas),
        'bodegas': bodegas,
    })


# =====================================================
# 5. ACTIVIDAD DE CREACIÓN MANUAL (verGestionProducto)
# =====================================================

@login_required
@require_GET
def api_actividad_creacion_manual(request):
    """KPIs + últimos ingresos hechos con el modal Crear Producto Manual.

    La página verGestionProducto nació centrada en el flujo "crear desde
    recepción" (hoy en desuso); este endpoint alimenta los indicadores y la
    tabla del flujo real: creaciones y sumas de stock del modal Crear Manual
    (movimientos con concepto INGRESO_MANUAL) en la sucursal activa.
    """
    from .utils_tallas import clave_orden_talla

    sucursal_id = (request.session.get('idSucursalActual')
                   or request.session.get('sucursalActual'))
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'Sin sucursal activa en la sesión'})

    hoy = timezone.localdate()
    desde_30 = hoy - timedelta(days=30)
    desde_7 = hoy - timedelta(days=6)

    # Ventana acotada: 30 días y máx. 600 movimientos (un evento del modal
    # genera un movimiento por talla, así que esto cubre cientos de creaciones)
    movs = list(
        Movimientos_Producto.objects
        .filter(concepto='INGRESO_MANUAL', sucursal_destino_id=sucursal_id,
                fecha__gte=desde_30)
        .select_related('ProductoTalla__producto__atributo1',
                        'ProductoTalla__producto__atributo2', 'dte')
        .order_by('-fecha', '-hora')[:600]
    )

    hoy_productos, semana_productos, mes_productos = set(), set(), set()
    kpis = {'hoy_unidades': 0, 'hoy_valor': 0,
            'semana_unidades': 0, 'semana_valor': 0, 'mes_unidades': 0}

    # Un "evento" de creación = mismo producto + día + documento + responsable
    # (el modal graba un movimiento por talla; acá se re-agrupan)
    grupos, orden = {}, []
    for m in movs:
        pt = m.ProductoTalla
        prod = pt.producto if pt else None
        if prod is None:
            continue
        unidades = max(m.cantidad or 0, 0)
        valor = unidades * (m.costo or 0)

        if m.fecha == hoy:
            hoy_productos.add(prod.id)
            kpis['hoy_unidades'] += unidades
            kpis['hoy_valor'] += valor
        if m.fecha >= desde_7:
            semana_productos.add(prod.id)
            kpis['semana_unidades'] += unidades
            kpis['semana_valor'] += valor
        mes_productos.add(prod.id)
        kpis['mes_unidades'] += unidades

        key = (prod.id, m.fecha, m.dte_id, m.responsable)
        if key not in grupos:
            # ¿La ficha nació ese mismo día? → evento "NUEVO"; si no, "+stock"
            creado = getattr(prod, 'fecha_creacion', None)
            es_nuevo = bool(creado) and timezone.localtime(creado).date() == m.fecha
            grupos[key] = {
                'fecha': m.fecha.strftime('%d-%m-%Y'),
                'hora': m.hora.strftime('%H:%M') if m.hora else '',
                'producto_id': prod.id,
                'articulo': prod.articulo,
                'descripcion': prod.descripcion or '',
                'marca': prod.atributo1.valor if prod.atributo1 else '-',
                'color': prod.atributo2.valor if prod.atributo2 else '-',
                'unidades': 0,
                'valor': 0,
                'tallas': set(),
                'responsable': m.responsable or '-',
                'documento': m.referencia_externa or (
                    f"{m.dte.tipo_documento} #{m.dte.numero_documento}" if m.dte else '-'),
                'es_nuevo': es_nuevo,
                # Identidad exacta del evento, para las acciones de la fila
                # (trazabilidad, edición rápida, reasignar DTE). `responsable`
                # sale con '-' cuando viene vacío: el crudo es el que matchea.
                'fecha_iso': m.fecha.isoformat() if m.fecha else '',
                'dte_id': m.dte_id,
                'responsable_raw': m.responsable or '',
                '_skus': {},
            }
            orden.append(key)
        g = grupos[key]
        g['unidades'] += unidades
        g['valor'] += valor
        if pt.talla:
            g['tallas'].add(pt.talla)
            g['_skus'].setdefault(pt.talla, pt.sku)

    actividad = []
    for key in orden[:60]:
        g = grupos[key]
        tallas = sorted(g.pop('tallas'), key=clave_orden_talla)
        skus = g.pop('_skus')
        g['tallas'] = tallas
        g['tallas_n'] = len(tallas)
        # Par talla→SKU: la trazabilidad se consulta por SKU, no por producto.
        g['skus'] = [{'talla': t, 'sku': skus.get(t, '')} for t in tallas]
        actividad.append(g)

    return JsonResponse({
        'success': True,
        'kpis': {
            'hoy_productos': len(hoy_productos),
            'hoy_unidades': kpis['hoy_unidades'],
            'hoy_valor': kpis['hoy_valor'],
            'semana_productos': len(semana_productos),
            'semana_unidades': kpis['semana_unidades'],
            'semana_valor': kpis['semana_valor'],
            'mes_productos': len(mes_productos),
            'mes_unidades': kpis['mes_unidades'],
        },
        'actividad': actividad,
    })


# =====================================================
# 5. ACCIONES RÁPIDAS SOBRE UN INGRESO MANUAL
# =====================================================
#
# La tabla "Actividad reciente" de verGestionProducto agrupa los movimientos
# INGRESO_MANUAL por (producto, fecha, DTE, responsable): ese es el "evento"
# real que el usuario hizo con el modal Crear Manual. Sobre ese mismo evento
# operan las acciones de abajo:
#
#   * edición rápida  -> sumar unidades a tallas YA existentes y corregir
#                        descripción/precios, replicando a las demás bodegas.
#   * reasignar DTE   -> mover el ingreso completo a la factura correcta cuando
#                        se eligió la equivocada en el modal.
#
# Un ingreso manual deja rastro del DTE en CINCO tablas (Movimientos_Producto,
# LoteProducto, Dte_Productos, Productos_Recepcionados y la compra manual). Si
# solo se corrige una, el resto sigue apuntando a la factura equivocada: por eso
# la reasignación las mueve todas dentro de una misma transacción.


def _sucursal_activa_validada(request):
    """
    (sucursal_id, error_response). La sucursal activa de la sesión, verificada
    contra las bodegas del usuario — no se confía en lo que quedó en sesión.
    """
    sucursal_id = (request.session.get('idSucursalActual')
                   or request.session.get('sucursalActual'))
    if not sucursal_id:
        return None, JsonResponse(
            {'success': False, 'error': 'Sin sucursal activa en la sesión.'}, status=400)
    if int(sucursal_id) not in set(_sucursales_usuario(request)):
        return None, _sin_acceso('La sucursal activa no pertenece a tus empresas.')
    return int(sucursal_id), None


def _parse_fecha_iso(valor):
    """'YYYY-MM-DD' -> date, o None si no se puede parsear."""
    try:
        return datetime.strptime(str(valor).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _movimientos_del_evento(producto_id, fecha, dte_id, responsable, sucursal_id):
    """
    Movimientos INGRESO_MANUAL que componen un evento de la tabla Actividad.

    Se usa exactamente la misma clave con la que `api_actividad_creacion_manual`
    agrupa las filas — (producto, fecha, dte, responsable) — para que lo que el
    usuario ve en pantalla sea lo que se toca en la BD, ni una unidad más.
    """
    qs = Movimientos_Producto.objects.filter(
        concepto='INGRESO_MANUAL',
        ProductoTalla__producto_id=producto_id,
        fecha=fecha,
        sucursal_destino_id=sucursal_id,
        responsable=responsable or '',
    )
    qs = qs.filter(dte_id=dte_id) if dte_id else qs.filter(dte__isnull=True)
    return qs.select_related('ProductoTalla', 'dte')


def _producto_en_alcance(request, producto_id):
    """(producto, error_response). Producto restringido a las bodegas del usuario."""
    producto = (
        Producto.objects
        .select_related('sucursal', 'categoria', 'atributo1', 'atributo2', 'atributo3')
        .filter(id=producto_id, sucursal_id__in=_sucursales_usuario(request))
        .first()
    )
    if producto is None:
        if Producto.objects.filter(id=producto_id).exists():
            return None, _sin_acceso('Ese producto pertenece a una empresa a la que no tienes acceso.')
        return None, JsonResponse({'success': False, 'error': 'Producto no encontrado.'}, status=404)
    return producto, None


@login_required
@require_GET
def api_evento_ingreso_manual(request):
    """
    Ficha de un evento de creación manual, para el modal de Edición Rápida.

    Parámetros GET: producto_id, fecha (YYYY-MM-DD), dte_id (opcional),
    responsable (tal como lo grabó el movimiento, puede venir vacío).
    """
    from .utils_tallas import clave_orden_talla

    producto_id = request.GET.get('producto_id')
    fecha = _parse_fecha_iso(request.GET.get('fecha'))
    dte_id = request.GET.get('dte_id') or None
    responsable = request.GET.get('responsable', '')

    if not producto_id or fecha is None:
        return JsonResponse({'success': False, 'error': 'Faltan producto_id o fecha.'}, status=400)

    sucursal_id, err = _sucursal_activa_validada(request)
    if err:
        return err
    producto, err = _producto_en_alcance(request, producto_id)
    if err:
        return err

    movimientos = list(_movimientos_del_evento(
        producto.id, fecha, dte_id, responsable, sucursal_id))
    unidades_evento = {}
    for m in movimientos:
        unidades_evento[m.ProductoTalla_id] = (
            unidades_evento.get(m.ProductoTalla_id, 0) + max(m.cantidad or 0, 0))

    tallas = [{
        'producto_talla_id': pt.id,
        'talla': pt.talla,
        'sku': pt.sku,
        'stock': pt.stock or 0,
        'unidades_evento': unidades_evento.get(pt.id, 0),
    } for pt in Producto_Talla.objects.filter(producto=producto)]
    tallas.sort(key=lambda t: clave_orden_talla(t['talla'] or ''))

    # Mismas bodegas que alcanzará la propagación: mismo código en las
    # sucursales del usuario. Se muestran para que el checkbox "todas las
    # bodegas" no sea un salto al vacío.
    bodegas = list(
        Producto.objects
        .filter(articulo=producto.articulo, sucursal_id__in=_sucursales_usuario(request))
        .values('id', 'sucursal__alias')
        .annotate(stock=Coalesce(Sum('producto_talla__stock'), 0))
        .order_by('sucursal__alias')
    )

    dte = movimientos[0].dte if movimientos and movimientos[0].dte else None

    return JsonResponse({
        'success': True,
        'producto': {
            'id': producto.id,
            'articulo': producto.articulo,
            'descripcion': producto.descripcion or '',
            'marca': producto.atributo1.valor if producto.atributo1 else '',
            'color': producto.atributo2.valor if producto.atributo2 else '',
            'genero': producto.atributo3.valor if producto.atributo3 else '',
            'categoria': producto.categoria.nombre if producto.categoria else '',
            'costo': int(producto.costo or 0),
            'sobreprecio': int(producto.sobreprecio or 0),
            'precioventa': int(producto.precioventa or 0),
            'sucursal': producto.sucursal.alias if producto.sucursal else '',
        },
        'tallas': tallas,
        'bodegas': [{
            'producto_id': b['id'],
            'sucursal': b['sucursal__alias'] or '—',
            'stock': b['stock'],
        } for b in bodegas],
        'dte': {
            'id': dte.id,
            'tipo_documento': dte.tipo_documento,
            'numero_documento': dte.numero_documento,
            'fecha': dte.fecha_emision.strftime('%d/%m/%Y') if dte.fecha_emision else '',
            'emisor': dte.emisor.nombre if dte.emisor else '',
            'emisor_id': dte.emisor_id,
        } if dte else None,
        'unidades_evento': sum(unidades_evento.values()),
    })


def _vincular_linea_a_compra_dte(dte, producto, producto_talla, talla, cantidad,
                                 sucursal, responsable_nombre):
    """
    Deja una suma de stock registrada en la compra y en el detalle del DTE,
    igual que hace `crear_producto_manual`.

    Sin esto el stock entra pero el DTE no lo refleja, y la factura termina
    declarando menos unidades de las que realmente ingresaron a bodega.
    """
    from .views import obtener_siguiente_correlativo

    hoy = timezone.localdate()
    proveedor = dte.emisor

    compra = Compras.objects.filter(
        empresa=proveedor,
        estado__in=['ACTIVA', 'COMPLETADA'],
        nombre__startswith='Compra Manual -',
        fecha=hoy,
    ).first()
    if compra is None:
        compra = Compras.objects.create(
            empresa=proveedor,
            nombre=f"Compra Manual - {proveedor.nombre} - {hoy.strftime('%d/%m/%Y')}",
            correlativo=obtener_siguiente_correlativo(sucursal, 'COMPRA'),
            responsable=responsable_nombre,
            temporada='',
            fecha=hoy,
            estado='COMPLETADA',
            tipo='inicial',
        )

    compra_producto = Compras_Producto.objects.create(
        compras=compra,
        nombre=producto.articulo,
        descripcion=producto.descripcion or '',
        atributo1=producto.atributo1.valor if producto.atributo1 else '',
        atributo2=producto.atributo2.valor if producto.atributo2 else '',
        atributo3=producto.atributo3.valor if producto.atributo3 else '',
        atributo4='',
        tipo_talla=producto.tipo_talla or 'CL',
        costo=int(producto.costo or 0),
        precioSugerido=int(producto.precioventa or 0),
        sucursal_destino=sucursal,
    )
    cpt = Compras_Producto_Talla.objects.create(
        compra_producto=compra_producto,
        stock=cantidad,
        talla=talla,
        producto_talla=producto_talla,
        unidades_recibidas=cantidad,
        estado_item='recibido_completo',
    )
    dte_prod = Dte_Productos.objects.create(
        dte=dte,
        productoTalla=producto_talla,
        descripcion=f"{producto.articulo} - Talla {talla}",
        costo=int(producto.costo or 0),
        sobreprecio=int(producto.sobreprecio or 0),
        precio=int(producto.precioventa or 0),
        precio_unitario=int(producto.costo or 0),
        monto_item=int(producto.costo or 0) * cantidad,
        stock=cantidad,
    )
    Productos_Recepcionados.objects.create(
        compra_producto_talla=cpt,
        dte=dte,
        dte_producto=dte_prod,
        producto_talla=producto_talla,
        stockArribado=cantidad,
        cantidad_esperada=cantidad,
        estado='RECEPCIONADO_OK',
        sucursal_destino=sucursal,
        recepcionado_por=responsable_nombre,
        fecha_recepcion=timezone.now(),
    )
    return dte_prod


@login_required
@require_POST
def api_sumar_stock_rapido(request):
    """
    Suma unidades a tallas YA existentes de un producto, contra el mismo DTE del
    evento (o sin documento si el usuario lo desmarca).

    Solo toca tallas existentes: para una talla nueva está el botón "Sumar", que
    reabre Crear Manual con el código precargado y pide costo/precio/guía.
    """
    from .views import registrar_movimiento_producto

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    lineas = data.get('lineas') or []
    vincular_dte = bool(data.get('vincular_dte', True))
    dte_id = data.get('dte_id') or None

    if not producto_id or not lineas:
        return JsonResponse({'success': False, 'error': 'Faltan producto o líneas.'}, status=400)

    sucursal_id, err = _sucursal_activa_validada(request)
    if err:
        return err
    producto, err = _producto_en_alcance(request, producto_id)
    if err:
        return err

    dte = None
    if vincular_dte and dte_id:
        dte = Dte.objects.select_related('emisor').filter(id=dte_id).first()
        if dte is None:
            return JsonResponse({'success': False, 'error': 'El DTE indicado no existe.'}, status=404)
        if dte.emisor is None:
            return JsonResponse(
                {'success': False,
                 'error': 'El DTE no tiene emisor: no se puede registrar la compra. '
                          'Desmarca "vincular al documento" o corrige el DTE.'}, status=400)

    # Normalizar líneas: solo tallas de ESTE producto y cantidades > 0.
    tallas_validas = {pt.id: pt for pt in Producto_Talla.objects.filter(producto=producto)}
    pendientes = []
    for linea in lineas:
        try:
            pt_id = int(linea.get('producto_talla_id'))
            cantidad = int(linea.get('cantidad') or 0)
        except (TypeError, ValueError):
            continue
        if cantidad <= 0 or pt_id not in tallas_validas:
            continue
        pendientes.append((tallas_validas[pt_id], cantidad))

    if not pendientes:
        return JsonResponse(
            {'success': False, 'error': 'No indicaste unidades para ninguna talla.'}, status=400)

    sucursal = Sucursal.objects.get(id=sucursal_id)
    responsable = request.session.get('nombreUsuario', '') or request.user.get_username()
    responsable_nombre = request.user.get_full_name() or responsable
    ref_externa = f'{dte.tipo_documento} #{dte.numero_documento}' if dte else 'AJUSTE MANUAL'

    detalle = []
    with transaction.atomic():
        for producto_talla, cantidad in pendientes:
            registrar_movimiento_producto(
                producto_talla=producto_talla,
                concepto='INGRESO_MANUAL',
                cantidad=cantidad,
                responsable=responsable,
                dte=dte,
                sucursal_origen=sucursal,
                sucursal_destino=sucursal,
                observaciones=(f'Suma rápida de stock - {producto.articulo} '
                               f'Talla {producto_talla.talla}'),
                referencia_externa=ref_externa,
                crear_lote_fifo=True,
            )
            if dte is not None:
                _vincular_linea_a_compra_dte(
                    dte, producto, producto_talla, producto_talla.talla,
                    cantidad, sucursal, responsable_nombre)
            detalle.append({'talla': producto_talla.talla, 'unidades': cantidad})

    total = sum(d['unidades'] for d in detalle)
    logger.info(
        "Suma rápida de stock: producto_id=%s sucursal_id=%s unidades=%s dte_id=%s usuario=%s",
        producto.id, sucursal_id, total, dte.id if dte else None, request.user.username,
    )
    return JsonResponse({
        'success': True,
        'unidades': total,
        'tallas': len(detalle),
        'detalle': detalle,
        'documento': ref_externa,
    })


def _piezas_del_evento(producto, movimientos, dte_origen):
    """
    Todo lo que un ingreso manual dejó colgando del DTE, para moverlo o para
    mostrar el impacto antes de mover nada.

    Las líneas del DTE y las recepciones se acotan a las tallas del evento; las
    líneas de compra solo se consideran cuando TODAS las tallas de esa
    Compras_Producto son del evento (si la compra mezcla otras cosas se informa
    y no se toca).
    """
    mov_ids = [m.id for m in movimientos]
    pt_ids = sorted({m.ProductoTalla_id for m in movimientos})

    lotes = LoteProducto.objects.filter(movimiento_id__in=mov_ids)

    lineas_dte = Dte_Productos.objects.none()
    recepciones = Productos_Recepcionados.objects.none()
    compras_producto_ids, compras_producto_mixtas = [], []

    if dte_origen is not None and pt_ids:
        lineas_dte = Dte_Productos.objects.filter(
            dte=dte_origen, productoTalla_id__in=pt_ids)
        recepciones = Productos_Recepcionados.objects.filter(
            dte=dte_origen, producto_talla_id__in=pt_ids)

        cp_ids = set(
            Compras_Producto_Talla.objects
            .filter(producto_talla_id__in=pt_ids,
                    compra_producto__nombre=producto.articulo)
            .values_list('compra_producto_id', flat=True)
        )
        for cp_id in cp_ids:
            tallas_cp = set(
                Compras_Producto_Talla.objects
                .filter(compra_producto_id=cp_id)
                .values_list('producto_talla_id', flat=True)
            )
            if tallas_cp and tallas_cp.issubset(set(pt_ids)):
                compras_producto_ids.append(cp_id)
            else:
                compras_producto_mixtas.append(cp_id)

    return {
        'movimientos': movimientos,
        'lotes': lotes,
        'lineas_dte': lineas_dte,
        'recepciones': recepciones,
        'compras_producto_ids': compras_producto_ids,
        'compras_producto_mixtas': compras_producto_mixtas,
    }


def _dte_destino_valido(dte_id):
    """(dte, mensaje_error). Valida el DTE al que se quiere mover el ingreso."""
    dte = Dte.objects.select_related('emisor').filter(id=dte_id).first()
    if dte is None:
        return None, 'El DTE de destino no existe.'
    if (dte.estado_dte or '').upper() in ('ANULADO', 'CANCELADO', 'RECHAZADO'):
        return None, f'El DTE de destino está {dte.estado_dte}: elige otro documento.'
    if (dte.tipo_transaccion or '').upper() not in ('COMPRA', ''):
        return None, ('El documento de destino no es una compra '
                      f'(tipo_transaccion={dte.tipo_transaccion}).')
    return dte, None


@login_required
@require_GET
def api_preview_reasignar_dte(request):
    """
    Qué se movería al reasignar el DTE de un ingreso manual. Read-only: es la
    pantalla de confirmación, no cambia nada.
    """
    producto_id = request.GET.get('producto_id')
    fecha = _parse_fecha_iso(request.GET.get('fecha'))
    dte_id = request.GET.get('dte_id') or None
    nuevo_dte_id = request.GET.get('nuevo_dte_id') or None
    responsable = request.GET.get('responsable', '')

    if not producto_id or fecha is None:
        return JsonResponse({'success': False, 'error': 'Faltan producto_id o fecha.'}, status=400)

    sucursal_id, err = _sucursal_activa_validada(request)
    if err:
        return err
    producto, err = _producto_en_alcance(request, producto_id)
    if err:
        return err

    movimientos = list(_movimientos_del_evento(
        producto.id, fecha, dte_id, responsable, sucursal_id))
    if not movimientos:
        return JsonResponse(
            {'success': False,
             'error': 'No se encontró el ingreso: puede que ya se haya corregido.'}, status=404)

    dte_origen = movimientos[0].dte
    piezas = _piezas_del_evento(producto, movimientos, dte_origen)

    nuevo_dte = None
    if nuevo_dte_id:
        nuevo_dte, error = _dte_destino_valido(nuevo_dte_id)
        if error:
            return JsonResponse({'success': False, 'error': error}, status=400)
        if dte_origen and int(nuevo_dte_id) == dte_origen.id:
            return JsonResponse(
                {'success': False, 'error': 'El DTE de destino es el mismo que el actual.'},
                status=400)

    unidades = sum(max(m.cantidad or 0, 0) for m in movimientos)
    lotes_consumidos = piezas['lotes'].filter(
        cantidad_disponible__lt=F('cantidad_inicial')).count()

    # Las líneas del DTE se identifican por (documento, talla). Si el DTE
    # equivocado ADEMÁS tiene una recepción real de estas mismas tallas, sus
    # líneas entran en el mismo filtro y se moverían de más. No hay campo que
    # las distinga, así que se compara el total contra las unidades del ingreso
    # y se avisa cuando no cuadra: es el usuario quien decide.
    unidades_lineas_dte = (
        piezas['lineas_dte'].aggregate(t=Coalesce(Sum('stock'), 0))['t'] or 0)

    avisos = []
    if lotes_consumidos:
        avisos.append(
            f'{lotes_consumidos} lote(s) FIFO ya tienen ventas. Se reetiqueta el documento del '
            f'lote, pero los DTE de venta ya emitidos no cambian.')
    if piezas['lineas_dte'].exists() and unidades_lineas_dte != unidades:
        avisos.append(
            f'REVISA: las líneas del DTE actual suman {unidades_lineas_dte} uds y este ingreso '
            f'fue de {unidades} uds. La factura puede tener además una recepción real de estas '
            f'mismas tallas, y esas líneas también se moverían.')
    if piezas['compras_producto_mixtas']:
        avisos.append(
            f'{len(piezas["compras_producto_mixtas"])} línea(s) de compra mezclan tallas de otros '
            f'ingresos: NO se moverán, quedan en la compra actual.')
    if nuevo_dte and dte_origen and nuevo_dte.emisor_id != dte_origen.emisor_id:
        avisos.append(
            f'Cambia el proveedor: de "{dte_origen.emisor.nombre if dte_origen.emisor else "—"}" '
            f'a "{nuevo_dte.emisor.nombre if nuevo_dte.emisor else "—"}".')

    return JsonResponse({
        'success': True,
        'producto': {'id': producto.id, 'articulo': producto.articulo,
                     'descripcion': producto.descripcion or ''},
        'dte_origen': {
            'id': dte_origen.id,
            'etiqueta': f'{dte_origen.tipo_documento} #{dte_origen.numero_documento}',
            'emisor': dte_origen.emisor.nombre if dte_origen.emisor else '—',
            'emisor_id': dte_origen.emisor_id,
        } if dte_origen else None,
        'dte_destino': {
            'id': nuevo_dte.id,
            'etiqueta': f'{nuevo_dte.tipo_documento} #{nuevo_dte.numero_documento}',
            'emisor': nuevo_dte.emisor.nombre if nuevo_dte.emisor else '—',
        } if nuevo_dte else None,
        'impacto': {
            'unidades': unidades,
            'movimientos': len(movimientos),
            'lotes_fifo': piezas['lotes'].count(),
            'lineas_dte': piezas['lineas_dte'].count(),
            'unidades_lineas_dte': unidades_lineas_dte,
            'recepciones': piezas['recepciones'].count(),
            'lineas_compra': len(piezas['compras_producto_ids']),
            'tallas': sorted({m.ProductoTalla.talla for m in movimientos if m.ProductoTalla}),
        },
        'avisos': avisos,
    })


@login_required
@require_POST
def api_reasignar_dte_ingreso(request):
    """
    Mueve un ingreso manual completo al DTE correcto.

    Reapunta, en una sola transacción: los movimientos de stock, los lotes FIFO
    que generaron, las líneas del detalle del DTE, las recepciones y —cuando la
    línea de compra es exclusiva de este ingreso— la compra manual del proveedor
    correcto. El stock NO se toca: las unidades ya están en bodega, lo que
    estaba mal era el documento que las respalda.
    """
    from .views import obtener_siguiente_correlativo

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    producto_id = data.get('producto_id')
    fecha = _parse_fecha_iso(data.get('fecha'))
    dte_id = data.get('dte_id') or None
    nuevo_dte_id = data.get('nuevo_dte_id')
    responsable = data.get('responsable', '')
    motivo = (data.get('motivo') or '').strip()

    if not producto_id or fecha is None or not nuevo_dte_id:
        return JsonResponse(
            {'success': False, 'error': 'Faltan producto_id, fecha o nuevo_dte_id.'}, status=400)

    sucursal_id, err = _sucursal_activa_validada(request)
    if err:
        return err
    producto, err = _producto_en_alcance(request, producto_id)
    if err:
        return err

    nuevo_dte, error = _dte_destino_valido(nuevo_dte_id)
    if error:
        return JsonResponse({'success': False, 'error': error}, status=400)

    resumen = {}
    with transaction.atomic():
        # Lock de los movimientos: dos usuarios corrigiendo el mismo ingreso a la
        # vez dejarían el DTE a medio mover.
        movimientos = list(
            _movimientos_del_evento(producto.id, fecha, dte_id, responsable, sucursal_id)
            .select_for_update(of=('self',))
        )
        if not movimientos:
            return JsonResponse(
                {'success': False,
                 'error': 'No se encontró el ingreso: puede que otro usuario ya lo haya corregido.'},
                status=404)

        dte_origen = movimientos[0].dte
        if dte_origen and dte_origen.id == nuevo_dte.id:
            return JsonResponse(
                {'success': False, 'error': 'El DTE de destino es el mismo que el actual.'},
                status=400)

        piezas = _piezas_del_evento(producto, movimientos, dte_origen)
        etiqueta_nueva = f'{nuevo_dte.tipo_documento} #{nuevo_dte.numero_documento}'
        etiqueta_vieja = (f'{dte_origen.tipo_documento} #{dte_origen.numero_documento}'
                          if dte_origen else 'sin documento')
        nota = (f' | DTE reasignado {etiqueta_vieja} -> {etiqueta_nueva} el '
                f'{timezone.localtime().strftime("%d-%m-%Y %H:%M")} por '
                f'{request.user.get_username()}'
                + (f': {motivo}' if motivo else ''))

        for m in movimientos:
            m.dte = nuevo_dte
            m.referencia_externa = etiqueta_nueva
            m.observaciones = ((m.observaciones or '') + nota)[:1000]
            m.save(update_fields=['dte', 'referencia_externa', 'observaciones'])

        resumen['movimientos'] = len(movimientos)
        resumen['lotes_fifo'] = piezas['lotes'].update(dte=nuevo_dte)
        resumen['lineas_dte'] = piezas['lineas_dte'].update(dte=nuevo_dte)
        resumen['recepciones'] = piezas['recepciones'].update(dte=nuevo_dte)

        # La compra manual solo se mueve si cambió el proveedor y la línea de
        # compra es exclusiva de este ingreso.
        resumen['lineas_compra'] = 0
        cambio_proveedor = (
            piezas['compras_producto_ids']
            and nuevo_dte.emisor_id
            and (dte_origen is None or nuevo_dte.emisor_id != dte_origen.emisor_id)
        )
        if cambio_proveedor:
            hoy = timezone.localdate()
            proveedor = nuevo_dte.emisor
            compra = Compras.objects.filter(
                empresa=proveedor,
                estado__in=['ACTIVA', 'COMPLETADA'],
                nombre__startswith='Compra Manual -',
                fecha=hoy,
            ).first()
            if compra is None:
                compra = Compras.objects.create(
                    empresa=proveedor,
                    nombre=f"Compra Manual - {proveedor.nombre} - {hoy.strftime('%d/%m/%Y')}",
                    correlativo=obtener_siguiente_correlativo(
                        Sucursal.objects.get(id=sucursal_id), 'COMPRA'),
                    responsable=request.user.get_full_name() or request.user.get_username(),
                    temporada='',
                    fecha=hoy,
                    estado='COMPLETADA',
                    tipo='inicial',
                )
            resumen['lineas_compra'] = Compras_Producto.objects.filter(
                id__in=piezas['compras_producto_ids']).update(compras=compra)

        resumen['compras_no_movidas'] = len(piezas['compras_producto_mixtas'])

    logger.info(
        "Reasignación de DTE: producto_id=%s fecha=%s dte_origen=%s dte_destino=%s "
        "sucursal_id=%s usuario=%s resumen=%s motivo=%s",
        producto.id, fecha, dte_id, nuevo_dte.id, sucursal_id,
        request.user.username, resumen, motivo or '(sin motivo)',
    )

    return JsonResponse({
        'success': True,
        'documento': etiqueta_nueva,
        'resumen': resumen,
    })
