"""
Servicio de detección de fugas y fraude para cambios y devoluciones.
Separa la lógica de análisis del archivo de vistas para mantener el código organizado.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum, Avg, F, Q, Value, CharField
from django.db.models.functions import TruncWeek, TruncMonth
from django.utils import timezone


def detectar_vendedores_alto_retorno(sucursal_id=None, fecha_inicio=None, fecha_fin=None, umbral_porcentaje=10):
    """
    Identifica vendedores con tasa de cambios/devoluciones superior al umbral.
    Calcula: (cambios asociados al vendedor / ventas del vendedor) * 100
    """
    from app.models import CambioDevolucion, Ticket

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    # Cambios por vendedor del ticket original
    cambios_qs = CambioDevolucion.objects.filter(
        fecha_solicitud__date__gte=fecha_inicio,
        fecha_solicitud__date__lte=fecha_fin,
        estado__in=['SOLICITADO', 'APROBADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE',
                     'EJECUTADO_DEVOL_PENDIENTE', 'COMPLETADO']
    )
    if sucursal_id:
        cambios_qs = cambios_qs.filter(sucursal_id=sucursal_id)

    cambios_por_vendedor = cambios_qs.values(
        vendedor_id=F('ticket_original__vendedor__id'),
        vendedor_nombre=F('ticket_original__vendedor__nombre'),
        vendedor_codigo=F('ticket_original__vendedor__codigo_vendedor'),
    ).annotate(
        total_cambios=Count('id'),
        monto_cambios=Sum('monto_original')
    ).order_by('-total_cambios')

    # Ventas por vendedor en el mismo periodo
    ventas_qs = Ticket.objects.filter(
        fecha__gte=fecha_inicio,
        fecha__lte=fecha_fin,
        estado='PAGADO'
    )
    if sucursal_id:
        ventas_qs = ventas_qs.filter(sucursal_id=sucursal_id)

    ventas_por_vendedor = {}
    for v in ventas_qs.values('vendedor__id').annotate(total_ventas=Count('id')):
        ventas_por_vendedor[v['vendedor__id']] = v['total_ventas']

    resultado = []
    for item in cambios_por_vendedor:
        vid = item['vendedor_id']
        total_ventas = ventas_por_vendedor.get(vid, 0)
        tasa = (item['total_cambios'] / total_ventas * 100) if total_ventas > 0 else 100
        resultado.append({
            'vendedor_id': vid,
            'vendedor_nombre': item['vendedor_nombre'] or 'Sin vendedor',
            'vendedor_codigo': item['vendedor_codigo'] or '',
            'total_cambios': item['total_cambios'],
            'total_ventas': total_ventas,
            'tasa_cambios': round(tasa, 1),
            'monto_cambios': float(item['monto_cambios'] or 0),
            'alerta': tasa >= umbral_porcentaje,
        })

    resultado.sort(key=lambda x: x['tasa_cambios'], reverse=True)
    return resultado


def detectar_productos_multiples_cambios(sucursal_id=None, fecha_inicio=None, fecha_fin=None, umbral_repeticiones=3):
    """
    Identifica productos que han sido devueltos/cambiados múltiples veces.
    """
    from app.models import CambioDevolucionDetalle

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    qs = CambioDevolucionDetalle.objects.filter(
        cambio_devolucion__fecha_solicitud__date__gte=fecha_inicio,
        cambio_devolucion__fecha_solicitud__date__lte=fecha_fin,
        producto_original__isnull=False,
        cantidad_original__gt=0,
    )
    if sucursal_id:
        qs = qs.filter(cambio_devolucion__sucursal_id=sucursal_id)

    productos = qs.values(
        sku=F('producto_original__ProductoTalla__sku'),
        articulo=F('producto_original__ProductoTalla__producto__articulo'),
        talla=F('producto_original__ProductoTalla__talla'),
    ).annotate(
        veces_devuelto=Count('cambio_devolucion', distinct=True),
        cantidad_total_devuelta=Sum('cantidad_original'),
        monto_total=Sum(F('precio_original_unitario') * F('cantidad_original')),
    ).filter(
        veces_devuelto__gte=umbral_repeticiones
    ).order_by('-veces_devuelto')

    return [
        {
            'sku': str(p['sku'] or ''),
            'articulo': p['articulo'] or 'Sin nombre',
            'talla': p['talla'] or '',
            'veces_devuelto': p['veces_devuelto'],
            'cantidad_total_devuelta': p['cantidad_total_devuelta'],
            'monto_total': float(p['monto_total'] or 0),
        }
        for p in productos
    ]


def detectar_perdidas_no_apto(sucursal_id=None, fecha_inicio=None, fecha_fin=None):
    """
    Calcula el impacto financiero de productos devueltos marcados como NO aptos para venta.
    Estos representan pérdida directa de inventario.
    """
    from app.models import CambioDevolucionDetalle

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    qs = CambioDevolucionDetalle.objects.filter(
        cambio_devolucion__fecha_solicitud__date__gte=fecha_inicio,
        cambio_devolucion__fecha_solicitud__date__lte=fecha_fin,
        apto_para_venta=False,
        cantidad_original__gt=0,
    )
    if sucursal_id:
        qs = qs.filter(cambio_devolucion__sucursal_id=sucursal_id)

    # Total general
    totales = qs.aggregate(
        total_items=Count('id'),
        total_unidades=Sum('cantidad_original'),
        perdida_financiera=Sum(F('precio_original_unitario') * F('cantidad_original')),
    )

    # Por condición
    por_condicion = list(qs.values('condicion_producto').annotate(
        cantidad=Count('id'),
        unidades=Sum('cantidad_original'),
        monto=Sum(F('precio_original_unitario') * F('cantidad_original')),
    ).order_by('-monto'))

    return {
        'total_items': totales['total_items'] or 0,
        'total_unidades': totales['total_unidades'] or 0,
        'perdida_financiera': float(totales['perdida_financiera'] or 0),
        'por_condicion': [
            {
                'condicion': c['condicion_producto'],
                'cantidad': c['cantidad'],
                'unidades': c['unidades'],
                'monto': float(c['monto'] or 0),
            }
            for c in por_condicion
        ],
    }


def detectar_cambios_fuera_plazo(sucursal_id=None, fecha_inicio=None, fecha_fin=None):
    """
    Analiza los cambios realizados fuera del plazo de 30 días.
    """
    from app.models import CambioDevolucion

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    qs = CambioDevolucion.objects.filter(
        fecha_solicitud__date__gte=fecha_inicio,
        fecha_solicitud__date__lte=fecha_fin,
        es_fuera_de_plazo=True,
    )
    if sucursal_id:
        qs = qs.filter(sucursal_id=sucursal_id)

    totales = qs.aggregate(
        total=Count('id'),
        promedio_dias=Avg('dias_fuera_de_plazo'),
        monto_total=Sum('monto_original'),
    )

    # Detalle de los últimos 20
    detalle = list(qs.order_by('-fecha_solicitud')[:20].values(
        'id', 'numero_operacion', 'tipo_operacion', 'estado',
        'dias_fuera_de_plazo', 'monto_original',
        'autorizado_por_usuario__username',
        'es_autorizacion_cross_branch',
        'fecha_solicitud',
    ))

    return {
        'total': totales['total'] or 0,
        'promedio_dias_fuera': round(float(totales['promedio_dias'] or 0), 1),
        'monto_total': float(totales['monto_total'] or 0),
        'detalle': [
            {
                'id': d['id'],
                'numero_operacion': d['numero_operacion'],
                'tipo_operacion': d['tipo_operacion'],
                'estado': d['estado'],
                'dias_fuera': d['dias_fuera_de_plazo'],
                'monto': float(d['monto_original'] or 0),
                'autorizado_por': d['autorizado_por_usuario__username'] or 'N/A',
                'es_cross_branch': d['es_autorizacion_cross_branch'],
                'fecha': d['fecha_solicitud'].strftime('%d/%m/%Y %H:%M') if d['fecha_solicitud'] else '',
            }
            for d in detalle
        ],
    }


def detectar_patrones_cross_branch(fecha_inicio=None, fecha_fin=None):
    """
    Analiza patrones de autorizaciones entre sucursales.
    """
    from app.models import RegistroAutorizacion

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    qs = RegistroAutorizacion.objects.filter(
        fecha_hora__date__gte=fecha_inicio,
        fecha_hora__date__lte=fecha_fin,
        es_cross_branch=True,
        exitoso=True,
    )

    totales = qs.aggregate(total=Count('id'))

    # Por supervisor autorizador
    por_supervisor = list(qs.values(
        supervisor_nombre=F('usuario_autorizador__username'),
        sucursal_nombre=F('sucursal_autorizador__alias'),
    ).annotate(
        total_autorizaciones=Count('id'),
    ).order_by('-total_autorizaciones')[:10])

    # Por sucursal solicitante
    por_sucursal = list(qs.values(
        sucursal=F('sucursal_solicitante__alias'),
    ).annotate(
        total_solicitudes=Count('id'),
    ).order_by('-total_solicitudes'))

    # Pendientes de revisión
    pendientes_revision = qs.filter(
        requiere_revision=True,
        revisado_por__isnull=True,
    ).count()

    return {
        'total': totales['total'] or 0,
        'pendientes_revision': pendientes_revision,
        'por_supervisor': [
            {
                'supervisor': s['supervisor_nombre'] or 'N/A',
                'sucursal': s['sucursal_nombre'] or 'N/A',
                'total': s['total_autorizaciones'],
            }
            for s in por_supervisor
        ],
        'por_sucursal': [
            {
                'sucursal': s['sucursal'] or 'N/A',
                'total': s['total_solicitudes'],
            }
            for s in por_sucursal
        ],
    }


def generar_score_riesgo(cambio):
    """
    Calcula un score de riesgo (0-100) para un cambio/devolución.
    Se puede pasar un objeto CambioDevolucion o un ID.
    """
    from app.models import CambioDevolucion, CambioDevolucionDetalle

    if isinstance(cambio, int):
        try:
            cambio = CambioDevolucion.objects.get(id=cambio)
        except CambioDevolucion.DoesNotExist:
            return 0

    score = 0

    # Fuera de plazo: +20 (más si muy fuera)
    if cambio.es_fuera_de_plazo:
        score += 20
        if cambio.dias_fuera_de_plazo > 30:
            score += 10

    # Productos NO aptos para venta: +30
    tiene_no_apto = cambio.detalles.filter(apto_para_venta=False).exists()
    if tiene_no_apto:
        score += 30

    # Autorización cross-branch: +10
    if cambio.es_autorizacion_cross_branch:
        score += 10

    # Alto valor (> $200.000): +10
    if cambio.monto_original and cambio.monto_original > 200000:
        score += 10

    # Producto devuelto múltiples veces: +15
    detalles = cambio.detalles.filter(producto_original__isnull=False)
    for det in detalles:
        if det.producto_original and det.producto_original.ProductoTalla:
            repeticiones = CambioDevolucionDetalle.objects.filter(
                producto_original__ProductoTalla=det.producto_original.ProductoTalla,
                cambio_devolucion__fecha_solicitud__date__gte=timezone.localdate() - timedelta(days=90),
            ).values('cambio_devolucion').distinct().count()
            if repeticiones >= 3:
                score += 15
                break

    # Vendedor con alta tasa: +15
    if cambio.ticket_original and cambio.ticket_original.vendedor:
        vendedor_id = cambio.ticket_original.vendedor.id
        fecha_30d = timezone.localdate() - timedelta(days=30)
        from app.models import Ticket
        cambios_vendedor = CambioDevolucion.objects.filter(
            ticket_original__vendedor_id=vendedor_id,
            fecha_solicitud__date__gte=fecha_30d,
        ).count()
        ventas_vendedor = Ticket.objects.filter(
            vendedor_id=vendedor_id,
            fecha__gte=fecha_30d,
            estado='PAGADO',
        ).count()
        if ventas_vendedor > 0 and (cambios_vendedor / ventas_vendedor * 100) >= 10:
            score += 15

    return min(score, 100)


def obtener_analisis_avanzado(sucursal_id=None, fecha_inicio=None, fecha_fin=None):
    """
    Retorna análisis avanzado completo consolidando todas las métricas.
    """
    from app.models import CambioDevolucion, CambioDevolucionDetalle, Ticket
    from app.models.ventas import MOTIVO_CAMBIO_CHOICES, TIPO_OPERACION_CAMBIO_CHOICES, ESTADO_CAMBIO_CHOICES

    if not fecha_inicio:
        fecha_fin = timezone.localdate()
        fecha_inicio = fecha_fin - timedelta(days=30)

    # Base queryset
    cambios_qs = CambioDevolucion.objects.filter(
        fecha_solicitud__date__gte=fecha_inicio,
        fecha_solicitud__date__lte=fecha_fin,
    )
    if sucursal_id:
        cambios_qs = cambios_qs.filter(sucursal_id=sucursal_id)

    # --- Métricas generales ---
    total_cambios = cambios_qs.count()
    monto_total = cambios_qs.aggregate(t=Sum('monto_original'))['t'] or 0

    ventas_qs = Ticket.objects.filter(
        fecha__gte=fecha_inicio, fecha__lte=fecha_fin, estado='PAGADO'
    )
    if sucursal_id:
        ventas_qs = ventas_qs.filter(sucursal_id=sucursal_id)
    cantidad_ventas = ventas_qs.count()
    ratio = (total_cambios / cantidad_ventas * 100) if cantidad_ventas > 0 else 0

    # --- Por motivo ---
    por_motivo = list(cambios_qs.values('motivo_principal').annotate(
        cantidad=Count('id')
    ).order_by('-cantidad'))
    motivos_dict = dict(MOTIVO_CAMBIO_CHOICES)

    # --- Por tipo ---
    por_tipo = list(cambios_qs.values('tipo_operacion').annotate(
        cantidad=Count('id'), monto=Sum('monto_original')
    ))
    tipos_dict = dict(TIPO_OPERACION_CAMBIO_CHOICES)

    # --- Por estado ---
    por_estado = list(cambios_qs.values('estado').annotate(cantidad=Count('id')))
    estados_dict = dict(ESTADO_CAMBIO_CHOICES)

    # --- Tiempo promedio de resolución ---
    completados = cambios_qs.filter(estado='COMPLETADO', fecha_completado__isnull=False)
    tiempo_promedio_horas = None
    if completados.exists():
        from django.db.models import ExpressionWrapper, DurationField
        tiempos = completados.annotate(
            duracion=ExpressionWrapper(
                F('fecha_completado') - F('fecha_solicitud'),
                output_field=DurationField()
            )
        ).aggregate(prom=Avg('duracion'))
        if tiempos['prom']:
            tiempo_promedio_horas = round(tiempos['prom'].total_seconds() / 3600, 1)

    # --- Impacto financiero neto ---
    impacto = cambios_qs.filter(
        estado__in=['COMPLETADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']
    ).aggregate(
        devoluciones=Sum('diferencia_monto', filter=Q(diferencia_monto__lt=0)),
        # Excluir condonaciones: la diferencia fue perdonada, no recaudada
        cobros=Sum('diferencia_monto', filter=Q(diferencia_monto__gt=0) & Q(diferencia_condonada=False)),
    )

    # --- Top 10 productos devueltos ---
    detalles_qs = CambioDevolucionDetalle.objects.filter(
        cambio_devolucion__fecha_solicitud__date__gte=fecha_inicio,
        cambio_devolucion__fecha_solicitud__date__lte=fecha_fin,
        producto_original__isnull=False,
        cantidad_original__gt=0,
    )
    if sucursal_id:
        detalles_qs = detalles_qs.filter(cambio_devolucion__sucursal_id=sucursal_id)

    top_productos = list(detalles_qs.values(
        sku=F('producto_original__ProductoTalla__sku'),
        articulo=F('producto_original__ProductoTalla__producto__articulo'),
    ).annotate(
        veces=Count('cambio_devolucion', distinct=True),
        cantidad=Sum('cantidad_original'),
    ).order_by('-veces')[:10])

    # --- Tasa fuera de plazo ---
    fuera_plazo_count = cambios_qs.filter(es_fuera_de_plazo=True).count()
    tasa_fuera_plazo = (fuera_plazo_count / total_cambios * 100) if total_cambios > 0 else 0

    # --- Tasa cross-branch ---
    cross_branch_count = cambios_qs.filter(es_autorizacion_cross_branch=True).count()
    tasa_cross_branch = (cross_branch_count / total_cambios * 100) if total_cambios > 0 else 0

    # --- Tasa NO_APTO ---
    total_detalles = detalles_qs.count()
    no_apto_count = detalles_qs.filter(apto_para_venta=False).count()
    tasa_no_apto = (no_apto_count / total_detalles * 100) if total_detalles > 0 else 0

    # --- Tendencia semanal ---
    tendencia = list(cambios_qs.annotate(
        semana=TruncWeek('fecha_solicitud')
    ).values('semana').annotate(
        cantidad=Count('id'),
        monto=Sum('monto_original'),
    ).order_by('semana'))

    # --- Por sucursal ---
    por_sucursal = list(cambios_qs.values(
        sucursal_nombre=F('sucursal__alias'),
    ).annotate(
        cantidad=Count('id'),
        monto=Sum('monto_original'),
    ).order_by('-cantidad'))

    # --- Por vendedor (top 10) ---
    por_vendedor = list(cambios_qs.values(
        vendedor_nombre=F('ticket_original__vendedor__nombre'),
        vendedor_codigo=F('ticket_original__vendedor__codigo_vendedor'),
    ).annotate(
        cantidad=Count('id'),
        monto=Sum('monto_original'),
    ).order_by('-cantidad')[:10])

    # --- Comparación periodo anterior ---
    duracion_dias = (fecha_fin - fecha_inicio).days
    periodo_ant_fin = fecha_inicio - timedelta(days=1)
    periodo_ant_inicio = periodo_ant_fin - timedelta(days=duracion_dias)

    cambios_ant = CambioDevolucion.objects.filter(
        fecha_solicitud__date__gte=periodo_ant_inicio,
        fecha_solicitud__date__lte=periodo_ant_fin,
    )
    if sucursal_id:
        cambios_ant = cambios_ant.filter(sucursal_id=sucursal_id)
    total_anterior = cambios_ant.count()
    monto_anterior = cambios_ant.aggregate(t=Sum('monto_original'))['t'] or 0

    # Pendientes de revisión gerencial
    pendientes_revision = cambios_qs.filter(
        requiere_revision_gerencial=True,
        revisado_por_gerencia__isnull=True,
    ).count()

    return {
        # Métricas generales
        'total_cambios': total_cambios,
        'monto_total': float(monto_total),
        'ratio': round(ratio, 2),
        'cantidad_ventas': cantidad_ventas,
        'tiempo_promedio_horas': tiempo_promedio_horas,

        # Desglose
        'por_motivo': [
            {'motivo': motivos_dict.get(m['motivo_principal'], m['motivo_principal']), 'cantidad': m['cantidad']}
            for m in por_motivo
        ],
        'por_tipo': [
            {'tipo': tipos_dict.get(t['tipo_operacion'], t['tipo_operacion']), 'cantidad': t['cantidad'], 'monto': float(t['monto'] or 0)}
            for t in por_tipo
        ],
        'por_estado': [
            {'estado': estados_dict.get(e['estado'], e['estado']), 'cantidad': e['cantidad']}
            for e in por_estado
        ],

        # Financiero
        'devoluciones_total': float(impacto.get('devoluciones') or 0),
        'cobros_total': float(impacto.get('cobros') or 0),

        # Top productos
        'top_productos': [
            {'sku': str(p['sku'] or ''), 'articulo': p['articulo'] or '', 'veces': p['veces'], 'cantidad': p['cantidad']}
            for p in top_productos
        ],

        # Tasas especiales
        'fuera_plazo_count': fuera_plazo_count,
        'tasa_fuera_plazo': round(tasa_fuera_plazo, 1),
        'cross_branch_count': cross_branch_count,
        'tasa_cross_branch': round(tasa_cross_branch, 1),
        'no_apto_count': no_apto_count,
        'tasa_no_apto': round(tasa_no_apto, 1),

        # Tendencia
        'tendencia_semanal': [
            {'semana': t['semana'].strftime('%Y-%m-%d'), 'cantidad': t['cantidad'], 'monto': float(t['monto'] or 0)}
            for t in tendencia
        ],

        # Por sucursal y vendedor
        'por_sucursal': [
            {'sucursal': s['sucursal_nombre'] or 'N/A', 'cantidad': s['cantidad'], 'monto': float(s['monto'] or 0)}
            for s in por_sucursal
        ],
        'por_vendedor': [
            {'nombre': v['vendedor_nombre'] or 'Sin vendedor', 'codigo': v['vendedor_codigo'] or '', 'cantidad': v['cantidad'], 'monto': float(v['monto'] or 0)}
            for v in por_vendedor
        ],

        # Comparación
        'periodo_anterior': {
            'total': total_anterior,
            'monto': float(monto_anterior),
            'variacion_cantidad': round(((total_cambios - total_anterior) / total_anterior * 100) if total_anterior > 0 else 0, 1),
            'variacion_monto': round(((float(monto_total) - float(monto_anterior)) / float(monto_anterior) * 100) if float(monto_anterior) > 0 else 0, 1),
        },

        # Revisión
        'pendientes_revision': pendientes_revision,
    }
