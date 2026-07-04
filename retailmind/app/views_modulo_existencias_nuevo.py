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
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.core.paginator import Paginator

from .models import (
    Producto, Producto_Talla, Movimientos_Producto, LoteProducto,
    Sucursal, EmpresaUser, Traspaso, Traspaso_Detalle,
    PendienteDespacho, HistorialCambioPrecio,
    CONCEPTO_MOVIMIENTO_CHOICES, TIPO_MOVIMIENTO_CHOICES,
)

logger = logging.getLogger('app')


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


def _empresa_ids_producto(producto):
    """IDs de sucursal de la empresa dueña del producto (para acotar la búsqueda)."""
    empresa_id = producto.sucursal.empresa_id if producto.sucursal else None
    if empresa_id is None:
        return None, [producto.sucursal_id] if producto.sucursal_id else []
    sucursal_ids = list(
        Sucursal.objects.filter(empresa_id=empresa_id).values_list('id', flat=True)
    )
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

    # 1) Resolver el artículo (código) a partir del SKU o del código directo.
    pt_ref = None
    if sku.isdigit():
        pt_ref = (
            Producto_Talla.objects
            .select_related('producto', 'producto__sucursal')
            .filter(sku=int(sku))
            .first()
        )
    if pt_ref is None:
        pt_ref = (
            Producto_Talla.objects
            .select_related('producto', 'producto__sucursal')
            .filter(producto__articulo__iexact=sku)
            .first()
        )
    if pt_ref is None:
        return JsonResponse({'success': False, 'error': f'SKU o código «{sku}» no encontrado.'}, status=404)

    articulo = pt_ref.producto.articulo
    empresa_id, sucursal_ids = _empresa_ids_producto(pt_ref.producto)

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
            'sucursal_origen', 'sucursal_destino', 'dte', 'ticket',
        )
    )
    if fecha_desde:
        movimientos_qs = movimientos_qs.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        movimientos_qs = movimientos_qs.filter(fecha__lte=fecha_hasta)

    movimientos = list(movimientos_qs.order_by('fecha', 'hora', 'id'))

    # 4) Kardex por serie (bodega dueña del SKU + talla) con saldo correcto.
    #    Cada Producto_Talla vive en UNA sucursal (producto.sucursal): esa es la
    #    "bodega" del saldo. En un TRASPASO, el movimiento con cantidad negativa
    #    es una salida de esa bodega; el positivo, una entrada.
    saldos_por_serie = {}
    movimientos_data = []
    for m in movimientos:
        pt = m.ProductoTalla
        bodega = pt.producto.sucursal
        bodega_alias = bodega.alias if bodega else '-'
        talla = pt.talla
        serie = (pt.producto.sucursal_id, talla)

        cant = m.cantidad
        if m.tipo_movimiento in _TIPOS_ENTRADA:
            delta = abs(cant)
        elif m.tipo_movimiento in _TIPOS_SALIDA:
            delta = -abs(cant)
        else:
            # TRASPASO / AJUSTE / otros: respetar el signo con que se guardó.
            delta = cant
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
    distribucion_list = sorted(
        distribucion.values(), key=lambda d: (not d['es_cd'], d['bodega'])
    )
    for d in distribucion_list:
        d['tallas'].sort(key=lambda t: t['talla'])

    # 7) Tallas y bodegas disponibles para poblar los filtros del frontend.
    tallas_disponibles = sorted({pt.talla for pt in productos_talla})
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

    resumen = {
        'total_movimientos': len(movimientos_data),
        'total_entradas': total_entradas,
        'total_salidas': total_salidas,
        'total_vendido': total_vendido,
        'traspasos': traspasos_count,
        'stock_actual': stock_total,
        'primer_movimiento': movimientos_data[0]['fecha'] if movimientos_data else '-',
        'ultimo_movimiento': movimientos_data[-1]['fecha'] if movimientos_data else '-',
    }

    return JsonResponse({
        'success': True,
        'producto': producto_info,
        'movimientos': movimientos_data,
        'distribucion': distribucion_list,
        'timeline': timeline,
        'timeline_omitidos': timeline_omitidos,
        'resumen': resumen,
        'filtros': {
            'tallas': tallas_disponibles,
            'bodegas': bodegas_disponibles,
        },
        'sucursal_actual_id': sucursal_actual_id,
    })


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

    filtro = (
        Q(producto__articulo__icontains=q) |
        Q(producto__descripcion__icontains=q)
    )
    if q.isdigit():
        filtro |= Q(sku__icontains=q)

    productos_talla = (
        Producto_Talla.objects
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

    sucursales = Sucursal.objects.filter(
        empresa_id=empresa_id,
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
    page_num = int(request.GET.get('page', 1))

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
    """Devuelve pendientes de despacho agrupados por sucursal destino."""
    sucursal_id = request.session.get('idSucursalActual')

    pendientes = PendienteDespacho.objects.filter(
        sucursal_origen_id=sucursal_id,
        estado__in=['PENDIENTE', 'PARCIAL'],
    ).select_related(
        'producto_talla', 'producto_talla__producto',
        'producto_talla__producto__atributo1',
        'sucursal_destino',
    ).order_by('sucursal_destino__alias', 'producto_talla__producto__articulo')

    por_sucursal = {}
    for p in pendientes:
        suc_alias = p.sucursal_destino.alias
        if suc_alias not in por_sucursal:
            por_sucursal[suc_alias] = {
                'sucursal_id': p.sucursal_destino_id,
                'alias': suc_alias,
                'items': [],
                'total_unidades': 0,
            }
        restante = p.cantidad_restante
        por_sucursal[suc_alias]['items'].append({
            'pendiente_id': p.id,
            'sku': str(p.producto_talla.sku),
            'articulo': p.producto_talla.producto.articulo,
            'talla': p.producto_talla.talla,
            'cantidad_total': p.cantidad,
            'cantidad_despachada': p.cantidad_despachada,
            'cantidad_restante': restante,
            'marca': p.producto_talla.producto.atributo1.valor if p.producto_talla.producto.atributo1 else '-',
        })
        por_sucursal[suc_alias]['total_unidades'] += restante

    return JsonResponse({
        'success': True,
        'pendientes_por_sucursal': list(por_sucursal.values()),
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

    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'mensaje': f'Se crearon {len(traspasos_creados)} despachos exitosamente.',
        'traspasos': traspasos_creados,
    })


_ESTADOS_TRASPASO_VALIDOS = {'PENDIENTE', 'APROBADO', 'EN_TRANSITO', 'RECIBIDO', 'RECHAZADO', 'ANULADO'}


@login_required
@require_GET
def api_historial_despachos(request):
    """
    Historial de despachos (Traspaso) creados desde la sucursal actual como origen.
    Parámetros: estado, fecha_desde, fecha_hasta, page.
    """
    sucursal_id = request.session.get('idSucursalActual')
    estado = request.GET.get('estado', '').strip().upper()
    fecha_desde = request.GET.get('fecha_desde', '')
    fecha_hasta = request.GET.get('fecha_hasta', '')
    page_num = int(request.GET.get('page', 1))

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

    # OJO: `sku` NO es único en la BD (dato legacy: hay SKUs repetidos). Antes
    # se usaba .get(sku=) y reventaba con MultipleObjectsReturned → la
    # trazabilidad no se mostraba. Se resuelve tolerando duplicados: se prefiere
    # la talla de la sucursal activa y se avisa que el SKU está duplicado.
    _qs_pt = Producto_Talla.objects.select_related(
        'producto', 'producto__atributo1', 'producto__atributo2',
        'producto__categoria', 'producto__sucursal',
    ).filter(sku=sku)
    _n_sku = _qs_pt.count()
    if _n_sku == 0:
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

    # --- Movimientos ---
    movimientos = Movimientos_Producto.objects.filter(
        ProductoTalla=producto_talla,
    ).select_related(
        'sucursal_origen', 'sucursal_destino', 'dte', 'ticket',
    ).order_by('-fecha', '-hora')[:200]

    movimientos_data = [{
        'id': m.id,
        'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
        'hora': m.hora.strftime('%H:%M') if m.hora else '',
        'tipo': m.tipo_movimiento,
        'concepto': m.get_concepto_display(),
        'cantidad': m.cantidad,
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

    # --- Lotes FIFO ---
    lotes = LoteProducto.objects.filter(
        producto_talla=producto_talla,
    ).order_by('-fecha_ingreso')[:50]

    lotes_data = [{
        'id': l.id,
        'fecha_ingreso': l.fecha_ingreso.strftime('%d/%m/%Y') if l.fecha_ingreso else '',
        'cantidad_inicial': l.cantidad_inicial,
        'cantidad_disponible': l.cantidad_disponible,
        'costo_unitario': l.costo_unitario,
        'precio_venta': l.precio_venta_unitario,
        'activo': l.activo,
        'agotado': l.agotado,
        'numero_lote': l.numero_lote or '-',
        'porcentaje_consumido': round(l.porcentaje_consumido, 1),
    } for l in lotes]

    # --- Traspasos ---
    traspasos_detalle = Traspaso_Detalle.objects.filter(
        producto_talla=producto_talla,
    ).select_related(
        'traspaso', 'traspaso__sucursal_origen', 'traspaso__sucursal_destino',
    ).order_by('-traspaso__fecha_solicitud')[:50]

    traspasos_data = [{
        'numero': td.traspaso.numero_traspaso,
        'fecha': td.traspaso.fecha_solicitud.strftime('%d/%m/%Y') if td.traspaso.fecha_solicitud else '',
        'origen': td.traspaso.sucursal_origen.alias,
        'destino': td.traspaso.sucursal_destino.alias,
        'estado': td.traspaso.estado,
        'cantidad_solicitada': td.cantidad_solicitada,
        'cantidad_recibida': td.cantidad_recibida,
        'costo': td.costo,
        'precio_venta': td.precio_venta,
    } for td in traspasos_detalle]

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
        timeline.append({
            'fecha': m['fecha'],
            'hora': m['hora'],
            'tipo': 'movimiento',
            'icono': 'ri-arrow-left-right-line',
            'color': '#00D4AA' if m['tipo'] == 'INGRESO' else '#FF6B6B' if m['tipo'] == 'EGRESO' else '#0066FF',
            'titulo': m['concepto'],
            'detalle': f"Cantidad: {m['cantidad']} | {m['origen']} → {m['destino']}",
            'responsable': m['responsable'],
        })
    for t in traspasos_data:
        timeline.append({
            'fecha': t['fecha'],
            'hora': '00:00',
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

    timeline.sort(key=lambda x: x.get('fecha', ''), reverse=True)

    return JsonResponse({
        'success': True,
        'producto': producto_info,
        'movimientos': movimientos_data,
        'lotes': lotes_data,
        'traspasos': traspasos_data,
        'historial_precios': precios_data,
        'pendientes_despacho': pendientes_data,
        'timeline': timeline[:100],
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

    producto = get_object_or_404(Producto, id=producto_id)
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
                    productos_mismos = Producto.objects.filter(
                        articulo=producto.articulo,
                        atributo1=producto.atributo1,
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

    try:
        with transaction.atomic():
            for item in productos_data:
                try:
                    producto = Producto.objects.get(id=item.get('producto_id'))
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
                    resultados['errores'].append(f"Producto ID {item.get('producto_id')} no encontrado")
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
