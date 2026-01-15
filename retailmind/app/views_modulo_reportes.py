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
    Compras, Dte, Dte_Productos, Dte_Detalle_Pago, Ticket, Ticket_Productos, 
    Producto, Producto_Talla, Movimientos_Producto, Sucursal, EmpresaUser, 
    Empresa, Vendedor, LoteProducto, Traspaso, AjusteInventario, 
    TicketDetallePago, METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES
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
    # Obtener sucursal activa de la sesión
    sucursal_activa_id = request.session.get('idSucursalActual')
    sucursal_activa_nombre = request.session.get('nombreSucursalActual', '')
    
    context = {
        'sucursal_activa_id': sucursal_activa_id,
        'sucursal_activa_nombre': sucursal_activa_nombre,
        'es_superuser': request.user.is_superuser,
    }
    return render(request, 'vistas/modulo_reportes/reporte_ventas_sucursal.html', context)


@require_GET
@login_required
def obtener_ventas_por_vendedor_reporte(request):
    """API para obtener datos de ventas por vendedor (incluye Tickets y DTEs)"""
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')  # Formato: YYYY-MM
        fecha = request.GET.get('fecha')  # Formato: YYYY-MM-DD (fecha específica)
        fecha_inicio_param = request.GET.get('fecha_inicio')  # Formato: YYYY-MM-DD (rango)
        fecha_fin_param = request.GET.get('fecha_fin')  # Formato: YYYY-MM-DD (rango)
        sucursal_id = request.GET.get('sucursal_id')
        vendedor_id = request.GET.get('vendedor_id')

        # Determinar rango de fechas según el tipo de filtro
        if fecha:
            # Filtro por fecha específica
            fecha_inicio = datetime.strptime(fecha, '%Y-%m-%d')
            fecha_fin = fecha_inicio
        elif fecha_inicio_param and fecha_fin_param:
            # Filtro por rango de fechas
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            # Filtro por mes (por defecto)
            if not mes:
                mes = timezone.now().strftime('%Y-%m')
            # Convertir mes a rango de fechas
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            # Último día del mes
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)
        
        # Diccionario para acumular ventas por vendedor
        ventas_acumuladas = {}  # {vendedor_id: {'nombre': ..., 'codigo': ..., 'ventas': ..., 'documentos': ...}}
        
        # ========== TICKETS (POS nuevo) ==========
        queryset_tickets = Ticket.objects.filter(
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
            estado='PAGADO'
        ).select_related('vendedor', 'sucursal')
        
        # Filtrar por sucursal según selección o permisos
        if sucursal_id:
            queryset_tickets = queryset_tickets.filter(sucursal_id=sucursal_id)
        elif not request.user.is_superuser:
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset_tickets = queryset_tickets.filter(sucursal_id=sucursal_sesion)

        if vendedor_id:
            queryset_tickets = queryset_tickets.filter(vendedor_id=vendedor_id)

        # Agrupar tickets por vendedor
        tickets_por_vendedor = queryset_tickets.values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor'
        ).annotate(
            total_ventas=Sum('total'),
            total_documentos=Count('id')
        )
        
        for item in tickets_por_vendedor:
            vid = item['vendedor__id']
            if vid:
                if vid not in ventas_acumuladas:
                    ventas_acumuladas[vid] = {
                        'nombre': item['vendedor__nombre'],
                        'codigo': item['vendedor__codigo_vendedor'],
                        'ventas': 0,
                        'documentos': 0
                    }
                ventas_acumuladas[vid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[vid]['documentos'] += item['total_documentos'] or 0
        
        # ========== DTEs (Documentos migrados) ==========
        # Incluir estados: EMITIDO (nuevo), PAGADO (migrados de Laravel)
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fecha_inicio,
            fecha_emision__lte=fecha_fin,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            estado_dte__in=['EMITIDO', 'PAGADO']
        ).select_related('vendedor', 'sucursal')

        # Filtrar por sucursal según selección o permisos
        if sucursal_id:
            queryset_dtes = queryset_dtes.filter(sucursal_id=sucursal_id)
        elif not request.user.is_superuser:
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset_dtes = queryset_dtes.filter(sucursal_id=sucursal_sesion)
        
        if vendedor_id:
            queryset_dtes = queryset_dtes.filter(vendedor_id=vendedor_id)
        
        # Agrupar DTEs por vendedor
        dtes_por_vendedor = queryset_dtes.values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor'
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            total_documentos=Count('id')
        )
        
        for item in dtes_por_vendedor:
            vid = item['vendedor__id']
            if vid:
                if vid not in ventas_acumuladas:
                    ventas_acumuladas[vid] = {
                        'nombre': item['vendedor__nombre'],
                        'codigo': item['vendedor__codigo_vendedor'],
                        'ventas': 0,
                        'documentos': 0
                    }
                ventas_acumuladas[vid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[vid]['documentos'] += item['total_documentos'] or 0
        
        # Calcular total general
        total_general = sum(v['ventas'] for v in ventas_acumuladas.values())
        
        # Procesar datos ordenados por ventas
        vendedores_data = []
        for vid, data in sorted(ventas_acumuladas.items(), key=lambda x: x[1]['ventas'], reverse=True):
            participacion = (data['ventas'] / total_general * 100) if total_general > 0 else 0
            vendedores_data.append({
                'id': vid,
                'nombre': data['nombre'],
                'codigo': data['codigo'],
                'ventas': data['ventas'],
                'documentos': data['documentos'],
                'participacion': round(participacion, 1)
            })
        
        # KPIs
        total_documentos = sum(v['documentos'] for v in ventas_acumuladas.values())
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
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por vendedor: {str(e)}'
        })


@require_GET
@login_required
def obtener_ventas_por_sucursal_reporte(request):
    """API para obtener datos de ventas por sucursal (incluye Tickets y DTEs)"""
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')  # Formato: YYYY-MM
        fecha = request.GET.get('fecha')  # Formato: YYYY-MM-DD (fecha específica)
        fecha_inicio_param = request.GET.get('fecha_inicio')  # Formato: YYYY-MM-DD (rango)
        fecha_fin_param = request.GET.get('fecha_fin')  # Formato: YYYY-MM-DD (rango)
        sucursal_id = request.GET.get('sucursal_id')
        
        # Determinar rango de fechas según el tipo de filtro
        if fecha:
            # Filtro por fecha específica
            fecha_inicio = datetime.strptime(fecha, '%Y-%m-%d')
            fecha_fin = fecha_inicio
        elif fecha_inicio_param and fecha_fin_param:
            # Filtro por rango de fechas
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            # Filtro por mes (por defecto)
            if not mes:
                mes = timezone.now().strftime('%Y-%m')
            # Convertir mes a rango de fechas
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            # Último día del mes
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)
        
        # Diccionario para acumular ventas por sucursal
        ventas_acumuladas = {}  # {sucursal_id: {...}}
        vendedores_por_sucursal = {}  # {sucursal_id: set(vendedor_ids)}
        
        # ========== TICKETS (POS nuevo) ==========
        queryset_tickets = Ticket.objects.filter(
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
            estado='PAGADO'
        ).select_related('sucursal', 'vendedor')
        
        # Filtrar por sucursal según selección o permisos
        # Prioridad: 1) Sucursal del dropdown, 2) Sucursal de sesión (si no es superuser)
        if sucursal_id:
            # Si se seleccionó una sucursal específica, filtrar por ella
            queryset_tickets = queryset_tickets.filter(sucursal_id=sucursal_id)
        elif not request.user.is_superuser:
            # Si no es superuser y no seleccionó sucursal, usar la de sesión
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset_tickets = queryset_tickets.filter(sucursal_id=sucursal_sesion)
        # Si es superuser y no seleccionó sucursal, mostrar todas
        
        # Agrupar tickets por sucursal
        tickets_por_sucursal = queryset_tickets.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion'
        ).annotate(
            total_ventas=Sum('total'),
            subtotal_ventas=Sum('subTotal'),
            total_documentos=Count('id')
        )
        
        for item in tickets_por_sucursal:
            sid = item['sucursal__id']
            if sid:
                if sid not in ventas_acumuladas:
                    ventas_acumuladas[sid] = {
                        'alias': item['sucursal__alias'],
                        'direccion': item['sucursal__direccion'],
                        'ventas': 0,
                        'subtotal': 0,
                        'documentos': 0
                    }
                    vendedores_por_sucursal[sid] = set()
                ventas_acumuladas[sid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[sid]['subtotal'] += int(item['subtotal_ventas'] or 0)
                ventas_acumuladas[sid]['documentos'] += item['total_documentos'] or 0
        
        # Contar vendedores de tickets
        for ticket in queryset_tickets.values('sucursal_id', 'vendedor_id').distinct():
            sid = ticket['sucursal_id']
            vid = ticket['vendedor_id']
            if sid and vid:
                if sid not in vendedores_por_sucursal:
                    vendedores_por_sucursal[sid] = set()
                vendedores_por_sucursal[sid].add(vid)
        
        # ========== DTEs (Documentos migrados) ==========
        # Incluir estados: EMITIDO (nuevo), PAGADO (migrados de Laravel)
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fecha_inicio,
            fecha_emision__lte=fecha_fin,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            estado_dte__in=['EMITIDO', 'PAGADO']
        ).select_related('sucursal', 'vendedor')

        # Filtrar por sucursal según selección o permisos
        if sucursal_id:
            queryset_dtes = queryset_dtes.filter(sucursal_id=sucursal_id)
        elif not request.user.is_superuser:
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                queryset_dtes = queryset_dtes.filter(sucursal_id=sucursal_sesion)
        
        # Agrupar DTEs por sucursal
        dtes_por_sucursal = queryset_dtes.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion'
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            subtotal_ventas=Sum('monto_neto'),
            total_documentos=Count('id')
        )
        
        for item in dtes_por_sucursal:
            sid = item['sucursal__id']
            if sid:
                if sid not in ventas_acumuladas:
                    ventas_acumuladas[sid] = {
                        'alias': item['sucursal__alias'],
                        'direccion': item['sucursal__direccion'],
                        'ventas': 0,
                        'subtotal': 0,
                        'documentos': 0
                    }
                    vendedores_por_sucursal[sid] = set()
                ventas_acumuladas[sid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[sid]['subtotal'] += int(item['subtotal_ventas'] or 0)
                ventas_acumuladas[sid]['documentos'] += item['total_documentos'] or 0
        
        # Contar vendedores de DTEs
        for dte in queryset_dtes.values('sucursal_id', 'vendedor_id').distinct():
            sid = dte['sucursal_id']
            vid = dte['vendedor_id']
            if sid and vid:
                if sid not in vendedores_por_sucursal:
                    vendedores_por_sucursal[sid] = set()
                vendedores_por_sucursal[sid].add(vid)
        
        # Calcular total general
        total_general = sum(v['ventas'] for v in ventas_acumuladas.values())
        
        # Convertir a formato de salida ordenado
        ventas_por_sucursal = []
        for sid, data in sorted(ventas_acumuladas.items(), key=lambda x: x[1]['ventas'], reverse=True):
            total_docs = data['documentos']
            ventas_por_sucursal.append({
                'sucursal__id': sid,
                'sucursal__alias': data['alias'],
                'sucursal__direccion': data['direccion'],
                'total_ventas': data['ventas'],
                'subtotal_ventas': data['subtotal'],
                'total_documentos': total_docs,
                'ticket_promedio': data['ventas'] / total_docs if total_docs > 0 else 0,
                'vendedores_count': len(vendedores_por_sucursal.get(sid, set()))
            })

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
    """API para obtener lista de vendedores que tienen ventas en la sucursal"""
    try:
        sucursal_id = request.GET.get('sucursal_id')

        if sucursal_id:
            # Obtener vendedores que tienen ventas EN esta sucursal (Tickets o DTEs)
            from django.db.models import Q
            
            # IDs de vendedores con Tickets en esta sucursal
            vendedores_tickets = set(Ticket.objects.filter(
                sucursal_id=sucursal_id
            ).values_list('vendedor_id', flat=True).distinct())
            
            # IDs de vendedores con DTEs en esta sucursal
            vendedores_dtes = set(Dte.objects.filter(
                sucursal_id=sucursal_id,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
            ).values_list('vendedor_id', flat=True).distinct())
            
            # Unir ambos conjuntos
            vendedores_ids = vendedores_tickets | vendedores_dtes
            vendedores_ids.discard(None)  # Remover None si existe
            
            vendedores = Vendedor.objects.filter(
                id__in=vendedores_ids,
                nombre__isnull=False
            ).order_by('nombre')
        else:
            # Sin filtro de sucursal, mostrar todos
            vendedores = Vendedor.objects.filter(
                nombre__isnull=False
            ).order_by('nombre')

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
        
        # Filtrar siempre por sucursal activa de la sesión
        sucursal_id = request.session.get('idSucursalActual')
        
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
            pagos_detalle = {}
            
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
                    pagos_detalle[detalle.metodo_pago] = pagos_detalle.get(detalle.metodo_pago, 0) + int(detalle.monto or 0)
                
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
                    pagos_ticket = TicketDetallePago.objects.filter(ticket=ticket_relacionado)
                    if pagos_ticket.exists():
                        vouchers = []
                        for pago_ticket in pagos_ticket:
                            if pago_ticket.voucher:
                                vouchers.append(str(pago_ticket.voucher))
                            if pago_ticket.tipo_tarjeta and not tipo_tarjeta:
                                tipo_tarjeta = pago_ticket.tipo_tarjeta
                            metodo_ticket = dict(METODO_PAGO_TICKET_CHOICES).get(
                                pago_ticket.metodo_pago,
                                pago_ticket.metodo_pago
                            )
                            pagos_detalle[metodo_ticket] = pagos_detalle.get(metodo_ticket, 0) + int(pago_ticket.monto or 0)
                        voucher = ', '.join(vouchers) if vouchers else ''
                else:
                    logger.info(f"  No se encontró ticket relacionado")
                    metodo_pago_display = 'Efectivo'  # Default si no hay info
                    pagos_detalle['Efectivo'] = int(dte.monto_con_iva or 0)
            
            pagos_detalle_str = ', '.join(
                [f"{metodo} ${int(monto):,}".replace(',', '.') for metodo, monto in pagos_detalle.items()]
            ) if pagos_detalle else 'No especificado'
            
            documentos_data.append({
                'id': dte.id,
                'tipo_documento': dte.tipo_documento,
                'tipo_documento_display': dte.tipo_documento,
                'metodo_pago': metodo_pago_display,
                'metodo_pago_display': metodo_pago_display,
                'pagos_detalle': pagos_detalle,
                'pagos_detalle_str': pagos_detalle_str,
                'correlativo': dte.numero_documento,
                'cliente_info': cliente_info,
                'total': int(dte.monto_con_iva),
                'descuento': int(dte.descuento) if dte.descuento else 0,
                'vendedor': dte.vendedor.nombre if dte.vendedor else 'N/A',
                'vendedor_codigo': dte.vendedor.codigo_vendedor if dte.vendedor else '',
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


@require_GET
@login_required
def exportar_documentos_emitidos_excel(request):
    """Exportar documentos emitidos a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from io import BytesIO

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

        # Filtrar siempre por sucursal activa de la sesión
        sucursal_id = request.session.get('idSucursalActual')

        # Consultar DTEs (Boletas y Facturas Electrónicas)
        queryset = Dte.objects.select_related(
            'vendedor',
            'receptor',
            'sucursal'
        ).prefetch_related(
            'dte_asociado'
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

        if metodo_pago_filtro:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(dte_asociado__metodo_pago__icontains=metodo_pago_filtro)
            ).distinct()

        queryset = queryset.order_by('-fecha_emision', '-numero_documento')

        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Documentos Emitidos"

        # Encabezados
        headers = [
            'ID', 'Tipo Documento', 'Método Pago', 'N° Documento', 'Cliente/RUT',
            'Total', 'Descuento', 'Pagado', 'Vendedor', 'Fecha', 'Voucher'
        ]
        ws.append(headers)

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4154F1", end_color="4154F1", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment

        for dte in queryset:
            # Cliente
            cliente_info = 'N.N'
            if dte.receptor:
                cliente_info = dte.receptor.nombre
                if dte.receptor.rut:
                    cliente_info += f' ({dte.receptor.rut})'

            # Método de pago y voucher
            metodo_pago_display = 'No especificado'
            voucher = ''
            tipo_tarjeta = ''

            detalles_pago = dte.dte_asociado.all()
            if detalles_pago.exists():
                metodos = []
                vouchers = []
                for detalle in detalles_pago:
                    metodos.append(detalle.metodo_pago)
                    if detalle.voucher:
                        vouchers.append(str(detalle.voucher))
                    if detalle.tipo_tarjeta and not tipo_tarjeta:
                        tipo_tarjeta = detalle.tipo_tarjeta

                metodo_pago_display = ', '.join(metodos) if metodos else 'No especificado'
                voucher = ', '.join(vouchers) if vouchers else ''
            else:
                ticket_relacionado = Ticket.objects.filter(
                    correlativo=dte.numero_documento,
                    sucursal=dte.sucursal
                ).first()

                if ticket_relacionado:
                    metodo_pago_display = dict(METODO_PAGO_TICKET_CHOICES).get(
                        ticket_relacionado.metodo_pago,
                        ticket_relacionado.metodo_pago
                    )
                    pago_ticket = TicketDetallePago.objects.filter(ticket=ticket_relacionado).first()
                    if pago_ticket:
                        voucher = pago_ticket.voucher or ''
                        tipo_tarjeta = pago_ticket.tipo_tarjeta or ''
                else:
                    metodo_pago_display = 'Efectivo'

            vendedor_nombre = dte.vendedor.nombre if dte.vendedor else 'N/A'
            vendedor_codigo = dte.vendedor.codigo_vendedor if dte.vendedor else ''
            vendedor_display = (
                f'{vendedor_nombre} ({vendedor_codigo})' if vendedor_codigo else vendedor_nombre
            )

            total = int(dte.monto_con_iva)
            descuento = int(dte.descuento) if dte.descuento else 0

            ws.append([
                dte.id,
                dte.tipo_documento,
                metodo_pago_display,
                dte.numero_documento,
                cliente_info,
                total,
                descuento,
                total,
                vendedor_display,
                dte.fecha_emision.strftime('%d/%m/%Y'),
                voucher
            ])

        # Ajustar ancho de columnas
        for column_cells in ws.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter
            for cell in column_cells:
                try:
                    max_length = max(max_length, len(str(cell.value)) if cell.value is not None else 0)
                except Exception:
                    pass
            ws.column_dimensions[column_letter].width = min(max_length + 2, 40)

        # Respuesta
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"documentos_emitidos_{fecha_desde.strftime('%Y%m%d')}_{fecha_hasta.strftime('%Y%m%d')}.xlsx"
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename=\"{filename}\"'
        return response

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar documentos emitidos: {str(e)}'
        })


# ========== REPORTE DE EXISTENCIAS POR MARCA ==========

@login_required
def ver_reporte_existencias_marca(request):
    """Vista principal del reporte de existencias por marca"""
    # Obtener sucursal actual para pasarla al template
    sucursal_actual_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    sucursal_actual = None
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            pass
    
    context = {
        'sucursal_actual': sucursal_actual,
        'sucursal_actual_id': sucursal_actual_id,
    }
    return render(request, 'vistas/modulo_reportes/reporte_existencias_marca.html', context)


@require_GET
@login_required
def obtener_reporte_existencias_marca(request):
    """
    API OPTIMIZADA para reporte de existencias por marca.
    
    CAMBIOS DE OPTIMIZACIÓN:
    1. Requiere al menos un filtro obligatorio (marca, departamento, búsqueda o sucursal)
    2. Usa la sucursal actual del usuario por defecto
    3. Limita resultados a 500 productos máximo
    4. Usa agregación en BD en lugar de Python
    5. Solo carga los campos necesarios con .only()
    """
    try:
        # ========== PARÁMETROS DE FILTRO ==========
        marca_id = request.GET.get('marca_id')
        departamento_id = request.GET.get('departamento_id')
        busqueda = request.GET.get('busqueda', '').strip()
        sucursal_id = request.GET.get('sucursal_id')
        solo_con_stock = request.GET.get('solo_con_stock', 'true') == 'true'
        limite = int(request.GET.get('limite', 500))
        sin_filtro = request.GET.get('sin_filtro', 'false') == 'true'
        
        # Obtener sucursal actual del usuario si no se especifica
        if not sucursal_id:
            sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # ========== VALIDACIÓN: REQUIERE AL MENOS UN FILTRO (A MENOS QUE FUERCE SIN FILTRO) ==========
        tiene_filtro = any([marca_id, departamento_id, busqueda])
        
        if not tiene_filtro and not sin_filtro:
            return JsonResponse({
                'success': False,
                'requiere_filtro': True,
                'error': 'Por favor selecciona al menos un filtro: Marca, Departamento o usa el buscador de artículos.',
                'sugerencia': 'Esto optimiza la consulta y evita cargar los 50,000+ productos.'
            })
        
        # Si no hay filtro, limitar más estrictamente
        if not tiene_filtro:
            limite = min(limite, 500)  # Máximo 500 sin filtro
        
        # ========== OBTENER SUCURSALES ==========
        # Si hay sucursal específica, solo esa; si no, todas las del usuario
        if sucursal_id:
            sucursales = Sucursal.objects.filter(id=sucursal_id)
        else:
            # Obtener sucursales del usuario
            empresas_usuario = EmpresaUser.objects.filter(
                user=request.user,
                status=True
            ).values_list('empresa_id', flat=True)
            sucursales = Sucursal.objects.filter(empresa_id__in=empresas_usuario).order_by('alias')
        
        sucursales_list = list(sucursales)
        sucursales_ids = [s.id for s in sucursales_list]
        
        # ========== CONSULTA OPTIMIZADA ==========
        # Base: productos de las sucursales del usuario
        queryset = Producto.objects.filter(
            sucursal_id__in=sucursales_ids
        ).select_related(
            'atributo1', 'atributo2', 'categoria', 'sucursal'
        ).only(
            'id', 'articulo', 'costo', 'precioventa', 'sucursal_id',
            'atributo1__id', 'atributo1__valor',
            'atributo2__id', 'atributo2__valor',
            'categoria__id', 'categoria__nombre',
            'sucursal__id', 'sucursal__alias'
        )
        
        # ========== APLICAR FILTROS ==========
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
        
        if departamento_id:
            queryset = queryset.filter(categoria_id=departamento_id)
        
        if busqueda:
            # Búsqueda por artículo, marca o color
            queryset = queryset.filter(
                Q(articulo__icontains=busqueda) |
                Q(atributo1__valor__icontains=busqueda) |
                Q(atributo2__valor__icontains=busqueda)
            )
        
        # ========== AGREGAR STOCK CON ANOTACIÓN ==========
        # Anotar el stock total por producto (suma de todas las tallas)
        queryset = queryset.annotate(
            total_stock_anotado=Sum('producto_talla__stock')
        )
        
        # Filtrar solo productos con stock si se requiere
        if solo_con_stock:
            queryset = queryset.filter(total_stock_anotado__gt=0)
        
        # Ordenar y limitar
        queryset = queryset.order_by('atributo1__valor', 'articulo')[:limite]
        
        # ========== PROCESAR DATOS ==========
        datos_reporte = []
        productos_procesados = 0
        
        # Prefetch de tallas para los productos filtrados
        productos_ids = [p.id for p in queryset]
        tallas_por_producto = {}
        
        if productos_ids:
            from django.db.models import Sum as DjangoSum
            tallas = Producto_Talla.objects.filter(
                producto_id__in=productos_ids
            ).values('producto_id').annotate(
                stock_total=DjangoSum('stock')
            )
            tallas_por_producto = {t['producto_id']: t['stock_total'] or 0 for t in tallas}
        
        for producto in queryset:
            productos_procesados += 1
            
            # Stock ya viene anotado, pero usamos el de tallas para precisión
            stock_total = tallas_por_producto.get(producto.id, 0)
            
            if solo_con_stock and stock_total <= 0:
                continue
            
            # Construir datos de sucursal
            stock_por_sucursal = {}
            if producto.sucursal:
                stock_por_sucursal[producto.sucursal.alias] = {
                    'inicial': stock_total,
                    'stock': stock_total,
                    'sucursal_id': producto.sucursal_id
                }
            
            datos_reporte.append({
                'articulo': producto.articulo,
                'marca': producto.atributo1.valor if producto.atributo1 else 'Sin Marca',
                'marca_id': producto.atributo1.id if producto.atributo1 else None,
                'color': producto.atributo2.valor if producto.atributo2 else '-',
                'departamento': producto.categoria.nombre if producto.categoria else '-',
                'costo': float(producto.costo) if producto.costo else 0,
                'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                'sucursales': stock_por_sucursal,
                'total_inicial': stock_total,
                'total_stock': stock_total
            })
        
        # ========== RESPUESTA ==========
        sucursales_data = [{'id': s.id, 'alias': s.alias} for s in sucursales_list]
        
        return JsonResponse({
            'success': True,
            'datos': datos_reporte,
            'sucursales': sucursales_data,
            'filtros_aplicados': {
                'marca_id': marca_id,
                'departamento_id': departamento_id,
                'busqueda': busqueda,
                'sucursal_id': sucursal_id,
                'solo_con_stock': solo_con_stock,
            },
            'debug': {
                'productos_procesados': productos_procesados,
                'registros_reporte': len(datos_reporte),
                'total_sucursales': len(sucursales_data),
                'limite_aplicado': limite
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


# ========== REPORTE DE COMPRAS INTEGRAL ==========

@login_required
def ver_reporte_compras(request):
    """Vista principal del reporte de compras"""
    return render(request, 'vistas/modulo_reportes/reporte_compras.html')


@login_required
@require_GET
def api_reporte_compras(request):
    """
    API completa para el reporte de compras.
    Proporciona métricas, análisis por proveedor, estado de recepciones y pagos.
    """
    try:
        from .models import (
            Compras, Compras_Producto, Compras_Producto_Talla,
            Productos_Recepcionados, Dte, Dte_Productos
        )
        
        # Parámetros de filtro
        anio = int(request.GET.get('anio', datetime.now().year))
        periodo = request.GET.get('periodo', 'anual')
        proveedor_id = request.GET.get('proveedor', '')
        temporada = request.GET.get('temporada', '')
        
        # Calcular rango de fechas según período
        hoy = datetime.now()
        if periodo == 'mes':
            fecha_inicio = hoy - timedelta(days=30)
        elif periodo == 'trimestre':
            fecha_inicio = hoy - timedelta(days=90)
        elif periodo == 'semana':
            fecha_inicio = hoy - timedelta(days=7)
        else:  # anual
            fecha_inicio = datetime(anio, 1, 1)
        
        fecha_fin = hoy
        
        # ===== MÉTRICAS PRINCIPALES =====
        metricas = calcular_metricas_compras(anio, periodo, proveedor_id, temporada)
        
        # ===== EVOLUCIÓN MENSUAL =====
        evolucion_mensual = calcular_evolucion_mensual_compras(anio, proveedor_id, temporada)
        
        # ===== TOP PROVEEDORES =====
        top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada)
        
        # ===== PARETO PROVEEDORES =====
        pareto_proveedores = calcular_pareto_proveedores_compras(anio, proveedor_id, temporada)
        
        # ===== CUMPLIMIENTO PROVEEDORES =====
        cumplimiento_proveedores = calcular_cumplimiento_proveedores_compras(anio, proveedor_id, temporada)
        
        # ===== ROI POR TEMPORADA =====
        roi_temporadas = calcular_roi_temporadas_compras(anio, proveedor_id)
        
        # ===== COMPARATIVA ANUAL =====
        comparativa_anual = calcular_comparativa_anual_compras(anio, proveedor_id, temporada)
        
        # ===== ESTADO DE RECEPCIONES =====
        estado_recepciones = calcular_estado_recepciones_compras(anio, proveedor_id, temporada)
        metricas_recepcion = calcular_metricas_recepcion_compras(anio, proveedor_id, temporada)
        recepciones_pendientes = obtener_recepciones_pendientes_compras(anio, proveedor_id)
        
        # ===== ESTADO DE PAGOS =====
        estado_pagos = calcular_estado_pagos_compras(anio, proveedor_id, temporada)
        vencimientos = calcular_vencimientos_compras()
        pagos_pendientes = obtener_pagos_pendientes_compras()
        
        # ===== INSIGHTS Y ALERTAS =====
        insights = generar_insights_compras(metricas, top_proveedores, comparativa_anual)
        alertas = generar_alertas_compras(metricas, estado_pagos, cumplimiento_proveedores)
        
        return JsonResponse({
            'success': True,
            'metricas': metricas,
            'evolucion_mensual': evolucion_mensual,
            'top_proveedores': top_proveedores,
            'pareto_proveedores': pareto_proveedores,
            'cumplimiento_proveedores': cumplimiento_proveedores,
            'roi_temporadas': roi_temporadas,
            'comparativa_anual': comparativa_anual,
            'estado_recepciones': estado_recepciones,
            'metricas_recepcion': metricas_recepcion,
            'recepciones_pendientes': recepciones_pendientes,
            'estado_pagos': estado_pagos,
            'vencimientos': vencimientos,
            'pagos_pendientes': pagos_pendientes,
            'insights': insights,
            'alertas': alertas,
            'filtros_aplicados': {
                'anio': anio,
                'periodo': periodo,
                'proveedor_id': proveedor_id,
                'temporada': temporada
            }
        })
        
    except Exception as e:
        import traceback
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte: {str(e)}',
            'traceback': traceback.format_exc()
        }, status=500)


def calcular_metricas_compras(anio, periodo, proveedor_id, temporada):
    """
    Calcula las métricas principales del reporte de compras.
    Usa DTEs con tipo_transaccion='COMPRA' donde el EMISOR es el proveedor.
    """
    from .models import Dte, Dte_Productos, Dte_Detalle_Pago, Empresa
    
    # Query base para DTEs de compra (emisor = proveedor)
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio
    ).select_related('emisor')
    
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    # Total documentos de compra
    total_compras = dtes_query.count()
    
    # Proveedores activos (emisores únicos)
    proveedores_activos = dtes_query.values('emisor_id').distinct().count()
    
    # Inversión total (monto neto de los DTEs)
    inversion_total = dtes_query.aggregate(
        total=Sum('monto_neto')
    )['total'] or 0
    
    # Monto total con IVA
    monto_con_iva_total = dtes_query.aggregate(
        total=Sum('monto_con_iva')
    )['total'] or 0
    
    # Unidades compradas (de dte_productos)
    dtes_ids = list(dtes_query.values_list('id', flat=True))
    unidades_compradas = Dte_Productos.objects.filter(
        dte_id__in=dtes_ids
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # Estado de pagos (usar ambas variantes por consistencia del sistema)
    pagados = dtes_query.filter(
        Q(estado_pago='PAGADO') | Q(estado_pago='Pagado')
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    pendientes = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente')
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Cumplimiento = % pagado del total
    cumplimiento_general = 0
    if monto_con_iva_total > 0:
        cumplimiento_general = round((float(pagados) / float(monto_con_iva_total)) * 100, 1)
    
    # Documentos recepcionados vs emitidos
    recepcionados = dtes_query.filter(
        estado_dte__in=['ACEPTADO', 'RECEPCIONADO_COMPLETO']
    ).count()
    tasa_recepcion = round((recepcionados / total_compras) * 100, 1) if total_compras > 0 else 0
    
    # Costo promedio por unidad
    costo_promedio = 0
    if unidades_compradas > 0:
        costo_promedio = round(float(inversion_total) / unidades_compradas)
    
    # ===== TENDENCIAS vs AÑO ANTERIOR =====
    dtes_anterior = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio-1
    )
    if proveedor_id:
        dtes_anterior = dtes_anterior.filter(emisor_id=proveedor_id)
    
    total_anterior = dtes_anterior.count()
    inversion_anterior = dtes_anterior.aggregate(total=Sum('monto_neto'))['total'] or 0
    proveedores_anterior = dtes_anterior.values('emisor_id').distinct().count()
    
    dtes_ids_anterior = list(dtes_anterior.values_list('id', flat=True))
    unidades_anterior = Dte_Productos.objects.filter(
        dte_id__in=dtes_ids_anterior
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # Calcular tendencias
    tendencia_compras = calcular_porcentaje_cambio(total_compras, total_anterior)
    tendencia_inversion = calcular_porcentaje_cambio(float(inversion_total), float(inversion_anterior))
    tendencia_unidades = calcular_porcentaje_cambio(unidades_compradas, unidades_anterior)
    tendencia_proveedores = calcular_porcentaje_cambio(proveedores_activos, proveedores_anterior)
    
    return {
        'total_compras': total_compras,
        'proveedores_activos': proveedores_activos,
        'unidades_esperadas': unidades_compradas,
        'unidades_recepcionadas': recepcionados,
        'cumplimiento_general': tasa_recepcion,
        'inversion_total': float(inversion_total),
        'valor_venta_esperado': float(monto_con_iva_total),
        'margen_bruto_esperado': float(monto_con_iva_total) - float(inversion_total),
        'roi_promedio': round(((float(monto_con_iva_total) - float(inversion_total)) / float(inversion_total)) * 100, 1) if inversion_total > 0 else 0,
        'costo_promedio_unidad': float(costo_promedio),
        'pendiente_pago': float(pendientes),
        'tendencia_compras': tendencia_compras,
        'tendencia_inversion': tendencia_inversion,
        'tendencia_unidades': tendencia_unidades,
        'tendencia_proveedores': tendencia_proveedores,
        'tendencia_cumplimiento': 0,
        'tendencia_roi': 0
    }


def calcular_porcentaje_cambio(actual, anterior):
    """Calcula el porcentaje de cambio entre dos valores"""
    if anterior == 0:
        return 100 if actual > 0 else 0
    return round(((actual - anterior) / anterior) * 100, 1)


def calcular_evolucion_mensual_compras(anio, proveedor_id, temporada):
    """Calcula la evolución mensual de compras usando DTEs"""
    from .models import Dte, Dte_Productos
    
    evolucion = []
    for mes in range(1, 13):
        dtes_mes = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__year=anio,
            fecha_emision__month=mes
        )
        
        if proveedor_id:
            dtes_mes = dtes_mes.filter(emisor_id=proveedor_id)
        
        total_compras = dtes_mes.count()
        inversion = dtes_mes.aggregate(total=Sum('monto_neto'))['total'] or 0
        
        # Unidades del mes
        dtes_ids = list(dtes_mes.values_list('id', flat=True))
        unidades = Dte_Productos.objects.filter(
            dte_id__in=dtes_ids
        ).aggregate(total=Sum('stock'))['total'] or 0
        
        evolucion.append({
            'mes': mes,
            'total_compras': total_compras,
            'inversion': float(inversion),
            'unidades': unidades
        })
    
    return evolucion


def calcular_top_proveedores_compras(anio, proveedor_id, temporada):
    """Calcula el ranking de proveedores usando DTEs (emisor = proveedor)"""
    from .models import Dte, Dte_Productos, Empresa
    
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio
    ).select_related('emisor')
    
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    # Obtener proveedores únicos (emisores)
    proveedores_ids = dtes_query.values_list('emisor_id', flat=True).distinct()
    
    resultado = []
    inversion_total_general = 0
    
    for prov_id in proveedores_ids:
        if not prov_id:
            continue
            
        try:
            proveedor = Empresa.objects.get(id=prov_id)
        except Empresa.DoesNotExist:
            continue
        
        dtes_prov = dtes_query.filter(emisor_id=prov_id)
        total_compras = dtes_prov.count()
        
        # Inversión (monto neto)
        inversion = dtes_prov.aggregate(total=Sum('monto_neto'))['total'] or 0
        monto_con_iva = dtes_prov.aggregate(total=Sum('monto_con_iva'))['total'] or 0
        
        inversion_total_general += float(inversion)
        
        # Unidades
        dtes_ids = list(dtes_prov.values_list('id', flat=True))
        unidades = Dte_Productos.objects.filter(
            dte_id__in=dtes_ids
        ).aggregate(total=Sum('stock'))['total'] or 0
        
        # Cumplimiento = % documentos pagados
        pagados = dtes_prov.filter(
            Q(estado_pago='PAGADO') | Q(estado_pago='Pagado')
        ).count()
        cumplimiento = round((pagados / total_compras * 100), 1) if total_compras > 0 else 0
        
        # ROI basado en IVA (margen implícito)
        roi = round(((float(monto_con_iva) - float(inversion)) / float(inversion) * 100), 1) if inversion > 0 else 0
        
        resultado.append({
            'id': prov_id,
            'nombre': proveedor.nombre,
            'rut': proveedor.rut if hasattr(proveedor, 'rut') else '',
            'total_compras': total_compras,
            'inversion': float(inversion),
            'unidades': unidades,
            'cumplimiento': cumplimiento,
            'roi': roi
        })
    
    # Calcular participación
    for item in resultado:
        item['participacion'] = round((item['inversion'] / inversion_total_general * 100), 1) if inversion_total_general > 0 else 0
    
    # Ordenar por inversión
    resultado.sort(key=lambda x: x['inversion'], reverse=True)
    
    return resultado[:15]  # Top 15


def calcular_pareto_proveedores_compras(anio, proveedor_id, temporada):
    """Calcula el análisis Pareto de proveedores"""
    top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada)
    
    # Calcular acumulado
    total = sum(p['inversion'] for p in top_proveedores)
    acumulado = 0
    
    for proveedor in top_proveedores:
        acumulado += proveedor['inversion']
        proveedor['acumulado_pct'] = round((acumulado / total * 100), 1) if total > 0 else 0
    
    return top_proveedores[:10]


def calcular_cumplimiento_proveedores_compras(anio, proveedor_id, temporada):
    """Calcula el cumplimiento por proveedor"""
    top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada)
    
    # Ordenar por cumplimiento
    resultado = sorted(top_proveedores, key=lambda x: x['cumplimiento'], reverse=True)
    
    return resultado[:10]


def calcular_roi_temporadas_compras(anio, proveedor_id):
    """Calcula la inversión por trimestre del año (simulando temporadas)"""
    from .models import Dte
    
    # Usar trimestres como "temporadas"
    trimestres = [
        {'nombre': 'Q1 (Ene-Mar)', 'meses': [1, 2, 3]},
        {'nombre': 'Q2 (Abr-Jun)', 'meses': [4, 5, 6]},
        {'nombre': 'Q3 (Jul-Sep)', 'meses': [7, 8, 9]},
        {'nombre': 'Q4 (Oct-Dic)', 'meses': [10, 11, 12]},
    ]
    
    resultado = []
    
    for trimestre in trimestres:
        dtes_query = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__year=anio,
            fecha_emision__month__in=trimestre['meses']
        )
        
        if proveedor_id:
            dtes_query = dtes_query.filter(emisor_id=proveedor_id)
        
        inversion = dtes_query.aggregate(total=Sum('monto_neto'))['total'] or 0
        monto_con_iva = dtes_query.aggregate(total=Sum('monto_con_iva'))['total'] or 0
        
        roi = round(((float(monto_con_iva) - float(inversion)) / float(inversion) * 100), 1) if inversion > 0 else 0
        
        if inversion > 0:
            resultado.append({
                'temporada': trimestre['nombre'],
                'inversion': float(inversion),
                'valor_venta': float(monto_con_iva),
                'roi': roi
            })
    
    return resultado


def calcular_comparativa_anual_compras(anio, proveedor_id, temporada):
    """Calcula la comparativa con el año anterior usando DTEs"""
    from .models import Dte
    
    actual = []
    anterior = []
    
    for mes in range(1, 13):
        # Año actual
        dtes_actual = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__year=anio,
            fecha_emision__month=mes
        )
        if proveedor_id:
            dtes_actual = dtes_actual.filter(emisor_id=proveedor_id)
        
        inv_actual = dtes_actual.aggregate(total=Sum('monto_neto'))['total'] or 0
        actual.append(float(inv_actual))
        
        # Año anterior
        dtes_ant = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__year=anio-1,
            fecha_emision__month=mes
        )
        if proveedor_id:
            dtes_ant = dtes_ant.filter(emisor_id=proveedor_id)
        
        inv_ant = dtes_ant.aggregate(total=Sum('monto_neto'))['total'] or 0
        anterior.append(float(inv_ant))
    
    return {
        'actual': actual,
        'anterior': anterior
    }


def calcular_estado_recepciones_compras(anio, proveedor_id, temporada):
    """Calcula el estado de recepciones de DTEs de compra"""
    from .models import Dte
    
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio
    )
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    total_dtes = dtes_query.count()
    
    # Estados de DTE
    recibido_ok = dtes_query.filter(
        estado_dte__in=['ACEPTADO', 'RECEPCIONADO_COMPLETO']
    ).count()
    
    parcial = dtes_query.filter(estado_dte='RECEPCIONADO_PARCIAL').count()
    
    pendiente = dtes_query.filter(estado_dte='EMITIDO').count()
    
    con_problemas = dtes_query.filter(
        estado_dte__in=['EN_REGULARIZACION', 'RECHAZADO']
    ).count()
    
    return {
        'recibido_ok': recibido_ok,
        'parcial': parcial,
        'pendiente': pendiente,
        'con_problemas': con_problemas
    }


def calcular_metricas_recepcion_compras(anio, proveedor_id, temporada):
    """Calcula las métricas detalladas de recepción de DTEs"""
    from .models import Dte, Dte_Productos
    
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio
    )
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    total_dtes = dtes_query.count()
    dtes_ids = list(dtes_query.values_list('id', flat=True))
    
    # Unidades de los productos en DTEs
    unidades_esperadas = Dte_Productos.objects.filter(
        dte_id__in=dtes_ids
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # DTEs recepcionados
    dtes_recibidos = dtes_query.filter(
        estado_dte__in=['ACEPTADO', 'RECEPCIONADO_COMPLETO', 'RECEPCIONADO_PARCIAL']
    )
    dtes_recibidos_ids = list(dtes_recibidos.values_list('id', flat=True))
    
    unidades_recibidas = Dte_Productos.objects.filter(
        dte_id__in=dtes_recibidos_ids
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    con_problemas = dtes_query.filter(
        estado_dte__in=['EN_REGULARIZACION', 'RECHAZADO', 'RECEPCIONADO_PARCIAL']
    ).count()
    
    # Faltantes = diferencia
    faltantes = max(0, unidades_esperadas - unidades_recibidas)
    
    porcentaje = round((unidades_recibidas / unidades_esperadas * 100), 1) if unidades_esperadas > 0 else 0
    
    return {
        'unidades_esperadas': unidades_esperadas,
        'unidades_recibidas': unidades_recibidas,
        'con_problemas': con_problemas,
        'faltantes': faltantes,
        'porcentaje_cumplimiento': porcentaje
    }


def obtener_recepciones_pendientes_compras(anio, proveedor_id):
    """Obtiene los DTEs de compra pendientes de recepción o con problemas"""
    from .models import Dte, Dte_Productos
    
    # DTEs pendientes, parciales o con problemas
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio,
        estado_dte__in=['EMITIDO', 'RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION']
    ).select_related('emisor').order_by('-fecha_emision')
    
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    resultado = []
    
    for dte in dtes_query[:20]:
        # Unidades del DTE
        unidades = Dte_Productos.objects.filter(dte=dte).aggregate(total=Sum('stock'))['total'] or 0
        
        # Determinar estado
        if dte.estado_dte == 'EMITIDO':
            estado = 'PENDIENTE'
            recibidas = 0
        elif dte.estado_dte == 'RECEPCIONADO_PARCIAL':
            estado = 'PARCIAL'
            recibidas = int(unidades * 0.5)  # Estimación
        else:
            estado = 'PROBLEMA'
            recibidas = unidades
        
        resultado.append({
            'id': dte.id,
            'numero': dte.numero_documento,
            'proveedor': dte.emisor.nombre if dte.emisor else 'N/A',
            'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
            'esperadas': unidades,
            'recibidas': recibidas,
            'estado': estado
        })
    
    return resultado[:10]


def calcular_estado_pagos_compras(anio, proveedor_id, temporada):
    """Calcula el estado de pagos de DTEs de compra"""
    from .models import Dte
    
    dtes_query = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__year=anio
    )
    
    if proveedor_id:
        dtes_query = dtes_query.filter(emisor_id=proveedor_id)
    
    pagados = dtes_query.filter(
        Q(estado_pago='PAGADO') | Q(estado_pago='Pagado')
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    pendientes = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__gte=datetime.now().date()
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    vencidos = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__lt=datetime.now().date()
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Vencen esta semana
    prox_semana = datetime.now().date() + timedelta(days=7)
    vencen_semana = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__range=[datetime.now().date(), prox_semana]
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    return {
        'pagados': float(pagados),
        'pendientes': float(pendientes),
        'vencidos': float(vencidos),
        'vencen_semana': float(vencen_semana)
    }


def calcular_vencimientos_compras():
    """Calcula los vencimientos próximos agrupados por período"""
    from .models import Dte
    
    hoy = datetime.now().date()
    
    periodos = [
        {'nombre': 'Vencidos', 'desde': None, 'hasta': hoy - timedelta(days=1), 'dias': -1},
        {'nombre': 'Hoy', 'desde': hoy, 'hasta': hoy, 'dias': 0},
        {'nombre': '1-7 días', 'desde': hoy + timedelta(days=1), 'hasta': hoy + timedelta(days=7), 'dias': 3},
        {'nombre': '8-15 días', 'desde': hoy + timedelta(days=8), 'hasta': hoy + timedelta(days=15), 'dias': 11},
        {'nombre': '16-30 días', 'desde': hoy + timedelta(days=16), 'hasta': hoy + timedelta(days=30), 'dias': 23},
        {'nombre': '+30 días', 'desde': hoy + timedelta(days=31), 'hasta': None, 'dias': 45}
    ]
    
    resultado = []
    
    for periodo in periodos:
        dtes_query = Dte.objects.filter(
            tipo_transaccion='COMPRA'
        ).filter(
            Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente')
        )
        
        if periodo['desde'] and periodo['hasta']:
            dtes_query = dtes_query.filter(fecha_vencimiento__range=[periodo['desde'], periodo['hasta']])
        elif periodo['desde']:
            dtes_query = dtes_query.filter(fecha_vencimiento__gte=periodo['desde'])
        elif periodo['hasta']:
            dtes_query = dtes_query.filter(fecha_vencimiento__lte=periodo['hasta'])
        
        monto = dtes_query.aggregate(total=Sum('monto_con_iva'))['total'] or 0
        
        resultado.append({
            'periodo': periodo['nombre'],
            'monto': float(monto),
            'dias_restantes': periodo['dias']
        })
    
    return resultado


def obtener_pagos_pendientes_compras():
    """Obtiene la lista de pagos pendientes"""
    from .models import Dte
    
    hoy = datetime.now().date()
    
    dtes = Dte.objects.filter(
        tipo_transaccion='COMPRA'
    ).filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente')
    ).select_related('emisor').order_by('fecha_vencimiento')[:20]
    
    resultado = []
    
    for dte in dtes:
        dias = (dte.fecha_vencimiento - hoy).days if dte.fecha_vencimiento else 0
        
        resultado.append({
            'id': dte.id,
            'numero_dte': dte.numero_documento,
            'proveedor': dte.emisor.nombre if dte.emisor else 'N/A',
            'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
            'fecha_vencimiento': dte.fecha_vencimiento.strftime('%d/%m/%Y') if dte.fecha_vencimiento else 'N/A',
            'monto': float(dte.monto_con_iva),
            'dias_restantes': dias
        })
    
    return resultado


def generar_insights_compras(metricas, top_proveedores, comparativa):
    """Genera insights estratégicos basados en los datos"""
    insights = []
    
    # Insight sobre ROI
    if metricas['roi_promedio'] >= 50:
        insights.append({
            'tipo': 'success',
            'titulo': 'Excelente rentabilidad',
            'descripcion': f"El ROI promedio de {metricas['roi_promedio']}% supera las expectativas del negocio."
        })
    elif metricas['roi_promedio'] < 30:
        insights.append({
            'tipo': 'warning',
            'titulo': 'ROI bajo',
            'descripcion': f"El ROI promedio de {metricas['roi_promedio']}% está por debajo del objetivo. Revise los precios de compra."
        })
    
    # Insight sobre cumplimiento
    if metricas['cumplimiento_general'] >= 95:
        insights.append({
            'tipo': 'success',
            'titulo': 'Excelente cumplimiento de proveedores',
            'descripcion': f"El cumplimiento general es del {metricas['cumplimiento_general']}%, lo cual indica buenas relaciones con proveedores."
        })
    elif metricas['cumplimiento_general'] < 80:
        insights.append({
            'tipo': 'warning',
            'titulo': 'Cumplimiento bajo',
            'descripcion': f"El cumplimiento de {metricas['cumplimiento_general']}% indica problemas en las entregas. Evalúe proveedores alternativos."
        })
    
    # Insight sobre concentración de proveedores
    if top_proveedores and len(top_proveedores) >= 3:
        top3_participacion = sum(p['participacion'] for p in top_proveedores[:3])
        if top3_participacion > 70:
            insights.append({
                'tipo': 'info',
                'titulo': 'Alta concentración de proveedores',
                'descripcion': f"El {top3_participacion:.0f}% de las compras se concentra en 3 proveedores. Considere diversificar."
            })
    
    # Insight sobre tendencia
    if metricas['tendencia_inversion'] > 20:
        insights.append({
            'tipo': 'info',
            'titulo': 'Inversión en crecimiento',
            'descripcion': f"La inversión ha aumentado un {metricas['tendencia_inversion']}% respecto al año anterior."
        })
    elif metricas['tendencia_inversion'] < -20:
        insights.append({
            'tipo': 'warning',
            'titulo': 'Reducción de inversión',
            'descripcion': f"La inversión ha disminuido un {abs(metricas['tendencia_inversion'])}% respecto al año anterior."
        })
    
    return insights


def generar_alertas_compras(metricas, estado_pagos, cumplimiento_proveedores):
    """Genera alertas importantes basadas en los datos"""
    alertas = []
    
    # Alerta de pagos vencidos
    if estado_pagos['vencidos'] > 0:
        alertas.append({
            'nivel': 'error',
            'titulo': 'Pagos vencidos',
            'descripcion': f"Tiene ${estado_pagos['vencidos']:,.0f} en pagos vencidos. Gestione con urgencia."
        })
    
    # Alerta de vencimientos próximos
    if estado_pagos['vencen_semana'] > 0:
        alertas.append({
            'nivel': 'warning',
            'titulo': 'Vencimientos próximos',
            'descripcion': f"${estado_pagos['vencen_semana']:,.0f} vencen esta semana. Planifique los pagos."
        })
    
    # Alerta de cumplimiento bajo
    if cumplimiento_proveedores:
        proveedores_bajos = [p for p in cumplimiento_proveedores if p['cumplimiento'] < 70]
        if proveedores_bajos:
            alertas.append({
                'nivel': 'warning',
                'titulo': f'{len(proveedores_bajos)} proveedores con bajo cumplimiento',
                'descripcion': 'Algunos proveedores tienen cumplimiento inferior al 70%. Revise las condiciones comerciales.'
            })
    
    # Alerta de pendiente de pago alto
    if metricas['pendiente_pago'] > metricas['inversion_total'] * 0.5:
        alertas.append({
            'nivel': 'warning',
            'titulo': 'Alto nivel de deuda',
            'descripcion': 'El monto pendiente de pago representa más del 50% de la inversión total.'
        })
    
    return alertas


@login_required
@require_GET
def exportar_reporte_compras_excel(request):
    """Exporta el reporte de compras a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        # Obtener datos
        anio = int(request.GET.get('anio', datetime.now().year))
        proveedor_id = request.GET.get('proveedor', '')
        temporada = request.GET.get('temporada', '')
        
        metricas = calcular_metricas_compras(anio, 'anual', proveedor_id, temporada)
        top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada)
        evolucion = calcular_evolucion_mensual_compras(anio, proveedor_id, temporada)
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Reporte Compras {anio}"
        
        # Estilos
        header_fill = PatternFill(start_color="0066FF", end_color="0066FF", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        subheader_fill = PatternFill(start_color="E6F0FF", end_color="E6F0FF", fill_type="solid")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws.merge_cells('A1:H1')
        cell = ws['A1']
        cell.value = f"REPORTE DE COMPRAS - AÑO {anio}"
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF", size=14)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Sección KPIs
        ws['A3'] = "INDICADORES PRINCIPALES"
        ws['A3'].font = Font(bold=True, size=12)
        ws['A3'].fill = subheader_fill
        ws.merge_cells('A3:H3')
        
        kpis = [
            ('Total Compras', metricas['total_compras']),
            ('Inversión Total', f"${metricas['inversion_total']:,.0f}"),
            ('Unidades Compradas', metricas['unidades_esperadas']),
            ('Cumplimiento', f"{metricas['cumplimiento_general']}%"),
            ('ROI Promedio', f"{metricas['roi_promedio']}%"),
            ('Proveedores Activos', metricas['proveedores_activos']),
            ('Pendiente Pago', f"${metricas['pendiente_pago']:,.0f}"),
        ]
        
        fila = 4
        for idx, (nombre, valor) in enumerate(kpis):
            col = (idx % 4) * 2 + 1
            if idx > 0 and idx % 4 == 0:
                fila += 1
            ws.cell(row=fila, column=col, value=nombre).font = Font(bold=True)
            ws.cell(row=fila, column=col + 1, value=valor)
        
        fila += 3
        
        # Sección Proveedores
        ws.cell(row=fila, column=1, value="TOP PROVEEDORES")
        ws.cell(row=fila, column=1).font = Font(bold=True, size=12)
        ws.cell(row=fila, column=1).fill = subheader_fill
        ws.merge_cells(f'A{fila}:H{fila}')
        
        fila += 1
        headers_prov = ['Proveedor', 'Compras', 'Inversión', 'Unidades', 'Cumplimiento', 'ROI', 'Participación']
        for idx, header in enumerate(headers_prov, start=1):
            cell = ws.cell(row=fila, column=idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
        
        fila += 1
        for proveedor in top_proveedores:
            ws.cell(row=fila, column=1, value=proveedor['nombre']).border = border
            ws.cell(row=fila, column=2, value=proveedor['total_compras']).border = border
            ws.cell(row=fila, column=3, value=f"${proveedor['inversion']:,.0f}").border = border
            ws.cell(row=fila, column=4, value=proveedor['unidades']).border = border
            ws.cell(row=fila, column=5, value=f"{proveedor['cumplimiento']}%").border = border
            ws.cell(row=fila, column=6, value=f"{proveedor['roi']}%").border = border
            ws.cell(row=fila, column=7, value=f"{proveedor['participacion']}%").border = border
            fila += 1
        
        fila += 2
        
        # Sección Evolución Mensual
        ws.cell(row=fila, column=1, value="EVOLUCIÓN MENSUAL")
        ws.cell(row=fila, column=1).font = Font(bold=True, size=12)
        ws.cell(row=fila, column=1).fill = subheader_fill
        ws.merge_cells(f'A{fila}:H{fila}')
        
        fila += 1
        headers_evol = ['Mes', 'Compras', 'Inversión', 'Unidades']
        for idx, header in enumerate(headers_evol, start=1):
            cell = ws.cell(row=fila, column=idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
        
        meses_nombres = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 
                        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        
        fila += 1
        for item in evolucion:
            ws.cell(row=fila, column=1, value=meses_nombres[item['mes'] - 1]).border = border
            ws.cell(row=fila, column=2, value=item['total_compras']).border = border
            ws.cell(row=fila, column=3, value=f"${item['inversion']:,.0f}").border = border
            ws.cell(row=fila, column=4, value=item['unidades']).border = border
            fila += 1
        
        # Ajustar anchos
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 18
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 15
        ws.column_dimensions['F'].width = 12
        ws.column_dimensions['G'].width = 15
        ws.column_dimensions['H'].width = 15
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="reporte_compras_{anio}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}',
            'traceback': traceback.format_exc()
        }, status=500)


# ========== REPORTE DE MOVIMIENTOS POR SUCURSAL (INICIAL VS RESTANTE) ==========

@login_required
def ver_reporte_movimientos_sucursal(request):
    """Vista principal del reporte de movimientos por sucursal (inicial vs restante)"""
    sucursal_actual_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    sucursal_actual = None
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            pass
    
    # Obtener todas las sucursales del usuario
    empresas_usuario = EmpresaUser.objects.filter(
        user=request.user,
        status=True
    ).values_list('empresa_id', flat=True)
    sucursales = Sucursal.objects.filter(empresa_id__in=empresas_usuario).order_by('alias')
    
    context = {
        'sucursal_actual': sucursal_actual,
        'sucursal_actual_id': sucursal_actual_id,
        'sucursales': sucursales,
    }
    return render(request, 'vistas/modulo_reportes/reporte_movimientos_sucursal.html', context)


@require_GET
@login_required
def obtener_reporte_movimientos_sucursal(request):
    """
    API OPTIMIZADA para reporte de stock inicial (recibido) vs restante por sucursal.
    
    OPTIMIZACIÓN: Usa agregaciones en BD en lugar de queries individuales.
    Reduce de N*M queries a solo 3 queries totales.
    """
    try:
        # ========== PARÁMETROS ==========
        marca_id = request.GET.get('marca_id')
        departamento_id = request.GET.get('departamento_id')
        busqueda = request.GET.get('busqueda', '').strip()
        limite_param = request.GET.get('limite', '')
        limite = int(limite_param) if limite_param else None
        sin_filtro = request.GET.get('sin_filtro', 'false') == 'true'
        
        tiene_filtro = any([marca_id, departamento_id, busqueda])
        
        if not tiene_filtro and not sin_filtro:
            return JsonResponse({
                'success': False,
                'requiere_filtro': True,
                'error': 'Por favor selecciona al menos un filtro: Marca, Departamento o busca un artículo.',
                'sugerencia': 'Usa los filtros para optimizar la consulta.'
            })
        
        # ========== OBTENER SUCURSALES DEL USUARIO ==========
        empresas_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True
        ).values_list('empresa_id', flat=True)
        sucursales = Sucursal.objects.filter(empresa_id__in=empresas_usuario).order_by('alias')
        sucursales_list = list(sucursales)
        sucursales_ids = [s.id for s in sucursales_list]
        sucursales_map = {s.id: s.alias for s in sucursales_list}
        
        # ========== QUERY 1: PRODUCTOS BASE (con filtros) ==========
        queryset = Producto.objects.filter(
            sucursal_id__in=sucursales_ids
        ).select_related(
            'atributo1', 'atributo2', 'categoria', 'sucursal'
        ).only(
            'id', 'articulo', 'costo', 'precioventa', 'sucursal_id',
            'atributo1__id', 'atributo1__valor',
            'atributo2__id', 'atributo2__valor',
            'categoria__id', 'categoria__nombre',
            'sucursal__id', 'sucursal__alias'
        )
        
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
        
        if departamento_id:
            queryset = queryset.filter(categoria_id=departamento_id)
        
        if busqueda:
            queryset = queryset.filter(
                Q(articulo__icontains=busqueda) |
                Q(atributo1__valor__icontains=busqueda) |
                Q(atributo2__valor__icontains=busqueda)
            )
        
        queryset = queryset.order_by('atributo1__valor', 'articulo')
        
        if limite:
            queryset = queryset[:limite]
        
        # Obtener IDs de productos para las siguientes queries
        productos_list = list(queryset)
        productos_ids = [p.id for p in productos_list]
        
        if not productos_ids:
            return JsonResponse({
                'success': True,
                'datos': [],
                'sucursales': [{'id': s.id, 'alias': s.alias} for s in sucursales_list],
                'debug': {'total_productos': 0, 'total_sucursales': len(sucursales_list)}
            })
        
        # ========== QUERY 2: INGRESOS AGREGADOS POR PRODUCTO Y SUCURSAL ==========
        # Una sola query para obtener todos los ingresos
        ingresos_query = Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=productos_ids,
            sucursal_destino_id__in=sucursales_ids,
            estado='COMPLETADO'
        ).filter(
            Q(tipo_movimiento='INGRESO') | 
            Q(concepto__in=['TRASPASO_ENTRADA', 'CAMBIO_PRODUCTO_ENTRADA'])
        ).values(
            'ProductoTalla__producto_id', 'sucursal_destino_id'
        ).annotate(
            total_ingresos=Sum('cantidad')
        )
        
        # Crear mapa de ingresos: {producto_id: {sucursal_id: cantidad}}
        ingresos_map = {}
        for item in ingresos_query:
            prod_id = item['ProductoTalla__producto_id']
            suc_id = item['sucursal_destino_id']
            cantidad = item['total_ingresos'] or 0
            
            if prod_id not in ingresos_map:
                ingresos_map[prod_id] = {}
            ingresos_map[prod_id][suc_id] = cantidad
        
        # ========== QUERY 3: STOCK ACTUAL AGREGADO POR PRODUCTO Y SUCURSAL ==========
        stock_query = Producto_Talla.objects.filter(
            producto_id__in=productos_ids
        ).values(
            'producto_id', 'producto__sucursal_id'
        ).annotate(
            total_stock=Sum('stock')
        )
        
        # Crear mapa de stock: {producto_id: {sucursal_id: stock}}
        stock_map = {}
        for item in stock_query:
            prod_id = item['producto_id']
            suc_id = item['producto__sucursal_id']
            stock = item['total_stock'] or 0
            
            if prod_id not in stock_map:
                stock_map[prod_id] = {}
            stock_map[prod_id][suc_id] = stock
        
        # ========== PROCESAR RESULTADOS (sin queries adicionales) ==========
        datos_reporte = []
        
        for producto in productos_list:
            prod_id = producto.id
            
            # Obtener datos de este producto desde los mapas
            ingresos_producto = ingresos_map.get(prod_id, {})
            stock_producto = stock_map.get(prod_id, {})
            
            # Construir datos por sucursal
            stock_por_sucursal = {}
            total_inicial = 0
            total_restante = 0
            
            # Revisar todas las sucursales donde hay ingresos o stock
            sucursales_con_datos = set(ingresos_producto.keys()) | set(stock_producto.keys())
            
            for suc_id in sucursales_con_datos:
                if suc_id not in sucursales_map:
                    continue
                    
                inicial = ingresos_producto.get(suc_id, 0)
                restante = stock_producto.get(suc_id, 0)
                
                if inicial > 0 or restante > 0:
                    suc_alias = sucursales_map[suc_id]
                    stock_por_sucursal[suc_alias] = {
                        'sucursal_id': suc_id,
                        'inicial': inicial,
                        'restante': restante,
                        'vendido': max(0, inicial - restante),
                    }
                    total_inicial += inicial
                    total_restante += restante
            
            # Solo incluir productos con datos
            if stock_por_sucursal or total_inicial > 0 or total_restante > 0:
                datos_reporte.append({
                    'articulo': producto.articulo,
                    'marca': producto.atributo1.valor if producto.atributo1 else 'Sin Marca',
                    'color': producto.atributo2.valor if producto.atributo2 else '-',
                    'departamento': producto.categoria.nombre if producto.categoria else '-',
                    'costo': float(producto.costo) if producto.costo else 0,
                    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                    'sucursales': stock_por_sucursal,
                    'total_inicial': total_inicial,
                    'total_restante': total_restante,
                    'total_vendido': max(0, total_inicial - total_restante),
                })
        
        sucursales_data = [{'id': s.id, 'alias': s.alias} for s in sucursales_list]
        
        return JsonResponse({
            'success': True,
            'datos': datos_reporte,
            'sucursales': sucursales_data,
            'debug': {
                'total_productos': len(datos_reporte),
                'total_sucursales': len(sucursales_data),
                'optimizado': True
            }
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en reporte movimientos por sucursal: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener reporte: {str(e)}'
        })


@require_GET
@login_required
def exportar_movimientos_sucursal_excel(request):
    """
    Exportar reporte de movimientos por sucursal a Excel.
    OPTIMIZADO: Usa agregaciones en BD (3 queries totales).
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        # Parámetros
        marca_id = request.GET.get('marca_id')
        departamento_id = request.GET.get('departamento_id')
        busqueda = request.GET.get('busqueda', '').strip()
        limite = request.GET.get('limite')
        
        # Obtener sucursales del usuario
        empresas_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True
        ).values_list('empresa_id', flat=True)
        sucursales = Sucursal.objects.filter(empresa_id__in=empresas_usuario).order_by('alias')
        sucursales_list = list(sucursales)
        sucursales_ids = [s.id for s in sucursales_list]
        sucursales_map = {s.id: s.alias for s in sucursales_list}
        
        # QUERY 1: Productos base
        queryset = Producto.objects.filter(
            sucursal_id__in=sucursales_ids
        ).select_related(
            'atributo1', 'atributo2', 'categoria', 'sucursal'
        ).only(
            'id', 'articulo', 'costo', 'precioventa', 'sucursal_id',
            'atributo1__id', 'atributo1__valor',
            'atributo2__id', 'atributo2__valor',
            'categoria__id', 'categoria__nombre',
            'sucursal__id', 'sucursal__alias'
        )
        
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
        
        if departamento_id:
            queryset = queryset.filter(categoria_id=departamento_id)
        
        if busqueda:
            queryset = queryset.filter(
                Q(articulo__icontains=busqueda) |
                Q(atributo1__valor__icontains=busqueda) |
                Q(atributo2__valor__icontains=busqueda)
            )
        
        queryset = queryset.order_by('atributo1__valor', 'articulo')
        
        if limite:
            queryset = queryset[:int(limite)]
        
        productos_list = list(queryset)
        productos_ids = [p.id for p in productos_list]
        
        # QUERY 2: Ingresos agregados
        ingresos_map = {}
        if productos_ids:
            ingresos_query = Movimientos_Producto.objects.filter(
                ProductoTalla__producto_id__in=productos_ids,
                sucursal_destino_id__in=sucursales_ids,
                estado='COMPLETADO'
            ).filter(
                Q(tipo_movimiento='INGRESO') | 
                Q(concepto__in=['TRASPASO_ENTRADA', 'CAMBIO_PRODUCTO_ENTRADA'])
            ).values(
                'ProductoTalla__producto_id', 'sucursal_destino_id'
            ).annotate(
                total_ingresos=Sum('cantidad')
            )
            
            for item in ingresos_query:
                prod_id = item['ProductoTalla__producto_id']
                suc_id = item['sucursal_destino_id']
                if prod_id not in ingresos_map:
                    ingresos_map[prod_id] = {}
                ingresos_map[prod_id][suc_id] = item['total_ingresos'] or 0
        
        # QUERY 3: Stock actual agregado
        stock_map = {}
        if productos_ids:
            stock_query = Producto_Talla.objects.filter(
                producto_id__in=productos_ids
            ).values(
                'producto_id', 'producto__sucursal_id'
            ).annotate(
                total_stock=Sum('stock')
            )
            
            for item in stock_query:
                prod_id = item['producto_id']
                suc_id = item['producto__sucursal_id']
                if prod_id not in stock_map:
                    stock_map[prod_id] = {}
                stock_map[prod_id][suc_id] = item['total_stock'] or 0
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Inicial vs Restante"
        
        # Estilos
        header_fill = PatternFill(start_color="1A1A2E", end_color="1A1A2E", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=10)
        inicial_fill = PatternFill(start_color="E8F8F5", end_color="E8F8F5", fill_type="solid")
        restante_fill = PatternFill(start_color="FFF9E6", end_color="FFF9E6", fill_type="solid")
        total_fill = PatternFill(start_color="FFE6E6", end_color="FFE6E6", fill_type="solid")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws.merge_cells('A1:F1')
        ws['A1'] = "REPORTE: STOCK INICIAL VS RESTANTE POR SUCURSAL"
        ws['A1'].font = Font(bold=True, color="FFFFFF", size=14)
        ws['A1'].fill = header_fill
        
        # Info
        ws['A2'] = f"Generado: {timezone.now().strftime('%d/%m/%Y %H:%M')} | {len(productos_list)} productos"
        ws['A2'].font = Font(italic=True, size=9)
        
        # Headers fila 4
        row = 4
        headers = ['Artículo', 'Marca', 'Color', 'Departamento']
        
        sucursales_nombres = [s.alias for s in sucursales_list]
        for suc in sucursales_nombres:
            headers.extend([f'{suc} Recib.', f'{suc} Rest.'])
        
        headers.extend(['TOTAL Recib.', 'TOTAL Rest.', 'VENDIDO'])
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # Datos (sin queries adicionales)
        row = 5
        for producto in productos_list:
            prod_id = producto.id
            ingresos_prod = ingresos_map.get(prod_id, {})
            stock_prod = stock_map.get(prod_id, {})
            
            # Calcular totales
            total_inicial = sum(ingresos_prod.values())
            total_restante = sum(stock_prod.values())
            
            if total_inicial > 0 or total_restante > 0:
                col = 1
                
                ws.cell(row=row, column=col, value=producto.articulo).border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.atributo1.valor if producto.atributo1 else '-').border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.atributo2.valor if producto.atributo2 else '-').border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.categoria.nombre if producto.categoria else '-').border = border
                col += 1
                
                # Datos por sucursal (desde los mapas, sin queries)
                for suc in sucursales_list:
                    suc_inicial = ingresos_prod.get(suc.id, 0)
                    suc_restante = stock_prod.get(suc.id, 0)
                    
                    cell_inicial = ws.cell(row=row, column=col, value=suc_inicial)
                    cell_inicial.border = border
                    cell_inicial.fill = inicial_fill
                    cell_inicial.alignment = Alignment(horizontal='center')
                    col += 1
                    
                    cell_restante = ws.cell(row=row, column=col, value=suc_restante)
                    cell_restante.border = border
                    cell_restante.fill = restante_fill
                    cell_restante.alignment = Alignment(horizontal='center')
                    col += 1
                
                # Totales
                cell = ws.cell(row=row, column=col, value=total_inicial)
                cell.border = border
                cell.fill = total_fill
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center')
                col += 1
                
                cell = ws.cell(row=row, column=col, value=total_restante)
                cell.border = border
                cell.fill = total_fill
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center')
                col += 1
                
                cell = ws.cell(row=row, column=col, value=max(0, total_inicial - total_restante))
                cell.border = border
                cell.fill = total_fill
                cell.font = Font(bold=True, color="CC0000")
                cell.alignment = Alignment(horizontal='center')
                
                row += 1
        
        # Ajustar anchos
        ws.column_dimensions['A'].width = 35
        ws.column_dimensions['B'].width = 18
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 18
        
        for col in range(5, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 10
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        fecha_str = timezone.now().strftime('%Y%m%d_%H%M')
        response['Content-Disposition'] = f'attachment; filename="stock_inicial_restante_{fecha_str}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        print(f"❌ Error exportando Excel: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        }, status=500)