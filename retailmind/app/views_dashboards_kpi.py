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
    Dte, Dte_Productos,
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
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    mes_anterior_inicio = (inicio - timedelta(days=30)).replace(day=1)
    mes_anterior_fin = inicio - timedelta(days=1)
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
        },
        'por_tipo': por_tipo,
        'por_estado': por_estado,
        'evolucion': evolucion,
        'top_emisores': top_emisores,
        'por_transaccion': por_transaccion,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })


# ==================== CAJA / ARQUEOS ====================

@login_required
@require_GET
def dashboard_caja(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_caja.html')


@login_required
@require_GET
def api_dashboard_caja(request):
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    base_qs = ArqueoCaja.objects.all()
    if suc_id:
        base_qs = base_qs.filter(sucursal_id=suc_id)

    periodo_qs = base_qs.filter(fecha_arqueo__range=[inicio, fin])

    total_arqueos = periodo_qs.count()

    agg = periodo_qs.aggregate(
        dif_total=Coalesce(Sum('diferencia_efectivo'), 0),
        dif_abs_total=Coalesce(Sum(
            Case(
                When(diferencia_efectivo__gte=0, then=F('diferencia_efectivo')),
                default=-F('diferencia_efectivo'),
                output_field=IntegerField(),
            )
        ), 0),
        venta_total=Coalesce(Sum('venta_total_teorica'), 0),
        efectivo_total=Coalesce(Sum('total_efectivo_teorico'), 0),
        prom_efectivo=Coalesce(Avg('total_efectivo_fisico'), 0),
        prom_diferencia=Coalesce(Avg('diferencia_efectivo'), 0),
        dif_transbank=Coalesce(Sum('diferencia_transbank'), 0),
    )

    cerrados_ok = periodo_qs.filter(estado='CERRADO').count()
    con_diferencias = periodo_qs.filter(estado='CON_DIFERENCIAS').count()
    pct_ok = round((cerrados_ok / total_arqueos * 100) if total_arqueos else 0, 1)

    por_estado = list(
        periodo_qs.values('estado')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    diferencias_diarias = list(
        periodo_qs.values('fecha_arqueo')
        .annotate(
            diferencia=Sum('diferencia_efectivo'),
            venta=Sum('venta_total_teorica'),
        )
        .order_by('fecha_arqueo')
    )
    for d in diferencias_diarias:
        d['fecha_arqueo'] = str(d['fecha_arqueo'])

    metodos = {
        'Efectivo': periodo_qs.aggregate(t=Coalesce(Sum('total_efectivo_teorico'), 0))['t'],
        'Tarjeta Debito': periodo_qs.aggregate(t=Coalesce(Sum('total_tarjeta_debito_teorico'), 0))['t'],
        'Tarjeta Credito': periodo_qs.aggregate(t=Coalesce(Sum('total_tarjeta_credito_teorico'), 0))['t'],
        'Transbank': periodo_qs.aggregate(t=Coalesce(Sum('total_transbank_teorico'), 0))['t'],
        'Transferencia': periodo_qs.aggregate(t=Coalesce(Sum('total_transferencia_teorico'), 0))['t'],
        'Convenio': periodo_qs.aggregate(t=Coalesce(Sum('total_convenio_teorico'), 0))['t'],
    }

    por_sucursal = list(
        periodo_qs.values('sucursal__alias')
        .annotate(
            arqueos=Count('id'),
            diferencia=Sum('diferencia_efectivo'),
            venta=Sum('venta_total_teorica'),
        )
        .order_by('-venta')
    )

    return JsonResponse({
        'success': True,
        'kpis': {
            'total_arqueos': total_arqueos,
            'diferencia_acumulada': agg['dif_total'],
            'diferencia_absoluta': agg['dif_abs_total'],
            'venta_total': agg['venta_total'],
            'efectivo_total': agg['efectivo_total'],
            'prom_efectivo': round(float(agg['prom_efectivo'])),
            'prom_diferencia': round(float(agg['prom_diferencia'])),
            'pct_cuadraturas_ok': pct_ok,
            'cerrados_ok': cerrados_ok,
            'con_diferencias': con_diferencias,
            'dif_transbank': agg['dif_transbank'],
        },
        'por_estado': por_estado,
        'diferencias_diarias': diferencias_diarias,
        'metodos_pago': metodos,
        'por_sucursal': por_sucursal,
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


# ==================== CRM ====================

@login_required
@require_GET
def dashboard_crm(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_crm.html')


@login_required
@require_GET
def api_dashboard_crm(request):
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    total_clientes = Cliente.objects.filter(activo=True).count()
    nuevos_clientes = Cliente.objects.filter(
        created_at__date__range=[inicio, fin]
    ).count()

    por_tipo_cliente = list(
        Cliente.objects.filter(activo=True)
        .values('tipo_cliente')
        .annotate(cantidad=Count('id'))
        .order_by('-cantidad')
    )

    total_proveedores = Proveedor.objects.filter(activo=True).count()
    cal_promedio = Proveedor.objects.filter(
        activo=True, calificacion__isnull=False
    ).aggregate(avg=Avg('calificacion'))['avg']
    cal_promedio = round(float(cal_promedio), 1) if cal_promedio else 0

    empresas_activas = Empresa.objects.filter(activo=True).count()
    empresas_cliente = Empresa.objects.filter(esCliente=True, activo=True).count()
    empresas_proveedor = Empresa.objects.filter(esProveedor=True, activo=True).count()

    con_contacto = Empresa.objects.filter(
        activo=True,
        contactos_crm__isnull=False,
    ).distinct().count()
    sin_contacto = empresas_activas - con_contacto

    top_empresas = list(
        Empresa.objects.filter(activo=True)
        .annotate(n_contactos=Count('contactos_crm'))
        .values('nombre', 'n_contactos')
        .order_by('-n_contactos')[:10]
    )

    evolucion_clientes = list(
        Cliente.objects.filter(
            created_at__date__range=[inicio, fin]
        ).annotate(mes=TruncMonth('created_at'))
        .values('mes')
        .annotate(cantidad=Count('id'))
        .order_by('mes')
    )
    for e in evolucion_clientes:
        e['mes'] = e['mes'].strftime('%Y-%m') if e['mes'] else ''

    por_calificacion = list(
        Proveedor.objects.filter(activo=True, calificacion__isnull=False)
        .values('calificacion')
        .annotate(cantidad=Count('id'))
        .order_by('calificacion')
    )

    return JsonResponse({
        'success': True,
        'kpis': {
            'total_clientes': total_clientes,
            'nuevos_clientes': nuevos_clientes,
            'total_proveedores': total_proveedores,
            'calificacion_promedio': cal_promedio,
            'empresas_activas': empresas_activas,
            'empresas_cliente': empresas_cliente,
            'empresas_proveedor': empresas_proveedor,
            'con_contacto': con_contacto,
            'sin_contacto': sin_contacto,
        },
        'por_tipo_cliente': por_tipo_cliente,
        'top_empresas': top_empresas,
        'evolucion_clientes': evolucion_clientes,
        'por_calificacion': por_calificacion,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })
