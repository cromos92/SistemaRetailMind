"""
Módulo Cupones de Descuento (dentro de Fidelización) - RetailMind

Campañas de cupones y emisión de cupones nominativos a clientes. Vistas HTML +
APIs JSON, function-based. La lógica vive en `app/services/cupon_service.py`.

Un cupón es un código que la empresa REGALA a un cliente concreto para que
obtenga un descuento en caja. Sólo lo puede usar ese cliente, identificándose
con su RUT, y no se combina con ningún otro beneficio.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from .decorators import requiere_permiso, requiere_alguno_de_los_permisos
from .models import (
    CampanaCupon, Cliente, CuponCliente, Empresa, EmpresaUser,
    normalizar_codigo_publico,
)
from .services import cupon_service
from .services.cupon_service import CuponError
from .utils_permisos import usuario_puede_ver_todas_sucursales

logger = logging.getLogger('app')


# ========== HELPERS DE ALCANCE ==========

def _empresa_ids_usuario(usuario):
    """IDs de empresas a las que el usuario tiene acceso (vía EmpresaUser)."""
    return list(
        EmpresaUser.objects
        .filter(user=usuario, status=True, empresa__isnull=False)
        .values_list('empresa_id', flat=True)
        .distinct()
    )


def _empresa_actual(request):
    """Empresa activa en sesión (la que emite los cupones)."""
    eid = request.session.get('idEmpresaActual') or request.session.get('empresaActual')
    if not eid:
        return None
    return Empresa.objects.filter(id=eid).first()


def _empresas_visibles(usuario):
    """Queryset de empresas sobre las que el usuario puede operar cupones."""
    if usuario_puede_ver_todas_sucursales(usuario):
        return Empresa.objects.all().order_by('nombre')
    return Empresa.objects.filter(
        id__in=_empresa_ids_usuario(usuario)).order_by('nombre')


def _campanas_en_alcance(usuario):
    """
    Campañas visibles para el usuario.

    A diferencia de los clientes (donde el histórico migrado sin empresa se
    considera visible para todos), una campaña SIEMPRE tiene empresa: si el
    usuario no la tiene asignada, no la ve.
    """
    qs = CampanaCupon.objects.select_related('empresa')
    if usuario_puede_ver_todas_sucursales(usuario):
        return qs
    return qs.filter(empresa_id__in=_empresa_ids_usuario(usuario))


def _cliente_en_alcance(usuario, cliente):
    """
    Mismo criterio que el módulo de fidelización: quien ve todas las sucursales
    ve todo; el resto ve los clientes de SUS empresas más los que no tienen
    empresa asignada (la mayoría del histórico migrado).
    """
    if cliente is None:
        return False
    if usuario_puede_ver_todas_sucursales(usuario):
        return True
    if cliente.empresa_id is None:
        return True
    return cliente.empresa_id in _empresa_ids_usuario(usuario)


def _serializar_campana(c):
    stats = c.stats if hasattr(c, 'stats') else None
    return {
        'id': c.id,
        'nombre': c.nombre,
        'descripcion': c.descripcion,
        'codigo_publico': c.codigo_publico or '',
        'es_codigo_publico': c.es_codigo_publico,
        'empresa_id': c.empresa_id,
        'empresa': c.empresa.nombre if c.empresa else '',
        'tipo_valor': c.tipo_valor,
        'valor': float(c.valor),
        'valor_display': (f'{c.valor:.0f}%' if c.tipo_valor == 'PORCENTAJE'
                          else f'${int(c.valor):,}'.replace(',', '.')),
        'tope_descuento': c.tope_descuento,
        'monto_minimo': c.monto_minimo,
        'vigencia_dias': c.vigencia_dias,
        'fecha_inicio': c.fecha_inicio.isoformat(),
        'fecha_fin': c.fecha_fin.isoformat(),
        'activo': c.activo,
        'vigente': c.vigente,
        'limite_por_cliente': c.limite_por_cliente,
        'limite_display': c.get_limite_por_cliente_display(),
        'emitidos': getattr(c, 'n_emitidos', 0),
        'canjeados': getattr(c, 'n_canjeados', 0),
        'regalado': getattr(c, 'total_regalado', 0) or 0,
    }


def _serializar_cupon(c):
    return {
        'id': c.id,
        'codigo': c.codigo,
        'campana': c.campana.nombre if c.campana else '',
        'cliente': c.cliente.nombre_completo if c.cliente else '',
        'cliente_id': c.cliente_id,
        'rut': c.cliente.rut if c.cliente else '',
        'estado': c.estado,
        'estado_display': c.get_estado_display(),
        'valor_display': (f'{c.valor:.0f}%' if c.tipo_valor == 'PORCENTAJE'
                          else f'${int(c.valor):,}'.replace(',', '.')),
        'monto_descuento': c.monto_descuento,
        'expira_en': timezone.localtime(c.expira_en).strftime('%d-%m-%Y'),
        'vencido': c.expira_en <= timezone.now() and c.estado == 'PENDIENTE',
        'canjeado_en': (timezone.localtime(c.canjeado_en).strftime('%d-%m-%Y %H:%M')
                        if c.canjeado_en else ''),
        'ticket': c.ticket.correlativo if c.ticket_id else '',
        'created_at': timezone.localtime(c.created_at).strftime('%d-%m-%Y'),
    }


# ========== VISTAS HTML ==========

@requiere_permiso('fidelizacion_cupones', 'puede_ver')
def modulo_cupones(request):
    """
    Pantalla única del módulo: campañas arriba, cupones emitidos abajo.

    Una sola pantalla en vez de tres porque el flujo real es corto: se crea una
    campaña (rara vez) y después se emiten cupones (a diario). Separarlo obligaba
    a navegar entre pantallas para una tarea de dos clics.
    """
    empresa_actual = _empresa_actual(request)
    context = {
        'empresas': _empresas_visibles(request.user),
        'empresa_actual': empresa_actual,
        'tipo_valor_choices': CampanaCupon._meta.get_field('tipo_valor').choices,
        'limite_choices': CampanaCupon._meta.get_field('limite_por_cliente').choices,
        'estado_choices': CuponCliente._meta.get_field('estado').choices,
        'origen_choices': CuponCliente._meta.get_field('origen').choices,
    }
    return render(request, 'vistas/modulo_fidelizacion/cupones.html', context)


# ========== APIs: CAMPAÑAS ==========

@require_GET
@requiere_permiso('fidelizacion_cupones', 'puede_ver')
def api_listar_campanas(request):
    """Campañas del usuario con sus contadores de emisión/canje."""
    qs = _campanas_en_alcance(request.user).annotate(
        n_emitidos=Count('cupones', distinct=True),
        n_canjeados=Count('cupones', filter=Q(cupones__estado='CANJEADO'),
                          distinct=True),
        total_regalado=Sum('cupones__monto_descuento'),
    )

    solo_activas = (request.GET.get('activas') or '').lower() in ('1', 'true')
    if solo_activas:
        qs = qs.filter(activo=True)

    campanas = [_serializar_campana(c) for c in qs]
    return JsonResponse({
        'success': True,
        'campanas': campanas,
        'resumen': {
            'campanas': len(campanas),
            'emitidos': sum(c['emitidos'] for c in campanas),
            'canjeados': sum(c['canjeados'] for c in campanas),
            'regalado': sum(c['regalado'] for c in campanas),
        },
    })


@require_POST
@requiere_permiso('fidelizacion_cupones', 'puede_crear')
def api_guardar_campana(request):
    """Crea o actualiza una campaña de cupones."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    campana_id = data.get('id')
    if campana_id:
        campana = _campanas_en_alcance(request.user).filter(id=campana_id).first()
        if not campana:
            return JsonResponse(
                {'success': False, 'error': 'Campaña no encontrada o fuera de tu alcance.'},
                status=404)
    else:
        campana = CampanaCupon(created_by=request.user)

    # --- Empresa: define el alcance del cupón, no se puede cambiar después ---
    if not campana.pk:
        empresa_id = data.get('empresa_id') or getattr(_empresa_actual(request), 'id', None)
        if not empresa_id:
            return JsonResponse(
                {'success': False,
                 'error': 'Selecciona la empresa a la que pertenece la campaña.'},
                status=400)
        if not _empresas_visibles(request.user).filter(id=empresa_id).exists():
            return JsonResponse(
                {'success': False, 'error': 'No puedes crear campañas para esa empresa.'},
                status=403)
        campana.empresa_id = empresa_id

    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return JsonResponse({'success': False, 'error': 'El nombre es obligatorio.'},
                            status=400)
    campana.nombre = nombre
    campana.descripcion = (data.get('descripcion') or '').strip()

    # --- Código público: define el MODO de la campaña ---
    # Con código → se publica uno solo y cualquier cliente lo presenta en caja.
    # Sin código → nominativa: se emite uno distinto a cada cliente.
    codigo_publico = normalizar_codigo_publico(data.get('codigo_publico'))
    if codigo_publico:
        if len(codigo_publico) < 4:
            return JsonResponse(
                {'success': False,
                 'error': 'El código debe tener al menos 4 caracteres.'},
                status=400)
        # Guardrail: si choca con un cupón nominativo ya emitido, en caja
        # `resolver_codigo` le daría prioridad al cupón individual y este código
        # nunca funcionaría. Mejor no dejar crearlo.
        if CuponCliente.objects.filter(codigo=codigo_publico).exists():
            return JsonResponse(
                {'success': False,
                 'error': f'El código {codigo_publico} choca con un cupón ya '
                          f'emitido. Elige otro.'},
                status=400)
        choque = CampanaCupon.objects.filter(codigo_publico=codigo_publico)
        if campana.pk:
            choque = choque.exclude(pk=campana.pk)
        if choque.exists():
            return JsonResponse(
                {'success': False,
                 'error': f'Ya existe otra campaña con el código {codigo_publico}.'},
                status=400)

    # Cambiar de modo con cupones ya emitidos deja al cliente con un código en la
    # mano que dejaría de servir (o al revés). Se bloquea; para eso está crear
    # una campaña nueva.
    if campana.pk and bool(campana.codigo_publico) != bool(codigo_publico):
        if campana.cupones.exists():
            return JsonResponse(
                {'success': False,
                 'error': 'Esta campaña ya tiene cupones emitidos o canjeados: no '
                          'se puede cambiar entre código público y nominativa. '
                          'Crea una campaña nueva.'},
                status=400)

    campana.codigo_publico = codigo_publico

    tipo_valor = (data.get('tipo_valor') or 'PORCENTAJE').upper()
    if tipo_valor not in dict(CampanaCupon._meta.get_field('tipo_valor').choices):
        return JsonResponse({'success': False, 'error': 'Tipo de descuento inválido.'},
                            status=400)
    campana.tipo_valor = tipo_valor

    try:
        valor = float(data.get('valor') or 0)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Valor inválido.'}, status=400)
    if valor <= 0:
        return JsonResponse({'success': False, 'error': 'El valor debe ser mayor a 0.'},
                            status=400)
    # Guardrail: un "5%" tecleado como "50" es un error de $ grande y silencioso.
    if tipo_valor == 'PORCENTAJE' and valor > 100:
        return JsonResponse(
            {'success': False, 'error': 'Un porcentaje no puede superar 100%.'},
            status=400)
    campana.valor = valor

    for campo in ('tope_descuento', 'monto_minimo', 'vigencia_dias'):
        bruto = data.get(campo)
        if bruto in (None, ''):
            if campo == 'tope_descuento':
                campana.tope_descuento = None
            continue
        try:
            setattr(campana, campo, int(float(bruto)))
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': f'{campo} inválido.'},
                                status=400)
    if campana.vigencia_dias <= 0:
        return JsonResponse(
            {'success': False, 'error': 'La vigencia debe ser de al menos 1 día.'},
            status=400)

    fi = parse_date(data.get('fecha_inicio') or '')
    ff = parse_date(data.get('fecha_fin') or '')
    if not fi or not ff:
        return JsonResponse(
            {'success': False, 'error': 'Indica desde y hasta cuándo se emiten cupones.'},
            status=400)
    if ff < fi:
        return JsonResponse(
            {'success': False, 'error': 'La fecha de término no puede ser anterior al inicio.'},
            status=400)
    campana.fecha_inicio, campana.fecha_fin = fi, ff

    limite = (data.get('limite_por_cliente') or 'UNICO').upper()
    if limite not in dict(CampanaCupon._meta.get_field('limite_por_cliente').choices):
        return JsonResponse({'success': False, 'error': 'Límite por cliente inválido.'},
                            status=400)
    campana.limite_por_cliente = limite
    campana.activo = bool(data.get('activo', True))

    campana.save()
    logger.info('Campaña de cupones guardada id=%s nombre=%s usuario=%s',
                campana.id, campana.nombre, request.user.username)
    return JsonResponse({'success': True, 'campana': _serializar_campana(campana)})


@require_POST
@requiere_permiso('fidelizacion_cupones', 'puede_editar')
def api_toggle_campana(request, campana_id):
    """Activa/desactiva una campaña (no borra: los cupones emitidos siguen vivos)."""
    campana = _campanas_en_alcance(request.user).filter(id=campana_id).first()
    if not campana:
        return JsonResponse({'success': False, 'error': 'Campaña no encontrada.'},
                            status=404)
    campana.activo = not campana.activo
    campana.save(update_fields=['activo', 'updated_at'])
    logger.info('Campaña %s %s por %s', campana.id,
                'activada' if campana.activo else 'desactivada', request.user.username)
    return JsonResponse({'success': True, 'activo': campana.activo})


# ========== APIs: EMISIÓN ==========

@require_GET
@requiere_permiso('fidelizacion_cupones', 'puede_ver')
def api_buscar_cliente_cupon(request):
    """
    Busca clientes por RUT, nombre o email para emitirles un cupón, e informa si
    su ficha está completa. Devolver el motivo acá evita que el usuario descubra
    el problema recién al apretar "Emitir".
    """
    termino = (request.GET.get('q') or '').strip()
    if len(termino) < 3:
        return JsonResponse({'success': True, 'clientes': []})

    qs = Cliente.objects.filter(activo=True).filter(
        Q(rut__icontains=termino) | Q(nombre__icontains=termino)
        | Q(apellido__icontains=termino) | Q(email__icontains=termino)
    )
    if not usuario_puede_ver_todas_sucursales(request.user):
        empresas = _empresa_ids_usuario(request.user)
        qs = qs.filter(Q(empresa__isnull=True) | Q(empresa_id__in=empresas))

    clientes = []
    for c in qs.order_by('apellido', 'nombre')[:20]:
        ok, faltantes = cupon_service.ficha_completa(c)
        clientes.append({
            'id': c.id,
            'nombre': c.nombre_completo,
            'rut': c.rut or '',
            'email': c.email or '',
            'celular': c.celular or '',
            'ficha_ok': ok,
            'faltantes': faltantes,
        })
    return JsonResponse({'success': True, 'clientes': clientes})


@require_POST
@requiere_permiso('fidelizacion_cupones', 'puede_crear')
def api_emitir_cupon(request):
    """
    Emite un cupón nominativo. Body: {"campana_id": 1, "cliente_id": 382}

    Idempotente vía header `Idempotency-Key` o campo homónimo: un doble clic no
    quema dos cupones (y con límite UNICO, el segundo sería irrecuperable).
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    campana = _campanas_en_alcance(request.user).filter(
        id=data.get('campana_id')).first()
    if not campana:
        return JsonResponse(
            {'success': False, 'error': 'Campaña no encontrada o fuera de tu alcance.'},
            status=404)

    cliente = Cliente.objects.filter(id=data.get('cliente_id')).first()
    if not _cliente_en_alcance(request.user, cliente):
        return JsonResponse(
            {'success': False, 'error': 'El cliente no pertenece a tus empresas.'},
            status=403)

    idem = (request.headers.get('Idempotency-Key')
            or (data.get('idempotency_key') or '').strip() or None)
    try:
        cupon = cupon_service.emitir_cupon(
            campana, cliente, usuario=request.user, idempotency_key=idem)
    except CuponError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': True, 'cupon': _serializar_cupon(cupon)})


@require_POST
@requiere_permiso('fidelizacion_cupones', 'puede_crear')
def api_emitir_lote_cupones(request):
    """
    Emisión masiva: {"campana_id": 1, "cliente_ids": [1,2,3]}.

    Devuelve emitidos y omitidos con su motivo. No falla en bloque: un cliente
    con ficha incompleta no debe frenar a los otros 200.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    campana = _campanas_en_alcance(request.user).filter(
        id=data.get('campana_id')).first()
    if not campana:
        return JsonResponse(
            {'success': False, 'error': 'Campaña no encontrada o fuera de tu alcance.'},
            status=404)

    ids = data.get('cliente_ids') or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'success': False, 'error': 'Selecciona al menos un cliente.'},
                            status=400)
    # Tope defensivo: la emisión serializa por campaña (lock), así que un lote
    # gigante bloquearía la tabla. Para más volumen, un command batch.
    if len(ids) > 500:
        return JsonResponse(
            {'success': False,
             'error': 'Máximo 500 clientes por lote. Divide la emisión.'},
            status=400)

    clientes = [c for c in Cliente.objects.filter(id__in=ids)
                if _cliente_en_alcance(request.user, c)]
    res = cupon_service.emitir_lote(campana, clientes, usuario=request.user)

    return JsonResponse({
        'success': True,
        'emitidos': [_serializar_cupon(c) for c in res['emitidos']],
        'omitidos': [{'cliente': c.nombre_completo, 'motivo': m}
                     for c, m in res['omitidos']],
    })


# ========== API DE CAJA ==========

@require_GET
@requiere_alguno_de_los_permisos(
    ('ticket_venta', 'puede_crear'), ('fidelizacion_cupones', 'puede_ver'),
)
def api_validar_cupon_caja(request, codigo):
    """
    Valida un cupón para la venta que el cajero tiene en pantalla, SIN consumirlo.

    Query params: `monto` (total actual del ticket), `rut` (RUT del cliente de
    la venta) y los flags `dcto_manual` / `vale` para la exclusión.

    El `rut` es obligatorio: sin él no se puede verificar que quien presenta el
    código sea su dueño, que es toda la protección de un cupón nominativo.

    Permiso: mismo criterio que `api_validar_vale_canje` — leer un código que el
    cliente acaba de mostrar es capacidad de caja, no de administración. Si se
    exigiera sólo 'fidelizacion_cupones', los cajeros recibirían 403 y ningún
    cupón podría usarse jamás (el error que ya se pagó con los vales).
    """
    try:
        monto = int(float(request.GET.get('monto') or 0))
    except (TypeError, ValueError):
        monto = 0

    sucursal = None
    sucursal_id = (request.session.get('idSucursalActual')
                   or request.session.get('sucursalActual')
                   or request.session.get('idSucursalActualPOS'))
    if sucursal_id:
        from .models import Sucursal
        sucursal = Sucursal.objects.filter(id=sucursal_id).first()

    def _flag(nombre):
        return (request.GET.get(nombre) or '').lower() in ('1', 'true', 'yes')

    info = cupon_service.validar_cupon(
        codigo,
        monto=monto,
        rut_cliente=request.GET.get('rut'),
        sucursal=sucursal,
        tiene_dcto_manual=_flag('dcto_manual'),
        tiene_vale_puntos=_flag('vale'),
    )
    return JsonResponse({'success': True, **info})


# ========== APIs: CUPONES EMITIDOS ==========

@require_GET
@requiere_permiso('fidelizacion_cupones', 'puede_ver')
def api_listar_cupones(request):
    """Listado paginado de cupones emitidos, con filtros."""
    qs = CuponCliente.objects.select_related('campana', 'cliente', 'ticket')
    if not usuario_puede_ver_todas_sucursales(request.user):
        qs = qs.filter(empresa_id__in=_empresa_ids_usuario(request.user))

    campana_id = request.GET.get('campana')
    if campana_id:
        qs = qs.filter(campana_id=campana_id)

    estado = (request.GET.get('estado') or '').upper()
    if estado:
        qs = qs.filter(estado=estado)

    termino = (request.GET.get('q') or '').strip()
    if termino:
        qs = qs.filter(
            Q(codigo__icontains=termino) | Q(rut_cliente__icontains=termino)
            | Q(cliente__nombre__icontains=termino)
            | Q(cliente__apellido__icontains=termino)
        )

    try:
        page_size = min(int(request.GET.get('page_size') or 25), 200)
    except (TypeError, ValueError):
        page_size = 25
    paginator = Paginator(qs.order_by('-created_at'), page_size)
    page = paginator.get_page(request.GET.get('page') or 1)

    return JsonResponse({
        'success': True,
        'cupones': [_serializar_cupon(c) for c in page.object_list],
        'total': paginator.count,
        'page': page.number,
        'total_pages': paginator.num_pages,
    })


@require_POST
@requiere_permiso('fidelizacion_cupones', 'puede_eliminar')
def api_anular_cupon(request, cupon_id):
    """
    Anula un cupón PENDIENTE (error de emisión, cliente equivocado).

    Un cupón CANJEADO no se anula: ya rebajó una venta y tocarlo descuadraría el
    documento. Para eso está la anulación del ticket, que lo libera.
    """
    cupon = CuponCliente.objects.filter(id=cupon_id).first()
    if not cupon:
        return JsonResponse({'success': False, 'error': 'Cupón no encontrado.'},
                            status=404)
    if (not usuario_puede_ver_todas_sucursales(request.user)
            and cupon.empresa_id not in _empresa_ids_usuario(request.user)):
        return JsonResponse({'success': False, 'error': 'Fuera de tu alcance.'},
                            status=403)
    if cupon.estado == 'CANJEADO':
        return JsonResponse(
            {'success': False,
             'error': 'Este cupón ya se usó en una venta. Para revertirlo hay que '
                      'anular el ticket.'},
            status=400)

    cupon.estado = 'ANULADO'
    cupon.save(update_fields=['estado', 'updated_at'])
    logger.info('Cupón anulado codigo=%s por usuario=%s', cupon.codigo,
                request.user.username)
    return JsonResponse({'success': True})
