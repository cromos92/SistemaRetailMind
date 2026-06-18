"""
Módulo Fidelización (puntos) - RetailMind

Configuración del programa, cuentas de puntos por cliente, ficha con historial,
ajustes manuales y reportes. Vistas HTML + APIs JSON, function-based.
La lógica vive en `app/services/fidelizacion_service.py`.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from django.utils import timezone

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
    ]
    for campo in campos_int:
        if data.get(campo) not in (None, ''):
            setattr(programa, campo, int(data[campo]))
    if data.get('nombre'):
        programa.nombre = data['nombre']
    if data.get('redondeo'):
        programa.redondeo = data['redondeo']
    if data.get('acumula_sobre'):
        programa.acumula_sobre = data['acumula_sobre']
    programa.activo = True
    programa.updated_by = request.user
    programa.save()

    return JsonResponse({
        'success': True,
        'programa_id': programa.id,
        'tasa_descuento_efectiva': programa.tasa_descuento_efectiva,
    })


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


@require_GET
@requiere_permiso('fidelizacion_reporte', 'puede_ver')
def api_reporte_fidelizacion(request):
    """KPIs del programa de puntos."""
    programa = ProgramaFidelizacion.get_activo()
    cuentas = CuentaPuntos.objects.all()
    total_puntos = cuentas.aggregate(s=Sum('saldo_puntos'))['s'] or 0
    valor_pto = programa.valor_punto_en_pesos if programa else 0
    return JsonResponse({
        'success': True,
        'total_clientes': cuentas.count(),
        'puntos_circulantes': total_puntos,
        'pasivo_estimado_pesos': total_puntos * valor_pto,
        'tasa_descuento_efectiva': programa.tasa_descuento_efectiva if programa else 0,
        'acumulaciones_30d': MovimientoPuntos.objects.filter(
            tipo='ACUMULACION',
            fecha__gte=timezone.now() - timezone.timedelta(days=30),
        ).count(),
    })
