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


# Un requerimiento sigue pidiendo gestion mientras no se cierre. APROBADO
# tambien cuenta: el proveedor ya respondio pero todavia falta completarlo.
ESTADOS_REQ_ABIERTOS = [
    'PENDIENTE', 'EN_REVISION', 'ESPERANDO_RESPUESTA', 'EN_PROCESO', 'APROBADO',
]
ESTADOS_REQ_CERRADOS = ['COMPLETADO', 'RECHAZADO', 'CANCELADO']


@login_required
@require_GET
def api_dashboard_requerimientos(request):
    try:
        suc_id, emp_id = _get_sucursal_empresa(request)
        inicio, fin = _parse_date_range(request)

        base_qs = Requerimiento.objects.all()
        if suc_id:
            base_qs = base_qs.filter(sucursal_id=suc_id)
        elif emp_id:
            sucursales_emp = Sucursal.objects.filter(
                empresa_id=emp_id
            ).values_list('id', flat=True)
            base_qs = base_qs.filter(sucursal_id__in=sucursales_emp)

        periodo_qs = base_qs.filter(fecha_creacion__date__range=[inicio, fin])

        # Etiquetas legibles tomadas de los choices del modelo: el dashboard
        # mostraba los codigos crudos ("ESPERANDO_RESPUESTA", "PRODUCTO_FALLADO").
        lbl_estado = dict(Requerimiento._meta.get_field('estado').choices)
        lbl_tipo = dict(Requerimiento._meta.get_field('tipo').choices)
        lbl_prioridad = dict(Requerimiento._meta.get_field('prioridad').choices)

        total = periodo_qs.count()

        conteo_estado = {
            row['estado']: row['c']
            for row in periodo_qs.values('estado').annotate(c=Count('id'))
        }

        # Se listan los 8 estados del modelo, no solo los 5 que conocia el
        # dashboard: EN_REVISION, EN_PROCESO y COMPLETADO caian en un gris
        # anonimo y COMPLETADO es justamente el desenlace normal del flujo.
        por_estado = [
            {
                'estado': codigo,
                'label': lbl_estado.get(codigo, codigo),
                'cantidad': conteo_estado.get(codigo, 0),
            }
            for codigo in list(lbl_estado.keys())
            if conteo_estado.get(codigo, 0) > 0
        ]

        por_tipo = [
            {
                'tipo': row['tipo'],
                'label': lbl_tipo.get(row['tipo'], row['tipo']),
                'cantidad': row['cantidad'],
            }
            for row in periodo_qs.values('tipo')
            .annotate(cantidad=Count('id'))
            .order_by('-cantidad')
        ]

        por_prioridad = [
            {
                'prioridad': row['prioridad'],
                'label': lbl_prioridad.get(row['prioridad'], row['prioridad']),
                'cantidad': row['cantidad'],
            }
            for row in periodo_qs.values('prioridad')
            .annotate(cantidad=Count('id'))
            .order_by('-cantidad')
        ]

        # El resultado con el proveedor se lee de decision_proveedor, no del
        # estado: al completar el requerimiento el estado pasa a COMPLETADO y
        # contar por estado hacia desaparecer los aprobados del periodo.
        aprobados = periodo_qs.filter(decision_proveedor='APROBADO').count()
        rechazados = periodo_qs.filter(decision_proveedor='RECHAZADO').count()
        parciales = periodo_qs.filter(decision_proveedor='PARCIAL').count()
        con_decision = aprobados + rechazados + parciales
        tasa_aprobacion = (
            round((aprobados + parciales) * 100.0 / con_decision, 1)
            if con_decision else 0
        )

        # Tiempo de resolucion: antes se filtraba por estado APROBADO/RECHAZADO,
        # asi que los COMPLETADO (el cierre real) quedaban fuera del promedio.
        from django.db.models import ExpressionWrapper, DurationField
        duracion_media = periodo_qs.filter(
            fecha_resolucion__isnull=False
        ).aggregate(
            promedio=Avg(
                ExpressionWrapper(
                    F('fecha_resolucion') - F('fecha_creacion'),
                    output_field=DurationField(),
                )
            )
        )['promedio']
        avg_days = (
            round(duracion_media.total_seconds() / 86400, 1)
            if duracion_media else 0
        )

        evolucion = list(
            periodo_qs.annotate(mes=TruncMonth('fecha_creacion'))
            .values('mes')
            .annotate(cantidad=Count('id'))
            .order_by('mes')
        )
        for e in evolucion:
            e['mes'] = e['mes'].strftime('%Y-%m') if e['mes'] else ''

        # --- Estado operativo vivo (sin filtro de fecha) --------------------
        # Un requerimiento abierto hace 3 meses sigue abierto hoy; acotarlo al
        # periodo lo escondia justo cuando mas urgia.
        ahora = timezone.now()
        abiertos_qs = base_qs.filter(estado__in=ESTADOS_REQ_ABIERTOS)
        abiertos = abiertos_qs.count()

        sin_respuesta_7d = base_qs.filter(
            estado='ESPERANDO_RESPUESTA',
            fecha_envio_proveedor__lte=ahora - timedelta(days=7),
            fecha_respuesta_proveedor__isnull=True,
        ).count()

        # Abiertos que nunca se enviaron al proveedor: el hoyo mas caro del
        # flujo, porque no hay nadie esperando del otro lado.
        sin_enviar = abiertos_qs.filter(correo_enviado_proveedor=False).count()

        # Antiguedad de lo abierto, en la misma escala que
        # Requerimiento.nivel_urgencia. Se agrega en la base: recorrer todos los
        # abiertos en Python cargaba una instancia por fila solo para contarlas.
        corte_3d = ahora - timedelta(days=3)
        corte_7d = ahora - timedelta(days=7)
        corte_14d = ahora - timedelta(days=14)
        aging = abiertos_qs.aggregate(
            NORMAL=Count('id', filter=Q(fecha_creacion__gt=corte_3d)),
            MEDIA=Count('id', filter=Q(
                fecha_creacion__lte=corte_3d, fecha_creacion__gt=corte_7d)),
            ALTA=Count('id', filter=Q(
                fecha_creacion__lte=corte_7d, fecha_creacion__gt=corte_14d)),
            CRITICA=Count('id', filter=Q(fecha_creacion__lte=corte_14d)),
        )

        def _nivel(fecha):
            if fecha > corte_3d:
                return 'NORMAL'
            if fecha > corte_7d:
                return 'MEDIA'
            if fecha > corte_14d:
                return 'ALTA'
            return 'CRITICA'

        criticos = [
            {
                'id': req.id,
                'numero': req.numero_requerimiento,
                'tipo': lbl_tipo.get(req.tipo, req.tipo),
                'estado': req.estado,
                'estado_label': lbl_estado.get(req.estado, req.estado),
                'prioridad': req.prioridad,
                'sucursal': req.sucursal.alias if req.sucursal else '',
                'proveedor': req.proveedor.nombre if req.proveedor else 'Sin proveedor',
                'producto': req.nombre_producto,
                'dias': (ahora - req.fecha_creacion).days,
                'nivel': _nivel(req.fecha_creacion),
                'enviado': req.correo_enviado_proveedor,
            }
            for req in abiertos_qs.select_related('sucursal', 'proveedor')
            .order_by('fecha_creacion')[:15]
        ]

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
                'abiertos': abiertos,
                'pendientes': conteo_estado.get('PENDIENTE', 0),
                'en_revision': conteo_estado.get('EN_REVISION', 0),
                'esperando_respuesta': conteo_estado.get('ESPERANDO_RESPUESTA', 0),
                'en_proceso': conteo_estado.get('EN_PROCESO', 0),
                'completados': conteo_estado.get('COMPLETADO', 0),
                'cancelados': conteo_estado.get('CANCELADO', 0),
                'aprobados': aprobados,
                'rechazados': rechazados,
                'parciales': parciales,
                'tasa_aprobacion': tasa_aprobacion,
                'tiempo_promedio_dias': avg_days,
                'sin_respuesta_7d': sin_respuesta_7d,
                'sin_enviar': sin_enviar,
            },
            'por_estado': por_estado,
            'por_tipo': por_tipo,
            'por_prioridad': por_prioridad,
            'evolucion': evolucion,
            'aging': aging,
            'criticos': criticos,
            'por_sucursal': por_sucursal,
            'top_proveedores': top_proveedores,
            'periodo': {'inicio': str(inicio), 'fin': str(fin)},
        })

    except Exception as e:
        # Sin esto, un fallo devolvia HTML de error y el dashboard se quedaba
        # mudo con los KPIs en "--" y sin ninguna pista de que paso.
        logger.exception('Error en api_dashboard_requerimientos: %s', e)
        return JsonResponse(
            {'success': False, 'error': 'No se pudieron cargar los datos del dashboard.'},
            status=500,
        )


# ==================== DESPACHOS / RECEPCIONES ====================

@login_required
@require_GET
def dashboard_despachos(request):
    return render(request, 'vistas/modulo_dashboards/dashboard_despachos.html')


def _es_cd_q(prefix=None):
    """Predicado ORM canónico para 'es centro de distribución' (CD/bodega).
    Usa OR porque es_centro_distribucion y tipo_sucursal pueden divergir
    (una MIXTA/CD marcada solo por tipo se caería con el booleano crudo)."""
    p = (prefix + '__') if prefix else ''
    return Q(**{f'{p}es_centro_distribucion': True}) | Q(**{f'{p}tipo_sucursal': 'CENTRO_DISTRIBUCION'})


@login_required
@require_GET
def api_despachos_flujo(request):
    """Zona DESPACHO del dashboard: cuenta SOLO la pierna TRASPASO_SALIDA COMPLETADA
    que sale de un CD (es_compradora) hacia una sucursal destino.

    Reglas duras (verificadas contra el kardex):
    - Detectar por `concepto` (tipo_movimiento nunca vale 'TRASPASO', el save() lo pisa por signo).
    - `cantidad` de la SALIDA es NEGATIVA -> se presenta con abs(). NUNCA sumar TRASPASO_ENTRADA
      (sería la misma unidad recibida -> doble conteo).
    - Un solo qs base (con sucursal_destino__isnull=False) para que KPIs y árbol reconcilien.
    Devuelve KPIs, árbol CD->sucursal (nodes/links Sankey), matriz, cantidad por marca por
    sucursal y distribución temporal. Los traspasos legacy single-leg (sin destino) NO entran.
    """
    from django.db.models import Max
    from django.db.models.functions import TruncWeek

    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)
    granularidad = request.GET.get('g', 'mes')
    cd_filtro = request.GET.get('cd')
    destino_filtro = request.GET.get('destino')

    qs = Movimientos_Producto.objects.filter(
        concepto='TRASPASO_SALIDA',
        estado='COMPLETADO',
        sucursal_destino__isnull=False,
        fecha__range=[inicio, fin],
    ).filter(_es_cd_q('sucursal_origen'))
    if emp_id:
        qs = qs.filter(sucursal_origen__empresa_id=emp_id)
    if cd_filtro:
        qs = qs.filter(sucursal_origen_id=cd_filtro)
    if destino_filtro:
        qs = qs.filter(sucursal_destino_id=destino_filtro)

    def _abs(v):
        return abs(int(v or 0))

    # --- KPIs (mismo qs base) ---
    agg = qs.aggregate(
        uds=Sum('cantidad'),
        movs=Count('id'),
        guias=Count('dte', distinct=True),
        cds=Count('sucursal_origen', distinct=True),
        destinos=Count('sucursal_destino', distinct=True),
        valor=Sum(F('cantidad') * F('costo')),
    )
    kpis = {
        'unidades': _abs(agg['uds']),
        'movimientos': agg['movs'] or 0,
        'guias': agg['guias'] or 0,
        'cds_activos': agg['cds'] or 0,
        'destinos_atendidos': agg['destinos'] or 0,
        'valor': _abs(agg['valor']),
    }

    # --- Árbol + matriz CD -> Sucursal (un solo group-by) ---
    pares = list(qs.values('sucursal_origen__alias', 'sucursal_destino__alias')
                 .annotate(uds=Sum('cantidad')).order_by('uds'))
    origenes, destinos, links = [], [], []
    matriz = {}
    tot_origen, tot_destino = defaultdict(int), defaultdict(int)
    for p in pares:
        o = p['sucursal_origen__alias'] or 'CD?'
        d = p['sucursal_destino__alias'] or 'Suc?'
        u = _abs(p['uds'])
        if o not in origenes:
            origenes.append(o)
        if d not in destinos:
            destinos.append(d)
        links.append({'source': 'CD:' + o, 'target': 'T:' + d, 'value': u})
        matriz.setdefault(o, {})[d] = u
        tot_origen[o] += u
        tot_destino[d] += u
    nodes = [{'name': 'CD:' + o} for o in origenes] + [{'name': 'T:' + d} for d in destinos]
    filas = sorted(tot_origen, key=lambda k: -tot_origen[k])
    cols = sorted(tot_destino, key=lambda k: -tot_destino[k])
    arbol = {'nodes': nodes, 'links': links}
    matriz_data = {
        'filas': filas,
        'cols': cols,
        'celdas': {o: {d: matriz.get(o, {}).get(d, 0) for d in cols} for o in filas},
        'tot_origen': {k: tot_origen[k] for k in filas},
        'tot_destino': {k: tot_destino[k] for k in cols},
    }

    # --- Cantidad por marca por sucursal (pivot, top 10 + 'Otras') ---
    marca_rows = qs.values(
        'sucursal_destino__alias',
        marca=Coalesce('ProductoTalla__producto__atributo1__valor', Value('Sin marca')),
    ).annotate(uds=Sum('cantidad'))
    piv = defaultdict(lambda: defaultdict(int))
    tot_marca = defaultdict(int)
    sucs = []
    for r in marca_rows:
        s = r['sucursal_destino__alias'] or 'Suc?'
        m = r['marca'] or 'Sin marca'
        u = _abs(r['uds'])
        piv[s][m] += u
        tot_marca[m] += u
        if s not in sucs:
            sucs.append(s)
    sucs = sorted(sucs, key=lambda s: -sum(piv[s].values()))
    top_marcas = sorted(tot_marca, key=lambda k: -tot_marca[k])[:10]
    series = [{'name': m, 'data': [piv[s].get(m, 0) for s in sucs]} for m in top_marcas]
    otras = [sum(v for k, v in piv[s].items() if k not in top_marcas) for s in sucs]
    if any(otras):
        series.append({'name': 'Otras', 'data': otras})
    marca_por_sucursal = {'sucursales': sucs, 'series': series}

    # --- Distribución temporal ---
    trunc = {'dia': TruncDate, 'semana': TruncWeek, 'mes': TruncMonth}.get(granularidad, TruncMonth)
    serie = qs.annotate(periodo=trunc('fecha')).values('periodo').annotate(
        uds=Sum('cantidad'), guias=Count('dte', distinct=True)
    ).order_by('periodo')
    serie_tiempo = [{
        'periodo': r['periodo'].strftime('%Y-%m-%d') if r['periodo'] else None,
        'unidades': _abs(r['uds']),
        'guias': r['guias'] or 0,
    } for r in serie]

    # --- Detalle por sucursal destino (drill) ---
    detalle = qs.values('sucursal_destino_id', 'sucursal_destino__alias').annotate(
        uds=Sum('cantidad'), guias=Count('dte', distinct=True), ultimo=Max('fecha')
    ).order_by('uds')
    detalle_sucursal = [{
        'sucursal_id': r['sucursal_destino_id'],
        'alias': r['sucursal_destino__alias'],
        'unidades': _abs(r['uds']),
        'guias': r['guias'] or 0,
        'ultimo': r['ultimo'].strftime('%Y-%m-%d') if r['ultimo'] else None,
    } for r in detalle]

    # --- Lista de CDs para el filtro (de la empresa) ---
    cds_qs = Sucursal.objects.filter(_es_cd_q())
    if emp_id:
        cds_qs = cds_qs.filter(empresa_id=emp_id)
    cds_disponibles = [{'id': s.id, 'alias': s.alias} for s in cds_qs.order_by('alias')]

    return JsonResponse({
        'success': True,
        'periodo': {
            'inicio': inicio.strftime('%Y-%m-%d'),
            'fin': fin.strftime('%Y-%m-%d'),
            'granularidad': granularidad,
        },
        'kpis': kpis,
        'arbol': arbol,
        'matriz': matriz_data,
        'marca_por_sucursal': marca_por_sucursal,
        'serie_tiempo': serie_tiempo,
        'detalle_sucursal': detalle_sucursal,
        'cds_disponibles': cds_disponibles,
    })


@login_required
@require_GET
def api_dashboard_despachos(request):
    """Analytics detallados de despachos, recepciones y regularizaciones."""
    suc_id, emp_id = _get_sucursal_empresa(request)
    inicio, fin = _parse_date_range(request)

    # Vista global: si el usuario puede ver todas las sucursales y lo pide (?todas=1),
    # no filtramos por sucursal (solo por empresa). Habilita el panorama origen→destino.
    from .models import PermisoUsuario
    ver_todas = request.GET.get('todas') == '1' and (
        request.user.is_superuser or PermisoUsuario.usuario_ve_todas_sucursales(request.user)
    )
    if ver_todas:
        suc_id = None

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
        'RECEPCIONADO_PARCIAL': {'texto': 'Regularizar', 'url': '/app/recepcion-dte/?vista=regularizar&tab=pendiente'},
        'EN_REGULARIZACION': {'texto': 'Regularizar', 'url': '/app/recepcion-dte/?vista=regularizar&tab=en_regularizacion'},
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

    # --- Flujo origen→destino (matriz de traspasos) ---
    flujo_qs = Movimientos_Producto.objects.filter(
        concepto='TRASPASO_SALIDA',
        dte__tipo_transaccion='TRASPASO',
        dte__descartado=False,
        dte__fecha_emision__range=[inicio, fin],
        sucursal_origen__isnull=False,
        sucursal_destino__isnull=False,
    )
    if emp_id:
        flujo_qs = flujo_qs.filter(Q(dte__emisor_id=emp_id) | Q(dte__receptor_id=emp_id))
    if suc_id:
        flujo_qs = flujo_qs.filter(Q(sucursal_origen_id=suc_id) | Q(sucursal_destino_id=suc_id))
    flujo_origen_destino = [
        {
            'origen': r['sucursal_origen__alias'] or '-',
            'destino': r['sucursal_destino__alias'] or '-',
            'dtes': r['dtes'],
            'emitidos': r['emitidos'],
            'parciales': r['parciales'],
            'completos': r['completos'],
            'por_resolver': (r['emitidos'] or 0) + (r['parciales'] or 0),
        }
        for r in (
            flujo_qs.values('sucursal_origen__alias', 'sucursal_destino__alias')
            .annotate(
                dtes=Count('dte', distinct=True),
                emitidos=Count('dte', distinct=True, filter=Q(dte__estado_dte='EMITIDO')),
                parciales=Count('dte', distinct=True, filter=Q(dte__estado_dte='RECEPCIONADO_PARCIAL')),
                completos=Count('dte', distinct=True, filter=Q(dte__estado_dte='RECEPCIONADO_COMPLETO')),
            )
            .order_by('-dtes')[:40]
        )
    ]

    # --- Atribución de error: una NC emitida sobre un traspaso = el ORIGEN aceptó
    # el faltante (no despachó lo facturado) → error de la bodega de inicio. ---
    nc_qs = Dte.objects.filter(
        es_nota_credito=True,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
        documento_afectado__tipo_transaccion='TRASPASO',
        documento_afectado__descartado=False,
        documento_afectado__fecha_emision__range=[inicio, fin],
    )
    if emp_id:
        nc_qs = nc_qs.filter(
            Q(documento_afectado__emisor_id=emp_id) | Q(documento_afectado__receptor_id=emp_id)
        )
    if suc_id:
        nc_qs = nc_qs.filter(documento_afectado__sucursal_id=suc_id)
    nc_por_origen = dict(
        nc_qs.values_list('documento_afectado__sucursal__alias')
        .annotate(c=Count('documento_afectado', distinct=True))
        .values_list('documento_afectado__sucursal__alias', 'c')
    )
    total_por_origen = dict(
        traspasos_qs.filter(sucursal__isnull=False)
        .values_list('sucursal__alias')
        .annotate(t=Count('id'))
        .values_list('sucursal__alias', 't')
    )
    errores_origen = []
    for alias, errores in nc_por_origen.items():
        if not alias:
            continue
        total = total_por_origen.get(alias, 0) or 0
        errores_origen.append({
            'origen': alias,
            'errores': errores,
            'total': total,
            'tasa': round(errores / total * 100, 1) if total else 0.0,
        })
    errores_origen.sort(key=lambda x: (-x['errores'], -x['tasa']))
    errores_origen = errores_origen[:15]

    # --- KPIs headline: por recibir / por regularizar (DTEs incompletos, sin fecha) ---
    _inc = {row['estado_dte']: row['cantidad'] for row in dtes_pendientes_raw}
    por_recibir = _inc.get('EMITIDO', 0)
    por_regularizar = (
        _inc.get('RECEPCIONADO_PARCIAL', 0)
        + _inc.get('EN_REGULARIZACION', 0)
        + _inc.get('RECHAZADO', 0)
    )

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
            'por_recibir': por_recibir,
            'por_regularizar': por_regularizar,
        },
        'flujo_origen_destino': flujo_origen_destino,
        'errores_origen': errores_origen,
        'ver_todas': ver_todas,
        'pipeline': pipeline,
        'por_sucursal': por_sucursal,
        'tendencia': tendencia,
        'regularizaciones_pendientes': reg_pendientes,
        'dtes_incompletos': dtes_incompletos,
        'dtes_incompletos_detalle': dtes_incompletos_detalle,
        'periodo': {'inicio': str(inicio), 'fin': str(fin)},
    })
