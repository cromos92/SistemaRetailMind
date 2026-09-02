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

from .models import (
    Empresa,
    MercadoPagoConfig,
    MercadoPagoCuenta,
    RetiroMercadoPago,
    Sucursal,
    TransaccionMercadoPago,
)
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
    canal = str(request.data.get('canal') or 'QR').upper()
    try:
        config = mp.obtener_config(sucursal_id)
        transaccion, qr_data = mp.crear_orden(
            config, correlativo, monto, canal=canal,
            descripcion=f'Venta {correlativo}', usuario=request.user,
        )
    except MercadoPagoError as e:
        return Response({'success': False, 'error': e.mensaje},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({
        'success': True,
        'transaccion_id': transaccion.id,
        'canal': canal,
        'external_reference': transaccion.external_reference,
        'qr_data': qr_data,
        'qr_base64': mp.qr_png_base64(qr_data) if qr_data else None,
        'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS,
    })


def _transaccion_de_sesion(request, transaccion_id):
    """Solo transacciones de la sucursal en sesión (evita IDOR entre tiendas).
    Un administrador puede consultar cualquiera (necesario para la transacción
    de prueba de la pestaña de gestión, que apunta a la caja de otra sucursal)."""
    qs = TransaccionMercadoPago.objects.filter(id=transaccion_id).select_related('config')
    if getattr(request.user, 'rol', '') in ('administrador', 'administracion'):
        return qs.first()
    sucursal_id = _sucursal_sesion(request)
    if not sucursal_id:
        return None
    return qs.filter(sucursal_id=sucursal_id).first()


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


# ==================== GESTIÓN (pestaña MP de /app/pos/transbank/) ====================

def _es_admin(request):
    return getattr(request.user, 'rol', '') in ('administrador', 'administracion')


@login_required
@require_POST
def gestion_guardar_cuenta_mp(request):
    """POST /app/pos/mercadopago/gestion/cuenta/ — SOLO ADMINISTRADOR.

    Crea/actualiza la MercadoPagoCuenta de una empresa. Token y secret se
    guardan CIFRADOS; campo vacío = conservar el valor actual.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False,
                             'error': 'Solo un Administrador puede modificar credenciales de Mercado Pago.'},
                            status=403)
    try:
        empresa = Empresa.objects.get(id=int(request.POST.get('empresa_id', 0)))
    except (Empresa.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Empresa inválida'}, status=400)

    cuenta, creada = MercadoPagoCuenta.objects.get_or_create(empresa=empresa)
    mp_user_id = (request.POST.get('mp_user_id') or '').strip()
    if mp_user_id:
        cuenta.mp_user_id = mp_user_id[:30]
    token = (request.POST.get('access_token') or '').strip()
    if token:
        cuenta.set_access_token(token)
    secret = (request.POST.get('webhook_secret') or '').strip()
    if secret:
        cuenta.set_webhook_secret(secret)
    cuenta.activo = True
    cuenta.save()
    logger.info(
        "MP gestión: cuenta %s de empresa %s por %s (token %s, secret %s)",
        'creada' if creada else 'actualizada', empresa.rut, request.user.username,
        'actualizado' if token else 'sin cambio', 'actualizado' if secret else 'sin cambio',
    )
    return JsonResponse({'success': True})


@login_required
@require_POST
def gestion_guardar_config_mp(request):
    """POST /app/pos/mercadopago/gestion/config/ — SOLO ADMINISTRADOR.

    Crea/edita la asociación de una caja QR (MercadoPagoConfig) a una
    sucursal: external_store_id/external_pos_id definidos en el panel de MP.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False,
                             'error': 'Solo un Administrador puede asociar máquinas de Mercado Pago.'},
                            status=403)
    try:
        sucursal = Sucursal.objects.get(id=int(request.POST.get('sucursal_id', 0)))
    except (Sucursal.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Sucursal inválida'}, status=400)

    nombre = (request.POST.get('nombre') or 'Caja principal').strip()[:100]
    config_id = (request.POST.get('config_id') or '').strip()
    if config_id:
        config = MercadoPagoConfig.objects.filter(id=int(config_id)).first()
        if not config:
            return JsonResponse({'success': False, 'error': 'Configuración no encontrada'}, status=404)
        config.sucursal = sucursal
        config.nombre = nombre
    else:
        config = MercadoPagoConfig.objects.filter(sucursal=sucursal, nombre=nombre).first() \
            or MercadoPagoConfig(sucursal=sucursal, nombre=nombre)

    config.external_store_id = (request.POST.get('external_store_id') or '').strip()[:60]
    config.external_pos_id = (request.POST.get('external_pos_id') or '').strip()[:60]
    config.habilitado = request.POST.get('habilitado') == '1'
    config.device_id = (request.POST.get('device_id') or '').strip()[:60]
    # Con máquina Point asociada la caja puede cobrar por ambos canales
    config.modo = 'AMBOS' if config.device_id else 'QR'
    # Primera caja de la sucursal = principal; si ya hay otra principal se respeta
    if not MercadoPagoConfig.objects.filter(sucursal=sucursal, es_principal=True).exclude(id=config.id).exists():
        config.es_principal = True
    try:
        config.save()
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'No se pudo guardar: {e}'}, status=400)
    logger.info(
        "MP gestión: caja '%s' de sucursal %s guardada por %s (pos_id=%s, habilitada=%s)",
        nombre, sucursal.alias, request.user.username,
        config.external_pos_id, config.habilitado,
    )
    return JsonResponse({'success': True, 'config_id': config.id})


@login_required
def gestion_datos_mp(request):
    """GET gestion/datos/ — cuentas y cajas actuales para refrescar las tablas
    de la pestaña MP por AJAX (sin recargar la página)."""
    cuentas = [{
        'empresa_id': c.empresa_id,
        'empresa_nombre': c.empresa.nombre or c.empresa.razon_social,
        'empresa_rut': c.empresa.rut,
        'mp_user_id': c.mp_user_id,
        'tiene_token': bool(c.access_token_cifrado),
        'tiene_secret': bool(c.webhook_secret_cifrado),
        'activo': c.activo,
    } for c in MercadoPagoCuenta.objects.select_related('empresa').all()]
    configs = [{
        'id': cfg.id,
        'sucursal_id': cfg.sucursal_id,
        'sucursal_alias': cfg.sucursal.alias,
        'nombre': cfg.nombre,
        'external_store_id': cfg.external_store_id,
        'external_pos_id': cfg.external_pos_id,
        'device_id': cfg.device_id,
        'habilitado': cfg.habilitado,
        'es_principal': cfg.es_principal,
    } for cfg in MercadoPagoConfig.objects.select_related('sucursal').order_by('sucursal__alias', 'nombre')]
    return JsonResponse({'success': True, 'cuentas': cuentas, 'configs': configs})


def _cuenta_por_empresa(request):
    try:
        return MercadoPagoCuenta.objects.get(empresa_id=int(request.POST.get('empresa_id', 0)))
    except (MercadoPagoCuenta.DoesNotExist, TypeError, ValueError):
        return None


@login_required
@require_POST
def gestion_probar_cuenta_mp(request):
    """POST gestion/cuenta/probar/ — valida el token contra /users/me (admin)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
    try:
        datos = mp.probar_cuenta(cuenta)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    return JsonResponse({'success': True, 'datos': datos})


@login_required
@require_POST
def gestion_devices_point_mp(request):
    """POST gestion/devices/ — lista las máquinas Point de la cuenta (admin)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
    try:
        devices = mp.listar_devices_point(cuenta)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    return JsonResponse({'success': True, 'devices': devices})


@login_required
@require_POST
def gestion_modo_device_mp(request):
    """POST gestion/devices/modo/ — cambia PDV/STANDALONE de una Point (admin).

    PDV: la máquina queda esclava del sistema (no cobra desde su pantalla).
    STANDALONE: vuelve a operar sola. El cambio es reversible al instante.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
    device_id = (request.POST.get('device_id') or '').strip()
    modo = (request.POST.get('modo') or '').strip().upper()
    if not device_id:
        return JsonResponse({'success': False, 'error': 'Falta el device_id.'}, status=400)
    try:
        modo_final = mp.cambiar_modo_device(cuenta, device_id, modo)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    logger.warning("MP gestión: device %s -> %s por %s", device_id, modo_final, request.user.username)
    return JsonResponse({'success': True, 'operating_mode': modo_final})


@login_required
@require_POST
def gestion_eliminar_cuenta_mp(request):
    """POST gestion/cuenta/eliminar/ — borra la cuenta de una empresa (admin)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
    from django.db.models import ProtectedError
    try:
        rut = cuenta.empresa.rut
        cuenta.delete()
    except ProtectedError:
        return JsonResponse({'success': False,
                             'error': 'Hay cajas apuntando explícitamente a esta cuenta: quita el vínculo primero.'},
                            status=400)
    logger.warning("MP gestión: cuenta de %s ELIMINADA por %s", rut, request.user.username)
    return JsonResponse({'success': True})


@login_required
@require_POST
def gestion_listar_cajas_mp(request):
    """POST gestion/cajas-mp/ — lista las sucursales/cajas YA CREADAS en la
    cuenta MP de la empresa, para asociarlas con un clic (admin)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False,
                             'error': 'Primero guarda las credenciales de la empresa (columna del medio).'},
                            status=404)
    try:
        cajas = mp.listar_cajas(cuenta)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    return JsonResponse({'success': True, 'cajas': cajas})


@login_required
@require_POST
def gestion_asignar_ids_mp(request):
    """POST gestion/cajas-mp/asignar/ — asigna external_id a una caja/sucursal
    ya creadas en MP (el panel web las crea sin ID externo). Admin."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request)
    if not cuenta:
        return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
    ext_store = (request.POST.get('external_store_id') or '').strip()[:60]
    ext_pos = (request.POST.get('external_pos_id') or '').strip()[:60]
    if not ext_pos:
        return JsonResponse({'success': False, 'error': 'Falta el ID externo de la caja.'}, status=400)
    try:
        mp.asignar_external_ids(
            cuenta,
            pos_id=request.POST.get('pos_id'),
            store_id=request.POST.get('store_id'),
            external_store_id=ext_store,
            external_pos_id=ext_pos,
        )
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    return JsonResponse({'success': True,
                         'external_store_id': ext_store, 'external_pos_id': ext_pos})


@login_required
@require_POST
def gestion_eliminar_config_mp(request):
    """POST gestion/config/eliminar/ — elimina una asociación de caja (admin).
    Si ya tiene transacciones, no se puede borrar: deshabilitarla."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    config = MercadoPagoConfig.objects.filter(id=int(request.POST.get('config_id', 0) or 0)).first()
    if not config:
        return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    from django.db.models import ProtectedError
    try:
        config.delete()
    except ProtectedError:
        config.habilitado = False
        config.save(update_fields=['habilitado', 'actualizado_en'])
        return JsonResponse({'success': False,
                             'error': 'La caja ya tiene cobros registrados y no puede borrarse: quedó DESHABILITADA.'},
                            status=400)
    return JsonResponse({'success': True})


@login_required
@require_POST
def gestion_probar_config_mp(request):
    """POST gestion/config/probar/ — TRANSACCIÓN DE PRUEBA: crea un QR real
    contra la caja para verificar token + external_pos_id de punta a punta
    (admin). El correlativo parte con 'PRUEBA-' y queda excluido de las
    alertas de huérfanas."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    config = MercadoPagoConfig.objects.select_related('sucursal').filter(
        id=int(request.POST.get('config_id', 0) or 0)).first()
    if not config:
        return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    try:
        monto = max(50, int(request.POST.get('monto', 100) or 100))
    except (TypeError, ValueError):
        monto = 100
    canal = (request.POST.get('canal') or 'QR').strip().upper()
    correlativo = f"PRUEBA-{timezone.now():%d%H%M%S}"
    try:
        transaccion, qr_data = mp.crear_orden(
            config, correlativo, monto, canal=canal,
            descripcion=f'PRUEBA caja {config.external_pos_id}', usuario=request.user,
        )
    except mp.MercadoPagoError as e:
        # Al admin se le muestra el payload crudo de MP: es la única forma de
        # diagnosticar un 400 de la Orders API sin ir a los logs.
        detalle = ''
        try:
            detalle = json.dumps(e.detalle, ensure_ascii=False)[:1200] if e.detalle else ''
        except (TypeError, ValueError):
            detalle = str(e.detalle)[:1200]
        return JsonResponse({'success': False, 'error': e.mensaje, 'detalle': detalle}, status=400)
    return JsonResponse({
        'success': True,
        'transaccion_id': transaccion.id,
        'canal': canal,
        'qr_base64': mp.qr_png_base64(qr_data) if qr_data else None,
        'qr_data': qr_data,
        'monto': monto,
        'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS,
    })


@login_required
def gestion_resumen_dia_mp(request):
    """GET gestion/resumen-dia/?fecha= — CIERRE del día POR CAJA/TERMINAL con
    desglose de canal (QR vs máquina Point). MP no tiene cierre de lote (los
    pagos liquidan solos): este resumen es el equivalente al cierre de
    Transbank, pensado para cuadrar e imprimir por máquina."""
    fecha = request.GET.get('fecha') or str(timezone.localdate())
    base = TransaccionMercadoPago.objects.filter(
        creado_en__date=fecha,
    ).exclude(correlativo_ticket__startswith='PRUEBA-').select_related(
        'sucursal', 'config')

    def _canal_vacio():
        return {'cobros': 0, 'monto': 0, 'devoluciones': 0, 'monto_devuelto': 0,
                'comisiones': 0, 'rechazadas': 0}

    cajas = {}
    for t in base:
        key = t.config_id
        caja = cajas.setdefault(key, {
            'caja': t.config.nombre if t.config_id else '?',
            'sucursal': t.sucursal.alias if t.sucursal_id else '?',
            'external_pos_id': t.config.external_pos_id if t.config_id else '',
            'device': t.config.device_id if t.config_id else '',
            'QR': _canal_vacio(), 'POINT': _canal_vacio(), 'medios': {},
        })
        canal = caja.get(t.canal) or caja['QR']
        if t.tipo == 'VENTA' and t.estado == 'APROBADA':
            canal['cobros'] += 1
            canal['monto'] += t.monto
            if t.fee_mp:
                canal['comisiones'] += t.fee_mp
            etiqueta = mp.etiqueta_medio_mp(t.metodo_pago_mp)
            medio = caja['medios'].setdefault(etiqueta, {'cobros': 0, 'monto': 0})
            medio['cobros'] += 1
            medio['monto'] += t.monto
        elif t.tipo == 'DEVOLUCION':
            canal['devoluciones'] += 1
            canal['monto_devuelto'] += t.monto
        elif t.estado in ('RECHAZADA', 'EXPIRADA', 'CANCELADA', 'ERROR'):
            canal['rechazadas'] += 1

    total = _canal_vacio()
    for caja in cajas.values():
        for canal in ('QR', 'POINT'):
            for k in total:
                total[k] += caja[canal][k]
        caja['total_monto'] = caja['QR']['monto'] + caja['POINT']['monto']
        caja['total_neto'] = (caja['total_monto']
                              - caja['QR']['monto_devuelto']
                              - caja['POINT']['monto_devuelto'])
    total['neto'] = total['monto'] - total['monto_devuelto']
    return JsonResponse({'success': True, 'fecha': fecha,
                         'cajas': list(cajas.values()), 'total': total})


@login_required
@require_POST
def gestion_imprimir_cierre_terminal_mp(request):
    """POST gestion/terminal/imprimir-cierre/ — imprime el cierre del día de
    una caja EN LA IMPRESORA de su máquina Point (API de Impresiones)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    config = MercadoPagoConfig.objects.select_related('sucursal').filter(
        id=int(request.POST.get('config_id', 0) or 0)).first()
    if not config:
        return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    if not config.device_id:
        return JsonResponse({'success': False, 'error': 'Esa caja no tiene máquina Point asociada.'}, status=400)
    fecha = request.POST.get('fecha') or str(timezone.localdate())

    def _canal_vacio():
        return {'cobros': 0, 'monto': 0, 'devoluciones': 0, 'monto_devuelto': 0, 'comisiones': 0}

    caja = {'caja': config.nombre, 'sucursal': config.sucursal.alias,
            'QR': _canal_vacio(), 'POINT': _canal_vacio(), 'medios': {}}
    trxs = TransaccionMercadoPago.objects.filter(
        config=config, creado_en__date=fecha,
    ).exclude(correlativo_ticket__startswith='PRUEBA-')
    for t in trxs:
        canal = caja.get(t.canal) or caja['QR']
        if t.tipo == 'VENTA' and t.estado == 'APROBADA':
            canal['cobros'] += 1
            canal['monto'] += t.monto
            if t.fee_mp:
                canal['comisiones'] += t.fee_mp
            # Desglose final por medio real (débito/crédito/prepago/…)
            etiqueta = mp.etiqueta_medio_mp(t.metodo_pago_mp)
            medio = caja['medios'].setdefault(etiqueta, {'cobros': 0, 'monto': 0})
            medio['cobros'] += 1
            medio['monto'] += t.monto
        elif t.tipo == 'DEVOLUCION':
            canal['devoluciones'] += 1
            canal['monto_devuelto'] += t.monto
    caja['total_neto'] = (caja['QR']['monto'] + caja['POINT']['monto']
                          - caja['QR']['monto_devuelto'] - caja['POINT']['monto_devuelto'])
    try:
        contenido = mp.contenido_cierre_terminal(caja, fecha)
        mp.imprimir_en_terminal(
            config, contenido,
            f"CIERRE-{config.id}-{fecha}-{timezone.now():%H%M%S}",
        )
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    logger.info("MP gestión: cierre %s de caja %s impreso en terminal por %s",
                fecha, config.id, request.user.username)
    return JsonResponse({'success': True})


@login_required
@require_POST
def gestion_cobrar_terminal_mp(request):
    """POST gestion/terminal/cobrar/ — COBRO DIRECTO en la máquina Point desde
    la pestaña de gestión (admin). ⚠️ No queda asociado a un ticket: para
    ventas normales se usa el POS; esto sirve para cobros sueltos/soporte.
    Correlativo DIRECTO-* — visible en Dineros y en el resumen por terminal."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    config = MercadoPagoConfig.objects.select_related('sucursal').filter(
        id=int(request.POST.get('config_id', 0) or 0)).first()
    if not config:
        return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    if not config.device_id:
        return JsonResponse({'success': False, 'error': 'Esa caja no tiene máquina Point asociada.'}, status=400)
    try:
        monto = int(request.POST.get('monto', 0) or 0)
    except (TypeError, ValueError):
        monto = 0
    if monto < 50:
        return JsonResponse({'success': False, 'error': 'Monto mínimo $50.'}, status=400)
    correlativo = f"DIRECTO-{timezone.now():%d%m-%H%M%S}"
    try:
        transaccion, _qr = mp.crear_orden(
            config, correlativo, monto, canal='POINT',
            descripcion=f'Cobro directo terminal {config.external_pos_id}',
            usuario=request.user,
        )
    except mp.MercadoPagoError as e:
        detalle = ''
        try:
            detalle = json.dumps(e.detalle, ensure_ascii=False)[:800] if e.detalle else ''
        except (TypeError, ValueError):
            detalle = str(e.detalle)[:800]
        return JsonResponse({'success': False, 'error': e.mensaje, 'detalle': detalle}, status=400)
    logger.warning("MP gestión: COBRO DIRECTO $%s en terminal %s por %s (%s)",
                   monto, config.device_id, request.user.username, correlativo)
    return JsonResponse({'success': True, 'transaccion_id': transaccion.id,
                         'canal': 'POINT', 'monto': monto,
                         'correlativo': correlativo,
                         'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS})


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
        'huerfanas': _suma(aprobadas.filter(consumida=False)
                           .exclude(correlativo_ticket__startswith='PRUEBA-')),
        'cantidad_huerfanas': aprobadas.filter(consumida=False)
                              .exclude(correlativo_ticket__startswith='PRUEBA-').count(),
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
