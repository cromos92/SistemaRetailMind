"""
Módulo de Reportes - RetailMind
Contiene todas las vistas relacionadas con reportes, dashboards estratégicos y análisis de datos
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg, Max, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncMonth, TruncWeek, TruncDate
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import re
import logging
from collections import defaultdict
from decimal import Decimal
from datetime import datetime, timedelta

from .models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Productos_Recepcionados,
    Dte, Dte_Productos, Dte_Detalle_Pago, Ticket, Ticket_Productos,
    Producto, Producto_Talla, Movimientos_Producto, Sucursal, EmpresaUser,
    Empresa, Vendedor, LoteProducto, Traspaso, AjusteInventario,
    TicketDetallePago, METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES,
    Categoria, AtributoOpcion, Productos_Atributos,
    PermisoRol, PedidoEcommerce, CANAL_ECOMMERCE_CHOICES,
)
from .utils_permisos import (
    obtener_sucursales_usuario,
    puede_ver_sucursal,
    filtrar_queryset_por_sucursal,
    usuario_puede_ver_todas_sucursales,
    obtener_contexto_sucursales,
)

logger = logging.getLogger('app')


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
            fecha_fin = timezone.localdate()
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
        
        # Detalles por documento, enriched with reception data
        detalles_documentos = []
        for dte in queryset.order_by('-fecha_emision'):
            recepciones = Productos_Recepcionados.objects.filter(dte=dte)
            total_recibido = recepciones.aggregate(t=Sum('stockArribado'))['t'] or 0
            total_esperado = Dte_Productos.objects.filter(dte=dte).aggregate(t=Sum('cantidad'))['t'] or 0
            reposicion_count = recepciones.filter(es_reposicion=True).count()
            nuevo_count = recepciones.filter(es_reposicion=False).count()

            sucursales = list(
                recepciones.filter(sucursal_destino__isnull=False)
                .values_list('sucursal_destino__alias', flat=True)
                .distinct()
            )

            detalles_documentos.append({
                'numero_dte': dte.numero_dte,
                'tipo_documento': dte.tipo_documento,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
                'proveedor': dte.emisor.nombre,
                'subtotal': float(dte.subtotal),
                'iva': float(dte.iva),
                'total': float(dte.total),
                'estado': dte.estado_dte,
                'unidades_esperadas': total_esperado,
                'unidades_recibidas': total_recibido,
                'porcentaje_recepcion': round(total_recibido / total_esperado * 100, 1) if total_esperado > 0 else 0,
                'items_reposicion': reposicion_count,
                'items_nuevo': nuevo_count,
                'sucursales_destino': sucursales,
            })
        
        total_docs = sum(item['total_documentos'] for item in despachos_por_proveedor)
        total_monto = sum(item['monto_total'] for item in despachos_por_proveedor)
        resumen = {
            'total_proveedores': len(despachos_por_proveedor),
            'total_documentos': total_docs,
            'monto_total_periodo': total_monto,
            'monto_promedio_documento': total_monto / max(total_docs, 1),
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
            esProveedor=True,
            activo=True,
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
            fecha_fin = timezone.localdate()
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
        fecha_inicio  = request.GET.get('fecha_inicio')
        fecha_fin     = request.GET.get('fecha_fin')
        sucursal_id   = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id  = request.GET.get('categoria_id')
        limite        = int(request.GET.get('limite', 20))

        if not fecha_inicio or not fecha_fin:
            fecha_fin    = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin    = datetime.strptime(fecha_fin,    '%Y-%m-%d').date()

        # Ticket_Productos: FK es idTicket (no ticket), cantidad es stock, precio es precio
        queryset = Ticket_Productos.objects.filter(
            idTicket__fecha__range=[fecha_inicio, fecha_fin],
            idTicket__estado='PAGADO'
        ).select_related(
            'ProductoTalla__producto',
            'ProductoTalla__producto__categoria',
            'ProductoTalla__producto__atributo1',
        )

        if sucursal_id:
            queryset = queryset.filter(idTicket__sucursal_id=sucursal_id)

        if categoria_id:
            queryset = queryset.filter(ProductoTalla__producto__categoria_id=categoria_id)

        productos_vendidos = queryset.values(
            'ProductoTalla__producto__articulo',
            'ProductoTalla__sku',
            'ProductoTalla__producto__categoria__nombre',
            'ProductoTalla__producto__atributo1__valor',
        ).annotate(
            cantidad_vendida=Sum('stock'),
            ingresos_totales=Sum(F('stock') * F('precio')),
            tickets_count=Count('idTicket', distinct=True),
            precio_promedio=Avg('precio'),
        ).order_by('-cantidad_vendida')[:limite]

        productos_data = []
        for item in productos_vendidos:
            productos_data.append({
                'producto':        item['ProductoTalla__producto__articulo'],
                'sku':             item['ProductoTalla__sku'],
                'categoria':       item['ProductoTalla__producto__categoria__nombre'] or '',
                'marca':           item['ProductoTalla__producto__atributo1__valor'] or '',
                'cantidad_vendida': item['cantidad_vendida'],
                'ingresos_totales': float(item['ingresos_totales'] or 0),
                'tickets_count':   item['tickets_count'],
                'precio_promedio': float(item['precio_promedio'] or 0),
            })

        top_categorias = queryset.values(
            'ProductoTalla__producto__categoria__nombre'
        ).annotate(
            cantidad_vendida=Sum('stock'),
            ingresos_totales=Sum(F('stock') * F('precio')),
        ).order_by('-cantidad_vendida')[:5]

        return JsonResponse({
            'success': True,
            'productos_mas_vendidos': productos_data,
            'top_categorias': [
                {
                    'categoria':      item['ProductoTalla__producto__categoria__nombre'] or 'Sin categoría',
                    'cantidad_vendida': item['cantidad_vendida'],
                    'ingresos_totales': float(item['ingresos_totales'] or 0),
                }
                for item in top_categorias
            ],
            'parametros': {
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin':    fecha_fin.strftime('%d/%m/%Y'),
                'limite':       limite,
            }
        })

    except Exception as e:
        logger.exception("Error al generar reporte de productos")
        return JsonResponse({'success': False, 'error': f'Error al generar reporte de productos: {str(e)}'})


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
            fecha_fin = timezone.localdate()
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
    """Reporte de valoración de inventario por sucursal activa"""
    try:
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')
        marca_id = request.GET.get('marca_id')

        # Base: productos de la sucursal activa (un Producto = una sucursal)
        queryset = Producto.objects.select_related(
            'categoria', 'atributo1', 'atributo2', 'sucursal'
        ).prefetch_related('producto_talla')

        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)

        if categoria_id:
            queryset = queryset.filter(categoria_id=categoria_id)

        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)

        inventario_data = []
        valor_total_inventario = 0
        stock_total_general = 0

        resumen_categorias = {}

        for producto in queryset:
            for talla in producto.producto_talla.all():
                stock_actual = talla.stock or 0
                if stock_actual <= 0:
                    continue

                costo_unitario = producto.costo or 0
                precio_venta   = producto.precioventa or 0
                valor_linea    = stock_actual * costo_unitario

                valor_total_inventario += valor_linea
                stock_total_general    += stock_actual

                margen_unitario = precio_venta - costo_unitario
                margen_pct = round((margen_unitario / precio_venta) * 100, 1) if precio_venta > 0 else 0

                inventario_data.append({
                    'sku':             talla.sku,
                    'articulo':        producto.articulo,
                    'descripcion':     producto.descripcion or '-',
                    'categoria':       producto.categoria.nombre if producto.categoria else '-',
                    'marca':           producto.atributo1.valor if producto.atributo1 else '-',
                    'color':           producto.atributo2.valor if producto.atributo2 else '-',
                    'talla':           talla.talla or '-',
                    'sucursal':        producto.sucursal.alias if producto.sucursal else '-',
                    'stock_actual':    stock_actual,
                    'costo_unitario':  float(costo_unitario),
                    'precio_venta':    float(precio_venta),
                    'valor_inventario': float(valor_linea),
                    'margen_unitario': float(margen_unitario),
                    'margen_porcentaje': margen_pct,
                })

                cat = producto.categoria.nombre if producto.categoria else 'Sin categoría'
                if cat not in resumen_categorias:
                    resumen_categorias[cat] = {'productos_count': 0, 'stock_total': 0, 'valor_total': 0}
                resumen_categorias[cat]['productos_count'] += 1
                resumen_categorias[cat]['stock_total']     += stock_actual
                resumen_categorias[cat]['valor_total']     += valor_linea

        inventario_data.sort(key=lambda x: x['valor_inventario'], reverse=True)

        return JsonResponse({
            'success': True,
            'inventario': inventario_data,
            'resumen': {
                'total_productos':       len(inventario_data),
                'valor_total_inventario': float(valor_total_inventario),
                'stock_total':           stock_total_general,
                'costo_promedio_general': float(valor_total_inventario / stock_total_general) if stock_total_general > 0 else 0,
            },
            'resumen_categorias': [
                {
                    'categoria':       cat,
                    'productos_count': data['productos_count'],
                    'stock_total':     data['stock_total'],
                    'valor_total':     float(data['valor_total']),
                    'porcentaje_valor': round((data['valor_total'] / valor_total_inventario) * 100, 1) if valor_total_inventario > 0 else 0,
                }
                for cat, data in resumen_categorias.items()
            ],
            'parametros': {
                'fecha_reporte': timezone.now().strftime('%d/%m/%Y %H:%M'),
                'sucursal_id':   sucursal_id,
            }
        })

    except Exception as e:
        logger.exception("Error al generar reporte de valoracion de inventario")
        return JsonResponse({'success': False, 'error': f'Error al generar reporte de valoración: {str(e)}'})


@require_GET
@login_required
def reporte_rotacion_inventario(request):
    """Reporte de rotación de inventario por sucursal"""
    try:
        periodo_dias = int(request.GET.get('periodo_dias', 90))
        sucursal_id  = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')

        fecha_inicio = timezone.localdate() - timedelta(days=periodo_dias)

        # Base: productos de la sucursal activa
        queryset = Producto.objects.select_related(
            'categoria', 'atributo1', 'sucursal'
        ).prefetch_related('producto_talla')

        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)

        if categoria_id:
            queryset = queryset.filter(categoria_id=categoria_id)

        # Pre-cargar ventas del período para todos los productos de una vez
        producto_ids = list(queryset.values_list('id', flat=True))

        # Ventas desde Dte_Productos (más completo que Ticket_Productos para este rango)
        from django.db.models import Sum as DSum
        ventas_map = {}
        if producto_ids:
            ventas_qs = Dte_Productos.objects.filter(
                productoTalla__producto_id__in=producto_ids,
                dte__fecha_emision__gte=fecha_inicio,
                dte__tipo_documento__in=[
                    'BOLETA ELECTRONICA', 'BOLETA PAPEL',
                    'FACTURA ELECTRONICA', 'FACTURA EXENTA'
                ],
                activo=True,
            )
            if sucursal_id:
                ventas_qs = ventas_qs.filter(dte__sucursal_id=sucursal_id)

            ventas_agg = ventas_qs.values(
                'productoTalla__producto_id'
            ).annotate(total_vendido=DSum('stock'))

            ventas_map = {
                v['productoTalla__producto_id']: v['total_vendido'] or 0
                for v in ventas_agg
            }

        def clasificar_rotacion(rot):
            if rot >= 2.0: return 'ALTA'
            if rot >= 0.5: return 'MEDIA'
            return 'BAJA'

        rotacion_data = []

        for producto in queryset:
            stock_actual = sum(t.stock or 0 for t in producto.producto_talla.all())
            if stock_actual <= 0:
                continue

            cantidad_vendida = ventas_map.get(producto.id, 0)
            if cantidad_vendida <= 0:
                continue

            rotacion      = round(cantidad_vendida / stock_actual, 2)
            dias_inventario = round(periodo_dias / rotacion, 1) if rotacion > 0 else None

            rotacion_data.append({
                'articulo':        producto.articulo,
                'descripcion':     producto.descripcion or '-',
                'categoria':       producto.categoria.nombre if producto.categoria else '-',
                'marca':           producto.atributo1.valor if producto.atributo1 else '-',
                'sucursal':        producto.sucursal.alias if producto.sucursal else '-',
                'stock_actual':    stock_actual,
                'cantidad_vendida': cantidad_vendida,
                'rotacion':        rotacion,
                'dias_inventario': dias_inventario if dias_inventario is not None else 'Sin rotación',
                'clasificacion':   clasificar_rotacion(rotacion),
            })

        rotacion_data.sort(key=lambda x: x['rotacion'], reverse=True)

        rotaciones_validas = [r for r in rotacion_data if isinstance(r['dias_inventario'], float)]

        resumen = {
            'productos_analizados':    len(rotacion_data),
            'productos_con_rotacion':  len(rotaciones_validas),
            'rotacion_promedio':       round(sum(r['rotacion'] for r in rotacion_data) / len(rotacion_data), 2) if rotacion_data else 0,
            'dias_inventario_promedio': round(sum(r['dias_inventario'] for r in rotaciones_validas) / len(rotaciones_validas), 1) if rotaciones_validas else 0,
            'productos_alta_rotacion': len([r for r in rotacion_data if r['clasificacion'] == 'ALTA']),
            'productos_baja_rotacion': len([r for r in rotacion_data if r['clasificacion'] == 'BAJA']),
        }

        return JsonResponse({
            'success': True,
            'rotacion_inventario': rotacion_data,
            'resumen': resumen,
            'parametros': {
                'periodo_dias':  periodo_dias,
                'fecha_inicio':  fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin':     timezone.now().strftime('%d/%m/%Y'),
                'sucursal_id':   sucursal_id,
            }
        })

    except Exception as e:
        logger.exception("Error al generar reporte de rotacion")
        return JsonResponse({'success': False, 'error': f'Error al generar reporte de rotación: {str(e)}'})


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
        fecha_fin = timezone.localdate()
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
    """Vista principal del reporte de ventas mensual por vendedor y sucursal.

    Adicionalmente expone los flags `puede_ver_reporte_comisiones` y
    `puede_exportar_reporte_comisiones` para que el template muestre /
    oculte el botón "Comisiones" y los controles del modal según el
    permiso `reporte_comisiones_vendedor`.
    """
    context = obtener_contexto_sucursales(request.user, request)
    sucursal_id_sesion = _sucursal_id_sesion(request)
    context['puede_ver_reporte_comisiones'] = _puede_ver_reporte_comisiones(
        request.user, sucursal_id=sucursal_id_sesion,
    )
    context['puede_exportar_reporte_comisiones'] = _puede_exportar_reporte_comisiones(
        request.user, sucursal_id=sucursal_id_sesion,
    )
    return render(request, 'vistas/modulo_reportes/reporte_ventas_sucursal.html', context)


@require_GET
@login_required
def obtener_ventas_por_vendedor_reporte(request):
    """API para obtener datos de ventas por vendedor.

    Mismos filtros que ``obtener_ventas_por_sucursal_reporte``: cuenta
    toda venta emitida (incluso ANULADO; el reporte es facturación
    histórica, no caja). Las NC restan independiente de modalidad.
    """
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')
        fecha = request.GET.get('fecha')
        fecha_inicio_param = request.GET.get('fecha_inicio')
        fecha_fin_param = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        vendedor_id = request.GET.get('vendedor_id')

        # Determinar rango de fechas (default: mes actual en TZ Chile).
        if fecha:
            fecha_inicio = datetime.strptime(fecha, '%Y-%m-%d')
            fecha_fin = fecha_inicio
        elif fecha_inicio_param and fecha_fin_param:
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            if not mes:
                mes = timezone.localdate().strftime('%Y-%m')
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)

        fi = fecha_inicio.date() if hasattr(fecha_inicio, 'date') else fecha_inicio
        ff = fecha_fin.date() if hasattr(fecha_fin, 'date') else fecha_fin

        # ========== DTEs (facturación histórica) ==========
        # Acepta ANULADO; las NC se restan independiente de modalidad
        # (DEVOLUCION o ANULACION) — ver docstring del endpoint y de
        # `obtener_ventas_por_sucursal_reporte` para el racional.
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fi,
            fecha_emision__lte=ff,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
            estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'],
            descartado=False,
        ).select_related('vendedor', 'sucursal')

        queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)

        if vendedor_id:
            queryset_dtes = queryset_dtes.filter(vendedor_id=vendedor_id)

        queryset_ventas = queryset_dtes.exclude(tipo_documento='NOTA DE CREDITO')
        # TODAS las NC restan (ambas modalidades anulan facturación).
        queryset_ncs = queryset_dtes.filter(tipo_documento='NOTA DE CREDITO')
        queryset_ncs_anulacion = queryset_ncs.filter(tipo_transaccion='ANULACION')

        # Ventas por vendedor
        ventas_por_vend = queryset_ventas.values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor',
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            total_descuentos=Sum('descuento'),
            total_documentos=Count('id'),
        )

        # Devoluciones (NC DEVOLUCION) por vendedor — restan del total.
        # Incluimos también las NC sin vendedor asignado (vendedor__id=None)
        # para que el row "Sin vendedor" refleje el neto correcto.
        ncs_por_vend = {
            r['vendedor__id']: {
                'total': int(r['total'] or 0),
                'cantidad': int(r['cant'] or 0),
            }
            for r in queryset_ncs.values('vendedor__id').annotate(
                total=Sum('monto_con_iva'),
                cant=Count('id'),
            )
        }

        # NC ANULACION (informativas) por vendedor — solo conteo, no restan.
        ncs_anul_por_vend = {
            r['vendedor__id']: {
                'total': int(r['total'] or 0),
                'cantidad': int(r['cant'] or 0),
            }
            for r in queryset_ncs_anulacion.values('vendedor__id').annotate(
                total=Sum('monto_con_iva'),
                cant=Count('id'),
            )
        }

        # Consolidar.
        # Usamos `None` como key para los DTEs sin vendedor asignado para
        # que el total de la tabla calce con el KPI "Total Ventas" del
        # reporte. Antes eso se descartaba con `if not vid: continue`,
        # creando un gap que confundía al usuario (veía $X arriba y la
        # tabla sumaba $X − sin_vendedor).
        ventas_acumuladas = {}
        for item in ventas_por_vend:
            vid = item['vendedor__id']  # puede ser None
            nc = ncs_por_vend.get(vid, {'total': 0, 'cantidad': 0})
            nc_anul = ncs_anul_por_vend.get(vid, {'total': 0, 'cantidad': 0})
            es_sin_vendedor = (not vid)
            ventas_acumuladas[vid] = {
                'nombre': item['vendedor__nombre'] if not es_sin_vendedor else 'Sin vendedor asignado',
                'codigo': item['vendedor__codigo_vendedor'] if not es_sin_vendedor else '',
                'ventas_brutas': int(item['total_ventas'] or 0),
                'descuentos': int(item['total_descuentos'] or 0),
                'documentos': int(item['total_documentos'] or 0),
                'devoluciones': nc['total'],
                'cantidad_devoluciones': nc['cantidad'],
                'nc_anulacion_total': nc_anul['total'],
                'cantidad_nc_anulacion': nc_anul['cantidad'],
                'sin_vendedor': es_sin_vendedor,
            }

        # Si hay NCs sin vendedor pero ningún DTE de venta sin vendedor,
        # igual mostramos la fila para que el total cierre correctamente.
        if None in ncs_por_vend and None not in ventas_acumuladas:
            nc = ncs_por_vend[None]
            nc_anul = ncs_anul_por_vend.get(None, {'total': 0, 'cantidad': 0})
            ventas_acumuladas[None] = {
                'nombre': 'Sin vendedor asignado',
                'codigo': '',
                'ventas_brutas': 0,
                'descuentos': 0,
                'documentos': 0,
                'devoluciones': nc['total'],
                'cantidad_devoluciones': nc['cantidad'],
                'nc_anulacion_total': nc_anul['total'],
                'cantidad_nc_anulacion': nc_anul['cantidad'],
                'sin_vendedor': True,
            }

        # Total general (ventas netas = brutas − devoluciones)
        total_general = sum(
            v['ventas_brutas'] - v['devoluciones']
            for v in ventas_acumuladas.values()
        )

        # Armar salida
        vendedores_data = []
        for vid, data in sorted(
            ventas_acumuladas.items(),
            key=lambda x: x[1]['ventas_brutas'] - x[1]['devoluciones'],
            reverse=True,
        ):
            ventas_netas = data['ventas_brutas'] - data['devoluciones']
            participacion = (ventas_netas / total_general * 100) if total_general > 0 else 0
            vendedores_data.append({
                'id': vid,
                'nombre': data['nombre'],
                'codigo': data['codigo'],
                'ventas': ventas_netas,
                'ventas_brutas': data['ventas_brutas'],
                'descuentos': data['descuentos'],
                'devoluciones': data['devoluciones'],
                'cantidad_devoluciones': data['cantidad_devoluciones'],
                # NC informativas (ANULACION) — no afectan ventas, solo conteo.
                'nc_anulacion_total': int(data.get('nc_anulacion_total', 0)),
                'cantidad_nc_anulacion': int(data.get('cantidad_nc_anulacion', 0)),
                'documentos': data['documentos'],
                'participacion': round(participacion, 1),
                # Marca para que el frontend pinte la fila distinto y NO
                # ofrezca el botón "ver documentos" (no hay vendedor_id).
                'sin_vendedor': bool(data.get('sin_vendedor', False)),
            })

        # KPIs.
        # `top_vendedor` ignora la fila virtual "Sin vendedor" para que
        # nunca aparezca como ranking #1.
        total_documentos = sum(v['documentos'] for v in ventas_acumuladas.values())
        ticket_promedio = total_general / total_documentos if total_documentos > 0 else 0
        top_vendedor = next(
            (v['nombre'] for v in vendedores_data if not v.get('sin_vendedor')),
            '-',
        )
        total_devoluciones_general = sum(
            v['devoluciones'] for v in ventas_acumuladas.values()
        )

        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data,
            'kpis': {
                'total_ventas': int(total_general),
                'total_ventas_brutas': int(total_general + total_devoluciones_general),
                'total_devoluciones': int(total_devoluciones_general),
                'total_documentos': total_documentos,
                'ticket_promedio': int(ticket_promedio),
                'top_vendedor': top_vendedor,
            }
        })
        
    except Exception as e:
        logger.exception("Error al obtener ventas por vendedor")
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por vendedor: {str(e)}'
        })


# ========== HELPERS COMUNES PARA REPORTES DE COMISIONES ==========

# Código del `OpcionMenu` que protege el reporte de comisiones por
# vendedor. Configurable por rol/sucursal desde la pantalla de gestión
# de permisos. Lo crea la migración 0150_permiso_reporte_comisiones_vendedor.
CODIGO_PERMISO_COMISIONES = 'reporte_comisiones_vendedor'


def _puede_ver_reporte_comisiones(user, sucursal_id=None) -> bool:
    """Atajo para chequear el permiso del reporte de comisiones.

    El permiso se verifica con `puede_ver`, que es la convención del
    resto de reportes del módulo (`reporte_ventas_sucursal`,
    `reporte_existencias`, etc.). Si el usuario no está autenticado
    devuelve False sin tocar la BD.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return PermisoRol.tiene_permiso(
        user, CODIGO_PERMISO_COMISIONES, 'puede_ver',
        sucursal_id=sucursal_id,
    )


def _puede_exportar_reporte_comisiones(user, sucursal_id=None) -> bool:
    """Chequea `puede_exportar` para el endpoint de Excel.

    Si el rol no tiene `puede_exportar=True` pero sí `puede_ver`, podrá
    consultar el reporte en pantalla pero no descargarlo. La migración
    inicial otorga `puede_ver=puede_exportar=True` al rol administrador.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return PermisoRol.tiene_permiso(
        user, CODIGO_PERMISO_COMISIONES, 'puede_exportar',
        sucursal_id=sucursal_id,
    )


def _sucursal_id_sesion(request):
    """Sucursal activa en la sesión (None si no hay).

    Se usa para que `PermisoRol.tiene_permiso` evalúe también el override
    por sucursal (`PermisoSucursal`). Replica la convención del resto
    del proyecto (idSucursalActual / sucursalActual).
    """
    return (
        request.session.get('idSucursalActual')
        or request.session.get('sucursalActual')
    )


def _parse_rango_fechas_reporte(request):
    """Calcula `(fecha_inicio, fecha_fin)` (date) según los parámetros GET.

    Soporta los mismos modos que el resto del reporte de ventas-sucursal:
      * `fecha=YYYY-MM-DD` → un día puntual.
      * `fecha_inicio=...&fecha_fin=...` → rango.
      * `mes=YYYY-MM` → mes completo (default: mes actual en TZ Chile).

    Devuelve un tuple `(fecha_inicio, fecha_fin)` con `datetime.date`.
    """
    mes = request.GET.get('mes')
    fecha = request.GET.get('fecha')
    fecha_inicio_param = request.GET.get('fecha_inicio')
    fecha_fin_param = request.GET.get('fecha_fin')

    if fecha:
        fi = datetime.strptime(fecha, '%Y-%m-%d').date()
        ff = fi
    elif fecha_inicio_param and fecha_fin_param:
        fi = datetime.strptime(fecha_inicio_param, '%Y-%m-%d').date()
        ff = datetime.strptime(fecha_fin_param, '%Y-%m-%d').date()
    else:
        if not mes:
            # Mes actual en zona horaria Chile (regla `timezone-chile`).
            mes = timezone.localdate().strftime('%Y-%m')
        primer_dia = datetime.strptime(mes, '%Y-%m').replace(day=1).date()
        if primer_dia.month == 12:
            ultimo_dia = primer_dia.replace(
                year=primer_dia.year + 1, month=1, day=1
            ) - timedelta(days=1)
        else:
            ultimo_dia = primer_dia.replace(
                month=primer_dia.month + 1, day=1
            ) - timedelta(days=1)
        fi = primer_dia
        ff = ultimo_dia
    return fi, ff


def _calcular_comisiones_vendedor(request):
    """Calcula la matriz de comisiones por vendedor para los filtros pedidos.

    Reusa los mismos filtros que `obtener_ventas_por_vendedor_reporte`
    (facturación histórica: incluye ``ANULADO``, todas las NC restan
    independiente de modalidad). La "venta neta" se define como la suma
    de `monto_neto` (sin IVA) de los DTEs de venta menos la suma de
    `monto_neto` de las Notas de Crédito asociadas al mismo vendedor
    en la misma sucursal y período.

    La comisión se calcula como `venta_neta_sin_iva * (Vendedor.comision / 100)`.

    El resultado se agrupa por **(vendedor, empresa emisora, sucursal)**: si
    un vendedor operó para más de una empresa o en más de una sucursal en el
    período aparece una fila por cada combinación, para que la liquidación
    contable pueda hacerse por razón social y por local.

    Las NC que salen en una sucursal distinta a la de la venta original se
    imputan a la sucursal donde efectivamente se emitió la NC (la cardinalidad
    natural del agregado SQL).

    Devuelve un dict con::

        {
          'empresas': [
            {
              'id': 1,
              'nombre': 'Empresa A',
              'rut': '76.123.456-7',
              'sucursales': [
                {
                  'id': 10,
                  'alias': 'Centro',
                  'direccion': 'Av. ...',
                  'vendedores': [<dict por vendedor en esta sucursal>],
                  'subtotales': {<mismas keys que totales>},
                },
                ...
              ],
              'subtotales': {<suma de las sucursales + cantidad_sucursales>},
            },
            ...
          ],
          'vendedores': [...],   # flat (1 por trío vendedor-empresa-sucursal)
          'fecha_inicio': 'YYYY-MM-DD',
          'fecha_fin': 'YYYY-MM-DD',
          'sucursal_id': str | None,
          'vendedor_id': str | None,
          'totales': {<global, incluye cantidad_sucursales>},
        }
    """
    fi, ff = _parse_rango_fechas_reporte(request)
    sucursal_id = request.GET.get('sucursal_id')
    vendedor_id = request.GET.get('vendedor_id')

    # Mismos filtros que los reportes de ventas-sucursal/-vendedor:
    # facturación histórica (ANULADO entra, todas las NC restan).
    queryset_dtes = Dte.objects.filter(
        fecha_emision__gte=fi,
        fecha_emision__lte=ff,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
        estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'],
        descartado=False,
    ).select_related('vendedor', 'sucursal', 'emisor')

    # Respetar permisos de sucursal del usuario y filtro explícito si vino.
    queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)
    if sucursal_id:
        queryset_dtes = queryset_dtes.filter(sucursal_id=sucursal_id)
    if vendedor_id:
        queryset_dtes = queryset_dtes.filter(vendedor_id=vendedor_id)

    queryset_ventas = queryset_dtes.exclude(tipo_documento='NOTA DE CREDITO')
    queryset_ncs = queryset_dtes.filter(tipo_documento='NOTA DE CREDITO')

    # Agrupación triple (vendedor, emisor=empresa, sucursal). Un mismo
    # vendedor que vendió para varias empresas o en varias sucursales
    # produce una fila por cada combinación.
    ventas_agg = queryset_ventas.values(
        'vendedor__id',
        'vendedor__nombre',
        'vendedor__codigo_vendedor',
        'vendedor__comision',
        'emisor__id',
        'emisor__nombre',
        'emisor__rut',
        'sucursal__id',
        'sucursal__alias',
        'sucursal__direccion',
    ).annotate(
        total_ventas=Sum('monto_con_iva'),
        total_neto=Sum('monto_neto'),
        total_documentos=Count('id'),
    )

    # NC con la misma clave triple. Si una NC se emite en otra sucursal
    # respecto de la venta original, se imputa donde se emitió.
    ncs_agg = {
        (r['vendedor__id'], r['emisor__id'], r['sucursal__id']): {
            'total': int(r['total'] or 0),
            'neto': int(r['neto'] or 0),
        }
        for r in queryset_ncs.values(
            'vendedor__id', 'emisor__id', 'sucursal__id',
        ).annotate(
            total=Sum('monto_con_iva'),
            neto=Sum('monto_neto'),
        )
        if r['vendedor__id'] and r['emisor__id']
    }

    def _nuevo_subtotal() -> dict:
        return {
            'total_ventas_brutas_con_iva': 0,
            'total_ventas_brutas_neto': 0,
            'total_devoluciones_con_iva': 0,
            'total_devoluciones_neto': 0,
            'total_ventas_netas_sin_iva': 0,
            'total_ventas_netas_con_iva': 0,
            'total_comisiones': 0,
            'total_documentos': 0,
            'cantidad_vendedores': 0,
        }

    # Construcción anidada Empresa -> Sucursal -> Vendedor + lista flat.
    empresas_map: dict[int, dict] = {}
    vendedores_data: list[dict] = []
    total_comisiones = 0
    total_ventas_brutas_con_iva = 0
    total_ventas_brutas_sin_iva = 0
    total_ventas_netas_sin_iva = 0
    total_ventas_netas_con_iva = 0
    total_documentos = 0
    total_devoluciones = 0
    total_devoluciones_neto = 0
    sucursales_distintas: set[int] = set()

    for item in ventas_agg:
        vid = item['vendedor__id']
        eid = item['emisor__id']
        sid = item['sucursal__id']
        if not vid or not eid:
            continue
        ventas_brutas_iva = int(item['total_ventas'] or 0)
        ventas_brutas_neto = int(item['total_neto'] or 0)
        nc = ncs_agg.get((vid, eid, sid), {'total': 0, 'neto': 0})
        ventas_netas_iva = ventas_brutas_iva - nc['total']
        ventas_netas_neto = ventas_brutas_neto - nc['neto']
        try:
            comision_pct = float(item['vendedor__comision'] or 0)
        except (TypeError, ValueError):
            comision_pct = 0.0
        comision_monto = int(round(ventas_netas_neto * comision_pct / 100.0))

        sucursal_alias = item['sucursal__alias'] or ''
        sucursal_direccion = item['sucursal__direccion'] or ''

        fila = {
            'id': vid,
            'nombre': item['vendedor__nombre'] or '(sin nombre)',
            'codigo': item['vendedor__codigo_vendedor'] or '',
            'empresa_id': eid,
            'empresa_nombre': item['emisor__nombre'] or '(sin empresa)',
            'empresa_rut': item['emisor__rut'] or '',
            'sucursal_id': sid,
            'sucursal_alias': sucursal_alias,
            'sucursal_direccion': sucursal_direccion,
            'ventas_brutas': ventas_brutas_iva,
            'ventas_brutas_neto': ventas_brutas_neto,
            'devoluciones': nc['total'],
            'devoluciones_neto': nc['neto'],
            'ventas_netas_con_iva': ventas_netas_iva,
            'ventas_netas_sin_iva': ventas_netas_neto,
            'documentos': int(item['total_documentos'] or 0),
            'comision_pct': comision_pct,
            'comision_monto': comision_monto,
        }
        vendedores_data.append(fila)

        emp = empresas_map.setdefault(eid, {
            'id': eid,
            'nombre': fila['empresa_nombre'],
            'rut': fila['empresa_rut'],
            'sucursales_map': {},  # interno, se elimina antes de devolver
            'subtotales': {**_nuevo_subtotal(), 'cantidad_sucursales': 0},
        })

        # Clave robusta para sucursales sin id (DTEs huérfanos): usa el
        # alias o un placeholder negativo derivado del id de empresa.
        suc_key = sid if sid is not None else f"_no_suc_{eid}"
        suc = emp['sucursales_map'].setdefault(suc_key, {
            'id': sid,
            'alias': sucursal_alias,
            'direccion': sucursal_direccion,
            'vendedores': [],
            'subtotales': _nuevo_subtotal(),
        })
        suc['vendedores'].append(fila)

        # Subtotales por sucursal.
        ssub = suc['subtotales']
        ssub['total_ventas_brutas_con_iva'] += ventas_brutas_iva
        ssub['total_ventas_brutas_neto'] += ventas_brutas_neto
        ssub['total_devoluciones_con_iva'] += nc['total']
        ssub['total_devoluciones_neto'] += nc['neto']
        ssub['total_ventas_netas_sin_iva'] += ventas_netas_neto
        ssub['total_ventas_netas_con_iva'] += ventas_netas_iva
        ssub['total_comisiones'] += comision_monto
        ssub['total_documentos'] += int(item['total_documentos'] or 0)
        ssub['cantidad_vendedores'] += 1

        # Subtotales por empresa = suma natural sobre todas sus sucursales.
        esub = emp['subtotales']
        esub['total_ventas_brutas_con_iva'] += ventas_brutas_iva
        esub['total_ventas_brutas_neto'] += ventas_brutas_neto
        esub['total_devoluciones_con_iva'] += nc['total']
        esub['total_devoluciones_neto'] += nc['neto']
        esub['total_ventas_netas_sin_iva'] += ventas_netas_neto
        esub['total_ventas_netas_con_iva'] += ventas_netas_iva
        esub['total_comisiones'] += comision_monto
        esub['total_documentos'] += int(item['total_documentos'] or 0)
        esub['cantidad_vendedores'] += 1

        total_comisiones += comision_monto
        total_ventas_brutas_con_iva += ventas_brutas_iva
        total_ventas_brutas_sin_iva += ventas_brutas_neto
        total_ventas_netas_sin_iva += ventas_netas_neto
        total_ventas_netas_con_iva += ventas_netas_iva
        total_documentos += int(item['total_documentos'] or 0)
        total_devoluciones += nc['total']
        total_devoluciones_neto += nc['neto']
        if sid is not None:
            sucursales_distintas.add(sid)

    # Ordenamientos: empresas alfabéticamente; dentro de cada empresa las
    # sucursales por alias alfabético; dentro de cada sucursal los
    # vendedores por ventas netas descendentes.
    for emp in empresas_map.values():
        sucursales_lista = list(emp['sucursales_map'].values())
        for suc in sucursales_lista:
            suc['vendedores'].sort(
                key=lambda v: v['ventas_netas_sin_iva'], reverse=True,
            )
        sucursales_lista.sort(
            key=lambda s: ((s['alias'] or '').lower(), (s['direccion'] or '').lower()),
        )
        emp['sucursales'] = sucursales_lista
        emp['subtotales']['cantidad_sucursales'] = len(sucursales_lista)
        del emp['sucursales_map']

    empresas_data = sorted(
        empresas_map.values(), key=lambda e: (e['nombre'] or '').lower(),
    )
    vendedores_data.sort(
        key=lambda v: (
            v['empresa_nombre'].lower(),
            (v['sucursal_alias'] or '').lower(),
            -v['ventas_netas_sin_iva'],
        ),
    )

    return {
        'empresas': empresas_data,
        'vendedores': vendedores_data,
        'fecha_inicio': fi.strftime('%Y-%m-%d'),
        'fecha_fin': ff.strftime('%Y-%m-%d'),
        'sucursal_id': sucursal_id or None,
        'vendedor_id': vendedor_id or None,
        'totales': {
            # "Total original" — la cifra que coincide con el KPI principal
            # del reporte (ventas brutas con IVA, antes de devoluciones).
            'total_ventas_brutas_con_iva': total_ventas_brutas_con_iva,
            'total_ventas_brutas_sin_iva': total_ventas_brutas_sin_iva,
            'total_ventas_netas_sin_iva': total_ventas_netas_sin_iva,
            'total_ventas_netas_con_iva': total_ventas_netas_con_iva,
            'total_comisiones': total_comisiones,
            'total_documentos': total_documentos,
            'total_devoluciones': total_devoluciones,
            'total_devoluciones_neto': total_devoluciones_neto,
            'cantidad_vendedores': len(vendedores_data),
            'cantidad_empresas': len(empresas_data),
            'cantidad_sucursales': len(sucursales_distintas),
        },
    }


@require_GET
@login_required
def obtener_comisiones_por_vendedor(request):
    """API JSON: comisiones por vendedor en el período/sucursal indicados.

    Reusa los filtros del reporte de ventas-sucursal (mes/fecha/rango y
    sucursal/vendedor). La comisión se calcula como
    `venta_neta_sin_iva × Vendedor.comision / 100`.

    Requiere el permiso `reporte_comisiones_vendedor` (acción `puede_ver`)
    configurable por rol/sucursal desde la pantalla de gestión de permisos.
    """
    if not _puede_ver_reporte_comisiones(
        request.user, sucursal_id=_sucursal_id_sesion(request)
    ):
        return JsonResponse(
            {
                'success': False,
                'error': 'No tienes permiso para ver el reporte de comisiones por vendedor',
            },
            status=403,
        )
    try:
        data = _calcular_comisiones_vendedor(request)
        return JsonResponse({'success': True, **data})
    except ValueError as e:
        return JsonResponse(
            {'success': False, 'error': f'Parámetros inválidos: {e}'},
            status=400,
        )
    except Exception as e:
        logger.exception("Error al calcular comisiones de vendedor")
        return JsonResponse(
            {'success': False, 'error': f'Error al calcular comisiones: {e}'},
            status=500,
        )


@require_GET
@login_required
def exportar_comisiones_vendedor_excel(request):
    """Exporta el reporte de comisiones por vendedor a Excel con formato.

    Encabezado con color institucional, totales en negrita, formato moneda
    y porcentaje, columnas auto-dimensionadas y bordes en toda la tabla.
    Reusa `_calcular_comisiones_vendedor` para garantizar que el Excel
    refleje exactamente los mismos números que el modal en pantalla.

    Requiere el permiso `reporte_comisiones_vendedor` con la acción
    `puede_exportar` (puede ser distinta de `puede_ver`: un rol puede
    consultar el reporte en pantalla pero no descargarlo).
    """
    sucursal_id_sesion = _sucursal_id_sesion(request)
    if not _puede_ver_reporte_comisiones(request.user, sucursal_id_sesion):
        return JsonResponse(
            {
                'success': False,
                'error': 'No tienes permiso para ver el reporte de comisiones',
            },
            status=403,
        )
    if not _puede_exportar_reporte_comisiones(request.user, sucursal_id_sesion):
        return JsonResponse(
            {
                'success': False,
                'error': 'No tienes permiso para exportar el reporte de comisiones',
            },
            status=403,
        )
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        data = _calcular_comisiones_vendedor(request)

        # Resolver alias de sucursal y vendedor para mostrar en el header.
        sucursal_label = 'Todas'
        if data['sucursal_id']:
            try:
                suc = Sucursal.objects.get(id=data['sucursal_id'])
                sucursal_label = suc.alias or suc.direccion or f"#{suc.id}"
            except Sucursal.DoesNotExist:
                sucursal_label = f"#{data['sucursal_id']}"

        vendedor_label = 'Todos'
        if data['vendedor_id']:
            try:
                v = Vendedor.objects.get(id=data['vendedor_id'])
                vendedor_label = f"{v.codigo_vendedor} - {v.nombre}"
            except Vendedor.DoesNotExist:
                vendedor_label = f"#{data['vendedor_id']}"

        wb = Workbook()
        ws = wb.active
        ws.title = 'Comisiones por Vendedor'

        # Estilos institucionales NEXO (azul corporativo).
        header_fill = PatternFill(start_color='0066FF', end_color='0066FF', fill_type='solid')
        header_font = Font(bold=True, color='FFFFFF', size=11)
        sub_fill = PatternFill(start_color='E6F0FF', end_color='E6F0FF', fill_type='solid')
        total_fill = PatternFill(start_color='1A1A2E', end_color='1A1A2E', fill_type='solid')
        total_font = Font(bold=True, color='FFFFFF', size=11)
        empresa_fill = PatternFill(start_color='0052CC', end_color='0052CC', fill_type='solid')
        empresa_font = Font(bold=True, color='FFFFFF', size=12)
        # Banner de sucursal: tono azul intermedio (más suave que empresa,
        # más fuerte que subtotal). Permite separar visualmente los 3
        # niveles de jerarquía: TOTAL > EMPRESA > SUCURSAL > vendedor.
        sucursal_fill = PatternFill(start_color='4D7BC9', end_color='4D7BC9', fill_type='solid')
        sucursal_font = Font(bold=True, color='FFFFFF', size=11)
        # Subtotal sucursal: tono más claro que el subtotal de empresa para
        # que la jerarquía siga reflejándose en las filas de totales.
        subtotal_suc_fill = PatternFill(start_color='EAF1FB', end_color='EAF1FB', fill_type='solid')
        subtotal_suc_font = Font(bold=True, color='1A1A2E', size=10)
        # Subtotal empresa (más oscuro que el de sucursal).
        subtotal_fill = PatternFill(start_color='D9E2F3', end_color='D9E2F3', fill_type='solid')
        subtotal_font = Font(bold=True, color='1A1A2E', size=10)
        thin = Side(style='thin', color='B0B0B0')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        center = Alignment(horizontal='center', vertical='center', wrap_text=True)
        right = Alignment(horizontal='right', vertical='center')
        left = Alignment(horizontal='left', vertical='center', wrap_text=True)
        left_indent = Alignment(horizontal='left', vertical='center', wrap_text=True, indent=1)

        # 8 columnas: #, Vendedor, Código, Ventas Brutas, Devoluciones,
        # Ventas Netas, % Comisión, Comisión $. La sucursal y dirección
        # se muestran como banner agrupador, no como columnas.
        N_COLS = 8
        last_col_letter = get_column_letter(N_COLS)

        # ===== Banner / título =====
        ws.merge_cells(f'A1:{last_col_letter}1')
        ws['A1'] = 'REPORTE DE COMISIONES POR VENDEDOR'
        ws['A1'].font = Font(bold=True, color='FFFFFF', size=14)
        ws['A1'].fill = total_fill
        ws['A1'].alignment = center
        ws.row_dimensions[1].height = 26

        # ===== Sub-banner: contexto de filtros =====
        ws.merge_cells(f'A2:{last_col_letter}2')
        rango_str = (
            f"Período: {data['fecha_inicio']} → {data['fecha_fin']}  ·  "
            f"Sucursal: {sucursal_label}  ·  Vendedor: {vendedor_label}"
        )
        ws['A2'] = rango_str
        ws['A2'].font = Font(italic=True, size=10, color='4A4A5A')
        ws['A2'].alignment = center
        ws['A2'].fill = sub_fill
        ws.row_dimensions[2].height = 20

        ws.merge_cells(f'A3:{last_col_letter}3')
        ws['A3'] = (
            f"Generado: {timezone.localtime().strftime('%d/%m/%Y %H:%M')}  ·  "
            f"Empresas: {data['totales'].get('cantidad_empresas', 0)}  ·  "
            f"Sucursales: {data['totales'].get('cantidad_sucursales', 0)}  ·  "
            f"Vendedores: {data['totales']['cantidad_vendedores']}"
        )
        ws['A3'].font = Font(italic=True, size=9, color='8A8A9A')
        ws['A3'].alignment = center

        # ===== Headers de tabla =====
        # "Ventas Brutas (c/IVA)" para que sea distinto a "Ventas Netas (s/IVA)"
        # cuando no hay devoluciones; concilia con el KPI "Total Ventas c/IVA".
        headers = [
            '#', 'Vendedor', 'Código',
            'Ventas Brutas (c/IVA)', 'Devoluciones (s/IVA)',
            'Ventas Netas (s/IVA)', '% Comisión', 'Comisión $',
        ]
        row = 5
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = center
        ws.row_dimensions[row].height = 32

        # ===== Filas de datos: Empresa -> Sucursal -> Vendedor =====
        money_fmt = '"$"#,##0'
        pct_fmt = '0.00"%"'
        row = 6
        idx_global = 0
        empresas = data.get('empresas') or []

        def _label_sucursal_compacto(suc: dict) -> str:
            """Texto del banner de sucursal: alias — dirección (compacto)."""
            alias = (suc.get('alias') or '').strip()
            direc = (suc.get('direccion') or '').strip()
            if alias and direc and alias.lower() != direc.lower():
                return f"{alias} — {direc}"
            return alias or direc or '(sin sucursal)'

        for emp in empresas:
            sucursales_emp = emp.get('sucursales') or []
            # Backcompat: si por algún motivo el backend mandara solo
            # `vendedores` (formato anterior), agruparlos en una
            # "sucursal sintética" sin alias para no romper el Excel.
            if not sucursales_emp and emp.get('vendedores'):
                sucursales_emp = [{
                    'id': None,
                    'alias': '',
                    'direccion': '',
                    'vendedores': emp.get('vendedores') or [],
                    'subtotales': emp.get('subtotales') or {},
                }]
            total_vend_emp = sum(
                len(s.get('vendedores') or []) for s in sucursales_emp
            )
            if not total_vend_emp:
                continue

            sub_emp = emp.get('subtotales') or {}
            cant_suc = sub_emp.get('cantidad_sucursales') or len(sucursales_emp)

            # Banner empresa.
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=N_COLS)
            cabecera_emp = ws.cell(
                row=row, column=1,
                value=(
                    f"EMPRESA: {emp.get('nombre') or '(sin nombre)'}"
                    + (f"  ·  RUT {emp.get('rut')}" if emp.get('rut') else '')
                    + f"  ·  {cant_suc} sucursal"
                    + ('' if cant_suc == 1 else 'es')
                    + f"  ·  {total_vend_emp} vendedor"
                    + ('' if total_vend_emp == 1 else 'es')
                ),
            )
            cabecera_emp.fill = empresa_fill
            cabecera_emp.font = empresa_font
            cabecera_emp.alignment = left
            ws.row_dimensions[row].height = 22
            for col in range(1, N_COLS + 1):
                ws.cell(row=row, column=col).border = border
            row += 1

            for suc in sucursales_emp:
                vendedores_suc = suc.get('vendedores') or []
                if not vendedores_suc:
                    continue
                ssub = suc.get('subtotales') or {}
                label_suc = _label_sucursal_compacto(suc)

                # Banner sucursal (nivel intermedio entre empresa y
                # subtotal). Compacto: alias y dirección en una sola línea.
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=N_COLS)
                cabecera_suc = ws.cell(
                    row=row, column=1,
                    value=(
                        f"↳ SUCURSAL: {label_suc}"
                        f"  ·  {len(vendedores_suc)} vendedor"
                        + ('' if len(vendedores_suc) == 1 else 'es')
                    ),
                )
                cabecera_suc.fill = sucursal_fill
                cabecera_suc.font = sucursal_font
                cabecera_suc.alignment = left_indent
                ws.row_dimensions[row].height = 20
                for col in range(1, N_COLS + 1):
                    ws.cell(row=row, column=col).border = border
                row += 1

                for v in vendedores_suc:
                    idx_global += 1
                    ws.cell(row=row, column=1, value=idx_global).alignment = center
                    ws.cell(row=row, column=2, value=v.get('nombre', '')).alignment = left
                    ws.cell(row=row, column=3, value=v.get('codigo', '')).alignment = center

                    c4 = ws.cell(row=row, column=4, value=v.get('ventas_brutas', 0))
                    c4.number_format = money_fmt
                    c4.alignment = right

                    c5 = ws.cell(row=row, column=5, value=v.get('devoluciones_neto', 0))
                    c5.number_format = money_fmt
                    c5.alignment = right

                    c6 = ws.cell(row=row, column=6, value=v.get('ventas_netas_sin_iva', 0))
                    c6.number_format = money_fmt
                    c6.alignment = right
                    c6.font = Font(bold=True, color='1A1A2E')

                    c7 = ws.cell(row=row, column=7, value=v.get('comision_pct', 0))
                    c7.number_format = pct_fmt
                    c7.alignment = center

                    c8 = ws.cell(row=row, column=8, value=v.get('comision_monto', 0))
                    c8.number_format = money_fmt
                    c8.alignment = right
                    c8.font = Font(bold=True, color='00B38A')

                    for col in range(1, N_COLS + 1):
                        ws.cell(row=row, column=col).border = border
                    row += 1

                # Subtotal por sucursal.
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
                cell_lbl = ws.cell(
                    row=row, column=1,
                    value=f"↳ Subtotal {label_suc}",
                )
                cell_lbl.fill = subtotal_suc_fill
                cell_lbl.font = subtotal_suc_font
                cell_lbl.alignment = right

                ss4 = ws.cell(row=row, column=4, value=ssub.get('total_ventas_brutas_con_iva', 0))
                ss4.number_format = money_fmt
                ss4.fill = subtotal_suc_fill
                ss4.font = subtotal_suc_font
                ss4.alignment = right

                ss5 = ws.cell(row=row, column=5, value=ssub.get('total_devoluciones_neto', 0))
                ss5.number_format = money_fmt
                ss5.fill = subtotal_suc_fill
                ss5.font = subtotal_suc_font
                ss5.alignment = right

                ss6 = ws.cell(row=row, column=6, value=ssub.get('total_ventas_netas_sin_iva', 0))
                ss6.number_format = money_fmt
                ss6.fill = subtotal_suc_fill
                ss6.font = subtotal_suc_font
                ss6.alignment = right

                ws.cell(row=row, column=7, value='').fill = subtotal_suc_fill

                ss8 = ws.cell(row=row, column=8, value=ssub.get('total_comisiones', 0))
                ss8.number_format = money_fmt
                ss8.fill = subtotal_suc_fill
                ss8.font = Font(bold=True, color='00B38A', size=10)
                ss8.alignment = right

                for col in range(1, N_COLS + 1):
                    ws.cell(row=row, column=col).border = border
                ws.row_dimensions[row].height = 18
                row += 1

            # Subtotal por empresa (suma de sus sucursales).
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            cell_lbl = ws.cell(
                row=row, column=1,
                value=f"Subtotal {emp.get('nombre') or ''}",
            )
            cell_lbl.fill = subtotal_fill
            cell_lbl.font = subtotal_font
            cell_lbl.alignment = right

            cs4 = ws.cell(row=row, column=4, value=sub_emp.get('total_ventas_brutas_con_iva', 0))
            cs4.number_format = money_fmt
            cs4.fill = subtotal_fill
            cs4.font = subtotal_font
            cs4.alignment = right

            cs5 = ws.cell(row=row, column=5, value=sub_emp.get('total_devoluciones_neto', 0))
            cs5.number_format = money_fmt
            cs5.fill = subtotal_fill
            cs5.font = subtotal_font
            cs5.alignment = right

            cs6 = ws.cell(row=row, column=6, value=sub_emp.get('total_ventas_netas_sin_iva', 0))
            cs6.number_format = money_fmt
            cs6.fill = subtotal_fill
            cs6.font = subtotal_font
            cs6.alignment = right

            ws.cell(row=row, column=7, value='').fill = subtotal_fill

            cs8 = ws.cell(row=row, column=8, value=sub_emp.get('total_comisiones', 0))
            cs8.number_format = money_fmt
            cs8.fill = subtotal_fill
            cs8.font = Font(bold=True, color='00B38A', size=10)
            cs8.alignment = right

            for col in range(1, N_COLS + 1):
                ws.cell(row=row, column=col).border = border
            ws.row_dimensions[row].height = 20
            row += 1

            # Fila vacía como separador entre empresas.
            row += 1

        # ===== Fila de TOTAL GENERAL =====
        if data['vendedores']:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            cell_label = ws.cell(row=row, column=1, value='TOTAL GENERAL')
            cell_label.fill = total_fill
            cell_label.font = total_font
            cell_label.alignment = right

            t_brutas = sum(v['ventas_brutas'] for v in data['vendedores'])
            t_dev = sum(v['devoluciones_neto'] for v in data['vendedores'])

            c4 = ws.cell(row=row, column=4, value=t_brutas)
            c4.number_format = money_fmt
            c4.fill = total_fill
            c4.font = total_font
            c4.alignment = right

            c5 = ws.cell(row=row, column=5, value=t_dev)
            c5.number_format = money_fmt
            c5.fill = total_fill
            c5.font = total_font
            c5.alignment = right

            c6 = ws.cell(
                row=row, column=6,
                value=data['totales']['total_ventas_netas_sin_iva'],
            )
            c6.number_format = money_fmt
            c6.fill = total_fill
            c6.font = total_font
            c6.alignment = right

            ws.cell(row=row, column=7, value='').fill = total_fill

            c8 = ws.cell(
                row=row, column=8,
                value=data['totales']['total_comisiones'],
            )
            c8.number_format = money_fmt
            c8.fill = total_fill
            c8.font = total_font
            c8.alignment = right

            for col in range(1, N_COLS + 1):
                ws.cell(row=row, column=col).border = border
            ws.row_dimensions[row].height = 22

        # ===== Anchos =====
        # #, Vendedor, Código, Brutas, Dev, Netas, %, Comisión.
        anchos = [5, 32, 14, 20, 20, 22, 12, 20]
        for col, w in enumerate(anchos, 1):
            ws.column_dimensions[get_column_letter(col)].width = w

        # Congelar encabezado de tabla.
        ws.freeze_panes = 'A6'

        # Render binario.
        from io import BytesIO
        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)

        nombre = (
            f"comisiones_{data['fecha_inicio']}_{data['fecha_fin']}.xlsx"
        )
        response = HttpResponse(
            bio.read(),
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.spreadsheetml.sheet'
            ),
        )
        response['Content-Disposition'] = (
            f'attachment; filename="{nombre}"'
        )
        return response
    except Exception as e:
        logger.exception("Error al exportar comisiones de vendedor")
        return JsonResponse(
            {'success': False, 'error': f'Error al exportar comisiones: {e}'},
            status=500,
        )


@require_GET
@login_required
def obtener_ventas_por_sucursal_reporte(request):
    """API para obtener datos de ventas por sucursal.

    Reporte de **facturación histórica** (no de caja):
      * Cuenta toda venta emitida, INCLUSO si después se anuló
        (``estado_dte=ANULADO`` también entra). Si vendiste $X el día Y,
        ese día Y vendiste $X — independiente de que más tarde se anule
        con NC.
      * Las NC restan del día en que se emitieron (sea ``DEVOLUCION`` o
        ``ANULACION``). Así, si emitiste la NC en otro mes, el reporte
        del mes original sigue mostrando la venta y el mes de la NC
        muestra la devolución.
      * No es lo mismo que cuadratura-caja: cuadratura excluye los
        ANULADOS (no hay dinero) y trata las NC ANULACION como
        informativas. Para conciliar las dos vistas usar el botón
        "Diagnóstico vs cuadratura" del tab sucursales.

    Filtros que SÍ aplica:
      * ``tipo_transaccion in {VENTA, VENTA_PUBLICO, DEVOLUCION, ANULACION}``
        (las dos primeras para ventas; las dos últimas para que las NC
        del día entren al queryset)
      * ``estado_dte in {EMITIDO, ACEPTADO, ANULADO}`` — excluye
        ``CANCELADO``/``RECHAZADO``/``PENDIENTE`` (rechazos del SII y
        documentos sin emitir).
      * ``descartado=False`` — NC ocultas y eliminaciones lógicas no
        cuentan (operación interna).
      * Incluye FACTURA EXENTA, autoventas, CDs y facturas sin receptor
        (todo lo que se haya emitido oficialmente).
    """
    try:
        # Parámetros de filtro
        mes = request.GET.get('mes')  # Formato: YYYY-MM
        fecha = request.GET.get('fecha')  # Formato: YYYY-MM-DD (fecha específica)
        fecha_inicio_param = request.GET.get('fecha_inicio')  # Formato: YYYY-MM-DD (rango)
        fecha_fin_param = request.GET.get('fecha_fin')  # Formato: YYYY-MM-DD (rango)
        sucursal_id = request.GET.get('sucursal_id')

        # Determinar rango de fechas según el tipo de filtro.
        # Default: mes actual en zona horaria Chile (regla `timezone-chile`).
        if fecha:
            fecha_inicio = datetime.strptime(fecha, '%Y-%m-%d')
            fecha_fin = fecha_inicio
        elif fecha_inicio_param and fecha_fin_param:
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            if not mes:
                mes = timezone.localdate().strftime('%Y-%m')
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)

        fi = fecha_inicio.date() if hasattr(fecha_inicio, 'date') else fecha_inicio
        ff = fecha_fin.date() if hasattr(fecha_fin, 'date') else fecha_fin

        # ========== DTEs (facturación histórica) ==========
        # Acepta ANULADO porque ese DTE fue una venta real el día que se
        # emitió; la NC asociada se contabiliza el día en que se emitió.
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fi,
            fecha_emision__lte=ff,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
            estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'],
            descartado=False,
        ).select_related('sucursal', 'vendedor')

        queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)

        # Separar ventas (no-NC) de notas de crédito.
        queryset_ventas = queryset_dtes.exclude(tipo_documento='NOTA DE CREDITO')
        # Restar TODAS las NC (DEVOLUCION + ANULACION). En este reporte
        # ambas representan ventas que se deshicieron oficialmente, así
        # que ambas afectan el total del mes. La distinción DEVOLUCION/
        # ANULACION es relevante para cuadratura-caja, no para un reporte
        # de facturación histórica.
        queryset_ncs = queryset_dtes.filter(tipo_documento='NOTA DE CREDITO')
        queryset_ncs_devolucion = queryset_ncs.filter(tipo_transaccion='DEVOLUCION')
        queryset_ncs_anulacion = queryset_ncs.filter(tipo_transaccion='ANULACION')

        # Agregar ventas por sucursal
        ventas_por_suc = queryset_ventas.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion',
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            total_neto=Sum('monto_neto'),
            total_descuentos=Sum('descuento'),
            total_documentos=Count('id'),
        )

        # NCs por sucursal: AMBAS modalidades (DEVOLUCION y ANULACION)
        # restan del total. Es un reporte de facturación, así que cualquier
        # NC válida cancela una venta previa.
        ncs_por_suc = {
            r['sucursal__id']: {
                'total': int(r['total'] or 0),
                'neto': int(r['neto'] or 0),
                'cantidad': int(r['cant'] or 0),
            }
            for r in queryset_ncs.values('sucursal__id').annotate(
                total=Sum('monto_con_iva'),
                neto=Sum('monto_neto'),
                cant=Count('id'),
            )
        }

        # Desglose informativo (no afecta totales — solo se muestra como
        # conteo en cada fila para que el operador sepa cuántas
        # ANULACIONes hay vs DEVOLUCIONes).
        ncs_anul_por_suc = {
            r['sucursal__id']: {
                'total': int(r['total'] or 0),
                'cantidad': int(r['cant'] or 0),
            }
            for r in queryset_ncs_anulacion.values('sucursal__id').annotate(
                total=Sum('monto_con_iva'),
                cant=Count('id'),
            )
        }

        # Vendedores distintos por sucursal
        vendedores_por_sucursal = {}
        for r in queryset_dtes.values('sucursal_id', 'vendedor_id').distinct():
            sid = r['sucursal_id']
            vid = r['vendedor_id']
            if sid and vid:
                vendedores_por_sucursal.setdefault(sid, set()).add(vid)

        # Consolidar ventas + devoluciones por sucursal
        ventas_acumuladas = {}
        for item in ventas_por_suc:
            sid = item['sucursal__id']
            if not sid:
                continue
            total_brutas = int(item['total_ventas'] or 0)
            neto_brutas = int(item['total_neto'] or 0)
            iva_brutas = total_brutas - neto_brutas
            nc = ncs_por_suc.get(sid, {'total': 0, 'neto': 0, 'cantidad': 0})
            nc_anul = ncs_anul_por_suc.get(sid, {'total': 0, 'cantidad': 0})
            ventas_acumuladas[sid] = {
                'alias': item['sucursal__alias'],
                'direccion': item['sucursal__direccion'],
                'ventas_brutas': total_brutas,
                'neto_brutas': neto_brutas,
                'iva_brutas': iva_brutas,
                'descuentos': int(item['total_descuentos'] or 0),
                'documentos': int(item['total_documentos'] or 0),
                'devoluciones': nc['total'],
                'devoluciones_neto': nc['neto'],
                'devoluciones_iva': nc['total'] - nc['neto'],
                'cantidad_devoluciones': nc['cantidad'],
                'nc_anulacion_total': nc_anul['total'],
                'cantidad_nc_anulacion': nc_anul['cantidad'],
            }

        # Sucursales con NC pero sin ventas en el período
        for sid, nc in ncs_por_suc.items():
            if not sid or sid in ventas_acumuladas:
                continue
            try:
                from .models import Sucursal as _Sucursal
                suc = _Sucursal.objects.get(id=sid)
                nc_anul = ncs_anul_por_suc.get(sid, {'total': 0, 'cantidad': 0})
                ventas_acumuladas[sid] = {
                    'alias': suc.alias,
                    'direccion': suc.direccion,
                    'ventas_brutas': 0,
                    'neto_brutas': 0,
                    'iva_brutas': 0,
                    'descuentos': 0,
                    'documentos': 0,
                    'devoluciones': nc['total'],
                    'devoluciones_neto': nc['neto'],
                    'devoluciones_iva': nc['total'] - nc['neto'],
                    'cantidad_devoluciones': nc['cantidad'],
                    'nc_anulacion_total': nc_anul['total'],
                    'cantidad_nc_anulacion': nc_anul['cantidad'],
                }
            except Exception:
                pass

        # Sucursales con NC ANULACION pero sin ventas ni NC devolución
        for sid, nc_anul in ncs_anul_por_suc.items():
            if not sid or sid in ventas_acumuladas:
                continue
            try:
                from .models import Sucursal as _Sucursal
                suc = _Sucursal.objects.get(id=sid)
                ventas_acumuladas[sid] = {
                    'alias': suc.alias,
                    'direccion': suc.direccion,
                    'ventas_brutas': 0,
                    'neto_brutas': 0,
                    'iva_brutas': 0,
                    'descuentos': 0,
                    'documentos': 0,
                    'devoluciones': 0,
                    'devoluciones_neto': 0,
                    'devoluciones_iva': 0,
                    'cantidad_devoluciones': 0,
                    'nc_anulacion_total': nc_anul['total'],
                    'cantidad_nc_anulacion': nc_anul['cantidad'],
                }
            except Exception:
                pass

        # Ventas netas (descontando devoluciones) para totales y ordenamiento
        total_general = sum(
            d['ventas_brutas'] - d['devoluciones']
            for d in ventas_acumuladas.values()
        )

        sucursales_data = []
        for sid, data in sorted(
            ventas_acumuladas.items(),
            key=lambda x: x[1]['ventas_brutas'] - x[1]['devoluciones'],
            reverse=True,
        ):
            ventas_netas = data['ventas_brutas'] - data['devoluciones']
            neto_netas = data['neto_brutas'] - data['devoluciones_neto']
            iva_netas = data['iva_brutas'] - data['devoluciones_iva']
            total_docs = data['documentos']
            participacion = (ventas_netas / total_general * 100) if total_general > 0 else 0

            sucursales_data.append({
                'id': sid,
                'nombre': data['alias'],
                'direccion': data['direccion'],
                'neto': int(neto_netas),
                'iva': int(iva_netas),
                'descuentos': int(data['descuentos']),
                'ventas': int(ventas_netas),
                'ventas_brutas': int(data['ventas_brutas']),
                'devoluciones': int(data['devoluciones']),
                'cantidad_devoluciones': int(data['cantidad_devoluciones']),
                # NC informativas (ANULACION) — no restan, solo se cuentan
                # para que el operador vea el dato sin descuadrar el total.
                'nc_anulacion_total': int(data.get('nc_anulacion_total', 0)),
                'cantidad_nc_anulacion': int(data.get('cantidad_nc_anulacion', 0)),
                'documentos': total_docs,
                'vendedores': len(vendedores_por_sucursal.get(sid, set())),
                'participacion': round(participacion, 1),
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


# ========== DIAGNÓSTICO: cuadratura-caja vs reporte ventas-sucursal ==========

# Filtros que aplica el reporte de ventas-sucursal (para detectar exclusiones).
# Mantener sincronizado con `obtener_ventas_por_sucursal_reporte`.
_REPORTE_TIPO_TRANSACCION_PERMITIDA = {
    'VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION',
}
# El reporte acepta ANULADO (es facturación histórica). Cuadratura no lo
# acepta (es caja del día). Por eso el diagnóstico mostrará el DTE ANULADO
# como "solo en reporte" — diferencia esperada y deseable.
_REPORTE_ESTADO_DTE_PERMITIDO = {'EMITIDO', 'ACEPTADO', 'ANULADO'}


def _diagnosticar_dte_vs_reporte(dte):
    """Devuelve la lista de motivos por los que un DTE NO entra al reporte
    de ventas-sucursal según los filtros que apliquen.

    Lista vacía => el DTE entraría al reporte. La función refleja los
    filtros REALES vigentes en `obtener_ventas_por_sucursal_reporte`
    tras alinear con cuadratura (sin exclusiones por CENTRO_DISTRIBUCION,
    autoventa, FACTURA_EXENTA o factura-sin-receptor). Si en el futuro
    se reintroduce alguna de esas exclusiones, sumar el motivo aquí
    para que el diagnóstico siga siendo fiel.
    """
    motivos = []
    if dte.tipo_transaccion not in _REPORTE_TIPO_TRANSACCION_PERMITIDA:
        motivos.append(f'TIPO_TRANSACCION={dte.tipo_transaccion}')
    if dte.estado_dte not in _REPORTE_ESTADO_DTE_PERMITIDO:
        motivos.append(f'ESTADO={dte.estado_dte}')
    if getattr(dte, 'descartado', False):
        motivos.append('DESCARTADO')
    return motivos


def _diagnosticar_dte_vs_cuadratura(dte):
    """Devuelve True si el DTE pasa los filtros que aplica
    `_calcular_cuadratura_data` al iterar DTEs del día.

    Filtros replicados (lectura — no se invoca):
      - estado_dte in {EMITIDO, ACEPTADO}
      - tipo_transaccion in {VENTA, VENTA_PUBLICO, DEVOLUCION, ANULACION}
      - descartado=False
    """
    if dte.estado_dte not in {'EMITIDO', 'ACEPTADO'}:
        return False
    if dte.tipo_transaccion not in {
        'VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'
    }:
        return False
    if getattr(dte, 'descartado', False):
        return False
    return True


@require_GET
@login_required
def api_diagnostico_cuadratura_vs_reporte(request):
    """Compara, para una sucursal y fecha puntual, los totales de
    cuadratura-caja contra los del reporte ventas-sucursal y lista los
    DTEs en disputa con su motivo de exclusión.

    Parámetros:
      - `fecha=YYYY-MM-DD` (requerido)
      - `sucursal_id=<int>` (requerido)

    Devuelve JSON con::

        {
          "success": True,
          "fecha": "...",
          "sucursal": {"id": ..., "alias": "..."},
          "cuadratura_total": <int>,
          "reporte_total": <int>,
          "diferencia": <cuadratura - reporte>,
          "solo_en_cuadratura": [
              {id, folio, tipo_documento, tipo_transaccion, estado, monto,
               descartado, receptor_id, motivos_exclusion_reporte: [...]}
          ],
          "solo_en_reporte": [...],
          "tickets_sin_dte": [
              {id, correlativo, total, folio_dte}
          ],
          "resumen_cuadratura": {  # extracto del dict de cuadratura
              "venta_total", "total_tickets", "total_boletas_electronicas",
              ...
          }
        }

    NO modifica datos. Es una herramienta de auditoría que llama al
    helper `_calcular_cuadratura_data` (lectura) y reconstruye los
    filtros del reporte para comparar dte por dte.
    """
    from .views_modulo_ventas import _calcular_cuadratura_data

    fecha_str = request.GET.get('fecha')
    sucursal_id = request.GET.get('sucursal_id')

    if not fecha_str:
        return JsonResponse({
            'success': False,
            'error': 'Parametro `fecha` requerido (YYYY-MM-DD).'
        }, status=400)
    if not sucursal_id:
        return JsonResponse({
            'success': False,
            'error': 'Parametro `sucursal_id` requerido.'
        }, status=400)

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'Formato de fecha invalido. Usar YYYY-MM-DD.'
        }, status=400)

    try:
        sucursal = Sucursal.objects.get(id=int(sucursal_id))
    except (Sucursal.DoesNotExist, ValueError, TypeError):
        return JsonResponse({
            'success': False,
            'error': 'Sucursal no encontrada.'
        }, status=404)

    # Cuadratura: fuente de verdad. NO se toca.
    cuadratura = _calcular_cuadratura_data(sucursal, fecha_str)

    # Set de DTEs "puro" del día y sucursal (sin exclusiones), para listar
    # caso por caso lo que entra/no entra a cada reporte.
    dtes_puros = list(
        Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,
        ).select_related('receptor', 'emisor', 'sucursal').order_by(
            'tipo_documento', 'numero_documento'
        )
    )

    # Recalcular los totales del reporte para esta fecha/sucursal usando
    # la misma logica del endpoint del reporte (tras los fixes), sin
    # llamar al endpoint para no depender de la request.
    reporte_total = 0
    solo_en_cuadratura = []
    solo_en_reporte = []
    en_ambos = 0

    for dte in dtes_puros:
        motivos_excl_reporte = _diagnosticar_dte_vs_reporte(dte)
        entra_reporte = (len(motivos_excl_reporte) == 0)
        entra_cuadratura = _diagnosticar_dte_vs_cuadratura(dte)

        # Calcular el aporte al "total ventas" del reporte:
        # el reporte hace `ventas_brutas - devoluciones`. Como es un
        # reporte de facturación histórica, restamos TODAS las NC
        # (DEVOLUCION y ANULACION). Cuadratura solo resta DEVOLUCION;
        # esa diferencia se puede observar en el listado de DTE en
        # disputa que devuelve este endpoint.
        es_nc = dte.tipo_documento == 'NOTA DE CREDITO'
        aporte_reporte = 0
        if entra_reporte:
            monto = int(dte.monto_con_iva or 0)
            if es_nc:
                aporte_reporte = -monto
            else:
                aporte_reporte = monto
        reporte_total += aporte_reporte

        info = {
            'id': dte.id,
            'folio': dte.numero_documento,
            'tipo_documento': dte.tipo_documento,
            'tipo_transaccion': dte.tipo_transaccion,
            'estado': dte.estado_dte,
            'monto': int(dte.monto_con_iva or 0),
            'descartado': bool(getattr(dte, 'descartado', False)),
            'receptor_id': dte.receptor_id,
            'receptor_nombre': dte.receptor.nombre if dte.receptor else None,
            'motivos_exclusion_reporte': motivos_excl_reporte,
            'entra_cuadratura': entra_cuadratura,
            'entra_reporte': entra_reporte,
        }

        if entra_cuadratura and not entra_reporte:
            solo_en_cuadratura.append(info)
        elif entra_reporte and not entra_cuadratura:
            solo_en_reporte.append(info)
        elif entra_cuadratura and entra_reporte:
            en_ambos += 1

    # Tickets pagados sin DTE (cuadratura los suma desde Ticket; el reporte
    # solo mira DTE, así que para alinear hay que conocerlos).
    tickets_pagados = Ticket.objects.filter(
        sucursal=sucursal,
        fecha=fecha_obj,
        estado='PAGADO',
    ).only('id', 'correlativo', 'total', 'folio_dte')

    folios_emitidos = {
        d.numero_documento for d in dtes_puros if d.numero_documento
    }
    tickets_sin_dte = []
    for t in tickets_pagados:
        if t.folio_dte and t.folio_dte in folios_emitidos:
            continue
        tickets_sin_dte.append({
            'id': t.id,
            'correlativo': t.correlativo,
            'total': int(t.total or 0),
            'folio_dte': t.folio_dte,
        })

    cuadratura_total = int(cuadratura.get('venta_total', 0) or 0)
    diferencia = cuadratura_total - reporte_total

    resumen_cuadratura = {
        k: int(cuadratura.get(k, 0) or 0)
        for k in (
            'venta_total',
            'total_tickets',
            'total_boletas_electronicas',
            'total_boletas_papel',
            'total_facturas',
            'total_facturas_exentas',
            'total_notas_credito',
            'cantidad_tickets',
            'cantidad_boletas_electronicas',
            'cantidad_boletas_papel',
            'cantidad_facturas',
            'cantidad_facturas_exentas',
            'cantidad_notas_credito',
        )
    }

    return JsonResponse({
        'success': True,
        'fecha': fecha_str,
        'sucursal': {'id': sucursal.id, 'alias': sucursal.alias},
        'cuadratura_total': cuadratura_total,
        'reporte_total': reporte_total,
        'diferencia': diferencia,
        'solo_en_cuadratura': solo_en_cuadratura,
        'solo_en_reporte': solo_en_reporte,
        'en_ambos': en_ambos,
        'tickets_sin_dte': tickets_sin_dte,
        'tickets_sin_dte_total': sum(t['total'] for t in tickets_sin_dte),
        'resumen_cuadratura': resumen_cuadratura,
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
            
            # IDs de vendedores con DTEs de venta al público en esta sucursal
            vendedores_dtes = set(Dte.objects.filter(
                sucursal_id=sucursal_id,
                tipo_transaccion='VENTA_PUBLICO',
            ).exclude(
                receptor__isnull=False,
                receptor_id=F('emisor_id')
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
    """API para obtener lista de sucursales según permisos del usuario"""
    try:
        sucursales = obtener_sucursales_usuario(request.user)

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
def obtener_documentos_vendedor_reporte(request):
    """API para obtener el detalle de DTEs y Tickets de un vendedor en el período filtrado"""
    try:
        vendedor_id = request.GET.get('vendedor_id')
        sucursal_id = request.GET.get('sucursal_id')
        mes = request.GET.get('mes')
        fecha = request.GET.get('fecha')
        fecha_inicio_param = request.GET.get('fecha_inicio')
        fecha_fin_param = request.GET.get('fecha_fin')

        if not vendedor_id:
            return JsonResponse({'success': False, 'error': 'Se requiere vendedor_id'})

        # Determinar rango de fechas
        if fecha:
            fecha_inicio = datetime.strptime(fecha, '%Y-%m-%d')
            fecha_fin = fecha_inicio
        elif fecha_inicio_param and fecha_fin_param:
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            if not mes:
                mes = timezone.now().strftime('%Y-%m')
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)

        documentos = []

        # ---- DTEs (solo ventas al público, sin facturas entre sucursales) ----
        qs_dtes = Dte.objects.filter(
            vendedor_id=vendedor_id,
            fecha_emision__gte=fecha_inicio,
            fecha_emision__lte=fecha_fin,
            tipo_transaccion='VENTA_PUBLICO',
            estado_dte__in=['EMITIDO', 'PAGADO'],
        ).exclude(
            receptor__isnull=False,
            receptor_id=F('emisor_id')
        ).select_related('sucursal', 'receptor')

        qs_dtes = filtrar_queryset_por_sucursal(qs_dtes, request.user, request)

        for d in qs_dtes.order_by('-fecha_emision'):
            documentos.append({
                'origen': 'DTE',
                'tipo': d.tipo_documento or '',
                'numero': d.numero_documento or '',
                'fecha': d.fecha_emision.strftime('%d/%m/%Y') if d.fecha_emision else '',
                'cliente': d.receptor.razon_social if d.receptor else 'Público General',
                'sucursal': d.sucursal.alias if d.sucursal else '-',
                'monto': int(d.monto_con_iva or 0),
                'estado': d.estado_dte or '',
            })

        # ---- Tickets ----
        qs_tickets = Ticket.objects.filter(
            vendedor_id=vendedor_id,
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
            estado='PAGADO',
        ).select_related('sucursal')

        qs_tickets = filtrar_queryset_por_sucursal(qs_tickets, request.user, request)

        for t in qs_tickets.order_by('-created_at'):
            documentos.append({
                'origen': 'TICKET',
                'tipo': 'Ticket POS',
                'numero': str(t.id),
                'fecha': t.created_at.strftime('%d/%m/%Y') if t.created_at else '',
                'cliente': 'Venta Directa',
                'sucursal': t.sucursal.alias if t.sucursal else '-',
                'monto': int(t.total or 0),
                'estado': 'PAGADO',
            })

        # Ordenar todos por fecha descendente
        documentos.sort(key=lambda x: x['fecha'], reverse=True)

        return JsonResponse({
            'success': True,
            'documentos': documentos,
            'total': sum(d['monto'] for d in documentos),
            'cantidad': len(documentos),
        })

    except Exception as e:
        logger.exception("Error al obtener documentos para reporte")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def obtener_comparativa_mensual(request):
    """API para obtener datos de comparativa mensual de sucursales (últimos 6 meses)"""
    try:
        fecha_fin = timezone.now()
        fecha_inicio = fecha_fin - timedelta(days=180)

        sucursales_dict = {}
        meses_set = set()

        # ========== TICKETS (POS nuevo) ==========
        queryset_tickets = Ticket.objects.filter(
            created_at__gte=fecha_inicio,
            estado='PAGADO',
            modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        ).select_related('sucursal')

        queryset_tickets = filtrar_queryset_por_sucursal(queryset_tickets, request.user, request)

        tickets_mensuales = queryset_tickets.annotate(
            mes=TruncMonth('created_at')
        ).values(
            'mes',
            'sucursal__alias'
        ).annotate(
            total_ventas=Sum('total')
        ).order_by('mes', 'sucursal__alias')

        for item in tickets_mensuales:
            sucursal_nombre = item['sucursal__alias']
            mes_str = item['mes'].strftime('%Y-%m')
            meses_set.add(mes_str)
            if sucursal_nombre not in sucursales_dict:
                sucursales_dict[sucursal_nombre] = {}
            sucursales_dict[sucursal_nombre][mes_str] = \
                sucursales_dict[sucursal_nombre].get(mes_str, 0) + int(item['total_ventas'] or 0)

        # ========== DTEs (ventas al público, sin facturas entre sucursales) ==========
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fecha_inicio.date(),
            tipo_transaccion='VENTA_PUBLICO',
        ).exclude(
            estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
        ).exclude(
            receptor__isnull=False,
            receptor_id=F('emisor_id')
        ).select_related('sucursal')

        queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)

        # Ventas DTE (excluir Notas de Crédito)
        dtes_mensuales = queryset_dtes.exclude(
            tipo_documento='NOTA DE CREDITO'
        ).annotate(
            mes=TruncMonth('fecha_emision')
        ).values(
            'mes',
            'sucursal__alias'
        ).annotate(
            total_ventas=Sum('monto_con_iva')
        ).order_by('mes', 'sucursal__alias')

        for item in dtes_mensuales:
            sucursal_nombre = item['sucursal__alias']
            mes_str = item['mes'].strftime('%Y-%m')
            meses_set.add(mes_str)
            if sucursal_nombre not in sucursales_dict:
                sucursales_dict[sucursal_nombre] = {}
            sucursales_dict[sucursal_nombre][mes_str] = \
                sucursales_dict[sucursal_nombre].get(mes_str, 0) + int(item['total_ventas'] or 0)

        # Restar Notas de Crédito (devoluciones)
        nc_mensuales = queryset_dtes.filter(
            tipo_documento='NOTA DE CREDITO'
        ).annotate(
            mes=TruncMonth('fecha_emision')
        ).values(
            'mes',
            'sucursal__alias'
        ).annotate(
            total_nc=Sum('monto_con_iva')
        ).order_by('mes', 'sucursal__alias')

        for item in nc_mensuales:
            sucursal_nombre = item['sucursal__alias']
            mes_str = item['mes'].strftime('%Y-%m')
            meses_set.add(mes_str)
            if sucursal_nombre not in sucursales_dict:
                sucursales_dict[sucursal_nombre] = {}
            sucursales_dict[sucursal_nombre][mes_str] = \
                sucursales_dict[sucursal_nombre].get(mes_str, 0) - int(item['total_nc'] or 0)

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
    sucursal_activa_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    sucursal_actual = None
    if sucursal_activa_id:
        sucursal_actual = Sucursal.objects.filter(id=sucursal_activa_id).first()
    context = obtener_contexto_sucursales(request.user, request)
    context['sucursal_actual'] = sucursal_actual
    return render(request, 'vistas/modulo_reportes/documentos_emitidos.html', context)


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
        sucursal_param = (request.GET.get('sucursal_id') or '').strip()

        # Si no se proporcionan fechas, usar el día actual
        if not fecha_desde or not fecha_hasta:
            fecha_fin = timezone.localdate()
            fecha_desde = fecha_fin
            fecha_hasta = fecha_fin
        else:
            fecha_desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()

        # Consultar DTEs (Boletas y Facturas Electrónicas)
        # NOTA: Incluimos facturas entre sucursales (receptor=emisor) para coincidir
        # con el comportamiento de /app/ventas/documentos/.
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

        # --- Filtro de sucursal con semántica explícita ---
        # Reglas:
        #   - sucursal_param == 'all'  → sin filtro (traer todas las sucursales accesibles)
        #   - sucursal_param == '<id>' → filtrar por esa sucursal (si tiene permiso)
        #   - sucursal_param vacío     → usar sucursal activa de la sesión (default seguro
        #                                que coincide con /app/ventas/documentos/)
        sucursal_sesion_id = (
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
        )
        filtro_sucursal_desc = ''
        sucursal_ids_permitidas = list(
            obtener_sucursales_usuario(request.user).values_list('id', flat=True)
        )

        if sucursal_param == 'all':
            if usuario_puede_ver_todas_sucursales(request.user):
                queryset = queryset.filter(sucursal_id__in=sucursal_ids_permitidas) \
                    if sucursal_ids_permitidas else queryset.none()
                filtro_sucursal_desc = 'Todas las sucursales accesibles'
            else:
                # Usuario no puede ver todas → se restringe a las asignadas
                queryset = queryset.filter(sucursal_id__in=sucursal_ids_permitidas) \
                    if sucursal_ids_permitidas else queryset.none()
                filtro_sucursal_desc = 'Sucursales asignadas al usuario'
        elif sucursal_param:
            # Validar permiso sobre esa sucursal específica
            if puede_ver_sucursal(request.user, sucursal_param):
                queryset = queryset.filter(sucursal_id=sucursal_param)
                filtro_sucursal_desc = f'Sucursal específica (id={sucursal_param})'
            else:
                # Fallback: sucursal de sesión si la tiene
                if sucursal_sesion_id:
                    queryset = queryset.filter(sucursal_id=sucursal_sesion_id)
                    filtro_sucursal_desc = (
                        f'Sin permiso para id={sucursal_param}, se usó sucursal de sesión'
                    )
                else:
                    queryset = queryset.none()
                    filtro_sucursal_desc = 'Sin permiso y sin sucursal de sesión'
        else:
            # Default: sucursal activa de la sesión
            if sucursal_sesion_id:
                queryset = queryset.filter(sucursal_id=sucursal_sesion_id)
                filtro_sucursal_desc = f'Sucursal activa de sesión (id={sucursal_sesion_id})'
            else:
                # Si no hay sesión, restringir a las sucursales del usuario
                queryset = queryset.filter(sucursal_id__in=sucursal_ids_permitidas) \
                    if sucursal_ids_permitidas else queryset.none()
                filtro_sucursal_desc = 'Sin sucursal de sesión, se usaron las accesibles'

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
        total_real = queryset.count()
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

            # Monto efectivamente pagado = suma de pagos registrados
            pagado = sum(pagos_detalle.values())
            if not pagado:
                # Fallback: monto_con_iva - descuento si no hay pagos
                pagado = int(dte.monto_con_iva) - (int(dte.descuento) if dte.descuento else 0)

            # Descuento real: preferir dte.descuento; si es 0 pero pagado < total, inferirlo
            descuento_dte = int(dte.descuento) if dte.descuento else 0
            if descuento_dte == 0 and pagado > 0:
                calculado = int(dte.monto_con_iva) - pagado
                if calculado > 0:
                    descuento_dte = calculado

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
                'descuento': descuento_dte,
                'pagado': pagado,
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
            'notas_credito': 0,   # total de NC (se resta del global)
            'ventas_brutas': 0,   # suma bruta sin NC ni descuentos
            'total_global': 0     # ventas_brutas - notas_credito - descuentos
        }
        
        # --- Helper: clasifica un método de pago en la categoría del resumen ---
        # Importante: el orden de verificación evita colisiones (ej. CREDITO_TRABAJADOR
        # debe detectarse ANTES que CREDITO genérico de tarjeta).
        def clasificar_metodo(metodo_raw):
            m = (metodo_raw or '').upper()
            if not m:
                return 'otros'
            # Específicos primero
            if 'TRABAJADOR' in m:
                return 'credito_trabajador'
            if 'INTERNET' in m or 'WEB' in m:
                return 'venta_internet'
            if 'TRANSFERENCIA' in m:
                return 'transferencia'
            if 'CONVENIO' in m:
                return 'convenio'
            if 'COMERCIAL' in m or 'PARIS' in m or 'RIPLEY' in m or 'FALABELLA' in m:
                return 'tarjeta_comercial'
            if 'EFECTIVO' in m:
                return 'efectivo'
            # Tarjetas TBK/bancarias
            if 'DEBITO' in m or 'REDCOMPRA' in m:
                return 'tbk_debito'
            if ('CREDITO' in m or 'VISA' in m or 'MASTERCARD' in m
                    or 'AMEX' in m or 'DINER' in m):
                return 'tbk_credito'
            # Otros (CHEQUE, OTRO, ORDEN_COMPRA, TBK_MANUAL, TBK_PREPAGO_POS,
            # CREDITO_EXTERNO sin TRABAJADOR, etc.)
            return 'otros'

        # --- Diagnóstico: contadores para cuadrar con /app/ventas/documentos/ ---
        diag_conteo_por_tipo = {}
        diag_sucursales = {}          # {sucursal_id: {'alias': ..., 'cantidad': N, 'monto': $}}
        diag_cant_dtes = 0            # DTEs "normales" (no NC) que aportan a ventas brutas
        diag_cant_nc = 0              # Notas de crédito
        diag_sin_pagos = 0            # DTEs sin registros en dte_asociado

        # Calcular totales por método de pago desde Dte_Detalle_Pago
        for dte in queryset:
            total = int(dte.monto_con_iva or 0)
            descuento_db = int(dte.descuento) if dte.descuento else 0
            tipo_doc_upper = dte.tipo_documento.upper() if dte.tipo_documento else ''

            # --- Diagnóstico: conteos por tipo y sucursal ---
            tipo_key = dte.tipo_documento or 'SIN_TIPO'
            if tipo_key not in diag_conteo_por_tipo:
                diag_conteo_por_tipo[tipo_key] = {'cantidad': 0, 'monto': 0}
            diag_conteo_por_tipo[tipo_key]['cantidad'] += 1
            diag_conteo_por_tipo[tipo_key]['monto'] += total

            suc_id = dte.sucursal_id
            if suc_id not in diag_sucursales:
                suc_alias = getattr(dte.sucursal, 'alias', None) or getattr(dte.sucursal, 'nombreSucursal', '') or f'ID {suc_id}'
                diag_sucursales[suc_id] = {
                    'id': suc_id,
                    'alias': suc_alias,
                    'cantidad': 0,
                    'monto': 0,
                }
            diag_sucursales[suc_id]['cantidad'] += 1
            diag_sucursales[suc_id]['monto'] += total

            # Las Notas de Crédito se acumulan aparte y se restan del total
            if 'NOTA' in tipo_doc_upper and 'CREDITO' in tipo_doc_upper:
                resumen['notas_credito'] += total
                diag_cant_nc += 1
                continue

            diag_cant_dtes += 1

            # Obtener métodos de pago del DTE usando la relación inversa
            detalles_pago = dte.dte_asociado.all()
            pagado_sum = 0

            if detalles_pago.exists():
                for detalle in detalles_pago:
                    monto = int(detalle.monto or 0)
                    pagado_sum += monto
                    categoria = clasificar_metodo(detalle.metodo_pago)
                    resumen[categoria] = resumen.get(categoria, 0) + monto
            else:
                diag_sin_pagos += 1

            # --- Cálculo de descuento unificado con /app/ventas/documentos/ ---
            # Prioridad:
            #   1) dte.descuento si existe y es coherente
            #   2) diferencia entre monto_con_iva y pagos (si hay pagos y son menores)
            # Esto evita sobreestimar descuentos cuando los pagos ya reflejan el neto.
            if descuento_db > 0:
                descuento = descuento_db
            elif pagado_sum > 0 and pagado_sum < total:
                descuento = total - pagado_sum
            else:
                descuento = 0

            resumen['descuentos'] += descuento
            resumen['ventas_brutas'] += total

            # Si no se procesó método de pago (no había registros en dte_asociado),
            # buscar en el ticket relacionado como fallback.
            if not detalles_pago.exists():
                ticket_relacionado = Ticket.objects.filter(
                    correlativo=dte.numero_documento,
                    sucursal=dte.sucursal
                ).first()

                if ticket_relacionado:
                    metodo_ticket = ticket_relacionado.metodo_pago

                    if metodo_ticket == 'TBK_POS_INTEGRADO':
                        # Verificar tipo de tarjeta en detalles
                        pago_ticket = TicketDetallePago.objects.filter(
                            ticket=ticket_relacionado
                        ).first()
                        if pago_ticket and pago_ticket.tipo_tarjeta and \
                                'DEBITO' in pago_ticket.tipo_tarjeta.upper():
                            resumen['tbk_debito'] += total
                        elif pago_ticket and pago_ticket.tipo_tarjeta:
                            resumen['tbk_credito'] += total
                        else:
                            resumen['tbk_debito'] += total  # default
                    else:
                        categoria = clasificar_metodo(metodo_ticket)
                        resumen[categoria] = resumen.get(categoria, 0) + total
                else:
                    # Si no hay ticket ni detalle de pago, asumir efectivo
                    resumen['efectivo'] += total

        # Total neto = ventas brutas - notas de crédito - descuentos
        resumen['total_global'] = resumen['ventas_brutas'] - resumen['notas_credito'] - resumen['descuentos']

        # --- Bloque de diagnóstico (para cuadrar con /app/ventas/documentos/) ---
        # Permite identificar rápidamente discrepancias: universo de sucursales,
        # tipos de documento incluidos, DTEs sin pagos registrados, etc.
        sucursales_incluidas = sorted(
            diag_sucursales.values(),
            key=lambda s: s.get('monto', 0),
            reverse=True,
        )
        conteo_por_tipo = [
            {'tipo': t, 'cantidad': v['cantidad'], 'monto': v['monto']}
            for t, v in sorted(
                diag_conteo_por_tipo.items(),
                key=lambda kv: kv[1]['monto'],
                reverse=True,
            )
        ]
        # Equivalente al 'total_ventas' que muestra /app/ventas/documentos/:
        # ventas brutas - descuentos (sin restar NC, tal como lo hace ventas).
        total_equivalente_ventas_doc = resumen['ventas_brutas'] - resumen['descuentos']

        diagnostico = {
            'filtro_sucursal_aplicado': filtro_sucursal_desc,
            'sucursal_param_recibido': sucursal_param or '(vacío)',
            'sucursal_sesion_id': sucursal_sesion_id,
            'fecha_desde': str(fecha_desde),
            'fecha_hasta': str(fecha_hasta),
            'sucursales_incluidas': sucursales_incluidas,
            'cantidad_sucursales_incluidas': len(sucursales_incluidas),
            'conteo_por_tipo': conteo_por_tipo,
            'cantidad_dtes': diag_cant_dtes,
            'cantidad_notas_credito': diag_cant_nc,
            'dtes_sin_pagos_registrados': diag_sin_pagos,
            # Totales útiles para cuadrar entre vistas
            'total_bruto_sin_descuento': resumen['ventas_brutas'],
            'total_equivalente_ventas_documentos': total_equivalente_ventas_doc,
            'total_neto': resumen['total_global'],
        }

        return JsonResponse({
            'success': True,
            'documentos': documentos_data,
            'resumen': resumen,
            'diagnostico': diagnostico,
            'total_registros': len(documentos_data),
            'total_real': total_real,
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
        sucursal_param = (request.GET.get('sucursal_id') or '').strip()

        # Si no se proporcionan fechas, usar el día actual
        if not fecha_desde or not fecha_hasta:
            fecha_fin = timezone.localdate()
            fecha_desde = fecha_fin
            fecha_hasta = fecha_fin
        else:
            fecha_desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()

        # Consultar DTEs (Boletas y Facturas Electrónicas)
        # NOTA: Sin exclusión de facturas internas (receptor=emisor) para que el
        # Excel coincida con /app/ventas/documentos/ y con la vista en pantalla.
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

        # Filtro de sucursal con la misma semántica que obtener_documentos_emitidos:
        #   'all' = todas, vacío = sucursal de sesión, id = específica.
        sucursal_sesion_id = (
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
        )
        sucursal_ids_permitidas = list(
            obtener_sucursales_usuario(request.user).values_list('id', flat=True)
        )

        if sucursal_param == 'all':
            queryset = queryset.filter(sucursal_id__in=sucursal_ids_permitidas) \
                if sucursal_ids_permitidas else queryset.none()
        elif sucursal_param:
            if puede_ver_sucursal(request.user, sucursal_param):
                queryset = queryset.filter(sucursal_id=sucursal_param)
            elif sucursal_sesion_id:
                queryset = queryset.filter(sucursal_id=sucursal_sesion_id)
            else:
                queryset = queryset.none()
        else:
            if sucursal_sesion_id:
                queryset = queryset.filter(sucursal_id=sucursal_sesion_id)
            else:
                queryset = queryset.filter(sucursal_id__in=sucursal_ids_permitidas) \
                    if sucursal_ids_permitidas else queryset.none()

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
            pagado = total - descuento

            ws.append([
                dte.id,
                dte.tipo_documento,
                metodo_pago_display,
                dte.numero_documento,
                cliente_info,
                total,
                descuento,
                pagado,
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
        
        # 'todas' indica explícitamente todas las sucursales del usuario (no aplicar fallback a sesión)
        todas_sucursales = (str(sucursal_id).lower() == 'todas') if sucursal_id else False
        if todas_sucursales:
            sucursal_id = None

        # Obtener sucursal actual del usuario si no se especifica (y no se pidió 'todas')
        if not sucursal_id and not todas_sucursales:
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
        # Si hay sucursal específica, solo esa; si se pidió 'todas' o no hay sesión, todas las del usuario
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
            sucursal_id__in=sucursales_ids,
            excluir_de_analitica=False
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
        
        # ========== PROCESAR DATOS (PIVOT POR SKU) ==========
        # Cada producto en BD pertenece a 1 sucursal. Aquí agrupamos por
        # (articulo, marca, color, departamento) y armamos un dict
        # stock_por_sucursal: {sucursal_id: stock} con TODAS las sucursales del usuario.
        productos_lista = list(queryset)
        productos_procesados = len(productos_lista)

        # Prefetch de tallas para los productos filtrados (stock real por producto)
        productos_ids = [p.id for p in productos_lista]
        tallas_por_producto = {}
        if productos_ids:
            from django.db.models import Sum as DjangoSum
            tallas = Producto_Talla.objects.filter(
                producto_id__in=productos_ids
            ).values('producto_id').annotate(
                stock_total=DjangoSum('stock')
            )
            tallas_por_producto = {t['producto_id']: t['stock_total'] or 0 for t in tallas}

        # Agrupar productos por (articulo, marca_id, atributo2_id, categoria_id)
        # para fusionar variantes que existen en distintas sucursales en una sola fila.
        agrupados = {}
        for producto in productos_lista:
            stock_total_producto = tallas_por_producto.get(producto.id, 0)
            clave = (
                producto.articulo,
                producto.atributo1_id,
                producto.atributo2_id,
                producto.categoria_id,
            )
            if clave not in agrupados:
                agrupados[clave] = {
                    'articulo': producto.articulo,
                    'marca': producto.atributo1.valor if producto.atributo1 else 'Sin Marca',
                    'marca_id': producto.atributo1.id if producto.atributo1 else None,
                    'color': producto.atributo2.valor if producto.atributo2 else '-',
                    'departamento': producto.categoria.nombre if producto.categoria else '-',
                    'costo': float(producto.costo) if producto.costo else 0,
                    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                    'stock_por_sucursal': {},  # {sucursal_id: stock}
                    'sucursales': {},          # {alias: {stock, sucursal_id}} — retrocompat
                    'total_stock': 0,
                }

            fila = agrupados[clave]
            if producto.sucursal_id:
                # Si el SKU aparece varias veces en la misma sucursal (no debería),
                # sumamos para no perder stock.
                key_id = str(producto.sucursal_id)
                fila['stock_por_sucursal'][key_id] = (
                    fila['stock_por_sucursal'].get(key_id, 0) + stock_total_producto
                )
                if producto.sucursal:
                    fila['sucursales'][producto.sucursal.alias] = {
                        'stock': fila['sucursales'].get(producto.sucursal.alias, {}).get('stock', 0) + stock_total_producto,
                        'sucursal_id': producto.sucursal_id,
                    }
            fila['total_stock'] += stock_total_producto

        # Aplicar filtro solo_con_stock sobre el TOTAL agrupado
        datos_reporte = [
            fila for fila in agrupados.values()
            if (not solo_con_stock) or fila['total_stock'] > 0
        ]

        # Ordenar por marca, luego artículo
        datos_reporte.sort(key=lambda f: (f.get('marca') or '', f.get('articulo') or ''))
        
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
        logger.exception("Error en reporte de existencias por marca")
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
            
            # Subencabezados (Stock)
            headers_row2 = ['', '', '', '', '']  # Columnas fijas sin subencabezado
            for sucursal in sucursales_con_stock:
                headers_row2.extend(['Stock', 'Stock'])
            headers_row2.extend(['Stock', 'Stock'])
            
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
            totales_por_sucursal = {suc['alias']: {'stock': 0} for suc in sucursales_con_stock}
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
                        stock_val = stock_suc.get('stock', 0)
                        row_data.append(stock_val)
                        row_data.append(stock_val)
                        # Acumular totales
                        totales_por_sucursal[sucursal['alias']]['stock'] += stock_val
                    else:
                        row_data.append('-')
                        row_data.append('-')

                # Totales del producto
                total_stock_prod = producto.get('total_stock', 0)
                row_data.append(total_stock_prod)
                row_data.append(total_stock_prod)
                gran_total_stock += total_stock_prod
                
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
                stock_val = total_suc['stock']

                cell = ws.cell(row=fila_actual, column=col_idx, value=stock_val)
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='right')
                cell.border = border
                col_idx += 1

                cell = ws.cell(row=fila_actual, column=col_idx, value=stock_val)
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='right')
                cell.border = border
                col_idx += 1

            # Gran total
            cell = ws.cell(row=fila_actual, column=col_idx, value=gran_total_stock)
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
        logger.exception("Error al exportar reporte de existencias por marca a Excel")
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
        sucursal_id = request.GET.get('sucursal_id')
        marca_id = request.GET.get('marca_id')
        incluir_sin_stock = request.GET.get('incluir_sin_stock', 'false').lower() == 'true'

        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'Sucursal es requerida'})

        # Validar que el usuario tenga acceso a esta sucursal
        empresas_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True
        ).values_list('empresa_id', flat=True)
        sucursal = get_object_or_404(Sucursal, id=sucursal_id, empresa_id__in=empresas_usuario)

        # Obtener productos de esta sucursal con sus tallas en un solo queryset
        queryset = Producto.objects.filter(
            sucursal_id=sucursal_id,
            excluir_de_analitica=False
        ).select_related(
            'atributo1', 'atributo2', 'atributo3', 'categoria'
        ).prefetch_related('producto_talla')

        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)

        # Pre-cargar total recibido desde Movimientos_Producto (todos los ingresos reales)
        # La FK en Movimientos_Producto se llama "ProductoTalla" (mayúsculas)
        talla_ids = list(
            Producto_Talla.objects.filter(
                producto__sucursal_id=sucursal_id,
                **({'producto__atributo1_id': marca_id} if marca_id else {})
            ).values_list('id', flat=True)
        )

        from django.db.models import Sum as _Sum
        conceptos_ingreso = [
            'INGRESO_INICIAL', 'INGRESO_MANUAL', 'RECEPCION_COMPRA',
            'REPOSICION_STOCK', 'TRASPASO_ENTRADA', 'CAMBIO_PRODUCTO_ENTRADA',
            'AJUSTE_POSITIVO', 'AJUSTE_INVENTARIO_ENTRADA',
        ]
        stocks_iniciales_qs = (
            Movimientos_Producto.objects.filter(
                ProductoTalla_id__in=talla_ids,
                concepto__in=conceptos_ingreso,
                estado='COMPLETADO',
            )
            .values('ProductoTalla_id')
            .annotate(total=_Sum('cantidad'))
        )
        stocks_iniciales_map = {
            row['ProductoTalla_id']: row['total']
            for row in stocks_iniciales_qs
        }

        datos_reporte = []
        total_stock = 0
        valor_inventario = 0
        skus_con_stock = 0
        productos_sin_stock = 0

        for producto in queryset:
            # Costo de adquisicion: para vendedoras incluye sobreprecio (precio interno)
            costo_unitario = (producto.costo or 0) + (producto.sobreprecio or 0)

            for talla in producto.producto_talla.all():
                stock = talla.stock or 0

                if stock <= 0:
                    productos_sin_stock += 1
                    if not incluir_sin_stock:
                        continue
                else:
                    skus_con_stock += 1
                    total_stock += stock
                    valor_inventario += costo_unitario * stock

                stock_inicial = stocks_iniciales_map.get(talla.id, 0) or 0

                datos_reporte.append({
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion or '-',
                    'categoria': producto.categoria.nombre if producto.categoria else '-',
                    'marca': producto.atributo1.valor if producto.atributo1 else '-',
                    'color': producto.atributo2.valor if producto.atributo2 else '-',
                    'genero': producto.atributo3.valor if producto.atributo3 else '-',
                    'talla': talla.talla if talla.talla else '-',
                    'sku': str(talla.sku),
                    'stock_inicial': stock_inicial,
                    'stock': stock,
                    'costo': float(producto.costo or 0),
                    'sobreprecio': float(producto.sobreprecio) if producto.sobreprecio else 0,
                    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
                    'sin_stock': stock <= 0,
                })

        resumen = {
            'total_productos': skus_con_stock,
            'stock_total': total_stock,
            'valor_inventario': valor_inventario,
            'sin_stock': productos_sin_stock,
            'sucursal': sucursal.alias or sucursal.nombre or f'Sucursal {sucursal.id}',
        }

        return JsonResponse({'success': True, 'datos': datos_reporte, 'resumen': resumen})

    except Exception as e:
        logger.exception("Error en reporte de existencias por sucursal")
        return JsonResponse({'success': False, 'error': f'Error al obtener reporte: {str(e)}'})


def _get_existencias_datos(request):
    """Helper que devuelve (datos_reporte, resumen, error) para exportaciones."""
    response_data = obtener_reporte_existencias_sucursal(request)
    datos = json.loads(response_data.content)
    if not datos.get('success'):
        return None, None, datos.get('error', 'Error desconocido')
    return datos.get('datos', []), datos.get('resumen', {}), None


@require_GET
@login_required
def exportar_existencias_sucursal_excel(request):
    """Exportar reporte de existencias por sucursal a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        if not request.GET.get('sucursal_id'):
            return JsonResponse({'success': False, 'error': 'Sucursal es requerida'})

        datos_reporte, resumen, error = _get_existencias_datos(request)
        if error:
            return JsonResponse({'success': False, 'error': error})
        if not datos_reporte:
            return JsonResponse({'success': False, 'error': 'No hay datos para exportar'})

        nombre_sucursal = resumen.get('sucursal', 'Sucursal')

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Exist. {nombre_sucursal}"[:31]

        verde = "1E7E34"
        verde_claro = "28A745"
        rojo_claro = "FFF0F0"
        header_fill = PatternFill(start_color=verde, end_color=verde, fill_type="solid")
        subheader_fill = PatternFill(start_color=verde_claro, end_color=verde_claro, fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # Fila 1: Título
        ws.merge_cells('A1:M1')
        cell = ws['A1']
        cell.value = f"REPORTE DE EXISTENCIAS — {nombre_sucursal.upper()}"
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF", size=14)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        # Fila 2: Fecha
        ws.merge_cells('A2:M2')
        from datetime import date as _date
        ws['A2'].value = f"Generado: {timezone.localdate().strftime('%d/%m/%Y')}   |   Total registros: {resumen.get('total_productos', 0)}   |   Stock total: {resumen.get('stock_total', 0):,}"
        ws['A2'].font = Font(italic=True, size=10, color="444444")
        ws['A2'].alignment = Alignment(horizontal='center')

        # Fila 3: Encabezados
        headers = [
            'SKU', 'Artículo', 'Descripción', 'Categoría', 'Marca', 'Color', 'Género',
            'Talla', 'Stock Inicial', 'Stock Actual', 'Costo', 'Sobreprecio', 'Precio Venta'
        ]
        for idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=3, column=idx, value=header)
            cell.fill = subheader_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
        ws.row_dimensions[3].height = 20

        # Datos
        for fila_idx, item in enumerate(datos_reporte, start=4):
            sin_stock = item.get('sin_stock', False)
            row_fill = PatternFill(start_color="FFF0F0", end_color="FFF0F0", fill_type="solid") if sin_stock else None

            def _cell(col, val, align='left'):
                c = ws.cell(row=fila_idx, column=col, value=val)
                c.border = border
                c.alignment = Alignment(horizontal=align)
                if row_fill:
                    c.fill = row_fill
                return c

            _cell(1, item['sku'])
            _cell(2, item['articulo'])
            _cell(3, item['descripcion'])
            _cell(4, item['categoria'])
            _cell(5, item['marca'])
            _cell(6, item['color'])
            _cell(7, item['genero'])
            _cell(8, item['talla'], 'center')
            c = _cell(9, item['stock_inicial'], 'right')
            c.number_format = '#,##0'
            c = _cell(10, item['stock'], 'right')
            c.number_format = '#,##0'
            if sin_stock:
                c.font = Font(color="CC0000", bold=True)
            c = _cell(11, item['costo'], 'right')
            c.number_format = '$#,##0'
            c = _cell(12, item['sobreprecio'], 'right')
            c.number_format = '$#,##0'
            c = _cell(13, item['precio_venta'], 'right')
            c.number_format = '$#,##0'

        # Anchos
        col_widths = [14, 22, 32, 16, 16, 14, 12, 8, 12, 12, 12, 12, 14]
        for i, w in enumerate(col_widths, start=1):
            from openpyxl.utils import get_column_letter
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = 'A4'

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        safe_name = nombre_sucursal.replace(' ', '_').replace('/', '-')
        response['Content-Disposition'] = f'attachment; filename="existencias_{safe_name}.xlsx"'
        wb.save(response)
        return response

    except Exception as e:
        logger.exception("Error al exportar existencias por sucursal a Excel")
        return JsonResponse({'success': False, 'error': f'Error al exportar: {str(e)}'})


@require_GET
@login_required
def exportar_existencias_sucursal_pdf(request):
    """Exportar reporte de existencias por sucursal a PDF usando ReportLab"""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from io import BytesIO

        if not request.GET.get('sucursal_id'):
            return JsonResponse({'success': False, 'error': 'Sucursal es requerida'})

        datos_reporte, resumen, error = _get_existencias_datos(request)
        if error:
            return JsonResponse({'success': False, 'error': error})
        if not datos_reporte:
            return JsonResponse({'success': False, 'error': 'No hay datos para exportar'})

        nombre_sucursal = resumen.get('sucursal', 'Sucursal')
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        verde = colors.HexColor('#1E7E34')
        verde_claro = colors.HexColor('#28A745')
        gris_claro = colors.HexColor('#F8F9FA')
        rojo_claro = colors.HexColor('#FFE0E0')

        title_style = ParagraphStyle('Title', parent=styles['Title'], fontSize=16, textColor=verde, spaceAfter=4)
        sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=12)

        elements = []
        from datetime import date as _date
        elements.append(Paragraph(f"Reporte de Existencias — {nombre_sucursal}", title_style))
        elements.append(Paragraph(
            f"Generado: {timezone.localdate().strftime('%d/%m/%Y')}  |  "
            f"Total SKUs: {resumen.get('total_productos', 0):,}  |  "
            f"Stock total: {resumen.get('stock_total', 0):,}  |  "
            f"Valor inventario: ${resumen.get('valor_inventario', 0):,.0f}",
            sub_style
        ))

        # Tabla
        col_labels = ['Artículo', 'Descripción', 'Marca', 'Color', 'Género', 'Talla',
                      'Stock Ini.', 'Stock Act.', 'Costo', 'Precio V.']
        col_widths_pdf = [3.5*cm, 6*cm, 3*cm, 2.8*cm, 2.5*cm, 1.8*cm,
                          2*cm, 2.2*cm, 2.5*cm, 2.5*cm]

        table_data = [col_labels]
        for item in datos_reporte:
            row = [
                item['articulo'],
                item['descripcion'][:45] + ('…' if len(item['descripcion']) > 45 else ''),
                item['marca'],
                item['color'],
                item['genero'],
                item['talla'],
                f"{item['stock_inicial']:,}",
                f"{item['stock']:,}",
                f"${item['costo']:,.0f}",
                f"${item['precio_venta']:,.0f}",
            ]
            table_data.append(row)

        table = Table(table_data, colWidths=col_widths_pdf, repeatRows=1)

        row_count = len(table_data)
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), verde),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DEE2E6')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, gris_claro]),
            ('ALIGN', (6, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]
        # Resaltar filas sin stock
        for row_idx, item in enumerate(datos_reporte, start=1):
            if item.get('sin_stock'):
                style_cmds.append(('BACKGROUND', (0, row_idx), (-1, row_idx), rojo_claro))
                style_cmds.append(('TEXTCOLOR', (7, row_idx), (7, row_idx), colors.red))

        table.setStyle(TableStyle(style_cmds))
        elements.append(table)

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont('Helvetica', 7)
            canvas.setFillColor(colors.grey)
            canvas.drawString(1.5*cm, 1*cm, f"RetailMind — {nombre_sucursal} — {timezone.localdate().strftime('%d/%m/%Y')}")
            canvas.drawRightString(landscape(A4)[0] - 1.5*cm, 1*cm, f"Página {doc.page}")
            canvas.restoreState()

        doc.build(elements, onFirstPage=footer, onLaterPages=footer)
        buffer.seek(0)

        response = HttpResponse(content_type='application/pdf')
        safe_name = nombre_sucursal.replace(' ', '_').replace('/', '-')
        response['Content-Disposition'] = f'attachment; filename="existencias_{safe_name}.pdf"'
        response.write(buffer.read())
        return response

    except Exception as e:
        logger.exception("Error al exportar existencias por sucursal a PDF")
        return JsonResponse({'success': False, 'error': f'Error al exportar PDF: {str(e)}'})


# ========== REPORTE DE COMPRAS INTEGRAL ==========

@login_required
def ver_reporte_compras(request):
    """Vista principal del reporte de compras"""
    return render(request, 'vistas/modulo_reportes/reporte_compras.html')


# ========== REPORTE: PRODUCTOS CREADOS POR ORIGEN (gap H4) ==========
# El ORIGEN de un SKU = el concepto de su PRIMER movimiento de INGRESO.
# Permite ver, de forma agregada, cuántos SKU nacieron por compra vs alta
# manual vs traspaso vs ajuste, en lugar de auditar SKU por SKU.

ORIGEN_LABELS = {
    'RECEPCION_COMPRA': 'Recepción de compra',
    'INGRESO_INICIAL': 'Alta inicial / saldo',
    'INGRESO_MANUAL': 'Alta manual',
    # En datos migrados de Laravel el primer ingreso del SKU a la tienda suele ser
    # el despacho interno bodega→tienda, registrado como TRASPASO_SUCURSAL (INGRESO).
    'TRASPASO_SUCURSAL': 'Despacho interno (traspaso)',
    'TRASPASO_ENTRADA': 'Traspaso desde otra sucursal',
    'REPOSICION_STOCK': 'Reposición',
    'AJUSTE_POSITIVO': 'Ajuste de inventario',
    'AJUSTE_INVENTARIO': 'Ajuste de inventario',
    'DEVOLUCION_CLIENTE': 'Devolución de cliente',
}


@login_required
def ver_reporte_productos_origen(request):
    """Vista principal: productos creados en el período clasificados por origen."""
    return render(request, 'vistas/modulo_reportes/productos_por_origen.html')


@login_required
@require_GET
def api_productos_por_origen(request):
    """
    Altas de catálogo del período, clasificadas por el ORIGEN del SKU = el
    concepto de su PRIMER movimiento de INGRESO. Responde el gap H4 de la
    auditoría de trazabilidad.

    GET params:
        anio      — año a analizar (default = actual)
        sucursal  — id de sucursal (opcional)
    """
    from django.db.models import Min
    from .utils_analitica import ids_producto_talla_activos

    try:
        anio = int(request.GET.get('anio', timezone.localdate().year))
    except (TypeError, ValueError):
        anio = timezone.localdate().year
    sucursal_id = request.GET.get('sucursal') or None

    pt_ids = ids_producto_talla_activos(sucursal_id=sucursal_id)

    base = Movimientos_Producto.objects.filter(
        tipo_movimiento='INGRESO', estado='COMPLETADO',
        ProductoTalla_id__in=pt_ids,
    )

    # 1) Primer ingreso (fecha mínima) por SKU
    primeras = {
        r['ProductoTalla_id']: r['primera']
        for r in base.values('ProductoTalla_id').annotate(primera=Min('fecha'))
    }
    # SKU cuyo primer ingreso cae en el año pedido = "creados" ese año
    pt_anio = [pt for pt, f in primeras.items() if f and f.year == anio]

    # 2) Concepto / costo / cantidad del PRIMER movimiento de esos SKU.
    #    Recorremos ordenado por (SKU, fecha, hora, id) y tomamos el primero por SKU.
    por_origen = defaultdict(lambda: {'skus': 0, 'unidades': 0, 'costo': 0.0})
    por_mes = defaultdict(lambda: {'skus': 0, 'unidades': 0})
    vistos = set()
    total_skus = total_unidades = 0
    total_costo = 0.0

    if pt_anio:
        movs = (
            base.filter(ProductoTalla_id__in=pt_anio)
            .values('ProductoTalla_id', 'fecha', 'hora', 'concepto', 'cantidad', 'costo')
            .order_by('ProductoTalla_id', 'fecha', 'hora', 'id')
        )
        for m in movs.iterator(chunk_size=5000):
            pt = m['ProductoTalla_id']
            if pt in vistos:
                continue   # ya tomamos su primer movimiento
            vistos.add(pt)
            uds = abs(int(m['cantidad'] or 0))
            costo = float(m['costo'] or 0) * uds
            o = por_origen[m['concepto'] or 'OTRO']
            o['skus'] += 1
            o['unidades'] += uds
            o['costo'] += costo
            pm = por_mes[m['fecha'].month if m['fecha'] else 0]
            pm['skus'] += 1
            pm['unidades'] += uds
            total_skus += 1
            total_unidades += uds
            total_costo += costo

    por_origen_data = sorted((
        {
            'concepto': concepto,
            'origen': ORIGEN_LABELS.get(concepto, 'Otro'),
            'skus': v['skus'],
            'unidades': v['unidades'],
            'costo': round(v['costo'], 0),
            'pct': round(v['skus'] / total_skus * 100, 1) if total_skus else 0,
        }
        for concepto, v in por_origen.items()
    ), key=lambda x: x['skus'], reverse=True)

    por_mes_data = [
        {'mes': mm, 'skus': por_mes.get(mm, {}).get('skus', 0),
         'unidades': por_mes.get(mm, {}).get('unidades', 0)}
        for mm in range(1, 13)
    ]

    anios = sorted({f.year for f in primeras.values() if f}, reverse=True)
    anio_actual = timezone.localdate().year
    if anio_actual not in anios:
        anios.insert(0, anio_actual)

    return JsonResponse({
        'success': True,
        'anio': anio,
        'resumen': {
            'total_skus': total_skus,
            'total_unidades': total_unidades,
            'total_costo': round(total_costo, 0),
        },
        'por_origen': por_origen_data,
        'por_mes': por_mes_data,
        'anios_disponibles': anios,
    })


@login_required
@require_GET
def api_reporte_compras(request):
    """
    API completa para el reporte de compras.
    Comportamiento según tipo de sucursal activa:
    - CENTRO_DISTRIBUCION (EDEL): lee DTEs tipo_transaccion='COMPRA' (compras a proveedores externos)
    - VENDEDORA: lee DTEs tipo_transaccion='TRASPASO' donde receptor=empresa_sucursal (recibido de EDEL)
    """
    try:
        from .models import (
            Compras, Compras_Producto, Compras_Producto_Talla,
            Productos_Recepcionados, Dte, Dte_Productos, Sucursal, Empresa
        )
        
        # Parámetros de filtro
        anio = int(request.GET.get('anio', timezone.localdate().year))
        periodo = request.GET.get('periodo', 'anual')
        proveedor_id = request.GET.get('proveedor', '')
        temporada = request.GET.get('temporada', '')

        # ── Detectar tipo de sucursal activa ──────────────────────────────
        sucursal_activa_id = request.session.get('idSucursalActual')
        sucursal_activa = None
        es_vendedora = False
        empresa_receptora_id = None  # Para sucursales vendedoras

        if sucursal_activa_id:
            try:
                sucursal_activa = Sucursal.objects.select_related('empresa').get(id=sucursal_activa_id)
                es_vendedora = sucursal_activa.es_solo_vendedora
                if es_vendedora:
                    empresa_receptora_id = sucursal_activa.empresa_id
            except Sucursal.DoesNotExist:
                pass

        # Construir contexto de modo para los helpers
        modo_ctx = {
            'es_vendedora': es_vendedora,
            'empresa_receptora_id': empresa_receptora_id,
            'sucursal_activa_id': sucursal_activa_id,
        }
        # ── fin detección ─────────────────────────────────────────────────
        
        # Calcular rango de fechas según período
        hoy = timezone.localtime()
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
        metricas = calcular_metricas_compras(anio, periodo, proveedor_id, temporada, modo_ctx)
        
        # ===== EVOLUCIÓN MENSUAL =====
        evolucion_mensual = calcular_evolucion_mensual_compras(anio, proveedor_id, temporada, modo_ctx)
        
        # ===== TOP PROVEEDORES =====
        top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada, modo_ctx)
        
        # ===== PARETO PROVEEDORES =====
        pareto_proveedores = calcular_pareto_proveedores_compras(anio, proveedor_id, temporada, modo_ctx)

        # ===== CUMPLIMIENTO PROVEEDORES =====
        cumplimiento_proveedores = calcular_cumplimiento_proveedores_compras(anio, proveedor_id, temporada, modo_ctx)

        # ===== ROI POR TEMPORADA =====
        roi_temporadas = calcular_roi_temporadas_compras(anio, proveedor_id, modo_ctx)
        
        # ===== COMPARATIVA ANUAL =====
        comparativa_anual = calcular_comparativa_anual_compras(anio, proveedor_id, temporada, modo_ctx)
        
        # ===== ESTADO DE RECEPCIONES =====
        estado_recepciones = calcular_estado_recepciones_compras(anio, proveedor_id, temporada, modo_ctx)
        metricas_recepcion = calcular_metricas_recepcion_compras(anio, proveedor_id, temporada, modo_ctx)
        recepciones_pendientes = obtener_recepciones_pendientes_compras(anio, proveedor_id, modo_ctx)
        
        # ===== ESTADO DE PAGOS =====
        estado_pagos = calcular_estado_pagos_compras(anio, proveedor_id, temporada, modo_ctx)
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
                'temporada': temporada,
                'modo': 'vendedora' if es_vendedora else 'centro_distribucion',
            }
        })
        
    except Exception as e:
        logger.exception("Error al generar reporte de compras")
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte: {str(e)}',
        }, status=500)


def _get_dtes_base_compras(anio, proveedor_id, modo_ctx=None):
    """
    Construye el queryset base de DTEs para el reporte de compras,
    adaptado según el tipo de sucursal activa:

    - CENTRO_DISTRIBUCION (EDEL): DTEs tipo_transaccion='COMPRA'
      (emisor = proveedor externo, receptor = nosotros)
    - VENDEDORA: DTEs tipo_transaccion='TRASPASO'
      (emisor = EDEL, receptor = empresa de la sucursal vendedora)
    """
    from .models import Dte

    ctx = modo_ctx or {}
    es_vendedora = ctx.get('es_vendedora', False)
    empresa_receptora_id = ctx.get('empresa_receptora_id')

    if es_vendedora and empresa_receptora_id:
        # Para sucursales vendedoras: su "compra" son los traspasos recibidos de EDEL
        qs = Dte.objects.filter(
            tipo_transaccion='TRASPASO',
            receptor_id=empresa_receptora_id,
            fecha_emision__year=anio
        ).select_related('emisor', 'receptor')
        if proveedor_id:
            # En modo vendedora, proveedor_id filtra la sucursal emisora (EDEL) por empresa
            qs = qs.filter(emisor_id=proveedor_id)
    else:
        # Para EDEL/CD: DTEs de compra a proveedores externos
        qs = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__year=anio
        ).select_related('emisor')
        if proveedor_id:
            qs = qs.filter(emisor_id=proveedor_id)

    return qs


def _get_compras_base(anio, proveedor_id):
    """Compras directas (modelo Compras) para complementar DTEs."""
    from .models import Compras
    qs = Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'], fecha__year=anio)
    if proveedor_id:
        qs = qs.filter(empresa_id=proveedor_id)
    return qs


def _datos_compras_por_proveedor(anio, proveedor_id):
    """Datos agrupados por proveedor desde Compras (complemento de DTEs)."""
    from .models import Compras_Producto_Talla, Compras_Producto, Productos_Recepcionados
    compras_qs = _get_compras_base(anio, proveedor_id)
    prov_ids = compras_qs.values_list('empresa_id', flat=True).distinct()
    resultado = {}
    for pid in prov_ids:
        if not pid:
            continue
        c_ids = list(compras_qs.filter(empresa_id=pid).values_list('id', flat=True))
        pares = Compras_Producto_Talla.objects.filter(
            compra_producto__compras_id__in=c_ids
        ).aggregate(total=Sum('stock'))['total'] or 0
        inv = Compras_Producto_Talla.objects.filter(
            compra_producto__compras_id__in=c_ids
        ).aggregate(total=Sum(F('compra_producto__costo') * F('stock')))['total'] or 0
        recep = Productos_Recepcionados.objects.filter(
            compra_producto_talla__compra_producto__compras_id__in=c_ids
        ).aggregate(total=Sum('stockArribado'))['total'] or 0
        resultado[pid] = {
            'compra_ids': c_ids,
            'pares_comprados': pares,
            'inversion': float(inv),
            'pares_recepcionados': recep,
            'total_compras': len(c_ids),
        }
    return resultado


def _datos_compras_mensuales(anio, proveedor_id):
    """Datos mensuales desde Compras para complementar evolucion."""
    from .models import Compras_Producto_Talla
    compras_qs = _get_compras_base(anio, proveedor_id)
    resultado = {}
    for mes in range(1, 13):
        c_ids = list(compras_qs.filter(fecha__month=mes).values_list('id', flat=True))
        if not c_ids:
            resultado[mes] = {'total_compras': 0, 'inversion': 0, 'unidades': 0}
            continue
        pares = Compras_Producto_Talla.objects.filter(
            compra_producto__compras_id__in=c_ids
        ).aggregate(total=Sum('stock'))['total'] or 0
        inv = Compras_Producto_Talla.objects.filter(
            compra_producto__compras_id__in=c_ids
        ).aggregate(total=Sum(F('compra_producto__costo') * F('stock')))['total'] or 0
        resultado[mes] = {'total_compras': len(c_ids), 'inversion': float(inv), 'unidades': pares}
    return resultado


def calcular_metricas_compras(anio, periodo, proveedor_id, temporada, modo_ctx=None):
    """
    Calcula las métricas principales del reporte de compras.
    Usa DTEs con tipo_transaccion='COMPRA' donde el EMISOR es el proveedor.
    Las unidades se obtienen de Productos_Recepcionados (fuente real del flujo de compras).
    """
    from .models import Dte, Dte_Productos, Dte_Detalle_Pago, Empresa, Productos_Recepcionados
    
    # Query base adaptada al tipo de sucursal
    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx)
    
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
    
    # ── Unidades compradas ──────────────────────────────────────────────────
    # Intentar primero desde Dte_Productos (flow de ventas/traspasos que vinculan ProductoTalla).
    # Para el flow de compras (guardar_recepcion) las unidades están en Productos_Recepcionados.
    dtes_ids = list(dtes_query.values_list('id', flat=True))
    
    unidades_dte_productos = Dte_Productos.objects.filter(
        dte_id__in=dtes_ids,
        productoTalla__producto__excluir_de_analitica=False,
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # Si Dte_Productos no tiene datos, leer desde Productos_Recepcionados (fuente real)
    if unidades_dte_productos == 0 and dtes_ids:
        unidades_recepcionadas_real = Productos_Recepcionados.objects.filter(
            dte_id__in=dtes_ids
        ).aggregate(total=Sum('stockArribado'))['total'] or 0
    else:
        unidades_recepcionadas_real = 0
    
    # También contar recepciones del año aunque no tengan DTE (recepciones por Compras directas)
    # usando fecha de la recepción en lugar del DTE
    recepciones_sin_dte_anio = 0
    if not proveedor_id:  # Solo en vista general para no inflar
        recepciones_sin_dte_anio = Productos_Recepcionados.objects.filter(
            fecha__year=anio,
            dte__isnull=True,
        ).aggregate(total=Sum('stockArribado'))['total'] or 0

    unidades_compradas = max(unidades_dte_productos, unidades_recepcionadas_real)

    # ── Complementar con Compras directas (sin DTE) ────────────────────────
    datos_compras = _datos_compras_por_proveedor(anio, proveedor_id)
    compras_unidades = sum(d['pares_comprados'] for d in datos_compras.values())
    compras_inv = sum(d['inversion'] for d in datos_compras.values())
    compras_recep = sum(d['pares_recepcionados'] for d in datos_compras.values())
    compras_count = sum(d['total_compras'] for d in datos_compras.values())

    if unidades_compradas == 0:
        unidades_compradas = compras_unidades
    if total_compras == 0:
        total_compras += compras_count
    if float(inversion_total) == 0:
        inversion_total = Decimal(str(compras_inv))
        monto_con_iva_total = inversion_total
    if proveedores_activos == 0:
        proveedores_activos = len(datos_compras)
    # ── fin complemento ────────────────────────────────────────────────────

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
    if recepcionados == 0 and compras_recep > 0:
        recepcionados = len([d for d in datos_compras.values() if d['pares_recepcionados'] > 0])
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
        dte_id__in=dtes_ids_anterior,
        productoTalla__producto__excluir_de_analitica=False,
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


def calcular_evolucion_mensual_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula la evolucion mensual de compras usando DTEs + Compras directas"""
    from .models import Dte, Dte_Productos

    # Datos mensuales desde Compras directas (complemento)
    compras_mensuales = _datos_compras_mensuales(anio, proveedor_id)

    evolucion = []
    for mes in range(1, 13):
        dtes_mes = _get_dtes_base_compras(anio, proveedor_id, modo_ctx).filter(
            fecha_emision__month=mes
        )

        total_compras = dtes_mes.count()
        inversion = dtes_mes.aggregate(total=Sum('monto_neto'))['total'] or 0

        # Unidades del mes
        dtes_ids = list(dtes_mes.values_list('id', flat=True))
        unidades = Dte_Productos.objects.filter(
            dte_id__in=dtes_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).aggregate(total=Sum('stock'))['total'] or 0

        # Complementar con Compras directas si DTE no tiene datos
        cm = compras_mensuales.get(mes, {})
        if total_compras == 0 and cm.get('total_compras', 0) > 0:
            total_compras = cm['total_compras']
        if float(inversion) == 0 and cm.get('inversion', 0) > 0:
            inversion = cm['inversion']
        if unidades == 0 and cm.get('unidades', 0) > 0:
            unidades = cm['unidades']

        evolucion.append({
            'mes': mes,
            'total_compras': total_compras,
            'inversion': float(inversion),
            'unidades': unidades
        })

    return evolucion


def calcular_top_proveedores_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """
    Calcula el ranking de emisores.
    - CD: proveedores externos (emisor de DTEs COMPRA)
    - Vendedora: EDEL como emisor (DTEs TRASPASO recibidos)
    """
    from .models import Dte, Dte_Productos, Empresa

    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx)
    
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
        
        # Unidades: leer desde Productos_Recepcionados (fuente real del flujo de compras)
        dtes_ids = list(dtes_prov.values_list('id', flat=True))
        unidades_recepcionadas = Dte_Productos.objects.filter(
            dte_id__in=dtes_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).aggregate(total=Sum('stock'))['total'] or 0
        
        # Si Dte_Productos está vacío (flujo de compras normal), leer de Productos_Recepcionados
        if unidades_recepcionadas == 0 and dtes_ids:
            from .models import Productos_Recepcionados
            unidades_recepcionadas = Productos_Recepcionados.objects.filter(
                dte_id__in=dtes_ids
            ).aggregate(total=Sum('stockArribado'))['total'] or 0
        
        unidades = unidades_recepcionadas
        
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
    
    # ── Complementar con proveedores de Compras directas (sin DTE) ──
    prov_ids_ya = {r['id'] for r in resultado}
    datos_compras = _datos_compras_por_proveedor(anio, proveedor_id)
    for pid, dc in datos_compras.items():
        if pid in prov_ids_ya:
            continue
        try:
            prov_obj = Empresa.objects.get(id=pid)
        except Empresa.DoesNotExist:
            continue
        inversion_total_general += dc['inversion']
        resultado.append({
            'id': pid,
            'nombre': prov_obj.nombre,
            'rut': prov_obj.rut if hasattr(prov_obj, 'rut') else '',
            'total_compras': dc['total_compras'],
            'inversion': dc['inversion'],
            'unidades': dc['pares_comprados'],
            'cumplimiento': 100 if dc['pares_recepcionados'] > 0 else 0,
            'roi': 0,
        })
    # ── fin complemento ──

    # Calcular participación
    for item in resultado:
        item['participacion'] = round((item['inversion'] / inversion_total_general * 100), 1) if inversion_total_general > 0 else 0

    # Ordenar por inversión
    resultado.sort(key=lambda x: x['inversion'], reverse=True)

    return resultado[:15]  # Top 15


def calcular_pareto_proveedores_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula el analisis Pareto de proveedores"""
    top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada, modo_ctx)
    
    # Calcular acumulado
    total = sum(p['inversion'] for p in top_proveedores)
    acumulado = 0
    
    for proveedor in top_proveedores:
        acumulado += proveedor['inversion']
        proveedor['acumulado_pct'] = round((acumulado / total * 100), 1) if total > 0 else 0
    
    return top_proveedores[:10]


def calcular_cumplimiento_proveedores_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula el cumplimiento por proveedor"""
    top_proveedores = calcular_top_proveedores_compras(anio, proveedor_id, temporada, modo_ctx)
    
    # Ordenar por cumplimiento
    resultado = sorted(top_proveedores, key=lambda x: x['cumplimiento'], reverse=True)
    
    return resultado[:10]


def calcular_roi_temporadas_compras(anio, proveedor_id, modo_ctx=None):
    """Calcula la inversión por trimestre del año (simulando temporadas)"""
    from .models import Dte
    
    trimestres = [
        {'nombre': 'Q1 (Ene-Mar)', 'meses': [1, 2, 3]},
        {'nombre': 'Q2 (Abr-Jun)', 'meses': [4, 5, 6]},
        {'nombre': 'Q3 (Jul-Sep)', 'meses': [7, 8, 9]},
        {'nombre': 'Q4 (Oct-Dic)', 'meses': [10, 11, 12]},
    ]
    
    resultado = []
    
    for trimestre in trimestres:
        dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx).filter(
            fecha_emision__month__in=trimestre['meses']
        )
        
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


def calcular_comparativa_anual_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula la comparativa con el año anterior usando DTEs"""
    from .models import Dte
    
    actual = []
    anterior = []
    ctx_anterior = dict(modo_ctx or {})  # mismo modo pero año anterior
    
    for mes in range(1, 13):
        dtes_actual = _get_dtes_base_compras(anio, proveedor_id, modo_ctx).filter(
            fecha_emision__month=mes
        )
        inv_actual = dtes_actual.aggregate(total=Sum('monto_neto'))['total'] or 0
        actual.append(float(inv_actual))
        
        dtes_ant = _get_dtes_base_compras(anio - 1, proveedor_id, ctx_anterior).filter(
            fecha_emision__month=mes
        )
        inv_ant = dtes_ant.aggregate(total=Sum('monto_neto'))['total'] or 0
        anterior.append(float(inv_ant))
    
    return {
        'actual': actual,
        'anterior': anterior
    }


def calcular_estado_recepciones_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula el estado de recepciones de DTEs"""
    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx)
    
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


def calcular_metricas_recepcion_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """
    Calcula las métricas detalladas de recepción.
    Lee directamente de Productos_Recepcionados (fuente real del flujo de compras).
    """
    from .models import Dte, Productos_Recepcionados, Dte_Productos

    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx)

    total_dtes = dtes_query.count()
    dtes_ids = list(dtes_query.values_list('id', flat=True))

    # ── Unidades reales desde Productos_Recepcionados ─────────────────────
    if dtes_ids:
        pr_qs_con_dte = Productos_Recepcionados.objects.filter(dte_id__in=dtes_ids)
        unidades_recibidas = pr_qs_con_dte.aggregate(total=Sum('stockArribado'))['total'] or 0
    else:
        unidades_recibidas = 0

    # Unidades "esperadas": desde Dte_Productos si existen, sino usar recibidas
    unidades_dte_prod = Dte_Productos.objects.filter(
        dte_id__in=dtes_ids,
        productoTalla__producto__excluir_de_analitica=False,
    ).aggregate(total=Sum('stock'))['total'] or 0 if dtes_ids else 0

    unidades_esperadas = unidades_dte_prod if unidades_dte_prod > 0 else unidades_recibidas

    # ── Estados ──────────────────────────────────────────────────────────
    con_problemas = dtes_query.filter(
        estado_dte__in=['EN_REGULARIZACION', 'RECHAZADO', 'RECEPCIONADO_PARCIAL']
    ).count()

    faltantes = max(0, unidades_esperadas - unidades_recibidas) if unidades_dte_prod > 0 else 0
    porcentaje = round((unidades_recibidas / unidades_esperadas * 100), 1) if unidades_esperadas > 0 else 100

    return {
        'unidades_esperadas': unidades_esperadas,
        'unidades_recibidas': unidades_recibidas,
        'con_problemas': con_problemas,
        'faltantes': faltantes,
        'porcentaje_cumplimiento': porcentaje
    }


def obtener_recepciones_pendientes_compras(anio, proveedor_id, modo_ctx=None):
    """Obtiene los DTEs pendientes de recepción o con problemas"""
    from .models import Dte, Dte_Productos, Productos_Recepcionados
    
    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx).filter(
        estado_dte__in=['EMITIDO', 'RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION']
    ).order_by('-fecha_emision')
    
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


def calcular_estado_pagos_compras(anio, proveedor_id, temporada, modo_ctx=None):
    """Calcula el estado de pagos de DTEs"""
    from .models import Dte
    
    dtes_query = _get_dtes_base_compras(anio, proveedor_id, modo_ctx)
    
    pagados = dtes_query.filter(
        Q(estado_pago='PAGADO') | Q(estado_pago='Pagado')
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    pendientes = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__gte=timezone.localdate()
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    vencidos = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__lt=timezone.localdate()
    ).aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Vencen esta semana
    prox_semana = timezone.localdate() + timedelta(days=7)
    vencen_semana = dtes_query.filter(
        Q(estado_pago='PENDIENTE') | Q(estado_pago='Pendiente'),
        fecha_vencimiento__range=[timezone.localdate(), prox_semana]
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
    
    hoy = timezone.localdate()
    
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
    
    hoy = timezone.localdate()
    
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
        anio = int(request.GET.get('anio', timezone.localdate().year))
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
        logger.exception("Error al exportar reporte de compras")
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}',
        }, status=500)


# ========== API RENDIMIENTO DE COMPRAS: ROTACIÓN REAL + TRAZABILIDAD ==========
# Estrategia: Reconstruir ciclo de vida COMPLETO desde Movimientos_Producto
#
# ENTRADA (comprado):   concepto RECEPCION_COMPRA + INGRESO_INICIAL + INGRESO_MANUAL
# DESPACHO (sucursales): concepto TRASPASO_SALIDA (saliente real) + DTEs TRASPASO
# VENTA (al cliente):   concepto VENTA_PUBLICO/MAYORISTA + Ticket_Productos
# OTROS EGRESOS:        ajustes a la baja, pérdidas, devoluciones a proveedor, cambios
#
# Esto cubre TANTO datos migrados de Laravel como datos nuevos del sistema.
# Laravel no tenía módulo de Compras, todo se registraba como movimientos.

# INGRESO_MANUAL cuenta como entrada: el alta manual de catálogo deja kardex
# (igual que FIFO/trazabilidad, que ya lo tratan como ingreso). Antes quedaba
# fuera del embudo y distorsionaba rotación/inversión de los SKU creados a mano.
CONCEPTOS_ENTRADA = ['RECEPCION_COMPRA', 'INGRESO_INICIAL', 'INGRESO_MANUAL']
# Despacho SALIENTE a sucursales = solo TRASPASO_SALIDA (egreso, cantidad
# negativa → se normaliza con abs() al consumir). TRASPASO_SUCURSAL NO va aquí:
# en datos migrados es la pata de ENTRADA a la tienda (tipo INGRESO), así que
# sumarlo mezclaba signos y el "despachado" salía negativo.
CONCEPTOS_DESPACHO = ['TRASPASO_SALIDA']
CONCEPTOS_VENTA = ['VENTA_PUBLICO', 'VENTA_MAYORISTA']
# Egresos que NO son venta ni traspaso interno: mermas, ajustes a la baja,
# devoluciones a proveedor y salida por cambio. Se cuentan aparte para cerrar la
# reconciliación entrada → (venta + despacho + otros egresos + remanente), en vez
# de que ese stock que salió aparezca como "no vendido" en góndola.
CONCEPTOS_OTROS_EGRESOS = [
    'AJUSTE_NEGATIVO', 'PERDIDA_ROBO', 'PERDIDA_DETERIORO',
    'DONACION_ENTREGADA', 'DEVOLUCION_PROVEEDOR', 'CAMBIO_PRODUCTO_SALIDA',
]


@login_required
@require_GET
def api_rendimiento_compras(request):
    """
    Análisis de rendimiento: Entrada → Despacho → Venta por año.
    DUAL-SOURCE:
      - Si el año tiene >= 500 movimientos en Movimientos_Producto → usa movimientos (sistema actual/2025-2026)
      - Si tiene < 500 movimientos → usa DTEs migrados de Laravel (histórico 2018-2024)
    Soporta comparativa interanual (param comparar=1).
    """
    from .models import Movimientos_Producto
    from .utils_analitica import ids_producto_talla_activos

    UMBRAL_MOVIMIENTOS = 500

    def _datos_desde_movimientos(anio, sucursal_id, pt_ids):
        """Agrega datos de Movimientos_Producto para el año dado."""
        bf = {'fecha__year': anio, 'estado': 'COMPLETADO', 'ProductoTalla_id__in': pt_ids}
        if sucursal_id:
            bf['sucursal_origen_id'] = sucursal_id

        ent = Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_ENTRADA, **bf).aggregate(
            uds=Sum('cantidad'),
            costo=Sum(F('costo') * F('cantidad')),
        )
        desp = Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_DESPACHO, **bf).aggregate(
            uds=Sum('cantidad'),
        )
        vtas = Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_VENTA, **bf).aggregate(
            uds=Sum('cantidad'),
            ingreso=Sum(F('precio') * F('cantidad')),
        )
        # Otros egresos (mermas/ajustes a la baja/devoluciones a proveedor/cambios).
        # Son EGRESO → cantidad negativa; se normaliza a positivo con abs() abajo.
        otros = Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_OTROS_EGRESOS, **bf).aggregate(
            uds=Sum('cantidad'),
        )
        q_tk = {'idTicket__fecha__year': anio, 'idTicket__estado__in': ['PAGADO', 'PENDIENTE'],
                'ProductoTalla_id__in': pt_ids}
        if sucursal_id:
            q_tk['idTicket__sucursal_id'] = sucursal_id
        vtas_tk = Ticket_Productos.objects.filter(**q_tk).aggregate(
            uds=Sum('stock'), ingreso=Sum('subtotal'),
        )
        return {
            'entrada': int(ent['uds'] or 0),
            'inversion': float(ent['costo'] or 0),
            'despacho': abs(int(desp['uds'] or 0)),
            'venta_mov': int(vtas['uds'] or 0),
            'venta_tk': int(vtas_tk['uds'] or 0),
            'ingreso_mov': float(vtas['ingreso'] or 0),
            'ingreso_tk': float(vtas_tk['ingreso'] or 0),
            'otros_egresos': abs(int(otros['uds'] or 0)),
            'fuente': 'movimientos',
            '_bf': bf,
        }

    def _datos_desde_dte(anio, sucursal_id):
        """Agrega datos de DTEs migrados de Laravel para el año dado."""
        dte_f = {'fecha_emision__year': anio}
        if sucursal_id:
            dte_f['sucursal_id'] = sucursal_id

        # Entrada: DTEs tipo COMPRA → Dte_Productos (stock=unidades, costo=costo unit)
        dte_compra_ids = Dte.objects.filter(tipo_transaccion='COMPRA', **dte_f).values_list('id', flat=True)
        ent = Dte_Productos.objects.filter(
            dte_id__in=dte_compra_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).aggregate(
            uds=Sum('stock'),
            costo=Sum(F('costo') * F('stock')),
        )

        # Despacho: DTEs tipo TRASPASO
        dte_traspaso_ids = Dte.objects.filter(tipo_transaccion='TRASPASO', **dte_f).values_list('id', flat=True)
        desp = Dte_Productos.objects.filter(
            dte_id__in=dte_traspaso_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).aggregate(
            uds=Sum('stock'),
        )

        # Ventas: DTEs tipo VENTA_PUBLICO + VENTA (ingreso = precio * stock)
        dte_venta_ids = Dte.objects.filter(
            tipo_transaccion__in=['VENTA_PUBLICO', 'VENTA'], **dte_f
        ).values_list('id', flat=True)
        vtas = Dte_Productos.objects.filter(
            dte_id__in=dte_venta_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).aggregate(
            uds=Sum('stock'),
            ingreso=Sum(F('precio') * F('stock')),
        )

        return {
            'entrada': int(ent['uds'] or 0),
            'inversion': float(ent['costo'] or 0),
            'despacho': abs(int(desp['uds'] or 0)),
            'venta_mov': int(vtas['uds'] or 0),
            'venta_tk': 0,
            'ingreso_mov': float(vtas['ingreso'] or 0),
            'ingreso_tk': 0,
            'otros_egresos': 0,  # DTEs migrados no registran mermas/ajustes
            'fuente': 'dte',
            '_bf': dte_f,
        }

    def _evolucion_desde_movimientos(anio, sucursal_id, pt_ids, bf):
        ent_m = {e['mes']: e for e in Movimientos_Producto.objects.filter(
            concepto__in=CONCEPTOS_ENTRADA, **bf
        ).values(mes=F('fecha__month')).annotate(uds=Sum('cantidad'))}

        vtas_m = {v['mes']: v for v in Movimientos_Producto.objects.filter(
            concepto__in=CONCEPTOS_VENTA, **bf
        ).values(mes=F('fecha__month')).annotate(uds=Sum('cantidad'), ingreso=Sum(F('precio') * F('cantidad')))}

        q_tk = {'idTicket__fecha__year': anio, 'idTicket__estado__in': ['PAGADO', 'PENDIENTE']}
        if sucursal_id:
            q_tk['idTicket__sucursal_id'] = sucursal_id
        vtas_tk = {v['mes']: v for v in Ticket_Productos.objects.filter(**q_tk).values(
            mes=F('idTicket__fecha__month')
        ).annotate(uds=Sum('stock'), ingreso=Sum('subtotal'))}

        evol = []
        for m in range(1, 13):
            e_m = ent_m.get(m, {})
            vm = vtas_m.get(m, {})
            vt = vtas_tk.get(m, {})
            evol.append({
                'mes': m,
                'uds_compradas': e_m.get('uds', 0) or 0,
                'uds_vendidas': max(vm.get('uds', 0) or 0, vt.get('uds', 0) or 0),
                'ingreso': max(float(vm.get('ingreso', 0) or 0), float(vt.get('ingreso', 0) or 0)),
            })
        return evol

    def _evolucion_desde_dte(anio, sucursal_id):
        dte_f = {'fecha_emision__year': anio}
        if sucursal_id:
            dte_f['sucursal_id'] = sucursal_id

        dte_compra_ids = list(Dte.objects.filter(tipo_transaccion='COMPRA', **dte_f).values_list('id', flat=True))
        dte_venta_ids = list(Dte.objects.filter(
            tipo_transaccion__in=['VENTA_PUBLICO', 'VENTA'], **dte_f
        ).values_list('id', flat=True))

        ent_m = {e['mes']: e for e in Dte_Productos.objects.filter(
            dte_id__in=dte_compra_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).annotate(
            mes=F('dte__fecha_emision__month')
        ).values('mes').annotate(uds=Sum('stock'))} if dte_compra_ids else {}

        vtas_m = {v['mes']: v for v in Dte_Productos.objects.filter(
            dte_id__in=dte_venta_ids,
            productoTalla__producto__excluir_de_analitica=False,
        ).annotate(
            mes=F('dte__fecha_emision__month')
        ).values('mes').annotate(uds=Sum('stock'), ingreso=Sum(F('precio') * F('stock')))} if dte_venta_ids else {}

        evol = []
        for m in range(1, 13):
            e_m = ent_m.get(m, {})
            vm = vtas_m.get(m, {})
            evol.append({
                'mes': m,
                'uds_compradas': e_m.get('uds', 0) or 0,
                'uds_vendidas': vm.get('uds', 0) or 0,
                'ingreso': float(vm.get('ingreso', 0) or 0),
            })
        return evol

    def _sucursales_desde_dte(anio, sucursal_id):
        """Desglose por sucursal usando DTEs (por sucursal del DTE)."""
        dte_f = {'fecha_emision__year': anio}
        if sucursal_id:
            dte_f['sucursal_id'] = sucursal_id

        # Ventas agrupadas por sucursal del DTE
        vtas_suc = {}
        for v in Dte.objects.filter(
            tipo_transaccion__in=['VENTA_PUBLICO', 'VENTA'], **dte_f
        ).annotate(
            uds=Sum('dte_productos__stock',
                    filter=Q(dte_productos__productoTalla__producto__excluir_de_analitica=False)),
            ingreso=Sum(F('dte_productos__precio') * F('dte_productos__stock'),
                        filter=Q(dte_productos__productoTalla__producto__excluir_de_analitica=False)),
        ).values('sucursal_id', 'sucursal__alias', 'uds', 'ingreso'):
            sid = v['sucursal_id']
            if sid not in vtas_suc:
                vtas_suc[sid] = {'alias': v['sucursal__alias'], 'uds': 0, 'ingreso': 0.0}
            vtas_suc[sid]['uds'] += v['uds'] or 0
            vtas_suc[sid]['ingreso'] += float(v['ingreso'] or 0)

        result = []
        for sid, v in vtas_suc.items():
            result.append({
                'sucursal': v['alias'] or 'Sin sucursal',
                'uds_entrada': 0,
                'uds_despachadas': 0,
                'uds_vendidas': v['uds'],
                'ingreso': v['ingreso'],
                'costo_despacho': 0,
                'rotacion_pct': 0,
                'stock_restante': 0,
                'roi': 0,
                'explicacion': 'Datos desde DTEs migrados de Laravel.',
            })
        result.sort(key=lambda x: x['uds_vendidas'], reverse=True)
        return result

    def _calcular_resumen(d):
        """Calcula KPIs a partir del dict de datos brutos."""
        entrada = d['entrada']
        inversion = d['inversion']
        despacho = d['despacho']
        otros_egresos = d.get('otros_egresos', 0)
        vendido = max(d['venta_mov'], d['venta_tk'])
        ingreso = max(d['ingreso_mov'], d['ingreso_tk'])

        costo_vendido = inversion * (vendido / entrada) if entrada > 0 else 0
        margen = ingreso - costo_vendido
        rotacion_raw = (vendido / entrada) * 100 if entrada > 0 else 0
        rotacion = min(999.0, round(rotacion_raw, 1))
        roi = round((margen / costo_vendido) * 100, 1) if costo_vendido > 0 else 0

        return {
            'total_comprado': entrada,
            'total_recibido': entrada,
            'total_despachado': despacho,
            'total_vendido': vendido,
            'total_inversion': inversion,
            'total_ingreso_ventas': ingreso,
            'margen_real': margen,
            'rotacion_global': rotacion,
            'rotacion_raw_pct': round(rotacion_raw, 1),
            'ventas_superan_entrada': vendido > entrada,
            'roi_real': roi,
            'tasa_despacho': round((despacho / entrada) * 100, 1) if entrada > 0 else 0,
            'total_otros_egresos': otros_egresos,
            # Remanente real = lo que entró menos lo que salió por CUALQUIER vía
            # (venta + otros egresos). Antes solo restaba ventas, así que las
            # mermas/devoluciones inflaban el "no vendido".
            'stock_sin_vender': entrada - vendido - otros_egresos,
            'productos_analizados': entrada,
        }

    def _delta(actual, anterior, campo):
        """Calcula delta % entre actual y año anterior."""
        a = actual.get(campo, 0) or 0
        b = anterior.get(campo, 0) or 0
        if b == 0:
            return None
        return round((a - b) / abs(b) * 100, 1)

    try:
        anio = int(request.GET.get('anio', timezone.localdate().year))
        sucursal_id = request.GET.get('sucursal', '') or None
        comparar = request.GET.get('comparar', '0') == '1'

        pt_ids = ids_producto_talla_activos(sucursal_id=sucursal_id)

        # ── Determinar fuente para el año solicitado ──
        count_movs = Movimientos_Producto.objects.filter(
            estado='COMPLETADO', fecha__year=anio
        ).count()
        usar_dte = count_movs < UMBRAL_MOVIMIENTOS

        if usar_dte:
            datos = _datos_desde_dte(anio, sucursal_id)
        else:
            datos = _datos_desde_movimientos(anio, sucursal_id, pt_ids)
            # Fallback: si hay movimientos en general pero sin entradas de stock,
            # complementar con DTEs para tener al menos los KPIs de ventas históricas
            if datos['entrada'] == 0:
                datos_dte = _datos_desde_dte(anio, sucursal_id)
                if datos_dte['entrada'] > 0 or datos_dte['venta_mov'] > 0:
                    # DTE tiene más datos; usarlo en modo mixto
                    datos = datos_dte
                    usar_dte = True
                else:
                    # Mantener movimientos con ventas aunque entrada=0
                    # (puede haber ventas de stock de años anteriores)
                    datos['fuente'] = 'movimientos'

        # ── Años disponibles (basados en DTEs que tienen datos) ──
        anios_dte = sorted(set(
            Dte.objects.filter(
                tipo_transaccion__in=['COMPRA', 'VENTA_PUBLICO', 'VENTA']
            ).dates('fecha_emision', 'year')
        ), key=lambda d: d.year, reverse=True)
        anios_disponibles = [d.year for d in anios_dte]
        # Asegurar que el año actual también aparezca
        anio_actual = timezone.localdate().year
        if anio_actual not in anios_disponibles:
            anios_disponibles.insert(0, anio_actual)

        # ── KPIs del año actual ──
        resumen = _calcular_resumen(datos)

        # ── Nota de metodología ──
        if usar_dte:
            nota_metodologia = (
                f'Datos del año {anio} obtenidos desde DTEs migrados de Laravel '
                f'(el año tiene solo {count_movs} movimientos registrados en el nuevo sistema). '
                'Entrada = DTEs tipo COMPRA, Ventas = DTEs tipo VENTA_PUBLICO/VENTA, '
                'Despacho = DTEs tipo TRASPASO.'
            )
        else:
            if resumen['total_comprado'] == 0:
                nota_metodologia = (
                    f'El año {anio} tiene {count_movs} movimientos pero sin ingresos de stock '
                    '(RECEPCION_COMPRA / INGRESO_INICIAL). Las ventas corresponden a stock ingresado en períodos anteriores. '
                    'Las métricas de rotación e inversión no están disponibles para este año.'
                )
            else:
                nota_metodologia = (
                    'Datos desde Movimientos_Producto del sistema actual. '
                    'Embudo coherente: mismo año y sucursal para entrada, despacho saliente y ventas.'
                )
        if resumen['ventas_superan_entrada']:
            nota_metodologia += ' Rotación >100% indica ventas con stock de ciclos anteriores.'

        # ── Comparativa interanual ──
        comparacion = None
        if comparar:
            anio_ant = anio - 1
            count_movs_ant = Movimientos_Producto.objects.filter(
                estado='COMPLETADO', fecha__year=anio_ant
            ).count()
            usar_dte_ant = count_movs_ant < UMBRAL_MOVIMIENTOS
            if usar_dte_ant:
                datos_ant = _datos_desde_dte(anio_ant, sucursal_id)
            else:
                datos_ant = _datos_desde_movimientos(anio_ant, sucursal_id, pt_ids)
            resumen_ant = _calcular_resumen(datos_ant)

            comparacion = {
                'anio_anterior': anio_ant,
                'fuente_anterior': datos_ant['fuente'],
                'delta_entrada': _delta(resumen, resumen_ant, 'total_comprado'),
                'delta_vendido': _delta(resumen, resumen_ant, 'total_vendido'),
                'delta_inversion': _delta(resumen, resumen_ant, 'total_inversion'),
                'delta_ingreso': _delta(resumen, resumen_ant, 'total_ingreso_ventas'),
                'delta_rotacion': _delta(resumen, resumen_ant, 'rotacion_global'),
                'delta_roi': _delta(resumen, resumen_ant, 'roi_real'),
                'resumen_anterior': resumen_ant,
            }

        # ── Evolución mensual ──
        bf = datos.get('_bf', {})
        if usar_dte:
            evolucion = _evolucion_desde_dte(anio, sucursal_id)
        else:
            evolucion = _evolucion_desde_movimientos(anio, sucursal_id, pt_ids, bf)

        # ── Por sucursal ──
        if usar_dte:
            por_sucursal = _sucursales_desde_dte(anio, sucursal_id)
            por_compra = []
        else:
            # Lógica existente de movimientos
            entradas_suc = {e['sucursal_origen_id']: e for e in Movimientos_Producto.objects.filter(
                concepto__in=CONCEPTOS_ENTRADA, **bf
            ).values('sucursal_origen_id', 'sucursal_origen__alias').annotate(
                uds=Sum('cantidad'), costo=Sum(F('costo') * F('cantidad')),
            )}
            despachos_suc = {d['sucursal_origen_id']: d for d in Movimientos_Producto.objects.filter(
                concepto__in=CONCEPTOS_DESPACHO, **bf
            ).values('sucursal_origen_id', 'sucursal_origen__alias').annotate(
                uds=Sum('cantidad'), costo=Sum(F('costo') * F('cantidad')),
            )}
            ventas_suc_mov = {v['sucursal_origen_id']: v for v in Movimientos_Producto.objects.filter(
                concepto__in=CONCEPTOS_VENTA, **bf
            ).values('sucursal_origen_id', 'sucursal_origen__alias').annotate(
                uds=Sum('cantidad'), ingreso=Sum(F('precio') * F('cantidad')),
            )}
            q_tk_suc = {'idTicket__fecha__year': anio, 'idTicket__estado__in': ['PAGADO', 'PENDIENTE']}
            if sucursal_id:
                q_tk_suc['idTicket__sucursal_id'] = sucursal_id
            ventas_suc_ticket = {v['idTicket__sucursal_id']: v for v in Ticket_Productos.objects.filter(
                **q_tk_suc
            ).values('idTicket__sucursal_id', 'idTicket__sucursal__alias').annotate(
                uds=Sum('stock'), ingreso=Sum('subtotal'),
            )}

            all_suc_ids = set(list(entradas_suc.keys()) + list(despachos_suc.keys()) +
                             list(ventas_suc_mov.keys()) + list(ventas_suc_ticket.keys()))
            por_sucursal = []
            por_compra = []
            for sid in all_suc_ids:
                if not sid:
                    continue
                e = entradas_suc.get(sid, {})
                d = despachos_suc.get(sid, {})
                vm = ventas_suc_mov.get(sid, {})
                vt = ventas_suc_ticket.get(sid, {})

                uds_ent = e.get('uds', 0) or 0
                uds_desp = abs(d.get('uds', 0) or 0)
                uds_vend = max(vm.get('uds', 0) or 0, vt.get('uds', 0) or 0)
                ingreso = max(float(vm.get('ingreso', 0) or 0), float(vt.get('ingreso', 0) or 0))
                costo_e = float(e.get('costo', 0) or 0)
                costo_d = abs(float(d.get('costo', 0) or 0))
                suc_nombre = (e.get('sucursal_origen__alias') or d.get('sucursal_origen__alias') or
                              vm.get('sucursal_origen__alias') or vt.get('idTicket__sucursal__alias') or 'Sin sucursal')

                rot = round((uds_vend / uds_ent) * 100, 1) if uds_ent > 0 else (
                    round((uds_vend / uds_desp) * 100, 1) if uds_desp > 0 else 0)
                roi = round(((ingreso - costo_e) / costo_e) * 100, 1) if costo_e > 0 else 0

                explicacion = ''
                if uds_ent < uds_vend and uds_ent > 0:
                    explicacion = 'Ventas mayores que entradas del año: el stock vendido puede venir de años anteriores o ingresos por traspaso desde otra bodega.'
                elif uds_ent == 0 and uds_vend > 0:
                    explicacion = 'Sin ingreso inicial en esta sucursal; ventas usan stock histórico o traspasos registrados con origen en central.'

                por_sucursal.append({
                    'sucursal': suc_nombre,
                    'uds_entrada': uds_ent,
                    'uds_despachadas': uds_desp,
                    'uds_vendidas': uds_vend,
                    'ingreso': ingreso,
                    'costo_despacho': costo_d,
                    'rotacion_pct': rot,
                    'stock_restante': uds_ent - uds_vend if uds_ent > 0 else uds_desp - uds_vend,
                    'roi': roi,
                    'explicacion': explicacion,
                })
                if uds_ent > 0:
                    por_compra.append({
                        'compra': f"{suc_nombre} (Ingreso)",
                        'fuente': 'Movimiento',
                        'productos': 0,
                        'uds_compradas': uds_ent,
                        'uds_despachadas': uds_desp,
                        'uds_vendidas': uds_vend,
                        'rotacion_pct': rot,
                        'inversion': costo_e,
                        'ingreso_venta': ingreso,
                        'roi_real': roi,
                    })

            por_sucursal.sort(key=lambda x: x['rotacion_pct'], reverse=True)
            por_compra.sort(key=lambda x: x['rotacion_pct'], reverse=True)

        # ── Top productos ──
        por_producto = []
        if not usar_dte:
            q_top_tk = {'idTicket__fecha__year': anio, 'idTicket__estado__in': ['PAGADO', 'PENDIENTE']}
            if sucursal_id:
                q_top_tk['idTicket__sucursal_id'] = sucursal_id
            top_vendidos = list(Ticket_Productos.objects.filter(**q_top_tk).values(
                'ProductoTalla_id', 'ProductoTalla__producto__articulo', 'ProductoTalla__talla',
            ).annotate(uds_vendidas=Sum('stock'), ingreso=Sum('subtotal')).order_by('-uds_vendidas')[:150])

            if not top_vendidos:
                top_vendidos = list(Movimientos_Producto.objects.filter(
                    concepto__in=CONCEPTOS_VENTA, **bf
                ).values(
                    'ProductoTalla_id', 'ProductoTalla__producto__articulo', 'ProductoTalla__talla',
                ).annotate(uds_vendidas=Sum('cantidad'), ingreso=Sum(F('precio') * F('cantidad'))
                ).order_by('-uds_vendidas')[:150])

            top_pt_ids = [t['ProductoTalla_id'] for t in top_vendidos]
            entradas_top = {}
            if top_pt_ids:
                for e in Movimientos_Producto.objects.filter(
                    ProductoTalla_id__in=top_pt_ids,
                    concepto__in=CONCEPTOS_ENTRADA, fecha__year=anio, estado='COMPLETADO',
                ).values('ProductoTalla_id').annotate(uds=Sum('cantidad'), costo=Sum(F('costo') * F('cantidad'))):
                    entradas_top[e['ProductoTalla_id']] = e
            despachos_top = {}
            if top_pt_ids:
                for d in Movimientos_Producto.objects.filter(
                    ProductoTalla_id__in=top_pt_ids,
                    concepto__in=CONCEPTOS_DESPACHO, fecha__year=anio, estado='COMPLETADO',
                ).values('ProductoTalla_id').annotate(uds=Sum('cantidad')):
                    despachos_top[d['ProductoTalla_id']] = abs(d['uds'] or 0)

            for tv in top_vendidos:
                pt_id = tv['ProductoTalla_id']
                uds_v = tv['uds_vendidas'] or 0
                ing = float(tv['ingreso'] or 0)
                e = entradas_top.get(pt_id, {})
                uds_e = e.get('uds', 0) or 0
                costo_t = float(e.get('costo', 0) or 0)
                uds_d = despachos_top.get(pt_id, 0)
                cu = round(costo_t / uds_e) if uds_e > 0 else 0
                margen = ing - (cu * uds_v)
                rot = round((uds_v / uds_e) * 100, 1) if uds_e > 0 else 0
                roi_p = round((margen / costo_t) * 100, 1) if costo_t > 0 else 0
                por_producto.append({
                    'articulo': tv['ProductoTalla__producto__articulo'] or '-',
                    'talla': tv['ProductoTalla__talla'] or '-',
                    'origen': '-', 'tipo_entrada': 'Movimiento', 'fuente': 'Movimiento',
                    'uds_compradas': uds_e, 'uds_recibidas': uds_e, 'uds_despachadas': uds_d,
                    'uds_vendidas': uds_v, 'costo_unitario': cu, 'ingreso_venta': ing,
                    'margen_real': margen, 'rotacion_pct': rot, 'roi_real': roi_p,
                    'stock_restante': uds_e - uds_v if uds_e > 0 else 0,
                })
            por_producto.sort(key=lambda x: x['rotacion_pct'], reverse=True)
        else:
            # Para años DTE: top ventas desde DTEs
            dte_venta_ids = list(Dte.objects.filter(
                tipo_transaccion__in=['VENTA_PUBLICO', 'VENTA'], fecha_emision__year=anio
            ).values_list('id', flat=True))
            if dte_venta_ids:
                for v in Dte_Productos.objects.filter(
                    dte_id__in=dte_venta_ids,
                    productoTalla__producto__excluir_de_analitica=False,
                ).values(
                    'descripcion'
                ).annotate(uds_vendidas=Sum('stock'), ingreso=Sum(F('precio') * F('stock'))
                ).order_by('-uds_vendidas')[:100]:
                    por_producto.append({
                        'articulo': v['descripcion'] or '-',
                        'talla': '-', 'origen': '-', 'tipo_entrada': 'DTE', 'fuente': 'DTE',
                        'uds_compradas': 0, 'uds_recibidas': 0, 'uds_despachadas': 0,
                        'uds_vendidas': v['uds_vendidas'] or 0,
                        'costo_unitario': 0, 'ingreso_venta': float(v['ingreso'] or 0),
                        'margen_real': 0, 'rotacion_pct': 0, 'roi_real': 0, 'stock_restante': 0,
                    })

        return JsonResponse({
            'success': True,
            'resumen': resumen,
            'nota_metodologia': nota_metodologia,
            'fuente_datos': datos['fuente'],
            'anios_disponibles': anios_disponibles,
            'comparacion': comparacion,
            'por_compra': por_compra[:50],
            'por_producto': por_producto[:100],
            'por_sucursal': por_sucursal,
            'evolucion_mensual': evolucion,
        })

    except Exception as e:
        logger.exception("Error al generar rendimiento de compras")
        return JsonResponse({
            'success': False,
            'error': str(e),
        }, status=500)


def _resumen_vacio():
    return {
        'total_comprado': 0, 'total_recibido': 0, 'total_despachado': 0,
        'total_vendido': 0, 'total_inversion': 0, 'total_ingreso_ventas': 0,
        'margen_real': 0, 'rotacion_global': 0, 'roi_real': 0,
        'tasa_despacho': 0, 'stock_sin_vender': 0, 'productos_analizados': 0,
    }


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
        fecha_desde = request.GET.get('fecha_desde', '').strip()
        fecha_hasta = request.GET.get('fecha_hasta', '').strip()

        tiene_filtro = any([marca_id, departamento_id, busqueda, fecha_desde, fecha_hasta])
        
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
            sucursal_id__in=sucursales_ids,
            excluir_de_analitica=False
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
        
        # ========== PARSEAR FILTRO DE FECHAS ==========
        filtro_fecha = {}
        if fecha_desde:
            try:
                filtro_fecha['fecha__gte'] = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            except ValueError:
                pass
        if fecha_hasta:
            try:
                filtro_fecha['fecha__lte'] = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
            except ValueError:
                pass

        # ========== QUERY 2: INGRESOS AGREGADOS POR PRODUCTO Y SUCURSAL ==========
        # Excluye DEVOLUCION_CLIENTE para no inflar el "recibido"
        conceptos_ingreso_real = [
            'INGRESO_INICIAL', 'INGRESO_MANUAL', 'RECEPCION_COMPRA',
            'REPOSICION_STOCK', 'TRASPASO_ENTRADA', 'CAMBIO_PRODUCTO_ENTRADA',
            'AJUSTE_POSITIVO', 'AJUSTE_INVENTARIO_ENTRADA',
        ]
        ingresos_query = Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=productos_ids,
            sucursal_destino_id__in=sucursales_ids,
            estado='COMPLETADO',
            concepto__in=conceptos_ingreso_real,
            **filtro_fecha
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

        # ========== QUERY 4: VENTAS REALES POR PRODUCTO Y SUCURSAL ==========
        # Calcula vendido desde movimientos de venta (no por resta)
        # Las cantidades de egreso son negativas, usamos abs()
        ventas_query = Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=productos_ids,
            sucursal_origen_id__in=sucursales_ids,
            estado='COMPLETADO',
            concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA'],
            **filtro_fecha
        ).values(
            'ProductoTalla__producto_id', 'sucursal_origen_id'
        ).annotate(
            total_ventas=Sum('cantidad')
        )

        # Crear mapa de ventas: {producto_id: {sucursal_id: cantidad}}
        ventas_map = {}
        for item in ventas_query:
            prod_id = item['ProductoTalla__producto_id']
            suc_id = item['sucursal_origen_id']
            # cantidad es negativa para egresos, tomamos abs
            cantidad = abs(item['total_ventas'] or 0)

            if prod_id not in ventas_map:
                ventas_map[prod_id] = {}
            ventas_map[prod_id][suc_id] = cantidad

        # ========== PROCESAR RESULTADOS (sin queries adicionales) ==========
        datos_reporte = []

        for producto in productos_list:
            prod_id = producto.id

            # Obtener datos de este producto desde los mapas
            ingresos_producto = ingresos_map.get(prod_id, {})
            stock_producto = stock_map.get(prod_id, {})
            ventas_producto = ventas_map.get(prod_id, {})

            # Construir datos por sucursal
            stock_por_sucursal = {}
            total_inicial = 0
            total_restante = 0
            total_vendido = 0

            # Revisar todas las sucursales donde hay ingresos, stock o ventas
            sucursales_con_datos = set(ingresos_producto.keys()) | set(stock_producto.keys()) | set(ventas_producto.keys())

            for suc_id in sucursales_con_datos:
                if suc_id not in sucursales_map:
                    continue

                inicial = ingresos_producto.get(suc_id, 0)
                restante = stock_producto.get(suc_id, 0)
                vendido = ventas_producto.get(suc_id, 0)

                if inicial > 0 or restante > 0 or vendido > 0:
                    suc_alias = sucursales_map[suc_id]
                    stock_por_sucursal[suc_alias] = {
                        'sucursal_id': suc_id,
                        'inicial': inicial,
                        'restante': restante,
                        'vendido': vendido,
                    }
                    total_inicial += inicial
                    total_restante += restante
                    total_vendido += vendido

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
                    'total_vendido': total_vendido,
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
        logger.exception("Error en reporte movimientos por sucursal")
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
        fecha_desde = request.GET.get('fecha_desde', '').strip()
        fecha_hasta = request.GET.get('fecha_hasta', '').strip()

        # Parsear filtro de fechas
        filtro_fecha = {}
        if fecha_desde:
            try:
                filtro_fecha['fecha__gte'] = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            except ValueError:
                pass
        if fecha_hasta:
            try:
                filtro_fecha['fecha__lte'] = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
            except ValueError:
                pass

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
            sucursal_id__in=sucursales_ids,
            excluir_de_analitica=False
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
        
        # QUERY 2: Ingresos agregados (excluye devoluciones de cliente)
        conceptos_ingreso_real = [
            'INGRESO_INICIAL', 'INGRESO_MANUAL', 'RECEPCION_COMPRA',
            'REPOSICION_STOCK', 'TRASPASO_ENTRADA', 'CAMBIO_PRODUCTO_ENTRADA',
            'AJUSTE_POSITIVO', 'AJUSTE_INVENTARIO_ENTRADA',
        ]
        ingresos_map = {}
        if productos_ids:
            ingresos_query = Movimientos_Producto.objects.filter(
                ProductoTalla__producto_id__in=productos_ids,
                sucursal_destino_id__in=sucursales_ids,
                estado='COMPLETADO',
                concepto__in=conceptos_ingreso_real,
                **filtro_fecha
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

        # QUERY 4: Ventas reales agregadas
        ventas_map = {}
        if productos_ids:
            ventas_query = Movimientos_Producto.objects.filter(
                ProductoTalla__producto_id__in=productos_ids,
                sucursal_origen_id__in=sucursales_ids,
                estado='COMPLETADO',
                concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA'],
                **filtro_fecha
            ).values(
                'ProductoTalla__producto_id', 'sucursal_origen_id'
            ).annotate(
                total_ventas=Sum('cantidad')
            )

            for item in ventas_query:
                prod_id = item['ProductoTalla__producto_id']
                suc_id = item['sucursal_origen_id']
                if prod_id not in ventas_map:
                    ventas_map[prod_id] = {}
                ventas_map[prod_id][suc_id] = abs(item['total_ventas'] or 0)

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
        headers = ['Artículo', 'Marca', 'Color', 'Departamento', 'Costo', 'Precio Venta']

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
            ventas_prod = ventas_map.get(prod_id, {})

            # Calcular totales
            total_inicial = sum(ingresos_prod.values())
            total_restante = sum(stock_prod.values())
            total_vendido = sum(ventas_prod.values())

            if total_inicial > 0 or total_restante > 0 or total_vendido > 0:
                col = 1

                ws.cell(row=row, column=col, value=producto.articulo).border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.atributo1.valor if producto.atributo1 else '-').border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.atributo2.valor if producto.atributo2 else '-').border = border
                col += 1
                ws.cell(row=row, column=col, value=producto.categoria.nombre if producto.categoria else '-').border = border
                col += 1

                costo_val = float(producto.costo) if producto.costo else 0
                cell_costo = ws.cell(row=row, column=col, value=costo_val)
                cell_costo.border = border
                cell_costo.number_format = '"$"#,##0'
                cell_costo.alignment = Alignment(horizontal='right')
                col += 1

                precio_val = float(producto.precioventa) if producto.precioventa else 0
                cell_precio = ws.cell(row=row, column=col, value=precio_val)
                cell_precio.border = border
                cell_precio.number_format = '"$"#,##0'
                cell_precio.alignment = Alignment(horizontal='right')
                cell_precio.font = Font(bold=True, color="00875A")
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

                cell = ws.cell(row=row, column=col, value=total_vendido)
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
        ws.column_dimensions['E'].width = 13
        ws.column_dimensions['F'].width = 13

        for col in range(7, len(headers) + 1):
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
        logger.exception("Error exportando stock inicial restante a Excel")
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        }, status=500)


# ========== REPORTES MEJORADOS: RECEPCIONES Y DESPACHOS ==========

@require_GET
@login_required
def api_reporte_recepciones_detallado(request):
    """
    API mejorada para reporte de recepciones con:
    - Datos reales de Productos_Recepcionados (no solo DTEs)
    - Desglose por sucursal destino
    - Split reposicion vs compra nueva
    - Diferencias de precio detectadas
    """
    try:
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        proveedor_id = request.GET.get('proveedor_id')
        sucursal_id = request.GET.get('sucursal_id')

        if not fecha_inicio or not fecha_fin:
            fecha_fin_dt = timezone.localdate()
            fecha_inicio_dt = fecha_fin_dt - timedelta(days=30)
        else:
            fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d').date()

        # Para compras históricas vinculadas retroactivamente, fecha_recepcion
        # se setea con compra.fecha (fecha real). Filtramos por ese campo
        # cuando existe, y caemos a `fecha` (auto_now) para registros legados.
        recepciones_qs = Productos_Recepcionados.objects.filter(
            Q(fecha_recepcion__date__range=[fecha_inicio_dt, fecha_fin_dt]) |
            (Q(fecha_recepcion__isnull=True) & Q(fecha__range=[fecha_inicio_dt, fecha_fin_dt]))
        ).select_related(
            'compra_producto_talla__compra_producto__compras__empresa',
            'producto_talla__producto',
            'dte__emisor',
            'sucursal_destino',
        )

        if proveedor_id:
            # Match por proveedor en DTE o en la compra vinculada (cubre históricas sin DTE).
            recepciones_qs = recepciones_qs.filter(
                Q(dte__emisor_id=proveedor_id) |
                Q(compra_producto_talla__compra_producto__compras__empresa_id=proveedor_id)
            )
        if sucursal_id:
            recepciones_qs = recepciones_qs.filter(sucursal_destino_id=sucursal_id)

        # --- Totals ---
        total_items = recepciones_qs.count()
        total_unidades = recepciones_qs.aggregate(t=Sum('stockArribado'))['t'] or 0
        total_reposicion = recepciones_qs.filter(es_reposicion=True).count()
        total_nuevo = recepciones_qs.filter(es_reposicion=False).count()
        total_historicas = recepciones_qs.filter(es_historica=True).count()
        total_con_cambio_precio = recepciones_qs.exclude(
            precio_anterior__isnull=True,
        ).exclude(
            precio_nuevo__isnull=True,
        ).exclude(
            precio_anterior=F('precio_nuevo'),
        ).count()

        # --- By sucursal ---
        por_sucursal = (
            recepciones_qs
            .values('sucursal_destino__alias', 'sucursal_destino_id')
            .annotate(
                items=Count('id'),
                unidades=Sum('stockArribado'),
                reposiciones=Count('id', filter=Q(es_reposicion=True)),
                nuevos=Count('id', filter=Q(es_reposicion=False)),
            )
            .order_by('-unidades')
        )
        por_sucursal_data = [
            {
                'sucursal': row['sucursal_destino__alias'] or 'Sin asignar',
                'sucursal_id': row['sucursal_destino_id'],
                'items': row['items'],
                'unidades': row['unidades'] or 0,
                'reposiciones': row['reposiciones'],
                'nuevos': row['nuevos'],
            }
            for row in por_sucursal
        ]

        # --- By proveedor ---
        # Rama 1: recepciones con DTE → proveedor es dte.emisor
        por_proveedor_dte = (
            recepciones_qs
            .filter(dte__isnull=False)
            .values('dte__emisor__nombre', 'dte__emisor__rut')
            .annotate(
                items=Count('id'),
                unidades=Sum('stockArribado'),
                reposiciones=Count('id', filter=Q(es_reposicion=True)),
                nuevos=Count('id', filter=Q(es_reposicion=False)),
            )
        )
        # Rama 2: recepciones sin DTE (típico de compras históricas vinculadas)
        # → proveedor es la empresa de la compra.
        por_proveedor_compra = (
            recepciones_qs
            .filter(dte__isnull=True, compra_producto_talla__isnull=False)
            .values(
                'compra_producto_talla__compra_producto__compras__empresa__nombre',
                'compra_producto_talla__compra_producto__compras__empresa__rut',
            )
            .annotate(
                items=Count('id'),
                unidades=Sum('stockArribado'),
                reposiciones=Count('id', filter=Q(es_reposicion=True)),
                nuevos=Count('id', filter=Q(es_reposicion=False)),
            )
        )

        # Merge por nombre+rut acumulando contadores.
        merged_proveedor = {}
        for row in por_proveedor_dte:
            key = (row['dte__emisor__nombre'], row['dte__emisor__rut'])
            if not key[0]:
                continue
            merged_proveedor[key] = {
                'proveedor': key[0],
                'rut': key[1],
                'items': row['items'],
                'unidades': row['unidades'] or 0,
                'reposiciones': row['reposiciones'],
                'nuevos': row['nuevos'],
            }
        for row in por_proveedor_compra:
            nombre = row['compra_producto_talla__compra_producto__compras__empresa__nombre']
            rut = row['compra_producto_talla__compra_producto__compras__empresa__rut']
            if not nombre:
                continue
            key = (nombre, rut)
            if key in merged_proveedor:
                bucket = merged_proveedor[key]
                bucket['items'] += row['items']
                bucket['unidades'] += row['unidades'] or 0
                bucket['reposiciones'] += row['reposiciones']
                bucket['nuevos'] += row['nuevos']
            else:
                merged_proveedor[key] = {
                    'proveedor': nombre,
                    'rut': rut,
                    'items': row['items'],
                    'unidades': row['unidades'] or 0,
                    'reposiciones': row['reposiciones'],
                    'nuevos': row['nuevos'],
                }
        por_proveedor_data = sorted(
            merged_proveedor.values(),
            key=lambda d: d['unidades'],
            reverse=True,
        )

        # --- Cambios de precio detectados ---
        cambios_precio = []
        for rec in recepciones_qs.exclude(
            precio_anterior__isnull=True,
        ).exclude(
            precio_nuevo__isnull=True,
        ).exclude(
            precio_anterior=F('precio_nuevo'),
        ).order_by('-fecha')[:50]:
            cambios_precio.append({
                'fecha': rec.fecha.strftime('%d/%m/%Y') if rec.fecha else '',
                'sku': rec.producto_talla.sku if rec.producto_talla else '',
                'producto': rec.producto_talla.producto.articulo if rec.producto_talla and rec.producto_talla.producto else '',
                'precio_anterior': rec.precio_anterior,
                'precio_nuevo': rec.precio_nuevo,
                'diferencia': (rec.precio_nuevo or 0) - (rec.precio_anterior or 0),
                'sucursal': rec.sucursal_destino.alias if rec.sucursal_destino else '',
                'proveedor': rec.dte.emisor.nombre if rec.dte and rec.dte.emisor else '',
            })

        return JsonResponse({
            'success': True,
            'resumen': {
                'total_items': total_items,
                'total_unidades': total_unidades,
                'total_reposicion': total_reposicion,
                'total_nuevo': total_nuevo,
                'total_historicas': total_historicas,
                'total_con_cambio_precio': total_con_cambio_precio,
            },
            'por_sucursal': por_sucursal_data,
            'por_proveedor': por_proveedor_data,
            'cambios_precio': cambios_precio,
            'parametros': {
                'fecha_inicio': fecha_inicio_dt.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin_dt.strftime('%d/%m/%Y'),
            },
        })

    except Exception as e:
        logger.exception("Error al generar reporte de recepciones detallado")
        return JsonResponse({
            'success': False,
            'error': str(e),
        }, status=500)


@require_GET
@login_required
def api_reporte_despachos_detallado(request):
    """
    API mejorada para despachos con datos reales de recepción,
    desglose por sucursal destino y split reposición/nuevo.
    """
    try:
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        proveedor_id = request.GET.get('proveedor_id')

        if not fecha_inicio or not fecha_fin:
            fecha_fin_dt = timezone.localdate()
            fecha_inicio_dt = fecha_fin_dt - timedelta(days=30)
        else:
            fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d').date()

        dtes_compra = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            fecha_emision__range=[fecha_inicio_dt, fecha_fin_dt],
        ).select_related('emisor')

        if proveedor_id:
            dtes_compra = dtes_compra.filter(emisor_id=proveedor_id)

        despachos = []
        for dte in dtes_compra.order_by('-fecha_emision'):
            productos_dte = Dte_Productos.objects.filter(dte=dte).select_related(
                'producto_talla__producto',
            )
            recepciones = Productos_Recepcionados.objects.filter(
                dte=dte,
            ).select_related('sucursal_destino', 'producto_talla__producto')

            total_esperado = sum(p.cantidad for p in productos_dte)
            total_recibido = sum(r.stockArribado for r in recepciones)

            sucursales_destino = {}
            for rec in recepciones:
                suc_name = rec.sucursal_destino.alias if rec.sucursal_destino else 'Sin asignar'
                if suc_name not in sucursales_destino:
                    sucursales_destino[suc_name] = {'unidades': 0, 'reposicion': 0, 'nuevo': 0}
                sucursales_destino[suc_name]['unidades'] += rec.stockArribado
                if rec.es_reposicion:
                    sucursales_destino[suc_name]['reposicion'] += rec.stockArribado
                else:
                    sucursales_destino[suc_name]['nuevo'] += rec.stockArribado

            despachos.append({
                'dte_id': dte.id,
                'numero_dte': dte.numero_dte,
                'tipo_documento': dte.tipo_documento,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
                'proveedor': dte.emisor.nombre if dte.emisor else '',
                'rut_proveedor': dte.emisor.rut if dte.emisor else '',
                'total': float(dte.total),
                'estado': dte.estado_dte,
                'total_esperado': total_esperado,
                'total_recibido': total_recibido,
                'porcentaje_recepcion': round(total_recibido / total_esperado * 100, 1) if total_esperado > 0 else 0,
                'sucursales_destino': [
                    {'sucursal': k, **v} for k, v in sucursales_destino.items()
                ],
            })

        resumen = {
            'total_documentos': len(despachos),
            'total_esperado': sum(d['total_esperado'] for d in despachos),
            'total_recibido': sum(d['total_recibido'] for d in despachos),
            'monto_total': sum(d['total'] for d in despachos),
        }
        if resumen['total_esperado'] > 0:
            resumen['porcentaje_recepcion_global'] = round(
                resumen['total_recibido'] / resumen['total_esperado'] * 100, 1
            )
        else:
            resumen['porcentaje_recepcion_global'] = 0

        return JsonResponse({
            'success': True,
            'despachos': despachos,
            'resumen': resumen,
            'parametros': {
                'fecha_inicio': fecha_inicio_dt.strftime('%d/%m/%Y'),
                'fecha_fin': fecha_fin_dt.strftime('%d/%m/%Y'),
            },
        })

    except Exception as e:
        logger.exception("Error al generar reporte de despachos detallado")
        return JsonResponse({
            'success': False,
            'error': str(e),
        }, status=500)


# ====================================================================
# REPORTE: RENDIMIENTO POR PROVEEDOR
# Compra -> Recepcion -> Venta a publico
# ====================================================================

@login_required
def ver_reporte_rendimiento_proveedor(request):
    return render(request, 'vistas/modulo_reportes/reporte_rendimiento_proveedor.html')


@require_GET
@login_required
def api_reporte_rendimiento_proveedor(request):
    """Metricas de rendimiento: comprados, recepcionados, vendidos a publico por proveedor."""
    try:
        anio = int(request.GET.get('anio', timezone.localdate().year))
        proveedor_id = request.GET.get('proveedor_id')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')

        compras_qs = Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'])
        dtes_compra_qs = Dte.objects.filter(tipo_transaccion='COMPRA')
        if fecha_inicio:
            dtes_compra_qs = dtes_compra_qs.filter(fecha_emision__gte=fecha_inicio)
        if fecha_fin:
            dtes_compra_qs = dtes_compra_qs.filter(fecha_emision__lte=fecha_fin)
        if not fecha_inicio and not fecha_fin:
            dtes_compra_qs = dtes_compra_qs.filter(fecha_emision__year=anio)
        if proveedor_id:
            dtes_compra_qs = dtes_compra_qs.filter(emisor_id=proveedor_id)

        # Para vinculaciones retroactivas, fecha_recepcion lleva la fecha real
        # de la compra; para recepciones normales puede ser NULL y usamos `fecha`.
        rec_path = 'compras_producto__compras_producto_talla__productos_recepcionados'
        recep_filter = (
            Q(**{f'{rec_path}__fecha_recepcion__year': anio}) |
            (Q(**{f'{rec_path}__fecha_recepcion__isnull': True}) &
             Q(**{f'{rec_path}__fecha__year': anio}))
        )
        if fecha_inicio:
            recep_filter &= (
                Q(**{f'{rec_path}__fecha_recepcion__date__gte': fecha_inicio}) |
                (Q(**{f'{rec_path}__fecha_recepcion__isnull': True}) &
                 Q(**{f'{rec_path}__fecha__gte': fecha_inicio}))
            )
        if fecha_fin:
            recep_filter &= (
                Q(**{f'{rec_path}__fecha_recepcion__date__lte': fecha_fin}) |
                (Q(**{f'{rec_path}__fecha_recepcion__isnull': True}) &
                 Q(**{f'{rec_path}__fecha__lte': fecha_fin}))
            )

        ids_con_recep = set(Compras.objects.filter(recep_filter).values_list('id', flat=True).distinct())
        ids_del_anio = set(Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'], fecha__year=anio).values_list('id', flat=True))
        ids_total = ids_con_recep | ids_del_anio

        if proveedor_id:
            compras_qs = compras_qs.filter(empresa_id=proveedor_id, id__in=ids_total)
        else:
            compras_qs = compras_qs.filter(id__in=ids_total)

        prov_ids = set(compras_qs.values_list('empresa_id', flat=True).distinct())
        prov_ids.update(dtes_compra_qs.values_list('emisor_id', flat=True).distinct())
        prov_ids.discard(None)
        proveedores = Empresa.objects.filter(id__in=prov_ids).order_by('nombre')

        results = []
        for prov in proveedores:
            c_ids = list(compras_qs.filter(empresa=prov).values_list('id', flat=True))
            dtes_prov = dtes_compra_qs.filter(emisor=prov).annotate(
                lineas_inventariables=Count(
                    'dte_productos',
                    filter=Q(dte_productos__productoTalla__isnull=False),
                )
            )
            dtes_no_inventariables = dtes_prov.filter(lineas_inventariables=0)
            docs_no_inventariables = dtes_no_inventariables.count()
            monto_no_inventariable = dtes_no_inventariables.aggregate(
                total=Sum('monto_con_iva')
            )['total'] or 0

            if not c_ids and not docs_no_inventariables:
                continue

            cpt_qs = Compras_Producto_Talla.objects.filter(compra_producto__compras_id__in=c_ids)
            pares_comprados = cpt_qs.aggregate(total=Sum('stock'))['total'] or 0
            inversion = cpt_qs.aggregate(total=Sum(F('compra_producto__costo') * F('stock')))['total'] or 0

            recep_qs = Productos_Recepcionados.objects.filter(compra_producto_talla__compra_producto__compras_id__in=c_ids)
            if fecha_inicio:
                recep_qs = recep_qs.filter(
                    Q(fecha_recepcion__date__gte=fecha_inicio) |
                    (Q(fecha_recepcion__isnull=True) & Q(fecha__gte=fecha_inicio))
                )
            if fecha_fin:
                recep_qs = recep_qs.filter(
                    Q(fecha_recepcion__date__lte=fecha_fin) |
                    (Q(fecha_recepcion__isnull=True) & Q(fecha__lte=fecha_fin))
                )
            if not fecha_inicio and not fecha_fin:
                recep_qs = recep_qs.filter(
                    Q(fecha_recepcion__year=anio) |
                    (Q(fecha_recepcion__isnull=True) & Q(fecha__year=anio))
                )
            pares_recepcionados = recep_qs.aggregate(total=Sum('stockArribado'))['total'] or 0

            # Vendidos: IDs directos + match por articulo cross-sucursal
            pt_directos = set(recep_qs.filter(producto_talla__isnull=False).values_list('producto_talla_id', flat=True))
            articulos = set(Compras_Producto.objects.filter(compras_id__in=c_ids).values_list('nombre', flat=True))
            pt_articulo = set()
            if articulos:
                pt_articulo = set(Producto_Talla.objects.filter(producto__articulo__in=articulos).values_list('id', flat=True))
            pts = pt_directos | pt_articulo

            pares_vendidos = 0
            venta_total = 0.0
            if pts:
                tf = {'ProductoTalla_id__in': pts, 'idTicket__estado__in': ['PAGADO', 'PENDIENTE'],
                      'idTicket__modulo_origen__in': ['VENTA_PUBLICO', 'POS', 'ECOMMERCE']}
                if fecha_inicio and fecha_fin:
                    tf['idTicket__fecha__range'] = [fecha_inicio, fecha_fin]
                else:
                    tf['idTicket__fecha__year'] = anio
                if sucursal_id:
                    tf['idTicket__sucursal_id'] = sucursal_id
                vd = Ticket_Productos.objects.filter(**tf).aggregate(p=Sum('stock'), v=Sum('subtotal'))
                pares_vendidos = vd['p'] or 0
                venta_total = float(vd['v'] or 0)

            pct_r = round((pares_recepcionados / pares_comprados) * 100, 1) if pares_comprados > 0 else 0
            pct_v = round((pares_vendidos / pares_recepcionados) * 100, 1) if pares_recepcionados > 0 else 0
            inv_f = float(inversion)
            costo_v = inv_f * (pares_vendidos / pares_comprados) if pares_comprados > 0 else 0

            results.append({
                'proveedor_id': prov.id, 'proveedor_nombre': prov.nombre, 'proveedor_rut': prov.rut or '',
                'pares_comprados': pares_comprados, 'pares_recepcionados': pares_recepcionados,
                'pares_vendidos': pares_vendidos, 'pct_recepcion': pct_r, 'pct_venta': pct_v,
                'inversion_total': inv_f, 'venta_total': venta_total,
                'margen': round(venta_total - costo_v, 0), 'stock_disponible': pares_recepcionados - pares_vendidos,
                'pendiente_recepcion': max((pares_comprados or 0) - (pares_recepcionados or 0), 0),
                'documentos_no_inventariables': docs_no_inventariables,
                'monto_no_inventariable': float(monto_no_inventariable),
                'metodologia': (
                    'Incluye compras por concepto/sin detalle inventariable; se informan como monto no inventariable.'
                    if docs_no_inventariables else ''
                ),
            })

        results.sort(key=lambda x: x['inversion_total'], reverse=True)
        tc = sum(r['pares_comprados'] for r in results)
        tr = sum(r['pares_recepcionados'] for r in results)
        tv = sum(r['pares_vendidos'] for r in results)

        return JsonResponse({'success': True, 'kpis': {
            'total_proveedores': len(results), 'total_comprados': tc, 'total_recepcionados': tr,
            'total_vendidos': tv, 'pct_recepcion_global': round((tr / tc) * 100, 1) if tc > 0 else 0,
            'pct_venta_global': round((tv / tr) * 100, 1) if tr > 0 else 0,
            'total_inversion': sum(r['inversion_total'] for r in results),
            'total_monto_no_inventariable': sum(r['monto_no_inventariable'] for r in results),
            'total_documentos_no_inventariables': sum(r['documentos_no_inventariables'] for r in results),
            'total_pendiente_recepcion': sum(r['pendiente_recepcion'] for r in results),
            'total_venta': sum(r['venta_total'] for r in results),
            'total_margen': sum(r['margen'] for r in results),
        }, 'proveedores': results, 'metodologia': {
            'comprados': 'Unidades desde Compras_Producto_Talla de compras activas/completadas.',
            'recepcionados': 'Unidades desde Productos_Recepcionados en el periodo consultado.',
            'no_inventariable': 'DTE de compra sin lineas con Producto_Talla se muestra como monto por concepto; no suma pares ni recepcion.',
        }})

    except Exception as e:
        logger.exception("Error al generar rendimiento por proveedor")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def exportar_rendimiento_proveedor_excel(request):
    """Exporta rendimiento por proveedor a Excel."""
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        resp_api = api_reporte_rendimiento_proveedor(request)
        data = json.loads(resp_api.content)
        if not data.get('success'):
            return JsonResponse({'error': 'No se pudo generar'}, status=500)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Rendimiento Proveedor'
        hf = Font(bold=True, color='FFFFFF', size=11)
        hfill = PatternFill(start_color='405189', end_color='405189', fill_type='solid')
        brd = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

        headers = ['Proveedor', 'RUT', 'Comprados', 'Recepcionados', 'Vendidos',
                    '% Recep', '% Venta', 'Inversion inventariable', 'Monto no inventariable',
                    'Docs no inventariables', 'Venta Total', 'Margen', 'Pend. recepcion', 'Stock Disp.']
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=h)
            c.font, c.fill, c.alignment, c.border = hf, hfill, Alignment(horizontal='center'), brd

        for i, p in enumerate(data['proveedores'], 2):
            vals = [p['proveedor_nombre'], p['proveedor_rut'], p['pares_comprados'], p['pares_recepcionados'],
                    p['pares_vendidos'], p['pct_recepcion'], p['pct_venta'], p['inversion_total'],
                    p.get('monto_no_inventariable', 0), p.get('documentos_no_inventariables', 0),
                    p['venta_total'], p['margen'], p.get('pendiente_recepcion', 0), p['stock_disponible']]
            for col, v in enumerate(vals, 1):
                c = ws.cell(row=i, column=col, value=v)
                c.border = brd
                if col >= 3:
                    c.alignment = Alignment(horizontal='right')

        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or '')) for c in col) + 3, 25)

        resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename=rendimiento_proveedor_{request.GET.get("anio", timezone.localdate().year)}.xlsx'
        wb.save(resp)
        return resp
    except Exception as e:
        logger.exception("Error al exportar rendimiento por proveedor")
        return JsonResponse({'error': str(e)}, status=500)


# ========== REPORTE DE VENTAS POR INTERNET ==========


def _decimal_seguro(valor, default=Decimal('0')):
    """Convierte valores de snapshots ecommerce sin romper el reporte."""
    try:
        return Decimal(str(valor if valor not in (None, '') else default))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _entero_seguro(valor, default=0):
    try:
        return int(_decimal_seguro(valor, Decimal(default)))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _rango_ventas_internet(request):
    hoy = timezone.localdate()
    if request.GET.get('todo_historico') == '1':
        return None, None, True

    inicio_default = hoy.replace(month=1, day=1)
    try:
        fecha_inicio = datetime.strptime(
            request.GET.get('fecha_inicio') or inicio_default.isoformat(),
            '%Y-%m-%d',
        ).date()
        fecha_fin = datetime.strptime(
            request.GET.get('fecha_fin') or hoy.isoformat(),
            '%Y-%m-%d',
        ).date()
    except ValueError as exc:
        raise ValidationError('Las fechas deben usar el formato YYYY-MM-DD.') from exc

    if fecha_inicio > fecha_fin:
        raise ValidationError('La fecha de inicio no puede ser posterior a la fecha de fin.')
    return fecha_inicio, fecha_fin, False


def _nombre_usuario(usuario):
    if not usuario:
        return 'Sin operador'
    return usuario.get_full_name() or usuario.username


def _fecha_hora_ticket(ticket):
    if ticket.created_at:
        return timezone.localtime(ticket.created_at)
    if ticket.fecha:
        hora = ticket.hora or datetime.min.time()
        naive = datetime.combine(ticket.fecha, hora)
        return timezone.make_aware(naive) if timezone.is_naive(naive) else naive
    return None


def _decimal_desde_int(valor):
    return Decimal(str(valor or 0))


def _nombre_plataforma(valor):
    valor = (valor or '').strip()
    return valor or 'Sin plataforma'


def _codigos_canal_ecommerce(valor):
    normalizado = (valor or '').strip().upper()
    if not normalizado:
        return []
    codigos = {normalizado}
    for codigo, nombre in CANAL_ECOMMERCE_CHOICES:
        if normalizado in {codigo.upper(), nombre.upper()}:
            codigos.add(codigo)
    return sorted(codigos)


def _normalizar_tipo_boleta(tipo_documento):
    tipo = (tipo_documento or '').strip().upper().replace('_', ' ')
    if 'BOLETA' not in tipo:
        return ''
    if 'EXENTA' in tipo:
        return 'Boleta exenta'
    if 'ELECTRONICA' in tipo:
        return 'Boleta electronica'
    if 'PAPEL' in tipo or tipo == 'BOLETA':
        return 'Boleta papel/manual'
    return 'Boleta'


def _tipo_boleta_emitida(ticket, dte=None):
    if dte and getattr(dte, 'tipo_documento', None):
        return _normalizar_tipo_boleta(dte.tipo_documento)
    if ticket.folio_dte:
        return _normalizar_tipo_boleta(ticket.tipo_dte)
    return ''


def _linea_producto_nombre(linea):
    if linea.ProductoTalla and linea.ProductoTalla.producto:
        producto = linea.ProductoTalla.producto
        return producto.descripcion or producto.articulo or 'Producto sin nombre'
    return linea.descripcion_linea or 'Linea manual'


def _linea_producto_sku(linea):
    if linea.ProductoTalla:
        return str(linea.ProductoTalla.sku or 'Sin SKU')
    return 'MANUAL'


def _construir_reporte_ventas_internet(request, paginar=True):
    fecha_inicio, fecha_fin, todo_historico = _rango_ventas_internet(request)
    sucursales_permitidas = obtener_sucursales_usuario(request.user).select_related('empresa')
    sucursal_ids = list(sucursales_permitidas.values_list('id', flat=True))

    queryset = Ticket.objects.filter(
        estado='PAGADO',
        sucursal_id__in=sucursal_ids,
    ).filter(
        Q(modulo_origen='ECOMMERCE') | Q(pagos__metodo_pago='VENTA_INTERNET')
    ).select_related(
        'sucursal__empresa', 'vendedor',
    ).prefetch_related(
        'pagos',
        'ticket_productos__ProductoTalla__producto',
        'pedidos_ecommerce__dte',
        'pedidos_ecommerce__facturado_por',
    ).distinct().order_by('-fecha', '-hora', '-created_at', '-id')

    if not todo_historico:
        queryset = queryset.filter(fecha__gte=fecha_inicio, fecha__lte=fecha_fin)

    empresa_id = request.GET.get('empresa_id', '').strip()
    sucursal_id = request.GET.get('sucursal_id', '').strip()
    plataforma = (request.GET.get('plataforma') or request.GET.get('canal') or '').strip()
    origen = request.GET.get('origen', '').strip().upper()
    vendedor_id = request.GET.get('vendedor_id', '').strip()
    busqueda = request.GET.get('q', '').strip()

    if empresa_id:
        queryset = queryset.filter(sucursal__empresa_id=empresa_id)
    if sucursal_id:
        if not str(sucursal_id).isdigit() or int(sucursal_id) not in sucursal_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(sucursal_id=sucursal_id)
    if plataforma:
        if plataforma == '__SIN_PLATAFORMA__':
            queryset = queryset.filter(
                Q(pagos__metodo_pago='VENTA_INTERNET', pagos__tipo_tarjeta__isnull=True)
                | Q(pagos__metodo_pago='VENTA_INTERNET', pagos__tipo_tarjeta='')
            )
        else:
            queryset = queryset.filter(
                Q(pagos__metodo_pago='VENTA_INTERNET', pagos__tipo_tarjeta__iexact=plataforma)
                | Q(pedidos_ecommerce__canal_origen__in=_codigos_canal_ecommerce(plataforma))
            ).distinct()
    if origen == 'ECOMMERCE':
        queryset = queryset.filter(modulo_origen='ECOMMERCE')
    elif origen == 'POS':
        queryset = queryset.exclude(modulo_origen='ECOMMERCE').filter(pagos__metodo_pago='VENTA_INTERNET')
    if vendedor_id:
        queryset = queryset.filter(vendedor_id=vendedor_id)
    if busqueda:
        queryset = queryset.filter(
            Q(correlativo__icontains=busqueda)
            | Q(cliente_nombre__icontains=busqueda)
            | Q(cliente_rut__icontains=busqueda)
            | Q(pagos__voucher__icontains=busqueda)
            | Q(pagos__tipo_tarjeta__icontains=busqueda)
            | Q(pedidos_ecommerce__numero_ticket_rm__icontains=busqueda)
            | Q(pedidos_ecommerce__numero_pedido_canal__icontains=busqueda)
            | Q(pedidos_ecommerce__correlativo__icontains=busqueda)
            | Q(pedidos_ecommerce__cliente_nombre__icontains=busqueda)
            | Q(pedidos_ecommerce__cliente_documento__icontains=busqueda)
        ).distinct()

    empresas = {}
    plataformas = defaultdict(lambda: {'tickets': set(), 'ventas': Decimal('0'), 'unidades': 0})
    origenes = defaultdict(lambda: {'tickets': 0, 'ventas': Decimal('0'), 'unidades': 0})
    vendedores = defaultdict(lambda: {'tickets': 0, 'ventas': Decimal('0'), 'unidades': 0})
    boletas_empresas = defaultdict(lambda: {
        'id': None,
        'empresa': '',
        'rut': '',
        'boletas_total': 0,
        'boleta_electronica': 0,
        'boleta_papel': 0,
        'boleta_exenta': 0,
        'otras_boletas': 0,
        'tickets': set(),
        'monto_internet': Decimal('0'),
    })
    productos = defaultdict(lambda: {
        'sku': '', 'producto': '', 'unidades': 0, 'venta_internet_asignada': Decimal('0'),
        'venta_ticket_total': Decimal('0'), 'tickets': set(), 'plataformas': set(), 'origenes': set(),
    })
    ventas_diarias = defaultdict(lambda: {'tickets': 0, 'ventas': Decimal('0'), 'unidades': 0})
    detalle = []
    alertas = {
        'ecommerce_sin_pago_internet': {'count': 0, 'monto': 0, 'tickets': []},
        'tickets_mixtos': {'count': 0, 'monto_internet': 0, 'tickets': []},
        'sin_plataforma': {'count': 0, 'monto': 0, 'tickets': []},
    }
    total_ventas = Decimal('0')
    total_unidades = 0
    total_ecommerce = 0
    total_pos = 0
    total_boletas = 0
    monto_fallback_ecommerce = Decimal('0')

    for ticket in queryset:
        pagos_internet = [pago for pago in ticket.pagos.all() if pago.metodo_pago == 'VENTA_INTERNET']
        pedido = next(iter(ticket.pedidos_ecommerce.all()), None)
        es_ecommerce = ticket.modulo_origen == 'ECOMMERCE' or pedido is not None
        monto_pagos_internet = sum((pago.monto or 0) for pago in pagos_internet)

        if monto_pagos_internet <= 0 and not es_ecommerce:
            continue

        usa_fallback = False
        if monto_pagos_internet > 0:
            monto_internet = _decimal_desde_int(monto_pagos_internet)
        else:
            monto_internet = _decimal_desde_int(ticket.total)
            usa_fallback = True
            monto_fallback_ecommerce += monto_internet

        total_ticket = _decimal_desde_int(ticket.total)
        proporcion_internet = (monto_internet / total_ticket) if total_ticket > 0 else Decimal('0')
        proporcion_internet = min(proporcion_internet, Decimal('1'))
        plataformas_ticket = sorted({
            _nombre_plataforma(pago.tipo_tarjeta)
            for pago in pagos_internet
        })
        if not plataformas_ticket and pedido:
            plataformas_ticket = [_nombre_plataforma(pedido.get_canal_origen_display())]
        if not plataformas_ticket:
            plataformas_ticket = ['Sin plataforma']

        origen_codigo = 'ECOMMERCE' if es_ecommerce else 'POS'
        origen_nombre = 'Ecommerce / AllConnected' if es_ecommerce else 'POS Venta Internet'
        fecha_dt = _fecha_hora_ticket(ticket)
        fecha_local = fecha_dt.date() if fecha_dt else ticket.fecha
        fecha_key = fecha_local.isoformat() if fecha_local else ''
        lineas = list(ticket.ticket_productos.all())
        unidades_ticket = sum(max(linea.stock or 0, 0) for linea in lineas)

        total_ventas += monto_internet
        total_unidades += unidades_ticket
        if es_ecommerce:
            total_ecommerce += 1
        else:
            total_pos += 1

        if usa_fallback:
            alertas['ecommerce_sin_pago_internet']['count'] += 1
            alertas['ecommerce_sin_pago_internet']['monto'] += int(monto_internet)
            if len(alertas['ecommerce_sin_pago_internet']['tickets']) < 20:
                alertas['ecommerce_sin_pago_internet']['tickets'].append(ticket.correlativo)
        if monto_pagos_internet > 0 and total_ticket > monto_internet:
            alertas['tickets_mixtos']['count'] += 1
            alertas['tickets_mixtos']['monto_internet'] += int(monto_internet)
            if len(alertas['tickets_mixtos']['tickets']) < 20:
                alertas['tickets_mixtos']['tickets'].append(ticket.correlativo)
        if 'Sin plataforma' in plataformas_ticket:
            alertas['sin_plataforma']['count'] += 1
            alertas['sin_plataforma']['monto'] += int(monto_internet)
            if len(alertas['sin_plataforma']['tickets']) < 20:
                alertas['sin_plataforma']['tickets'].append(ticket.correlativo)

        empresa = ticket.sucursal.empresa
        empresa_data = empresas.setdefault(empresa.id, {
            'id': empresa.id,
            'nombre': empresa.nombre,
            'rut': empresa.rut,
            'tickets': 0,
            'ventas': Decimal('0'),
            'unidades': 0,
            'sucursales': {},
        })
        sucursal_data = empresa_data['sucursales'].setdefault(ticket.sucursal_id, {
            'id': ticket.sucursal_id,
            'nombre': ticket.sucursal.alias,
            'tickets': 0,
            'ventas': Decimal('0'),
            'unidades': 0,
        })
        empresa_data['tickets'] += 1
        empresa_data['ventas'] += monto_internet
        empresa_data['unidades'] += unidades_ticket
        sucursal_data['tickets'] += 1
        sucursal_data['ventas'] += monto_internet
        sucursal_data['unidades'] += unidades_ticket

        for plataforma_item in plataformas_ticket:
            plataformas[plataforma_item]['tickets'].add(ticket.id)
            if pagos_internet:
                ventas_plataforma = sum(
                    _decimal_desde_int(pago.monto)
                    for pago in pagos_internet
                    if _nombre_plataforma(pago.tipo_tarjeta) == plataforma_item
                )
            else:
                ventas_plataforma = monto_internet
            plataformas[plataforma_item]['ventas'] += ventas_plataforma
            plataformas[plataforma_item]['unidades'] += unidades_ticket

        origenes[origen_codigo]['codigo'] = origen_codigo
        origenes[origen_codigo]['nombre'] = origen_nombre
        origenes[origen_codigo]['tickets'] += 1
        origenes[origen_codigo]['ventas'] += monto_internet
        origenes[origen_codigo]['unidades'] += unidades_ticket

        vendedor = ticket.vendedor
        vendedor_key = str(vendedor.id) if vendedor else 'sin-vendedor'
        vendedor_nombre = str(vendedor) if vendedor else 'Sin vendedor asociado'
        vendedores[vendedor_key]['id'] = vendedor.id if vendedor else None
        vendedores[vendedor_key]['nombre'] = vendedor_nombre
        vendedores[vendedor_key]['codigo'] = vendedor.codigo_vendedor if vendedor else '-'
        vendedores[vendedor_key]['tickets'] += 1
        vendedores[vendedor_key]['ventas'] += monto_internet
        vendedores[vendedor_key]['unidades'] += unidades_ticket

        if fecha_key:
            ventas_diarias[fecha_key]['tickets'] += 1
            ventas_diarias[fecha_key]['ventas'] += monto_internet
            ventas_diarias[fecha_key]['unidades'] += unidades_ticket

        for linea in lineas:
            cantidad = max(linea.stock or 0, 0)
            subtotal_linea = _decimal_desde_int(linea.subtotal)
            venta_asignada = subtotal_linea * proporcion_internet
            sku = _linea_producto_sku(linea)
            nombre = _linea_producto_nombre(linea)
            producto_key = f'{sku}|{nombre}'
            producto = productos[producto_key]
            producto['sku'] = sku
            producto['producto'] = nombre
            producto['unidades'] += cantidad
            producto['venta_internet_asignada'] += venta_asignada
            producto['venta_ticket_total'] += subtotal_linea
            producto['tickets'].add(ticket.id)
            producto['plataformas'].update(plataformas_ticket)
            producto['origenes'].add(origen_nombre)

        dte = pedido.dte if pedido and pedido.dte_id else None
        dte_label = ''
        if dte:
            dte_label = f'{dte.tipo_documento} #{dte.numero_documento}'
        elif ticket.folio_dte:
            dte_label = f'{ticket.tipo_dte or "DTE"} #{ticket.folio_dte}'
        tipo_boleta = _tipo_boleta_emitida(ticket, dte)
        if tipo_boleta:
            total_boletas += 1
            boleta_empresa = boletas_empresas[empresa.id]
            boleta_empresa['id'] = empresa.id
            boleta_empresa['empresa'] = empresa.nombre
            boleta_empresa['rut'] = empresa.rut
            boleta_empresa['boletas_total'] += 1
            boleta_empresa['tickets'].add(ticket.id)
            boleta_empresa['monto_internet'] += monto_internet
            if tipo_boleta == 'Boleta electronica':
                boleta_empresa['boleta_electronica'] += 1
            elif tipo_boleta == 'Boleta papel/manual':
                boleta_empresa['boleta_papel'] += 1
            elif tipo_boleta == 'Boleta exenta':
                boleta_empresa['boleta_exenta'] += 1
            else:
                boleta_empresa['otras_boletas'] += 1
        fecha_venta_str = fecha_dt.strftime('%d/%m/%Y %H:%M') if fecha_dt else ''
        fecha_venta_iso = fecha_dt.isoformat() if fecha_dt else ''
        fecha_recepcion = timezone.localtime(pedido.fecha_recepcion).strftime('%d/%m/%Y %H:%M') if pedido and pedido.fecha_recepcion else ''
        fecha_facturacion = timezone.localtime(pedido.fecha_facturacion).strftime('%d/%m/%Y %H:%M') if pedido and pedido.fecha_facturacion else ''
        operador = _nombre_usuario(pedido.facturado_por) if pedido else ticket.responsable or 'POS'

        detalle.append({
            'id': ticket.id,
            'ticket_id': ticket.id,
            'correlativo': ticket.correlativo,
            'fecha': fecha_venta_str,
            'fecha_venta': fecha_venta_str,
            'fecha_iso': fecha_venta_iso,
            'fecha_recepcion': fecha_recepcion,
            'fecha_facturacion': fecha_facturacion,
            'origen': origen_nombre,
            'origen_codigo': origen_codigo,
            'plataforma': ', '.join(plataformas_ticket),
            'voucher': ', '.join([pago.voucher for pago in pagos_internet if pago.voucher]) or '-',
            'pedido_ecommerce_id': pedido.id if pedido else None,
            'ticket_rm': pedido.numero_ticket_rm if pedido else f'Ticket #{ticket.correlativo}',
            'numero_ticket_rm': pedido.numero_ticket_rm if pedido else '',
            'pedido_canal': pedido.numero_pedido_canal if pedido else '',
            'numero_pedido_canal': pedido.numero_pedido_canal if pedido else '',
            'dte': dte_label or '-',
            'tipo_boleta': tipo_boleta,
            'empresa': empresa.nombre,
            'sucursal': ticket.sucursal.alias,
            'cliente': ticket.cliente_nombre or (pedido.cliente_nombre if pedido else ''),
            'documento_cliente': ticket.cliente_rut or (pedido.cliente_documento if pedido else '') or '-',
            'vendedor': vendedor_nombre,
            'codigo_vendedor': vendedor.codigo_vendedor if vendedor else '-',
            'operador': operador,
            'unidades': unidades_ticket,
            'monto_internet': float(monto_internet),
            'total': float(monto_internet),
            'total_ticket': float(total_ticket),
            'es_mixto': monto_pagos_internet > 0 and total_ticket > monto_internet,
            'usa_fallback': usa_fallback,
            'pos_url': f'/app/pos-dashboard/?ticket={ticket.correlativo}',
            'ecommerce_url': f'/app/ecommerce/pedidos/{pedido.id}/' if pedido else '',
        })

    total_pedidos = len(detalle)
    for empresa in empresas.values():
        empresa['pedidos'] = empresa['tickets']
        empresa['ticket_promedio'] = float(empresa['ventas'] / empresa['tickets']) if empresa['tickets'] else 0
        empresa['participacion'] = float(empresa['ventas'] / total_ventas * 100) if total_ventas else 0
        empresa['ventas'] = float(empresa['ventas'])
        empresa['sucursales'] = sorted(
            [
                {
                    **sucursal,
                    'pedidos': sucursal['tickets'],
                    'ventas': float(sucursal['ventas']),
                    'ticket_promedio': float(sucursal['ventas'] / sucursal['tickets']) if sucursal['tickets'] else 0,
                }
                for sucursal in empresa['sucursales'].values()
            ],
            key=lambda item: item['ventas'],
            reverse=True,
        )

    productos_data = sorted([
        {
            'sku': item['sku'],
            'producto': item['producto'],
            'unidades': item['unidades'],
            'venta_internet_asignada': float(item['venta_internet_asignada']),
            'venta_ticket_total': float(item['venta_ticket_total']),
            'ventas': float(item['venta_internet_asignada']),
            'pedidos': len(item['tickets']),
            'tickets': len(item['tickets']),
            'canales': ', '.join(sorted(item['plataformas'])),
            'plataformas': ', '.join(sorted(item['plataformas'])),
            'origenes': ', '.join(sorted(item['origenes'])),
            'precio_promedio': float(item['venta_internet_asignada'] / item['unidades']) if item['unidades'] else 0,
        }
        for item in productos.values()
    ], key=lambda item: (item['venta_internet_asignada'], item['unidades']), reverse=True)

    vendedores_data = sorted([
        {
            **item,
            'pedidos': item['tickets'],
            'ventas': float(item['ventas']),
            'ticket_promedio': float(item['ventas'] / item['tickets']) if item['tickets'] else 0,
        }
        for item in vendedores.values()
    ], key=lambda item: item['ventas'], reverse=True)
    vendedores_reales = [item for item in vendedores_data if item['id'] is not None]
    pedidos_sin_vendedor = vendedores.get('sin-vendedor', {}).get('tickets', 0)

    boletas_empresas_data = sorted([
        {
            'id': item['id'],
            'empresa': item['empresa'],
            'nombre': item['empresa'],
            'rut': item['rut'],
            'boletas_total': item['boletas_total'],
            'boleta_electronica': item['boleta_electronica'],
            'boleta_papel': item['boleta_papel'],
            'boleta_exenta': item['boleta_exenta'],
            'otras_boletas': item['otras_boletas'],
            'tickets': len(item['tickets']),
            'monto_internet': float(item['monto_internet']),
            'participacion': (item['boletas_total'] / total_boletas * 100) if total_boletas else 0,
        }
        for item in boletas_empresas.values()
    ], key=lambda item: (item['boletas_total'], item['monto_internet']), reverse=True)

    paginator = Paginator(detalle, min(max(_entero_seguro(request.GET.get('page_size'), 25), 10), 100))
    pagina = paginator.get_page(request.GET.get('page', 1)) if paginar else None

    empresas_filtro = {}
    for sucursal in sucursales_permitidas:
        empresas_filtro[sucursal.empresa_id] = sucursal.empresa
    plataformas_filtro = set(
        TicketDetallePago.objects.filter(
            ticket__sucursal_id__in=sucursal_ids,
            metodo_pago='VENTA_INTERNET',
        )
        .exclude(tipo_tarjeta__isnull=True)
        .exclude(tipo_tarjeta='')
        .values_list('tipo_tarjeta', flat=True)
    )
    canal_display = dict(CANAL_ECOMMERCE_CHOICES)
    canales_ecommerce = PedidoEcommerce.objects.filter(
        ticket__sucursal_id__in=sucursal_ids,
    ).exclude(canal_origen__isnull=True).exclude(canal_origen='').values_list('canal_origen', flat=True).distinct()
    for canal in canales_ecommerce:
        plataformas_filtro.add(canal_display.get(canal, canal))
    vendedores_ticket_ids = Ticket.objects.filter(
        sucursal_id__in=sucursal_ids,
        vendedor__isnull=False,
    ).values_list('vendedor_id', flat=True).distinct()
    vendedores_filtro = Vendedor.objects.filter(
        Q(sucursales__id__in=sucursal_ids) | Q(id__in=vendedores_ticket_ids)
    ).distinct().order_by('nombre')

    return {
        'success': True,
        'periodo': {
            'inicio': fecha_inicio.isoformat() if fecha_inicio else '',
            'fin': fecha_fin.isoformat() if fecha_fin else '',
            'todo_historico': todo_historico,
            'label': 'Todo historico' if todo_historico else f'{fecha_inicio.isoformat()} al {fecha_fin.isoformat()}',
        },
        'resumen': {
            'ventas': float(total_ventas),
            'venta_internet': float(total_ventas),
            'pedidos': total_pedidos,
            'tickets': total_pedidos,
            'unidades': total_unidades,
            'ticket_promedio': float(total_ventas / total_pedidos) if total_pedidos else 0,
            'empresas': len(empresas),
            'sucursales': sum(len(empresa['sucursales']) for empresa in empresas.values()),
            'productos': len(productos_data),
            'plataformas': len(plataformas),
            'ecommerce_count': total_ecommerce,
            'pos_count': total_pos,
            'boletas': total_boletas,
            'boletas_empresas': len(boletas_empresas_data),
            'monto_fallback_ecommerce': float(monto_fallback_ecommerce),
            'vendedores': len(vendedores_reales),
            'vendedor_unico': len(vendedores_reales) == 1 and pedidos_sin_vendedor == 0,
            'pedidos_sin_vendedor': pedidos_sin_vendedor,
        },
        'empresas': sorted(empresas.values(), key=lambda item: item['ventas'], reverse=True),
        'canales': sorted([
            {
                'codigo': nombre,
                'nombre': nombre,
                'pedidos': len(item['tickets']),
                'tickets': len(item['tickets']),
                'ventas': float(item['ventas']),
                'unidades': item['unidades'],
                'participacion': float(item['ventas'] / total_ventas * 100) if total_ventas else 0,
            }
            for nombre, item in plataformas.items()
        ], key=lambda item: item['ventas'], reverse=True),
        'plataformas': sorted([
            {
                'codigo': nombre,
                'nombre': nombre,
                'tickets': len(item['tickets']),
                'ventas': float(item['ventas']),
                'unidades': item['unidades'],
                'participacion': float(item['ventas'] / total_ventas * 100) if total_ventas else 0,
            }
            for nombre, item in plataformas.items()
        ], key=lambda item: item['ventas'], reverse=True),
        'origenes': sorted([
            {
                'codigo': item['codigo'],
                'nombre': item['nombre'],
                'tickets': item['tickets'],
                'ventas': float(item['ventas']),
                'unidades': item['unidades'],
                'participacion': float(item['ventas'] / total_ventas * 100) if total_ventas else 0,
            }
            for item in origenes.values()
        ], key=lambda item: item['ventas'], reverse=True),
        'vendedores': vendedores_data,
        'boletas_empresas': boletas_empresas_data,
        'productos': productos_data,
        'ventas_diarias': [
            {'fecha': fecha, 'pedidos': item['tickets'], 'tickets': item['tickets'], 'ventas': float(item['ventas']), 'unidades': item['unidades']}
            for fecha, item in sorted(ventas_diarias.items())
        ],
        'detalle': list(pagina.object_list) if pagina else detalle,
        'alertas': alertas,
        'paginacion': {
            'pagina': pagina.number if pagina else 1,
            'paginas': paginator.num_pages if paginar else 1,
            'total': paginator.count,
            'page_size': paginator.per_page,
            'tiene_anterior': pagina.has_previous() if pagina else False,
            'tiene_siguiente': pagina.has_next() if pagina else False,
        },
        'filtros': {
            'empresas': [
                {'id': empresa.id, 'nombre': empresa.nombre}
                for empresa in sorted(empresas_filtro.values(), key=lambda item: item.nombre.lower())
            ],
            'sucursales': [
                {'id': sucursal.id, 'nombre': sucursal.alias, 'empresa_id': sucursal.empresa_id}
                for sucursal in sucursales_permitidas
            ],
            'canales': [],
            'plataformas': [
                {'codigo': item['nombre'], 'nombre': item['nombre']}
                for item in sorted(
                    [{'nombre': nombre} for nombre in plataformas_filtro],
                    key=lambda item: item['nombre'].lower(),
                )
            ],
            'origenes': [
                {'codigo': 'ECOMMERCE', 'nombre': 'Ecommerce / AllConnected'},
                {'codigo': 'POS', 'nombre': 'POS Venta Internet'},
            ],
            'vendedores': [
                {'id': vendedor.id, 'nombre': str(vendedor), 'codigo': vendedor.codigo_vendedor or '-'}
                for vendedor in vendedores_filtro
            ],
        },
    }


@login_required
def ver_reporte_ventas_internet(request):
    context = obtener_contexto_sucursales(request.user, request)
    return render(request, 'vistas/modulo_reportes/reporte_ventas_internet.html', context)


@require_GET
@login_required
def obtener_reporte_ventas_internet(request):
    try:
        return JsonResponse(_construir_reporte_ventas_internet(request))
    except ValidationError as exc:
        mensaje = exc.messages[0] if exc.messages else str(exc)
        return JsonResponse({'success': False, 'error': mensaje}, status=400)


@require_GET
@login_required
def exportar_reporte_ventas_internet(request):
    try:
        data = _construir_reporte_ventas_internet(request, paginar=False)
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        workbook = openpyxl.Workbook()
        resumen = workbook.active
        resumen.title = 'Resumen'
        encabezado = PatternFill('solid', fgColor='405189')
        fuente_encabezado = Font(color='FFFFFF', bold=True)

        resumen.append(['INFORME VENTAS INTERNET', f"{data['periodo']['inicio']} al {data['periodo']['fin']}"])
        resumen.append([])
        resumen.append(['Indicador', 'Valor'])
        indicadores = [
            ('Venta internet', data['resumen']['venta_internet']),
            ('Tickets', data['resumen']['tickets']),
            ('Unidades vendidas', data['resumen']['unidades']),
            ('Ticket promedio', data['resumen']['ticket_promedio']),
            ('Ecommerce / AllConnected', data['resumen']['ecommerce_count']),
            ('POS Venta Internet', data['resumen']['pos_count']),
            ('Boletas emitidas', data['resumen']['boletas']),
            ('Plataformas', data['resumen']['plataformas']),
            ('Fallback ecommerce sin pago internet', data['resumen']['monto_fallback_ecommerce']),
            ('Empresas', data['resumen']['empresas']),
            ('Sucursales', data['resumen']['sucursales']),
            ('Vendedores detectados', data['resumen']['vendedores']),
        ]
        for indicador in indicadores:
            resumen.append(indicador)

        productos_ws = workbook.create_sheet('Productos')
        productos_ws.append([
            'SKU', 'Producto', 'Unidades', 'Venta internet asignada', 'Venta ticket total',
            'Tickets', 'Precio promedio internet', 'Plataformas', 'Origenes',
        ])
        for producto in data['productos']:
            productos_ws.append([
                producto['sku'], producto['producto'], producto['unidades'],
                producto['venta_internet_asignada'], producto['venta_ticket_total'],
                producto['tickets'], producto['precio_promedio'], producto['plataformas'],
                producto['origenes'],
            ])

        plataformas_ws = workbook.create_sheet('Plataformas')
        plataformas_ws.append(['Plataforma', 'Tickets', 'Unidades', 'Venta', 'Participacion %'])
        for plataforma in data['plataformas']:
            plataformas_ws.append([
                plataforma['nombre'], plataforma['tickets'], plataforma['unidades'],
                plataforma['ventas'], plataforma['participacion'],
            ])

        boletas_ws = workbook.create_sheet('Boletas empresa')
        boletas_ws.append([
            'Empresa', 'RUT', 'Boletas total', 'Boleta electronica',
            'Boleta papel/manual', 'Boleta exenta', 'Otras boletas',
            'Tickets', 'Monto internet', 'Participacion %',
        ])
        for item in data['boletas_empresas']:
            boletas_ws.append([
                item['empresa'], item['rut'], item['boletas_total'],
                item['boleta_electronica'], item['boleta_papel'],
                item['boleta_exenta'], item['otras_boletas'], item['tickets'],
                item['monto_internet'], item['participacion'],
            ])

        detalle_ws = workbook.create_sheet('Detalle pedidos')
        detalle_ws.append([
            'Fecha venta', 'Origen', 'Plataforma', 'Ticket', 'Pedido RM', 'Pedido canal',
            'DTE/Folio', 'Tipo boleta', 'Empresa', 'Sucursal', 'Cliente', 'RUT/Doc', 'Vendedor',
            'Codigo vendedor', 'Operador', 'Fecha recepcion', 'Fecha facturacion',
            'Voucher', 'Unidades', 'Monto internet', 'Total ticket', 'Mixto', 'Fallback',
        ])
        for pedido in data['detalle']:
            detalle_ws.append([
                pedido['fecha_venta'], pedido['origen'], pedido['plataforma'], pedido['correlativo'],
                pedido['numero_ticket_rm'], pedido['numero_pedido_canal'], pedido['dte'],
                pedido['tipo_boleta'], pedido['empresa'], pedido['sucursal'], pedido['cliente'],
                pedido['documento_cliente'], pedido['vendedor'], pedido['codigo_vendedor'], pedido['operador'],
                pedido['fecha_recepcion'], pedido['fecha_facturacion'], pedido['voucher'],
                pedido['unidades'], pedido['monto_internet'], pedido['total_ticket'],
                'Si' if pedido['es_mixto'] else 'No',
                'Si' if pedido['usa_fallback'] else 'No',
            ])

        alertas_ws = workbook.create_sheet('Alertas')
        alertas_ws.append(['Alerta', 'Cantidad', 'Monto', 'Tickets muestra'])
        alertas_ws.append([
            'Ecommerce sin pago VENTA_INTERNET',
            data['alertas']['ecommerce_sin_pago_internet']['count'],
            data['alertas']['ecommerce_sin_pago_internet']['monto'],
            ', '.join(map(str, data['alertas']['ecommerce_sin_pago_internet']['tickets'])),
        ])
        alertas_ws.append([
            'Tickets mixtos',
            data['alertas']['tickets_mixtos']['count'],
            data['alertas']['tickets_mixtos']['monto_internet'],
            ', '.join(map(str, data['alertas']['tickets_mixtos']['tickets'])),
        ])
        alertas_ws.append([
            'Sin plataforma',
            data['alertas']['sin_plataforma']['count'],
            data['alertas']['sin_plataforma']['monto'],
            ', '.join(map(str, data['alertas']['sin_plataforma']['tickets'])),
        ])

        for sheet in workbook.worksheets:
            header_row = 3 if sheet.title == 'Resumen' else 1
            for cell in sheet[header_row]:
                cell.fill = encabezado
                cell.font = fuente_encabezado
                cell.alignment = Alignment(horizontal='center')
            sheet.freeze_panes = f'A{header_row + 1}'
            for column_cells in sheet.columns:
                width = min(max(len(str(cell.value or '')) for cell in column_cells) + 2, 45)
                sheet.column_dimensions[column_cells[0].column_letter].width = width

        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = (
            f"attachment; filename=ventas_internet_{data['periodo']['inicio']}_{data['periodo']['fin']}.xlsx"
        )
        workbook.save(response)
        return response
    except ValidationError as exc:
        mensaje = exc.messages[0] if exc.messages else str(exc)
        return JsonResponse({'success': False, 'error': mensaje}, status=400)


# ========== REPORTE VENTAS GLOBALES POR EMPRESA ==========

@login_required
def ver_reporte_ventas_global(request):
    """Vista del reporte de ventas globales agrupadas por empresa"""
    context = obtener_contexto_sucursales(request.user, request)
    return render(request, 'vistas/modulo_reportes/reporte_ventas_global.html', context)


@require_GET
@login_required
def obtener_ventas_global_por_empresa(request):
    """API: ventas globales de todas las sucursales agrupadas por empresa con comparativo"""
    try:
        mes = request.GET.get('mes')
        fecha_inicio_param = request.GET.get('fecha_inicio')
        fecha_fin_param = request.GET.get('fecha_fin')

        if fecha_inicio_param and fecha_fin_param:
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d')
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d')
        else:
            if not mes:
                mes = timezone.now().strftime('%Y-%m')
            fecha_inicio = datetime.strptime(mes, '%Y-%m').replace(day=1)
            if fecha_inicio.month == 12:
                fecha_fin = fecha_inicio.replace(year=fecha_inicio.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                fecha_fin = fecha_inicio.replace(month=fecha_inicio.month + 1, day=1) - timedelta(days=1)

        # Periodo anterior (mismo largo) para comparativo
        delta_dias = (fecha_fin - fecha_inicio).days + 1
        fecha_inicio_ant = fecha_inicio - timedelta(days=delta_dias)
        fecha_fin_ant = fecha_inicio - timedelta(days=1)

        def _sumar_periodo(fi, ff):
            """Suma ventas de Tickets + DTEs por sucursal para un rango"""
            fi_date = fi.date() if hasattr(fi, 'date') else fi
            ff_date = ff.date() if hasattr(ff, 'date') else ff

            data = {}  # {sucursal_id: {ventas, descuentos, documentos, devoluciones}}

            # Tickets
            for r in Ticket.objects.filter(
                created_at__date__gte=fi_date, created_at__date__lte=ff_date,
                estado='PAGADO',
                modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
            ).values('sucursal_id', 'sucursal__alias', 'sucursal__empresa_id', 'sucursal__empresa__nombre').annotate(
                total=Sum('total'), dcto=Sum('descuento'), docs=Count('id'),
            ):
                sid = r['sucursal_id']
                if not sid:
                    continue
                if sid not in data:
                    data[sid] = {
                        'alias': r['sucursal__alias'],
                        'empresa_id': r['sucursal__empresa_id'],
                        'empresa_nombre': r['sucursal__empresa__nombre'],
                        'ventas': 0, 'descuentos': 0, 'documentos': 0, 'devoluciones': 0,
                    }
                data[sid]['ventas'] += int(r['total'] or 0)
                data[sid]['descuentos'] += int(r['dcto'] or 0)
                data[sid]['documentos'] += int(r['docs'] or 0)

            # DTEs ventas
            for r in Dte.objects.filter(
                fecha_emision__gte=fi_date, fecha_emision__lte=ff_date,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            ).exclude(
                estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
            ).exclude(
                tipo_documento='NOTA DE CREDITO'
            ).values('sucursal_id', 'sucursal__alias', 'sucursal__empresa_id', 'sucursal__empresa__nombre').annotate(
                total=Sum('monto_con_iva'), dcto=Sum('descuento'), docs=Count('id'),
            ):
                sid = r['sucursal_id']
                if not sid:
                    continue
                if sid not in data:
                    data[sid] = {
                        'alias': r['sucursal__alias'],
                        'empresa_id': r['sucursal__empresa_id'],
                        'empresa_nombre': r['sucursal__empresa__nombre'],
                        'ventas': 0, 'descuentos': 0, 'documentos': 0, 'devoluciones': 0,
                    }
                data[sid]['ventas'] += int(r['total'] or 0)
                data[sid]['descuentos'] += int(r['dcto'] or 0)
                data[sid]['documentos'] += int(r['docs'] or 0)

            # DTEs notas de credito
            for r in Dte.objects.filter(
                fecha_emision__gte=fi_date, fecha_emision__lte=ff_date,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                tipo_documento='NOTA DE CREDITO',
            ).exclude(
                estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
            ).values('sucursal_id').annotate(total=Sum('monto_con_iva')):
                sid = r['sucursal_id']
                if sid and sid in data:
                    data[sid]['devoluciones'] += int(r['total'] or 0)

            return data

        actual = _sumar_periodo(fecha_inicio, fecha_fin)
        anterior = _sumar_periodo(fecha_inicio_ant, fecha_fin_ant)

        # Agrupar por empresa
        empresas = {}  # {empresa_id: {nombre, sucursales: [...], totales}}
        for sid, d in actual.items():
            eid = d['empresa_id']
            if not eid:
                continue
            if eid not in empresas:
                empresas[eid] = {
                    'nombre': d['empresa_nombre'],
                    'sucursales': [],
                    'ventas': 0, 'descuentos': 0, 'documentos': 0, 'devoluciones': 0,
                }
            netas = d['ventas'] - d['devoluciones']
            empresas[eid]['ventas'] += netas
            empresas[eid]['descuentos'] += d['descuentos']
            empresas[eid]['documentos'] += d['documentos']
            empresas[eid]['devoluciones'] += d['devoluciones']

            # Comparativo sucursal
            ant = anterior.get(sid, {})
            ventas_ant = (ant.get('ventas', 0) - ant.get('devoluciones', 0))
            variacion = ((netas - ventas_ant) / ventas_ant * 100) if ventas_ant > 0 else (100 if netas > 0 else 0)

            empresas[eid]['sucursales'].append({
                'id': sid,
                'nombre': d['alias'],
                'ventas': netas,
                'ventas_brutas': d['ventas'],
                'descuentos': d['descuentos'],
                'documentos': d['documentos'],
                'devoluciones': d['devoluciones'],
                'ticket_promedio': netas // d['documentos'] if d['documentos'] > 0 else 0,
                'ventas_anterior': ventas_ant,
                'variacion': round(variacion, 1),
            })

        # Totales globales y variaciones por empresa
        total_global = sum(e['ventas'] for e in empresas.values())

        # Anterior por empresa para comparativo
        empresas_ant = {}
        for sid, d in anterior.items():
            eid = d.get('empresa_id')
            if eid:
                if eid not in empresas_ant:
                    empresas_ant[eid] = 0
                empresas_ant[eid] += d['ventas'] - d.get('devoluciones', 0)

        resultado = []
        for eid, e in sorted(empresas.items(), key=lambda x: x[1]['ventas'], reverse=True):
            ventas_ant_emp = empresas_ant.get(eid, 0)
            variacion_emp = ((e['ventas'] - ventas_ant_emp) / ventas_ant_emp * 100) if ventas_ant_emp > 0 else (100 if e['ventas'] > 0 else 0)
            participacion = (e['ventas'] / total_global * 100) if total_global > 0 else 0

            # Ordenar sucursales por ventas
            e['sucursales'].sort(key=lambda s: s['ventas'], reverse=True)

            resultado.append({
                'id': eid,
                'nombre': e['nombre'],
                'ventas': e['ventas'],
                'descuentos': e['descuentos'],
                'documentos': e['documentos'],
                'devoluciones': e['devoluciones'],
                'ticket_promedio': e['ventas'] // e['documentos'] if e['documentos'] > 0 else 0,
                'participacion': round(participacion, 1),
                'ventas_anterior': ventas_ant_emp,
                'variacion': round(variacion_emp, 1),
                'sucursales': e['sucursales'],
            })

        return JsonResponse({
            'success': True,
            'empresas': resultado,
            'kpis': {
                'total_ventas': total_global,
                'total_descuentos': sum(e['descuentos'] for e in empresas.values()),
                'total_documentos': sum(e['documentos'] for e in empresas.values()),
                'total_devoluciones': sum(e['devoluciones'] for e in empresas.values()),
                'total_empresas': len(resultado),
                'total_sucursales': sum(len(e['sucursales']) for e in empresas.values()),
            },
            'periodo': {
                'inicio': fecha_inicio.strftime('%Y-%m-%d'),
                'fin': fecha_fin.strftime('%Y-%m-%d'),
                'inicio_anterior': fecha_inicio_ant.strftime('%Y-%m-%d'),
                'fin_anterior': fecha_fin_ant.strftime('%Y-%m-%d'),
            }
        })

    except Exception as e:
        logger.exception("Error al obtener reporte de ventas sucursal")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== REPORTE COMPARATIVO DE VENTAS ==========

def _calcular_rangos_comparativo(tipo_flujo, fecha_inicio_param=None, fecha_fin_param=None):
    """
    Calcula el rango (fi_act, ff_act) del periodo actual y (fi_ant, ff_ant) del
    periodo anterior equivalente, según el tipo de flujo temporal.

    Devuelve 4 objetos datetime.date.
    Flujos soportados:
      - 'hoy'           : hoy vs ayer
      - 'semana'        : lunes-hoy vs lunes-domingo semana anterior
      - 'mes_mtd'       : mes-a-la-fecha vs mismo rango mes anterior
      - 'mes_full'      : mes actual completo vs mes anterior completo
      - 'ultimos_7'     : últimos 7 días vs 7 días previos
      - 'ultimos_30'    : últimos 30 días vs 30 días previos
      - 'yoy'           : mes actual vs mismo mes año anterior (elimina estacionalidad)
      - 'trimestre_yoy' : trimestre actual vs mismo trimestre año anterior
      - 'anio_ytd'      : año a la fecha (ene-hoy) vs mismo rango año anterior
      - 'anio_full'     : año calendario anterior completo vs año antepasado completo
      - 'custom'        : usa fecha_inicio_param/fecha_fin_param y desplaza el mismo largo
    """
    hoy = timezone.localdate()

    def _restar_meses(d, meses):
        y = d.year
        m = d.month - meses
        while m <= 0:
            m += 12
            y -= 1
        from calendar import monthrange
        dia = min(d.day, monthrange(y, m)[1])
        return d.replace(year=y, month=m, day=dia)

    if tipo_flujo == 'hoy':
        fi_act = ff_act = hoy
        fi_ant = ff_ant = hoy - timedelta(days=1)

    elif tipo_flujo == 'semana':
        dia_semana = hoy.weekday()
        fi_act = hoy - timedelta(days=dia_semana)
        ff_act = hoy
        fi_ant = fi_act - timedelta(days=7)
        ff_ant = fi_ant + timedelta(days=dia_semana)

    elif tipo_flujo == 'mes_mtd':
        fi_act = hoy.replace(day=1)
        ff_act = hoy
        fi_ant = _restar_meses(fi_act, 1)
        dia_actual = hoy.day
        from calendar import monthrange
        ultimo_dia_mes_ant = monthrange(fi_ant.year, fi_ant.month)[1]
        ff_ant = fi_ant.replace(day=min(dia_actual, ultimo_dia_mes_ant))

    elif tipo_flujo == 'mes_full':
        fi_act = hoy.replace(day=1)
        from calendar import monthrange
        ultimo_dia = monthrange(hoy.year, hoy.month)[1]
        ff_act = hoy.replace(day=ultimo_dia)
        fi_ant = _restar_meses(fi_act, 1)
        ultimo_dia_ant = monthrange(fi_ant.year, fi_ant.month)[1]
        ff_ant = fi_ant.replace(day=ultimo_dia_ant)

    elif tipo_flujo == 'ultimos_7':
        ff_act = hoy
        fi_act = hoy - timedelta(days=6)
        ff_ant = fi_act - timedelta(days=1)
        fi_ant = ff_ant - timedelta(days=6)

    elif tipo_flujo == 'ultimos_30':
        ff_act = hoy
        fi_act = hoy - timedelta(days=29)
        ff_ant = fi_act - timedelta(days=1)
        fi_ant = ff_ant - timedelta(days=29)

    elif tipo_flujo == 'yoy':
        fi_act = hoy.replace(day=1)
        from calendar import monthrange
        ultimo_dia = monthrange(hoy.year, hoy.month)[1]
        ff_act = hoy.replace(day=ultimo_dia)
        try:
            fi_ant = fi_act.replace(year=fi_act.year - 1)
        except ValueError:
            fi_ant = fi_act.replace(year=fi_act.year - 1, day=28)
        ultimo_dia_ant = monthrange(fi_ant.year, fi_ant.month)[1]
        ff_ant = fi_ant.replace(day=ultimo_dia_ant)

    elif tipo_flujo == 'trimestre_yoy':
        from calendar import monthrange
        trimestre = (hoy.month - 1) // 3 + 1
        mes_inicio_q = (trimestre - 1) * 3 + 1
        mes_fin_q = mes_inicio_q + 2
        fi_act = hoy.replace(month=mes_inicio_q, day=1)
        ff_act = hoy.replace(
            month=mes_fin_q,
            day=monthrange(hoy.year, mes_fin_q)[1],
        )
        fi_ant = fi_act.replace(year=fi_act.year - 1)
        ff_ant = fi_ant.replace(
            day=monthrange(fi_ant.year, fi_ant.month)[1],
        )
        # Ajustar día final por si cambia último día del mes entre años
        ff_ant = ff_ant.replace(
            month=mes_fin_q,
            day=monthrange(ff_ant.year, mes_fin_q)[1],
        )

    elif tipo_flujo == 'anio_ytd':
        fi_act = hoy.replace(month=1, day=1)
        ff_act = hoy
        fi_ant = fi_act.replace(year=fi_act.year - 1)
        try:
            ff_ant = hoy.replace(year=hoy.year - 1)
        except ValueError:
            ff_ant = hoy.replace(year=hoy.year - 1, day=28)

    elif tipo_flujo == 'anio_full':
        from calendar import monthrange
        anio_act = hoy.year - 1
        anio_ant = hoy.year - 2
        fi_act = hoy.replace(year=anio_act, month=1, day=1)
        ff_act = hoy.replace(
            year=anio_act, month=12,
            day=monthrange(anio_act, 12)[1],
        )
        fi_ant = hoy.replace(year=anio_ant, month=1, day=1)
        ff_ant = hoy.replace(
            year=anio_ant, month=12,
            day=monthrange(anio_ant, 12)[1],
        )

    else:  # 'custom' o default
        if fecha_inicio_param and fecha_fin_param:
            fi_act = datetime.strptime(fecha_inicio_param, '%Y-%m-%d').date()
            ff_act = datetime.strptime(fecha_fin_param, '%Y-%m-%d').date()
        else:
            fi_act = hoy.replace(day=1)
            ff_act = hoy
        delta_dias = (ff_act - fi_act).days + 1
        ff_ant = fi_act - timedelta(days=1)
        fi_ant = ff_ant - timedelta(days=delta_dias - 1)

    return fi_act, ff_act, fi_ant, ff_ant


def _sumar_ventas_comparativo(fi, ff, user, request):
    """
    Suma ventas de Tickets + DTEs para el rango (fi, ff), con breakdown por
    sucursal, vendedor y canal, **evitando el doble contado** entre Ticket y Dte.

    Regla anti-doble contado (clave):
      - Un Ticket con `dte_generado=True` ya está representado en la tabla Dte
        como su boleta/factura electrónica. Para NO contarlo dos veces:
          * Total ventas brutas = sum(DTEs) + sum(Tickets con dte_generado=False)
      - ECOMMERCE se calcula INDEPENDIENTE desde Ticket.modulo_origen='ECOMMERCE'
        (da igual si tiene DTE o no; el monto es el del ticket).
      - "POS presencial" (canal 'POS') = Tickets POS/VENTA_PUBLICO sin DTE.
      - Canal 'DTE' = DTEs MENOS los tickets ECOMMERCE que ya generaron DTE
        (para no duplicar ventas de internet dentro de 'presencial').

    Devuelve dict:
    {
      'total_ventas_netas', 'total_ventas_brutas', 'total_documentos',
      'total_descuentos', 'total_devoluciones', 'cantidad_devoluciones',
      'total_unidades',
      'canal': {
          'ECOMMERCE':  {'ventas', 'documentos', 'unidades'},
          'POS':        {'ventas', 'documentos', 'unidades'},
          'DTE':        {'ventas', 'documentos', 'unidades'},
      },
      'sucursales': { sid: {...} },
      'vendedores': { vid: {...} },
    }
    """
    resultado = {
        'total_ventas_netas': 0,
        'total_ventas_brutas': 0,
        'total_documentos': 0,
        'total_descuentos': 0,
        'total_devoluciones': 0,
        'cantidad_devoluciones': 0,
        'total_unidades': 0,
        'canal': {
            'ECOMMERCE': {'ventas': 0, 'documentos': 0, 'unidades': 0},
            'POS':       {'ventas': 0, 'documentos': 0, 'unidades': 0},
            'DTE':       {'ventas': 0, 'documentos': 0, 'unidades': 0},
        },
        'sucursales': {},
        'vendedores': {},
    }

    def _suc_row(sid, nombre):
        return resultado['sucursales'].setdefault(sid, {
            'nombre': nombre or '-',
            'ventas_brutas': 0, 'devoluciones': 0, 'documentos': 0,
            'descuentos': 0, 'unidades': 0,
            'ventas_ecommerce': 0, 'ventas_presencial': 0,
        })

    def _vend_row(vid, nombre, codigo, sucursal):
        return resultado['vendedores'].setdefault(vid, {
            'nombre': nombre or '-',
            'codigo': codigo or '',
            'sucursal': sucursal or '-',
            'ventas_brutas': 0, 'devoluciones': 0, 'documentos': 0,
        })

    # ========== TICKETS (base común) ==========
    qs_tickets = Ticket.objects.filter(
        created_at__date__gte=fi,
        created_at__date__lte=ff,
        estado='PAGADO',
        modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
    ).select_related('sucursal', 'vendedor')
    qs_tickets = filtrar_queryset_por_sucursal(qs_tickets, user, request)

    # --- Canal ECOMMERCE: TODOS los tickets ECOMMERCE (aunque tengan DTE) ---
    # El monto real de la venta está siempre en el Ticket. Se suma aquí y
    # luego se RESTA del canal DTE para no contarlo dos veces.
    qs_ecom = qs_tickets.filter(modulo_origen='ECOMMERCE')
    for r in qs_ecom.values('sucursal_id', 'sucursal__alias').annotate(
        total=Sum('total'), dcto=Sum('descuento'), docs=Count('id'),
    ):
        sid = r['sucursal_id']
        total = int(r['total'] or 0)
        docs = int(r['docs'] or 0)
        dcto = int(r['dcto'] or 0)
        resultado['canal']['ECOMMERCE']['ventas'] += total
        resultado['canal']['ECOMMERCE']['documentos'] += docs
        # OJO: los tickets ECOMMERCE con dte_generado=True YA están en DTEs.
        # NO sumamos al total_ventas_brutas aquí para esos. Solo sumamos los
        # ECOMMERCE SIN DTE (caso raro, pero existe).
        if sid:
            s = _suc_row(sid, r['sucursal__alias'])
            s['ventas_ecommerce'] += total

    # Los tickets ECOMMERCE sin DTE sí se suman al total bruto (no están en DTE)
    for r in qs_ecom.filter(dte_generado=False).values(
        'sucursal_id', 'sucursal__alias'
    ).annotate(total=Sum('total'), dcto=Sum('descuento'), docs=Count('id')):
        sid = r['sucursal_id']
        total = int(r['total'] or 0)
        docs = int(r['docs'] or 0)
        dcto = int(r['dcto'] or 0)
        resultado['total_ventas_brutas'] += total
        resultado['total_documentos'] += docs
        resultado['total_descuentos'] += dcto
        if sid:
            s = _suc_row(sid, r['sucursal__alias'])
            s['ventas_brutas'] += total
            s['documentos'] += docs
            s['descuentos'] += dcto

    # --- Canal POS presencial: tickets POS/VENTA_PUBLICO sin DTE ---
    qs_pos_solo = qs_tickets.filter(
        modulo_origen__in=['POS', 'VENTA_PUBLICO'],
        dte_generado=False,
    )
    for r in qs_pos_solo.values('sucursal_id', 'sucursal__alias').annotate(
        total=Sum('total'), dcto=Sum('descuento'), docs=Count('id'),
    ):
        sid = r['sucursal_id']
        total = int(r['total'] or 0)
        docs = int(r['docs'] or 0)
        dcto = int(r['dcto'] or 0)
        resultado['canal']['POS']['ventas'] += total
        resultado['canal']['POS']['documentos'] += docs
        resultado['total_ventas_brutas'] += total
        resultado['total_documentos'] += docs
        resultado['total_descuentos'] += dcto
        if sid:
            s = _suc_row(sid, r['sucursal__alias'])
            s['ventas_brutas'] += total
            s['documentos'] += docs
            s['descuentos'] += dcto
            s['ventas_presencial'] += total

    # --- Unidades de tickets ECOMMERCE + POS-sin-DTE ---
    tickets_unidades = Ticket_Productos.objects.filter(
        idTicket__in=qs_ecom
    ).values(
        'idTicket__sucursal_id'
    ).annotate(u=Sum('stock'))
    for r in tickets_unidades:
        u = int(r['u'] or 0)
        resultado['canal']['ECOMMERCE']['unidades'] += u
        sid = r['idTicket__sucursal_id']
        # No sumamos al total_unidades aquí; se suma desde DTEs + tickets sin DTE
        if sid and sid in resultado['sucursales']:
            # sólo contar para la sucursal si el ticket NO tiene DTE (evitar doble)
            pass

    # Unidades solo de tickets sin DTE (se suman al total)
    unid_tick_sin_dte = Ticket_Productos.objects.filter(
        idTicket__in=qs_tickets.filter(dte_generado=False)
    ).values(
        'idTicket__sucursal_id', 'idTicket__modulo_origen'
    ).annotate(u=Sum('stock'))
    for r in unid_tick_sin_dte:
        u = int(r['u'] or 0)
        resultado['total_unidades'] += u
        sid = r['idTicket__sucursal_id']
        canal = 'ECOMMERCE' if r['idTicket__modulo_origen'] == 'ECOMMERCE' else 'POS'
        # ECOMMERCE ya sumó unidades arriba; solo sumamos POS sin DTE al canal POS
        if canal == 'POS':
            resultado['canal']['POS']['unidades'] += u
        if sid and sid in resultado['sucursales']:
            resultado['sucursales'][sid]['unidades'] += u

    # --- Tickets por vendedor (solo los sin DTE, para no duplicar con DTE) ---
    for r in qs_tickets.filter(dte_generado=False).values(
        'vendedor_id', 'vendedor__nombre', 'vendedor__codigo_vendedor',
        'sucursal__alias',
    ).annotate(total=Sum('total'), docs=Count('id')):
        vid = r['vendedor_id']
        if not vid:
            continue
        v = _vend_row(vid, r['vendedor__nombre'],
                      r['vendedor__codigo_vendedor'], r['sucursal__alias'])
        v['ventas_brutas'] += int(r['total'] or 0)
        v['documentos'] += int(r['docs'] or 0)

    # Identificar ID de tickets ECOMMERCE con DTE (para restar luego del canal DTE)
    ecom_con_dte_por_suc = {}  # {sid: monto_a_restar}
    for r in qs_ecom.filter(dte_generado=True).values(
        'sucursal_id'
    ).annotate(total=Sum('total'), docs=Count('id')):
        sid = r['sucursal_id']
        ecom_con_dte_por_suc[sid] = {
            'total': int(r['total'] or 0),
            'docs': int(r['docs'] or 0),
        }

    # ========== DTEs ==========
    qs_dtes_base = Dte.objects.filter(
        fecha_emision__gte=fi,
        fecha_emision__lte=ff,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    ).exclude(
        estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
    ).exclude(
        receptor__isnull=False,
        receptor_id=F('emisor_id')
    ).select_related('sucursal', 'vendedor')
    qs_dtes_base = filtrar_queryset_por_sucursal(qs_dtes_base, user, request)

    # DTEs de venta (excluyendo NC)
    qs_dte_ventas = qs_dtes_base.exclude(tipo_documento='NOTA DE CREDITO')
    for r in qs_dte_ventas.values('sucursal_id', 'sucursal__alias').annotate(
        total=Sum('monto_con_iva'),
        dcto=Sum('descuento'),
        docs=Count('id'),
        unidades=Sum('unidades_productos'),
    ):
        sid = r['sucursal_id']
        total = int(r['total'] or 0)
        docs = int(r['docs'] or 0)
        dcto = int(r['dcto'] or 0)
        unidades = int(r['unidades'] or 0)

        # Restar los tickets ECOMMERCE que YA fueron contados arriba (viven
        # en la tabla Dte como boletas electrónicas)
        ecom_info = ecom_con_dte_por_suc.get(sid, {'total': 0, 'docs': 0})
        dte_neto = total - ecom_info['total']
        dte_docs_neto = max(docs - ecom_info['docs'], 0)

        resultado['canal']['DTE']['ventas'] += dte_neto
        resultado['canal']['DTE']['documentos'] += dte_docs_neto
        resultado['canal']['DTE']['unidades'] += unidades

        # Al total general sí sumamos TODOS los DTEs (incluyen a los ECOMMERCE
        # que se convirtieron en boleta). Por eso NO sumamos arriba los tickets
        # ECOMMERCE con DTE al total: ya están representados acá.
        resultado['total_ventas_brutas'] += total
        resultado['total_documentos'] += docs
        resultado['total_descuentos'] += dcto
        resultado['total_unidades'] += unidades

        if sid:
            s = _suc_row(sid, r['sucursal__alias'])
            s['ventas_brutas'] += total
            s['documentos'] += docs
            s['descuentos'] += dcto
            s['unidades'] += unidades
            # Presencial a nivel sucursal = DTEs totales - parte ECOMMERCE
            s['ventas_presencial'] += dte_neto

    # DTEs por vendedor
    for r in qs_dte_ventas.values(
        'vendedor_id', 'vendedor__nombre', 'vendedor__codigo_vendedor',
        'sucursal__alias'
    ).annotate(total=Sum('monto_con_iva'), docs=Count('id')):
        vid = r['vendedor_id']
        if not vid:
            continue
        v = _vend_row(vid, r['vendedor__nombre'],
                      r['vendedor__codigo_vendedor'], r['sucursal__alias'])
        v['ventas_brutas'] += int(r['total'] or 0)
        v['documentos'] += int(r['docs'] or 0)

    # NC (devoluciones) por sucursal
    qs_nc = qs_dtes_base.filter(tipo_documento='NOTA DE CREDITO')
    for r in qs_nc.values('sucursal_id').annotate(
        total=Sum('monto_con_iva'), cant=Count('id')
    ):
        sid = r['sucursal_id']
        total_nc = int(r['total'] or 0)
        cant_nc = int(r['cant'] or 0)
        resultado['total_devoluciones'] += total_nc
        resultado['cantidad_devoluciones'] += cant_nc
        if sid and sid in resultado['sucursales']:
            resultado['sucursales'][sid]['devoluciones'] += total_nc

    # NC por vendedor
    for r in qs_nc.values('vendedor_id').annotate(total=Sum('monto_con_iva')):
        vid = r['vendedor_id']
        if vid and vid in resultado['vendedores']:
            resultado['vendedores'][vid]['devoluciones'] += int(r['total'] or 0)

    resultado['total_ventas_netas'] = (
        resultado['total_ventas_brutas'] - resultado['total_devoluciones']
    )
    return resultado


@login_required
def ver_reporte_ventas_comparativo(request):
    """Vista principal del reporte comparativo de ventas (actual vs periodo anterior)."""
    context = obtener_contexto_sucursales(request.user, request)
    return render(request, 'vistas/modulo_reportes/reporte_ventas_comparativo.html', context)


@require_GET
@login_required
def obtener_ventas_comparativo(request):
    """API: datos comparativos de ventas entre periodo actual y periodo anterior equivalente.

    Parámetros:
      - tipo_flujo: hoy | semana | mes_mtd | mes_full | ultimos_7 | ultimos_30 | yoy | custom
      - fecha_inicio, fecha_fin: requeridos si tipo_flujo=custom
      - sucursal_id: opcional (filtra por una sucursal; respeta permisos)
    """
    try:
        tipo_flujo = request.GET.get('tipo_flujo', 'mes_full')
        fi_param = request.GET.get('fecha_inicio')
        ff_param = request.GET.get('fecha_fin')

        fi_act, ff_act, fi_ant, ff_ant = _calcular_rangos_comparativo(
            tipo_flujo, fi_param, ff_param
        )

        actual = _sumar_ventas_comparativo(fi_act, ff_act, request.user, request)
        anterior = _sumar_ventas_comparativo(fi_ant, ff_ant, request.user, request)

        def _var_pct(act, ant):
            if ant > 0:
                return round((act - ant) / ant * 100, 1)
            return 100.0 if act > 0 else 0.0

        def _pct(num, den):
            return round(num / den * 100, 1) if den > 0 else 0.0

        # ---------- KPIs globales ----------
        ventas_act = actual['total_ventas_netas']
        ventas_ant = anterior['total_ventas_netas']
        docs_act = actual['total_documentos']
        docs_ant = anterior['total_documentos']
        ticket_act = (ventas_act / docs_act) if docs_act > 0 else 0
        ticket_ant = (ventas_ant / docs_ant) if docs_ant > 0 else 0

        pct_internet_act = _pct(
            actual['canal']['ECOMMERCE']['ventas'], actual['total_ventas_brutas']
        )
        pct_internet_ant = _pct(
            anterior['canal']['ECOMMERCE']['ventas'], anterior['total_ventas_brutas']
        )

        tasa_dev_act = _pct(actual['total_devoluciones'], actual['total_ventas_brutas'])
        tasa_dev_ant = _pct(anterior['total_devoluciones'], anterior['total_ventas_brutas'])

        kpis = {
            'ventas_actual': ventas_act,
            'ventas_anterior': ventas_ant,
            'variacion_pct': _var_pct(ventas_act, ventas_ant),
            'variacion_abs': ventas_act - ventas_ant,

            'pct_internet_actual': pct_internet_act,
            'pct_internet_anterior': pct_internet_ant,
            'delta_internet_pp': round(pct_internet_act - pct_internet_ant, 1),
            'ventas_internet_actual': actual['canal']['ECOMMERCE']['ventas'],
            'ventas_internet_anterior': anterior['canal']['ECOMMERCE']['ventas'],

            'ticket_promedio_actual': int(ticket_act),
            'ticket_promedio_anterior': int(ticket_ant),
            'variacion_ticket_pct': _var_pct(ticket_act, ticket_ant),

            'documentos_actual': docs_act,
            'documentos_anterior': docs_ant,
            'variacion_documentos_pct': _var_pct(docs_act, docs_ant),

            'tasa_devolucion_actual': tasa_dev_act,
            'tasa_devolucion_anterior': tasa_dev_ant,
            'delta_tasa_devolucion_pp': round(tasa_dev_act - tasa_dev_ant, 1),

            'unidades_actual': actual['total_unidades'],
            'unidades_anterior': anterior['total_unidades'],
            'variacion_unidades_pct': _var_pct(actual['total_unidades'], anterior['total_unidades']),

            'descuentos_actual': actual['total_descuentos'],
            'descuentos_anterior': anterior['total_descuentos'],
        }

        # ---------- Por sucursal ----------
        sucursales_rows = []
        todas_sids = set(actual['sucursales'].keys()) | set(anterior['sucursales'].keys())
        for sid in todas_sids:
            a = actual['sucursales'].get(sid, {})
            b = anterior['sucursales'].get(sid, {})
            nombre = a.get('nombre') or b.get('nombre') or '-'
            v_brutas_a = a.get('ventas_brutas', 0)
            v_brutas_b = b.get('ventas_brutas', 0)
            dev_a = a.get('devoluciones', 0)
            dev_b = b.get('devoluciones', 0)
            netas_a = v_brutas_a - dev_a
            netas_b = v_brutas_b - dev_b
            docs_a = a.get('documentos', 0)
            docs_b = b.get('documentos', 0)
            tp_a = int(netas_a / docs_a) if docs_a > 0 else 0
            ecom_a = a.get('ventas_ecommerce', 0)
            ecom_b = b.get('ventas_ecommerce', 0)
            pct_int_a = _pct(ecom_a, v_brutas_a)
            pct_int_b = _pct(ecom_b, v_brutas_b)

            sucursales_rows.append({
                'id': sid,
                'nombre': nombre,
                'ventas_actual': netas_a,
                'ventas_anterior': netas_b,
                'variacion_pct': _var_pct(netas_a, netas_b),
                'variacion_abs': netas_a - netas_b,
                'pct_internet_actual': pct_int_a,
                'pct_internet_anterior': pct_int_b,
                'ventas_internet_actual': ecom_a,
                'documentos_actual': docs_a,
                'documentos_anterior': docs_b,
                'ticket_promedio_actual': tp_a,
                'devoluciones_actual': dev_a,
                'unidades_actual': a.get('unidades', 0),
                'participacion': _pct(netas_a, ventas_act),
            })
        sucursales_rows.sort(key=lambda x: x['ventas_actual'], reverse=True)

        # ---------- Por vendedor ----------
        vendedores_rows = []
        todas_vids = set(actual['vendedores'].keys()) | set(anterior['vendedores'].keys())
        for vid in todas_vids:
            a = actual['vendedores'].get(vid, {})
            b = anterior['vendedores'].get(vid, {})
            nombre = a.get('nombre') or b.get('nombre') or '-'
            codigo = a.get('codigo') or b.get('codigo') or ''
            sucursal = a.get('sucursal') or b.get('sucursal') or '-'
            v_brutas_a = a.get('ventas_brutas', 0)
            v_brutas_b = b.get('ventas_brutas', 0)
            dev_a = a.get('devoluciones', 0)
            dev_b = b.get('devoluciones', 0)
            netas_a = v_brutas_a - dev_a
            netas_b = v_brutas_b - dev_b
            docs_a = a.get('documentos', 0)
            docs_b = b.get('documentos', 0)
            tp_a = int(netas_a / docs_a) if docs_a > 0 else 0

            vendedores_rows.append({
                'id': vid,
                'nombre': nombre,
                'codigo': codigo,
                'sucursal': sucursal,
                'ventas_actual': netas_a,
                'ventas_anterior': netas_b,
                'variacion_pct': _var_pct(netas_a, netas_b),
                'variacion_abs': netas_a - netas_b,
                'documentos_actual': docs_a,
                'documentos_anterior': docs_b,
                'ticket_promedio_actual': tp_a,
                'participacion': _pct(netas_a, ventas_act),
            })
        vendedores_rows.sort(key=lambda x: x['ventas_actual'], reverse=True)

        # ---------- Por canal ----------
        canales_rows = []
        etiquetas_canal = {
            'ECOMMERCE': 'Internet (E-commerce)',
            'POS': 'Presencial (POS/Tickets)',
            'DTE': 'Documentos Tributarios (DTE)',
        }
        for key in ['ECOMMERCE', 'POS', 'DTE']:
            a = actual['canal'][key]
            b = anterior['canal'][key]
            canales_rows.append({
                'canal': key,
                'nombre': etiquetas_canal[key],
                'ventas_actual': a['ventas'],
                'ventas_anterior': b['ventas'],
                'variacion_pct': _var_pct(a['ventas'], b['ventas']),
                'documentos_actual': a['documentos'],
                'documentos_anterior': b['documentos'],
                'unidades_actual': a['unidades'],
                'unidades_anterior': b['unidades'],
                'participacion_actual': _pct(a['ventas'], actual['total_ventas_brutas']),
                'participacion_anterior': _pct(b['ventas'], anterior['total_ventas_brutas']),
            })

        # ---------- Top crecimiento ----------
        top_suc = max(sucursales_rows, key=lambda x: x['variacion_pct'], default=None)
        top_vend = max(vendedores_rows, key=lambda x: x['variacion_pct'], default=None)
        kpis['top_sucursal_crecimiento'] = (
            {'nombre': top_suc['nombre'], 'variacion': top_suc['variacion_pct']}
            if top_suc and top_suc['ventas_actual'] > 0 else None
        )
        kpis['top_vendedor_crecimiento'] = (
            {'nombre': top_vend['nombre'], 'variacion': top_vend['variacion_pct']}
            if top_vend and top_vend['ventas_actual'] > 0 else None
        )

        return JsonResponse({
            'success': True,
            'tipo_flujo': tipo_flujo,
            'periodo': {
                'actual': {
                    'inicio': fi_act.strftime('%Y-%m-%d'),
                    'fin': ff_act.strftime('%Y-%m-%d'),
                },
                'anterior': {
                    'inicio': fi_ant.strftime('%Y-%m-%d'),
                    'fin': ff_ant.strftime('%Y-%m-%d'),
                },
            },
            'kpis': kpis,
            'sucursales': sucursales_rows,
            'vendedores': vendedores_rows,
            'canales': canales_rows,
        })

    except Exception as e:
        logger.exception("Error al obtener comparativo de ventas")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== REPORTE DE PRODUCTOS VENDIDOS ==========

def _aplicar_filtros_producto(qs, prefix, filtros):
    """Aplica filtros de atributo/categoría/temporada a un queryset de líneas de venta.
    'prefix' es el prefijo relacional al Producto, por ejemplo:
      - 'ProductoTalla__producto' para Ticket_Productos
      - 'productoTalla__producto' para Dte_Productos

    Por defecto excluye productos con Producto.excluir_de_analitica=True
    (productos marcados desde Gestión de Productos como no-analíticos:
    exhibición, consignación, catálogo retirado, etc.).
    Pasar filtros['incluir_excluidos']=True para incluirlos (auditoría).
    """
    # Exclusión por defecto de productos marcados como no-analíticos
    if not filtros.get('incluir_excluidos'):
        qs = qs.filter(**{f'{prefix}__excluir_de_analitica': False})

    if filtros.get('marca_id'):
        qs = qs.filter(**{f'{prefix}__atributo1_id': filtros['marca_id']})
    if filtros.get('color_id'):
        qs = qs.filter(**{f'{prefix}__atributo2_id': filtros['color_id']})
    if filtros.get('sexo_id'):
        qs = qs.filter(**{f'{prefix}__atributo3_id': filtros['sexo_id']})
    if filtros.get('genero_id'):
        qs = qs.filter(**{f'{prefix}__atributo4_id': filtros['genero_id']})
    if filtros.get('categoria_id'):
        qs = qs.filter(**{f'{prefix}__categoria_id': filtros['categoria_id']})
    if filtros.get('temporada'):
        qs = qs.filter(**{f'{prefix}__temporada': filtros['temporada']})
    if filtros.get('anio_temporada'):
        qs = qs.filter(**{f'{prefix}__anio_temporada': filtros['anio_temporada']})
    if filtros.get('rango_precio'):
        qs = qs.filter(**{f'{prefix}__rango_precio': filtros['rango_precio']})
    if filtros.get('busqueda'):
        q = filtros['busqueda']
        qs = qs.filter(
            Q(**{f'{prefix}__articulo__icontains': q}) |
            Q(**{f'{prefix}__descripcion__icontains': q})
        )
    return qs


def _agregar_productos_vendidos(fi, ff, filtros, user, request):
    """Agrega ventas a nivel producto desde Ticket_Productos + Dte_Productos
    aplicando la regla anti-doble-contado:
      - Tickets: solo los que NO generaron DTE (`dte_generado=False`).
      - DTEs: todos los de venta no-anulados/no-NC (incluyen boletas de tickets).

    Métricas calculadas por producto:
      - unidades, monto, costo, margen, margen_pct
      - docs (cantidad de ventas), sucursales_count

    Devuelve dict con:
      {
        'productos': [{producto_id, articulo, descripcion, marca, categoria,
                       sexo, genero, unidades, monto, costo, margen, margen_pct, docs}],
        'por_marca': [...], 'por_categoria': [...], 'por_sexo': [...],
        'por_genero': [...], 'por_sucursal_categoria': [{sucursal, categoria, unidades, monto}],
        'kpis': {...}
      }
    """
    # ---------- TICKET_PRODUCTOS (solo tickets sin DTE) ----------
    qs_tp = Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=fi,
        idTicket__created_at__date__lte=ff,
        idTicket__estado='PAGADO',
        idTicket__modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        idTicket__dte_generado=False,
        ProductoTalla__isnull=False,
    )
    # Permisos / sucursal (aplica al ticket padre)
    qs_tp = filtrar_queryset_por_sucursal(
        qs_tp, user, request, campo_sucursal='idTicket__sucursal_id'
    )
    qs_tp = _aplicar_filtros_producto(qs_tp, 'ProductoTalla__producto', filtros)

    # ---------- DTE_PRODUCTOS ----------
    qs_dp = Dte_Productos.objects.filter(
        dte__fecha_emision__gte=fi,
        dte__fecha_emision__lte=ff,
        dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        productoTalla__isnull=False,
    ).exclude(
        dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
    ).exclude(
        dte__tipo_documento='NOTA DE CREDITO'
    ).exclude(
        dte__receptor__isnull=False,
        dte__receptor_id=F('dte__emisor_id'),
    )
    qs_dp = filtrar_queryset_por_sucursal(
        qs_dp, user, request, campo_sucursal='dte__sucursal_id'
    )
    qs_dp = _aplicar_filtros_producto(qs_dp, 'productoTalla__producto', filtros)

    # ---------- Agregación por producto ----------
    productos_acum = {}  # {producto_id: dict}
    sucursales_por_producto = {}  # {producto_id: set(sucursal_id)}

    def _get_prod(pid, data_row):
        return productos_acum.setdefault(pid, {
            'producto_id': pid,
            'articulo': data_row.get('prod_articulo') or '-',
            'descripcion': data_row.get('prod_descripcion') or '',
            'marca': data_row.get('prod_marca') or '-',
            'marca_id': data_row.get('prod_marca_id'),
            'categoria': data_row.get('prod_categoria') or '-',
            'categoria_id': data_row.get('prod_categoria_id'),
            'sexo': data_row.get('prod_sexo') or '-',
            'sexo_id': data_row.get('prod_sexo_id'),
            'genero': data_row.get('prod_genero') or '-',
            'genero_id': data_row.get('prod_genero_id'),
            'unidades': 0, 'monto': 0, 'costo': 0, 'docs': 0,
        })

    # --- Tickets ---
    agg_tp = qs_tp.values(
        'ProductoTalla__producto_id',
        prod_articulo=F('ProductoTalla__producto__articulo'),
        prod_descripcion=F('ProductoTalla__producto__descripcion'),
        prod_marca=F('ProductoTalla__producto__atributo1__valor'),
        prod_marca_id=F('ProductoTalla__producto__atributo1_id'),
        prod_categoria=F('ProductoTalla__producto__categoria__nombre'),
        prod_categoria_id=F('ProductoTalla__producto__categoria_id'),
        prod_sexo=F('ProductoTalla__producto__atributo3__valor'),
        prod_sexo_id=F('ProductoTalla__producto__atributo3_id'),
        prod_genero=F('ProductoTalla__producto__atributo4__valor'),
        prod_genero_id=F('ProductoTalla__producto__atributo4_id'),
    ).annotate(
        unid=Sum('stock'),
        monto=Sum('subtotal'),
        # costo FIFO total = costo_fifo * cantidad
        costo_total=Sum(ExpressionWrapper(
            F('costo_fifo') * F('stock'), output_field=DecimalField()
        )),
        docs=Count('idTicket', distinct=True),
    )
    for r in agg_tp:
        pid = r['ProductoTalla__producto_id']
        if not pid:
            continue
        p = _get_prod(pid, r)
        p['unidades'] += int(r['unid'] or 0)
        p['monto'] += int(r['monto'] or 0)
        p['costo'] += int(r['costo_total'] or 0)
        p['docs'] += int(r['docs'] or 0)

    # Sucursales por producto (Tickets)
    for r in qs_tp.values('ProductoTalla__producto_id', 'idTicket__sucursal_id').distinct():
        pid = r['ProductoTalla__producto_id']
        sid = r['idTicket__sucursal_id']
        if pid and sid:
            sucursales_por_producto.setdefault(pid, set()).add(sid)

    # --- DTEs ---
    # monto por línea: preferir monto_item si > 0, si no usar precio * stock
    agg_dp = qs_dp.values(
        'productoTalla__producto_id',
        prod_articulo=F('productoTalla__producto__articulo'),
        prod_descripcion=F('productoTalla__producto__descripcion'),
        prod_marca=F('productoTalla__producto__atributo1__valor'),
        prod_marca_id=F('productoTalla__producto__atributo1_id'),
        prod_categoria=F('productoTalla__producto__categoria__nombre'),
        prod_categoria_id=F('productoTalla__producto__categoria_id'),
        prod_sexo=F('productoTalla__producto__atributo3__valor'),
        prod_sexo_id=F('productoTalla__producto__atributo3_id'),
        prod_genero=F('productoTalla__producto__atributo4__valor'),
        prod_genero_id=F('productoTalla__producto__atributo4_id'),
    ).annotate(
        unid=Sum('stock'),
        monto_item_total=Sum('monto_item'),
        monto_precio_total=Sum(ExpressionWrapper(
            F('precio') * F('stock'), output_field=DecimalField()
        )),
        costo_total=Sum(ExpressionWrapper(
            F('costo') * F('stock'), output_field=DecimalField()
        )),
        docs=Count('dte', distinct=True),
    )
    for r in agg_dp:
        pid = r['productoTalla__producto_id']
        if not pid:
            continue
        p = _get_prod(pid, r)
        p['unidades'] += int(r['unid'] or 0)
        # Fallback: si monto_item está vacío (0), usar precio * stock
        monto = int(r['monto_item_total'] or 0)
        if monto <= 0:
            monto = int(r['monto_precio_total'] or 0)
        p['monto'] += monto
        p['costo'] += int(r['costo_total'] or 0)
        p['docs'] += int(r['docs'] or 0)

    # Sucursales por producto (DTEs)
    for r in qs_dp.values('productoTalla__producto_id', 'dte__sucursal_id').distinct():
        pid = r['productoTalla__producto_id']
        sid = r['dte__sucursal_id']
        if pid and sid:
            sucursales_por_producto.setdefault(pid, set()).add(sid)

    # ---------- Calcular margen por producto + totales ----------
    productos = []
    tot_unid = 0
    tot_monto = 0
    tot_costo = 0
    for pid, p in productos_acum.items():
        margen = p['monto'] - p['costo']
        margen_pct = (margen / p['monto'] * 100) if p['monto'] > 0 else 0
        sucs = len(sucursales_por_producto.get(pid, set()))
        productos.append({
            **p,
            'margen': margen,
            'margen_pct': round(margen_pct, 1),
            'sucursales_count': sucs,
        })
        tot_unid += p['unidades']
        tot_monto += p['monto']
        tot_costo += p['costo']

    tot_margen = tot_monto - tot_costo
    tot_margen_pct = (tot_margen / tot_monto * 100) if tot_monto > 0 else 0

    # Orden según parámetro
    orden = filtros.get('orden', 'unidades_desc')
    key_map = {
        'unidades_desc': lambda x: x['unidades'],
        'monto_desc':    lambda x: x['monto'],
        'margen_desc':   lambda x: x['margen'],
        'margen_pct_desc': lambda x: x['margen_pct'],
    }
    productos.sort(key=key_map.get(orden, key_map['unidades_desc']), reverse=True)

    # Participación % (en unidades)
    for p in productos:
        p['participacion'] = round(p['unidades'] / tot_unid * 100, 1) if tot_unid > 0 else 0

    # ---------- Agregaciones por dimensión ----------
    def _agrupar_por(campo_key, campo_nombre):
        acc = {}
        for p in productos:
            key = p.get(campo_key) or None
            nombre = p.get(campo_nombre) or 'Sin clasificar'
            slot = acc.setdefault(key, {
                'id': key,
                'nombre': nombre,
                'unidades': 0, 'monto': 0, 'costo': 0,
                'skus': 0,
            })
            slot['unidades'] += p['unidades']
            slot['monto'] += p['monto']
            slot['costo'] += p['costo']
            slot['skus'] += 1
        filas = []
        for s in acc.values():
            m = s['monto'] - s['costo']
            filas.append({
                **s,
                'margen': m,
                'margen_pct': round((m / s['monto'] * 100), 1) if s['monto'] > 0 else 0,
                'participacion': round(s['unidades'] / tot_unid * 100, 1) if tot_unid > 0 else 0,
            })
        filas.sort(key=lambda x: x['unidades'], reverse=True)
        return filas

    por_marca = _agrupar_por('marca_id', 'marca')
    por_categoria = _agrupar_por('categoria_id', 'categoria')
    por_sexo = _agrupar_por('sexo_id', 'sexo')
    por_genero = _agrupar_por('genero_id', 'genero')

    # ---------- Heatmap Sucursal × Categoría ----------
    # Recalcular directo en DB para mejor precisión (evitar perder unidades por producto sin categoría)
    heat = {}  # {(sid, cat_nombre): {unidades, monto}}

    for r in qs_tp.values(
        'idTicket__sucursal_id',
        suc_nombre=F('idTicket__sucursal__alias'),
        cat_nombre=F('ProductoTalla__producto__categoria__nombre'),
    ).annotate(unid=Sum('stock'), monto=Sum('subtotal')):
        sid = r['idTicket__sucursal_id']
        suc = r['suc_nombre'] or '-'
        cat = r['cat_nombre'] or 'Sin clasificar'
        key = (sid, cat)
        h = heat.setdefault(key, {'sucursal_id': sid, 'sucursal': suc,
                                   'categoria': cat, 'unidades': 0, 'monto': 0})
        h['unidades'] += int(r['unid'] or 0)
        h['monto'] += int(r['monto'] or 0)

    for r in qs_dp.values(
        'dte__sucursal_id',
        suc_nombre=F('dte__sucursal__alias'),
        cat_nombre=F('productoTalla__producto__categoria__nombre'),
    ).annotate(unid=Sum('stock'),
               monto_item_total=Sum('monto_item'),
               monto_precio_total=Sum(ExpressionWrapper(
                   F('precio') * F('stock'), output_field=DecimalField()))):
        sid = r['dte__sucursal_id']
        suc = r['suc_nombre'] or '-'
        cat = r['cat_nombre'] or 'Sin clasificar'
        key = (sid, cat)
        monto = int(r['monto_item_total'] or 0)
        if monto <= 0:
            monto = int(r['monto_precio_total'] or 0)
        h = heat.setdefault(key, {'sucursal_id': sid, 'sucursal': suc,
                                   'categoria': cat, 'unidades': 0, 'monto': 0})
        h['unidades'] += int(r['unid'] or 0)
        h['monto'] += monto

    heatmap = sorted(
        heat.values(),
        key=lambda x: (x['sucursal'], -x['unidades']),
    )

    # ---------- KPIs ----------
    top_marca = por_marca[0]['nombre'] if por_marca else '-'
    top_categoria = por_categoria[0]['nombre'] if por_categoria else '-'
    top_producto = productos[0]['articulo'] if productos else '-'

    kpis = {
        'total_unidades': tot_unid,
        'total_monto': tot_monto,
        'total_costo': tot_costo,
        'total_margen': tot_margen,
        'total_margen_pct': round(tot_margen_pct, 1),
        'total_skus': len(productos),
        'ticket_promedio_unid': round(tot_monto / tot_unid, 0) if tot_unid > 0 else 0,
        'top_marca': top_marca,
        'top_categoria': top_categoria,
        'top_producto': top_producto,
    }

    return {
        'productos': productos,
        'por_marca': por_marca,
        'por_categoria': por_categoria,
        'por_sexo': por_sexo,
        'por_genero': por_genero,
        'heatmap': heatmap,
        'kpis': kpis,
    }


@login_required
def ver_reporte_productos_vendidos(request):
    """Vista principal del reporte de productos vendidos."""
    context = obtener_contexto_sucursales(request.user, request)
    return render(request, 'vistas/modulo_reportes/reporte_productos_vendidos.html', context)


@require_GET
@login_required
def obtener_productos_vendidos(request):
    """API: productos vendidos con agregaciones por marca/categoría/sexo/género y heatmap.

    Parámetros (todos opcionales):
      - tipo_flujo / fecha_inicio / fecha_fin
      - sucursal_id
      - marca_id, color_id, sexo_id, genero_id, categoria_id
      - temporada, anio_temporada, rango_precio
      - busqueda (texto libre)
      - orden: unidades_desc | monto_desc | margen_desc | margen_pct_desc
      - top_n (default 50) — limita sólo la tabla de productos, no las agregaciones
    """
    try:
        tipo_flujo = request.GET.get('tipo_flujo', 'mes_full')
        fi_param = request.GET.get('fecha_inicio')
        ff_param = request.GET.get('fecha_fin')

        # Reusar helper de rangos del reporte comparativo
        fi, ff, _fi_ant, _ff_ant = _calcular_rangos_comparativo(
            tipo_flujo, fi_param, ff_param
        )

        filtros = {
            'marca_id': request.GET.get('marca_id') or None,
            'color_id': request.GET.get('color_id') or None,
            'sexo_id': request.GET.get('sexo_id') or None,
            'genero_id': request.GET.get('genero_id') or None,
            'categoria_id': request.GET.get('categoria_id') or None,
            'temporada': request.GET.get('temporada') or None,
            'anio_temporada': request.GET.get('anio_temporada') or None,
            'rango_precio': request.GET.get('rango_precio') or None,
            'busqueda': (request.GET.get('busqueda') or '').strip() or None,
            'orden': request.GET.get('orden', 'unidades_desc'),
            # Por defecto excluye productos marcados como no-analíticos
            # (exhibición, consignación, etc.) — misma lógica que verGestionProducto
            'incluir_excluidos': request.GET.get('incluir_excluidos') in ('1', 'true', 'True'),
        }

        try:
            top_n = int(request.GET.get('top_n', 50))
        except (TypeError, ValueError):
            top_n = 50
        top_n = max(1, min(top_n, 500))

        data = _agregar_productos_vendidos(fi, ff, filtros, request.user, request)

        return JsonResponse({
            'success': True,
            'tipo_flujo': tipo_flujo,
            'periodo': {
                'inicio': fi.strftime('%Y-%m-%d'),
                'fin': ff.strftime('%Y-%m-%d'),
            },
            'kpis': data['kpis'],
            'productos': data['productos'][:top_n],
            'productos_total': len(data['productos']),
            'por_marca': data['por_marca'][:100],
            'por_categoria': data['por_categoria'][:100],
            'por_sexo': data['por_sexo'],
            'por_genero': data['por_genero'],
            'heatmap': data['heatmap'],
        })

    except Exception as e:
        logger.exception("Error al obtener reporte productos vendidos")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def obtener_atributo_opciones(request):
    """API: lista de opciones de un atributo (marca, color, sexo, género) o categorías.

    Parámetro:
      - tipo: 'marca' | 'color' | 'sexo' | 'genero' | 'categoria'
    """
    try:
        tipo = (request.GET.get('tipo') or '').lower()
        mapping = {
            'marca': 'Marca',
            'color': 'Color',
            'sexo': 'Sexo',
            'genero': 'Género',
        }

        if tipo == 'categoria':
            qs = Categoria.objects.order_by('nombre').values('id', 'nombre')
            return JsonResponse({
                'success': True,
                'opciones': [{'id': x['id'], 'nombre': x['nombre']} for x in qs],
            })

        nombre_atributo = mapping.get(tipo)
        if not nombre_atributo:
            return JsonResponse({
                'success': False,
                'error': "Parametro 'tipo' invalido. Use: marca | color | sexo | genero | categoria",
            })

        qs = AtributoOpcion.objects.filter(
            atributo__nombre=nombre_atributo
        ).order_by('valor').values('id', 'valor')

        return JsonResponse({
            'success': True,
            'opciones': [{'id': x['id'], 'nombre': x['valor']} for x in qs],
        })
    except Exception as e:
        logger.exception("Error al obtener opciones de atributo para reportes")
        return JsonResponse({'success': False, 'error': str(e)})
