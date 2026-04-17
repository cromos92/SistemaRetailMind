"""
API views for KPI dashboards: Documentos/DTE, Caja/Arqueos,
Requerimientos, and CRM.
"""
import logging
from datetime import timedelta
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import (
    Sum, Count, Avg, Q, F, Case, When, Value,
    IntegerField, CharField, DecimalField,
)
from django.db.models.functions import (
    TruncMonth, TruncDate, Coalesce, ExtractMonth, ExtractYear,
)
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import (
    Dte, Dte_Productos, Dte_Incidencia,
    ArqueoCaja,
    Requerimiento,
    Cliente, Proveedor, ContactoEmpresa, Empresa,
    Sucursal,
    Productos_Recepcionados,
    Movimientos_Producto,
)

logger = logging.getLogger('app')


def _get_sucursal_empresa(request):
    suc_id = request.session.get('idSucursalActual')
    emp_id = request.session.get('idEmpresaActual')
    return suc_id, emp_id


def _parse_date_range(request):
    hoy = timezone.localdate()
    inicio = request.GET.get('fecha_inicio')
    fin = request.GET.get('fecha_fin')
    try:
        from datetime import date
        if inicio:
            parts = inicio.split('-')
            inicio = date(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            inicio = hoy.replace(day=1)
        if fin:
            parts = fin.split('-')
            fin = date(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            fin = hoy
    except (ValueError, IndexError):
        inicio = hoy.replace(day=1)
        fin = hoy
    return inicio, fin


# ==================== DOCUMENTOS / DTE ====================

@login_required
@require_GET
def dashboard_documentos(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_documentos.html')


@login_required
@require_GET
def api_dashboard_documentos(request):
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    base_qs = Dte.objects.filter(descartado=False)
    if emp_id:
        base_qs = base_qs.filter(
            Q(emisor_id=emp_id) | Q(receptor_id=emp_id)
        )
    if suc_id:
        base_qs = base_qs.filter(sucursal_id=suc_id)

    periodo_qs = base_qs.filter(fecha_emision__range=[inicio, fin])

    total_dtes = periodo_qs.count()
    monto_total = periodo_qs.aggregate(
        total=Coalesce(Sum('monto_con_iva'), 0, output_field=DecimalField())
    )['total']

    por_tipo = list(
        periodo_qs.values('tipo_documento')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('-cantidad')
    )
    for t in por_tipo:
        t['monto'] = float(t['monto'] or 0)

    por_estado = list(
        periodo_qs.values('estado_dte')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    aceptados = periodo_qs.filter(
        estado_dte__in=['ACEPTADO', 'RECEPCIONADO_COMPLETO']
    ).count()
    pct_aceptados = round((aceptados / total_dtes * 100) if total_dtes else 0, 1)

    facturas = periodo_qs.filter(tipo_documento='FACTURA ELECTRONICA').count()
    notas_credito = periodo_qs.filter(tipo_documento='NOTA DE CREDITO').count()
    guias = periodo_qs.filter(tipo_documento='GUIA').count()
    boletas = periodo_qs.filter(tipo_documento='BOLETA ELECTRONICA').count()

    # --- Nuevos KPIs retail ---
    pendientes_recepcion = 0
    if emp_id:
        pendientes_recepcion = periodo_qs.filter(
            receptor_id=emp_id, estado_dte='EMITIDO'
        ).count()

    monto_pendiente_pago = float(periodo_qs.filter(
        estado_pago='PENDIENTE'
    ).aggregate(
        total=Coalesce(Sum('monto_con_iva'), 0, output_field=DecimalField())
    )['total'])

    dtes_vencidos = periodo_qs.filter(estado_pago='VENCIDO').count()

    incidencias_qs = Dte_Incidencia.objects.filter(
        dte__descartado=False,
        estado__in=['PENDIENTE', 'EN_GESTION'],
    )
    if emp_id:
        incidencias_qs = incidencias_qs.filter(
            Q(dte__emisor_id=emp_id) | Q(dte__receptor_id=emp_id)
        )
    if suc_id:
        incidencias_qs = incidencias_qs.filter(dte__sucursal_id=suc_id)
    incidencias_abiertas = incidencias_qs.count()

    dias_credito_avg = periodo_qs.aggregate(
        avg=Coalesce(Avg('diasCredito'), 0, output_field=DecimalField())
    )['avg']
    dias_credito_promedio = round(float(dias_credito_avg), 1)

    ticket_promedio = round(float(monto_total) / total_dtes, 0) if total_dtes else 0
    tasa_nc = round((notas_credito / facturas * 100) if facturas else 0, 1)

    # --- Evolución mensual ---
    evolucion = list(
        periodo_qs.annotate(mes=TruncMonth('fecha_emision'))
        .values('mes')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('mes')
    )
    for e in evolucion:
        e['mes'] = e['mes'].strftime('%Y-%m') if e['mes'] else ''
        e['monto'] = float(e['monto'] or 0)

    top_emisores = list(
        periodo_qs.values('emisor__nombre')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('-monto')[:10]
    )
    for t in top_emisores:
        t['monto'] = float(t['monto'] or 0)

    por_transaccion = list(
        periodo_qs.values('tipo_transaccion')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('-cantidad')
    )
    for t in por_transaccion:
        t['monto'] = float(t['monto'] or 0)

    # --- Estado de pagos ---
    por_estado_pago = list(
        periodo_qs.values('estado_pago')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('-cantidad')
    )
    for p in por_estado_pago:
        p['monto'] = float(p['monto'] or 0)

    # --- Por sucursal ---
    por_sucursal = list(
        periodo_qs.filter(sucursal__isnull=False)
        .values('sucursal__alias')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('-cantidad')[:10]
    )
    for s in por_sucursal:
        s['monto'] = float(s['monto'] or 0)

    # --- Top proveedores (compras) ---
    top_proveedores = list(
        periodo_qs.filter(tipo_transaccion='COMPRA')
        .values('emisor__nombre')
        .annotate(
            cantidad=Count('id'),
            monto=Sum('monto_con_iva'),
            pendientes=Count('id', filter=Q(estado_pago='PENDIENTE')),
        )
        .order_by('-monto')[:10]
    )
    for tp in top_proveedores:
        tp['monto'] = float(tp['monto'] or 0)
        tp['pct_pendientes'] = round(
            (tp['pendientes'] / tp['cantidad'] * 100) if tp['cantidad'] else 0, 1
        )

    # --- Evolución compras vs ventas mensual ---
    evolucion_transaccion = list(
        periodo_qs.annotate(mes=TruncMonth('fecha_emision'))
        .values('mes', 'tipo_transaccion')
        .annotate(cantidad=Count('id'), monto=Sum('monto_con_iva'))
        .order_by('mes')
    )
    for e in evolucion_transaccion:
        e['mes'] = e['mes'].strftime('%Y-%m') if e['mes'] else ''
        e['monto'] = float(e['monto'] or 0)

    # --- Flujo Despacho/Recepción (pipeline traspasos) ---
    traspasos_qs = periodo_qs.filter(tipo_transaccion='TRASPASO')
    flujo_total = traspasos_qs.count()
    flujo = {
        'total': flujo_total,
        'emitidos': traspasos_qs.filter(estado_dte='EMITIDO').count(),
        'recepcionado_completo': traspasos_qs.filter(estado_dte='RECEPCIONADO_COMPLETO').count(),
        'recepcionado_parcial': traspasos_qs.filter(estado_dte='RECEPCIONADO_PARCIAL').count(),
        'en_regularizacion': traspasos_qs.filter(estado_dte='EN_REGULARIZACION').count(),
        'rechazados': traspasos_qs.filter(estado_dte='RECHAZADO').count(),
        'anulados': traspasos_qs.filter(estado_dte__in=['ANULADO', 'CANCELADO']).count(),
        'unidades_despachadas': traspasos_qs.aggregate(
            u=Coalesce(Sum('unidades_productos'), 0)
        )['u'],
    }

    flujo_por_origen = list(
        traspasos_qs.values('sucursal__alias')
        .annotate(
            total=Count('id'),
            pendientes=Count('id', filter=Q(estado_dte='EMITIDO')),
            completados=Count('id', filter=Q(estado_dte='RECEPCIONADO_COMPLETO')),
            parciales=Count('id', filter=Q(estado_dte__in=['RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION'])),
            rechazados=Count('id', filter=Q(estado_dte='RECHAZADO')),
            unidades=Sum('unidades_productos'),
        )
        .order_by('-total')[:10]
    )

    # --- Variación vs mes anterior (calendario real) ---
    mes_anterior_fin = inicio - timedelta(days=1)
    mes_anterior_inicio = mes_anterior_fin.replace(day=1)
    total_anterior = base_qs.filter(
        fecha_emision__range=[mes_anterior_inicio, mes_anterior_fin]
    ).count()
    variacion = round(
        ((total_dtes - total_anterior) / total_anterior * 100)
        if total_anterior else 0, 1
    )

    return JsonResponse({
        'success': True,
        'kpis': {
            'total_dtes': total_dtes,
            'monto_total': float(monto_total),
            'facturas': facturas,
            'notas_credito': notas_credito,
            'guias': guias,
            'boletas': boletas,
            'pct_aceptados': pct_aceptados,
            'variacion_mes': variacion,
            'pendientes_recepcion': pendientes_recepcion,
            'monto_pendiente_pago': monto_pendiente_pago,
            'dtes_vencidos': dtes_vencidos,
            'incidencias_abiertas': incidencias_abiertas,
            'dias_credito_promedio': dias_credito_promedio,
            'ticket_promedio': ticket_promedio,
            'tasa_nc': tasa_nc,
        },
        'por_tipo': por_tipo,
        'por_estado': por_estado,
        'por_estado_pago': por_estado_pago,
        'evolucion': evolucion,
        'evolucion_transaccion': evolucion_transaccion,
        'top_emisores': top_emisores,
        'por_transaccion': por_transaccion,
        'por_sucursal': por_sucursal,
        'top_proveedores': top_proveedores,
        'flujo': flujo,
        'flujo_por_origen': flujo_por_origen,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })




# ==================== REQUERIMIENTOS ====================

@login_required
@require_GET
def dashboard_requerimientos(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_requerimientos.html')


@login_required
@require_GET
def api_dashboard_requerimientos(request):
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    base_qs = Requerimiento.objects.all()
    if suc_id:
        base_qs = base_qs.filter(sucursal_id=suc_id)
    elif emp_id:
        from .models import Sucursal
        sucursales_emp = Sucursal.objects.filter(empresa_id=emp_id).values_list('id', flat=True)
        base_qs = base_qs.filter(sucursal_id__in=sucursales_emp)

    periodo_qs = base_qs.filter(fecha_creacion__date__range=[inicio, fin])

    total = periodo_qs.count()

    por_estado = {}
    for row in periodo_qs.values('estado').annotate(c=Count('id')):
        por_estado[row['estado']] = row['c']

    por_tipo = list(
        periodo_qs.values('tipo')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    por_prioridad = list(
        periodo_qs.values('prioridad')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    resueltos = periodo_qs.filter(
        estado__in=['APROBADO', 'RECHAZADO'],
        fecha_resolucion__isnull=False,
    )
    from django.db.models import ExpressionWrapper, DurationField
    tiempos = resueltos.annotate(
        duracion=ExpressionWrapper(
            F('fecha_resolucion') - F('fecha_creacion'),
            output_field=DurationField()
        )
    )
    avg_days = 0
    if tiempos.exists():
        total_seconds = sum(
            (t.duracion.total_seconds() for t in tiempos if t.duracion), start=0.0
        )
        avg_days = round(total_seconds / tiempos.count() / 86400, 1)

    evolucion = list(
        periodo_qs.annotate(mes=TruncMonth('fecha_creacion'))
        .values('mes')
        .annotate(cantidad=Count('id'))
        .order_by('mes')
    )
    for e in evolucion:
        e['mes'] = e['mes'].strftime('%Y-%m') if e['mes'] else ''

    esperando = base_qs.filter(estado='ESPERANDO_RESPUESTA')
    sin_respuesta_7d = esperando.filter(
        fecha_envio_proveedor__lte=timezone.now() - timedelta(days=7),
        fecha_respuesta_proveedor__isnull=True,
    ).count()

    por_sucursal = list(
        periodo_qs.values('sucursal__alias')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    top_proveedores = list(
        periodo_qs.filter(proveedor__isnull=False)
        .values('proveedor__nombre')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')[:10]
    )

    return JsonResponse({
        'success': True,
        'kpis': {
            'total': total,
            'pendientes': por_estado.get('PENDIENTE', 0),
            'esperando_respuesta': por_estado.get('ESPERANDO_RESPUESTA', 0),
            'aprobados': por_estado.get('APROBADO', 0),
            'rechazados': por_estado.get('RECHAZADO', 0),
            'cancelados': por_estado.get('CANCELADO', 0),
            'tiempo_promedio_dias': avg_days,
            'sin_respuesta_7d': sin_respuesta_7d,
        },
        'por_estado': [
            {'estado': k, 'cantidad': v} for k, v in por_estado.items()
        ],
        'por_tipo': por_tipo,
        'por_prioridad': por_prioridad,
        'evolucion': evolucion,
        'por_sucursal': por_sucursal,
        'top_proveedores': top_proveedores,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })


# ==================== DESPACHOS / RECEPCIONES ====================

@login_required
@require_GET
def dashboard_despachos(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_despachos.html')


@login_required
@require_GET
def api_dashboard_despachos(request):
    """Analytics detallados de despachos, recepciones y regularizaciones."""
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    # Base: DTEs de traspaso no descartados
    traspasos_qs = Dte.objects.filter(
        descartado=False,
        tipo_transaccion='TRASPASO',
        fecha_emision__range=[inicio, fin],
    )
    if emp_id:
        traspasos_qs = traspasos_qs.filter(
            Q(emisor_id=emp_id) | Q(receptor_id=emp_id)
        )
    if suc_id:
        traspasos_qs = traspasos_qs.filter(
            Q(sucursal_id=suc_id) | Q(dte_movimientos__sucursal_destino_id=suc_id)
        ).distinct()

    total_traspasos = traspasos_qs.count()

    # --- KPIs principales ---
    por_estado_dte = dict(
        traspasos_qs.values_list('estado_dte').annotate(c=Count('id')).values_list('estado_dte', 'c')
    )

    # Recepciones base
    rec_qs = Productos_Recepcionados.objects.filter(
        dte__isnull=False,
        dte__descartado=False,
        dte__tipo_transaccion='TRASPASO',
    )
    if emp_id:
        rec_qs = rec_qs.filter(
            Q(dte__emisor_id=emp_id) | Q(dte__receptor_id=emp_id)
        )
    if suc_id:
        rec_qs = rec_qs.filter(
            Q(dte__sucursal_id=suc_id) | Q(dte__dte_movimientos__sucursal_destino_id=suc_id)
        ).distinct()

    rec_periodo = rec_qs.filter(
        Q(fecha_recepcion__date__range=[inicio, fin]) |
        Q(fecha_recepcion__isnull=True, dte__fecha_emision__range=[inicio, fin])
    )

    total_recepciones = rec_periodo.count()
    total_ok = rec_periodo.filter(estado='RECEPCIONADO_OK').count()
    tasa_exito = round((total_ok / total_recepciones * 100) if total_recepciones else 0, 1)

    total_faltantes = rec_periodo.filter(estado__in=['FALTANTE', 'RECEPCIONADO_PARCIAL']).count()
    total_danados = rec_periodo.filter(estado='RECEPCIONADO_DANADO').count()
    total_sobrantes = rec_periodo.filter(estado__in=['RECEPCIONADO_SOBRANTE', 'SOBRANTE_PENDIENTE']).count()
    total_regularizados = rec_periodo.filter(estado='REGULARIZADO').count()
    total_pendientes = rec_periodo.filter(
        estado__in=['RECEPCIONADO_PARCIAL', 'RECEPCIONADO_DANADO', 'FALTANTE',
                    'EN_REGULARIZACION', 'EN_SOLICITUD_REGULARIZACION',
                    'RECEPCIONADO_SOBRANTE', 'SOBRANTE_PENDIENTE']
    ).count()

    # Tiempo promedio emisión a recepción
    dtes_con_recepcion = traspasos_qs.filter(fecha_recepcion__isnull=False)
    avg_dias_raw = None
    if dtes_con_recepcion.exists():
        from django.db.models import ExpressionWrapper, DurationField
        dtes_con_recepcion = dtes_con_recepcion.annotate(
            dias_dur=ExpressionWrapper(
                F('fecha_recepcion') - F('fecha_emision'),
                output_field=DurationField()
            )
        )
        avg_dias_raw = dtes_con_recepcion.aggregate(avg=Avg('dias_dur'))['avg']
    avg_dias = round(avg_dias_raw.days, 1) if avg_dias_raw else 0

    # --- Pipeline ---
    pipeline = {
        'emitidos': por_estado_dte.get('EMITIDO', 0),
        'recepcionado_completo': por_estado_dte.get('RECEPCIONADO_COMPLETO', 0),
        'recepcionado_parcial': por_estado_dte.get('RECEPCIONADO_PARCIAL', 0),
        'recepcionado_sobrante': por_estado_dte.get('RECEPCIONADO_SOBRANTE', 0),
        'en_regularizacion': por_estado_dte.get('EN_REGULARIZACION', 0),
        'rechazados': por_estado_dte.get('RECHAZADO', 0),
        'anulados': por_estado_dte.get('ANULADO', 0) + por_estado_dte.get('CANCELADO', 0),
    }

    # --- Por sucursal (origen) ---
    por_sucursal = []
    suc_data = rec_periodo.filter(dte__sucursal__isnull=False).values(
        'dte__sucursal__alias'
    ).annotate(
        total=Count('id'),
        ok=Count('id', filter=Q(estado='RECEPCIONADO_OK')),
        faltantes=Count('id', filter=Q(estado__in=['FALTANTE', 'RECEPCIONADO_PARCIAL'])),
        danados=Count('id', filter=Q(estado='RECEPCIONADO_DANADO')),
        sobrantes=Count('id', filter=Q(estado__in=['RECEPCIONADO_SOBRANTE', 'SOBRANTE_PENDIENTE'])),
    ).order_by('-total')[:15]

    for s in suc_data:
        total = s['total'] or 1
        por_sucursal.append({
            'sucursal': s['dte__sucursal__alias'],
            'total': s['total'],
            'ok': s['ok'],
            'pct_ok': round(s['ok'] / total * 100, 1),
            'faltantes': s['faltantes'],
            'danados': s['danados'],
            'sobrantes': s['sobrantes'],
        })

    # --- Tendencia mensual de problemas ---
    tendencia = list(
        rec_periodo.filter(fecha_recepcion__isnull=False).annotate(
            mes=TruncMonth('fecha_recepcion')
        ).values('mes').annotate(
            total=Count('id'),
            ok=Count('id', filter=Q(estado='RECEPCIONADO_OK')),
            faltantes=Count('id', filter=Q(estado__in=['FALTANTE', 'RECEPCIONADO_PARCIAL'])),
            danados=Count('id', filter=Q(estado='RECEPCIONADO_DANADO')),
            sobrantes=Count('id', filter=Q(estado__in=['RECEPCIONADO_SOBRANTE', 'SOBRANTE_PENDIENTE'])),
        ).order_by('mes')
    )
    for t in tendencia:
        t['mes'] = t['mes'].strftime('%Y-%m') if t['mes'] else ''

    # --- Regularizaciones pendientes (lista) ---
    reg_pendientes = list(
        rec_qs.filter(
            estado__in=['EN_REGULARIZACION', 'EN_SOLICITUD_REGULARIZACION',
                        'RECEPCIONADO_SOBRANTE', 'SOBRANTE_PENDIENTE',
                        'RECEPCIONADO_PARCIAL', 'RECEPCIONADO_DANADO', 'FALTANTE']
        ).values(
            'dte__numero_documento', 'dte__sucursal__alias', 'dte__fecha_emision'
        ).annotate(
            productos_pendientes=Count('id')
        ).order_by('-dte__fecha_emision')[:20]
    )
    for r in reg_pendientes:
        fecha_emi = r['dte__fecha_emision']
        if fecha_emi:
            r['dias_pendiente'] = (timezone.localdate() - fecha_emi).days
        else:
            r['dias_pendiente'] = 0
        r['dte__fecha_emision'] = str(fecha_emi) if fecha_emi else ''

    # --- DTEs con proceso incompleto (sin filtro de fecha - muestra TODO lo pendiente) ---
    dtes_incompletos_qs = Dte.objects.filter(
        descartado=False,
        tipo_transaccion='TRASPASO',
    )
    if emp_id:
        dtes_incompletos_qs = dtes_incompletos_qs.filter(
            Q(emisor_id=emp_id) | Q(receptor_id=emp_id)
        )
    if suc_id:
        dtes_incompletos_qs = dtes_incompletos_qs.filter(
            Q(sucursal_id=suc_id) | Q(dte_movimientos__sucursal_destino_id=suc_id)
        ).distinct()

    estados_incompletos = ['EMITIDO', 'RECHAZADO', 'RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION']
    from django.db.models import Min
    dtes_pendientes_raw = (
        dtes_incompletos_qs
        .filter(estado_dte__in=estados_incompletos)
        .values('estado_dte')
        .annotate(
            cantidad=Count('id'),
            fecha_mas_antigua=Min('fecha_emision'),
        )
        .order_by('estado_dte')
    )

    ESTADO_LABELS = {
        'EMITIDO': 'Sin recepcionar',
        'RECHAZADO': 'Rechazado (sin resolver)',
        'RECEPCIONADO_PARCIAL': 'Recepcionado parcial',
        'EN_REGULARIZACION': 'En regularización',
    }
    ESTADO_ACCIONES = {
        'EMITIDO': {'texto': 'Ir a recepcionar', 'url': '/app/recepcion-dte/'},
        'RECHAZADO': {'texto': 'Ver rechazados', 'url': '/app/recepcion-dte/'},
        'RECEPCIONADO_PARCIAL': {'texto': 'Regularizar', 'url': '/app/regularizar-recepciones/'},
        'EN_REGULARIZACION': {'texto': 'Regularizar', 'url': '/app/regularizar-recepciones/'},
    }

    hoy_incompletos = timezone.localdate()
    dtes_incompletos = []
    for row in dtes_pendientes_raw:
        estado = row['estado_dte']
        fecha_antigua = row['fecha_mas_antigua']
        dias = (hoy_incompletos - fecha_antigua).days if fecha_antigua else 0
        dtes_incompletos.append({
            'estado': estado,
            'label': ESTADO_LABELS.get(estado, estado),
            'cantidad': row['cantidad'],
            'dias_max': dias,
            'accion': ESTADO_ACCIONES.get(estado, {}),
        })

    # Detalle: lista de los DTEs incompletos más antiguos (top 30)
    dtes_incompletos_detalle = list(
        dtes_incompletos_qs
        .filter(estado_dte__in=estados_incompletos)
        .select_related('sucursal')
        .values(
            'id', 'numero_documento', 'estado_dte',
            'fecha_emision', 'sucursal__alias',
            'motivo_rechazo',
        )
        .order_by('fecha_emision')[:30]
    )
    hoy = timezone.localdate()
    for d in dtes_incompletos_detalle:
        fecha = d['fecha_emision']
        d['dias_pendiente'] = (hoy - fecha).days if fecha else 0
        d['fecha_emision'] = str(fecha) if fecha else ''
        d['estado_label'] = ESTADO_LABELS.get(d['estado_dte'], d['estado_dte'])

    return JsonResponse({
        'kpis': {
            'total_traspasos': total_traspasos,
            'total_recepciones': total_recepciones,
            'tasa_exito': tasa_exito,
            'avg_dias_recepcion': avg_dias,
            'total_pendientes': total_pendientes,
            'total_faltantes': total_faltantes,
            'total_danados': total_danados,
            'total_sobrantes': total_sobrantes,
            'total_regularizados': total_regularizados,
        },
        'pipeline': pipeline,
        'por_sucursal': por_sucursal,
        'tendencia': tendencia,
        'regularizaciones_pendientes': reg_pendientes,
        'dtes_incompletos': dtes_incompletos,
        'dtes_incompletos_detalle': dtes_incompletos_detalle,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })
