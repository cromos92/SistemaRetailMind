"""
Vistas del módulo Existencias — funcionalidades nuevas:
  1. Tarjeta de Movimiento por Producto
  2. Despacho a Todas Sucursales
  3. Trazabilidad Completa de Producto
  4. Modificación de Precios y Costos
"""
import json
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


# =====================================================
# 1. TARJETA DE MOVIMIENTO POR PRODUCTO
# =====================================================

@login_required
@require_GET
def tarjeta_movimiento_producto(request):
    """Vista principal: tarjeta de movimiento por producto."""
    sucursal_id = request.session.get('idSucursalActual')
    return render(request, 'vistas/modulo_existencias/tarjeta_movimiento_producto.html', {
        'sucursal_id': sucursal_id,
    })


@login_required
@require_GET
def api_tarjeta_movimiento(request):
    """
    API: devuelve la tarjeta de movimiento de un producto/SKU.
    Parámetros GET: sku, fecha_desde, fecha_hasta, sucursal_id (opcional).
    """
    sku = request.GET.get('sku', '').strip()
    fecha_desde = request.GET.get('fecha_desde', '')
    fecha_hasta = request.GET.get('fecha_hasta', '')
    sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

    if not sku:
        return JsonResponse({'success': False, 'error': 'Debe ingresar un SKU.'}, status=400)

    productos_talla_qs = Producto_Talla.objects.select_related(
        'producto', 'producto__atributo1', 'producto__sucursal',
    )
    if sku.isdigit():
        productos_talla = list(
            productos_talla_qs.filter(sku=int(sku)).order_by('producto__articulo', 'talla')
        )
        if not productos_talla:
            productos_talla = list(
                productos_talla_qs.filter(producto__articulo__iexact=sku).order_by('producto__articulo', 'talla')
            )
    else:
        productos_talla = list(
            productos_talla_qs.filter(producto__articulo__iexact=sku).order_by('producto__articulo', 'talla')
        )
    if not productos_talla:
        return JsonResponse({'success': False, 'error': f'SKU o artículo {sku} no encontrado.'}, status=404)

    movimientos_qs = Movimientos_Producto.objects.filter(
        ProductoTalla__in=productos_talla,
    ).select_related(
        'ProductoTalla', 'ProductoTalla__producto',
        'sucursal_origen', 'sucursal_destino', 'dte', 'ticket',
    )

    if fecha_desde:
        movimientos_qs = movimientos_qs.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        movimientos_qs = movimientos_qs.filter(fecha__lte=fecha_hasta)

    movimientos_qs = movimientos_qs.order_by('fecha', 'hora')

    saldo_acumulado = 0
    movimientos_data = []
    for m in movimientos_qs:
        if m.tipo_movimiento in ('INGRESO', 'DEVOLUCION'):
            saldo_acumulado += abs(m.cantidad)
        elif m.tipo_movimiento in ('EGRESO', 'PERDIDA'):
            saldo_acumulado -= abs(m.cantidad)
        elif m.tipo_movimiento == 'TRASPASO':
            if m.sucursal_destino_id and str(m.sucursal_destino_id) == str(sucursal_id):
                saldo_acumulado += abs(m.cantidad)
            else:
                saldo_acumulado -= abs(m.cantidad)
        else:
            saldo_acumulado += m.cantidad

        movimientos_data.append({
            'id': m.id,
            'sku': str(m.ProductoTalla.sku),
            'talla': m.ProductoTalla.talla,
            'fecha': m.fecha.strftime('%d/%m/%Y') if m.fecha else '',
            'hora': m.hora.strftime('%H:%M') if m.hora else '',
            'tipo_movimiento': m.tipo_movimiento,
            'concepto': m.concepto,
            'concepto_display': m.get_concepto_display(),
            'cantidad': m.cantidad,
            'costo': m.costo,
            'precio': m.precio,
            'saldo': saldo_acumulado,
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

    producto_talla = productos_talla[0]
    producto = producto_talla.producto
    productos_ids = {pt.producto_id for pt in productos_talla}
    sucursales = {
        pt.producto.sucursal.alias
        for pt in productos_talla
        if pt.producto.sucursal
    }
    producto_info = {
        'articulo': producto.articulo,
        'descripcion': producto.descripcion,
        'sku': str(producto_talla.sku) if len(productos_talla) == 1 else f'{len(productos_talla)} SKUs',
        'talla': producto_talla.talla if len(productos_talla) == 1 else 'Varias',
        'stock_actual': sum(pt.stock or 0 for pt in productos_talla),
        'costo': producto.costo,
        'precio_venta': producto.precioventa,
        'marca': producto.atributo1.valor if producto.atributo1 else '-',
        'sucursal': producto.sucursal.alias if len(productos_ids) == 1 and producto.sucursal else ', '.join(sorted(sucursales)) or '-',
    }

    resumen = {
        'total_ingresos': sum(m['cantidad'] for m in movimientos_data if m['tipo_movimiento'] in ('INGRESO', 'DEVOLUCION') or m['cantidad'] > 0),
        'total_egresos': sum(abs(m['cantidad']) for m in movimientos_data if m['tipo_movimiento'] in ('EGRESO', 'PERDIDA') or m['cantidad'] < 0),
        'total_movimientos': len(movimientos_data),
        'saldo_final': saldo_acumulado,
    }

    return JsonResponse({
        'success': True,
        'producto': producto_info,
        'movimientos': movimientos_data,
        'resumen': resumen,
    })


@login_required
@require_GET
def api_buscar_productos_tarjeta_movimiento(request):
    """Devuelve sugerencias para buscar tarjeta de movimiento por SKU o artículo."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'success': True, 'productos': []})

    filtro = (
        Q(producto__articulo__icontains=q) |
        Q(producto__descripcion__icontains=q)
    )
    if q.isdigit():
        filtro |= Q(sku__icontains=q)

    productos = (
        Producto_Talla.objects
        .filter(filtro)
        .select_related('producto', 'producto__atributo1', 'producto__sucursal')
        .order_by('producto__articulo', 'talla')[:15]
    )

    return JsonResponse({
        'success': True,
        'productos': [
            {
                'sku': str(pt.sku),
                'articulo': pt.producto.articulo,
                'descripcion': pt.producto.descripcion,
                'talla': pt.talla,
                'stock': pt.stock,
                'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '-',
                'sucursal': pt.producto.sucursal.alias if pt.producto.sucursal else '-',
            }
            for pt in productos
        ],
    })


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


@login_required
@require_GET
def api_productos_disponibles_despacho(request):
    """
    Busca productos disponibles para despacho desde la sucursal actual.
    Parámetros: q (texto búsqueda), marca_id, categoria_id, page.
    """
    sucursal_id = request.session.get('idSucursalActual')
    q = request.GET.get('q', '').strip()
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

    productos_qs = productos_qs.order_by('producto__articulo', 'talla')
    paginator = Paginator(productos_qs, 50)
    page = paginator.get_page(page_num)

    datos = []
    for pt in page:
        datos.append({
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
        })

    return JsonResponse({
        'success': True,
        'productos': datos,
        'pagina_actual': page.number,
        'total_paginas': paginator.num_pages,
        'total_productos': paginator.count,
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

    try:
        producto_talla = Producto_Talla.objects.select_related(
            'producto', 'producto__atributo1', 'producto__atributo2',
            'producto__categoria', 'producto__sucursal',
        ).get(sku=sku)
    except Producto_Talla.DoesNotExist:
        return JsonResponse({'success': False, 'error': f'SKU {sku} no encontrado.'}, status=404)

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
