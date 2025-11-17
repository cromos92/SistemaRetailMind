"""
Módulo de Reportes - RetailMind
Contiene todas las vistas relacionadas con reportes, dashboards estratégicos y análisis de datos
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg, Max
from django.db.models.functions import TruncMonth, TruncWeek, TruncDate
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
    LoteProducto, Traspaso, AjusteInventario, TicketDetallePago,
    METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES
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


# ========== REPORTE DE VENTAS POR SUCURSAL ==========

@login_required
def ver_reporte_ventas_sucursal(request):
    """Vista principal del reporte de ventas mensual por vendedor y sucursal"""
    return render(request, 'vistas/modulo_reportes/reporte_ventas_sucursal.html')


@require_GET
@login_required
def obtener_ventas_por_vendedor_reporte(request):
    """API para obtener datos de ventas por vendedor"""
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')  # Formato: YYYY-MM
        sucursal_id = request.GET.get('sucursal_id')
        vendedor_id = request.GET.get('vendedor_id')
        
        # Si no se proporciona mes, usar el mes actual
        if not mes:
            mes = timezone.now().strftime('%Y-%m')
        
        # Convertir mes a rango de fechas
        fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
        # Último día del mes
        if fecha_inicio.month == 12:
            fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
            estado='PAGADO'
        ).select_related('vendedor', 'sucursal')
        
        # Filtrar por sucursal según permisos
        if not request.user.is_superuser:
            # Si no es superuser, solo mostrar su sucursal
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset = queryset.filter(sucursal_id=sucursal_sesion)
        elif sucursal_id:
            # Si es superuser y seleccionó una sucursal específica
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        if vendedor_id:
            queryset = queryset.filter(vendedor_id=vendedor_id)
        
        # Agrupar por vendedor
        ventas_por_vendedor = queryset.values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor'
        ).annotate(
            total_ventas=Sum('total'),
            total_documentos=Count('id'),
            ticket_promedio=Avg('total')
        ).order_by('-total_ventas')
        
        # Calcular total general
        total_general = queryset.aggregate(total=Sum('total'))['total'] or 0
        
        # Procesar datos
        vendedores_data = []
        for idx, item in enumerate(ventas_por_vendedor, 1):
            total_ventas = item['total_ventas'] or 0
            total_docs = item['total_documentos'] or 0
            participacion = (total_ventas / total_general * 100) if total_general > 0 else 0
            
            vendedores_data.append({
                'id': item['vendedor__id'],
                'nombre': item['vendedor__nombre'],
                'codigo': item['vendedor__codigo_vendedor'],
                'ventas': int(total_ventas),
                'documentos': total_docs,
                'participacion': round(participacion, 1)
            })
        
        # KPIs
        total_documentos = queryset.count()
        ticket_promedio = total_general / total_documentos if total_documentos > 0 else 0
        top_vendedor = vendedores_data[0]['nombre'] if vendedores_data else '-'
        
        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data,
            'kpis': {
                'total_ventas': int(total_general),
                'total_documentos': total_documentos,
                'ticket_promedio': int(ticket_promedio),
                'top_vendedor': top_vendedor
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por vendedor: {str(e)}'
        })


@require_GET
@login_required
def obtener_ventas_por_sucursal_reporte(request):
    """API para obtener datos de ventas por sucursal"""
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')  # Formato: YYYY-MM
        sucursal_id = request.GET.get('sucursal_id')
        
        # Si no se proporciona mes, usar el mes actual
        if not mes:
            mes = timezone.now().strftime('%Y-%m')
        
        # Convertir mes a rango de fechas
        fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
        # Último día del mes
        if fecha_inicio.month == 12:
            fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
            estado='PAGADO'
        ).select_related('sucursal', 'vendedor')
        
        # Filtrar por sucursal según permisos
        if not request.user.is_superuser:
            # Si no es superuser, solo mostrar su sucursal
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset = queryset.filter(sucursal_id=sucursal_sesion)
        elif sucursal_id:
            # Si es superuser y seleccionó una sucursal específica
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Agrupar por sucursal
        ventas_por_sucursal = queryset.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion'
        ).annotate(
            total_ventas=Sum('total'),
            subtotal_ventas=Sum('subTotal'),
            total_documentos=Count('id'),
            ticket_promedio=Avg('total'),
            vendedores_count=Count('vendedor', distinct=True)
        ).order_by('-total_ventas')
        
        # Calcular total general
        total_general = queryset.aggregate(total=Sum('total'))['total'] or 0
        
        # Procesar datos
        sucursales_data = []
        for item in ventas_por_sucursal:
            total_ventas = item['total_ventas'] or 0
            subtotal = item['subtotal_ventas'] or 0
            # Calcular IVA (total - subtotal)
            iva = total_ventas - subtotal
            total_docs = item['total_documentos'] or 0
            participacion = (total_ventas / total_general * 100) if total_general > 0 else 0
            
            sucursales_data.append({
                'id': item['sucursal__id'],
                'nombre': item['sucursal__alias'],
                'direccion': item['sucursal__direccion'],
                'neto': int(subtotal),
                'iva': int(iva),
                'ventas': int(total_ventas),
                'documentos': total_docs,
                'vendedores': item['vendedores_count'],
                'participacion': round(participacion, 1)
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por sucursal: {str(e)}'
        })


@require_GET
@login_required
def obtener_vendedores_reporte(request):
    """API para obtener lista de vendedores"""
    try:
        sucursal_id = request.GET.get('sucursal_id')
        
        # Obtener vendedores
        vendedores = Vendedor.objects.filter(
            nombre__isnull=False
        ).order_by('nombre')
        
        # Si se especifica sucursal, filtrar vendedores de esa sucursal
        if sucursal_id:
            vendedores = vendedores.filter(sucursales__id=sucursal_id)
        
        vendedores_data = []
        for vendedor in vendedores:
            vendedores_data.append({
                'id': vendedor.id,
                'nombre': vendedor.nombre,
                'codigo': vendedor.codigo_vendedor
            })
        
        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener vendedores: {str(e)}'
        })


@require_GET
@login_required  
def obtener_sucursales_reporte(request):
    """API para obtener lista de sucursales"""
    try:
        # Obtener sucursales según permisos
        if request.user.is_superuser:
            # Superuser ve todas las sucursales
            sucursales = Sucursal.objects.all().order_by('alias')
        else:
            # Usuario normal solo ve su sucursal actual
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                sucursales = Sucursal.objects.filter(id=sucursal_sesion)
            else:
                sucursales = Sucursal.objects.none()
        
        sucursales_data = []
        for sucursal in sucursales:
            sucursales_data.append({
                'id': sucursal.id,
                'nombre': sucursal.alias,
                'direccion': sucursal.direccion
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener sucursales: {str(e)}'
        })


@require_GET
@login_required
def obtener_comparativa_mensual(request):
    """API para obtener datos de comparativa mensual de sucursales (últimos 6 meses)"""
    try:
        sucursal_id = request.GET.get('sucursal_id')
        
        # Calcular últimos 6 meses
        fecha_fin = timezone.now()
        fecha_inicio = fecha_fin - timedelta(days=180)  # Aproximadamente 6 meses
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            created_at__gte=fecha_inicio,
            estado='PAGADO'
        ).select_related('sucursal')
        
        # Filtrar por sucursal según permisos
        if not request.user.is_superuser:
            # Si no es superuser, solo mostrar su sucursal
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset = queryset.filter(sucursal_id=sucursal_sesion)
        elif sucursal_id:
            # Si es superuser y seleccionó una sucursal específica
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Agrupar por mes y sucursal
        ventas_mensuales = queryset.annotate(
            mes=TruncMonth('created_at')
        ).values(
            'mes',
            'sucursal__alias'
        ).annotate(
            total_ventas=Sum('total')
        ).order_by('mes', 'sucursal__alias')
        
        # Organizar datos por sucursal
        sucursales_dict = {}
        meses_set = set()
        
        for item in ventas_mensuales:
            sucursal_nombre = item['sucursal__alias']
            mes_str = item['mes'].strftime('%Y-%m')
            meses_set.add(mes_str)
            
            if sucursal_nombre not in sucursales_dict:
                sucursales_dict[sucursal_nombre] = {}
            
            sucursales_dict[sucursal_nombre][mes_str] = int(item['total_ventas'])
        
        # Ordenar meses
        meses_ordenados = sorted(list(meses_set))
        
        # Formatear datos para el gráfico
        series_data = []
        for sucursal, ventas_por_mes in sucursales_dict.items():
            data = [ventas_por_mes.get(mes, 0) for mes in meses_ordenados]
            series_data.append({
                'name': sucursal,
                'data': data
            })
        
        # Formatear etiquetas de meses
        meses_labels = [datetime.strptime(m, '%Y-%m').strftime('%b %Y') for m in meses_ordenados]
        
        return JsonResponse({
            'success': True,
            'series': series_data,
            'categories': meses_labels
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener comparativa mensual: {str(e)}'
        })


# ========== REPORTE DE DOCUMENTOS EMITIDOS ==========

@login_required
def ver_documentos_emitidos(request):
    """Vista principal del reporte de documentos emitidos"""
    return render(request, 'vistas/modulo_reportes/documentos_emitidos.html')


@require_GET
@login_required
def obtener_documentos_emitidos(request):
    """API para obtener documentos emitidos con filtros"""
    try:
        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        tipo_documento = request.GET.get('tipo_documento')
        metodo_pago_filtro = request.GET.get('metodo_pago')
        
        # Si no se proporcionan fechas, usar el día actual
        if not fecha_desde or not fecha_hasta:
            fecha_fin = timezone.now().date()
            fecha_desde = fecha_fin
            fecha_hasta = fecha_fin
        else:
            fecha_desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        
        # Obtener sucursal según permisos
        if not request.user.is_superuser:
            sucursal_id = request.session.get('idSucursalActual')
        else:
            sucursal_id = None  # Superuser ve todas
        
        # Consultar DTEs (Boletas y Facturas Electrónicas)
        queryset = Dte.objects.select_related(
            'vendedor', 
            'receptor',
            'sucursal'
        ).prefetch_related(
            'dte_asociado'  # Prefetch de los detalles de pago
        ).filter(
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            fecha_emision__gte=fecha_desde,
            fecha_emision__lte=fecha_hasta
        )
        
        # Filtrar por sucursal
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Aplicar filtros
        if tipo_documento:
            if tipo_documento == 'BOLETA_ELECTRONICA':
                queryset = queryset.filter(tipo_documento='BOLETA ELECTRONICA')
            elif tipo_documento == 'BOLETA_PAPEL':
                queryset = queryset.filter(tipo_documento='BOLETA PAPEL')
            elif tipo_documento == 'FACTURA_ELECTRONICA':
                queryset = queryset.filter(tipo_documento='FACTURA ELECTRONICA')
            elif tipo_documento == 'FACTURA_EXENTA':
                queryset = queryset.filter(tipo_documento='FACTURA EXENTA')
            elif tipo_documento == 'BOLETA':
                queryset = queryset.filter(tipo_documento='BOLETA')
        
        # ✅ FILTRO POR MÉTODO DE PAGO - Aplicado ANTES de limitar registros
        if metodo_pago_filtro:
            from django.db.models import Q
            # Filtrar por método de pago en Dte_Detalle_Pago
            # Usamos distinct() porque un DTE puede tener múltiples detalles de pago
            queryset = queryset.filter(
                Q(dte_asociado__metodo_pago__icontains=metodo_pago_filtro)
            ).distinct()
            
            # Si el filtro es por método de pago específico del sistema de tickets, 
            # también necesitamos incluir DTEs sin detalles de pago pero con ticket relacionado
            # (Esta es una optimización adicional que puede implementarse después)
        
        # Ordernar por fecha descendente
        queryset = queryset.order_by('-fecha_emision', '-numero_documento')
        
        # Preparar datos de documentos
        documentos_data = []
        # ✅ Ahora el límite de 100 se aplica DESPUÉS del filtro de método de pago
        for dte in queryset[:100]:  # Limitar a 100 registros
            # Obtener información del cliente
            cliente_info = 'N.N'
            if dte.receptor:
                cliente_info = dte.receptor.nombre
                if dte.receptor.rut:
                    cliente_info += f' ({dte.receptor.rut})'
            
            # Obtener método de pago (desde detalles de pago)
            metodo_pago_display = 'No especificado'
            voucher = ''
            tipo_tarjeta = ''
            
            # Buscar en detalles de pago del DTE usando la relación inversa
            detalles_pago = dte.dte_asociado.all()
            
            # DEBUG: Contar detalles de pago
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"DTE {dte.id} - Detalles pago: {detalles_pago.count()}")
            
            if detalles_pago.exists():
                # Si hay múltiples pagos, concatenar
                metodos = []
                vouchers = []
                for detalle in detalles_pago:
                    logger.info(f"  Método: {detalle.metodo_pago}, Monto: {detalle.monto}")
                    metodos.append(detalle.metodo_pago)
                    if detalle.voucher:
                        vouchers.append(str(detalle.voucher))
                    if detalle.tipo_tarjeta and not tipo_tarjeta:
                        tipo_tarjeta = detalle.tipo_tarjeta
                
                metodo_pago_display = ', '.join(metodos) if metodos else 'No especificado'
                voucher = ', '.join(vouchers) if vouchers else ''
            else:
                # Si no hay detalles de pago, buscar en ticket relacionado
                logger.info(f"  No hay detalles de pago para DTE {dte.id}, buscando en tickets...")
                
                # Buscar ticket por correlativo y sucursal
                ticket_relacionado = Ticket.objects.filter(
                    correlativo=dte.numero_documento,
                    sucursal=dte.sucursal
                ).first()
                
                if ticket_relacionado:
                    logger.info(f"  Ticket encontrado: {ticket_relacionado.id}, método: {ticket_relacionado.metodo_pago}")
                    metodo_pago_display = dict(METODO_PAGO_TICKET_CHOICES).get(
                        ticket_relacionado.metodo_pago, 
                        ticket_relacionado.metodo_pago
                    )
                    
                    # Buscar voucher en detalles de pago del ticket
                    pago_ticket = TicketDetallePago.objects.filter(ticket=ticket_relacionado).first()
                    if pago_ticket:
                        voucher = pago_ticket.voucher or ''
                        tipo_tarjeta = pago_ticket.tipo_tarjeta or ''
                else:
                    logger.info(f"  No se encontró ticket relacionado")
                    metodo_pago_display = 'Efectivo'  # Default si no hay info
            
            documentos_data.append({
                'id': dte.id,
                'tipo_documento': dte.tipo_documento,
                'tipo_documento_display': dte.tipo_documento,
                'metodo_pago': metodo_pago_display,
                'metodo_pago_display': metodo_pago_display,
                'correlativo': dte.numero_documento,
                'cliente_info': cliente_info,
                'total': int(dte.monto_con_iva),
                'descuento': int(dte.descuento) if dte.descuento else 0,
                'vendedor': dte.vendedor.nombre if dte.vendedor else 'N/A',
                'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                'voucher': voucher,
                'tipo_tarjeta': tipo_tarjeta
            })
        
        # Calcular resúmenes por método de pago
        resumen = {
            'efectivo': 0,
            'tbk_credito': 0,
            'tbk_debito': 0,
            'tarjeta_comercial': 0,
            'convenio': 0,
            'descuentos': 0,
            'venta_internet': 0,
            'transferencia': 0,
            'credito_trabajador': 0,
            'otros': 0,
            'total_global': 0
        }
        
        # Calcular totales por método de pago desde Dte_Detalle_Pago
        for dte in queryset:
            total = int(dte.monto_con_iva)
            descuento = int(dte.descuento) if dte.descuento else 0
            
            resumen['descuentos'] += descuento
            resumen['total_global'] += total
            
            # Obtener métodos de pago del DTE usando la relación inversa
            detalles_pago = dte.dte_asociado.all()
            metodo_procesado = False
            
            if detalles_pago.exists():
                for detalle in detalles_pago:
                    metodo = detalle.metodo_pago.upper()
                    monto = int(detalle.monto)
                    metodo_procesado = True
                    
                    if 'EFECTIVO' in metodo:
                        resumen['efectivo'] += monto
                    elif ('CREDITO' in metodo or 'VISA' in metodo or 'MASTERCARD' in metodo or 'AMEX' in metodo or 'DINER' in metodo) and 'DEBITO' not in metodo:
                        resumen['tbk_credito'] += monto
                    elif 'DEBITO' in metodo or 'REDCOMPRA' in metodo:
                        resumen['tbk_debito'] += monto
                    elif 'COMERCIAL' in metodo or 'PARIS' in metodo or 'RIPLEY' in metodo or 'FALABELLA' in metodo:
                        resumen['tarjeta_comercial'] += monto
                    elif 'CONVENIO' in metodo:
                        resumen['convenio'] += monto
                    elif 'INTERNET' in metodo or 'WEB' in metodo:
                        resumen['venta_internet'] += monto
                    elif 'TRANSFERENCIA' in metodo:
                        resumen['transferencia'] += monto
                    elif 'CREDITO' in metodo and 'TRABAJADOR' in metodo:
                        resumen['credito_trabajador'] += monto
                    else:
                        resumen['otros'] += monto
            
            # Si no se procesó método de pago, buscar en ticket relacionado
            if not metodo_procesado:
                ticket_relacionado = Ticket.objects.filter(
                    correlativo=dte.numero_documento,
                    sucursal=dte.sucursal
                ).first()
                
                if ticket_relacionado:
                    metodo_ticket = ticket_relacionado.metodo_pago
                    
                    # Clasificar método de pago del ticket
                    if metodo_ticket == 'EFECTIVO':
                        resumen['efectivo'] += total
                    elif metodo_ticket in ['TBK_CREDITO_POS', 'TARJETA_CREDITO']:
                        resumen['tbk_credito'] += total
                    elif metodo_ticket in ['TBK_DEBITO_POS', 'TARJETA_DEBITO']:
                        resumen['tbk_debito'] += total
                    elif metodo_ticket == 'TARJETA_COMERCIAL':
                        resumen['tarjeta_comercial'] += total
                    elif metodo_ticket == 'CONVENIO':
                        resumen['convenio'] += total
                    elif metodo_ticket == 'VENTA_INTERNET':
                        resumen['venta_internet'] += total
                    elif metodo_ticket == 'TRANSFERENCIA':
                        resumen['transferencia'] += total
                    elif metodo_ticket == 'CREDITO_TRABAJADOR':
                        resumen['credito_trabajador'] += total
                    elif metodo_ticket == 'TBK_POS_INTEGRADO':
                        # Verificar tipo de tarjeta en detalles
                        pago_ticket = TicketDetallePago.objects.filter(ticket=ticket_relacionado).first()
                        if pago_ticket and pago_ticket.tipo_tarjeta:
                            if 'DEBITO' in pago_ticket.tipo_tarjeta.upper():
                                resumen['tbk_debito'] += total
                            else:
                                resumen['tbk_credito'] += total
                        else:
                            resumen['tbk_debito'] += total  # Default débito
                    else:
                        resumen['otros'] += total
                else:
                    # Si no hay ticket ni detalle de pago, asumir efectivo
                    resumen['efectivo'] += total
        
        return JsonResponse({
            'success': True,
            'documentos': documentos_data,
            'resumen': resumen,
            'total_registros': len(documentos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener documentos: {str(e)}'
        })


# ========== REPORTE DE EXISTENCIAS POR MARCA ==========

@login_required
def ver_reporte_existencias_marca(request):
    """Vista principal del reporte de existencias por marca"""
    return render(request, 'vistas/modulo_reportes/reporte_existencias_marca.html')


@require_GET
@login_required
def obtener_reporte_existencias_marca(request):
    """API para obtener datos del reporte de existencias por marca agrupado por sucursales"""
    try:
        # Parámetros de filtro
        marca_id = request.GET.get('marca_id')
        departamento_id = request.GET.get('departamento_id')
        
        # Obtener todas las sucursales
        sucursales = Sucursal.objects.all().order_by('alias')
        
        # Obtener productos
        queryset = Producto.objects.all().select_related(
            'atributo1', 'atributo2', 'atributo3', 'categoria', 'sucursal'
        ).prefetch_related('producto_talla')
        
        # Aplicar filtros
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
            print(f"🔍 Filtrando por marca_id={marca_id}")
        
        if departamento_id:
            queryset = queryset.filter(categoria_id=departamento_id)
        
        print(f"📦 Productos encontrados: {queryset.count()}")
        
        # Procesar datos - AGRUPAR POR PRODUCTO (no por talla)
        datos_reporte = []
        
        for producto in queryset:
            # Obtener stock por sucursal
            stock_por_sucursal = {}
            total_inicial = 0
            total_stock = 0
            
            # Obtener todas las tallas de este producto
            tallas = producto.producto_talla.all()
            
            for sucursal in sucursales:
                inicial_sucursal = 0
                stock_sucursal = 0
                
                # Sumar stock de todas las tallas en esta sucursal
                for talla in tallas:
                    # Verificar si esta talla pertenece a esta sucursal
                    if producto.sucursal_id == sucursal.id:
                        inicial_sucursal += talla.stock
                        stock_sucursal += talla.stock
                
                if inicial_sucursal > 0 or stock_sucursal > 0:
                    stock_por_sucursal[sucursal.alias] = {
                        'inicial': inicial_sucursal,
                        'stock': stock_sucursal,
                        'sucursal_id': sucursal.id
                    }
                    total_inicial += inicial_sucursal
                    total_stock += stock_sucursal
            
            # Solo agregar si tiene stock en alguna sucursal
            if total_stock > 0:
                datos_reporte.append({
                    'articulo': producto.articulo,
                    'marca': producto.atributo1.valor if producto.atributo1 else 'Sin Marca',
                    'marca_id': producto.atributo1.id if producto.atributo1 else None,
                    'color': producto.atributo2.valor if producto.atributo2 else '-',
                    'departamento': producto.categoria.nombre if producto.categoria else '-',
                    'costo': float(producto.costo) if producto.costo else 0,
                    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                    'sucursales': stock_por_sucursal,
                    'total_inicial': total_inicial,
                    'total_stock': total_stock
                })
        
        print(f"📊 Total productos en reporte: {len(datos_reporte)}")
        
        # Lista de sucursales para el frontend
        sucursales_data = [{'id': s.id, 'alias': s.alias} for s in sucursales]
        
        return JsonResponse({
            'success': True,
            'datos': datos_reporte,
            'sucursales': sucursales_data,
            'debug': {
                'total_productos': queryset.count(),
                'registros_reporte': len(datos_reporte),
                'total_sucursales': len(sucursales_data)
            }
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en reporte de existencias por marca: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener reporte: {str(e)}'
        })


@require_GET
@login_required
def exportar_existencias_marca_excel(request):
    """Exportar reporte de existencias por marca a Excel (por sucursales)"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        # Obtener datos del reporte
        marca_id = request.GET.get('marca_id')
        departamento_id = request.GET.get('departamento_id')
        
        # Reutilizar la función de obtención de datos
        temp_request = request
        response_data = obtener_reporte_existencias_marca(temp_request)
        datos = json.loads(response_data.content)
        
        if not datos.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'No se pudieron obtener los datos'
            })
        
        datos_reporte = datos.get('datos', [])
        sucursales_lista = datos.get('sucursales', [])
        
        if not datos_reporte:
            return JsonResponse({
                'success': False,
                'error': 'No hay datos para exportar'
            })
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Existencias por Marca"
        
        # Estilos
        header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        sucursal_fill = PatternFill(start_color="E7F1FF", end_color="E7F1FF", fill_type="solid")
        sucursal_font = Font(bold=True, color="004085", size=10)
        total_fill = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
        total_font = Font(bold=True, size=10)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Agrupar por marca
        por_marca = {}
        for item in datos_reporte:
            marca = item['marca']
            if marca not in por_marca:
                por_marca[marca] = []
            por_marca[marca].append(item)
        
        fila_actual = 1
        
        # Procesar cada marca
        for marca, productos in sorted(por_marca.items()):
            # Filtrar solo sucursales con stock en esta marca
            sucursales_con_stock = []
            for sucursal in sucursales_lista:
                tiene_stock = any(
                    sucursal['alias'] in p['sucursales'] and 
                    p['sucursales'][sucursal['alias']]['stock'] > 0 
                    for p in productos
                )
                if tiene_stock:
                    sucursales_con_stock.append(sucursal)
            
            # Título de la marca
            ws.merge_cells(f'A{fila_actual}:E{fila_actual}')
            cell = ws[f'A{fila_actual}']
            cell.value = f"MARCA: {marca}"
            cell.fill = header_fill
            cell.font = Font(bold=True, color="FFFFFF", size=14)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            fila_actual += 1
            
            # Encabezados principales
            headers_row1 = ['Artículo', 'Color', 'Depart', 'Costo', 'PrecioV.']
            
            # Agregar solo columnas de sucursales con stock
            for sucursal in sucursales_con_stock:
                headers_row1.append(sucursal['alias'])
                headers_row1.append('')  # Para el stock
                
            headers_row1.append('TT (TOTAL)')
            headers_row1.append('')  # Para el stock total
            
            # Escribir primera fila de encabezados
            for idx, header in enumerate(headers_row1, start=1):
                cell = ws.cell(row=fila_actual, column=idx, value=header if header else None)
                if header and header not in ['Artículo', 'Color', 'Depart', 'Costo', 'PrecioV.']:
                    cell.fill = sucursal_fill
                    cell.font = sucursal_font
                else:
                    cell.fill = header_fill
                    cell.font = header_font
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = border
            
            fila_actual += 1
            
            # Subencabezados (Inicial / Stk)
            headers_row2 = ['', '', '', '', '']  # Columnas fijas sin subencabezado
            for sucursal in sucursales_con_stock:
                headers_row2.extend(['Inicial', 'Stk'])
            headers_row2.extend(['Inicial', 'Stk'])
            
            for idx, header in enumerate(headers_row2, start=1):
                if header:
                    cell = ws.cell(row=fila_actual, column=idx, value=header)
                    if idx > 5:  # Solo las columnas de sucursales
                        cell.fill = sucursal_fill
                        cell.font = sucursal_font
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = border
            
            fila_actual += 1
            
            # Inicializar totales por columna
            totales_por_sucursal = {suc['alias']: {'inicial': 0, 'stock': 0} for suc in sucursales_con_stock}
            gran_total_inicial = 0
            gran_total_stock = 0
            
            # Datos de productos
            for producto in productos:
                row_data = [
                    producto['articulo'],
                    producto['color'],
                    producto['departamento'],
                    producto['costo'],
                    producto['precio_venta']
                ]
                
                # Datos por sucursal (solo las que tienen stock)
                for sucursal in sucursales_con_stock:
                    stock_suc = producto['sucursales'].get(sucursal['alias'])
                    if stock_suc:
                        row_data.append(stock_suc['inicial'])
                        row_data.append(stock_suc['stock'])
                        # Acumular totales
                        totales_por_sucursal[sucursal['alias']]['inicial'] += stock_suc['inicial']
                        totales_por_sucursal[sucursal['alias']]['stock'] += stock_suc['stock']
                    else:
                        row_data.append('-')
                        row_data.append('-')
                
                # Totales del producto
                row_data.append(producto['total_inicial'])
                row_data.append(producto['total_stock'])
                gran_total_inicial += producto['total_inicial']
                gran_total_stock += producto['total_stock']
                
                # Escribir fila
                for idx, value in enumerate(row_data, start=1):
                    cell = ws.cell(row=fila_actual, column=idx, value=value)
                    
                    # Aplicar formato
                    if idx > 5:  # Columnas numéricas
                        if idx in [len(row_data) - 1, len(row_data)]:  # Columnas de total
                            cell.fill = total_fill
                            cell.font = total_font
                        
                        if isinstance(value, (int, float)):
                            cell.alignment = Alignment(horizontal='right')
                        else:
                            cell.alignment = Alignment(horizontal='center')
                    else:
                        cell.alignment = Alignment(horizontal='left' if idx <= 2 else 'center')
                    
                    cell.border = border
                
                fila_actual += 1
            
            # Fila de TOTALES
            total_fill_yellow = PatternFill(start_color="FFC107", end_color="FFC107", fill_type="solid")
            ws.merge_cells(f'A{fila_actual}:E{fila_actual}')
            cell = ws[f'A{fila_actual}']
            cell.value = "TOTALES:"
            cell.font = Font(bold=True, size=12)
            cell.alignment = Alignment(horizontal='right', vertical='center')
            cell.fill = total_fill
            cell.border = border
            
            col_idx = 6
            for sucursal in sucursales_con_stock:
                total_suc = totales_por_sucursal[sucursal['alias']]
                
                cell = ws.cell(row=fila_actual, column=col_idx, value=total_suc['inicial'])
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='right')
                cell.border = border
                col_idx += 1
                
                cell = ws.cell(row=fila_actual, column=col_idx, value=total_suc['stock'])
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='right')
                cell.border = border
                col_idx += 1
            
            # Gran total
            cell = ws.cell(row=fila_actual, column=col_idx, value=gran_total_inicial)
            cell.font = Font(bold=True, size=12)
            cell.fill = total_fill_yellow
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila_actual, column=col_idx + 1, value=gran_total_stock)
            cell.font = Font(bold=True, size=12)
            cell.fill = total_fill_yellow
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            fila_actual += 1
            
            # Espacio entre marcas
            fila_actual += 2
        
        # Ajustar anchos de columna
        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 12
        
        for i in range(6, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(i)].width = 10
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="reporte_existencias_marca.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        print(f"❌ Error al exportar a Excel: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })


# ========== REPORTE DE EXISTENCIAS POR SUCURSAL ==========

@login_required
def ver_reporte_existencias_sucursal(request):
    """Vista principal del reporte de existencias por sucursal"""
    return render(request, 'vistas/modulo_reportes/reporte_existencias_sucursal.html')


@require_GET
@login_required
def obtener_reporte_existencias_sucursal(request):
    """API para obtener datos del reporte de existencias por sucursal"""
    try:
        # Parámetros de filtro
        sucursal_id = request.GET.get('sucursal_id')
        marca_id = request.GET.get('marca_id')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Sucursal es requerida'
            })
        
        # Obtener sucursal
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Obtener productos de esta sucursal
        queryset = Producto.objects.filter(
            sucursal_id=sucursal_id
        ).select_related(
            'atributo1', 'atributo2', 'atributo3', 'categoria'
        ).prefetch_related('producto_talla')
        
        # Filtrar por marca si se especifica
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
        
        print(f"🏪 Sucursal: {sucursal.alias}")
        print(f"📦 Productos encontrados: {queryset.count()}")
        
        # Procesar datos - Una fila por talla
        datos_reporte = []
        total_stock = 0
        valor_inventario = 0
        productos_sin_stock = 0
        
        for producto in queryset:
            for talla in producto.producto_talla.all():
                stock = talla.stock
                
                if stock == 0:
                    productos_sin_stock += 1
                    continue  # Saltar productos sin stock
                
                total_stock += stock
                valor_inventario += (producto.costo * stock) if producto.costo else 0
                
                datos_reporte.append({
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion or '-',
                    'categoria': producto.categoria.nombre if producto.categoria else '-',
                    'marca': producto.atributo1.valor if producto.atributo1 else '-',
                    'color': producto.atributo2.valor if producto.atributo2 else '-',
                    'genero': producto.atributo3.valor if producto.atributo3 else '-',
                    'talla': talla.talla if talla.talla else '-',
                    'stock_inicial': stock,  # En sistema legacy, el stock actual es el inicial
                    'stock': stock,
                    'costo': float(producto.costo) if producto.costo else 0,
                    'sobreprecio': float(producto.sobreprecio) if producto.sobreprecio else 0,
                    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                })
        
        print(f"📊 Total registros: {len(datos_reporte)}")
        print(f"📈 Stock total: {total_stock}")
        
        # Resumen
        resumen = {
            'total_productos': len(datos_reporte),
            'stock_total': total_stock,
            'valor_inventario': valor_inventario,
            'sin_stock': productos_sin_stock,
            'sucursal': sucursal.alias
        }
        
        return JsonResponse({
            'success': True,
            'datos': datos_reporte,
            'resumen': resumen
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en reporte de existencias por sucursal: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener reporte: {str(e)}'
        })


@require_GET
@login_required
def exportar_existencias_sucursal_excel(request):
    """Exportar reporte de existencias por sucursal a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        # Obtener datos del reporte
        sucursal_id = request.GET.get('sucursal_id')
        marca_id = request.GET.get('marca_id')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Sucursal es requerida'
            })
        
        # Reutilizar la función de obtención de datos
        temp_request = request
        response_data = obtener_reporte_existencias_sucursal(temp_request)
        datos = json.loads(response_data.content)
        
        if not datos.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'No se pudieron obtener los datos'
            })
        
        datos_reporte = datos.get('datos', [])
        resumen = datos.get('resumen', {})
        
        if not datos_reporte:
            return JsonResponse({
                'success': False,
                'error': 'No hay datos para exportar'
            })
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Existencias {resumen.get('sucursal', 'Sucursal')}"
        
        # Estilos
        header_fill = PatternFill(start_color="28A745", end_color="28A745", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws.merge_cells('A1:L1')
        cell = ws['A1']
        cell.value = f"REPORTE DE EXISTENCIAS - {resumen.get('sucursal', '').upper()}"
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF", size=14)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Encabezados
        headers = [
            'Artículo', 'Descripción', 'Categoría', 'Marca', 'Color', 'Género',
            'Talla', 'Stock Inicial', 'Stock', 'Costo', 'Sobreprecio', 'Precio Venta'
        ]
        
        for idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=2, column=idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
        
        # Datos
        fila = 3
        for item in datos_reporte:
            ws.cell(row=fila, column=1, value=item['articulo']).border = border
            ws.cell(row=fila, column=2, value=item['descripcion']).border = border
            ws.cell(row=fila, column=3, value=item['categoria']).border = border
            ws.cell(row=fila, column=4, value=item['marca']).border = border
            ws.cell(row=fila, column=5, value=item['color']).border = border
            ws.cell(row=fila, column=6, value=item['genero']).border = border
            
            cell = ws.cell(row=fila, column=7, value=item['talla'])
            cell.alignment = Alignment(horizontal='center')
            cell.border = border
            
            cell = ws.cell(row=fila, column=8, value=item['stock_inicial'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=9, value=item['stock'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=10, value=item['costo'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=11, value=item['sobreprecio'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=12, value=item['precio_venta'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            fila += 1
        
        # Ajustar anchos de columna
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 30
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 15
        ws.column_dimensions['F'].width = 15
        ws.column_dimensions['G'].width = 10
        ws.column_dimensions['H'].width = 12
        ws.column_dimensions['I'].width = 12
        ws.column_dimensions['J'].width = 12
        ws.column_dimensions['K'].width = 12
        ws.column_dimensions['L'].width = 12
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="existencias_{resumen.get("sucursal", "sucursal")}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        print(f"❌ Error al exportar a Excel: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })