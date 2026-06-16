"""
Módulo Gift Cards - RetailMind

Emisión, consulta, recarga y anulación de tarjetas de regalo con saldo.
Vistas HTML (render) + APIs JSON, 100% function-based (patrón del proyecto).
La lógica de saldos vive en `app/services/giftcard_service.py`.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from django.utils import timezone

from .decorators import requiere_permiso
from .models import GiftCard, MovimientoGiftCard, Sucursal, Cliente, Vendedor
from .services import giftcard_service

logger = logging.getLogger('app')


def _sucursal_actual(request):
    sid = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sid:
        return None
    return Sucursal.objects.filter(id=sid).first()


# ========== VISTAS HTML ==========

@requiere_permiso('giftcards_listado', 'puede_ver')
def modulo_giftcards(request):
    """Listado / gestión de gift cards."""
    context = {
        'sucursal_actual': _sucursal_actual(request),
        'estado_choices': GiftCard._meta.get_field('estado').choices,
    }
    return render(request, 'vistas/modulo_giftcards/lista.html', context)


@requiere_permiso('giftcards_emitir', 'puede_crear')
def emitir_giftcard_vista(request):
    """Formulario de emisión de gift card."""
    context = {
        'sucursal_actual': _sucursal_actual(request),
    }
    return render(request, 'vistas/modulo_giftcards/emitir.html', context)


@requiere_permiso('giftcards_listado', 'puede_ver')
def detalle_giftcard_vista(request, giftcard_id):
    """Detalle de una gift card con su historial de movimientos."""
    giftcard = get_object_or_404(GiftCard, id=giftcard_id)
    context = {
        'giftcard': giftcard,
        'movimientos': giftcard.movimientos.all().order_by('-fecha'),
    }
    return render(request, 'vistas/modulo_giftcards/detalle.html', context)


# ========== APIs JSON ==========

@require_POST
@requiere_permiso('giftcards_emitir', 'puede_crear')
def api_emitir_giftcard(request):
    """Emite una nueva gift card."""
    try:
        data = json.loads(request.body or '{}')
        monto = int(data.get('monto') or 0)
        if monto <= 0:
            return JsonResponse({'success': False, 'error': 'Monto inválido.'}, status=400)

        cliente = None
        if data.get('cliente_id'):
            cliente = Cliente.objects.filter(id=data['cliente_id']).first()
        vendedor = None
        if data.get('vendedor_id'):
            vendedor = Vendedor.objects.filter(id=data['vendedor_id']).first()

        vencimiento = None
        if data.get('fecha_vencimiento'):
            vencimiento = data['fecha_vencimiento']

        giftcard = giftcard_service.emitir(
            monto,
            sucursal=_sucursal_actual(request),
            cliente=cliente,
            vendedor=vendedor,
            vencimiento=vencimiento,
            pin=(data.get('pin') or None),
            usuario=request.user,
            observaciones=data.get('observaciones', ''),
        )
        return JsonResponse({
            'success': True,
            'giftcard': {
                'id': giftcard.id,
                'codigo': giftcard.codigo,
                'saldo_actual': giftcard.saldo_actual,
                'fecha_vencimiento': giftcard.fecha_vencimiento.isoformat() if giftcard.fecha_vencimiento else None,
            },
        })
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al emitir gift card")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_listar_giftcards(request):
    """Listado paginado de gift cards con filtros."""
    qs = GiftCard.objects.select_related('cliente', 'sucursal_emision').all()

    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(codigo__icontains=busqueda) |
            Q(cliente__nombre__icontains=busqueda) |
            Q(cliente__apellido__icontains=busqueda) |
            Q(cliente__rut__icontains=busqueda)
        )
    estado = (request.GET.get('estado') or '').strip()
    if estado:
        qs = qs.filter(estado=estado)

    paginator = Paginator(qs, int(request.GET.get('per_page', 20)))
    page = paginator.get_page(request.GET.get('page', 1))

    items = [{
        'id': gc.id,
        'codigo': gc.codigo,
        'saldo_inicial': gc.saldo_inicial,
        'saldo_actual': gc.saldo_actual,
        'estado': gc.estado,
        'estado_display': gc.get_estado_display(),
        'cliente': gc.cliente.nombre_completo if gc.cliente else '',
        'fecha_emision': gc.fecha_emision.strftime('%Y-%m-%d %H:%M'),
        'fecha_vencimiento': gc.fecha_vencimiento.isoformat() if gc.fecha_vencimiento else None,
        'vencida': gc.esta_vencida,
    } for gc in page]

    return JsonResponse({
        'success': True,
        'items': items,
        'page': page.number,
        'num_pages': paginator.num_pages,
        'total': paginator.count,
    })


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_detalle_giftcard(request, giftcard_id):
    """Detalle + historial de una gift card."""
    gc = get_object_or_404(GiftCard, id=giftcard_id)
    movimientos = [{
        'tipo': m.tipo,
        'tipo_display': m.get_tipo_display(),
        'monto': m.monto,
        'saldo_resultante': m.saldo_resultante,
        'fecha': m.fecha.strftime('%Y-%m-%d %H:%M'),
        'ticket': m.ticket.correlativo if m.ticket else None,
        'observaciones': m.observaciones or '',
    } for m in gc.movimientos.all().order_by('-fecha')]

    return JsonResponse({
        'success': True,
        'giftcard': {
            'id': gc.id,
            'codigo': gc.codigo,
            'saldo_inicial': gc.saldo_inicial,
            'saldo_actual': gc.saldo_actual,
            'estado': gc.estado,
            'estado_display': gc.get_estado_display(),
            'cliente': gc.cliente.nombre_completo if gc.cliente else '',
            'fecha_emision': gc.fecha_emision.strftime('%Y-%m-%d %H:%M'),
            'fecha_vencimiento': gc.fecha_vencimiento.isoformat() if gc.fecha_vencimiento else None,
        },
        'movimientos': movimientos,
    })


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_consultar_saldo_giftcard(request):
    """Consulta saldo por código (usada por el POS web y la pantalla de gestión)."""
    codigo = (request.GET.get('codigo') or '').strip()
    if not codigo:
        return JsonResponse({'success': False, 'error': 'Código requerido.'}, status=400)
    try:
        info = giftcard_service.consultar_saldo(codigo)
        return JsonResponse({'success': True, **info})
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)


@require_POST
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_validar_giftcard(request):
    """
    Pre-validación de gift card antes de cobrar (no descuenta).
    La usa el POS al agregar un pago con gift card.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST
    codigo = (data.get('codigo') or '').strip()
    monto = int(data.get('monto') or 0)
    pin = data.get('pin')
    resultado = giftcard_service.validar(codigo, monto, pin=pin)
    return JsonResponse({'success': True, **resultado})


@require_POST
@requiere_permiso('giftcards_emitir', 'puede_crear')
def api_recargar_giftcard(request):
    """Recarga saldo a una gift card existente."""
    try:
        data = json.loads(request.body or '{}')
        codigo = (data.get('codigo') or '').strip()
        monto = int(data.get('monto') or 0)
        mov = giftcard_service.recargar(
            codigo, monto,
            sucursal=_sucursal_actual(request), usuario=request.user,
            observaciones=data.get('observaciones', ''),
        )
        return JsonResponse({'success': True, 'saldo_actual': mov.saldo_resultante})
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
@requiere_permiso('giftcards_emitir', 'puede_editar')
def api_anular_giftcard(request):
    """Anula una gift card (lleva saldo a 0, estado ANULADA)."""
    try:
        data = json.loads(request.body or '{}')
        codigo = (data.get('codigo') or '').strip()
        gc = giftcard_service.anular(
            codigo, usuario=request.user,
            observaciones=data.get('motivo', ''),
        )
        return JsonResponse({'success': True, 'estado': gc.estado})
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_reporte_giftcards(request):
    """KPIs del programa de gift cards."""
    qs = GiftCard.objects.all()
    activas = qs.filter(estado='ACTIVA')
    return JsonResponse({
        'success': True,
        'total_emitidas': qs.count(),
        'activas': activas.count(),
        'saldo_circulante': activas.aggregate(s=Sum('saldo_actual'))['s'] or 0,
        'monto_total_emitido': qs.aggregate(s=Sum('saldo_inicial'))['s'] or 0,
        'por_estado': list(
            qs.values('estado').annotate(n=Count('id')).order_by('-n')
        ),
    })
