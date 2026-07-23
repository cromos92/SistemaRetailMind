"""
Módulo Gift Cards - RetailMind

Emisión, consulta, recarga y anulación de tarjetas de regalo con saldo.
Vistas HTML (render) + APIs JSON, 100% function-based (patrón del proyecto).
La lógica de saldos vive en `app/services/giftcard_service.py`.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from django.utils import timezone

from .decorators import requiere_permiso
from .models import GiftCard, MovimientoGiftCard, Sucursal, Cliente, Vendedor, Empresa
from .services import giftcard_service, fidelizacion_service

logger = logging.getLogger('app')


def _sucursal_actual(request):
    sid = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sid:
        return None
    return Sucursal.objects.filter(id=sid).first()


def _empresa_actual(request):
    eid = request.session.get('idEmpresaActual') or request.session.get('empresaActual')
    if not eid:
        return None
    return Empresa.objects.filter(id=eid).first()


def _aplicar_filtros_giftcards(qs, request):
    """Filtros compartidos por el listado y la exportación de gift cards."""
    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(codigo__icontains=busqueda) |
            Q(codigo_fisico__icontains=busqueda) |
            Q(cliente__nombre__icontains=busqueda) |
            Q(cliente__apellido__icontains=busqueda) |
            Q(cliente__rut__icontains=busqueda) |
            Q(descripcion__icontains=busqueda)
        )
    estado = (request.GET.get('estado') or '').strip()
    if estado:
        qs = qs.filter(estado=estado)
    motivo = (request.GET.get('motivo') or '').strip()
    if motivo:
        qs = qs.filter(motivo=motivo)
    desde = (request.GET.get('desde') or '').strip()
    if desde:
        qs = qs.filter(fecha_emision__date__gte=desde)
    hasta = (request.GET.get('hasta') or '').strip()
    if hasta:
        qs = qs.filter(fecha_emision__date__lte=hasta)
    return qs


def _aplicar_filtros_trazabilidad(qs, request):
    """Filtros compartidos por la trazabilidad global y su exportación."""
    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(giftcard__codigo__icontains=busqueda) |
            Q(giftcard__codigo_fisico__icontains=busqueda) |
            Q(giftcard__cliente__nombre__icontains=busqueda) |
            Q(giftcard__cliente__rut__icontains=busqueda)
        )
    tipo = (request.GET.get('tipo') or '').strip()
    if tipo:
        qs = qs.filter(tipo=tipo)
    sucursal_id = (request.GET.get('sucursal_id') or '').strip()
    if sucursal_id:
        qs = qs.filter(sucursal_id=sucursal_id)
    usuario = (request.GET.get('usuario') or '').strip()
    if usuario:
        qs = qs.filter(usuario__username__icontains=usuario)
    desde = (request.GET.get('desde') or '').strip()
    if desde:
        qs = qs.filter(fecha__date__gte=desde)
    hasta = (request.GET.get('hasta') or '').strip()
    if hasta:
        qs = qs.filter(fecha__date__lte=hasta)
    return qs


# ========== VISTAS HTML ==========

@requiere_permiso('giftcards_listado', 'puede_ver')
def modulo_giftcards(request):
    """Listado / gestión de gift cards. La emisión se hace en un modal aquí."""
    context = {
        'sucursal_actual': _sucursal_actual(request),
        'estado_choices': GiftCard._meta.get_field('estado').choices,
        'tipo_tarjeta_choices': GiftCard._meta.get_field('tipo_tarjeta').choices,
        'motivo_choices': GiftCard._meta.get_field('motivo').choices,
    }
    return render(request, 'vistas/modulo_giftcards/lista.html', context)


@requiere_permiso('giftcards_emitir', 'puede_crear')
def emitir_giftcard_vista(request):
    """
    Emisión movida a un modal dentro del listado. Esta ruta se conserva (la usa
    el menú/permiso `giftcards_emitir`) y redirige al listado abriendo el modal.
    """
    return redirect('/app/giftcards/?nueva=1')


@requiere_permiso('giftcards_listado', 'puede_ver')
def detalle_giftcard_vista(request, giftcard_id):
    """Detalle de una gift card con su historial de movimientos."""
    giftcard = get_object_or_404(GiftCard, id=giftcard_id)
    context = {
        'giftcard': giftcard,
        'movimientos': giftcard.movimientos.select_related(
            'usuario', 'sucursal', 'ticket'
        ).order_by('-fecha'),
    }
    return render(request, 'vistas/modulo_giftcards/detalle.html', context)


# ========== APIs JSON ==========

@require_POST
@requiere_permiso('giftcards_emitir', 'puede_crear')
def api_emitir_giftcard(request):
    """
    Emite una nueva gift card desde el modal del listado.

    Tipos:
    - DIGITAL: requiere un cliente (se resuelve por RUT o cliente_id).
    - FISICA:  requiere `codigo_fisico` (impreso en la tarjeta); el RUT/cliente
      es opcional (se puede vincular al canjear).
    """
    try:
        data = json.loads(request.body or '{}')
        monto = int(data.get('monto') or 0)
        if monto <= 0:
            return JsonResponse({'success': False, 'error': 'Monto inválido.'}, status=400)

        tipo_tarjeta = (data.get('tipo_tarjeta') or 'DIGITAL').upper()

        # Resolver titular: por cliente_id explícito o por RUT.
        cliente = None
        if data.get('cliente_id'):
            cliente = Cliente.objects.filter(id=data['cliente_id']).first()
        rut = (data.get('rut') or '').strip()
        nombre_nuevo = (data.get('nombre') or '').strip()
        if cliente is None and rut:
            cliente = fidelizacion_service.resolver_cliente_por_rut(rut)
            if cliente is None:
                if nombre_nuevo:
                    # El RUT no existe: crear el cliente al vuelo (mínimo nombre + RUT).
                    try:
                        cliente, _, _ = fidelizacion_service.registrar_cliente_manual(
                            nombre=nombre_nuevo,
                            rut=rut,
                            usuario=request.user,
                            empresa=_empresa_actual(request),
                        )
                    except fidelizacion_service.FidelizacionError as e:
                        return JsonResponse({'success': False, 'error': str(e)}, status=400)
                elif tipo_tarjeta == 'DIGITAL':
                    # Pedir el nombre para poder crearlo (lo maneja el modal).
                    return JsonResponse({
                        'success': False,
                        'need_nombre': True,
                        'error': 'No existe un cliente con ese RUT. Ingresa el nombre para crearlo.',
                    }, status=400)
                # FÍSICA con RUT no encontrado y sin nombre → se emite sin titular.

        if tipo_tarjeta == 'DIGITAL' and cliente is None:
            return JsonResponse({
                'success': False,
                'error': 'Para una gift card digital debes indicar el RUT del cliente.',
            }, status=400)

        vendedor = None
        if data.get('vendedor_id'):
            vendedor = Vendedor.objects.filter(id=data['vendedor_id']).first()

        vencimiento = data.get('fecha_vencimiento') or None

        giftcard = giftcard_service.emitir(
            monto,
            sucursal=_sucursal_actual(request),
            cliente=cliente,
            vendedor=vendedor,
            vencimiento=vencimiento,
            pin=(data.get('pin') or None),
            usuario=request.user,
            observaciones=data.get('observaciones', ''),
            tipo_tarjeta=tipo_tarjeta,
            codigo_fisico=(data.get('codigo_fisico') or None),
            motivo=(data.get('motivo') or 'OTRO'),
            descripcion=(data.get('descripcion') or ''),
        )
        return JsonResponse({
            'success': True,
            'giftcard': {
                'id': giftcard.id,
                'codigo': giftcard.codigo,
                'codigo_fisico': giftcard.codigo_fisico,
                'tipo_tarjeta': giftcard.tipo_tarjeta,
                'cliente': giftcard.cliente.nombre_completo if giftcard.cliente else '',
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
    qs = _aplicar_filtros_giftcards(qs, request)

    paginator = Paginator(qs, int(request.GET.get('per_page', 20)))
    page = paginator.get_page(request.GET.get('page', 1))

    items = [{
        'id': gc.id,
        'codigo': gc.codigo,
        'codigo_fisico': gc.codigo_fisico,
        'tipo_tarjeta': gc.tipo_tarjeta,
        'tipo_display': gc.get_tipo_tarjeta_display(),
        'motivo': gc.motivo,
        'motivo_display': gc.get_motivo_display(),
        'descripcion': gc.descripcion or '',
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
        'usuario': m.usuario.get_username() if m.usuario else '',
        'sucursal': m.sucursal.nombre if m.sucursal else '',
        'observaciones': m.observaciones or '',
    } for m in gc.movimientos.select_related('usuario', 'sucursal', 'ticket').order_by('-fecha')]

    return JsonResponse({
        'success': True,
        'giftcard': {
            'id': gc.id,
            'codigo': gc.codigo,
            'codigo_fisico': gc.codigo_fisico,
            'tipo_tarjeta': gc.tipo_tarjeta,
            'tipo_display': gc.get_tipo_tarjeta_display(),
            'motivo': gc.motivo,
            'motivo_display': gc.get_motivo_display(),
            'descripcion': gc.descripcion or '',
            'observaciones': gc.observaciones or '',
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


@require_POST
@requiere_permiso('giftcards_emitir', 'puede_editar')
def api_bloquear_giftcard(request):
    """Bloquea temporalmente una gift card (estado BLOQUEADA, reversible)."""
    try:
        data = json.loads(request.body or '{}')
        codigo = (data.get('codigo') or '').strip()
        gc = giftcard_service.bloquear(
            codigo, usuario=request.user,
            observaciones=data.get('motivo', ''),
        )
        return JsonResponse({'success': True, 'estado': gc.estado})
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
@requiere_permiso('giftcards_emitir', 'puede_editar')
def api_desbloquear_giftcard(request):
    """Levanta el bloqueo de una gift card."""
    try:
        data = json.loads(request.body or '{}')
        codigo = (data.get('codigo') or '').strip()
        gc = giftcard_service.desbloquear(
            codigo, usuario=request.user,
            observaciones=data.get('motivo', ''),
        )
        return JsonResponse({'success': True, 'estado': gc.estado})
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@require_POST
@requiere_permiso('giftcards_emitir', 'puede_editar')
def api_editar_giftcard(request):
    """Edita la descripción / motivo / observaciones de una gift card emitida."""
    try:
        data = json.loads(request.body or '{}')
        codigo = (data.get('codigo') or '').strip()
        gc = giftcard_service.editar(
            codigo,
            descripcion=data.get('descripcion'),
            motivo=data.get('motivo'),
            observaciones=data.get('observaciones'),
            usuario=request.user,
        )
        return JsonResponse({
            'success': True,
            'motivo': gc.motivo,
            'motivo_display': gc.get_motivo_display(),
            'descripcion': gc.descripcion or '',
        })
    except giftcard_service.GiftCardError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


# ========== TRAZABILIDAD GLOBAL ==========

@requiere_permiso('giftcards_listado', 'puede_ver')
def trazabilidad_giftcards_vista(request):
    """Log global de uso de gift cards (todas las tarjetas), con filtros."""
    context = {
        'sucursales': Sucursal.objects.all().order_by('nombre'),
        'tipo_mov_choices': MovimientoGiftCard._meta.get_field('tipo').choices,
    }
    return render(request, 'vistas/modulo_giftcards/trazabilidad.html', context)


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_trazabilidad_giftcards(request):
    """Listado paginado del ledger de TODAS las gift cards, con filtros."""
    qs = MovimientoGiftCard.objects.select_related(
        'giftcard', 'giftcard__cliente', 'ticket', 'usuario', 'sucursal'
    ).all()
    qs = _aplicar_filtros_trazabilidad(qs, request).order_by('-fecha')

    paginator = Paginator(qs, int(request.GET.get('per_page', 30)))
    page = paginator.get_page(request.GET.get('page', 1))

    items = [{
        'fecha': m.fecha.strftime('%Y-%m-%d %H:%M'),
        'codigo': m.giftcard.codigo,
        'giftcard_id': m.giftcard_id,
        'cliente': m.giftcard.cliente.nombre_completo if m.giftcard.cliente else '',
        'tipo': m.tipo,
        'tipo_display': m.get_tipo_display(),
        'monto': m.monto,
        'saldo_resultante': m.saldo_resultante,
        'ticket': m.ticket.correlativo if m.ticket else None,
        'usuario': str(m.usuario) if m.usuario else '',
        'sucursal': m.sucursal.nombre if m.sucursal else '',
        'observaciones': m.observaciones or '',
    } for m in page]

    return JsonResponse({
        'success': True,
        'items': items,
        'page': page.number,
        'num_pages': paginator.num_pages,
        'total': paginator.count,
    })


# ========== EXPORTES A EXCEL ==========

def _excel_response(wb, filename):
    from io import BytesIO
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _estilar_encabezados(ws, headers):
    import openpyxl  # noqa: F401  (asegura dependencia disponible)
    from openpyxl.styles import Font, PatternFill, Alignment
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="405189", end_color="405189", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_exportar_giftcards(request):
    """Exporta el listado de gift cards (respeta filtros) a Excel."""
    import openpyxl
    qs = GiftCard.objects.select_related('cliente', 'sucursal_emision').all()
    qs = _aplicar_filtros_giftcards(qs, request)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gift Cards"
    headers = [
        'Código', 'Código físico', 'Tipo', 'Motivo', 'Descripción', 'Cliente',
        'Saldo inicial', 'Saldo actual', 'Estado', 'Emisión', 'Vencimiento', 'Sucursal',
    ]
    _estilar_encabezados(ws, headers)
    for row, gc in enumerate(qs.iterator(), 2):
        ws.cell(row=row, column=1, value=gc.codigo)
        ws.cell(row=row, column=2, value=gc.codigo_fisico or '')
        ws.cell(row=row, column=3, value=gc.get_tipo_tarjeta_display())
        ws.cell(row=row, column=4, value=gc.get_motivo_display())
        ws.cell(row=row, column=5, value=gc.descripcion or '')
        ws.cell(row=row, column=6, value=gc.cliente.nombre_completo if gc.cliente else '')
        ws.cell(row=row, column=7, value=gc.saldo_inicial)
        ws.cell(row=row, column=8, value=gc.saldo_actual)
        ws.cell(row=row, column=9, value=gc.get_estado_display())
        ws.cell(row=row, column=10, value=gc.fecha_emision.strftime('%Y-%m-%d %H:%M'))
        ws.cell(row=row, column=11, value=gc.fecha_vencimiento.strftime('%Y-%m-%d') if gc.fecha_vencimiento else '')
        ws.cell(row=row, column=12, value=gc.sucursal_emision.nombre if gc.sucursal_emision else '')
    return _excel_response(wb, 'giftcards.xlsx')


@require_GET
@requiere_permiso('giftcards_listado', 'puede_ver')
def api_exportar_trazabilidad(request):
    """Exporta la trazabilidad global de uso (respeta filtros) a Excel."""
    import openpyxl
    qs = MovimientoGiftCard.objects.select_related(
        'giftcard', 'giftcard__cliente', 'ticket', 'usuario', 'sucursal'
    ).all()
    qs = _aplicar_filtros_trazabilidad(qs, request).order_by('-fecha')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Trazabilidad Gift Cards"
    headers = [
        'Fecha', 'Código', 'Cliente', 'Tipo', 'Monto', 'Saldo', 'Ticket',
        'Usuario', 'Sucursal', 'Observaciones',
    ]
    _estilar_encabezados(ws, headers)
    for row, m in enumerate(qs.iterator(), 2):
        ws.cell(row=row, column=1, value=m.fecha.strftime('%Y-%m-%d %H:%M'))
        ws.cell(row=row, column=2, value=m.giftcard.codigo)
        ws.cell(row=row, column=3, value=m.giftcard.cliente.nombre_completo if m.giftcard.cliente else '')
        ws.cell(row=row, column=4, value=m.get_tipo_display())
        ws.cell(row=row, column=5, value=m.monto)
        ws.cell(row=row, column=6, value=m.saldo_resultante)
        ws.cell(row=row, column=7, value=m.ticket.correlativo if m.ticket else '')
        ws.cell(row=row, column=8, value=str(m.usuario) if m.usuario else '')
        ws.cell(row=row, column=9, value=m.sucursal.nombre if m.sucursal else '')
        ws.cell(row=row, column=10, value=m.observaciones or '')
    return _excel_response(wb, 'trazabilidad_giftcards.xlsx')
