"""
Dashboard Home - RetailMind
Vista principal con KPIs de retail para seguimiento de sucursal
"""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum, Count, Q, Avg, F, Min, Max
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .models import (
    Ticket, Ticket_Productos, Dte, Dte_Productos, Producto, Producto_Talla,
    Sucursal, EmpresaUser, Empresa, Compras, Compras_Producto, Compras_Producto_Talla,
    Productos_Recepcionados, Requerimiento, Movimientos_Producto, LoteProducto,
    CambioDevolucion, Solicitud_Regularizacion, Traspaso, AjusteInventario
)


@login_required
def dashboard_home(request):
    """
    Dashboard Home con KPIs de Retail
    Métricas principales para seguimiento de sucursal
    """
    try:
        # Obtener sucursal y empresa actual
        sucursal_id = request.session.get('idSucursalActual')
        empresa_id = request.session.get('idEmpresaActual')
        
        # Obtener información de la sucursal
        sucursal_actual = None
        if sucursal_id:
            sucursal_actual = Sucursal.objects.filter(id=sucursal_id).first()
        
        # Fechas para filtros
        hoy = timezone.now().date()
        inicio_semana = hoy - timedelta(days=hoy.weekday())  # Lunes de esta semana
        inicio_mes = hoy.replace(day=1)
        mes_pasado_inicio = (inicio_mes - timedelta(days=1)).replace(day=1)
        mes_pasado_fin = inicio_mes - timedelta(days=1)
        
        # ========== 1. KPIs DE VENTAS ==========
        ventas_data = calcular_kpis_ventas(sucursal_id, hoy, inicio_semana, inicio_mes, mes_pasado_inicio, mes_pasado_fin)
        
        # ========== 2. KPIs DE STOCK/EXISTENCIAS ==========
        stock_data = calcular_kpis_stock(sucursal_id, empresa_id)
        
        # ========== 3. KPIs DE COMPRAS ==========
        compras_data = calcular_kpis_compras(sucursal_id, empresa_id, hoy, inicio_mes)
        
        # ========== 4. KPIs DE REQUERIMIENTOS ==========
        requerimientos_data = calcular_kpis_requerimientos(sucursal_id, hoy, inicio_mes)
        
        # ========== 5. KPIs OPERACIONALES ==========
        operaciones_data = calcular_kpis_operaciones(sucursal_id, empresa_id, hoy, inicio_mes)
        
        # ========== 6. ALERTAS CRÍTICAS ==========
        alertas = generar_alertas_criticas(stock_data, compras_data, requerimientos_data, operaciones_data)
        
        # ========== 7. TOP PRODUCTOS ==========
        top_productos = obtener_top_productos(sucursal_id, inicio_mes, hoy)
        
        # ========== 8. PRODUCTOS SIN MOVIMIENTO ==========
        productos_sin_movimiento = obtener_productos_sin_movimiento(sucursal_id, 30)
        
        context = {
            # Información de la sucursal
            'sucursal_actual': sucursal_actual,
            'fecha_actual': hoy,
            
            # KPIs principales
            'ventas': ventas_data,
            'stock': stock_data,
            'compras': compras_data,
            'requerimientos': requerimientos_data,
            'operaciones': operaciones_data,
            
            # Alertas y listas
            'alertas': alertas,
            'top_productos': top_productos,
            'productos_sin_movimiento': productos_sin_movimiento,
        }
        
        return render(request, 'vistas/dashboard_home.html', context)
        
    except Exception as e:
        import traceback
        print(f"Error en dashboard_home: {str(e)}")
        traceback.print_exc()
        
        # Retornar contexto mínimo en caso de error
        return render(request, 'vistas/dashboard_home.html', {
            'error': str(e),
            'ventas': {'hoy': 0, 'semana': 0, 'mes': 0, 'unidades_hoy': 0, 'ticket_promedio': 0},
            'stock': {'total_skus': 0, 'stock_critico': 0, 'sin_stock': 0, 'valor_inventario': 0},
            'compras': {'pendientes_recepcion': 0, 'dtes_pendientes': 0, 'monto_pendiente': 0},
            'requerimientos': {'pendientes': 0, 'en_proceso': 0, 'total_mes': 0},
            'operaciones': {'traspasos_pendientes': 0, 'ajustes_pendientes': 0},
            'alertas': [],
            'top_productos': [],
            'productos_sin_movimiento': [],
            'fecha_actual': timezone.now().date(),
        })


def calcular_kpis_ventas(sucursal_id, hoy, inicio_semana, inicio_mes, mes_pasado_inicio, mes_pasado_fin):
    """Calcula KPIs de ventas"""
    # Base queryset de tickets
    tickets_base = Ticket.objects.filter(estado='PAGADO')
    if sucursal_id:
        tickets_base = tickets_base.filter(sucursal_id=sucursal_id)
    
    # Ventas HOY
    ventas_hoy_query = tickets_base.filter(fecha=hoy)
    ventas_hoy = ventas_hoy_query.aggregate(total=Sum('total'))['total'] or 0
    tickets_hoy = ventas_hoy_query.count()
    
    # Unidades vendidas hoy
    unidades_hoy = Ticket_Productos.objects.filter(
        idTicket__in=ventas_hoy_query
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # Ventas SEMANA
    ventas_semana = tickets_base.filter(
        fecha__gte=inicio_semana,
        fecha__lte=hoy
    ).aggregate(total=Sum('total'))['total'] or 0
    
    # Ventas MES
    ventas_mes_query = tickets_base.filter(fecha__gte=inicio_mes, fecha__lte=hoy)
    ventas_mes = ventas_mes_query.aggregate(total=Sum('total'))['total'] or 0
    tickets_mes = ventas_mes_query.count()
    
    # Ventas MES PASADO (para comparar)
    ventas_mes_pasado = tickets_base.filter(
        fecha__gte=mes_pasado_inicio,
        fecha__lte=mes_pasado_fin
    ).aggregate(total=Sum('total'))['total'] or 0
    
    # Calcular variación porcentual
    if ventas_mes_pasado > 0:
        variacion_mes = round(((ventas_mes - ventas_mes_pasado) / ventas_mes_pasado) * 100, 1)
    else:
        variacion_mes = 100 if ventas_mes > 0 else 0
    
    # Ticket promedio
    ticket_promedio = round(ventas_hoy / tickets_hoy, 0) if tickets_hoy > 0 else 0
    ticket_promedio_mes = round(ventas_mes / tickets_mes, 0) if tickets_mes > 0 else 0
    
    # Ventas por hora (últimas 24 horas para gráfico)
    ventas_por_hora = []
    for i in range(24):
        hora_inicio = timezone.now().replace(hour=i, minute=0, second=0, microsecond=0) - timedelta(days=1)
        hora_fin = hora_inicio + timedelta(hours=1)
        venta_hora = tickets_base.filter(
            fecha=hoy,
            hora__gte=hora_inicio.time(),
            hora__lt=hora_fin.time()
        ).aggregate(total=Sum('total'))['total'] or 0
        ventas_por_hora.append({'hora': i, 'monto': int(venta_hora)})
    
    return {
        'hoy': int(ventas_hoy),
        'semana': int(ventas_semana),
        'mes': int(ventas_mes),
        'mes_pasado': int(ventas_mes_pasado),
        'variacion_mes': variacion_mes,
        'tickets_hoy': tickets_hoy,
        'tickets_mes': tickets_mes,
        'unidades_hoy': unidades_hoy,
        'ticket_promedio': int(ticket_promedio),
        'ticket_promedio_mes': int(ticket_promedio_mes),
        'ventas_por_hora': ventas_por_hora,
        'tendencia': 'up' if variacion_mes > 0 else ('down' if variacion_mes < 0 else 'stable'),
    }


def calcular_kpis_stock(sucursal_id, empresa_id):
    """
    Calcula KPIs de stock/inventario
    CORREGIDO: Usa Producto_Talla.stock directamente para reflejar los 100,000+ productos
    """
    # Base query para productos con talla
    productos_talla_query = Producto_Talla.objects.select_related(
        'producto', 
        'producto__sucursal',
        'producto__atributo1'
    )
    
    # Filtrar por sucursal si está especificada
    if sucursal_id:
        productos_talla_query = productos_talla_query.filter(producto__sucursal_id=sucursal_id)
    
    # Contar total de SKUs
    total_skus = productos_talla_query.count()
    
    # Productos con stock positivo
    productos_con_stock = productos_talla_query.filter(stock__gt=0)
    skus_con_stock = productos_con_stock.count()
    
    # Productos sin stock (stock = 0 o NULL)
    sin_stock_count = productos_talla_query.filter(Q(stock=0) | Q(stock__isnull=True)).count()
    
    # Productos con stock crítico (1-5 unidades)
    productos_criticos_query = productos_talla_query.filter(stock__gt=0, stock__lte=5).select_related(
        'producto'
    ).order_by('stock')[:10]
    
    productos_criticos = []
    for pt in productos_criticos_query:
        productos_criticos.append({
            'producto': pt.producto.articulo[:30] if pt.producto.articulo else pt.sku,
            'talla': pt.talla,
            'stock': pt.stock,
            'sku': str(pt.sku)
        })
    
    stock_critico_count = productos_talla_query.filter(stock__gt=0, stock__lte=5).count()
    
    # Calcular totales de stock y valor
    # Usamos agregación directa sobre Producto_Talla con stock > 0
    totales = productos_con_stock.annotate(
        valor_unitario=F('producto__costo')
    ).aggregate(
        total_unidades=Sum('stock'),
        valor_total=Sum(F('stock') * F('producto__costo'))
    )
    
    total_stock = totales['total_unidades'] or 0
    valor_inventario_total = totales['valor_total'] or 0
    
    # Rotación de inventario (simplificada)
    # Ventas del mes / Inventario promedio
    inicio_mes = timezone.now().date().replace(day=1)
    ventas_mes_query = Ticket_Productos.objects.filter(
        idTicket__fecha__gte=inicio_mes,
        idTicket__estado='PAGADO'
    )
    if sucursal_id:
        ventas_mes_query = ventas_mes_query.filter(idTicket__sucursal_id=sucursal_id)
    
    total_vendido = ventas_mes_query.aggregate(total=Sum('stock'))['total'] or 0
    rotacion = round((total_vendido / total_stock) * 100, 1) if total_stock > 0 else 0
    
    # Log para debug
    print(f"📊 KPIs Stock - Sucursal: {sucursal_id}")
    print(f"   Total SKUs: {total_skus:,}")
    print(f"   Con Stock: {skus_con_stock:,}")
    print(f"   Sin Stock: {sin_stock_count:,}")
    print(f"   Crítico: {stock_critico_count:,}")
    print(f"   Total Unidades: {total_stock:,}")
    print(f"   Valor Inventario: ${valor_inventario_total:,.0f}")
    
    return {
        'total_skus': total_skus,
        'skus_con_stock': skus_con_stock,
        'stock_critico': stock_critico_count,
        'sin_stock': sin_stock_count,
        'valor_inventario': int(valor_inventario_total),
        'productos_criticos': productos_criticos,
        'total_unidades': total_stock,
        'rotacion_mes': rotacion,
    }


def calcular_kpis_compras(sucursal_id, empresa_id, hoy, inicio_mes):
    """Calcula KPIs de compras"""
    # DTEs de compra pendientes de recepcionar
    dtes_pendientes = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        estado_dte='EMITIDO'
    )
    if empresa_id:
        dtes_pendientes = dtes_pendientes.filter(receptor_id=empresa_id)
    
    total_dtes_pendientes = dtes_pendientes.count()
    monto_pendiente = dtes_pendientes.aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Productos comprados pendientes de recepcionar
    productos_pendientes = Compras_Producto_Talla.objects.filter(
        compra_producto__compras__fecha__gte=inicio_mes
    ).exclude(
        id__in=Productos_Recepcionados.objects.values('compra_producto_talla_id')
    ).count()
    
    # Compras del mes
    compras_mes = Compras.objects.filter(fecha__gte=inicio_mes)
    if empresa_id:
        compras_mes = compras_mes.filter(empresa_id=empresa_id)
    
    total_compras_mes = compras_mes.count()
    
    # Valor de compras del mes (desde DTEs)
    valor_compras_mes = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        fecha_emision__gte=inicio_mes
    )
    if empresa_id:
        valor_compras_mes = valor_compras_mes.filter(receptor_id=empresa_id)
    
    monto_compras_mes = valor_compras_mes.aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Lista de DTEs pendientes más antiguos
    lista_dtes_pendientes = []
    for dte in dtes_pendientes.order_by('fecha_emision')[:5]:
        dias_pendiente = (hoy - dte.fecha_emision).days if dte.fecha_emision else 0
        lista_dtes_pendientes.append({
            'id': dte.id,
            'numero': dte.numero_documento,
            'tipo': dte.tipo_documento,
            'emisor': dte.emisor.nombre if dte.emisor else 'N/A',
            'monto': int(dte.monto_con_iva),
            'fecha': dte.fecha_emision.strftime('%d/%m') if dte.fecha_emision else '',
            'dias': dias_pendiente,
            'urgente': dias_pendiente > 7
        })
    
    return {
        'dtes_pendientes': total_dtes_pendientes,
        'monto_pendiente': int(monto_pendiente),
        'productos_pendientes': productos_pendientes,
        'compras_mes': total_compras_mes,
        'monto_compras_mes': int(monto_compras_mes),
        'lista_pendientes': lista_dtes_pendientes,
    }


def calcular_kpis_requerimientos(sucursal_id, hoy, inicio_mes):
    """Calcula KPIs de requerimientos"""
    requerimientos_base = Requerimiento.objects.all()
    if sucursal_id:
        requerimientos_base = requerimientos_base.filter(sucursal_id=sucursal_id)
    
    pendientes = requerimientos_base.filter(estado='PENDIENTE').count()
    esperando = requerimientos_base.filter(estado='ESPERANDO_RESPUESTA').count()
    aprobados = requerimientos_base.filter(estado='APROBADO').count()
    rechazados = requerimientos_base.filter(estado='RECHAZADO').count()
    
    total_mes = requerimientos_base.filter(fecha_creacion__gte=inicio_mes).count()
    
    # Tiempo promedio de resolución (para los resueltos este mes)
    resueltos_mes = requerimientos_base.filter(
        estado__in=['APROBADO', 'RECHAZADO'],
        fecha_creacion__gte=inicio_mes
    )
    
    # Requerimientos por tipo
    por_tipo = requerimientos_base.filter(
        fecha_creacion__gte=inicio_mes
    ).values('tipo').annotate(cantidad=Count('id')).order_by('-cantidad')[:5]
    
    # Antigüedad promedio de pendientes
    pendientes_query = requerimientos_base.filter(estado='PENDIENTE')
    dias_promedio = 0
    if pendientes_query.exists():
        total_dias = sum((hoy - r.fecha_creacion.date()).days for r in pendientes_query)
        dias_promedio = total_dias // pendientes_query.count()
    
    return {
        'pendientes': pendientes,
        'esperando': esperando,
        'aprobados': aprobados,
        'rechazados': rechazados,
        'total': requerimientos_base.count(),
        'total_mes': total_mes,
        'dias_promedio': dias_promedio,
        'por_tipo': list(por_tipo),
        'tasa_aprobacion': round((aprobados / (aprobados + rechazados) * 100), 1) if (aprobados + rechazados) > 0 else 0,
    }


def calcular_kpis_operaciones(sucursal_id, empresa_id, hoy, inicio_mes):
    """Calcula KPIs operacionales"""
    # Traspasos pendientes
    traspasos_pendientes = Traspaso.objects.filter(
        estado__in=['SOLICITADO', 'EN_TRANSITO']
    )
    if sucursal_id:
        traspasos_pendientes = traspasos_pendientes.filter(
            Q(sucursal_origen_id=sucursal_id) | Q(sucursal_destino_id=sucursal_id)
        )
    
    # Ajustes de inventario pendientes
    ajustes_pendientes = AjusteInventario.objects.filter(estado='PENDIENTE')
    if sucursal_id:
        ajustes_pendientes = ajustes_pendientes.filter(sucursal_id=sucursal_id)
    
    # Cambios y devoluciones pendientes
    cambios_pendientes = CambioDevolucion.objects.filter(
        estado__in=['SOLICITADO', 'EN_PROCESO', 'APROBADO', 'EJECUTADO_COBRO_PENDIENTE']
    )
    if sucursal_id:
        cambios_pendientes = cambios_pendientes.filter(sucursal_id=sucursal_id)
    
    # DTEs pendientes de pago
    dtes_pago_pendiente = Dte.objects.filter(
        estado_pago='PENDIENTE',
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
    )
    if sucursal_id:
        dtes_pago_pendiente = dtes_pago_pendiente.filter(sucursal_id=sucursal_id)
    
    monto_por_cobrar = dtes_pago_pendiente.aggregate(total=Sum('monto_con_iva'))['total'] or 0
    
    # Regularizaciones pendientes
    regularizaciones = Solicitud_Regularizacion.objects.filter(estado='PENDIENTE')
    if sucursal_id:
        regularizaciones = regularizaciones.filter(
            Q(sucursal_solicitante_id=sucursal_id) | Q(sucursal_emisora_id=sucursal_id)
        )
    
    return {
        'traspasos_pendientes': traspasos_pendientes.count(),
        'ajustes_pendientes': ajustes_pendientes.count(),
        'cambios_pendientes': cambios_pendientes.count(),
        'dtes_por_cobrar': dtes_pago_pendiente.count(),
        'monto_por_cobrar': int(monto_por_cobrar),
        'regularizaciones': regularizaciones.count(),
    }


def generar_alertas_criticas(stock_data, compras_data, requerimientos_data, operaciones_data):
    """Genera lista de alertas críticas ordenadas por prioridad"""
    alertas = []
    
    # Alerta de stock crítico
    if stock_data['sin_stock'] > 0:
        alertas.append({
            'tipo': 'danger',
            'icono': 'ri-error-warning-fill',
            'titulo': f"{stock_data['sin_stock']} productos sin stock",
            'descripcion': 'Requiere atención inmediata para evitar quiebres de venta',
            'accion': 'Ver productos',
            'url': '/app/existencias/resumen/',
            'prioridad': 1
        })
    
    if stock_data['stock_critico'] > 5:
        alertas.append({
            'tipo': 'warning',
            'icono': 'ri-alert-fill',
            'titulo': f"{stock_data['stock_critico']} productos con stock crítico",
            'descripcion': 'Productos con menos de 5 unidades disponibles',
            'accion': 'Revisar stock',
            'url': '/app/existencias/resumen/',
            'prioridad': 2
        })
    
    # Alerta de compras pendientes
    if compras_data['dtes_pendientes'] > 0:
        alertas.append({
            'tipo': 'info',
            'icono': 'ri-inbox-archive-fill',
            'titulo': f"{compras_data['dtes_pendientes']} documentos por recepcionar",
            'descripcion': f"Monto total: ${compras_data['monto_pendiente']:,}",
            'accion': 'Recepcionar',
            'url': '/app/recepcion-dte/',
            'prioridad': 3
        })
    
    # Alerta de requerimientos
    if requerimientos_data['pendientes'] > 3:
        alertas.append({
            'tipo': 'warning',
            'icono': 'ri-customer-service-2-fill',
            'titulo': f"{requerimientos_data['pendientes']} requerimientos pendientes",
            'descripcion': f"Antigüedad promedio: {requerimientos_data['dias_promedio']} días",
            'accion': 'Gestionar',
            'url': '/app/requerimientos/',
            'prioridad': 4
        })
    
    # Alerta de cambios/devoluciones
    if operaciones_data['cambios_pendientes'] > 0:
        alertas.append({
            'tipo': 'info',
            'icono': 'ri-exchange-fill',
            'titulo': f"{operaciones_data['cambios_pendientes']} cambios/devoluciones pendientes",
            'descripcion': 'Cambios y devoluciones que requieren acción',
            'accion': 'Ver cambios',
            'url': '/app/cambios-devoluciones/',
            'prioridad': 5
        })
    
    # Ordenar por prioridad
    alertas.sort(key=lambda x: x['prioridad'])
    
    return alertas


def obtener_top_productos(sucursal_id, inicio_mes, hoy):
    """Obtiene los productos más vendidos del mes"""
    ventas_productos = Ticket_Productos.objects.filter(
        idTicket__fecha__gte=inicio_mes,
        idTicket__fecha__lte=hoy,
        idTicket__estado='PAGADO'
    )
    
    if sucursal_id:
        ventas_productos = ventas_productos.filter(idTicket__sucursal_id=sucursal_id)
    
    top = ventas_productos.values(
        'ProductoTalla__producto__articulo',
        'ProductoTalla__producto__atributo1__valor',
        'ProductoTalla__producto__atributo2__valor',
    ).annotate(
        unidades=Sum('stock'),
        monto=Sum('subtotal')
    ).order_by('-unidades')[:10]
    
    resultado = []
    for i, item in enumerate(top, 1):
        resultado.append({
            'posicion': i,
            'producto': item['ProductoTalla__producto__articulo'] or 'N/A',
            'marca': item['ProductoTalla__producto__atributo1__valor'] or '',
            'color': item['ProductoTalla__producto__atributo2__valor'] or '',
            'unidades': item['unidades'] or 0,
            'monto': int(item['monto'] or 0)
        })
    
    return resultado


def obtener_productos_sin_movimiento(sucursal_id, dias=30):
    """Obtiene productos sin ventas en los últimos X días"""
    fecha_limite = timezone.now().date() - timedelta(days=dias)
    
    # SKUs que SÍ tuvieron ventas en el período
    skus_con_ventas = Ticket_Productos.objects.filter(
        idTicket__fecha__gte=fecha_limite,
        idTicket__estado='PAGADO'
    )
    if sucursal_id:
        skus_con_ventas = skus_con_ventas.filter(idTicket__sucursal_id=sucursal_id)
    
    skus_vendidos = set(skus_con_ventas.values_list('ProductoTalla_id', flat=True))
    
    # Productos con stock que NO se vendieron
    productos_con_stock = LoteProducto.objects.filter(
        activo=True,
        cantidad_disponible__gt=0
    ).values(
        'producto_talla_id',
        'producto_talla__producto__articulo',
        'producto_talla__talla',
    ).annotate(
        stock=Sum('cantidad_disponible')
    ).exclude(
        producto_talla_id__in=skus_vendidos
    ).order_by('-stock')[:10]
    
    resultado = []
    for item in productos_con_stock:
        resultado.append({
            'producto': item['producto_talla__producto__articulo'],
            'talla': item['producto_talla__talla'],
            'stock': item['stock'],
            'dias_sin_venta': dias
        })
    
    return resultado


# ========== API ENDPOINTS PARA DASHBOARD ==========

@login_required
def api_dashboard_ventas_tiempo_real(request):
    """API para obtener datos de ventas en tiempo real"""
    try:
        sucursal_id = request.session.get('idSucursalActual')
        hoy = timezone.now().date()
        
        # Ventas de hoy actualizadas
        tickets_hoy = Ticket.objects.filter(fecha=hoy, estado='PAGADO')
        if sucursal_id:
            tickets_hoy = tickets_hoy.filter(sucursal_id=sucursal_id)
        
        total_hoy = tickets_hoy.aggregate(total=Sum('total'))['total'] or 0
        cantidad_tickets = tickets_hoy.count()
        
        # Última venta
        ultima_venta = tickets_hoy.order_by('-hora').first()
        
        return JsonResponse({
            'success': True,
            'ventas_hoy': int(total_hoy),
            'tickets_hoy': cantidad_tickets,
            'ultima_venta': {
                'hora': ultima_venta.hora.strftime('%H:%M') if ultima_venta else None,
                'monto': int(ultima_venta.total) if ultima_venta else 0
            } if ultima_venta else None
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def api_dashboard_stock_alertas(request):
    """API para obtener alertas de stock en tiempo real"""
    try:
        sucursal_id = request.session.get('idSucursalActual')
        empresa_id = request.session.get('idEmpresaActual')
        
        stock_data = calcular_kpis_stock(sucursal_id, empresa_id)
        
        return JsonResponse({
            'success': True,
            'stock_critico': stock_data['stock_critico'],
            'sin_stock': stock_data['sin_stock'],
            'productos_criticos': stock_data['productos_criticos'][:5]
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
