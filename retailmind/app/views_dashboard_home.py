"""
Dashboard Home - RetailMind
Vista principal con KPIs de retail para seguimiento de sucursal
"""

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum, Count, Q, Avg, F, Min, Max
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import logging

from .models import (
    Ticket, Ticket_Productos, Dte, Dte_Productos, Producto, Producto_Talla,
    Sucursal, EmpresaUser, Empresa, Compras, Compras_Producto, Compras_Producto_Talla,
    Productos_Recepcionados, Requerimiento, Movimientos_Producto, LoteProducto,
    CambioDevolucion, Solicitud_Regularizacion, Traspaso, AjusteInventario,
    PermisoRol, OpcionMenu, ModuloSistema,
    ArqueoCaja, DepositoBancario, CambioPrecioPendiente,
)

logger = logging.getLogger('app')


@login_required
def bienvenida(request):
    """
    Página de bienvenida con accesos rápidos organizados por módulo y perfil.
    No requiere permisos especiales.
    """
    sucursal_id = request.session.get('idSucursalActual')
    sucursal_actual = None
    if sucursal_id:
        sucursal_actual = Sucursal.objects.filter(id=sucursal_id).first()

    rol = request.user.rol
    modulos_con_opciones = []
    total_accesos = 0

    try:
        modulos = ModuloSistema.objects.filter(activo=True).order_by('orden')

        for modulo in modulos:
            opciones_hijas = OpcionMenu.objects.filter(
                activo=True,
                modulo=modulo,
                padre__isnull=False,
                es_submenu=False,
            ).filter(
                Q(url_name__isnull=False) | Q(url_path__isnull=False)
            ).exclude(url_name='').exclude(url_path='').select_related('modulo').order_by('orden')

            items = []
            for opcion in opciones_hijas:
                if PermisoRol.tiene_permiso(request.user, opcion.codigo, 'puede_ver', sucursal_id):
                    items.append({
                        'nombre': opcion.nombre,
                        'codigo': opcion.codigo,
                        'icono': opcion.icono or 'ri-apps-line',
                        'url_name': opcion.url_name,
                        'url_path': opcion.url_path,
                    })

            if not items:
                opciones_padre = OpcionMenu.objects.filter(
                    activo=True,
                    modulo=modulo,
                    padre__isnull=True,
                    es_submenu=False,
                ).filter(
                    Q(url_name__isnull=False) | Q(url_path__isnull=False)
                ).exclude(url_name='').exclude(url_path='').select_related('modulo').order_by('orden')

                for opcion in opciones_padre:
                    if PermisoRol.tiene_permiso(request.user, opcion.codigo, 'puede_ver', sucursal_id):
                        items.append({
                            'nombre': opcion.nombre,
                            'codigo': opcion.codigo,
                            'icono': opcion.icono or 'ri-apps-line',
                            'url_name': opcion.url_name,
                            'url_path': opcion.url_path,
                        })

            if items:
                total_accesos += len(items)
                modulos_con_opciones.append({
                    'nombre': modulo.nombre,
                    'codigo': modulo.codigo,
                    'icono': modulo.icono or 'ri-apps-line',
                    'opciones': items,
                })
    except Exception:
        logger.exception("Error obteniendo opciones de bienvenida usuario_id=%s", request.user.id)

    ACCESOS_DESTACADOS_POR_ROL = {
        'administrador': [
            'dashboard_general', 'gestion_usuarios', 'gestion_permisos',
            'gestion_sucursales', 'gestion_empresas', 'pos_dashboard',
            'documentos_emitidos', 'existencias_resumen',
        ],
        'administracion': [
            'dashboard_general', 'documentos_emitidos', 'recepcion_dte',
            'existencias_resumen', 'requerimientos', 'gestion_dte',
        ],
        'jefe_local': [
            'dashboard_general', 'pos_dashboard', 'existencias_resumen',
            'requerimientos', 'cambios_devoluciones', 'revision_arqueos',
        ],
        'cajero': [
            'pos_dashboard', 'generacion_ventas', 'cambios_devoluciones',
            'arqueo_caja', 'documentos_emitidos',
        ],
        'vendedor': [
            'pos_dashboard', 'generacion_ventas', 'existencias_resumen',
            'requerimientos',
        ],
    }

    codigos_destacados = ACCESOS_DESTACADOS_POR_ROL.get(rol, [])
    accesos_destacados = []

    for modulo_data in modulos_con_opciones:
        for opcion in modulo_data['opciones']:
            if opcion['codigo'] in codigos_destacados:
                accesos_destacados.append({
                    **opcion,
                    'modulo': modulo_data['nombre'],
                })

    accesos_destacados.sort(key=lambda x: (
        codigos_destacados.index(x['codigo'])
        if x['codigo'] in codigos_destacados else 999
    ))

    context = {
        'sucursal_actual': sucursal_actual,
        'fecha_actual': timezone.localdate(),
        'modulos_con_opciones': modulos_con_opciones,
        'accesos_destacados': accesos_destacados,
        'total_accesos': total_accesos,
        'rol_display': request.user.get_rol_display(),
    }

    return render(request, 'vistas/bienvenida.html', context)


def calcular_kpis_salud_inventario(sucursal_id, hoy):
    """KPIs ejecutivos de salud de inventario: cobertura en días, % de stock
    envejecido (>180 días vía lotes FIFO) y sell-through 30d. Con sucursal
    activa mide esa sucursal; sin ella, toda la cadena."""
    from app.constants_kardex import CONCEPTOS_VENTA
    from app.models import LoteProducto, Movimientos_Producto

    d30 = hoy - timedelta(days=30)
    ventas_qs = Movimientos_Producto.objects.filter(
        concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO', fecha__gte=d30,
        ProductoTalla__producto__excluir_de_analitica=False,
    )
    stock_qs = Producto_Talla.objects.filter(
        stock__gt=0, producto__excluir_de_analitica=False,
    )
    lotes_qs = LoteProducto.objects.filter(
        activo=True, cantidad_disponible__gt=0,
        producto_talla__producto__excluir_de_analitica=False,
    )
    if sucursal_id:
        ventas_qs = ventas_qs.filter(sucursal_origen_id=sucursal_id)
        stock_qs = stock_qs.filter(producto__sucursal_id=sucursal_id)
        lotes_qs = lotes_qs.filter(producto_talla__producto__sucursal_id=sucursal_id)

    vendidas_30 = abs(ventas_qs.aggregate(s=Sum('cantidad'))['s'] or 0)
    stock_total = stock_qs.aggregate(s=Sum('stock'))['s'] or 0
    stock_viejo = lotes_qs.filter(
        fecha_ingreso__date__lte=hoy - timedelta(days=181)
    ).aggregate(s=Sum('cantidad_disponible'))['s'] or 0

    velocidad = vendidas_30 / 30 if vendidas_30 else 0
    return {
        'cobertura_dias': int(stock_total / velocidad) if velocidad else None,
        'pct_stock_viejo': round(100 * stock_viejo / stock_total, 1) if stock_total else 0,
        'sell_through_30': round(100 * vendidas_30 / (vendidas_30 + stock_total), 1)
                           if (vendidas_30 + stock_total) else 0,
        'vendidas_30': vendidas_30,
        'stock_total': stock_total,
        'stock_viejo_unidades': stock_viejo,
    }


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
        
        # Verificar si el usuario tiene permiso para ver el dashboard con estadísticas
        tiene_permiso_dashboard = PermisoRol.tiene_permiso(
            request.user, 
            'dashboard_general', 
            'puede_ver',
            sucursal_id=sucursal_id
        )
        
        if not tiene_permiso_dashboard:
            # Redirigir a página de bienvenida básica
            return redirect('bienvenida')
        
        # Obtener información de la sucursal
        sucursal_actual = None
        if sucursal_id:
            sucursal_actual = Sucursal.objects.filter(id=sucursal_id).first()
        
        # Fechas para filtros
        hoy = timezone.localdate()
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
        
        # ========== 6. KPIs CAJA Y DEPOSITOS ==========
        try:
            caja_data = calcular_kpis_caja_depositos(sucursal_id, hoy, inicio_mes)
        except Exception:
            caja_data = {
                'arqueos_abiertos': 0, 'arqueos_con_diferencias': 0,
                'diferencia_efectivo': 0, 'diferencia_transbank': 0,
                'diferencia_total': 0, 'fecha_ultimo_arqueo': None,
                'depositos_pendientes': 0, 'monto_sin_verificar': 0,
                'total_arqueos_mes': 0,
            }

        # ========== 7. KPIs PRECIOS PENDIENTES ==========
        try:
            precios_data = calcular_kpis_precios_pendientes(sucursal_id)
        except Exception:
            precios_data = {
                'total_pendientes': 0, 'urgentes': 0, 'impacto_estimado': 0,
            }

        # ========== 8. DTEs CON PROBLEMAS (rechazados / en regularización) ==========
        try:
            dte_problemas = calcular_kpis_dte_problemas(sucursal_id, empresa_id)
        except Exception:
            dte_problemas = {'rechazados': 0, 'en_regularizacion': 0, 'total': 0}

        # ========== 9. TOP PRODUCTOS DEL MES ==========
        try:
            top_productos = obtener_top_productos(sucursal_id, inicio_mes, hoy)
        except Exception:
            top_productos = []

        # ========== 9b. SALUD DE INVENTARIO (ejecutivo) ==========
        try:
            salud_inventario = calcular_kpis_salud_inventario(sucursal_id, hoy)
        except Exception:
            logger.exception("Error calculando salud de inventario")
            salud_inventario = None

        # ========== 10. ALERTAS CRÍTICAS ==========
        alertas = generar_alertas_criticas(
            stock_data, compras_data, requerimientos_data, operaciones_data,
            caja_data, precios_data, dte_problemas
        )

        context = {
            'sucursal_actual': sucursal_actual,
            'fecha_actual': hoy,

            'ventas': ventas_data,
            'stock': stock_data,
            'compras': compras_data,
            'requerimientos': requerimientos_data,
            'operaciones': operaciones_data,
            'caja': caja_data,
            'precios': precios_data,
            'dte_problemas': dte_problemas,
            'top_productos': top_productos,
            'salud_inventario': salud_inventario,

            'alertas': alertas,
        }

        return render(request, 'vistas/dashboard_home.html', context)
        
    except Exception as e:
        logger.exception("Error en dashboard_home usuario_id=%s", request.user.id)
        
        # Retornar contexto mínimo en caso de error
        return render(request, 'vistas/dashboard_home.html', {
            'error': str(e),
            'ventas': {'hoy': 0, 'semana': 0, 'mes': 0, 'unidades_hoy': 0, 'ticket_promedio': 0, 'ventas_ultimos_30_dias': [], 'ventas_por_hora': []},
            'stock': {'total_skus': 0, 'stock_critico': 0, 'sin_stock': 0, 'valor_inventario': 0,
                      'quiebres_rotantes': 0, 'quiebres_lista': [], 'total_unidades': 0, 'rotacion_mes': 0},
            'compras': {'pendientes_recepcion': 0, 'dtes_pendientes': 0, 'monto_pendiente': 0, 'compras_mes': 0, 'lista_pendientes': []},
            'requerimientos': {'pendientes': 0, 'en_proceso': 0, 'total_mes': 0, 'esperando': 0, 'tasa_aprobacion': 0, 'dias_promedio': 0},
            'operaciones': {'traspasos_pendientes': 0, 'ajustes_pendientes': 0, 'cambios_pendientes': 0, 'regularizaciones': 0, 'dtes_por_cobrar': 0, 'monto_por_cobrar': 0},
            'caja': {
                'arqueos_abiertos': 0, 'arqueos_con_diferencias': 0,
                'diferencia_efectivo': 0, 'diferencia_transbank': 0,
                'diferencia_total': 0, 'fecha_ultimo_arqueo': None,
                'depositos_pendientes': 0, 'monto_sin_verificar': 0,
                'total_arqueos_mes': 0,
            },
            'precios': {'total_pendientes': 0, 'urgentes': 0, 'impacto_estimado': 0},
            'dte_problemas': {'rechazados': 0, 'en_regularizacion': 0, 'total': 0},
            'top_productos': [],
            'alertas': [],
            'fecha_actual': timezone.localdate(),
        })


def calcular_kpis_ventas(sucursal_id, hoy, inicio_semana, inicio_mes, mes_pasado_inicio, mes_pasado_fin):
    """Calcula KPIs de ventas"""
    # Base queryset de tickets.
    # Se excluyen los tickets de CAMBIO_DEVOLUCION: son la diferencia a cobrar
    # de un cambio, no una venta nueva. Sumarlos inflaba ventas, ticket
    # promedio y el Top de productos (el POS y el reporte de ventas ya los
    # excluyen; el home era el único que los contaba).
    tickets_base = Ticket.objects.filter(estado='PAGADO').exclude(
        modulo_origen='CAMBIO_DEVOLUCION'
    )
    if sucursal_id:
        tickets_base = tickets_base.filter(sucursal_id=sucursal_id)
    
    # Ventas HOY — created_at es la fecha real de venta; Ticket.fecha es
    # auto_now (se reescribe en cada save y desplaza ventas entre días).
    ventas_hoy_query = tickets_base.filter(created_at__date=hoy)
    ventas_hoy = ventas_hoy_query.aggregate(total=Sum('total'))['total'] or 0
    tickets_hoy = ventas_hoy_query.count()
    
    # Unidades vendidas hoy (sin pseudo-artículos: un COSTO ENVIO no es una unidad)
    unidades_hoy = Ticket_Productos.objects.filter(
        idTicket__in=ventas_hoy_query
    ).exclude(
        ProductoTalla__producto__excluir_de_analitica=True
    ).aggregate(total=Sum('stock'))['total'] or 0
    
    # Ventas SEMANA
    ventas_semana = tickets_base.filter(
        created_at__date__gte=inicio_semana,
        created_at__date__lte=hoy
    ).aggregate(total=Sum('total'))['total'] or 0

    # Ventas MES
    ventas_mes_query = tickets_base.filter(
        created_at__date__gte=inicio_mes, created_at__date__lte=hoy
    )
    ventas_mes = ventas_mes_query.aggregate(total=Sum('total'))['total'] or 0
    tickets_mes = ventas_mes_query.count()

    # Ventas MES PASADO (para comparar)
    ventas_mes_pasado = tickets_base.filter(
        created_at__date__gte=mes_pasado_inicio,
        created_at__date__lte=mes_pasado_fin
    ).aggregate(total=Sum('total'))['total'] or 0
    
    # Calcular variación porcentual
    if ventas_mes_pasado > 0:
        variacion_mes = round(((ventas_mes - ventas_mes_pasado) / ventas_mes_pasado) * 100, 1)
    else:
        variacion_mes = 100 if ventas_mes > 0 else 0
    
    # Ticket promedio
    ticket_promedio = round(ventas_hoy / tickets_hoy, 0) if tickets_hoy > 0 else 0
    ticket_promedio_mes = round(ventas_mes / tickets_mes, 0) if tickets_mes > 0 else 0
    
    # Ventas por hora (hoy - una sola query agrupada por hora, desde created_at)
    from django.db.models.functions import ExtractHour, TruncDate
    ventas_hora_qs = tickets_base.filter(created_at__date=hoy).annotate(
        hora_num=ExtractHour('created_at')
    ).values('hora_num').annotate(
        monto=Sum('total')
    ).order_by('hora_num')
    ventas_hora_map = {item['hora_num']: int(item['monto'] or 0) for item in ventas_hora_qs}
    ventas_por_hora = [{'hora': i, 'monto': ventas_hora_map.get(i, 0)} for i in range(24)]

    # Venta general de los ultimos 30 dias (todas las ventas, agrupadas por dia real)
    fecha_30d = hoy - timedelta(days=29)
    ventas_dia_qs = tickets_base.filter(
        created_at__date__gte=fecha_30d, created_at__date__lte=hoy
    ).annotate(dia=TruncDate('created_at')).values('dia').annotate(
        monto=Sum('total'),
        documentos=Count('id')
    ).order_by('dia')
    ventas_dia_map = {item['dia']: item for item in ventas_dia_qs}
    ventas_ultimos_30_dias = []
    for i in range(30):
        dia = fecha_30d + timedelta(days=i)
        item = ventas_dia_map.get(dia)
        ventas_ultimos_30_dias.append({
            'fecha': dia.strftime('%d/%m'),
            'monto': int(item['monto']) if item else 0,
            'documentos': item['documentos'] if item else 0,
        })

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
        'ventas_ultimos_30_dias': ventas_ultimos_30_dias,
        'tendencia': 'up' if variacion_mes > 0 else ('down' if variacion_mes < 0 else 'stable'),
    }


def calcular_kpis_stock(sucursal_id, empresa_id):
    """
    Calcula KPIs de stock/inventario
    CORREGIDO: Usa Producto_Talla.stock directamente para reflejar los 100,000+ productos
    """
    # Base query para productos con talla. Excluye los pseudo-artículos
    # (excluir_de_analitica=True: DIFER VISA, bolsas de empaque, COSTO ENVIO...)
    # cuyo "stock" no es mercadería: solo en PAO2 sumaban ~14k unidades. El
    # panel de Salud de Inventario ya los filtraba y esta sección no, así que
    # el mismo tablero mostraba dos totales de unidades distintos.
    productos_talla_query = Producto_Talla.objects.filter(
        producto__excluir_de_analitica=False,
    ).select_related(
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
    inicio_mes = timezone.localdate().replace(day=1)
    ventas_mes_query = Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=inicio_mes,
        idTicket__estado='PAGADO'
    ).exclude(
        # La rotación compara vendido vs stock: ambos lados sin pseudo-artículos.
        ProductoTalla__producto__excluir_de_analitica=True
    )
    if sucursal_id:
        ventas_mes_query = ventas_mes_query.filter(idTicket__sucursal_id=sucursal_id)
    
    total_vendido = ventas_mes_query.aggregate(total=Sum('stock'))['total'] or 0
    rotacion = round((total_vendido / total_stock) * 100, 1) if total_stock > 0 else 0

    # ===== QUIEBRES ACCIONABLES =====
    # SKUs que SÍ rotan (vendieron en los últimos 30 días) y hoy están en cero.
    # Esto es lo accionable de verdad, a diferencia de "22.307 sin stock" (catálogo muerto).
    fecha_30 = timezone.localdate() - timedelta(days=30)
    vendidos_30d = Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=fecha_30,
        idTicket__estado='PAGADO',
    )
    if sucursal_id:
        vendidos_30d = vendidos_30d.filter(idTicket__sucursal_id=sucursal_id)

    quiebres_qs = Producto_Talla.objects.filter(
        Q(stock=0) | Q(stock__isnull=True),
        id__in=vendidos_30d.values('ProductoTalla_id'),
        producto__excluir_de_analitica=False,
    )
    if sucursal_id:
        quiebres_qs = quiebres_qs.filter(producto__sucursal_id=sucursal_id)
    quiebres_rotantes = quiebres_qs.count()

    # Top quiebres por unidades vendidas en el período (lo más urgente de reponer)
    top_quiebres = vendidos_30d.filter(ProductoTalla__in=quiebres_qs).values(
        'ProductoTalla__producto__articulo', 'ProductoTalla__talla'
    ).annotate(vendidas=Sum('stock')).order_by('-vendidas')[:8]
    quiebres_lista = [{
        'producto': (it['ProductoTalla__producto__articulo'] or 'N/A')[:30],
        'talla': it['ProductoTalla__talla'],
        'vendidas': int(it['vendidas'] or 0),
    } for it in top_quiebres]

    logger.debug(
        "KPIs stock dashboard: sucursal_id=%s total_skus=%s con_stock=%s sin_stock=%s "
        "stock_critico=%s quiebres_rotantes=%s total_unidades=%s valor_inventario=%s",
        sucursal_id,
        total_skus,
        skus_con_stock,
        sin_stock_count,
        stock_critico_count,
        quiebres_rotantes,
        total_stock,
        valor_inventario_total,
    )

    return {
        'total_skus': total_skus,
        'skus_con_stock': skus_con_stock,
        'stock_critico': stock_critico_count,
        'sin_stock': sin_stock_count,
        'quiebres_rotantes': quiebres_rotantes,
        'quiebres_lista': quiebres_lista,
        'valor_inventario': int(valor_inventario_total),
        'productos_criticos': productos_criticos,
        'total_unidades': total_stock,
        'rotacion_mes': rotacion,
    }


def calcular_kpis_dte_problemas(sucursal_id, empresa_id):
    """
    DTEs que requieren acción: rechazados o en regularización.
    Lo que el usuario llama "DTEs por actualizar / con problemas".
    """
    qs = Dte.objects.filter(
        estado_dte__in=['RECHAZADO', 'EN_REGULARIZACION'],
        descartado=False,
    )
    if sucursal_id:
        qs = qs.filter(sucursal_id=sucursal_id)

    rechazados = qs.filter(estado_dte='RECHAZADO').count()
    en_regularizacion = qs.filter(estado_dte='EN_REGULARIZACION').count()

    return {
        'rechazados': rechazados,
        'en_regularizacion': en_regularizacion,
        'total': rechazados + en_regularizacion,
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
        estado__in=['SOLICITADO', 'EN_PROCESO', 'APROBADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']
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


def calcular_kpis_caja_depositos(sucursal_id, hoy, inicio_mes):
    """Calcula KPIs de cuadraturas de caja y depositos bancarios"""
    arqueos_qs = ArqueoCaja.objects.filter(fecha_arqueo__gte=inicio_mes)
    if sucursal_id:
        arqueos_qs = arqueos_qs.filter(sucursal_id=sucursal_id)

    arqueos_abiertos = arqueos_qs.filter(estado='ABIERTO').count()
    arqueos_con_diferencias = arqueos_qs.filter(estado='CON_DIFERENCIAS').count()

    agg_dif = arqueos_qs.aggregate(
        dif_efectivo=Sum('diferencia_efectivo'),
        dif_transbank=Sum('diferencia_transbank'),
    )
    diferencia_efectivo = abs(int(agg_dif['dif_efectivo'] or 0))
    diferencia_transbank = abs(int(agg_dif['dif_transbank'] or 0))
    diferencia_total = diferencia_efectivo + diferencia_transbank

    ultimo_arqueo = arqueos_qs.order_by('-fecha_arqueo').first()
    fecha_ultimo_arqueo = ultimo_arqueo.fecha_arqueo if ultimo_arqueo else None

    depositos_qs = DepositoBancario.objects.filter(fecha_deposito__gte=inicio_mes)
    if sucursal_id:
        depositos_qs = depositos_qs.filter(arqueo__sucursal_id=sucursal_id)

    dep_sin_verificar = depositos_qs.filter(verificado=False)
    depositos_pendientes = dep_sin_verificar.count()
    monto_sin_verificar = int(dep_sin_verificar.aggregate(t=Sum('monto'))['t'] or 0)

    total_arqueos_mes = arqueos_qs.count()

    return {
        'arqueos_abiertos': arqueos_abiertos,
        'arqueos_con_diferencias': arqueos_con_diferencias,
        'diferencia_efectivo': diferencia_efectivo,
        'diferencia_transbank': diferencia_transbank,
        'diferencia_total': diferencia_total,
        'fecha_ultimo_arqueo': fecha_ultimo_arqueo,
        'depositos_pendientes': depositos_pendientes,
        'monto_sin_verificar': monto_sin_verificar,
        'total_arqueos_mes': total_arqueos_mes,
    }


def calcular_kpis_precios_pendientes(sucursal_id):
    """Calcula KPIs de precios pendientes de regularizar"""
    precios_qs = CambioPrecioPendiente.objects.filter(
        estado='PENDIENTE', descartado=False
    )
    if sucursal_id:
        precios_qs = precios_qs.filter(sucursal_id=sucursal_id)

    total_pendientes = precios_qs.count()

    urgentes = precios_qs.filter(
        Q(prioridad__in=['ALTA', 'URGENTE']) |
        Q(fecha_creacion__lte=timezone.now() - timedelta(days=7))
    ).count()

    agg = precios_qs.aggregate(
        impacto_total=Sum('diferencia'),
    )
    impacto_estimado = abs(int(agg['impacto_total'] or 0))

    return {
        'total_pendientes': total_pendientes,
        'urgentes': urgentes,
        'impacto_estimado': impacto_estimado,
    }


def generar_alertas_criticas(stock_data, compras_data, requerimientos_data, operaciones_data, caja_data=None, precios_data=None, dte_problemas=None):
    """Genera lista de alertas críticas ordenadas por prioridad"""
    alertas = []

    # Alerta de QUIEBRES ACCIONABLES (productos que rotan y hoy están en cero)
    if stock_data.get('quiebres_rotantes', 0) > 0:
        alertas.append({
            'tipo': 'danger',
            'icono': 'ri-error-warning-fill',
            'titulo': f"{stock_data['quiebres_rotantes']} quiebres de productos que rotan",
            'descripcion': 'SKUs vendidos en los últimos 30 días y hoy en cero — repón para no perder ventas',
            'accion': 'Ver quiebres',
            'url': '/app/reportes/resumen-existencias/',
            'prioridad': 1
        })

    # DTEs con problemas (rechazados / en regularización)
    if dte_problemas and dte_problemas.get('total', 0) > 0:
        alertas.append({
            'tipo': 'warning',
            'icono': 'ri-file-warning-fill',
            'titulo': f"{dte_problemas['total']} DTEs con problemas",
            'descripcion': f"Rechazados: {dte_problemas['rechazados']} · En regularización: {dte_problemas['en_regularizacion']}",
            'accion': 'Revisar DTEs',
            'url': '/app/documentos/gestion-dte/',
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
            'url': '/app/ventas/cambios-devoluciones/',
            'prioridad': 5
        })

    if caja_data:
        if caja_data['arqueos_abiertos'] > 0:
            alertas.append({
                'tipo': 'warning',
                'icono': 'ri-safe-2-fill',
                'titulo': f"{caja_data['arqueos_abiertos']} arqueos de caja abiertos",
                'descripcion': 'Hay cuadraturas pendientes de cierre',
                'accion': 'Revisar arqueos',
                'url': '/app/ventas/revision-arqueos/',
                'prioridad': 2
            })
        if caja_data['depositos_pendientes'] > 0:
            alertas.append({
                'tipo': 'warning',
                'icono': 'ri-bank-fill',
                'titulo': f"{caja_data['depositos_pendientes']} depósitos sin verificar",
                'descripcion': f"Monto pendiente: ${caja_data['monto_sin_verificar']:,}",
                'accion': 'Verificar depósitos',
                'url': '/app/ventas/revision-arqueos/',
                'prioridad': 3
            })
        if caja_data['diferencia_total'] > 0:
            alertas.append({
                'tipo': 'danger',
                'icono': 'ri-error-warning-fill',
                'titulo': f"Diferencia acumulada en caja: ${caja_data['diferencia_total']:,}",
                'descripcion': f"Efectivo: ${caja_data['diferencia_efectivo']:,} | Transbank: ${caja_data['diferencia_transbank']:,}",
                'accion': 'Revisar cuadraturas',
                'url': '/app/ventas/revision-arqueos/',
                'prioridad': 1
            })

    if precios_data:
        if precios_data['urgentes'] > 0:
            alertas.append({
                'tipo': 'danger',
                'icono': 'ri-price-tag-3-fill',
                'titulo': f"{precios_data['urgentes']} precios urgentes por regularizar",
                'descripcion': f"Total pendientes: {precios_data['total_pendientes']}",
                'accion': 'Regularizar precios',
                'url': '/app/gestion-precios/revisar-pendientes/',
                'prioridad': 2
            })
        elif precios_data['total_pendientes'] > 0:
            alertas.append({
                'tipo': 'info',
                'icono': 'ri-price-tag-3-line',
                'titulo': f"{precios_data['total_pendientes']} precios pendientes de regularizar",
                'descripcion': 'Revisión de cambios de precio sugeridos',
                'accion': 'Revisar precios',
                'url': '/app/gestion-precios/revisar-pendientes/',
                'prioridad': 5
            })
    
    # Ordenar por prioridad
    alertas.sort(key=lambda x: x['prioridad'])
    
    return alertas


def obtener_top_productos(sucursal_id, inicio_mes, hoy):
    """Obtiene los productos más vendidos del mes"""
    ventas_productos = Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=inicio_mes,
        idTicket__created_at__date__lte=hoy,
        idTicket__estado='PAGADO'
    ).exclude(
        # Mismo criterio que calcular_kpis_ventas: la diferencia cobrada en un
        # cambio no es venta de ese producto.
        idTicket__modulo_origen='CAMBIO_DEVOLUCION'
    ).exclude(
        # Los pseudo-artículos (COSTO ENVIO, bolsas) se "venden" en tickets y
        # por unidades se colaban al Top desplazando productos reales.
        ProductoTalla__producto__excluir_de_analitica=True
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
    fecha_limite = timezone.localdate() - timedelta(days=dias)
    
    # SKUs que SÍ tuvieron ventas en el período
    skus_con_ventas = Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=fecha_limite,
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
        hoy = timezone.localdate()
        
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
