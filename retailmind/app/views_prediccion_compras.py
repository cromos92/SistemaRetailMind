"""
Views del módulo de predicción de compras.
Dashboard + API JSON endpoints.
"""
import json
import logging
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, Avg, F
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    Producto, Producto_Talla, Empresa, EmpresaUser, Sucursal,
)
from .models.compras import Compras_Producto, Compras_Producto_Talla
from .models.predicciones import (
    ConfiguracionPrediccion,
    ClasificacionABC,
    VelocidadHistorica,
    CurvaTalles,
    PrediccionDemanda,
    SugerenciaCompra,
    AlertaVelocidad,
    AlertaQuiebreTalle,
    StockInicialTemporada,
)
from .services.prediccion_compras import ejecutar_pipeline_completo

logger = logging.getLogger('app')

TIPOS_SUCURSAL_VENTA = ['VENDEDORA', 'MIXTA']


def _decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def _get_sucursal_context(request):
    """
    Devuelve (sucursal, es_cd) desde la sesión del usuario.
    - Si la sucursal activa es VENDEDORA/MIXTA: filtra a esa sucursal.
    - Si es CENTRO_DISTRIBUCION: muestra vista agregada de todas las vendedoras.
    - Si no hay sucursal en sesión: vista agregada.
    """
    suc_id = request.session.get('idSucursalActual')
    if not suc_id:
        return None, True
    try:
        suc = Sucursal.objects.get(id=suc_id)
    except Sucursal.DoesNotExist:
        return None, True
    es_cd = suc.es_centro_distribucion or suc.tipo_sucursal == 'CENTRO_DISTRIBUCION'
    return suc, es_cd


def _q_sucursal_productos(suc, es_cd, prefix=''):
    """
    Q filter para productos según contexto de sucursal.
    prefix: '' para Producto, 'articulo__' para ClasificacionABC, etc.
    """
    if es_cd or not suc:
        return Q(**{f'{prefix}sucursal__tipo_sucursal__in': TIPOS_SUCURSAL_VENTA})
    return Q(**{f'{prefix}sucursal': suc})


# ──────────────────────────────────────────────────────────────
#  Dashboard HTML
# ──────────────────────────────────────────────────────────────

@login_required
def dashboard_prediccion(request):
    suc, es_cd = _get_sucursal_context(request)
    suc_nombre = suc.alias if suc else 'Todas las sucursales'
    return render(request, 'vistas/modulo_dashboards/prediccion_compras_dashboard.html', {
        'sucursal_nombre': suc_nombre,
        'es_centro_distribucion': es_cd,
    })


# ──────────────────────────────────────────────────────────────
#  API: Resumen / KPIs
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_resumen(request):
    suc, es_cd = _get_sucursal_context(request)
    q_prod = _q_sucursal_productos(suc, es_cd, prefix='articulo__')
    q_prod_sug = _q_sucursal_productos(suc, es_cd, prefix='articulo_talle__producto__')

    alertas_vel = AlertaVelocidad.objects.filter(q_prod, resuelta=False)
    alertas_quiebre = AlertaQuiebreTalle.objects.filter(q_prod, resuelta=False)
    sugerencias = SugerenciaCompra.objects.filter(
        q_prod_sug, aprobada=False,
        articulo_talle__producto__excluir_de_analitica=False,
    ).select_related(
        'articulo_talle__producto',
        'articulo_talle__producto__categoria',
        'articulo_talle__producto__atributo1',
    )

    vel_criticas = alertas_vel.filter(urgencia='CRITICA').count()
    vel_altas = alertas_vel.filter(urgencia='ALTA').count()
    quiebre_criticas = alertas_quiebre.filter(urgencia='CRITICA').count()
    quiebre_altas = alertas_quiebre.filter(urgencia='ALTA').count()

    total_a_pedir = sugerencias.aggregate(
        total=Sum('unidades_a_pedir'))['total'] or 0
    total_sugerencias = sugerencias.count()

    plan_proveedores = {}
    plan_origenes = {}
    productos_plan = set()
    talles_plan = set()
    costo_estimado = Decimal('0')
    sugerencias_urgentes = 0
    for sugerencia in sugerencias:
        unidades = sugerencia.unidades_a_pedir or 0
        if unidades <= 0:
            continue
        pt = sugerencia.articulo_talle
        producto = pt.producto
        productos_plan.add(producto.id)
        talles_plan.add(pt.id)
        costo_unitario = Decimal(str(producto.costo or 0))
        costo_estimado += costo_unitario * unidades
        plan_origenes[sugerencia.origen] = plan_origenes.get(sugerencia.origen, 0) + unidades
        if sugerencia.origen in {'alerta_velocidad', 'alerta_quiebre'}:
            sugerencias_urgentes += 1

        proveedor = Compras_Producto.objects.filter(
            nombre=producto.articulo,
        ).select_related('compras__empresa').order_by('-fecha').first()
        empresa = proveedor.compras.empresa if proveedor else None
        proveedor_id = empresa.id if empresa else None
        proveedor_nombre = empresa.nombre if empresa else 'Sin proveedor asignado'
        bucket = plan_proveedores.setdefault(proveedor_id or 'sin-proveedor', {
            'proveedor_id': proveedor_id,
            'proveedor': proveedor_nombre,
            'unidades': 0,
            'costo_estimado': Decimal('0'),
            'items': 0,
        })
        bucket['unidades'] += unidades
        bucket['costo_estimado'] += costo_unitario * unidades
        bucket['items'] += 1

    plan_proveedores_data = sorted(
        [
            {
                **item,
                'costo_estimado': float(item['costo_estimado']),
                'participacion': round(item['unidades'] / total_a_pedir * 100, 1) if total_a_pedir else 0,
            }
            for item in plan_proveedores.values()
        ],
        key=lambda item: item['unidades'],
        reverse=True,
    )

    q_abc_suc = _q_sucursal_productos(suc, es_cd, prefix='articulo__')
    abc_dist = ClasificacionABC.objects.filter(
        q_abc_suc, articulo__excluir_de_analitica=False,
    ).values('clasificacion_abc').annotate(
        count=Count('id')).order_by('clasificacion_abc')
    xyz_dist = ClasificacionABC.objects.filter(
        q_abc_suc, articulo__excluir_de_analitica=False,
    ).values('clasificacion_xyz').annotate(
        count=Count('id')).order_by('clasificacion_xyz')

    from .models.inventario import Movimientos_Producto
    from django.db.models import Min, Max
    from django.db.models.functions import ExtractYear

    q_mov_suc = _q_sucursal_productos(suc, es_cd, prefix='ProductoTalla__producto__')
    ventas_conceptos = ['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET']
    ventas_qs = Movimientos_Producto.objects.filter(
        q_mov_suc, concepto__in=ventas_conceptos,
    )
    ventas_stats = ventas_qs.aggregate(
        total=Count('id'),
        fecha_min=Min('fecha'),
        fecha_max=Max('fecha'),
    )
    ventas_migradas = ventas_qs.filter(referencia_externa__startswith='MIG:').count()
    ventas_nuevas = (ventas_stats['total'] or 0) - ventas_migradas

    ventas_por_anio = list(
        ventas_qs.annotate(yr=ExtractYear('fecha'))
        .values('yr').annotate(n=Count('id')).order_by('yr')
    )

    return JsonResponse({
        'alertas': {
            'velocidad_critica': vel_criticas,
            'velocidad_alta': vel_altas,
            'velocidad_total': alertas_vel.count(),
            'quiebre_critica': quiebre_criticas,
            'quiebre_alta': quiebre_altas,
            'quiebre_total': alertas_quiebre.count(),
            'total_criticas': vel_criticas + quiebre_criticas,
        },
        'sugerencias': {
            'total': total_sugerencias,
            'unidades_a_pedir': total_a_pedir,
            'productos': len(productos_plan),
            'talles': len(talles_plan),
            'costo_estimado': float(costo_estimado),
            'urgentes': sugerencias_urgentes,
            'por_origen': plan_origenes,
            'proveedores': plan_proveedores_data[:8],
        },
        'clasificacion_abc': {d['clasificacion_abc']: d['count'] for d in abc_dist},
        'clasificacion_xyz': {d['clasificacion_xyz']: d['count'] for d in xyz_dist},
        'datos_historicos': {
            'ventas_totales': ventas_stats['total'] or 0,
            'ventas_migradas': ventas_migradas,
            'ventas_nuevas': ventas_nuevas,
            'fecha_min': ventas_stats['fecha_min'].isoformat() if ventas_stats['fecha_min'] else None,
            'fecha_max': ventas_stats['fecha_max'].isoformat() if ventas_stats['fecha_max'] else None,
            'por_anio': {str(r['yr']): r['n'] for r in ventas_por_anio if r['yr']},
        },
    })


# ──────────────────────────────────────────────────────────────
#  API: Clasificación ABC-XYZ
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_clasificacion(request):
    suc, es_cd = _get_sucursal_context(request)
    q_suc = _q_sucursal_productos(suc, es_cd, prefix='articulo__')
    clasificaciones = ClasificacionABC.objects.filter(
        q_suc, articulo__excluir_de_analitica=False,
    ).select_related(
        'articulo', 'articulo__atributo1', 'articulo__categoria',
    ).order_by('clasificacion_abc', '-ventas_corregidas')[:500]

    data = []
    for c in clasificaciones:
        data.append({
            'id': c.id,
            'producto_id': c.articulo_id,
            'articulo': c.articulo.articulo,
            'marca': c.articulo.atributo1.valor if c.articulo.atributo1 else '',
            'categoria': c.articulo.categoria.nombre if c.articulo.categoria else '',
            'abc': c.clasificacion_abc,
            'xyz': c.clasificacion_xyz,
            'clase': c.clase_combinada,
            'ventas': c.ventas_totales_periodo,
            'ventas_corregidas': c.ventas_corregidas,
            'cv': float(c.coeficiente_variacion),
            'pct_acumulado': float(c.porcentaje_acumulado),
            'censurada': c.demanda_censurada,
            'factor_censura': float(c.factor_correccion_censura),
        })

    return JsonResponse({'clasificaciones': data})


# ──────────────────────────────────────────────────────────────
#  API: Sugerencias de compra
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_sugerencias(request):
    suc, es_cd = _get_sucursal_context(request)
    q_suc = _q_sucursal_productos(suc, es_cd, prefix='articulo_talle__producto__')
    filtro = Q(q_suc, aprobada=False, unidades_a_pedir__gt=0, articulo_talle__producto__excluir_de_analitica=False)
    origen = request.GET.get('origen')
    if origen:
        filtro &= Q(origen=origen)

    sugerencias = SugerenciaCompra.objects.filter(filtro).select_related(
        'articulo_talle', 'articulo_talle__producto',
        'articulo_talle__producto__atributo1',
        'articulo_talle__producto__categoria',
        'prediccion',
    ).order_by('-unidades_a_pedir')[:300]

    data = []
    for s in sugerencias:
        pt = s.articulo_talle
        prod = pt.producto
        data.append({
            'id': s.id,
            'sku': pt.sku,
            'articulo': prod.articulo,
            'talle': pt.talla,
            'marca': prod.atributo1.valor if prod.atributo1 else '',
            'categoria': prod.categoria.nombre if prod.categoria else '',
            'unidades_sugeridas': s.unidades_sugeridas,
            'stock_actual': s.stock_actual,
            'en_transito': s.unidades_en_transito,
            'a_pedir': s.unidades_a_pedir,
            'origen': s.origen,
            'confianza': float(s.prediccion.confianza) if s.prediccion else 0,
            'fecha': s.fecha_generacion.strftime('%Y-%m-%d'),
        })

    return JsonResponse({'sugerencias': data})


# ──────────────────────────────────────────────────────────────
#  API: Alertas de velocidad
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_alertas_velocidad(request):
    suc, es_cd = _get_sucursal_context(request)
    q_suc = _q_sucursal_productos(suc, es_cd, prefix='articulo__')
    alertas = AlertaVelocidad.objects.filter(
        q_suc, resuelta=False,
        articulo__excluir_de_analitica=False,
    ).select_related(
        'articulo', 'articulo__atributo1', 'articulo__categoria',
    ).order_by(
        models_urgencia_order(), '-fecha_deteccion',
    )[:200]

    data = []
    for a in alertas:
        data.append({
            'id': a.id,
            'producto_id': a.articulo_id,
            'articulo': a.articulo.articulo,
            'marca': a.articulo.atributo1.valor if a.articulo.atributo1 else '',
            'urgencia': a.urgencia,
            'stock_restante': a.stock_restante,
            'semanas_stock': float(a.semanas_stock_restante),
            'semanas_temporada': float(a.semanas_temporada_restante),
            'velocidad_real': float(a.velocidad_real_semanas),
            'velocidad_normal': float(a.velocidad_normal_semanas),
            'ratio': float(a.ratio_velocidad),
            'lead_time_dias': a.lead_time_proveedor_dias,
            'recompra_sugerida': a.unidades_recompra_sugerida,
            'en_transito': a.unidades_en_transito,
            'fecha': a.fecha_deteccion.strftime('%Y-%m-%d %H:%M'),
        })

    return JsonResponse({'alertas_velocidad': data})


def models_urgencia_order():
    from django.db.models import Case, When, Value, IntegerField
    return Case(
        When(urgencia='CRITICA', then=Value(0)),
        When(urgencia='ALTA', then=Value(1)),
        When(urgencia='MEDIA', then=Value(2)),
        When(urgencia='BAJA', then=Value(3)),
        output_field=IntegerField(),
    )


# ──────────────────────────────────────────────────────────────
#  API: Alertas de quiebre de talle
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_alertas_quiebre(request):
    suc, es_cd = _get_sucursal_context(request)
    q_suc = _q_sucursal_productos(suc, es_cd, prefix='articulo__')
    alertas = AlertaQuiebreTalle.objects.filter(
        q_suc, resuelta=False,
    ).select_related(
        'articulo', 'articulo__atributo1', 'articulo__categoria',
    ).order_by('-porcentaje_demanda_sin_cubrir')[:200]

    data = []
    for a in alertas:
        data.append({
            'id': a.id,
            'producto_id': a.articulo_id,
            'articulo': a.articulo.articulo,
            'marca': a.articulo.atributo1.valor if a.articulo.atributo1 else '',
            'urgencia': a.urgencia,
            'talles_agotados': a.talles_agotados,
            'talles_con_stock': a.talles_con_stock,
            'talles_criticos': a.talles_agotados_criticos,
            'stock_restante': a.stock_total_restante,
            'pct_sin_cubrir': float(a.porcentaje_demanda_sin_cubrir),
            'perdidas_semana': a.unidades_perdidas_semana_estimadas,
            'fecha': a.fecha_deteccion.strftime('%Y-%m-%d %H:%M'),
        })

    return JsonResponse({'alertas_quiebre': data})


# ──────────────────────────────────────────────────────────────
#  API: Detalle de producto
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_producto_detalle(request, producto_id):
    try:
        producto = Producto.objects.select_related(
            'atributo1', 'atributo3', 'categoria',
        ).get(id=producto_id)
    except Producto.DoesNotExist:
        return JsonResponse({'error': 'Producto no encontrado'}, status=404)

    from .models.inventario import Movimientos_Producto
    from .models.compras import Compras_Producto_Talla as CPT, Compras_Producto as CP
    from django.db.models.functions import ExtractWeek, ExtractYear, Abs

    ventas_semanales = Movimientos_Producto.objects.filter(
        ProductoTalla__producto=producto,
        concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET'],
    ).annotate(
        semana=ExtractWeek('fecha'),
        yr=ExtractYear('fecha'),
    ).values('semana', 'yr').annotate(
        total=Sum(Abs(F('cantidad'))),
    ).order_by('yr', 'semana')

    serie_temporal = [
        {'semana': v['semana'], 'anio': v['yr'], 'unidades': v['total']}
        for v in ventas_semanales
    ]

    abc = ClasificacionABC.objects.filter(articulo=producto).order_by('-fecha_calculo').first()
    pred = PrediccionDemanda.objects.filter(articulo=producto).order_by('-fecha_generacion').first()
    sit = StockInicialTemporada.objects.filter(producto=producto).order_by('-anio').first()
    alerta_vel = AlertaVelocidad.objects.filter(
        articulo=producto, resuelta=False,
    ).order_by('-fecha_deteccion').first()
    alerta_quiebre = AlertaQuiebreTalle.objects.filter(
        articulo=producto, resuelta=False,
    ).order_by('-fecha_deteccion').first()

    talles_stock = [
        {'talle': t.talla, 'stock': t.stock, 'sku': t.sku}
        for t in producto.producto_talla.all().order_by('talla')
    ]

    sugerencias = SugerenciaCompra.objects.filter(
        articulo_talle__producto=producto, aprobada=False,
    ).values('articulo_talle__talla', 'unidades_a_pedir', 'stock_actual', 'unidades_en_transito')

    # ── Trazabilidad: historial de compras del producto ───────────
    tallas_ids = list(producto.producto_talla.values_list('id', flat=True))
    historial_compras = []
    if tallas_ids:
        compras_rows = (
            CPT.objects
            .filter(producto_talla_id__in=tallas_ids)
            .select_related(
                'compra_producto__compras',
                'compra_producto__compras__empresa',
            )
            .order_by('-compra_producto__compras__fecha')[:20]
        )
        for c in compras_rows:
            oc = c.compra_producto.compras
            fecha_oc = oc.fecha.isoformat() if oc.fecha else None
            fecha_entrega_est = oc.fecha_entrega_estimada.isoformat() if oc.fecha_entrega_estimada else None
            fecha_entrega_real = oc.fecha_entrega_real.isoformat() if oc.fecha_entrega_real else None
            lead_time_real = None
            if oc.fecha_envio_proveedor and oc.fecha_entrega_real:
                lead_time_real = (oc.fecha_entrega_real - oc.fecha_envio_proveedor).days
            lead_time_est = None
            if oc.fecha_envio_proveedor and oc.fecha_entrega_estimada:
                lead_time_est = (oc.fecha_entrega_estimada - oc.fecha_envio_proveedor).days
            historial_compras.append({
                'oc_id': oc.id,
                'oc_nombre': oc.nombre,
                'empresa': oc.empresa.nombre if oc.empresa else '',
                'temporada': oc.temporada,
                'estado': oc.estado,
                'talla': c.talla,
                'unidades_ordenadas': c.stock,
                'unidades_recibidas': c.unidades_recibidas,
                'fecha_oc': fecha_oc,
                'fecha_envio_proveedor': oc.fecha_envio_proveedor.isoformat() if oc.fecha_envio_proveedor else None,
                'fecha_entrega_estimada': fecha_entrega_est,
                'fecha_entrega_real': fecha_entrega_real,
                'lead_time_estimado_dias': lead_time_est,
                'lead_time_real_dias': lead_time_real,
            })

    # Últimas ventas detalladas (movimientos recientes)
    ultimas_ventas = list(
        Movimientos_Producto.objects.filter(
            ProductoTalla__producto=producto,
            concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET'],
        ).order_by('-fecha')
        .values('fecha', 'cantidad', 'concepto', 'ProductoTalla__talla')[:10]
    )
    for v in ultimas_ventas:
        if hasattr(v['fecha'], 'isoformat'):
            v['fecha'] = v['fecha'].isoformat()

    return JsonResponse({
        'producto': {
            'id': producto.id,
            'articulo': producto.articulo,
            'descripcion': producto.descripcion,
            'marca': producto.atributo1.valor if producto.atributo1 else '',
            'genero': producto.atributo3.valor if producto.atributo3 else '',
            'categoria': producto.categoria.nombre if producto.categoria else '',
            'temporada': producto.temporada,
            'semanas_temporada': producto.semanas_temporada,
        },
        'serie_temporal': serie_temporal,
        'talles_stock': talles_stock,
        'clasificacion': {
            'abc': abc.clasificacion_abc if abc else None,
            'xyz': abc.clasificacion_xyz if abc else None,
            'cv': float(abc.coeficiente_variacion) if abc else None,
            'censurada': abc.demanda_censurada if abc else False,
        } if abc else None,
        'prediccion': {
            'unidades': pred.unidades_predichas if pred else None,
            'metodo': pred.metodo_usado if pred else None,
            'confianza': float(pred.confianza) if pred else None,
        } if pred else None,
        'stock_temporada': {
            'inicial': sit.stock_inicial if sit else None,
            'total_ingresado': sit.stock_total_ingresado if sit else None,
            'fecha_primer_ingreso': sit.fecha_primer_ingreso.isoformat() if sit and sit.fecha_primer_ingreso else None,
        } if sit else None,
        'alerta_velocidad': {
            'urgencia': alerta_vel.urgencia,
            'semanas_stock': float(alerta_vel.semanas_stock_restante),
            'semanas_temporada_restante': float(alerta_vel.semanas_temporada_restante),
            'ratio': float(alerta_vel.ratio_velocidad),
            'velocidad_real': float(alerta_vel.velocidad_real_semanas),
            'velocidad_normal': float(alerta_vel.velocidad_normal_semanas),
            'stock_inicial': alerta_vel.stock_inicial,
            'ventas_acumuladas': alerta_vel.ventas_acumuladas,
            'stock_restante': alerta_vel.stock_restante,
            'semanas_activo': float(alerta_vel.semanas_activo),
            'lead_time_dias': alerta_vel.lead_time_proveedor_dias,
            'semanas_para_pedir': float(alerta_vel.semanas_para_pedir),
            'recompra': alerta_vel.unidades_recompra_sugerida,
            'en_transito': alerta_vel.unidades_en_transito,
            'fecha_deteccion': alerta_vel.fecha_deteccion.isoformat(),
        } if alerta_vel else None,
        'alerta_quiebre': {
            'urgencia': alerta_quiebre.urgencia,
            'talles_agotados': alerta_quiebre.talles_agotados,
            'talles_criticos': alerta_quiebre.talles_agotados_criticos,
            'talles_con_stock': alerta_quiebre.talles_con_stock,
            'stock_total_restante': alerta_quiebre.stock_total_restante,
            'pct_sin_cubrir': float(alerta_quiebre.porcentaje_demanda_sin_cubrir),
            'perdidas_semana': alerta_quiebre.unidades_perdidas_semana_estimadas,
            'fecha_deteccion': alerta_quiebre.fecha_deteccion.isoformat(),
        } if alerta_quiebre else None,
        'sugerencias': list(sugerencias),
        'historial_compras': historial_compras,
        'ultimas_ventas': ultimas_ventas,
    }, json_dumps_params={'default': _decimal_to_float})


# ──────────────────────────────────────────────────────────────
#  API: Aprobar sugerencia
# ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def api_prediccion_aprobar_sugerencia(request):
    try:
        body = json.loads(request.body)
        ids = body.get('ids', [])
        if not ids:
            return JsonResponse({'error': 'No se proporcionaron IDs'}, status=400)

        updated = SugerenciaCompra.objects.filter(
            id__in=ids, aprobada=False,
        ).update(
            aprobada=True,
            fecha_aprobacion=timezone.localdate(),
            aprobada_por=request.user.username,
        )
        return JsonResponse({'aprobadas': updated})
    except Exception as e:
        logger.exception("Error al aprobar sugerencias")
        return JsonResponse({'error': str(e)}, status=500)


# ──────────────────────────────────────────────────────────────
#  API: Recalcular (trigger manual)
# ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def api_prediccion_recalcular(request):
    try:
        body = json.loads(request.body) if request.body else {}
        resultados = ejecutar_pipeline_completo(
            temporada=body.get('temporada'),
            anio=body.get('anio'),
        )
        return JsonResponse({'resultados': resultados})
    except Exception as e:
        logger.exception("Error al recalcular predicciones")
        return JsonResponse({'error': str(e)}, status=500)


# ──────────────────────────────────────────────────────────────
#  API: Datos para gráficos avanzados
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_graficos(request):
    """
    Datos para la tab Gráficos mejorada:
    1) Tendencia semanal de ventas (últimas 12 semanas)
    2) Precisión del modelo (MAPE por método)
    3) Comparativa vs temporada anterior
    4) Alertas por categoría (heatmap data)
    """
    from .models.inventario import Movimientos_Producto
    from django.db.models.functions import ExtractWeek, ExtractYear, Abs as AbsFunc

    suc, es_cd = _get_sucursal_context(request)
    q_prod = _q_sucursal_productos(suc, es_cd, prefix='ProductoTalla__producto__')
    q_prod_alerta = _q_sucursal_productos(suc, es_cd, prefix='articulo__')

    from datetime import timedelta as _td
    fecha_12sem = timezone.localdate() - _td(weeks=12)
    ventas_sem = (
        Movimientos_Producto.objects.filter(
            q_prod,
            concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET'],
            fecha__gte=fecha_12sem,
        ).annotate(
            semana=ExtractWeek('fecha'),
            yr=ExtractYear('fecha'),
        ).values('semana', 'yr')
        .annotate(total=Sum(AbsFunc(F('cantidad'))))
        .order_by('yr', 'semana')
    )
    tendencia = [
        {'semana': v['semana'], 'anio': v['yr'], 'unidades': int(v['total'] or 0)}
        for v in ventas_sem
    ]

    metodos_agg = (
        PrediccionDemanda.objects.filter(
            articulo__excluir_de_analitica=False,
        ).values('metodo_usado')
        .annotate(
            n=Count('id'),
            conf_avg=Avg('confianza'),
        ).order_by('metodo_usado')
    )
    precision = [
        {
            'metodo': m['metodo_usado'],
            'cantidad': m['n'],
            'confianza_promedio': round(float(m['conf_avg'] or 0) * 100, 1),
        }
        for m in metodos_agg
    ]

    t_def, a_def = _prediccion_default_temporada_anio()
    anio_actual = a_def
    anio_anterior = anio_actual - 1

    def _ventas_por_cat(anio_temp):
        return dict(
            Movimientos_Producto.objects.filter(
                q_prod,
                concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET'],
                ProductoTalla__producto__anio_temporada=anio_temp,
            ).values('ProductoTalla__producto__categoria__nombre')
            .annotate(total=Sum(AbsFunc(F('cantidad'))))
            .values_list('ProductoTalla__producto__categoria__nombre', 'total')
        )

    ventas_act = _ventas_por_cat(anio_actual)
    ventas_ant = _ventas_por_cat(anio_anterior)
    categorias_all = sorted(set(list(ventas_act.keys()) + list(ventas_ant.keys())))
    comparativa = [
        {
            'categoria': cat or 'Sin categoría',
            'actual': int(ventas_act.get(cat, 0) or 0),
            'anterior': int(ventas_ant.get(cat, 0) or 0),
        }
        for cat in categorias_all[:15]
    ]

    alertas_cat = (
        AlertaVelocidad.objects.filter(q_prod_alerta, resuelta=False)
        .values('articulo__categoria__nombre', 'urgencia')
        .annotate(n=Count('id'))
        .order_by('articulo__categoria__nombre')
    )
    heatmap = {}
    for row in alertas_cat:
        cat = row['articulo__categoria__nombre'] or 'Sin categoría'
        if cat not in heatmap:
            heatmap[cat] = {'CRITICA': 0, 'ALTA': 0, 'MEDIA': 0}
        heatmap[cat][row['urgencia']] = row['n']
    heatmap_list = [
        {'categoria': cat, **vals}
        for cat, vals in sorted(heatmap.items(), key=lambda x: -(x[1].get('CRITICA', 0) + x[1].get('ALTA', 0)))
    ][:15]

    return JsonResponse({
        'tendencia_semanal': tendencia,
        'precision_modelo': precision,
        'comparativa_temporal': {
            'anio_actual': anio_actual,
            'anio_anterior': anio_anterior,
            'categorias': comparativa,
        },
        'heatmap_alertas': heatmap_list,
    }, json_dumps_params={'default': _decimal_to_float})


# ──────────────────────────────────────────────────────────────
#  API: Configuración
# ──────────────────────────────────────────────────────────────

@login_required
def api_prediccion_configuracion(request):
    config = ConfiguracionPrediccion.get_config()

    if request.method == 'GET':
        return JsonResponse({
            'dias_historico_analisis': config.dias_historico_analisis,
            'factor_seguridad_clase_a': float(config.factor_seguridad_clase_a),
            'factor_seguridad_clase_b': float(config.factor_seguridad_clase_b),
            'factor_seguridad_clase_c': float(config.factor_seguridad_clase_c),
            'semanas_minimas_para_alerta': config.semanas_minimas_para_alerta,
            'umbral_cv_x': float(config.umbral_cv_x),
            'umbral_cv_y': float(config.umbral_cv_y),
            'umbral_ratio_velocidad_alta': float(config.umbral_ratio_velocidad_alta),
            'umbral_ratio_velocidad_critica': float(config.umbral_ratio_velocidad_critica),
            'umbral_quiebre_talle_critico': float(config.umbral_quiebre_talle_critico),
            'semanas_buffer_seguridad': config.semanas_buffer_seguridad,
            'sellthrough_default': float(config.sellthrough_default),
            'velocidad_default_semanas': config.velocidad_default_semanas,
        })

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            campos_int = [
                'dias_historico_analisis', 'semanas_minimas_para_alerta',
                'semanas_buffer_seguridad', 'velocidad_default_semanas',
            ]
            campos_decimal = [
                'factor_seguridad_clase_a', 'factor_seguridad_clase_b',
                'factor_seguridad_clase_c', 'umbral_cv_x', 'umbral_cv_y',
                'umbral_ratio_velocidad_alta', 'umbral_ratio_velocidad_critica',
                'umbral_quiebre_talle_critico', 'sellthrough_default',
            ]
            for campo in campos_int:
                if campo in body:
                    setattr(config, campo, int(body[campo]))
            for campo in campos_decimal:
                if campo in body:
                    setattr(config, campo, Decimal(str(body[campo])))
            config.save()
            return JsonResponse({'ok': True})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'error': 'Método no permitido'}, status=405)


# ──────────────────────────────────────────────────────────────
#  API: Categorías disponibles (selector dashboard)
# ──────────────────────────────────────────────────────────────

def _prediccion_default_temporada_anio():
    row = PrediccionDemanda.objects.values('temporada', 'anio').annotate(
        n=Count('id')).order_by('-n').first()
    if row:
        return row['temporada'], row['anio']
    return 'verano', timezone.localdate().year


def _resolver_categoria_nombre(categoria_param):
    """Devuelve el nombre de categoría tal como está en VelocidadHistorica/Producto."""
    categoria_param = (categoria_param or '').strip()
    if not categoria_param:
        return None
    exact = VelocidadHistorica.objects.filter(
        categoria__iexact=categoria_param,
    ).values_list('categoria', flat=True).first()
    if exact:
        return exact
    partial = VelocidadHistorica.objects.filter(
        categoria__icontains=categoria_param,
    ).values_list('categoria', flat=True).first()
    if partial:
        return partial
    from .models.catalogo import Categoria
    cat = Categoria.objects.filter(nombre__iexact=categoria_param).first()
    if cat:
        vh = VelocidadHistorica.objects.filter(
            categoria__iexact=cat.nombre,
        ).values_list('categoria', flat=True).first()
        if vh:
            return vh
        return cat.nombre
    return None


def _q_categoria_canonical(canonical, prefix=''):
    """
    Devuelve un Q para filtrar por categoría canonical, resolviendo padre/hijo.
    - prefix='' → para Producto directamente (campo categoria)
    - prefix='articulo__' → para ClasificacionABC, PrediccionDemanda (articulo FK → Producto)
    - prefix='articulo_talle__producto__' → para SugerenciaCompra
    - prefix='producto_talla__producto__' → para Compras_Producto_Talla
    El servicio usa _get_categoria_nombre que devuelve el nombre del PADRE cuando existe,
    por eso hay que filtrar también hijos del padre canonical.
    """
    from .models.catalogo import Categoria
    base = f'{prefix}categoria__nombre__iexact'
    padre_base = f'{prefix}categoria__padre__nombre__iexact'
    q = Q(**{base: canonical})
    padre = Categoria.objects.filter(nombre__iexact=canonical).first()
    if padre:
        q |= Q(**{padre_base: canonical})
    return q


def _weighted_velocidad_rows(rows):
    """Promedio ponderado por total_articulos_base."""
    wsum = 0
    p25 = p50 = p75 = st = 0.0
    for r in rows:
        w = int(r.total_articulos_base or 0)
        if w <= 0:
            w = 1
        p25 += float(r.velocidad_semanas_p25 or 0) * w
        p50 += float(r.velocidad_semanas_p50 or 0) * w
        p75 += float(r.velocidad_semanas_p75 or 0) * w
        st += float(r.sellthrough_p50 or 0) * w
        wsum += w
    if wsum <= 0:
        return None
    inv = 1.0 / wsum
    return {
        'p25': p25 * inv,
        'p50': p50 * inv,
        'p75': p75 * inv,
        'sellthrough': st * inv,
    }


@login_required
@require_GET
def api_prediccion_categorias_disponibles(request):
    """
    Lista categorías presentes en VelocidadHistorica con métricas agregadas
    para poblar el selector del análisis por categoría.
    """
    cfg = ConfiguracionPrediccion.get_config()
    st_default = float(cfg.sellthrough_default)

    qs = VelocidadHistorica.objects.exclude(
        categoria='',
    ).values('categoria').annotate(
        marcas_activas=Count('marca', filter=Q(marca__isnull=False), distinct=True),
        sell_avg=Avg('sellthrough_p50'),
    ).order_by('categoria')

    # Pre-calcular unidades_a_pedir por categoria en una sola query (fix N+1)
    # Agrupa por nombre de categoria directa; luego se une con el canonical en Python
    sug_por_cat = (
        SugerenciaCompra.objects.filter(aprobada=False)
        .values('articulo_talle__producto__categoria__nombre')
        .annotate(u_pedir=Sum('unidades_a_pedir'))
    )
    sug_map = {
        r['articulo_talle__producto__categoria__nombre']: r['u_pedir'] or 0
        for r in sug_por_cat
    }

    categorias = []
    for row in qs:
        nombre = row['categoria']
        sell = float(row['sell_avg'] or st_default)
        u_pedir = sug_map.get(nombre, 0)
        categorias.append({
            'categoria': nombre,
            'marcas_activas': row['marcas_activas'],
            'sell_through_promedio_pct': round(sell * 100, 1),
            'unidades_a_pedir': int(u_pedir),
        })

    categorias.sort(key=lambda x: (-x['unidades_a_pedir'], x['categoria']))
    temporada, anio = _prediccion_default_temporada_anio()
    return JsonResponse({
        'temporada_default': temporada,
        'anio_default': anio,
        'categorias': categorias,
    })


@login_required
@require_GET
def api_prediccion_analisis_categoria(request):
    """
    Análisis agregado por marca dentro de una categoría (rotación, ABC, predicción, alertas).
    """
    categoria_param = request.GET.get('categoria', '').strip()
    if not categoria_param:
        return JsonResponse({'error': 'Parámetro categoria es obligatorio'}, status=400)

    canonical = _resolver_categoria_nombre(categoria_param)
    if not canonical:
        return JsonResponse({
            'error': 'Categoría no encontrada en datos de predicción',
            'categoria_solicitada': categoria_param,
        }, status=404)

    t_def, a_def = _prediccion_default_temporada_anio()
    temporada = request.GET.get('temporada', '').strip() or t_def
    anio_str = request.GET.get('anio', '').strip()
    anio = int(anio_str) if anio_str else a_def

    genero = request.GET.get('genero', '').strip().upper()
    genero_filtro = genero if genero in ('HOMBRE', 'MUJER', 'UNISEX', 'NIÑO', 'NIÑA', 'INFANTIL') else ''

    cfg = ConfiguracionPrediccion.get_config()
    fa = float(cfg.factor_seguridad_clase_a)
    fb = float(cfg.factor_seguridad_clase_b)
    fc = float(cfg.factor_seguridad_clase_c)

    vh_base = VelocidadHistorica.objects.filter(
        categoria=canonical,
        temporada=temporada,
    )
    if genero_filtro:
        vh_base = vh_base.filter(genero__iexact=genero_filtro)

    marca_ids = vh_base.exclude(marca__isnull=True).values_list(
        'marca_id', flat=True).distinct()

    # Q objects con prefijo correcto para cada modelo (fix padre/hijo)
    q_cat_abc = _q_categoria_canonical(canonical, prefix='articulo__')
    q_cat_pred = _q_categoria_canonical(canonical, prefix='articulo__')
    q_cat_sug = _q_categoria_canonical(canonical, prefix='articulo_talle__producto__')
    q_cat_compras = _q_categoria_canonical(canonical, prefix='producto_talla__producto__')
    q_cat_alerta = _q_categoria_canonical(canonical, prefix='articulo__')

    filas = []
    for mid in marca_ids:
        vh_rows = list(vh_base.filter(marca_id=mid))
        if not vh_rows:
            continue
        wv = _weighted_velocidad_rows(vh_rows)
        if not wv:
            continue
        mediana = wv['p50']
        sell = wv['sellthrough']

        abc_vals = ClasificacionABC.objects.filter(
            q_cat_abc,
            articulo__atributo1_id=mid,
            temporada=temporada,
            anio=anio,
        )
        if genero_filtro:
            abc_vals = abc_vals.filter(articulo__atributo3__valor__iexact=genero_filtro)

        # Tres counts en una sola query con conditional aggregation
        abc_agg = abc_vals.aggregate(
            n_a=Count('id', filter=Q(clasificacion_abc='A')),
            n_b=Count('id', filter=Q(clasificacion_abc='B')),
            n_c=Count('id', filter=Q(clasificacion_abc='C')),
        )
        n_a = abc_agg['n_a']
        n_b = abc_agg['n_b']
        n_c = abc_agg['n_c']
        n_tot = n_a + n_b + n_c
        factor_abc = (
            (n_a * fa + n_b * fb + n_c * fc) / n_tot if n_tot else 1.0
        )

        pred_qs = PrediccionDemanda.objects.filter(
            q_cat_pred,
            articulo__atributo1_id=mid,
            temporada=temporada,
            anio=anio,
        )
        if genero_filtro:
            pred_qs = pred_qs.filter(articulo__atributo3__valor__iexact=genero_filtro)
        unidades_pred = pred_qs.aggregate(s=Sum('unidades_predichas'))['s'] or 0

        sug_qs = SugerenciaCompra.objects.filter(
            q_cat_sug,
            aprobada=False,
            articulo_talle__producto__atributo1_id=mid,
        ).filter(
            Q(prediccion__temporada=temporada, prediccion__anio=anio)
            | Q(
                prediccion__isnull=True,
                articulo_talle__producto__temporada__iexact=temporada,
                articulo_talle__producto__anio_temporada=anio,
            )
        )
        if genero_filtro:
            sug_qs = sug_qs.filter(
                articulo_talle__producto__atributo3__valor__iexact=genero_filtro,
            )

        # Tres agregados en una sola query
        sug_agg = sug_qs.aggregate(
            u_sugeridas=Sum('unidades_sugeridas'),
            u_stock=Sum('stock_actual'),
            u_pedir=Sum('unidades_a_pedir'),
        )
        u_sugeridas = sug_agg['u_sugeridas'] or 0
        u_stock = sug_agg['u_stock'] or 0
        u_pedir = sug_agg['u_pedir'] or 0

        # Monto: precio de venta × unidades a pedir  (agregado en DB evita iteración Python)
        from django.db.models import ExpressionWrapper, IntegerField
        monto_agg = sug_qs.annotate(
            monto_linea=ExpressionWrapper(
                F('unidades_a_pedir') * F('articulo_talle__producto__precioventa'),
                output_field=IntegerField(),
            ),
        ).aggregate(total_monto=Sum('monto_linea'))
        monto = monto_agg['total_monto'] or 0

        anio_ant = anio - 1
        compras_ant = Compras_Producto_Talla.objects.filter(
            q_cat_compras,
            producto_talla__isnull=False,
            producto_talla__producto__atributo1_id=mid,
            compra_producto__compras__temporada__iexact=temporada,
            compra_producto__compras__fecha__year=anio_ant,
            compra_producto__compras__estado__in=['ACTIVA', 'COMPLETADA'],
        )
        if genero_filtro:
            compras_ant = compras_ant.filter(
                producto_talla__producto__atributo3__valor__iexact=genero_filtro,
            )
        u_compradas_ant = compras_ant.aggregate(t=Sum('stock'))['t'] or 0

        variacion_pct = None
        if u_compradas_ant:
            variacion_pct = round(
                (float(u_pedir) - float(u_compradas_ant)) / float(u_compradas_ant) * 100.0,
                2,
            )

        alertas_vel = AlertaVelocidad.objects.filter(
            q_cat_alerta,
            resuelta=False,
            urgencia='CRITICA',
            articulo__atributo1_id=mid,
            temporada=temporada,
            anio=anio,
        )
        if genero_filtro:
            alertas_vel = alertas_vel.filter(
                articulo__atributo3__valor__iexact=genero_filtro,
            )

        alertas_q = AlertaQuiebreTalle.objects.filter(
            q_cat_alerta,
            resuelta=False,
            urgencia='CRITICA',
            articulo__atributo1_id=mid,
            temporada=temporada,
            anio=anio,
        )
        if genero_filtro:
            alertas_q = alertas_q.filter(
                articulo__atributo3__valor__iexact=genero_filtro,
            )

        marca_nombre = vh_rows[0].marca.valor if vh_rows[0].marca else ''

        score_f = (sell / max(mediana, 0.01)) * factor_abc

        if sell > 0.85 and mediana < 15:
            badge = 'alta_prioridad'
        elif sell < 0.50 and mediana > 20:
            badge = 'revisar'
        else:
            badge = 'normal'

        filas.append({
            'marca': marca_nombre,
            'marca_id': mid,
            'categoria': canonical,
            'mediana_semanas_agotar': float(mediana),
            'sell_through_promedio_pct': round(float(sell) * 100, 1),
            'percentil_25_semanas': round(wv['p25'], 2),
            'percentil_75_semanas': round(wv['p75'], 2),
            'score_rotacion': round(score_f, 6),
            'skus_clase_A': n_a,
            'skus_clase_B': n_b,
            'skus_clase_C': n_c,
            'unidades_sugeridas_total': int(u_sugeridas),
            'unidades_stock_actual': int(u_stock),
            'unidades_a_pedir': int(u_pedir),
            'monto_estimado': monto,
            'unidades_compradas_anterior': int(u_compradas_ant),
            'variacion_pct': variacion_pct,
            'alertas_criticas': alertas_vel.count(),
            'tallas_agotadas_criticas': alertas_q.count(),
            'badge': badge,
        })

    filas.sort(key=lambda x: -x['score_rotacion'])
    for i, row in enumerate(filas, start=1):
        row['ranking_rotacion'] = i

    total_pedir = sum(r['unidades_a_pedir'] for r in filas)
    total_monto = sum(r['monto_estimado'] for r in filas)
    mejor_sem = min(filas, key=lambda x: x['mediana_semanas_agotar']) if filas else None
    mayor_vol = max(filas, key=lambda x: x['unidades_a_pedir']) if filas else None

    return JsonResponse({
        'categoria': canonical,
        'categoria_solicitada': categoria_param,
        'temporada': temporada,
        'anio': anio,
        'genero': genero_filtro or None,
        'resumen': {
            'total_unidades_a_pedir': total_pedir,
            'total_monto_estimado': total_monto,
            'mejor_rotacion_marca': mejor_sem['marca'] if mejor_sem else None,
            'mejor_rotacion_semanas': mejor_sem['mediana_semanas_agotar'] if mejor_sem else None,
            'mayor_volumen_marca': mayor_vol['marca'] if mayor_vol else None,
            'mayor_volumen_unidades': mayor_vol['unidades_a_pedir'] if mayor_vol else None,
        },
        'marcas': filas,
    }, json_dumps_params={'default': _decimal_to_float})


# ──────────────────────────────────────────────────────────────
#  API: Ranking global por Marca (con filtro opcional de categoría)
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_analisis_marca(request):
    """
    Ranking de marcas agregando todas las categorías.
    GET ?categoria=ZAPATILLAS&temporada=verano&anio=2025
    VelocidadHistorica no tiene campo anio → sólo filtramos por temporada.
    """
    t_def, a_def = _prediccion_default_temporada_anio()
    temporada = request.GET.get('temporada', '').strip() or t_def
    anio_str = request.GET.get('anio', '').strip()
    anio = int(anio_str) if anio_str else a_def
    categoria_param = request.GET.get('categoria', '').strip()
    canonical = _resolver_categoria_nombre(categoria_param) if categoria_param else None

    from collections import defaultdict

    # ── VelocidadHistorica (NO tiene campo anio) ─────────────────
    vh_base = VelocidadHistorica.objects.filter(temporada__iexact=temporada)
    if canonical:
        vh_base = vh_base.filter(
            Q(categoria__iexact=canonical) | Q(categoria__icontains=canonical)
        )

    marca_ids = list(
        vh_base.exclude(marca__isnull=True)
        .values_list('marca_id', flat=True)
        .distinct()
    )
    if not marca_ids:
        return JsonResponse(
            {'marcas': [], 'resumen': {}, 'categoria_filtro': canonical, 'temporada': temporada, 'anio': anio},
            json_dumps_params={'default': _decimal_to_float},
        )

    # ── Q helpers para categoría padre/hijo ──────────────────────
    q_cat_abc    = _q_categoria_canonical(canonical, 'articulo__') if canonical else Q()
    q_cat_alerta = _q_categoria_canonical(canonical, 'articulo__') if canonical else Q()

    # ── Ventas desde Movimientos (temporada actual y anterior) ────
    from .models.inventario import Movimientos_Producto
    from django.db.models.functions import Abs as AbsFunc

    _VENTAS_TIPOS = ['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET']

    def _bulk_ventas(anio_temp):
        """Suma de ventas de la temporada por marca_id."""
        rows = (
            Movimientos_Producto.objects
            .filter(
                ProductoTalla__producto__atributo1_id__in=marca_ids,
                ProductoTalla__producto__temporada__iexact=temporada,
                ProductoTalla__producto__anio_temporada=anio_temp,
                concepto__in=_VENTAS_TIPOS,
            )
            .values('ProductoTalla__producto__atributo1_id')
            .annotate(total=Sum(AbsFunc(F('cantidad'))))
        )
        return {r['ProductoTalla__producto__atributo1_id']: int(r['total'] or 0) for r in rows}

    ventas_act_map = _bulk_ventas(anio)
    ventas_ant_map = _bulk_ventas(anio - 1)

    # ── Prefetch alertas críticas por marca_id ────────────────────
    alertas_raw = (
        AlertaVelocidad.objects
        .filter(q_cat_alerta, resuelta=False, urgencia='CRITICA',
                articulo__atributo1_id__in=marca_ids)
        .values('articulo__atributo1_id')
        .annotate(n=Count('id'))
    )
    alertas_map = {r['articulo__atributo1_id']: r['n'] for r in alertas_raw}

    # ── Construir filas por marca ─────────────────────────────────
    filas = []
    for mid in marca_ids:
        vh_rows = list(vh_base.filter(marca_id=mid).select_related('marca'))
        if not vh_rows:
            continue
        wv = _weighted_velocidad_rows(vh_rows)
        if not wv:
            continue

        mediana = wv['p50']
        sell    = wv['sellthrough']
        marca_nombre = vh_rows[0].marca.valor if vh_rows[0].marca else ''
        categorias = list({r.categoria for r in vh_rows if r.categoria})

        # ABC aggregate
        abc_agg = ClasificacionABC.objects.filter(
            q_cat_abc,
            articulo__atributo1_id=mid,
            temporada=temporada,
            anio=anio,
        ).aggregate(
            n_a=Count('id', filter=Q(clasificacion_abc='A')),
            n_b=Count('id', filter=Q(clasificacion_abc='B')),
            n_c=Count('id', filter=Q(clasificacion_abc='C')),
        )
        n_a = abc_agg['n_a'] or 0
        n_b = abc_agg['n_b'] or 0
        n_c = abc_agg['n_c'] or 0
        n_tot = n_a + n_b + n_c
        factor_abc = (n_a * 1.0 + n_b * 0.7 + n_c * 0.3) / n_tot if n_tot else 1.0

        # Sugerencias aggregate
        sug_agg = SugerenciaCompra.objects.filter(
            aprobada=False,
            articulo_talle__producto__atributo1_id=mid,
        ).filter(
            Q(prediccion__temporada=temporada, prediccion__anio=anio)
            | Q(prediccion__isnull=True,
                articulo_talle__producto__temporada__iexact=temporada,
                articulo_talle__producto__anio_temporada=anio)
        ).aggregate(
            u_sugeridas=Sum('unidades_sugeridas'),
            u_stock=Sum('stock_actual'),
            u_pedir=Sum('unidades_a_pedir'),
        )
        u_sugeridas = int(sug_agg['u_sugeridas'] or 0)
        u_stock     = int(sug_agg['u_stock'] or 0)
        u_pedir     = int(sug_agg['u_pedir'] or 0)

        # Ventas temporada actual y anterior desde Movimientos
        ventas_act = ventas_act_map.get(mid, 0)
        ventas_ant = ventas_ant_map.get(mid, 0)
        variacion_pct = None
        if ventas_ant:
            variacion_pct = round((u_pedir - ventas_ant) / ventas_ant * 100, 1)

        score_f = (sell / max(mediana, 0.01)) * factor_abc
        if sell > 0.85 and mediana < 15:
            badge = 'alta_prioridad'
        elif sell < 0.50 and mediana > 20:
            badge = 'revisar'
        else:
            badge = 'normal'

        filas.append({
            'marca': marca_nombre,
            'marca_id': mid,
            'categorias': categorias,
            'mediana_semanas_agotar': round(float(mediana), 1),
            'sell_through_promedio_pct': round(float(sell) * 100, 1),
            'percentil_25_semanas': round(wv['p25'], 1),
            'percentil_75_semanas': round(wv['p75'], 1),
            'score_rotacion': round(score_f, 4),
            'badge': badge,
            'skus_clase_A': n_a,
            'skus_clase_B': n_b,
            'skus_clase_C': n_c,
            'unidades_sugeridas_total': u_sugeridas,
            'unidades_stock_actual': u_stock,
            'unidades_a_pedir': u_pedir,
            'ventas_temporada_actual': ventas_act,
            'ventas_temporada_anterior': ventas_ant,
            'variacion_vs_anterior_pct': variacion_pct,
            'alertas_criticas': alertas_map.get(mid, 0),
        })

    filas.sort(key=lambda x: -x['score_rotacion'])
    for i, f in enumerate(filas):
        f['ranking'] = i + 1

    total_pedir = sum(f['unidades_a_pedir'] for f in filas)
    mejor = min(filas, key=lambda x: x['mediana_semanas_agotar']) if filas else None
    mayor = max(filas, key=lambda x: x['unidades_a_pedir']) if filas else None

    return JsonResponse({
        'categoria_filtro': canonical,
        'temporada': temporada,
        'anio': anio,
        'resumen': {
            'total_marcas': len(filas),
            'total_unidades_a_pedir': total_pedir,
            'mejor_rotacion_marca': mejor['marca'] if mejor else None,
            'mejor_rotacion_semanas': mejor['mediana_semanas_agotar'] if mejor else None,
            'mayor_volumen_marca': mayor['marca'] if mayor else None,
            'mayor_volumen_unidades': mayor['unidades_a_pedir'] if mayor else None,
        },
        'marcas': filas,
    }, json_dumps_params={'default': _decimal_to_float})


# ──────────────────────────────────────────────────────────────
#  API: Ranking global por Proveedor/Empresa
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_analisis_proveedor(request):
    """
    Ranking de proveedores a partir de las órdenes de compra.
    Consulta desde Compras (tiene FK a Empresa directa) para evitar
    traversales profundas con F() que no soporta Django 4.2 en .values().
    GET ?temporada=verano&anio=2025
    """
    t_def, a_def = _prediccion_default_temporada_anio()
    temporada = request.GET.get('temporada', '').strip() or t_def
    anio_str  = request.GET.get('anio', '').strip()
    anio      = int(anio_str) if anio_str else a_def

    from .models.compras import Compras

    # ── OCs por empresa (query desde Compras, que tiene FK directa) ─
    ocs_base = Compras.objects.filter(
        temporada__iexact=temporada,
        estado__in=['ACTIVA', 'COMPLETADA'],
    )

    ocs_por_empresa = (
        ocs_base
        .values('empresa_id', 'empresa__nombre')
        .annotate(num_ocs=Count('id', distinct=True))
        .order_by('empresa_id')
    )

    if not ocs_por_empresa:
        return JsonResponse({
            'temporada': temporada, 'anio': anio,
            'resumen': {'total_proveedores': 0, 'total_unidades_ordenadas': 0},
            'proveedores': [],
        }, json_dumps_params={'default': _decimal_to_float})

    # IDs de las OC de la temporada
    oc_ids = list(ocs_base.values_list('id', flat=True))

    # ── Totales de stock y recibidas por empresa (desde Compras_Producto_Talla) ─
    stock_qs = (
        Compras_Producto_Talla.objects
        .filter(compra_producto__compras_id__in=oc_ids)
        .values('compra_producto__compras__empresa_id')
        .annotate(u_ord=Sum('stock'), u_rec=Sum('unidades_recibidas'),
                  num_prod=Count('producto_talla__producto_id', distinct=True))
    )
    stock_map = {r['compra_producto__compras__empresa_id']: r for r in stock_qs}

    # ── OC año anterior (misma temporada) ────────────────────────
    oc_ant_ids = list(
        Compras.objects
        .filter(temporada__iexact=temporada, fecha__year=anio - 1,
                estado__in=['ACTIVA', 'COMPLETADA'])
        .values_list('id', flat=True)
    )
    ant_map = {}
    if oc_ant_ids:
        for r in (
            Compras_Producto_Talla.objects
            .filter(compra_producto__compras_id__in=oc_ant_ids)
            .values('compra_producto__compras__empresa_id')
            .annotate(total=Sum('stock'))
        ):
            ant_map[r['compra_producto__compras__empresa_id']] = r['total']

    filas = []
    for row in ocs_por_empresa:
        eid  = row['empresa_id']
        enombre = row['empresa__nombre'] or f'Empresa #{eid}'
        st   = stock_map.get(eid, {})
        u_ord     = int(st.get('u_ord') or 0)
        u_rec     = int(st.get('u_rec') or 0)
        u_trans   = max(0, u_ord - u_rec)
        num_prod  = int(st.get('num_prod') or 0)
        ant       = int(ant_map.get(eid) or 0)
        variacion = round((u_ord - ant) / ant * 100, 1) if ant else None
        filas.append({
            'empresa_id': eid,
            'empresa': enombre,
            'num_ocs': row['num_ocs'],
            'num_productos': num_prod,
            'unidades_ordenadas': u_ord,
            'unidades_recibidas': u_rec,
            'unidades_en_transito': u_trans,
            'unidades_compradas_anterior': ant,
            'variacion_vs_anterior_pct': variacion,
        })

    filas.sort(key=lambda x: -x['unidades_ordenadas'])
    total_ord = sum(f['unidades_ordenadas'] for f in filas)

    return JsonResponse({
        'temporada': temporada,
        'anio': anio,
        'resumen': {
            'total_proveedores': len(filas),
            'total_unidades_ordenadas': total_ord,
        },
        'proveedores': filas,
    }, json_dumps_params={'default': _decimal_to_float})


# ──────────────────────────────────────────────────────────────
#  API: Desglose de artículos por marca (drill-down del ranking)
# ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def api_prediccion_marca_articulos(request):
    """
    Devuelve los artículos de una marca para la temporada dada,
    con ventas, stock, ABC y sugerencias de compra.
    GET ?marca_id=X&temporada=verano&anio=2025&categoria=ZAPATILLAS
    """
    marca_id_str = request.GET.get('marca_id', '').strip()
    if not marca_id_str:
        return JsonResponse({'error': 'marca_id requerido'}, status=400)
    try:
        marca_id = int(marca_id_str)
    except ValueError:
        return JsonResponse({'error': 'marca_id inválido'}, status=400)

    t_def, a_def = _prediccion_default_temporada_anio()
    temporada = request.GET.get('temporada', '').strip() or t_def
    anio_str  = request.GET.get('anio', '').strip()
    anio      = int(anio_str) if anio_str else a_def
    categoria_param = request.GET.get('categoria', '').strip()
    canonical = _resolver_categoria_nombre(categoria_param) if categoria_param else None

    from .models.inventario import Movimientos_Producto
    from django.db.models.functions import Abs as AbsFunc

    # ── Productos de la marca / temporada ─────────────────────────
    prods_qs = Producto.objects.filter(
        atributo1_id=marca_id,
        temporada__iexact=temporada,
        anio_temporada=anio,
        excluir_de_analitica=False,
    ).select_related('atributo1', 'atributo3', 'categoria').order_by('articulo')

    if canonical:
        from .models.catalogo import Categoria
        cats = list(Categoria.objects.filter(
            Q(nombre__iexact=canonical) | Q(nombre__icontains=canonical)
        ).values_list('id', flat=True))
        if cats:
            prods_qs = prods_qs.filter(categoria_id__in=cats)

    prod_ids = list(prods_qs.values_list('id', flat=True)[:80])
    if not prod_ids:
        # Si no hay en anio_temporada exacto, buscar sin filtro de año
        prods_qs = Producto.objects.filter(
            atributo1_id=marca_id,
            temporada__iexact=temporada,
            excluir_de_analitica=False,
        ).select_related('atributo1', 'atributo3', 'categoria').order_by('articulo')
        if canonical:
            cats = list(Categoria.objects.filter(
                Q(nombre__iexact=canonical) | Q(nombre__icontains=canonical)
            ).values_list('id', flat=True))
            if cats:
                prods_qs = prods_qs.filter(categoria_id__in=cats)
        prod_ids = list(prods_qs.values_list('id', flat=True)[:80])

    marca_nombre_ref = ''
    try:
        from .models.catalogo import AtributoOpcion
        marca_nombre_ref = AtributoOpcion.objects.get(id=marca_id).valor
    except Exception:
        pass

    if not prod_ids:
        return JsonResponse({
            'marca': marca_nombre_ref, 'marca_id': marca_id,
            'temporada': temporada, 'anio': anio,
            'resumen': {'total_articulos': 0, 'total_ventas': 0,
                        'total_ventas_anterior': 0, 'total_stock': 0, 'total_a_pedir': 0},
            'articulos': [],
        }, json_dumps_params={'default': _decimal_to_float})

    _VENTAS_TIPOS = ['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET']

    # ── Bulk: ABC por producto ────────────────────────────────────
    abc_map = {
        r['articulo_id']: r
        for r in ClasificacionABC.objects.filter(
            articulo_id__in=prod_ids, temporada=temporada, anio=anio,
        ).values('articulo_id', 'clasificacion_abc', 'clasificacion_xyz',
                 'ventas_totales_periodo', 'coeficiente_variacion', 'porcentaje_acumulado')
    }

    # ── Bulk: stock por producto ──────────────────────────────────
    stock_map = {
        r['producto_id']: int(r['total'] or 0)
        for r in Producto_Talla.objects.filter(producto_id__in=prod_ids)
        .values('producto_id').annotate(total=Sum('stock'))
    }

    # ── Bulk: ventas temporada actual ─────────────────────────────
    ventas_map = {
        r['ProductoTalla__producto_id']: int(r['total'] or 0)
        for r in Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=prod_ids,
            concepto__in=_VENTAS_TIPOS,
        ).values('ProductoTalla__producto_id')
        .annotate(total=Sum(AbsFunc(F('cantidad'))))
    }

    # ── Ventas temporada anterior (mismo artículo, anio-1) ────────
    ant_prod_ids = list(
        Producto.objects.filter(
            atributo1_id=marca_id,
            temporada__iexact=temporada,
            anio_temporada=anio - 1,
            excluir_de_analitica=False,
        ).values_list('id', flat=True)[:200]
    )
    ventas_ant_total_marca = 0
    if ant_prod_ids:
        ventas_ant_total_marca = int(
            Movimientos_Producto.objects.filter(
                ProductoTalla__producto_id__in=ant_prod_ids,
                concepto__in=_VENTAS_TIPOS,
            ).aggregate(t=Sum(AbsFunc(F('cantidad'))))['t'] or 0
        )

    # ── Bulk: sugerencias por producto ────────────────────────────
    sug_map = {
        r['articulo_talle__producto_id']: r
        for r in SugerenciaCompra.objects.filter(
            articulo_talle__producto_id__in=prod_ids, aprobada=False,
        ).values('articulo_talle__producto_id')
        .annotate(u_pedir=Sum('unidades_a_pedir'), u_stock=Sum('stock_actual'))
    }

    # ── Construir lista de artículos ──────────────────────────────
    articulos = []
    for prod in prods_qs.filter(id__in=prod_ids):
        pid = prod.id
        abc  = abc_map.get(pid, {})
        stk  = stock_map.get(pid, 0)
        ven  = ventas_map.get(pid, 0)
        sug  = sug_map.get(pid, {})
        u_pedir = int(sug.get('u_pedir') or 0)

        # Sell-through estimado real: vendido / (vendido + stock)
        total_disp = ven + stk
        st_est = round(ven / total_disp * 100, 1) if total_disp > 0 else None

        # Rotación implícita: si conocemos semanas activo desde StockInicialTemporada
        sit = StockInicialTemporada.objects.filter(producto=prod).order_by('-anio').first()
        semanas_activo = None
        if sit and sit.fecha_primer_ingreso and ven > 0 and stk >= 0:
            from datetime import date as date_cls
            dias = (date_cls.today() - sit.fecha_primer_ingreso).days
            semanas = max(1, dias / 7)
            semanas_activo = round(semanas, 1)

        articulos.append({
            'id': pid,
            'articulo': prod.articulo,
            'categoria': prod.categoria.nombre if prod.categoria else '',
            'genero': prod.atributo3.valor if prod.atributo3 else '',
            'abc': abc.get('clasificacion_abc') or '—',
            'xyz': abc.get('clasificacion_xyz') or '—',
            'ventas_abc': int(abc.get('ventas_totales_periodo') or 0),
            'cv': round(float(abc.get('coeficiente_variacion') or 0), 2),
            'pct_ingreso': round(float(abc.get('porcentaje_acumulado') or 0) * 100, 1),
            'stock_actual': stk,
            'ventas_temporada': ven,
            'sell_through_estimado_pct': st_est,
            'semanas_activo': semanas_activo,
            'unidades_a_pedir': u_pedir,
        })

    articulos.sort(key=lambda x: -x['ventas_temporada'])

    total_ventas = sum(a['ventas_temporada'] for a in articulos)
    total_stock  = sum(a['stock_actual'] for a in articulos)
    total_pedir  = sum(a['unidades_a_pedir'] for a in articulos)

    return JsonResponse({
        'marca': marca_nombre_ref,
        'marca_id': marca_id,
        'temporada': temporada,
        'anio': anio,
        'resumen': {
            'total_articulos': len(articulos),
            'total_ventas': total_ventas,
            'total_ventas_anterior': ventas_ant_total_marca,
            'total_stock': total_stock,
            'total_a_pedir': total_pedir,
        },
        'articulos': articulos,
    }, json_dumps_params={'default': _decimal_to_float})
