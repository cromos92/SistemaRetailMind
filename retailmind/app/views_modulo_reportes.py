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
    Compras, Compras_Producto, Compras_Producto_Talla, Productos_Recepcionados,
    Dte, Dte_Productos, Dte_Detalle_Pago, Ticket, Ticket_Productos,
    Producto, Producto_Talla, Movimientos_Producto, Sucursal, EmpresaUser,
    Empresa, Vendedor, LoteProducto, Traspaso, AjusteInventario,
    TicketDetallePago, METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES
)
from .utils_permisos import (
    obtener_sucursales_usuario,
    puede_ver_sucursal,
    filtrar_queryset_por_sucursal,
    usuario_puede_ver_todas_sucursales,
    obtener_contexto_sucursales,
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
        fecha_inicio  = request.GET.get('fecha_inicio')
        fecha_fin     = request.GET.get('fecha_fin')
        sucursal_id   = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id  = request.GET.get('categoria_id')
        limite        = int(request.GET.get('limite', 20))

        if not fecha_inicio or not fecha_fin:
            fecha_fin    = timezone.now().date()
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
        import traceback
        print(traceback.format_exc())
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
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'success': False, 'error': f'Error al generar reporte de valoración: {str(e)}'})


@require_GET
@login_required
def reporte_rotacion_inventario(request):
    """Reporte de rotación de inventario por sucursal"""
    try:
        periodo_dias = int(request.GET.get('periodo_dias', 90))
        sucursal_id  = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = request.GET.get('categoria_id')

        fecha_inicio = timezone.now().date() - timedelta(days=periodo_dias)

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
        import traceback
        print(traceback.format_exc())
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
    context = obtener_contexto_sucursales(request.user, request)
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
            estado='PAGADO',
            modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        ).select_related('vendedor', 'sucursal')

        # Filtrar por sucursal según selección o permisos
        queryset_tickets = filtrar_queryset_por_sucursal(queryset_tickets, request.user, request)

        if vendedor_id:
            queryset_tickets = queryset_tickets.filter(vendedor_id=vendedor_id)

        # Agrupar tickets por vendedor
        tickets_por_vendedor = queryset_tickets.values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor'
        ).annotate(
            total_ventas=Sum('total'),
            total_descuentos=Sum('descuento'),
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
                        'descuentos': 0,
                        'documentos': 0
                    }
                ventas_acumuladas[vid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[vid]['descuentos'] += int(item['total_descuentos'] or 0)
                ventas_acumuladas[vid]['documentos'] += item['total_documentos'] or 0
        
        # ========== DTEs (Documentos migrados) ==========
        # Incluir estados: EMITIDO (nuevo), PAGADO (migrados de Laravel)
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fecha_inicio.date() if hasattr(fecha_inicio, 'date') else fecha_inicio,
            fecha_emision__lte=fecha_fin.date() if hasattr(fecha_fin, 'date') else fecha_fin,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        ).exclude(
            estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
        ).select_related('vendedor', 'sucursal')

        # Filtrar por sucursal según selección o permisos
        queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)

        if vendedor_id:
            queryset_dtes = queryset_dtes.filter(vendedor_id=vendedor_id)

        # Agrupar DTEs de VENTA (excluir NCs)
        dtes_por_vendedor = queryset_dtes.exclude(tipo_documento='NOTA DE CREDITO').values(
            'vendedor__id',
            'vendedor__nombre',
            'vendedor__codigo_vendedor'
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            total_documentos=Count('id')
        )

        # Agrupar NCs (devoluciones) por vendedor
        nc_por_vendedor = queryset_dtes.filter(tipo_documento='NOTA DE CREDITO').values(
            'vendedor__id'
        ).annotate(
            total_devoluciones=Sum('monto_con_iva'),
            cantidad_devoluciones=Count('id')
        )
        devoluciones_vend = {
            item['vendedor__id']: {
                'total': int(item['total_devoluciones'] or 0),
                'cantidad': item['cantidad_devoluciones'] or 0
            }
            for item in nc_por_vendedor if item['vendedor__id']
        }
        
        for item in dtes_por_vendedor:
            vid = item['vendedor__id']
            if vid:
                if vid not in ventas_acumuladas:
                    ventas_acumuladas[vid] = {
                        'nombre': item['vendedor__nombre'],
                        'codigo': item['vendedor__codigo_vendedor'],
                        'ventas': 0,
                        'documentos': 0,
                        'devoluciones': 0,
                        'cantidad_devoluciones': 0
                    }
                ventas_acumuladas[vid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[vid]['documentos'] += item['total_documentos'] or 0

        # Agregar devoluciones NC a los vendedores
        for vid, nc_data in devoluciones_vend.items():
            if vid in ventas_acumuladas:
                ventas_acumuladas[vid]['devoluciones'] = nc_data['total']
                ventas_acumuladas[vid]['cantidad_devoluciones'] = nc_data['cantidad']
        
        # Calcular total general (ventas netas)
        total_general = sum(
            v['ventas'] - v.get('devoluciones', 0)
            for v in ventas_acumuladas.values()
        )
        
        # Procesar datos ordenados por ventas netas
        vendedores_data = []
        for vid, data in sorted(ventas_acumuladas.items(), key=lambda x: x[1]['ventas'], reverse=True):
            ventas_netas = data['ventas'] - data.get('devoluciones', 0)
            participacion = (ventas_netas / total_general * 100) if total_general > 0 else 0
            vendedores_data.append({
                'id': vid,
                'nombre': data['nombre'],
                'codigo': data['codigo'],
                'ventas': ventas_netas,
                'ventas_brutas': data['ventas'],
                'descuentos': data.get('descuentos', 0),
                'devoluciones': data.get('devoluciones', 0),
                'cantidad_devoluciones': data.get('cantidad_devoluciones', 0),
                'documentos': data['documentos'],
                'participacion': round(participacion, 1)
            })
        
        # KPIs
        total_documentos = sum(v['documentos'] for v in ventas_acumuladas.values())
        ticket_promedio = total_general / total_documentos if total_documentos > 0 else 0
        top_vendedor = vendedores_data[0]['nombre'] if vendedores_data else '-'
        total_devoluciones_general = sum(v.get('devoluciones', 0) for v in ventas_acumuladas.values())
        
        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data,
            'kpis': {
                'total_ventas': int(total_general),
                'total_ventas_brutas': int(total_general + total_devoluciones_general),
                'total_devoluciones': int(total_devoluciones_general),
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
            estado='PAGADO',
            modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        ).select_related('sucursal', 'vendedor')

        # Filtrar por sucursal según selección o permisos
        if sucursal_id:
            queryset_tickets = filtrar_queryset_por_sucursal(queryset_tickets, request.user, request)

        # Agrupar tickets por sucursal
        tickets_por_sucursal = queryset_tickets.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion'
        ).annotate(
            total_ventas=Sum('total'),
            subtotal_ventas=Sum('subTotal'),
            total_descuentos=Sum('descuento'),
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
                        'descuentos': 0,
                        'documentos': 0
                    }
                    vendedores_por_sucursal[sid] = set()
                ventas_acumuladas[sid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[sid]['subtotal'] += int(item['subtotal_ventas'] or 0)
                ventas_acumuladas[sid]['descuentos'] += int(item['total_descuentos'] or 0)
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
        queryset_dtes = Dte.objects.filter(
            fecha_emision__gte=fecha_inicio.date() if hasattr(fecha_inicio, 'date') else fecha_inicio,
            fecha_emision__lte=fecha_fin.date() if hasattr(fecha_fin, 'date') else fecha_fin,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        ).exclude(
            estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']
        ).select_related('sucursal', 'vendedor')

        # Filtrar por sucursal según selección o permisos
        queryset_dtes = filtrar_queryset_por_sucursal(queryset_dtes, request.user, request)

        # Agrupar DTEs de VENTA (excluir NCs)
        queryset_ventas = queryset_dtes.exclude(tipo_documento='NOTA DE CREDITO')
        dtes_por_sucursal = queryset_ventas.values(
            'sucursal__id',
            'sucursal__alias',
            'sucursal__direccion'
        ).annotate(
            total_ventas=Sum('monto_con_iva'),
            subtotal_ventas=Sum('monto_neto'),
            total_descuentos=Sum('descuento'),
            total_documentos=Count('id')
        )
        
        # Agrupar NCs (devoluciones) por sucursal
        queryset_nc = queryset_dtes.filter(tipo_documento='NOTA DE CREDITO')
        nc_por_sucursal = queryset_nc.values('sucursal__id').annotate(
            total_devoluciones=Sum('monto_con_iva'),
            cantidad_devoluciones=Count('id')
        )
        devoluciones_map = {
            item['sucursal__id']: {
                'total': int(item['total_devoluciones'] or 0),
                'cantidad': item['cantidad_devoluciones'] or 0
            }
            for item in nc_por_sucursal
        }
        
        for item in dtes_por_sucursal:
            sid = item['sucursal__id']
            if sid:
                if sid not in ventas_acumuladas:
                    ventas_acumuladas[sid] = {
                        'alias': item['sucursal__alias'],
                        'direccion': item['sucursal__direccion'],
                        'ventas': 0,
                        'subtotal': 0,
                        'descuentos': 0,
                        'documentos': 0,
                        'devoluciones': 0,
                        'cantidad_devoluciones': 0
                    }
                    vendedores_por_sucursal[sid] = set()
                ventas_acumuladas[sid]['ventas'] += int(item['total_ventas'] or 0)
                ventas_acumuladas[sid]['subtotal'] += int(item['subtotal_ventas'] or 0)
                ventas_acumuladas[sid]['descuentos'] += int(item['total_descuentos'] or 0)
                ventas_acumuladas[sid]['documentos'] += item['total_documentos'] or 0

        # Agregar devoluciones NC a las sucursales
        for sid, nc_data in devoluciones_map.items():
            if sid:
                if sid not in ventas_acumuladas:
                    # Sucursal solo con NC (sin ventas en el período)
                    try:
                        from .models import Sucursal as _Sucursal
                        suc = _Sucursal.objects.get(id=sid)
                        ventas_acumuladas[sid] = {
                            'alias': suc.alias, 'direccion': suc.direccion,
                            'ventas': 0, 'subtotal': 0, 'documentos': 0,
                            'devoluciones': 0, 'cantidad_devoluciones': 0
                        }
                    except Exception:
                        pass
                if sid in ventas_acumuladas:
                    ventas_acumuladas[sid]['devoluciones'] = nc_data['total']
                    ventas_acumuladas[sid]['cantidad_devoluciones'] = nc_data['cantidad']
        
        # Contar vendedores de DTEs
        for dte in queryset_dtes.values('sucursal_id', 'vendedor_id').distinct():
            sid = dte['sucursal_id']
            vid = dte['vendedor_id']
            if sid and vid:
                if sid not in vendedores_por_sucursal:
                    vendedores_por_sucursal[sid] = set()
                vendedores_por_sucursal[sid].add(vid)
        
        # Calcular total general (ventas netas = bruto - devoluciones)
        total_general = sum(
            v['ventas'] - v.get('devoluciones', 0)
            for v in ventas_acumuladas.values()
        )
        
        # Convertir a formato de salida ordenado
        ventas_por_sucursal = []
        for sid, data in sorted(ventas_acumuladas.items(), key=lambda x: x[1]['ventas'], reverse=True):
            total_docs = data['documentos']
            ventas_netas = data['ventas'] - data.get('devoluciones', 0)
            ventas_por_sucursal.append({
                'sucursal__id': sid,
                'sucursal__alias': data['alias'],
                'sucursal__direccion': data['direccion'],
                'total_ventas': ventas_netas,
                'total_ventas_brutas': data['ventas'],
                'total_devoluciones': data.get('devoluciones', 0),
                'cantidad_devoluciones': data.get('cantidad_devoluciones', 0),
                'descuentos': data.get('descuentos', 0),
                'subtotal_ventas': data['subtotal'],
                'total_documentos': total_docs,
                'ticket_promedio': ventas_netas / total_docs if total_docs > 0 else 0,
                'vendedores_count': len(vendedores_por_sucursal.get(sid, set()))
            })

        # Procesar datos
        sucursales_data = []
        for item in ventas_por_sucursal:
            total_ventas = item['total_ventas'] or 0       # ventas netas
            subtotal = item['subtotal_ventas'] or 0
            # Calcular IVA (total neto - subtotal neto)
            iva = total_ventas - subtotal
            total_docs = item['total_documentos'] or 0
            participacion = (total_ventas / total_general * 100) if total_general > 0 else 0
            
            sucursales_data.append({
                'id': item['sucursal__id'],
                'nombre': item['sucursal__alias'],
                'direccion': item['sucursal__direccion'],
                'neto': int(subtotal),
                'iva': int(iva),
                'descuentos': int(item.get('descuentos', 0)),
                'ventas': int(total_ventas),
                'ventas_brutas': int(item.get('total_ventas_brutas', total_ventas)),
                'devoluciones': int(item.get('total_devoluciones', 0)),
                'cantidad_devoluciones': int(item.get('cantidad_devoluciones', 0)),
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

        # ---- DTEs ----
        qs_dtes = Dte.objects.filter(
            vendedor_id=vendedor_id,
            fecha_emision__gte=fecha_inicio,
            fecha_emision__lte=fecha_fin,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            estado_dte__in=['EMITIDO', 'PAGADO'],
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
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})


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
            estado='PAGADO',
            modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        ).select_related('sucursal')
        
        # Filtrar por sucursal según permisos
        queryset = filtrar_queryset_por_sucursal(queryset, request.user, request)

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
        
        # Si no se proporcionan fechas, usar el día actual
        if not fecha_desde or not fecha_hasta:
            fecha_fin = timezone.now().date()
            fecha_desde = fecha_fin
            fecha_hasta = fecha_fin
        else:
            fecha_desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        
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
        ).exclude(
            # Excluir facturas entre sucursales de la misma empresa
            receptor__isnull=False,
            receptor_id=F('emisor_id')
        )
        
        # Filtrar por sucursal según permisos del usuario
        queryset = filtrar_queryset_por_sucursal(queryset, request.user, request)

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
        
        # Calcular totales por método de pago desde Dte_Detalle_Pago
        for dte in queryset:
            total = int(dte.monto_con_iva)
            descuento_db = int(dte.descuento) if dte.descuento else 0
            tipo_doc_upper = dte.tipo_documento.upper() if dte.tipo_documento else ''

            # Las Notas de Crédito se acumulan aparte y se restan del total
            if 'NOTA' in tipo_doc_upper and 'CREDITO' in tipo_doc_upper:
                resumen['notas_credito'] += total
                continue

            # Obtener métodos de pago del DTE usando la relación inversa
            detalles_pago = dte.dte_asociado.all()
            metodo_procesado = False
            pagado_sum = 0
            
            if detalles_pago.exists():
                for detalle in detalles_pago:
                    metodo = detalle.metodo_pago.upper()
                    monto = int(detalle.monto)
                    pagado_sum += monto
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

            # Calcular descuento real: usar dte.descuento si existe,
            # sino inferirlo de la diferencia total - pagado
            if pagado_sum > 0 and descuento_db == 0:
                descuento = max(0, total - pagado_sum)
            else:
                descuento = descuento_db

            resumen['descuentos'] += descuento
            resumen['ventas_brutas'] += total
            
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
        
        # Total neto = ventas brutas - notas de crédito - descuentos
        resumen['total_global'] = resumen['ventas_brutas'] - resumen['notas_credito'] - resumen['descuentos']

        return JsonResponse({
            'success': True,
            'documentos': documentos_data,
            'resumen': resumen,
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

        # Si no se proporcionan fechas, usar el día actual
        if not fecha_desde or not fecha_hasta:
            fecha_fin = timezone.now().date()
            fecha_desde = fecha_fin
            fecha_hasta = fecha_fin
        else:
            fecha_desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            fecha_hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()

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
        ).exclude(
            receptor__isnull=False,
            receptor_id=F('emisor_id')
        )

        # Filtrar por sucursal: superuser puede ver todas, usuario normal solo la suya
        queryset = filtrar_queryset_por_sucursal(queryset, request.user, request)

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
        import traceback
        print(f"❌ Error en reporte de existencias por sucursal: {str(e)}")
        print(traceback.format_exc())
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
        ws['A2'].value = f"Generado: {_date.today().strftime('%d/%m/%Y')}   |   Total registros: {resumen.get('total_productos', 0)}   |   Stock total: {resumen.get('stock_total', 0):,}"
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
        import traceback
        print(f"❌ Error al exportar a Excel: {str(e)}")
        print(traceback.format_exc())
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
            f"Generado: {_date.today().strftime('%d/%m/%Y')}  |  "
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
            canvas.drawString(1.5*cm, 1*cm, f"RetailMind — {nombre_sucursal} — {_date.today().strftime('%d/%m/%Y')}")
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
        import traceback
        print(f"❌ Error al exportar a PDF: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({'success': False, 'error': f'Error al exportar PDF: {str(e)}'})


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
        anio = int(request.GET.get('anio', datetime.now().year))
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
        import traceback
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte: {str(e)}',
            'traceback': traceback.format_exc()
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


# ========== API RENDIMIENTO DE COMPRAS: ROTACIÓN REAL + TRAZABILIDAD ==========
# Estrategia: Reconstruir ciclo de vida COMPLETO desde Movimientos_Producto
#
# ENTRADA (comprado):   concepto RECEPCION_COMPRA + INGRESO_INICIAL
# DESPACHO (sucursales): concepto TRASPASO_SUCURSAL + DTEs TRASPASO
# VENTA (al cliente):   concepto VENTA_PUBLICO/MAYORISTA + Ticket_Productos
#
# Esto cubre TANTO datos migrados de Laravel como datos nuevos del sistema.
# Laravel no tenía módulo de Compras, todo se registraba como movimientos.

CONCEPTOS_ENTRADA = ['RECEPCION_COMPRA', 'INGRESO_INICIAL']
CONCEPTOS_DESPACHO = ['TRASPASO_SUCURSAL', 'TRASPASO_SALIDA']
CONCEPTOS_VENTA = ['VENTA_PUBLICO', 'VENTA_MAYORISTA']


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
            'despacho': int(desp['uds'] or 0),
            'venta_mov': int(vtas['uds'] or 0),
            'venta_tk': int(vtas_tk['uds'] or 0),
            'ingreso_mov': float(vtas['ingreso'] or 0),
            'ingreso_tk': float(vtas_tk['ingreso'] or 0),
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
            'despacho': int(desp['uds'] or 0),
            'venta_mov': int(vtas['uds'] or 0),
            'venta_tk': 0,
            'ingreso_mov': float(vtas['ingreso'] or 0),
            'ingreso_tk': 0,
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
            'stock_sin_vender': entrada - vendido,
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
        anio = int(request.GET.get('anio', datetime.now().year))
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
        anio_actual = datetime.now().year
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
                uds_desp = d.get('uds', 0) or 0
                uds_vend = max(vm.get('uds', 0) or 0, vt.get('uds', 0) or 0)
                ingreso = max(float(vm.get('ingreso', 0) or 0), float(vt.get('ingreso', 0) or 0))
                costo_e = float(e.get('costo', 0) or 0)
                costo_d = float(d.get('costo', 0) or 0)
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
                    despachos_top[d['ProductoTalla_id']] = d['uds'] or 0

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
        import traceback
        return JsonResponse({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
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
            fecha_fin_dt = timezone.now().date()
            fecha_inicio_dt = fecha_fin_dt - timedelta(days=30)
        else:
            fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d').date()

        recepciones_qs = Productos_Recepcionados.objects.filter(
            fecha__range=[fecha_inicio_dt, fecha_fin_dt],
        ).select_related(
            'compra_producto_talla__compra_producto__compras',
            'producto_talla__producto',
            'dte__emisor',
            'sucursal_destino',
        )

        if proveedor_id:
            recepciones_qs = recepciones_qs.filter(dte__emisor_id=proveedor_id)
        if sucursal_id:
            recepciones_qs = recepciones_qs.filter(sucursal_destino_id=sucursal_id)

        # --- Totals ---
        total_items = recepciones_qs.count()
        total_unidades = recepciones_qs.aggregate(t=Sum('stockArribado'))['t'] or 0
        total_reposicion = recepciones_qs.filter(es_reposicion=True).count()
        total_nuevo = recepciones_qs.filter(es_reposicion=False).count()
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
        por_proveedor = (
            recepciones_qs
            .filter(dte__isnull=False)
            .values('dte__emisor__nombre', 'dte__emisor__rut')
            .annotate(
                items=Count('id'),
                unidades=Sum('stockArribado'),
                reposiciones=Count('id', filter=Q(es_reposicion=True)),
                nuevos=Count('id', filter=Q(es_reposicion=False)),
            )
            .order_by('-unidades')
        )
        por_proveedor_data = [
            {
                'proveedor': row['dte__emisor__nombre'],
                'rut': row['dte__emisor__rut'],
                'items': row['items'],
                'unidades': row['unidades'] or 0,
                'reposiciones': row['reposiciones'],
                'nuevos': row['nuevos'],
            }
            for row in por_proveedor
        ]

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
        import traceback
        return JsonResponse({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
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
            fecha_fin_dt = timezone.now().date()
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
        import traceback
        return JsonResponse({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
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
        anio = int(request.GET.get('anio', datetime.now().year))
        proveedor_id = request.GET.get('proveedor_id')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')

        compras_qs = Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'])
        recep_filter = Q(compras_producto__compras_producto_talla__productos_recepcionados__fecha__year=anio)
        if fecha_inicio:
            recep_filter &= Q(compras_producto__compras_producto_talla__productos_recepcionados__fecha__gte=fecha_inicio)
        if fecha_fin:
            recep_filter &= Q(compras_producto__compras_producto_talla__productos_recepcionados__fecha__lte=fecha_fin)

        ids_con_recep = set(Compras.objects.filter(recep_filter).values_list('id', flat=True).distinct())
        ids_del_anio = set(Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'], fecha__year=anio).values_list('id', flat=True))
        ids_total = ids_con_recep | ids_del_anio

        if proveedor_id:
            compras_qs = compras_qs.filter(empresa_id=proveedor_id, id__in=ids_total)
        else:
            compras_qs = compras_qs.filter(id__in=ids_total)

        prov_ids = list(compras_qs.values_list('empresa_id', flat=True).distinct())
        proveedores = Empresa.objects.filter(id__in=prov_ids).order_by('nombre')

        results = []
        for prov in proveedores:
            c_ids = list(compras_qs.filter(empresa=prov).values_list('id', flat=True))
            if not c_ids:
                continue

            cpt_qs = Compras_Producto_Talla.objects.filter(compra_producto__compras_id__in=c_ids)
            pares_comprados = cpt_qs.aggregate(total=Sum('stock'))['total'] or 0
            inversion = cpt_qs.aggregate(total=Sum(F('compra_producto__costo') * F('stock')))['total'] or 0

            recep_qs = Productos_Recepcionados.objects.filter(compra_producto_talla__compra_producto__compras_id__in=c_ids)
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
            'total_venta': sum(r['venta_total'] for r in results),
            'total_margen': sum(r['margen'] for r in results),
        }, 'proveedores': results})

    except Exception as e:
        import traceback
        return JsonResponse({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}, status=500)


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
                    '% Recep', '% Venta', 'Inversion', 'Venta Total', 'Margen', 'Stock Disp.']
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=h)
            c.font, c.fill, c.alignment, c.border = hf, hfill, Alignment(horizontal='center'), brd

        for i, p in enumerate(data['proveedores'], 2):
            vals = [p['proveedor_nombre'], p['proveedor_rut'], p['pares_comprados'], p['pares_recepcionados'],
                    p['pares_vendidos'], p['pct_recepcion'], p['pct_venta'], p['inversion_total'],
                    p['venta_total'], p['margen'], p['stock_disponible']]
            for col, v in enumerate(vals, 1):
                c = ws.cell(row=i, column=col, value=v)
                c.border = brd
                if col >= 3:
                    c.alignment = Alignment(horizontal='right')

        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or '')) for c in col) + 3, 25)

        resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename=rendimiento_proveedor_{request.GET.get("anio", datetime.now().year)}.xlsx'
        wb.save(resp)
        return resp
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


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
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)})