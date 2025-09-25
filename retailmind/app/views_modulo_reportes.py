"""
Módulo de Reportes - RetailMind
Contiene todas las vistas relacionadas con reportes, dashboards estratégicos y análisis de datos
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg, TruncMonth, TruncWeek, TruncDate
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import re
from decimal import Decimal
from datetime import datetime, timedelta

from .models import (
    Compras, Dte, Ticket, Ticket_Productos, Producto, Producto_Talla, 
    Movimientos_Producto, Sucursal, EmpresaUser, Empresa, Vendedor,
    LoteProducto, Traspaso, AjusteInventario
)


# ========== REPORTES DE COMPRAS ==========

@require_GET
@login_required
def reporte_despachos_por_proveedor(request):
    """Reporte de despachos por proveedor"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        proveedor_id = request.GET.get('proveedor_id')
        
        # Fechas por defecto (último mes)
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.now().date()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Construir queryset
        queryset = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__range=[fecha_inicio, fecha_fin]
        ).select_related('emisor')
        
        if proveedor_id:
            queryset = queryset.filter(emisor_id=proveedor_id)
        
        # Agrupar por proveedor
        despachos_por_proveedor = queryset.values(
            'emisor__nombre',
            'emisor__rut'
        ).annotate(
            total_documentos=Count('id'),
            monto_total=Sum('total'),
            monto_promedio=Avg('total')
        ).order_by('-monto_total')
        
        # Detalles por documento
        detalles_documentos = []
        for dte in queryset.order_by('-fecha_emision'):
            detalles_documentos.append({
                'numero_dte': dte.numero_dte,
                'tipo_documento': dte.tipo_documento,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
                'proveedor': dte.emisor.nombre,
                'subtotal': float(dte.subtotal),
                'iva': float(dte.iva),
                'total': float(dte.total),
                'estado': dte.estado_dte
            })
        
        # Resumen general
        resumen = {
            'total_proveedores': len(despachos_por_proveedor),
            'total_documentos': sum(item['total_documentos'] for item in despachos_por_proveedor),
            'monto_total_periodo': sum(item['monto_total'] for item in despachos_por_proveedor),
            'monto_promedio_documento': sum(item['monto_total'] for item in despachos_por_proveedor) / max(sum(item['total_documentos'] for item in despachos_por_proveedor), 1)
        }
        
        return JsonResponse({
            'success': True,
            'despachos_por_proveedor': [
                {
                    'proveedor': item['emisor__nombre'],
                    'rut': item['emisor__rut'],
                    'total_documentos': item['total_documentos'],
                    'monto_total': float(item['monto_total']),
                    'monto_promedio': float(item['monto_promedio'])
                }
                for item in despachos_por_proveedor
            ],
            'detalles_documentos': detalles_documentos,
            'resumen': resumen,
            'parametros': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin.strftime('%d/%m/%Y')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte: {str(e)}'
        })


@require_GET
@login_required
def obtener_proveedores_para_reporte(request):
    """Obtener lista de proveedores para filtros de reportes"""
    try:
        proveedores = Empresa.objects.filter(
            es_proveedor=True,
            activo=True
        ).order_by('nombre')
        
        proveedores_data = []
        for proveedor in proveedores:
            # Contar DTEs del proveedor
            total_dtes = Dte.objects.filter(
                emisor=proveedor,
                tipo_transaccion='COMPRA'
            ).count()
            
            if total_dtes > 0:  # Solo incluir proveedores con DTEs
                proveedores_data.append({
                    'id': proveedor.id,
                    'nombre': proveedor.nombre,
                    'rut': proveedor.rut,
                    'total_documentos': total_dtes
                })
        
        return JsonResponse({
            'success': True,
            'proveedores': proveedores_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener proveedores: {str(e)}'
        })


@login_required
def verReporteDespachosProveedor(request):
    """Vista principal del reporte de despachos por proveedor"""
    return render(request, 'vistas/modulo reportes/reporteDespachosProveedor.html')


# ========== REPORTES DE MOVIMIENTOS ==========

@require_GET
@login_required
def reporte_movimientos_kardex(request):
    """Reporte de movimientos tipo kardex"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        producto_id = request.GET.get('producto_id')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        concepto = request.GET.get('concepto')
        
        # Construir queryset
        queryset = Movimientos_Producto.objects.select_related(
            'producto_talla__producto', 'responsable', 'sucursal_origen', 'sucursal_destino'
        )
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(fecha_creacion__date__gte=fecha_inicio)
        
        if fecha_fin:
            queryset = queryset.filter(fecha_creacion__date__lte=fecha_fin)
        
        if producto_id:
            queryset = queryset.filter(producto_talla__producto_id=producto_id)
        
        if sucursal_id:
            queryset = queryset.filter(
                Q(sucursal_origen_id=sucursal_id) | Q(sucursal_destino_id=sucursal_id)
            )
        
        if concepto:
            queryset = queryset.filter(concepto=concepto)
        
        # Ordenar por fecha
        queryset = queryset.order_by('fecha_creacion')
        
        # Generar kardex
        kardex_data = []
        saldo_acumulado = {}  # Por producto_talla_id
        
        for mov in queryset:
            producto_talla_id = mov.producto_talla.id
            
            # Inicializar saldo si no existe
            if producto_talla_id not in saldo_acumulado:
                saldo_acumulado[producto_talla_id] = 0
            
            # Determinar si es entrada o salida
            if mov.concepto in ['COMPRA', 'TRASPASO_ENTRADA', 'AJUSTE_ENTRADA']:
                entrada = abs(mov.cantidad)
                salida = 0
                saldo_acumulado[producto_talla_id] += entrada
            else:
                entrada = 0
                salida = abs(mov.cantidad)
                saldo_acumulado[producto_talla_id] -= salida
            
            kardex_data.append({
                'fecha': mov.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'concepto': mov.concepto,
                'tipo_movimiento': mov.tipo_movimiento,
                'producto': mov.producto_talla.producto.nombre,
                'sku': mov.producto_talla.sku,
                'entrada': entrada,
                'salida': salida,
                'saldo': saldo_acumulado[producto_talla_id],
                'responsable': mov.responsable.username if mov.responsable else '',
                'observaciones': mov.observaciones or '',
                'referencia': mov.referencia_externa or ''
            })
        
        # Resumen por concepto
        resumen_conceptos = queryset.values('concepto').annotate(
            total_movimientos=Count('id'),
            cantidad_total=Sum('cantidad')
        ).order_by('-total_movimientos')
        
        return JsonResponse({
            'success': True,
            'kardex': kardex_data,
            'resumen_conceptos': [
                {
                    'concepto': item['concepto'],
                    'total_movimientos': item['total_movimientos'],
                    'cantidad_total': item['cantidad_total']
                }
                for item in resumen_conceptos
            ],
            'total_movimientos': len(kardex_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar kardex: {str(e)}'
        })


@require_GET
@login_required
def reporte_kardex_agrupado(request):
    """Reporte kardex agrupado por producto"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        categoria_id = request.GET.get('categoria_id')
        marca_id = request.GET.get('marca_id')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        
        # Construir queryset base
        queryset = Movimientos_Producto.objects.select_related(
            'producto_talla__producto', 'producto_talla__producto__categoria', 'producto_talla__producto__marca'
        )
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(fecha_creacion__date__gte=fecha_inicio)
        
        if fecha_fin:
            queryset = queryset.filter(fecha_creacion__date__lte=fecha_fin)
        
        if categoria_id:
            queryset = queryset.filter(producto_talla__producto__categoria_id=categoria_id)
        
        if marca_id:
            queryset = queryset.filter(producto_talla__producto__marca_id=marca_id)
        
        if sucursal_id:
            queryset = queryset.filter(
                Q(sucursal_origen_id=sucursal_id) | Q(sucursal_destino_id=sucursal_id)
            )
        
        # Agrupar por producto
        kardex_agrupado = queryset.values(
            'producto_talla__producto__nombre',
            'producto_talla__sku',
            'producto_talla__producto__categoria__nombre',
            'producto_talla__producto__marca__nombre'
        ).annotate(
            total_entradas=Sum(
                'cantidad',
                filter=Q(concepto__in=['COMPRA', 'TRASPASO_ENTRADA', 'AJUSTE_ENTRADA'])
            ),
            total_salidas=Sum(
                'cantidad',
                filter=Q(concepto__in=['VENTA', 'TRASPASO_SALIDA', 'AJUSTE_SALIDA'])
            ),
            total_movimientos=Count('id'),
            ultimo_movimiento=Max('fecha_creacion')
        ).order_by('producto_talla__producto__nombre')
        
        # Procesar datos
        kardex_data = []
        for item in kardex_agrupado:
            entradas = item['total_entradas'] or 0
            salidas = abs(item['total_salidas']) if item['total_salidas'] else 0
            saldo_neto = entradas - salidas
            
            kardex_data.append({
                'producto': item['producto_talla__producto__nombre'],
                'sku': item['producto_talla__sku'],
                'categoria': item['producto_talla__producto__categoria__nombre'] or '',
                'marca': item['producto_talla__producto__marca__nombre'] or '',
                'total_entradas': entradas,
                'total_salidas': salidas,
                'saldo_neto': saldo_neto,
                'total_movimientos': item['total_movimientos'],
                'ultimo_movimiento': item['ultimo_movimiento'].strftime('%d/%m/%Y %H:%M') if item['ultimo_movimiento'] else ''
            })
        
        # Resumen general
        resumen = {
            'total_productos': len(kardex_data),
            'total_entradas': sum(item['total_entradas'] for item in kardex_data),
            'total_salidas': sum(item['total_salidas'] for item in kardex_data),
            'productos_con_saldo_positivo': len([item for item in kardex_data if item['saldo_neto'] > 0]),
            'productos_con_saldo_negativo': len([item for item in kardex_data if item['saldo_neto'] < 0])
        }
        
        return JsonResponse({
            'success': True,
            'kardex_agrupado': kardex_data,
            'resumen': resumen
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar kardex agrupado: {str(e)}'
        })


# ========== REPORTES DE VENTAS ==========

@require_GET
@login_required
def reporte_ventas_por_periodo(request):
    """Reporte de ventas por período"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        vendedor_id = request.GET.get('vendedor_id')
        agrupacion = request.GET.get('agrupacion', 'dia')  # dia, semana, mes
        
        # Fechas por defecto (último mes)
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.now().date()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            fecha_creacion__date__range=[fecha_inicio, fecha_fin],
            estado='PAGADO'
        ).select_related('vendedor', 'sucursal')
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        if vendedor_id:
            queryset = queryset.filter(vendedor_id=vendedor_id)
        
        # Agrupar según el tipo de agrupación
        if agrupacion == 'dia':
            ventas_agrupadas = queryset.annotate(
                periodo=TruncDate('fecha_creacion')
            ).values('periodo').annotate(
                total_tickets=Count('id'),
                monto_total=Sum('total'),
                monto_promedio=Avg('total')
            ).order_by('periodo')
            
        elif agrupacion == 'semana':
            ventas_agrupadas = queryset.annotate(
                periodo=TruncWeek('fecha_creacion')
            ).values('periodo').annotate(
                total_tickets=Count('id'),
                monto_total=Sum('total'),
                monto_promedio=Avg('total')
            ).order_by('periodo')
            
        elif agrupacion == 'mes':
            ventas_agrupadas = queryset.annotate(
                periodo=TruncMonth('fecha_creacion')
            ).values('periodo').annotate(
                total_tickets=Count('id'),
                monto_total=Sum('total'),
                monto_promedio=Avg('total')
            ).order_by('periodo')
        
        # Procesar datos
        ventas_data = []
        for item in ventas_agrupadas:
            ventas_data.append({
                'periodo': item['periodo'].strftime('%d/%m/%Y') if agrupacion == 'dia' else item['periodo'].strftime('%m/%Y'),
                'total_tickets': item['total_tickets'],
                'monto_total': float(item['monto_total']),
                'monto_promedio': float(item['monto_promedio'])
            })
        
        # Resumen general
        resumen = {
            'total_tickets': queryset.count(),
            'monto_total_periodo': float(queryset.aggregate(total=Sum('total'))['total'] or 0),
            'ticket_promedio': float(queryset.aggregate(promedio=Avg('total'))['promedio'] or 0),
            'mejor_dia': max(ventas_data, key=lambda x: x['monto_total']) if ventas_data else None
        }
        
        return JsonResponse({
            'success': True,
            'ventas_por_periodo': ventas_data,
            'resumen': resumen,
            'parametros': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin.strftime('%d/%m/%Y'),
                'agrupacion': agrupacion
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte de ventas: {str(e)}'
        })


@require_GET
@login_required
def reporte_productos_mas_vendidos(request):
    """Reporte de productos más vendidos"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')
        limite = int(request.GET.get('limite', 20))
        
        # Fechas por defecto (último mes)
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.now().date()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Construir queryset
        queryset = Ticket_Productos.objects.filter(
            ticket__created_at__date__range=[fecha_inicio, fecha_fin],
            ticket__estado='PAGADO'
        ).select_related('productoTalla__producto', 'productoTalla__talla')
        
        if sucursal_id:
            queryset = queryset.filter(ticket__sucursal_id=sucursal_id)
        
        if categoria_id:
            queryset = queryset.filter(productoTalla__producto__categoria_id=categoria_id)
        
        # Agrupar por producto
        productos_vendidos = queryset.values(
            'productoTalla__producto__nombre',
            'productoTalla__sku',
            'productoTalla__producto__categoria__nombre',
            'productoTalla__producto__marca__nombre'
        ).annotate(
            cantidad_vendida=Sum('cantidad'),
            ingresos_totales=Sum(F('cantidad') * F('precio_unitario')),
            tickets_count=Count('ticket', distinct=True),
            precio_promedio=Avg('precio_unitario')
        ).order_by('-cantidad_vendida')[:limite]
        
        # Procesar datos
        productos_data = []
        for item in productos_vendidos:
            productos_data.append({
                'producto': item['productoTalla__producto__nombre'],
                'sku': item['productoTalla__sku'],
                'categoria': item['productoTalla__producto__categoria__nombre'] or '',
                'marca': item['productoTalla__producto__marca__nombre'] or '',
                'cantidad_vendida': item['cantidad_vendida'],
                'ingresos_totales': float(item['ingresos_totales']),
                'tickets_count': item['tickets_count'],
                'precio_promedio': float(item['precio_promedio'])
            })
        
        # Top 5 categorías
        top_categorias = queryset.values(
            'productoTalla__producto__categoria__nombre'
        ).annotate(
            cantidad_vendida=Sum('cantidad'),
            ingresos_totales=Sum(F('cantidad') * F('precio_unitario'))
        ).order_by('-cantidad_vendida')[:5]
        
        return JsonResponse({
            'success': True,
            'productos_mas_vendidos': productos_data,
            'top_categorias': [
                {
                    'categoria': item['productoTalla__producto__categoria__nombre'] or 'Sin categoría',
                    'cantidad_vendida': item['cantidad_vendida'],
                    'ingresos_totales': float(item['ingresos_totales'])
                }
                for item in top_categorias
            ],
            'parametros': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin.strftime('%d/%m/%Y'),
                'limite': limite
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte de productos: {str(e)}'
        })


@require_GET
@login_required
def reporte_vendedores_performance(request):
    """Reporte de performance de vendedores"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        
        # Fechas por defecto (último mes)
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.now().date()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            fecha_creacion__date__range=[fecha_inicio, fecha_fin],
            estado='PAGADO'
        ).select_related('vendedor')
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Agrupar por vendedor
        performance_vendedores = queryset.values(
            'vendedor__nombre',
            'vendedor__codigo'
        ).annotate(
            total_tickets=Count('id'),
            monto_total_ventas=Sum('total'),
            ticket_promedio=Avg('total'),
            comision_total=Sum(F('total') * F('vendedor__comision_porcentaje') / 100)
        ).order_by('-monto_total_ventas')
        
        # Procesar datos
        vendedores_data = []
        for item in performance_vendedores:
            # Calcular días trabajados (días con al menos una venta)
            dias_trabajados = queryset.filter(
                vendedor__nombre=item['vendedor__nombre']
            ).dates('fecha_creacion', 'day').count()
            
            promedio_diario = item['monto_total_ventas'] / max(dias_trabajados, 1)
            
            vendedores_data.append({
                'vendedor': item['vendedor__nombre'],
                'codigo': item['vendedor__codigo'],
                'total_tickets': item['total_tickets'],
                'monto_total_ventas': float(item['monto_total_ventas']),
                'ticket_promedio': float(item['ticket_promedio']),
                'comision_total': float(item['comision_total'] or 0),
                'dias_trabajados': dias_trabajados,
                'promedio_diario': float(promedio_diario)
            })
        
        # Ranking y métricas
        if vendedores_data:
            mejor_vendedor = max(vendedores_data, key=lambda x: x['monto_total_ventas'])
            mayor_ticket_promedio = max(vendedores_data, key=lambda x: x['ticket_promedio'])
        else:
            mejor_vendedor = None
            mayor_ticket_promedio = None
        
        return JsonResponse({
            'success': True,
            'performance_vendedores': vendedores_data,
            'ranking': {
                'mejor_vendedor': mejor_vendedor,
                'mayor_ticket_promedio': mayor_ticket_promedio
            },
            'parametros': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin.strftime('%d/%m/%Y')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte de vendedores: {str(e)}'
        })


# ========== REPORTES DE INVENTARIO ==========

@require_GET
@login_required
def reporte_valoracion_inventario(request):
    """Reporte de valoración de inventario"""
    try:
        # Parámetros de filtro
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')
        marca_id = request.GET.get('marca_id')
        metodo_valoracion = request.GET.get('metodo', 'FIFO')  # FIFO, PROMEDIO
        
        # Construir queryset
        queryset = Producto_Talla.objects.select_related(
            'producto', 'producto__categoria', 'producto__marca', 'talla'
        ).filter(activo=True)
        
        if categoria_id:
            queryset = queryset.filter(producto__categoria_id=categoria_id)
        
        if marca_id:
            queryset = queryset.filter(producto__marca_id=marca_id)
        
        # Calcular valoración
        inventario_data = []
        valor_total_inventario = 0
        
        for pt in queryset:
            # Calcular stock
            if sucursal_id:
                stock_actual = pt.stock_sucursal(sucursal_id)
            else:
                stock_actual = pt.stock_total()
            
            if stock_actual > 0:
                # Calcular valor según método
                if metodo_valoracion == 'FIFO':
                    from .views import obtener_valor_inventario_fifo, obtener_costo_promedio_fifo
                    valor_inventario = obtener_valor_inventario_fifo(pt)
                    costo_unitario = obtener_costo_promedio_fifo(pt)
                else:  # PROMEDIO
                    # Calcular costo promedio ponderado
                    lotes = LoteProducto.objects.filter(
                        producto_talla=pt,
                        cantidad_disponible__gt=0,
                        activo=True
                    )
                    
                    if lotes.exists():
                        valor_total = sum(lote.cantidad_disponible * lote.costo_unitario for lote in lotes)
                        cantidad_total = sum(lote.cantidad_disponible for lote in lotes)
                        costo_unitario = valor_total / cantidad_total if cantidad_total > 0 else 0
                        valor_inventario = stock_actual * costo_unitario
                    else:
                        costo_unitario = 0
                        valor_inventario = 0
                
                valor_total_inventario += valor_inventario
                
                inventario_data.append({
                    'sku': pt.sku,
                    'producto': pt.producto.nombre,
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else '',
                    'marca': pt.producto.marca.nombre if pt.producto.marca else '',
                    'talla': pt.talla.nombre if pt.talla else 'Sin talla',
                    'stock_actual': stock_actual,
                    'costo_unitario': float(costo_unitario),
                    'precio_venta': float(pt.precio_venta),
                    'valor_inventario': float(valor_inventario),
                    'margen_unitario': float(pt.precio_venta - costo_unitario),
                    'margen_porcentaje': float(((pt.precio_venta - costo_unitario) / pt.precio_venta) * 100) if pt.precio_venta > 0 else 0
                })
        
        # Ordenar por valor de inventario descendente
        inventario_data.sort(key=lambda x: x['valor_inventario'], reverse=True)
        
        # Resumen por categoría
        resumen_categorias = {}
        for item in inventario_data:
            categoria = item['categoria'] or 'Sin categoría'
            if categoria not in resumen_categorias:
                resumen_categorias[categoria] = {
                    'productos_count': 0,
                    'stock_total': 0,
                    'valor_total': 0
                }
            
            resumen_categorias[categoria]['productos_count'] += 1
            resumen_categorias[categoria]['stock_total'] += item['stock_actual']
            resumen_categorias[categoria]['valor_total'] += item['valor_inventario']
        
        return JsonResponse({
            'success': True,
            'inventario': inventario_data,
            'resumen': {
                'total_productos': len(inventario_data),
                'valor_total_inventario': float(valor_total_inventario),
                'stock_total': sum(item['stock_actual'] for item in inventario_data),
                'costo_promedio_general': float(valor_total_inventario / sum(item['stock_actual'] for item in inventario_data)) if sum(item['stock_actual'] for item in inventario_data) > 0 else 0
            },
            'resumen_categorias': [
                {
                    'categoria': categoria,
                    'productos_count': data['productos_count'],
                    'stock_total': data['stock_total'],
                    'valor_total': float(data['valor_total']),
                    'porcentaje_valor': float((data['valor_total'] / valor_total_inventario) * 100) if valor_total_inventario > 0 else 0
                }
                for categoria, data in resumen_categorias.items()
            ],
            'parametros': {
                'metodo_valoracion': metodo_valoracion,
                'fecha_reporte': timezone.now().strftime('%d/%m/%Y %H:%M')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte de valoración: {str(e)}'
        })


@require_GET
@login_required
def reporte_rotacion_inventario(request):
    """Reporte de rotación de inventario"""
    try:
        # Parámetros de filtro
        periodo_dias = int(request.GET.get('periodo_dias', 90))
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')
        
        fecha_inicio = timezone.now() - timedelta(days=periodo_dias)
        
        # Construir queryset de productos
        queryset = Producto_Talla.objects.select_related(
            'producto', 'producto__categoria', 'producto__marca', 'talla'
        ).filter(activo=True)
        
        if categoria_id:
            queryset = queryset.filter(producto__categoria_id=categoria_id)
        
        rotacion_data = []
        
        for pt in queryset:
            # Stock actual
            if sucursal_id:
                stock_actual = pt.stock_sucursal(sucursal_id)
            else:
                stock_actual = pt.stock_total()
            
            # Ventas en el período
            ventas_periodo = Ticket_Productos.objects.filter(
                productoTalla=pt,
                ticket__created_at__gte=fecha_inicio,
                ticket__estado='PAGADO'
            )
            
            if sucursal_id:
                ventas_periodo = ventas_periodo.filter(ticket__sucursal_id=sucursal_id)
            
            cantidad_vendida = ventas_periodo.aggregate(
                total=Sum('cantidad')
            )['total'] or 0
            
            # Calcular métricas de rotación
            if stock_actual > 0 and cantidad_vendida > 0:
                # Rotación = Ventas del período / Stock promedio
                # Asumimos stock promedio = stock actual (simplificación)
                rotacion = cantidad_vendida / stock_actual
                dias_inventario = periodo_dias / rotacion if rotacion > 0 else float('inf')
                
                # Valor del inventario
                from .views import obtener_valor_inventario_fifo
                valor_inventario = obtener_valor_inventario_fifo(pt)
                
                rotacion_data.append({
                    'sku': pt.sku,
                    'producto': pt.producto.nombre,
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else '',
                    'stock_actual': stock_actual,
                    'cantidad_vendida': cantidad_vendida,
                    'rotacion': round(rotacion, 2),
                    'dias_inventario': round(dias_inventario, 1) if dias_inventario != float('inf') else 'Sin rotación',
                    'valor_inventario': float(valor_inventario),
                    'clasificacion': self._clasificar_rotacion(rotacion)
                })
        
        # Ordenar por rotación descendente
        rotacion_data.sort(key=lambda x: x['rotacion'], reverse=True)
        
        # Estadísticas generales
        rotaciones_validas = [item for item in rotacion_data if isinstance(item['dias_inventario'], (int, float))]
        
        resumen = {
            'productos_analizados': len(rotacion_data),
            'productos_con_rotacion': len(rotaciones_validas),
            'rotacion_promedio': sum(item['rotacion'] for item in rotacion_data) / len(rotacion_data) if rotacion_data else 0,
            'dias_inventario_promedio': sum(item['dias_inventario'] for item in rotaciones_validas) / len(rotaciones_validas) if rotaciones_validas else 0,
            'productos_alta_rotacion': len([item for item in rotacion_data if item['clasificacion'] == 'ALTA']),
            'productos_baja_rotacion': len([item for item in rotacion_data if item['clasificacion'] == 'BAJA'])
        }
        
        return JsonResponse({
            'success': True,
            'rotacion_inventario': rotacion_data,
            'resumen': resumen,
            'parametros': {
                'periodo_dias': periodo_dias,
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': timezone.now().strftime('%d/%m/%Y')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte de rotación: {str(e)}'
        })
    
    def _clasificar_rotacion(self, rotacion):
        """Clasificar rotación de inventario"""
        if rotacion >= 2.0:
            return 'ALTA'
        elif rotacion >= 0.5:
            return 'MEDIA'
        else:
            return 'BAJA'


# ========== EXPORTACIÓN DE REPORTES ==========

@require_GET
@login_required
def exportar_reporte_excel(request):
    """Exportar cualquier reporte a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        
        tipo_reporte = request.GET.get('tipo_reporte')
        
        if not tipo_reporte:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de reporte requerido'
            })
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        
        # Configurar según tipo de reporte
        if tipo_reporte == 'ventas_periodo':
            ws.title = "Ventas por Período"
            # Obtener datos del reporte
            reporte_response = reporte_ventas_por_periodo(request)
            data = json.loads(reporte_response.content)
            
            # Encabezados
            headers = ['Período', 'Total Tickets', 'Monto Total', 'Monto Promedio']
            ws.append(headers)
            
            # Datos
            for item in data['ventas_por_periodo']:
                ws.append([
                    item['periodo'],
                    item['total_tickets'],
                    item['monto_total'],
                    item['monto_promedio']
                ])
        
        elif tipo_reporte == 'productos_vendidos':
            ws.title = "Productos Más Vendidos"
            # Obtener datos del reporte
            reporte_response = reporte_productos_mas_vendidos(request)
            data = json.loads(reporte_response.content)
            
            # Encabezados
            headers = ['Producto', 'SKU', 'Categoría', 'Cantidad Vendida', 'Ingresos Totales']
            ws.append(headers)
            
            # Datos
            for item in data['productos_mas_vendidos']:
                ws.append([
                    item['producto'],
                    item['sku'],
                    item['categoria'],
                    item['cantidad_vendida'],
                    item['ingresos_totales']
                ])
        
        elif tipo_reporte == 'valoracion_inventario':
            ws.title = "Valoración de Inventario"
            # Obtener datos del reporte
            reporte_response = reporte_valoracion_inventario(request)
            data = json.loads(reporte_response.content)
            
            # Encabezados
            headers = ['SKU', 'Producto', 'Categoría', 'Stock', 'Costo Unitario', 'Valor Inventario']
            ws.append(headers)
            
            # Datos
            for item in data['inventario']:
                ws.append([
                    item['sku'],
                    item['producto'],
                    item['categoria'],
                    item['stock_actual'],
                    item['costo_unitario'],
                    item['valor_inventario']
                ])
        
        else:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de reporte no soportado'
            })
        
        # Aplicar estilos a encabezados
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        
        # Ajustar ancho de columnas
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="reporte_{tipo_reporte}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar reporte: {str(e)}'
        })


# ========== DASHBOARD GENERAL DE REPORTES ==========

@login_required
def dashboard_reportes(request):
    """Vista principal del dashboard de reportes"""
    return render(request, 'vistas/modulo_dashboards/dashboard_reportes.html')


@require_GET
@login_required
def obtener_resumen_reportes(request):
    """Obtener resumen general para el dashboard de reportes"""
    try:
        # Período de análisis (último mes)
        fecha_fin = timezone.now().date()
        fecha_inicio = fecha_fin - timedelta(days=30)
        
        # Métricas de ventas
        tickets_periodo = Ticket.objects.filter(
            fecha_creacion__date__range=[fecha_inicio, fecha_fin],
            estado='PAGADO'
        )
        
        total_ventas = tickets_periodo.aggregate(total=Sum('total'))['total'] or 0
        total_tickets = tickets_periodo.count()
        ticket_promedio = total_ventas / total_tickets if total_tickets > 0 else 0
        
        # Métricas de inventario
        total_productos = Producto_Talla.objects.filter(activo=True).count()
        productos_con_stock = 0
        valor_total_inventario = 0
        
        for pt in Producto_Talla.objects.filter(activo=True):
            stock = pt.stock_total()
            if stock > 0:
                productos_con_stock += 1
                from .views import obtener_valor_inventario_fifo
                valor_total_inventario += obtener_valor_inventario_fifo(pt)
        
        # Métricas de compras
        compras_periodo = Compras.objects.filter(
            fecha_compra__range=[fecha_inicio, fecha_fin]
        )
        
        total_compras = compras_periodo.aggregate(total=Sum('total'))['total'] or 0
        cantidad_compras = compras_periodo.count()
        
        # Top 5 productos más vendidos
        top_productos = Ticket_Productos.objects.filter(
            ticket__created_at__date__range=[fecha_inicio, fecha_fin],
            ticket__estado='PAGADO'
        ).values(
            'productoTalla__producto__nombre',
            'productoTalla__sku'
        ).annotate(
            cantidad_vendida=Sum('cantidad')
        ).order_by('-cantidad_vendida')[:5]
        
        resumen = {
            'ventas': {
                'total_ventas': float(total_ventas),
                'total_tickets': total_tickets,
                'ticket_promedio': float(ticket_promedio)
            },
            'inventario': {
                'total_productos': total_productos,
                'productos_con_stock': productos_con_stock,
                'valor_total_inventario': float(valor_total_inventario)
            },
            'compras': {
                'total_compras': float(total_compras),
                'cantidad_compras': cantidad_compras
            },
            'top_productos': [
                {
                    'producto': item['productoTalla__producto__nombre'],
                    'sku': item['productoTalla__sku'],
                    'cantidad_vendida': item['cantidad_vendida']
                }
                for item in top_productos
            ],
            'periodo': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin.strftime('%d/%m/%Y')
            }
        }
        
        return JsonResponse({
            'success': True,
            'resumen': resumen
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener resumen: {str(e)}'
        })
