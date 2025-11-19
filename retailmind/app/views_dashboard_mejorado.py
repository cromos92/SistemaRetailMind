"""
Dashboard Mejorado - RetailMind
Dashboard integral que combina datos de Compras, Documentos y Productos
Análisis completo del negocio con métricas avanzadas
"""

from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.db.models import Sum, Count, Avg, F, Q, DecimalField, ExpressionWrapper
from django.db.models.functions import TruncMonth, TruncDay, TruncWeek
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
import json

from .models import (
    Compras, Compras_Producto, Compras_Producto_Talla,
    Dte, Dte_Productos, Dte_Detalle_Pago,
    Producto, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados, LoteProducto,
    Empresa, Sucursal, Vendedor,
    Ticket, Ticket_Productos, TicketDetallePago
)


# ========== DASHBOARD INTEGRAL MEJORADO ==========

@login_required
@require_GET
def dashboard_integral_mejorado(request):
    """Vista principal del dashboard integral mejorado"""
    return render(request, 'vistas/modulo_dashboards/dashboard_integral_mejorado.html')


@login_required
@require_GET
def obtener_metricas_dashboard_integral(request):
    """
    Obtiene todas las métricas del dashboard integral
    Combina datos de compras, ventas, documentos y productos
    """
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        periodo = request.GET.get('periodo', '30')  # días
        
        # Si no se especifican fechas, usar período por defecto
        if not fecha_inicio or not fecha_fin:
            fecha_fin_dt = timezone.now()
            fecha_inicio_dt = fecha_fin_dt - timedelta(days=int(periodo))
            fecha_inicio = fecha_inicio_dt.strftime('%Y-%m-%d')
            fecha_fin = fecha_fin_dt.strftime('%Y-%m-%d')
        
        # ===== MÉTRICAS DE COMPRAS =====
        metricas_compras = calcular_metricas_compras(fecha_inicio, fecha_fin, sucursal_id)
        
        # ===== MÉTRICAS DE VENTAS =====
        metricas_ventas = calcular_metricas_ventas(fecha_inicio, fecha_fin, sucursal_id)
        
        # ===== MÉTRICAS DE DOCUMENTOS =====
        metricas_documentos = calcular_metricas_documentos(fecha_inicio, fecha_fin)
        
        # ===== MÉTRICAS DE INVENTARIO =====
        metricas_inventario = calcular_metricas_inventario(sucursal_id)
        
        # ===== ANÁLISIS FINANCIERO =====
        analisis_financiero = calcular_analisis_financiero(fecha_inicio, fecha_fin, 
                                                           metricas_compras, 
                                                           metricas_ventas)
        
        # ===== ANÁLISIS DE EFICIENCIA =====
        analisis_eficiencia = calcular_analisis_eficiencia(metricas_compras, 
                                                           metricas_ventas, 
                                                           metricas_inventario)
        
        # ===== GRÁFICOS Y ANÁLISIS TEMPORALES =====
        graficos = {
            'ventas_vs_compras_mensual': obtener_ventas_vs_compras_mensual(fecha_inicio, fecha_fin),
            'documentos_por_tipo': obtener_documentos_por_tipo(fecha_inicio, fecha_fin),
            'top_productos_vendidos': obtener_top_productos_vendidos(fecha_inicio, fecha_fin, limit=10),
            'top_productos_comprados': obtener_top_productos_comprados(fecha_inicio, fecha_fin, limit=10),
            'cumplimiento_proveedores': obtener_cumplimiento_proveedores(fecha_inicio, fecha_fin),
            'estado_stock_critico': obtener_estado_stock_critico(),
            'flujo_caja_proyectado': obtener_flujo_caja_proyectado(fecha_inicio, fecha_fin),
            'rentabilidad_por_categoria': obtener_rentabilidad_por_categoria(fecha_inicio, fecha_fin),
        }
        
        # ===== ALERTAS Y RECOMENDACIONES =====
        alertas = generar_alertas_inteligentes(metricas_inventario, metricas_compras, metricas_ventas)
        
        return JsonResponse({
            'success': True,
            'metricas': {
                'compras': metricas_compras,
                'ventas': metricas_ventas,
                'documentos': metricas_documentos,
                'inventario': metricas_inventario,
                'financiero': analisis_financiero,
                'eficiencia': analisis_eficiencia
            },
            'graficos': graficos,
            'alertas': alertas,
            'periodo': {
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin,
                'dias': (datetime.strptime(fecha_fin, '%Y-%m-%d') - 
                        datetime.strptime(fecha_inicio, '%Y-%m-%d')).days
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener métricas: {str(e)}'
        }, status=500)


# ========== FUNCIONES DE CÁLCULO DE MÉTRICAS ==========

def calcular_metricas_compras(fecha_inicio, fecha_fin, sucursal_id=None):
    """Calcula métricas relacionadas con compras"""
    
    # Query base de compras
    compras_query = Compras.objects.filter(
        fecha__range=[fecha_inicio, fecha_fin]
    )
    
    # Total de compras
    total_compras = compras_query.count()
    
    # IDs de compras para filtros
    compras_ids = list(compras_query.values_list('id', flat=True))
    
    # Productos y tallas de compras
    productos_compras = Compras_Producto.objects.filter(compras__in=compras_ids)
    tallas_compras = Compras_Producto_Talla.objects.filter(
        compra_producto__compras__in=compras_ids
    )
    
    # Unidades esperadas vs recepcionadas
    total_unidades_esperadas = tallas_compras.aggregate(
        total=Sum('stock')
    )['total'] or 0
    
    recepciones = Productos_Recepcionados.objects.filter(
        compra_producto_talla__compra_producto__compras__in=compras_ids
    )
    total_unidades_recepcionadas = recepciones.aggregate(
        total=Sum('stockArribado')
    )['total'] or 0
    
    # Cumplimiento
    cumplimiento = 0
    if total_unidades_esperadas > 0:
        cumplimiento = round((total_unidades_recepcionadas / total_unidades_esperadas) * 100, 1)
    
    # Inversión total (costo)
    inversion_total = productos_compras.aggregate(
        total=Sum(F('costo') * F('compras_producto_talla__stock'))
    )['total'] or 0
    
    # Valor esperado de venta
    valor_venta_esperado = productos_compras.aggregate(
        total=Sum(F('precioSugerido') * F('compras_producto_talla__stock'))
    )['total'] or 0
    
    # Costo promedio por unidad
    costo_promedio_unidad = 0
    if total_unidades_esperadas > 0:
        costo_promedio_unidad = float(inversion_total) / total_unidades_esperadas
    
    # Número de proveedores únicos
    proveedores_unicos = compras_query.values('empresa').distinct().count()
    
    # Ticket promedio de compra
    ticket_promedio = 0
    if total_compras > 0:
        ticket_promedio = float(inversion_total) / total_compras
    
    return {
        'total_compras': total_compras,
        'total_unidades_esperadas': int(total_unidades_esperadas),
        'total_unidades_recepcionadas': int(total_unidades_recepcionadas),
        'cumplimiento_porcentaje': float(cumplimiento),
        'inversion_total': float(inversion_total),
        'valor_venta_esperado': float(valor_venta_esperado),
        'costo_promedio_unidad': round(costo_promedio_unidad, 2),
        'proveedores_unicos': proveedores_unicos,
        'ticket_promedio': round(ticket_promedio, 2),
        'productos_distintos': productos_compras.values('producto').distinct().count()
    }


def calcular_metricas_ventas(fecha_inicio, fecha_fin, sucursal_id=None):
    """Calcula métricas relacionadas con ventas"""
    
    # Query base de tickets de venta
    tickets_query = Ticket.objects.filter(
        fecha_creacion__range=[fecha_inicio, fecha_fin],
        estado__in=['completado', 'pagado']
    )
    
    if sucursal_id:
        tickets_query = tickets_query.filter(sucursal_id=sucursal_id)
    
    # Total de ventas
    total_ventas = tickets_query.count()
    
    # Total de ingresos
    total_ingresos = tickets_query.aggregate(
        total=Sum('total')
    )['total'] or 0
    
    # Unidades vendidas
    detalles_ventas = Ticket_Productos.objects.filter(
        ticket__in=tickets_query
    )
    
    total_unidades_vendidas = detalles_ventas.aggregate(
        total=Sum('cantidad')
    )['total'] or 0
    
    # Ticket promedio
    ticket_promedio = 0
    if total_ventas > 0:
        ticket_promedio = float(total_ingresos) / total_ventas
    
    # Precio promedio por unidad
    precio_promedio_unidad = 0
    if total_unidades_vendidas > 0:
        precio_promedio_unidad = float(total_ingresos) / total_unidades_vendidas
    
    # Ventas por método de pago
    pagos = TicketDetallePago.objects.filter(
        ticket__in=tickets_query
    ).values('metodo_pago').annotate(
        total=Sum('monto'),
        cantidad=Count('id')
    )
    
    metodos_pago = {}
    for pago in pagos:
        metodos_pago[pago['metodo_pago']] = {
            'total': float(pago['total']),
            'cantidad': pago['cantidad']
        }
    
    # Número de clientes únicos
    clientes_unicos = tickets_query.exclude(cliente__isnull=True).values('cliente').distinct().count()
    
    # Número de vendedores activos
    vendedores_activos = tickets_query.exclude(vendedor__isnull=True).values('vendedor').distinct().count()
    
    return {
        'total_ventas': total_ventas,
        'total_ingresos': float(total_ingresos),
        'total_unidades_vendidas': int(total_unidades_vendidas),
        'ticket_promedio': round(ticket_promedio, 2),
        'precio_promedio_unidad': round(precio_promedio_unidad, 2),
        'metodos_pago': metodos_pago,
        'clientes_unicos': clientes_unicos,
        'vendedores_activos': vendedores_activos,
        'productos_distintos_vendidos': detalles_ventas.values('producto_talla__producto').distinct().count()
    }


def calcular_metricas_documentos(fecha_inicio, fecha_fin):
    """Calcula métricas relacionadas con documentos (DTEs)"""
    
    # DTEs emitidos
    dtes_emitidos = Dte.objects.filter(
        fecha_emision__range=[fecha_inicio, fecha_fin],
        tipo_transaccion='emitido'
    )
    
    # DTEs recibidos
    dtes_recibidos = Dte.objects.filter(
        fecha_emision__range=[fecha_inicio, fecha_fin],
        tipo_transaccion='recibido'
    )
    
    # Documentos por tipo
    docs_por_tipo = {}
    for tipo in ['33', '34', '39', '52', '56', '61']:  # Facturas, NC, Boletas, Guías
        count_emitidos = dtes_emitidos.filter(tipo_documento=tipo).count()
        count_recibidos = dtes_recibidos.filter(tipo_documento=tipo).count()
        if count_emitidos > 0 or count_recibidos > 0:
            docs_por_tipo[tipo] = {
                'emitidos': count_emitidos,
                'recibidos': count_recibidos
            }
    
    # Monto total de DTEs emitidos
    monto_total_emitidos = dtes_emitidos.aggregate(
        total=Sum('total')
    )['total'] or 0
    
    # Monto total de DTEs recibidos
    monto_total_recibidos = dtes_recibidos.aggregate(
        total=Sum('total')
    )['total'] or 0
    
    # Documentos por estado
    estados_emitidos = dtes_emitidos.values('estado_dte').annotate(
        cantidad=Count('id')
    )
    
    docs_por_estado = {}
    for estado in estados_emitidos:
        docs_por_estado[estado['estado_dte']] = estado['cantidad']
    
    # Pagos realizados
    pagos_realizados = Dte_Detalle_Pago.objects.filter(
        dte__fecha_emision__range=[fecha_inicio, fecha_fin]
    ).aggregate(
        total=Sum('monto'),
        cantidad=Count('id')
    )
    
    return {
        'total_dtes_emitidos': dtes_emitidos.count(),
        'total_dtes_recibidos': dtes_recibidos.count(),
        'monto_total_emitidos': float(monto_total_emitidos),
        'monto_total_recibidos': float(monto_total_recibidos),
        'documentos_por_tipo': docs_por_tipo,
        'documentos_por_estado': docs_por_estado,
        'pagos_realizados': float(pagos_realizados['total'] or 0),
        'cantidad_pagos': pagos_realizados['cantidad'] or 0
    }


def calcular_metricas_inventario(sucursal_id=None):
    """Calcula métricas relacionadas con inventario"""
    
    # Query base de productos
    productos_query = Producto_Talla.objects.all()
    
    if sucursal_id:
        productos_query = productos_query.filter(sucursal_id=sucursal_id)
    
    # Total de SKUs
    total_skus = productos_query.count()
    
    # SKUs con stock
    skus_con_stock = productos_query.filter(stock__gt=0).count()
    
    # SKUs sin stock
    skus_sin_stock = productos_query.filter(stock=0).count()
    
    # Stock total
    stock_total = productos_query.aggregate(
        total=Sum('stock')
    )['total'] or 0
    
    # Valor total del inventario (precio de venta)
    valor_inventario_venta = productos_query.aggregate(
        total=Sum(F('precioventa') * F('stock'))
    )['total'] or 0
    
    # Obtener valor FIFO (costo real)
    lotes = LoteProducto.objects.filter(
        cantidad_restante__gt=0
    )
    
    if sucursal_id:
        lotes = lotes.filter(sucursal_id=sucursal_id)
    
    valor_inventario_costo_fifo = lotes.aggregate(
        total=Sum(F('costo_unitario') * F('cantidad_restante'))
    )['total'] or 0
    
    # Margen potencial
    margen_potencial = float(valor_inventario_venta) - float(valor_inventario_costo_fifo or 0)
    margen_porcentual = 0
    if valor_inventario_costo_fifo > 0:
        margen_porcentual = (margen_potencial / float(valor_inventario_costo_fifo)) * 100
    
    # Stock crítico (menos de 10 unidades)
    stock_critico = productos_query.filter(stock__lt=10, stock__gt=0).count()
    
    # Productos base únicos
    productos_base_unicos = productos_query.values('producto').distinct().count()
    
    # Stock promedio por SKU
    stock_promedio = 0
    if total_skus > 0:
        stock_promedio = int(stock_total) / total_skus
    
    return {
        'total_skus': total_skus,
        'skus_con_stock': skus_con_stock,
        'skus_sin_stock': skus_sin_stock,
        'stock_total': int(stock_total),
        'valor_inventario_venta': float(valor_inventario_venta),
        'valor_inventario_costo': float(valor_inventario_costo_fifo or 0),
        'margen_potencial': round(margen_potencial, 2),
        'margen_porcentual': round(margen_porcentual, 2),
        'stock_critico': stock_critico,
        'productos_base_unicos': productos_base_unicos,
        'stock_promedio_sku': round(stock_promedio, 2)
    }


def calcular_analisis_financiero(fecha_inicio, fecha_fin, metricas_compras, metricas_ventas):
    """Calcula análisis financiero combinando compras y ventas"""
    
    # Margen bruto
    ingresos = metricas_ventas['total_ingresos']
    costo_ventas = metricas_compras['inversion_total']
    margen_bruto = ingresos - costo_ventas
    margen_bruto_porcentaje = 0
    if costo_ventas > 0:
        margen_bruto_porcentaje = (margen_bruto / costo_ventas) * 100
    
    # ROI
    roi = 0
    if metricas_compras['inversion_total'] > 0:
        roi = ((metricas_ventas['total_ingresos'] - metricas_compras['inversion_total']) / 
               metricas_compras['inversion_total']) * 100
    
    # Relación ventas/compras
    relacion_ventas_compras = 0
    if metricas_compras['inversion_total'] > 0:
        relacion_ventas_compras = metricas_ventas['total_ingresos'] / metricas_compras['inversion_total']
    
    # Días promedio de cobranza (estimado basado en documentos)
    dias_cobranza_estimado = 30  # Placeholder - se puede calcular con fechas de pago
    
    return {
        'margen_bruto': round(margen_bruto, 2),
        'margen_bruto_porcentaje': round(margen_bruto_porcentaje, 2),
        'roi': round(roi, 2),
        'relacion_ventas_compras': round(relacion_ventas_compras, 2),
        'dias_cobranza_estimado': dias_cobranza_estimado,
        'ingresos_totales': metricas_ventas['total_ingresos'],
        'costos_totales': metricas_compras['inversion_total']
    }


def calcular_analisis_eficiencia(metricas_compras, metricas_ventas, metricas_inventario):
    """Calcula indicadores de eficiencia operativa"""
    
    # Rotación de inventario
    rotacion_inventario = 0
    if metricas_inventario['stock_total'] > 0:
        rotacion_inventario = metricas_ventas['total_unidades_vendidas'] / metricas_inventario['stock_total']
    
    # Días de inventario
    dias_inventario = 0
    if rotacion_inventario > 0:
        dias_inventario = 30 / rotacion_inventario  # Asumiendo período de 30 días
    
    # Eficiencia de compras (cumplimiento)
    eficiencia_compras = metricas_compras['cumplimiento_porcentaje']
    
    # Tasa de conversión stock a ventas
    tasa_conversion = 0
    if metricas_compras['total_unidades_recepcionadas'] > 0:
        tasa_conversion = (metricas_ventas['total_unidades_vendidas'] / 
                          metricas_compras['total_unidades_recepcionadas']) * 100
    
    # Cobertura de stock (días)
    cobertura_stock = dias_inventario
    
    return {
        'rotacion_inventario': round(rotacion_inventario, 2),
        'dias_inventario': round(dias_inventario, 1),
        'eficiencia_compras': round(eficiencia_compras, 2),
        'tasa_conversion_stock': round(tasa_conversion, 2),
        'cobertura_stock_dias': round(cobertura_stock, 1)
    }


# ========== FUNCIONES DE GRÁFICOS ==========

def obtener_ventas_vs_compras_mensual(fecha_inicio, fecha_fin):
    """Obtiene comparativa mensual de ventas vs compras"""
    
    # Compras por mes
    compras_mensuales = Compras.objects.filter(
        fecha__range=[fecha_inicio, fecha_fin]
    ).annotate(
        mes=TruncMonth('fecha')
    ).values('mes').annotate(
        total=Sum(F('compras_producto__costo') * F('compras_producto__compras_producto_talla__stock'))
    ).order_by('mes')
    
    # Ventas por mes
    ventas_mensuales = Ticket.objects.filter(
        fecha_creacion__range=[fecha_inicio, fecha_fin],
        estado__in=['completado', 'pagado']
    ).annotate(
        mes=TruncMonth('fecha_creacion')
    ).values('mes').annotate(
        total=Sum('total')
    ).order_by('mes')
    
    # Combinar datos
    meses = {}
    for compra in compras_mensuales:
        mes_str = compra['mes'].strftime('%Y-%m')
        meses[mes_str] = {'compras': float(compra['total'] or 0), 'ventas': 0}
    
    for venta in ventas_mensuales:
        mes_str = venta['mes'].strftime('%Y-%m')
        if mes_str not in meses:
            meses[mes_str] = {'compras': 0, 'ventas': 0}
        meses[mes_str]['ventas'] = float(venta['total'] or 0)
    
    return {
        'labels': list(meses.keys()),
        'compras': [meses[m]['compras'] for m in meses.keys()],
        'ventas': [meses[m]['ventas'] for m in meses.keys()]
    }


def obtener_documentos_por_tipo(fecha_inicio, fecha_fin):
    """Obtiene distribución de documentos por tipo"""
    
    tipos_documento = {
        '33': 'Factura Electrónica',
        '34': 'Factura Exenta',
        '39': 'Boleta Electrónica',
        '52': 'Guía de Despacho',
        '56': 'Nota de Débito',
        '61': 'Nota de Crédito'
    }
    
    docs = Dte.objects.filter(
        fecha_emision__range=[fecha_inicio, fecha_fin]
    ).values('tipo_documento').annotate(
        cantidad=Count('id')
    )
    
    resultado = {
        'labels': [],
        'valores': []
    }
    
    for doc in docs:
        tipo = doc['tipo_documento']
        resultado['labels'].append(tipos_documento.get(tipo, f'Tipo {tipo}'))
        resultado['valores'].append(doc['cantidad'])
    
    return resultado


def obtener_top_productos_vendidos(fecha_inicio, fecha_fin, limit=10):
    """Obtiene los productos más vendidos"""
    
    top_productos = Ticket_Productos.objects.filter(
        idTicket__fecha__range=[fecha_inicio, fecha_fin],
        idTicket__estado='PAGADO'
    ).values(
        'producto_talla__producto__articulo',
        'producto_talla__producto__id'
    ).annotate(
        total_vendido=Sum('cantidad'),
        ingresos=Sum(F('cantidad') * F('precio_unitario'))
    ).order_by('-total_vendido')[:limit]
    
    return {
        'labels': [p['producto_talla__producto__articulo'] for p in top_productos],
        'unidades': [int(p['total_vendido']) for p in top_productos],
        'ingresos': [float(p['ingresos']) for p in top_productos]
    }


def obtener_top_productos_comprados(fecha_inicio, fecha_fin, limit=10):
    """Obtiene los productos más comprados"""
    
    top_productos = Compras_Producto_Talla.objects.filter(
        compra_producto__compras__fecha__range=[fecha_inicio, fecha_fin]
    ).values(
        'compra_producto__producto__articulo',
        'compra_producto__producto__id'
    ).annotate(
        total_comprado=Sum('stock'),
        inversion=Sum(F('compra_producto__costo') * F('stock'))
    ).order_by('-total_comprado')[:limit]
    
    return {
        'labels': [p['compra_producto__producto__articulo'] for p in top_productos],
        'unidades': [int(p['total_comprado']) for p in top_productos],
        'inversion': [float(p['inversion']) for p in top_productos]
    }


def obtener_cumplimiento_proveedores(fecha_inicio, fecha_fin):
    """Obtiene cumplimiento de proveedores"""
    
    proveedores = Compras.objects.filter(
        fecha__range=[fecha_inicio, fecha_fin]
    ).values('empresa__nombre', 'empresa__id').annotate(
        total_compras=Count('id')
    ).order_by('-total_compras')[:10]
    
    resultado = []
    for proveedor in proveedores:
        compras_ids = Compras.objects.filter(
            empresa_id=proveedor['empresa__id'],
            fecha__range=[fecha_inicio, fecha_fin]
        ).values_list('id', flat=True)
        
        esperadas = Compras_Producto_Talla.objects.filter(
            compra_producto__compras__in=compras_ids
        ).aggregate(total=Sum('stock'))['total'] or 0
        
        recibidas = Productos_Recepcionados.objects.filter(
            compra_producto_talla__compra_producto__compras__in=compras_ids
        ).aggregate(total=Sum('stockArribado'))['total'] or 0
        
        cumplimiento = 0
        if esperadas > 0:
            cumplimiento = (recibidas / esperadas) * 100
        
        resultado.append({
            'nombre': proveedor['empresa__nombre'],
            'cumplimiento': round(cumplimiento, 1),
            'compras': proveedor['total_compras']
        })
    
    return {
        'labels': [p['nombre'] for p in resultado],
        'cumplimiento': [p['cumplimiento'] for p in resultado],
        'compras': [p['compras'] for p in resultado]
    }


def obtener_estado_stock_critico():
    """Obtiene productos con stock crítico"""
    
    stock_critico = Producto_Talla.objects.filter(
        stock__lt=10,
        stock__gt=0
    ).select_related('producto').order_by('stock')[:20]
    
    return {
        'labels': [f"{pt.producto.articulo} - {pt.talla}" for pt in stock_critico],
        'stock': [pt.stock for pt in stock_critico]
    }


def obtener_flujo_caja_proyectado(fecha_inicio, fecha_fin):
    """Calcula flujo de caja proyectado"""
    
    # Ventas diarias
    ventas_diarias = Ticket.objects.filter(
        fecha_creacion__range=[fecha_inicio, fecha_fin],
        estado__in=['completado', 'pagado']
    ).annotate(
        dia=TruncDay('fecha_creacion')
    ).values('dia').annotate(
        total=Sum('total')
    ).order_by('dia')
    
    # Compras diarias (pagos)
    pagos_compras = Dte_Detalle_Pago.objects.filter(
        fecha_pago__range=[fecha_inicio, fecha_fin],
        dte__tipo_transaccion='recibido'
    ).annotate(
        dia=TruncDay('fecha_pago')
    ).values('dia').annotate(
        total=Sum('monto')
    ).order_by('dia')
    
    # Combinar datos
    dias = {}
    for venta in ventas_diarias:
        dia_str = venta['dia'].strftime('%Y-%m-%d')
        dias[dia_str] = {'ingresos': float(venta['total'] or 0), 'egresos': 0}
    
    for pago in pagos_compras:
        dia_str = pago['dia'].strftime('%Y-%m-%d')
        if dia_str not in dias:
            dias[dia_str] = {'ingresos': 0, 'egresos': 0}
        dias[dia_str]['egresos'] = float(pago['total'] or 0)
    
    # Calcular flujo acumulado
    flujo_acumulado = 0
    resultado = []
    for dia in sorted(dias.keys()):
        flujo_dia = dias[dia]['ingresos'] - dias[dia]['egresos']
        flujo_acumulado += flujo_dia
        resultado.append({
            'fecha': dia,
            'ingresos': dias[dia]['ingresos'],
            'egresos': dias[dia]['egresos'],
            'flujo': flujo_dia,
            'acumulado': flujo_acumulado
        })
    
    return resultado


def obtener_rentabilidad_por_categoria(fecha_inicio, fecha_fin):
    """Obtiene rentabilidad por categoría de producto"""
    
    # Ventas por categoría
    ventas_categoria = Ticket_Productos.objects.filter(
        idTicket__fecha__range=[fecha_inicio, fecha_fin],
        idTicket__estado='PAGADO'
    ).values(
        'producto_talla__producto__categoria__nombre'
    ).annotate(
        ingresos=Sum(F('cantidad') * F('precio_unitario')),
        unidades=Sum('cantidad')
    )
    
    resultado = {}
    for venta in ventas_categoria:
        categoria = venta['producto_talla__producto__categoria__nombre'] or 'Sin Categoría'
        resultado[categoria] = {
            'ingresos': float(venta['ingresos'] or 0),
            'unidades': int(venta['unidades'] or 0)
        }
    
    return {
        'labels': list(resultado.keys()),
        'ingresos': [resultado[c]['ingresos'] for c in resultado.keys()],
        'unidades': [resultado[c]['unidades'] for c in resultado.keys()]
    }


# ========== SISTEMA DE ALERTAS INTELIGENTES ==========

def generar_alertas_inteligentes(metricas_inventario, metricas_compras, metricas_ventas):
    """Genera alertas y recomendaciones basadas en las métricas"""
    
    alertas = []
    
    # Alerta de stock crítico
    if metricas_inventario['stock_critico'] > 10:
        alertas.append({
            'tipo': 'warning',
            'categoria': 'inventario',
            'titulo': 'Stock Crítico',
            'mensaje': f'{metricas_inventario["stock_critico"]} productos tienen stock menor a 10 unidades',
            'prioridad': 'alta'
        })
    
    # Alerta de rotación baja
    rotacion = metricas_ventas['total_unidades_vendidas'] / max(metricas_inventario['stock_total'], 1)
    if rotacion < 0.1:  # Menos del 10% de rotación en el período
        alertas.append({
            'tipo': 'danger',
            'categoria': 'eficiencia',
            'titulo': 'Baja Rotación de Inventario',
            'mensaje': 'La rotación de inventario es inferior al 10%. Considere estrategias promocionales.',
            'prioridad': 'alta'
        })
    
    # Alerta de cumplimiento de proveedores
    if metricas_compras['cumplimiento_porcentaje'] < 80:
        alertas.append({
            'tipo': 'warning',
            'categoria': 'compras',
            'titulo': 'Bajo Cumplimiento de Proveedores',
            'mensaje': f'El cumplimiento promedio es de {metricas_compras["cumplimiento_porcentaje"]}%. Revise acuerdos con proveedores.',
            'prioridad': 'media'
        })
    
    # Alerta de margen bajo
    if metricas_inventario['margen_porcentual'] < 20:
        alertas.append({
            'tipo': 'danger',
            'categoria': 'financiero',
            'titulo': 'Margen de Ganancia Bajo',
            'mensaje': f'El margen potencial es de {metricas_inventario["margen_porcentual"]:.1f}%. Revise estrategia de precios.',
            'prioridad': 'alta'
        })
    
    # Alerta de stock sin movimiento
    if metricas_inventario['skus_sin_stock'] > metricas_inventario['skus_con_stock'] * 0.3:
        alertas.append({
            'tipo': 'info',
            'categoria': 'inventario',
            'titulo': 'Muchos SKUs sin Stock',
            'mensaje': f'{metricas_inventario["skus_sin_stock"]} SKUs están agotados. Considere reabastecimiento.',
            'prioridad': 'media'
        })
    
    # Recomendación de compra
    if metricas_ventas['total_unidades_vendidas'] > metricas_compras['total_unidades_recepcionadas']:
        alertas.append({
            'tipo': 'success',
            'categoria': 'recomendacion',
            'titulo': 'Oportunidad de Compra',
            'mensaje': 'Las ventas superan las compras. Buen momento para reabastecer productos populares.',
            'prioridad': 'baja'
        })
    
    return alertas


# ========== EXPORTACIÓN DE DATOS ==========

@login_required
@require_GET
def exportar_dashboard_integral_excel(request):
    """Exporta todos los datos del dashboard a Excel"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # Obtener todas las métricas
        metricas_response = obtener_metricas_dashboard_integral(request)
        datos = json.loads(metricas_response.content)
        
        if not datos.get('success'):
            return JsonResponse({'success': False, 'error': 'Error al obtener datos'})
        
        # Crear workbook
        wb = Workbook()
        
        # Hoja 1: Resumen General
        ws_resumen = wb.active
        ws_resumen.title = "Resumen General"
        
        # Encabezados
        ws_resumen['A1'] = 'DASHBOARD INTEGRAL - RETAILMIND'
        ws_resumen['A1'].font = Font(bold=True, size=14)
        ws_resumen['A2'] = f"Período: {datos['periodo']['fecha_inicio']} a {datos['periodo']['fecha_fin']}"
        
        # Métricas de compras
        row = 4
        ws_resumen[f'A{row}'] = 'MÉTRICAS DE COMPRAS'
        ws_resumen[f'A{row}'].font = Font(bold=True)
        row += 1
        
        for key, value in datos['metricas']['compras'].items():
            ws_resumen[f'A{row}'] = key.replace('_', ' ').title()
            ws_resumen[f'B{row}'] = value
            row += 1
        
        # Métricas de ventas
        row += 1
        ws_resumen[f'A{row}'] = 'MÉTRICAS DE VENTAS'
        ws_resumen[f'A{row}'].font = Font(bold=True)
        row += 1
        
        for key, value in datos['metricas']['ventas'].items():
            if key != 'metodos_pago':
                ws_resumen[f'A{row}'] = key.replace('_', ' ').title()
                ws_resumen[f'B{row}'] = value
                row += 1
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="dashboard_integral.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        }, status=500)

