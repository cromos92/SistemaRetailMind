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
)

logger = logging.getLogger('app')


def _get_sucursal_empresa(request):
    suc_id = request.session.get('idSucursalActual')
    emp_id = request.session.get('idEmpresaActual')
    return suc_id, emp_id


def _parse_date_range(request):
    hoy = timezone.now().date()
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


