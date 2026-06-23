"""
Módulo Fidelización (puntos) - RetailMind

Configuración del programa, cuentas de puntos por cliente, ficha con historial,
ajustes manuales y reportes. Vistas HTML + APIs JSON, function-based.
La lógica vive en `app/services/fidelizacion_service.py`.
"""
import json
import logging
from datetime import datetime, time

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count, Min, Max
from django.utils import timezone
from django.utils.dateparse import parse_date

from .decorators import requiere_permiso
from .models import (
    Cliente, CuentaPuntos, MovimientoPuntos, ProgramaFidelizacion,
    Empresa, EmpresaUser,
)
from .services import fidelizacion_service
from .utils_permisos import usuario_puede_ver_todas_sucursales

logger = logging.getLogger('app')


def _empresa_ids_usuario(usuario):
    """IDs de empresas a las que el usuario tiene acceso (vía EmpresaUser)."""
    return list(
        EmpresaUser.objects
        .filter(user=usuario, status=True, empresa__isnull=False)
        .values_list('empresa_id', flat=True)
        .distinct()
    )


def _empresa_actual(request):
    """Empresa activa en sesión (para asociar clientes nuevos)."""
    eid = request.session.get('idEmpresaActual') or request.session.get('empresaActual')
    if not eid:
        return None
    return Empresa.objects.filter(id=eid).first()


# ========== VISTAS HTML ==========

@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def modulo_fidelizacion(request):
    """Listado de clientes con puntos."""
    programa = ProgramaFidelizacion.get_activo()
    context = {'programa': programa}
    return render(request, 'vistas/modulo_fidelizacion/lista.html', context)


@requiere_permiso('fidelizacion_programa', 'puede_ver')
def configurar_programa_vista(request):
    """Configuración del programa de puntos (solo admin)."""
    programa = ProgramaFidelizacion.get_activo()
    context = {
        'programa': programa,
        'redondeo_choices': ProgramaFidelizacion._meta.get_field('redondeo').choices,
        'acumula_choices': ProgramaFidelizacion._meta.get_field('acumula_sobre').choices,
    }
    return render(request, 'vistas/modulo_fidelizacion/configuracion.html', context)


@requiere_permiso('fidelizacion_reporte', 'puede_ver')
def reporte_fidelizacion_vista(request):
    """Reporte operativo de puntos, vencimientos, canjes y señales de abuso."""
    programa = ProgramaFidelizacion.get_activo()
    context = {'programa': programa}
    return render(request, 'vistas/modulo_fidelizacion/reporte.html', context)


@requiere_permiso('fidelizacion_cuentas', 'puede_crear')
def registrar_cliente_vista(request):
    """
    Alta manual movida a un modal dentro del listado. Esta ruta se conserva y
    redirige al listado abriendo el modal de registro.
    """
    return redirect('/app/fidelizacion/?nuevo=1')


@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def ficha_cliente_puntos_vista(request, cliente_id):
    """Ficha de cliente con saldo, lotes próximos a vencer e historial."""
    cliente = get_object_or_404(Cliente, id=cliente_id)
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    movimientos = []
    if cuenta:
        movimientos = cuenta.movimientos.all().order_by('-fecha')[:200]
    context = {
        'cliente': cliente,
        'cuenta': cuenta,
        'movimientos': movimientos,
        'saldo_info': fidelizacion_service.consultar_saldo(cliente=cliente),
    }
    return render(request, 'vistas/modulo_fidelizacion/ficha_cliente.html', context)


# ========== APIs JSON ==========

@require_GET
@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def api_listar_cuentas(request):
    """
    Listado paginado de TODAS las personas registradas en las empresas a las que
    el usuario tiene acceso (no solo las que ya tienen cuenta de puntos).
    Muestra su saldo de puntos (0 si aún no acumula).
    """
    qs = Cliente.objects.filter(activo=True).select_related('empresa', 'cuenta_puntos')

    # Multi-empresa: el admin (o quien ve todas las sucursales) ve todo; el resto
    # ve los clientes de sus empresas + los clientes sin empresa asignada.
    if not usuario_puede_ver_todas_sucursales(request.user):
        empresas = _empresa_ids_usuario(request.user)
        qs = qs.filter(Q(empresa__isnull=True) | Q(empresa_id__in=empresas))

    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(rut__icontains=busqueda) |
            Q(empresa__nombre__icontains=busqueda)
        )

    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0

    paginator = Paginator(qs, int(request.GET.get('per_page', 20)))
    page = paginator.get_page(request.GET.get('page', 1))

    items = []
    for c in page:
        cuenta = getattr(c, 'cuenta_puntos', None)
        saldo = cuenta.saldo_puntos if cuenta else 0
        items.append({
            'cliente_id': c.id,
            'cliente': c.nombre_completo,
            'rut': c.rut or '',
            'empresa': c.empresa.nombre if c.empresa else '',
            'saldo_puntos': saldo,
            'valor_pesos': saldo * valor_pto,
            'tiene_cuenta': cuenta is not None,
            'nivel': cuenta.nivel if cuenta else 'PLATA',
        })

    return JsonResponse({
        'success': True,
        'items': items,
        'page': page.number,
        'num_pages': paginator.num_pages,
        'total': paginator.count,
    })


@require_GET
@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def api_detalle_cuenta(request, cliente_id):
    """Saldo + historial de la cuenta de un cliente."""
    cliente = get_object_or_404(Cliente, id=cliente_id)
    info = fidelizacion_service.consultar_saldo(cliente=cliente)
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    movimientos = []
    if cuenta:
        movimientos = [{
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'puntos': m.puntos,
            'saldo_resultante': m.saldo_resultante,
            'fecha': m.fecha.strftime('%Y-%m-%d %H:%M'),
            'fecha_expiracion': m.fecha_expiracion.isoformat() if m.fecha_expiracion else None,
            'ticket': m.ticket.correlativo if m.ticket else None,
            'observaciones': m.observaciones or '',
        } for m in cuenta.movimientos.all().order_by('-fecha')[:200]]

    return JsonResponse({'success': True, 'saldo': info, 'movimientos': movimientos})


@require_GET
@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def api_consultar_saldo_puntos(request):
    """Consulta saldo de puntos por RUT (usada por el POS al cobrar)."""
    rut = (request.GET.get('rut') or '').strip()
    if not rut:
        return JsonResponse({'success': False, 'error': 'RUT requerido.'}, status=400)
    info = fidelizacion_service.consultar_saldo(rut=rut)
    return JsonResponse({'success': True, **info})


def _parse_fecha_reporte(valor, default):
    fecha = parse_date(valor or '')
    return fecha or default


def _rango_reporte(request):
    hoy = timezone.localdate()
    fecha_inicio = _parse_fecha_reporte(request.GET.get('desde'), hoy - timezone.timedelta(days=29))
    fecha_fin = _parse_fecha_reporte(request.GET.get('hasta'), hoy)
    if fecha_fin < fecha_inicio:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    tz = timezone.get_current_timezone()
    inicio_dt = timezone.make_aware(datetime.combine(fecha_inicio, time.min), tz)
    fin_dt = timezone.make_aware(datetime.combine(fecha_fin, time.max), tz)
    return fecha_inicio, fecha_fin, inicio_dt, fin_dt


def _movimientos_visibles_para_usuario(qs, usuario):
    """Aplica el mismo alcance multi-empresa que el listado de cuentas."""
    if usuario_puede_ver_todas_sucursales(usuario):
        return qs
    empresas = _empresa_ids_usuario(usuario)
    return qs.filter(Q(cuenta__cliente__empresa__isnull=True) |
                     Q(cuenta__cliente__empresa_id__in=empresas))


def _cliente_nombre(cliente):
    if not cliente:
        return ''
    return cliente.nombre_completo


def construir_reporte_fidelizacion(*, fecha_inicio_dt, fecha_fin_dt,
                                   dias_vencimiento=30, usuario=None):
    """
    Calcula el reporte de fidelización. Separado para testear la lógica sin
    depender de la vista ni del JavaScript.
    """
    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0
    hoy = timezone.localdate()
    vence_hasta = hoy + timezone.timedelta(days=max(1, int(dias_vencimiento or 30)))

    cuentas = CuentaPuntos.objects.select_related('cliente')
    mov_base = MovimientoPuntos.objects.select_related(
        'cuenta__cliente', 'ticket', 'usuario', 'sucursal',
    )
    if usuario is not None:
        mov_base = _movimientos_visibles_para_usuario(mov_base, usuario)
        if not usuario_puede_ver_todas_sucursales(usuario):
            empresas = _empresa_ids_usuario(usuario)
            cuentas = cuentas.filter(Q(cliente__empresa__isnull=True) |
                                     Q(cliente__empresa_id__in=empresas))

    periodo_qs = mov_base.filter(fecha__gte=fecha_inicio_dt, fecha__lte=fecha_fin_dt)
    acumulaciones_qs = periodo_qs.filter(tipo='ACUMULACION', puntos__gt=0)
    bienvenida_qs = periodo_qs.filter(tipo='BIENVENIDA', puntos__gt=0)
    canjes_qs = periodo_qs.filter(tipo='CANJE', puntos__lt=0)
    expiraciones_qs = periodo_qs.filter(tipo='EXPIRACION', puntos__lt=0)

    total_puntos = cuentas.aggregate(s=Sum('saldo_puntos'))['s'] or 0
    puntos_acumulados = acumulaciones_qs.aggregate(s=Sum('puntos'))['s'] or 0
    puntos_bienvenida = bienvenida_qs.aggregate(s=Sum('puntos'))['s'] or 0
    puntos_canjeados = abs(canjes_qs.aggregate(s=Sum('puntos'))['s'] or 0)
    puntos_expirados = abs(expiraciones_qs.aggregate(s=Sum('puntos'))['s'] or 0)

    por_vencer_map = {}
    lotes_por_vencer = mov_base.filter(
        puntos__gt=0,
        fecha_expiracion__gte=hoy,
        fecha_expiracion__lte=vence_hasta,
    ).order_by('fecha_expiracion', 'fecha')
    for lote in lotes_por_vencer:
        disponible = lote.saldo_lote
        if disponible <= 0:
            continue
        cliente = lote.cuenta.cliente
        item = por_vencer_map.setdefault(lote.cuenta_id, {
            'cliente_id': cliente.id,
            'cliente': _cliente_nombre(cliente),
            'rut': cliente.rut or '',
            'puntos': 0,
            'valor_pesos': 0,
            'proximo_vencimiento': lote.fecha_expiracion,
        })
        item['puntos'] += disponible
        item['valor_pesos'] = item['puntos'] * valor_pto
        if lote.fecha_expiracion < item['proximo_vencimiento']:
            item['proximo_vencimiento'] = lote.fecha_expiracion

    puntos_por_vencer = sorted(
        por_vencer_map.values(),
        key=lambda x: (x['proximo_vencimiento'], -x['puntos']),
    )
    total_por_vencer = sum(i['puntos'] for i in puntos_por_vencer)

    canjes_recientes = []
    for mov in canjes_qs.order_by('-fecha')[:20]:
        cliente = mov.cuenta.cliente
        puntos = abs(mov.puntos)
        canjes_recientes.append({
            'fecha': timezone.localtime(mov.fecha).strftime('%Y-%m-%d %H:%M'),
            'cliente_id': cliente.id,
            'cliente': _cliente_nombre(cliente),
            'rut': cliente.rut or '',
            'puntos': puntos,
            'valor_pesos': puntos * valor_pto,
            'usuario': mov.usuario.get_full_name() if mov.usuario else '',
            'ticket': mov.ticket.correlativo if mov.ticket else None,
            'observaciones': mov.observaciones or '',
        })

    total_tickets_por_usuario = {
        row['usuario_id']: row['tickets_usuario']
        for row in acumulaciones_qs.filter(usuario__isnull=False, ticket__isnull=False)
        .values('usuario_id')
        .annotate(tickets_usuario=Count('ticket', distinct=True))
    }

    alertas_concentracion = []
    concentracion_rows = (
        acumulaciones_qs
        .filter(usuario__isnull=False, ticket__isnull=False)
        .values(
            'usuario_id', 'usuario__username', 'usuario__first_name', 'usuario__last_name',
            'cuenta_id', 'cuenta__cliente_id', 'cuenta__cliente__nombre',
            'cuenta__cliente__apellido', 'cuenta__cliente__rut',
        )
        .annotate(
            tickets=Count('ticket', distinct=True),
            dias=Count('ticket__fecha', distinct=True),
            puntos=Sum('puntos'),
            venta_total=Sum('ticket__total'),
            primer_mov=Min('fecha'),
            ultimo_mov=Max('fecha'),
        )
        .filter(tickets__gte=5)
        .order_by('-tickets', '-puntos')[:30]
    )
    for row in concentracion_rows:
        tickets_usuario = total_tickets_por_usuario.get(row['usuario_id'], 0) or 0
        participacion = round((row['tickets'] / tickets_usuario * 100), 1) if tickets_usuario else 0
        severidad = 'ALTA' if row['tickets'] >= 10 or participacion >= 50 else 'MEDIA'
        nombre_usuario = (
            f"{row.get('usuario__first_name') or ''} {row.get('usuario__last_name') or ''}".strip()
            or row.get('usuario__username') or 'Usuario'
        )
        nombre_cliente = (
            f"{row.get('cuenta__cliente__nombre') or ''} {row.get('cuenta__cliente__apellido') or ''}".strip()
            or 'Cliente'
        )
        alertas_concentracion.append({
            'severidad': severidad,
            'usuario_id': row['usuario_id'],
            'usuario': nombre_usuario,
            'cliente_id': row['cuenta__cliente_id'],
            'cliente': nombre_cliente,
            'rut': row.get('cuenta__cliente__rut') or '',
            'tickets': row['tickets'],
            'tickets_usuario': tickets_usuario,
            'participacion': participacion,
            'dias': row['dias'],
            'puntos': row['puntos'] or 0,
            'valor_pesos': (row['puntos'] or 0) * valor_pto,
            'venta_total': row['venta_total'] or 0,
            'primer_mov': timezone.localtime(row['primer_mov']).strftime('%Y-%m-%d') if row['primer_mov'] else '',
            'ultimo_mov': timezone.localtime(row['ultimo_mov']).strftime('%Y-%m-%d') if row['ultimo_mov'] else '',
            'motivo': (
                'Alta concentración de tickets de una cajera en una misma cuenta. '
                'Revisar si corresponde al cliente real o a acumulación indebida.'
            ),
        })

    # Desglose por nivel
    niveles_qs = cuentas.values('nivel').annotate(
        cantidad=Count('id'),
        puntos_total=Sum('saldo_puntos'),
    )
    desglose_niveles = {row['nivel']: {
        'cantidad': row['cantidad'],
        'puntos': row['puntos_total'] or 0,
        'valor_pesos': (row['puntos_total'] or 0) * valor_pto,
    } for row in niveles_qs}

    # Bonos cumpleaños en el período
    cumpleanos_qs = periodo_qs.filter(tipo='CUMPLEANOS', puntos__gt=0)
    puntos_cumpleanos = cumpleanos_qs.aggregate(s=Sum('puntos'))['s'] or 0

    return {
        'programa': {
            'nombre': programa.nombre if programa else '',
            'valor_punto': valor_pto,
            'tasa_descuento_efectiva': programa.tasa_descuento_efectiva if programa else 0,
            'tasa_plata': float(programa.tasa_plata) if programa else 3.0,
            'tasa_oro': float(programa.tasa_oro) if programa else 4.0,
            'tasa_platino': float(programa.tasa_platino) if programa else 5.0,
            'umbral_oro': programa.umbral_oro if programa else 300000,
            'umbral_platino': programa.umbral_platino if programa else 800000,
        },
        'resumen': {
            'total_clientes': cuentas.count(),
            'puntos_circulantes': total_puntos,
            'pasivo_estimado_pesos': total_puntos * valor_pto,
            'puntos_emitidos_periodo': puntos_acumulados + puntos_bienvenida + puntos_cumpleanos,
            'puntos_acumulados_periodo': puntos_acumulados,
            'puntos_bienvenida_periodo': puntos_bienvenida,
            'puntos_cumpleanos_periodo': puntos_cumpleanos,
            'valor_emitido_periodo': (puntos_acumulados + puntos_bienvenida + puntos_cumpleanos) * valor_pto,
            'puntos_canjeados_periodo': puntos_canjeados,
            'valor_canjeado_periodo': puntos_canjeados * valor_pto,
            'puntos_expirados_periodo': puntos_expirados,
            'valor_expirado_periodo': puntos_expirados * valor_pto,
            'puntos_por_vencer': total_por_vencer,
            'valor_por_vencer': total_por_vencer * valor_pto,
            'alertas_concentracion': len(alertas_concentracion),
            'acumulaciones_30d': mov_base.filter(
                tipo='ACUMULACION',
                fecha__gte=timezone.now() - timezone.timedelta(days=30),
            ).count(),
        },
        'desglose_niveles': desglose_niveles,
        'puntos_por_vencer': [{
            **item,
            'proximo_vencimiento': item['proximo_vencimiento'].isoformat(),
        } for item in puntos_por_vencer[:20]],
        'canjes_recientes': canjes_recientes,
        'alertas_concentracion': alertas_concentracion,
    }


@require_POST
@requiere_permiso('fidelizacion_programa', 'puede_editar')
def api_guardar_programa(request):
    """Crea/actualiza el programa de fidelización activo."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        programa = ProgramaFidelizacion()

    campos_int = [
        'puntos_por_monto', 'monto_base_acumulacion', 'valor_punto_en_pesos',
        'minimo_canje_puntos', 'vigencia_dias', 'puntos_bienvenida',
        'puntos_cumpleanos', 'incremento_canje', 'umbral_oro', 'umbral_platino',
    ]
    for campo in campos_int:
        if data.get(campo) not in (None, ''):
            setattr(programa, campo, int(float(data[campo])))
    campos_decimal = ['tasa_plata', 'tasa_oro', 'tasa_platino']
    for campo in campos_decimal:
        if data.get(campo) not in (None, ''):
            setattr(programa, campo, float(data[campo]))
    if data.get('nombre'):
        programa.nombre = data['nombre']
    if data.get('redondeo'):
        programa.redondeo = data['redondeo']
    if data.get('acumula_sobre'):
        programa.acumula_sobre = data['acumula_sobre']

    # Guardrail de costo: `tasa_descuento_efectiva` es el % de cada venta que se
    # devuelve en puntos. Se evalúa sobre la instancia ya seteada pero AÚN no
    # guardada. Sobre el tope duro exigimos confirmación explícita (evita dejar
    # el programa en, p.ej., 100% por un typo); sobre el umbral de aviso solo
    # devolvemos un warning informativo.
    TASA_AVISO = 5.0
    TASA_TOPE = 10.0
    tasa = programa.tasa_descuento_efectiva
    confirmado = str(data.get('confirmar_tasa_alta', '')).lower() in ('true', '1', 'on', 'yes')
    if tasa > TASA_TOPE and not confirmado:
        return JsonResponse({
            'success': False,
            'requiere_confirmacion': True,
            'tasa_descuento_efectiva': tasa,
            'warning': (f'La tasa efectiva quedaría en {tasa}%: estarías devolviendo '
                        f'más del {TASA_TOPE:.0f}% de cada venta en puntos. '
                        '¿Confirmas que es correcto?'),
        })

    programa.activo = True
    programa.updated_by = request.user
    programa.save()

    resp = {
        'success': True,
        'programa_id': programa.id,
        'tasa_descuento_efectiva': tasa,
    }
    if tasa > TASA_AVISO:
        resp['warning'] = (f'Tasa efectiva {tasa}%: por encima del {TASA_AVISO:.0f}% '
                           'recomendado. Revisa que tu margen lo aguante.')
    return JsonResponse(resp)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_editar')
def api_ajuste_manual_puntos(request):
    """Ajuste manual de puntos (suma o resta). Solo roles con puede_editar."""
    try:
        data = json.loads(request.body or '{}')
        cliente = get_object_or_404(Cliente, id=data.get('cliente_id'))
        puntos = int(data.get('puntos') or 0)
        saldo = fidelizacion_service.ajuste_manual(
            cliente, puntos, usuario=request.user,
            observaciones=data.get('observaciones', ''),
        )
        return JsonResponse({'success': True, 'saldo_total': saldo})
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error en ajuste manual de puntos")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_crear')
def api_registrar_cliente(request):
    """Alta manual de un cliente para fidelización."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST
    try:
        cliente, cuenta, creado = fidelizacion_service.registrar_cliente_manual(
            nombre=data.get('nombre', ''),
            apellido=data.get('apellido', ''),
            rut=data.get('rut', ''),
            email=data.get('email', ''),
            celular=data.get('celular', ''),
            fecha_nacimiento=(data.get('fecha_nacimiento') or None),
            genero=data.get('genero', ''),
            usuario=request.user,
            empresa=_empresa_actual(request),
        )
        return JsonResponse({
            'success': True,
            'creado': creado,
            'cliente_id': cliente.id,
            'cliente': cliente.nombre_completo,
            'saldo_puntos': cuenta.saldo_puntos,
        })
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error en alta manual de cliente fidelización")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_editar')
def api_bono_cumpleanos(request):
    """Otorga el bono de cumpleaños al cliente (si hoy es su cumpleaños y no se otorgó aún este año)."""
    try:
        data = json.loads(request.body or '{}')
        cliente = get_object_or_404(Cliente, id=data.get('cliente_id'))
        resultado = fidelizacion_service.otorgar_bono_cumpleanos(
            cliente, usuario=request.user
        )
        if resultado is None:
            return JsonResponse({
                'success': False,
                'error': 'No corresponde bono: hoy no es el cumpleaños del cliente, ya se otorgó este año, o el cliente no tiene fecha de nacimiento registrada.',
            }, status=400)
        return JsonResponse({'success': True, **resultado})
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al otorgar bono de cumpleaños")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@requiere_permiso('fidelizacion_reporte', 'puede_ver')
def api_reporte_fidelizacion(request):
    """KPIs y detalle operativo del programa de puntos."""
    _, _, inicio_dt, fin_dt = _rango_reporte(request)
    try:
        dias_vencimiento = int(request.GET.get('vencen_en') or 30)
    except (TypeError, ValueError):
        dias_vencimiento = 30
    reporte = construir_reporte_fidelizacion(
        fecha_inicio_dt=inicio_dt,
        fecha_fin_dt=fin_dt,
        dias_vencimiento=dias_vencimiento,
        usuario=request.user,
    )
    # Compatibilidad con el listado actual: conserva los KPIs en la raíz.
    return JsonResponse({'success': True, **reporte['resumen'], **reporte})
