"""Vistas Mercado Pago presencial: cobro QR desde el POS, webhook firmado y
pantalla Dineros (pendiente de liberación / liberado / depositado).

Espejo estructural de views_transbank_sdk.py: DRF para los endpoints del POS
(sesión + login) y vista Django plana csrf_exempt para el webhook (viene de
los servidores de MP, sin sesión).
"""
import json
import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import MercadoPagoConfig, RetiroMercadoPago, TransaccionMercadoPago
from .services import mercadopago_service as mp
from .services.mercadopago_service import MercadoPagoError

logger = logging.getLogger('app')


def _sucursal_sesion(request):
    return (request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
            or request.session.get('idSucursalActualPOS'))


# ==================== COBRO DESDE EL POS ====================

@api_view(['POST'])
@login_required
def crear_pago_qr_mp(request):
    """POST /app/pos/mercadopago/qr/crear/  Body: {correlativo, monto}

    Crea la orden QR en MP y devuelve el QR para mostrar en el paso 3 del POS.
    """
    sucursal_id = _sucursal_sesion(request)
    if not sucursal_id:
        return Response({'success': False, 'error': 'No hay sucursal en sesión'},
                        status=status.HTTP_400_BAD_REQUEST)
    correlativo = str(request.data.get('correlativo') or '').strip()
    if not correlativo:
        return Response({'success': False, 'error': 'Falta el correlativo del ticket'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        monto = int(request.data.get('monto'))
    except (TypeError, ValueError):
        return Response({'success': False, 'error': 'Monto inválido'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        config = mp.obtener_config(sucursal_id)
        transaccion, qr_data = mp.crear_orden(
            config, correlativo, monto,
            descripcion=f'Venta {correlativo}', usuario=request.user,
        )
    except MercadoPagoError as e:
        return Response({'success': False, 'error': e.mensaje},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({
        'success': True,
        'transaccion_id': transaccion.id,
        'external_reference': transaccion.external_reference,
        'qr_data': qr_data,
        'qr_base64': mp.qr_png_base64(qr_data),
        'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS,
    })


def _transaccion_de_sesion(request, transaccion_id):
    """Solo transacciones de la sucursal en sesión (evita IDOR entre tiendas)."""
    sucursal_id = _sucursal_sesion(request)
    if not sucursal_id:
        return None
    return TransaccionMercadoPago.objects.filter(
        id=transaccion_id, sucursal_id=sucursal_id
    ).select_related('config').first()


@api_view(['GET'])
@login_required
def estado_pago_mp(request, transaccion_id):
    """GET /app/pos/mercadopago/estado/<id>/ — polling del POS (cada 2-3 s)."""
    transaccion = _transaccion_de_sesion(request, transaccion_id)
    if not transaccion:
        return Response({'success': False, 'error': 'Transacción no encontrada'},
                        status=status.HTTP_404_NOT_FOUND)
    transaccion = mp.consultar_estado(transaccion)
    return Response({
        'success': True,
        'estado': transaccion.estado,
        'estado_detalle': transaccion.estado_detalle,
        'payment_id': transaccion.payment_id,
        'metodo_pago_mp': transaccion.metodo_pago_mp,
        'ultimos_4_digitos': transaccion.ultimos_4_digitos,
        'codigo_autorizacion': transaccion.codigo_autorizacion,
        'monto': transaccion.monto,
    })


@api_view(['POST'])
@login_required
def cancelar_pago_mp(request, transaccion_id):
    """POST /app/pos/mercadopago/cancelar/<id>/ — botón Cancelar del modal QR."""
    transaccion = _transaccion_de_sesion(request, transaccion_id)
    if not transaccion:
        return Response({'success': False, 'error': 'Transacción no encontrada'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        transaccion = mp.cancelar(transaccion)
    except MercadoPagoError as e:
        return Response({'success': False, 'error': e.mensaje, 'estado': transaccion.estado},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'success': True, 'estado': transaccion.estado})


# ==================== WEBHOOK (sin sesión, viene de MP) ====================

@csrf_exempt
@require_POST
def webhook_mercadopago(request):
    """POST /app/pos/mercadopago/webhook/

    SIEMPRE responde 200 en <22s: la firma inválida se registra y se ignora
    (no dar señal al emisor), un error interno se loggea y el polling actúa
    como red de seguridad. La idempotencia vive en procesar_notificacion.
    """
    try:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            payload = {}
        data_id = (
            request.GET.get('data.id') or request.GET.get('id')
            or (payload.get('data') or {}).get('id') or payload.get('id') or ''
        )
        topic = (
            request.GET.get('type') or request.GET.get('topic')
            or payload.get('type') or payload.get('topic') or payload.get('action') or ''
        )
        request_id = request.headers.get('x-request-id', '')
        mp.procesar_notificacion(request_id, str(topic), str(data_id), payload,
                                 request.headers)
    except Exception as e:  # noqa: BLE001 — jamás devolver 500 a MP
        logger.error(f"MP webhook: error no controlado: {e}")
    return HttpResponse(status=200)


# ==================== PANTALLA DINEROS ====================

@login_required
def dineros_mercadopago(request):
    """GET /app/ventas/dineros-mercadopago/ — página."""
    return render(request, 'vistas/modulo_ventas/dinerosMercadoPago.html', {})


@login_required
def api_dineros_mercadopago(request):
    """GET /app/api/mercadopago/dineros/?fecha_desde=&fecha_hasta=

    KPIs y tablas del ciclo del dinero MP: cobrado → pendiente de liberación →
    liberado → depositado. Todo con datos locales (sin llamar a MP al pintar).
    """
    hoy = timezone.localdate()
    try:
        fecha_desde = request.GET.get('fecha_desde') or str(hoy - timedelta(days=30))
        fecha_hasta = request.GET.get('fecha_hasta') or str(hoy)
    except Exception:
        fecha_desde, fecha_hasta = str(hoy), str(hoy)

    ahora = timezone.now()
    base = TransaccionMercadoPago.objects.filter(
        tipo='VENTA',
        creado_en__date__gte=fecha_desde,
        creado_en__date__lte=fecha_hasta,
    ).select_related('sucursal', 'retiro')

    aprobadas = base.filter(estado='APROBADA')

    def _suma(qs, campo='monto'):
        return int(qs.aggregate(total=Sum(campo))['total'] or 0)

    pendiente_liberacion = aprobadas.filter(retiro__isnull=True).filter(
        money_release_date__gt=ahora
    )
    liberado_sin_retirar = aprobadas.filter(retiro__isnull=True).exclude(
        money_release_date__gt=ahora
    )
    depositado = aprobadas.filter(retiro__isnull=False)

    kpis = {
        'cobrado': _suma(aprobadas),
        'cantidad_cobros': aprobadas.count(),
        'comisiones': _suma(aprobadas.filter(fee_mp__isnull=False), 'fee_mp'),
        'pendiente_liberacion': _suma(pendiente_liberacion),
        'liberado_sin_retirar': _suma(liberado_sin_retirar),
        'depositado': _suma(depositado),
        'devuelto': _suma(base.filter(estado='DEVUELTA')),
        'contracargos': _suma(base.filter(estado='CONTRACARGO')),
        'huerfanas': _suma(aprobadas.filter(consumida=False)),
        'cantidad_huerfanas': aprobadas.filter(consumida=False).count(),
    }

    por_sucursal = list(
        aprobadas.values('sucursal_id', 'sucursal__alias')
        .annotate(total=Sum('monto'), cantidad=Count('id'))
        .order_by('-total')
    )

    transacciones = [{
        'id': t.id,
        'fecha': timezone.localtime(t.creado_en).strftime('%d/%m/%Y %H:%M'),
        'sucursal': t.sucursal.alias if t.sucursal_id else '',
        'correlativo': t.correlativo_ticket,
        'monto': t.monto,
        'monto_neto': t.monto_neto,
        'estado': t.estado,
        'metodo_pago_mp': t.metodo_pago_mp,
        'liberacion': (timezone.localtime(t.money_release_date).strftime('%d/%m/%Y')
                       if t.money_release_date else ''),
        'liberado': bool(t.money_release_date and t.money_release_date <= ahora),
        'retiro': t.retiro.withdrawal_id if t.retiro_id else '',
        'consumida': t.consumida,
    } for t in base.order_by('-creado_en')[:200]]

    retiros = [{
        'withdrawal_id': r.withdrawal_id,
        'fecha': r.fecha.strftime('%d/%m/%Y'),
        'monto': r.monto,
        'estado': r.estado,
        'visto_en_cartola': r.visto_en_cartola,
        'transacciones': r.transacciones.count(),
    } for r in RetiroMercadoPago.objects.all().order_by('-fecha')[:100]]

    return JsonResponse({
        'success': True,
        'fecha_desde': str(fecha_desde),
        'fecha_hasta': str(fecha_hasta),
        'kpis': kpis,
        'por_sucursal': por_sucursal,
        'transacciones': transacciones,
        'retiros': retiros,
        'configs': list(MercadoPagoConfig.objects.values(
            'id', 'sucursal__alias', 'nombre', 'modo', 'habilitado')),
    })
