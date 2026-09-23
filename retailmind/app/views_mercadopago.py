"""Vistas Mercado Pago presencial: cobro QR desde el POS, webhook firmado y
pantalla Dineros (pendiente de liberación / liberado / depositado).

Espejo estructural de views_transbank_sdk.py: DRF para los endpoints del POS
(sesión + login) y vista Django plana csrf_exempt para el webhook (viene de
los servidores de MP, sin sesión).
"""
import json
import logging
import re
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    Dte,
    Empresa,
    MercadoPagoConfig,
    MercadoPagoCuenta,
    RetiroMercadoPago,
    Sucursal,
    TransaccionMercadoPago,
    rol_efectivo,
)
from .services import mercadopago_service as mp
from .services.mercadopago_service import MercadoPagoError

logger = logging.getLogger('app')


def _sucursal_sesion(request):
    return (request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
            or request.session.get('idSucursalActualPOS'))


# ==================== COBRO DESDE EL POS ====================

# Tope de ids de pago que el POS puede declarar como "ya cargados" en un
# ticket: nadie paga con más de 20 tarjetas y así el campo no sirve para
# meter basura en el guard.
_MAX_PAGOS_MP_CARGADOS = 20


def _payment_ids_cargados(crudo):
    """Sanea `pagos_mp_cargados` del body: lista (o string separado por comas)
    de vouchers. Devuelve set de str no vacíos, ≤64 chars, máximo 20."""
    if crudo is None:
        return None
    if isinstance(crudo, str):
        crudo = crudo.split(',')
    if not isinstance(crudo, (list, tuple, set)):
        return None
    limpios = []
    for v in crudo:
        s = str(v or '').strip()
        if s and len(s) <= 64 and s not in limpios:
            limpios.append(s)
        if len(limpios) >= _MAX_PAGOS_MP_CARGADOS:
            break
    return set(limpios)


def _montos_cargados(crudo):
    """Montos (int > 0) de los pagos MP integrados que el POS ya tiene cargados.

    Respaldo de `_payment_ids_cargados` para la segunda tarjeta del mismo
    ticket cuando el primer pago aún no trae id (la orden Point puede quedar
    APROBADA en el polling antes de que MP informe el payment_id)."""
    if isinstance(crudo, str):
        crudo = crudo.split(',')
    if not isinstance(crudo, (list, tuple)):
        return []
    montos = []
    for v in list(crudo)[:_MAX_PAGOS_MP_CARGADOS]:
        try:
            m = int(v)
        except (TypeError, ValueError):
            continue
        if m > 0:
            montos.append(m)
    return montos


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
    # Cobros MP que el POS ya cargó como pagos de este ticket (segunda tarjeta
    # del mismo ticket). Sin el campo, el guard se comporta igual que siempre.
    pagos_mp_cargados = _payment_ids_cargados(request.data.get('pagos_mp_cargados'))
    montos_mp_cargados = _montos_cargados(request.data.get('montos_mp_cargados'))
    try:
        config = mp.obtener_config(sucursal_id)
        transaccion, qr_data = mp.crear_orden(
            config, correlativo, monto, canal=canal,
            descripcion=f'Venta {correlativo}', usuario=request.user,
            payment_ids_cargados=pagos_mp_cargados,
            montos_cargados=montos_mp_cargados,
        )
    except MercadoPagoError as e:
        cuerpo = {'success': False, 'error': e.mensaje}
        # Si el rechazo es porque ya hay un cobro vivo/incierto, el POS necesita
        # su id para VIGILARLO en vez de dejar la caja libre para cobrar de nuevo.
        previa = getattr(e, 'transaccion', None)
        if previa is not None:
            cuerpo['error_tipo'] = 'MP_COBRO_EN_CURSO'
            cuerpo['transaccion_id'] = previa.id
            cuerpo['cobro'] = mp.resumen_cobro(previa)
        return Response(cuerpo, status=status.HTTP_400_BAD_REQUEST)

    # ── Cobro SIN CONFIRMAR (corte de red al crear) ────────────────────────
    # NO es un error: Mercado Pago pudo haber recibido la orden y estar
    # cobrándola en la pantalla de la máquina. Se responde 200 con el
    # transaccion_id para que el POS lo vigile. Devolver un 400 acá es lo que el
    # 13-09 empujó al cajero a reintentar a ciegas y cobrar dos veces.
    if mp.es_incierta(transaccion):
        return Response({
            'success': True,
            'estado': 'INCIERTO',
            'transaccion_id': transaccion.id,
            'canal': canal,
            'monto': transaccion.monto,
            'external_reference': transaccion.external_reference,
            'qr_data': None,
            'qr_base64': None,
            'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS,
            'mensaje': ('No pudimos confirmar el envío a Mercado Pago. Estamos '
                        'verificando: NO cobres de nuevo todavía.'),
        })

    return Response({
        'success': True,
        'estado': 'OK',
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
    if rol_efectivo(request.user) in ('administrador', 'administracion'):
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
    # Aprobada sin id de pago (p.ej. aprobación por webhook de orden sin el
    # detalle del pago): completarlo acá, que es de donde el POS saca el voucher.
    transaccion = mp.completar_ids_aprobada(transaccion)
    return Response({
        'success': True,
        # OJO: `estado` NO puede salirse de {'CREADA','PENDIENTE'} mientras el
        # cobro todavía pueda aprobarse. Los cuatro consumidores del polling
        # tratan cualquier otro valor como TERMINAL: apagan la vigilancia y
        # ofrecen "Reintentar", que es el gatillo exacto del doble cobro. La
        # incertidumbre viaja en campos NUEVOS, nunca en `estado`.
        'estado': transaccion.estado,
        'estado_detalle': transaccion.estado_detalle,
        'payment_id': transaccion.payment_id,
        'metodo_pago_mp': transaccion.metodo_pago_mp,
        'ultimos_4_digitos': transaccion.ultimos_4_digitos,
        'codigo_autorizacion': transaccion.codigo_autorizacion,
        'monto': transaccion.monto,
        'incierto': mp.es_incierta(transaccion),
        'puede_reintentar': transaccion.estado in mp.ESTADOS_FINALES_MP,
        # Aditivos (los usa el cobro directo de /app/pos/transbank/): el N° de
        # operación que muestra el panel/app de MP —el que se digita en
        # "MP manual"— y la etiqueta legible del medio.
        'payment_id_mp': transaccion.payment_id_mp,
        'medio': mp.etiqueta_medio_mp(transaccion.metodo_pago_mp),
        'canal': transaccion.canal,
        'correlativo': transaccion.correlativo_ticket,
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


@api_view(['POST'])
@login_required
def expirar_pago_mp(request, transaccion_id):
    """POST /app/pos/mercadopago/expirar/<id>/ — «Marcar expirado en el sistema».

    Caso real (PAO4, 21-09): un cobro Point de $3.333 quedó PENDIENTE en la
    pantalla de la máquina; MP no lo deja cancelar por API (409 cannot_cancel
    mientras está at_terminal) y reiniciar la máquina no sirve porque la orden
    vive en MP y la vuelve a bajar. El cajero necesita poder sacarla del
    sistema. Esto marca la fila EXPIRADA (estado final: deja de bloquear el
    ticket y de contar como viva), pero NO toca la orden en MP: hay que
    cancelarla igual en la máquina. Es reversible: si el cliente paga igual,
    _aplicar_estado permite FINAL→APROBADA y el webhook la revive.

    Solo filas CREADA/PENDIENTE CON order_id. Una fila sin order_id (incierta
    o reserva) NO se puede dar por muerta a ciegas: es la protección contra el
    doble cobro del 13-09 y acá no se relaja.
    """
    transaccion = _transaccion_de_sesion(request, transaccion_id)
    if not transaccion:
        return Response({'success': False, 'error': 'Transacción no encontrada'},
                        status=status.HTTP_404_NOT_FOUND)
    msg_aprobada = ('El cliente alcanzó a pagar: el cobro quedó APROBADO. '
                    'Corresponde devolución, no expirarlo.')
    if transaccion.estado == 'APROBADA':
        return Response({'success': False, 'error': msg_aprobada, 'estado': 'APROBADA'},
                        status=status.HTTP_400_BAD_REQUEST)
    if transaccion.estado in mp.ESTADOS_FINALES_MP:
        return Response({'success': True, 'estado': transaccion.estado})
    if transaccion.estado not in ('CREADA', 'PENDIENTE'):
        return Response({'success': False, 'estado': transaccion.estado,
                         'error': f'El cobro está {transaccion.estado}: no se puede expirar a mano.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not transaccion.order_id:
        return Response({
            'success': False, 'estado': transaccion.estado,
            'error': ('No pudimos confirmar con Mercado Pago si este cobro existe, '
                      'así que no se puede dar por expirado. Usa «Liberar máquina» '
                      'en Máquinas POS o espera unos minutos a que se resuelva solo.'),
        }, status=status.HTTP_400_BAD_REQUEST)

    # Última mirada a MP antes de cerrarla: si el cliente pagó mientras tanto,
    # no se puede expirar. Sin red se decide con lo que hay en la base.
    try:
        transaccion = mp.consultar_estado(transaccion, forzar=True)
    except Exception:  # noqa: BLE001 — sin red igual se sigue con la BD
        logger.warning("MP: no se pudo consultar %s antes de expirarla a mano",
                       transaccion.external_reference)
    if transaccion.estado == 'APROBADA':
        return Response({'success': False, 'error': msg_aprobada, 'estado': 'APROBADA'},
                        status=status.HTTP_400_BAD_REQUEST)
    if transaccion.estado in mp.ESTADOS_FINALES_MP:
        return Response({'success': True, 'estado': transaccion.estado})

    usuario = getattr(request.user, 'username', '') or 'sistema'
    transaccion = mp._aplicar_estado(
        transaccion, 'EXPIRADA',
        detalle=f'Expirada a mano por {usuario}; cancélala en la máquina')
    logger.warning(
        "MP: cobro %s (id=%s, $%s, ticket %s) marcado EXPIRADO a mano por %s; "
        "la orden %s sigue en MP hasta cancelarla en la máquina",
        transaccion.external_reference, transaccion.id, transaccion.monto,
        transaccion.correlativo_ticket, usuario, transaccion.order_id,
    )
    return Response({'success': True, 'estado': transaccion.estado})


@api_view(['GET'])
@login_required
def cobros_vivos_ticket_mp(request, correlativo):
    """GET /app/pos/mercadopago/en-curso/<correlativo>/

    Cobros de Mercado Pago que siguen vivos para este ticket: en la pantalla de
    la máquina (CREADA/PENDIENTE) o ya aprobados sin respaldar ningún pago.
    El POS lo consulta al cargar el paso de cobro y antes de finalizar, para
    que nunca se cierre una venta con crédito manual mientras la Point todavía
    tiene el cobro encima.

    `?refrescar=1` fuerza la consulta a MP (más lento, pero es el dato real).
    """
    sucursal_id = _sucursal_sesion(request)
    if not sucursal_id:
        return Response({'success': False, 'error': 'No hay sucursal en sesión'},
                        status=status.HTTP_400_BAD_REQUEST)
    refrescar = str(request.GET.get('refrescar') or '') in ('1', 'true', 'True')
    # Un ticket nacido de una cotización también busca los cobros hechos bajo
    # COT-… (se crearon antes de que existiera el ticket).
    correlativos = mp.correlativos_equivalentes_de_ticket(sucursal_id, correlativo)
    cobros = mp.cobros_vivos_de_ticket(sucursal_id, correlativos, refrescar=refrescar)
    return Response({
        'success': True,
        'correlativo': str(correlativo),
        'cobros': [mp.resumen_cobro(t) for t in cobros],
        'hay_aprobado_sin_usar': any(t.estado == 'APROBADA' for t in cobros),
        'total': sum(t.monto for t in cobros),
    })


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
    return rol_efectivo(request.user) in ('administrador', 'administracion')


_SUGERIR_LIBERAR_TERMINAL = (' Usa «Liberar máquina» en Máquinas POS para ver qué tiene '
                             'encolado Mercado Pago y cancelar lo que se pueda.')


def _describir_cobro_terminal(trx):
    hora = timezone.localtime(trx.creado_en).strftime('%H:%M')
    monto = f'{trx.monto:,}'.replace(',', '.')
    corr = trx.correlativo_ticket or ''
    ref = ('suelto' if corr.startswith(('DIRECTO-', 'PRUEBA-'))
           else (f'del ticket {corr}' if corr else 'sin ticket'))
    return ref, monto, hora


def _pendiente_en_terminal(config):
    """Explica un `already_queued_order_on_terminal` con datos concretos.

    Primero, un cobro Point que el sistema ve VIVO en esa caja (hay que
    terminarlo o cancelarlo en la pantalla del terminal). Si no hay ninguno,
    lo ÚLTIMO que esa caja mandó a la máquina en 7 días, en cualquier estado:
    una fila cerrada local (ERROR/EXPIRADA/CANCELADA) puede seguir viva en MP
    y ser justo lo que ocupa la ranura. Siempre cierra sugiriendo «Liberar
    máquina». Devuelve '' solo si no hay nada de nada.
    """
    trx = (TransaccionMercadoPago.objects
           .filter(config=config, canal='POINT', estado__in=('CREADA', 'PENDIENTE'))
           .order_by('-creado_en').first())
    if trx:
        ref, monto, hora = _describir_cobro_terminal(trx)
        return (f' Pendiente en esa máquina: un cobro {ref} por ${monto} enviado a las {hora}.'
                + _SUGERIR_LIBERAR_TERMINAL)
    ultimo = (TransaccionMercadoPago.objects
              .filter(config=config, canal='POINT',
                      creado_en__gte=timezone.now() - timedelta(days=7))
              .exclude(order_id='')
              .order_by('-creado_en').first())
    if not ultimo:
        return ''
    ref, monto, hora = _describir_cobro_terminal(ultimo)
    cuando = timezone.localtime(ultimo.creado_en).strftime('%d-%m')
    return (f' Lo último enviado a esa máquina: cobro {ref} por ${monto} el {cuando} a las '
            f'{hora}, estado local {ultimo.estado}.' + _SUGERIR_LIBERAR_TERMINAL)


def _config_operable(request, config_id=None, requerir_habilitada=False):
    """Caja MP sobre la que opera el usuario en la pestaña Mercado Pago.

    Admin: la que elija (``config_id``); si no manda ninguna, la de su
    sucursal de sesión. Cualquier otro rol: SIEMPRE la caja de su sucursal de
    sesión (no puede apuntar a otra tienda). Se prefiere la caja habilitada y
    principal; con ``requerir_habilitada`` una caja deshabilitada no sirve.

    Devuelve ``(config, respuesta_error)``: exactamente uno de los dos es None.
    """
    qs = MercadoPagoConfig.objects.select_related('sucursal', 'cuenta')
    if _es_admin(request) and config_id:
        config = qs.filter(id=config_id).first()
        if not config:
            return None, JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
        return config, None
    sucursal_id = _sucursal_sesion(request)
    if not sucursal_id:
        return None, JsonResponse({'success': False, 'error': 'No hay sucursal en sesión.'}, status=400)
    base = qs.filter(sucursal_id=sucursal_id).order_by('-es_principal', 'id')
    config = base.filter(habilitado=True).first()
    if not config and not requerir_habilitada:
        config = base.first()
    if not config:
        return None, JsonResponse({
            'success': False,
            'error': ('Tu sucursal no tiene una caja de Mercado Pago '
                      + ('habilitada.' if requerir_habilitada else 'asociada.')
                      + ' Pídele al administrador que la cree en «Configuración avanzada».'),
        }, status=404)
    return config, None


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
    respuesta = {'success': True}
    # Verificación inmediata (la pide el formulario con verificar=1): valida el
    # token contra /users/me y deja el mp_user_id REAL, que es el que usan
    # «Buscar cajas» y la asignación de IDs. Si MP no responde, la cuenta queda
    # guardada igual y se avisa.
    if request.POST.get('verificar') == '1':
        try:
            datos = mp.probar_cuenta(cuenta)
            respuesta['verificacion'] = dict(datos, ok=True)
            real = str(datos.get('user_id') or '')
            if mp_user_id and real and mp_user_id != real:
                respuesta['aviso'] = (f'El User ID que escribiste ({mp_user_id}) no es el dueño del '
                                      f'token: se guardó el real ({real}).')
        except mp.MercadoPagoError as e:
            respuesta['verificacion'] = {'ok': False, 'error': e.mensaje}
            respuesta['aviso'] = f'Cuenta guardada, pero el token no pasó la prueba: {e.mensaje}'
    return JsonResponse(respuesta)


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
    movida = False
    origen_id = None
    if config_id:
        if not _int_o_cero(config_id):
            return JsonResponse({'success': False, 'error': 'config_id inválido'}, status=400)
        config = MercadoPagoConfig.objects.filter(id=int(config_id)).first()
        if not config:
            return JsonResponse({'success': False, 'error': 'Configuración no encontrada'}, status=404)
        if MercadoPagoConfig.objects.filter(sucursal=sucursal, nombre=nombre).exclude(id=config.id).exists():
            return JsonResponse({'success': False,
                                 'error': f'Ya existe otra caja llamada «{nombre}» en {sucursal.alias}: usa otro nombre.'},
                                status=400)
        movida = config.sucursal_id != sucursal.id
        origen_id = config.sucursal_id if movida else None
        config.sucursal = sucursal
        config.nombre = nombre
    else:
        # Antes, un nombre repetido PISABA la caja existente en silencio: el
        # admin creía crear una segunda caja y en realidad reescribía la primera.
        if MercadoPagoConfig.objects.filter(sucursal=sucursal, nombre=nombre).exists():
            return JsonResponse({'success': False,
                                 'error': (f'Ya existe una caja llamada «{nombre}» en {sucursal.alias}. Para modificarla '
                                           'haz clic en su fila de la tabla; para agregar otra, ponle un nombre distinto '
                                           '(p.ej. "Caja 2").')},
                                status=400)
        config = MercadoPagoConfig(sucursal=sucursal, nombre=nombre)

    config.external_store_id = (request.POST.get('external_store_id') or '').strip()[:60]
    config.external_pos_id = (request.POST.get('external_pos_id') or '').strip()[:60]
    config.habilitado = request.POST.get('habilitado') == '1'
    config.device_id = (request.POST.get('device_id') or '').strip()[:60]
    if config.device_id:
        # Una máquina cobra para UNA caja. Este formulario no libera la caja
        # anterior (eso lo hace «Asignar a sucursal», que además arregla cuenta,
        # modo y caja principal), así que guardar aquí la dejaría en dos sitios:
        # el POS de las dos tiendas la mostraría y la cuadratura no sabría de
        # quién es la venta.
        otra = (MercadoPagoConfig.objects.filter(device_id=config.device_id)
                .exclude(pk=config.pk).select_related('sucursal').first())
        if otra:
            return JsonResponse({'success': False, 'error': (
                f'Esa máquina ya la usa «{otra.sucursal.alias} · {otra.nombre}». '
                'Para cambiarla de tienda usa «Asignar a sucursal» en la tarjeta '
                '«Máquinas POS y su sucursal»: así se libera la caja anterior.')}, status=400)
    # Cuenta MP explícita (empresa dueña del token). Vacío = automática: la de
    # la empresa dueña de la sucursal. Hace falta cuando la caja cobra con la
    # cuenta de OTRA empresa (p.ej. una máquina de Paola en una sucursal de
    # EDEL): el modelo siempre tuvo el FK, pero la pantalla no lo exponía.
    cuenta_empresa_id = (request.POST.get('cuenta_empresa_id') or '').strip()
    if cuenta_empresa_id:
        try:
            config.cuenta = MercadoPagoCuenta.objects.get(empresa_id=int(cuenta_empresa_id), activo=True)
        except (MercadoPagoCuenta.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'success': False,
                                 'error': 'Esa empresa no tiene cuenta de Mercado Pago guardada.'},
                                status=400)
    else:
        config.cuenta = None
    # Con máquina Point asociada la caja puede cobrar por ambos canales
    config.modo = 'AMBOS' if config.device_id else 'QR'
    # Principal = la caja que usa el POS de la sucursal (obtener_config y
    # pos_dashboard ordenan por -es_principal). Primera caja = principal; el
    # admin puede marcar otra y aquí se destrona a la anterior EXPLÍCITAMENTE
    # (MercadoPagoConfig no tiene save() que lo haga); una caja que se MUEVE
    # de sucursal nunca destrona a la principal del destino; y la sucursal de
    # origen no queda sin principal.
    otra_principal = MercadoPagoConfig.objects.filter(
        sucursal=sucursal, es_principal=True).exclude(id=config.id).exists()
    quiere_principal = request.POST.get('es_principal')
    if not otra_principal:
        config.es_principal = True
    elif movida:
        config.es_principal = False
    elif quiere_principal == '1':
        config.es_principal = True
    else:
        config.es_principal = False
    try:
        with transaction.atomic():
            config.save()
            if config.es_principal:
                _hacer_principal_mp(config)
            if origen_id:
                _asegurar_principal_mp(origen_id)
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
    configs = [serializar_config_mp(cfg) for cfg in MercadoPagoConfig.objects.select_related(
        'sucursal', 'sucursal__empresa', 'cuenta', 'cuenta__empresa').order_by('sucursal__alias', 'nombre')]
    return JsonResponse({'success': True, 'cuentas': cuentas, 'configs': configs})


def _cuenta_por_empresa(request, requerir_activa=False):
    try:
        qs = MercadoPagoCuenta.objects.filter(empresa_id=int(request.POST.get('empresa_id', 0)))
        if requerir_activa:
            qs = qs.filter(activo=True)
        return qs.get()
    except (MercadoPagoCuenta.DoesNotExist, TypeError, ValueError):
        return None


def _int_o_cero(valor):
    """int() tolerante para ids que vienen del POST (un 'abc' era un 500)."""
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def _hacer_principal_mp(config):
    """Deja a `config` como ÚNICA caja principal de su sucursal. MercadoPagoConfig
    NO sobreescribe save() (el que destrona es el de ConfiguracionPOS), así que
    el destronado se hace aquí, explícito. Llamar dentro de la transacción y
    con `config` ya guardado."""
    MercadoPagoConfig.objects.filter(sucursal_id=config.sucursal_id, es_principal=True) \
        .exclude(id=config.id).update(es_principal=False)
    if not config.es_principal:
        MercadoPagoConfig.objects.filter(id=config.id).update(es_principal=True)
        config.es_principal = True


def _asegurar_principal_mp(sucursal_id):
    """Si la sucursal quedó sin caja principal (se movió o borró), promueve la
    que realmente toma obtener_config/pos_dashboard: habilitada primero y, a
    igualdad, la de menor id. Así la ⭐ de la tabla dice la verdad."""
    if not sucursal_id or MercadoPagoConfig.objects.filter(sucursal_id=sucursal_id, es_principal=True).exists():
        return
    primera = MercadoPagoConfig.objects.filter(sucursal_id=sucursal_id).order_by('-habilitado', 'id').first()
    if primera:
        MercadoPagoConfig.objects.filter(id=primera.id).update(es_principal=True)


def caja_mp_de_sucursal(sucursal_id, con_maquina=False):
    """La caja Mercado Pago con la que opera una sucursal, o None.

    ÚNICA regla de resolución del proyecto: la usan el cierre impreso, el cobro
    directo de un usuario no admin y la preselección del selector del admin. Que
    sea una sola función es el punto: mientras la pantalla preseleccionaba solo
    cuando la caja tenía ``es_principal``, una sucursal sin principal marcada
    dejaba el ``<select>`` sin opción elegida y el navegador caía en la PRIMERA
    de la lista — la de otra tienda. El cierre salía entonces con la venta de esa
    otra tienda (NICK1 estando en PAO1), que es justo lo que se reportó.

    `con_maquina=True` exige caja habilitada y con máquina Point asociada (lo que
    necesita el cierre impreso y el cobro por terminal). `False` sirve para
    preseleccionar: prefiere igual la que serviría para cobrar, pero si no hay
    devuelve cualquier caja de la sucursal antes que una ajena.
    """
    if not sucursal_id:
        return None
    qs = MercadoPagoConfig.objects.select_related('sucursal').filter(sucursal_id=sucursal_id)
    operable = qs.filter(habilitado=True).exclude(device_id='').order_by('-es_principal', 'id').first()
    if con_maquina:
        return operable
    return operable or qs.order_by('-habilitado', '-es_principal', 'id').first()


def _msg_sin_cuenta(request):
    """Error legible cuando la empresa pedida no tiene MercadoPagoCuenta: dice
    QUÉ empresa es y cómo salir del paso (elegir otra cuenta en el formulario)."""
    try:
        emp = Empresa.objects.filter(id=int(request.POST.get('empresa_id', 0) or 0)).first()
    except (TypeError, ValueError):
        emp = None
    nombre = (emp.nombre or emp.razon_social) if emp else 'La empresa de la sucursal'
    return (f'«{nombre}» no tiene cuenta de Mercado Pago guardada. En el formulario de '
            'asociación elige la empresa correcta en «Cuenta Mercado Pago» (p.ej. la dueña '
            'de la máquina), o guarda primero sus credenciales.')


def serializar_config_mp(cfg):
    """Dict de una caja para las tablas de gestión (template inicial y AJAX).
    Incluye con qué cuenta cobra de verdad: la explícita (FK ``cuenta``) o, en
    automático, la de la empresa dueña de la sucursal (si existe)."""
    empresa = cfg.sucursal.empresa
    cuenta_nombre = ''
    if cfg.cuenta_id:
        cuenta_nombre = cfg.cuenta.empresa.nombre or cfg.cuenta.empresa.razon_social
    empresa_tiene_cuenta = MercadoPagoCuenta.objects.filter(empresa_id=empresa.id, activo=True).exists()
    return {
        'id': cfg.id,
        'sucursal_id': cfg.sucursal_id,
        'sucursal_alias': cfg.sucursal.alias,
        'nombre': cfg.nombre,
        'external_store_id': cfg.external_store_id,
        'external_pos_id': cfg.external_pos_id,
        'device_id': cfg.device_id,
        'habilitado': cfg.habilitado,
        'es_principal': cfg.es_principal,
        'cuenta_empresa_id': cfg.cuenta.empresa_id if cfg.cuenta_id else None,
        'cuenta_nombre': cuenta_nombre,
        'empresa_id': empresa.id,
        'empresa_nombre': empresa.nombre or empresa.razon_social,
        # Con qué cuenta cobra de verdad (lo que resuelve _cuenta_de en el service)
        'cuenta_efectiva': cuenta_nombre or ((empresa.nombre or empresa.razon_social)
                                              if empresa_tiene_cuenta else ''),
    }


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
        return JsonResponse({'success': False, 'error': _msg_sin_cuenta(request)}, status=404)
    try:
        devices = mp.listar_devices_point(cuenta)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    # A qué sucursal/caja del SISTEMA está asignada cada máquina (puede ser
    # ninguna, o más de una si se asoció a mano dos veces).
    ids = [d.get('device_id') for d in devices if d.get('device_id')]
    asignaciones = {}
    for cfg in MercadoPagoConfig.objects.filter(device_id__in=ids).select_related('sucursal'):
        asignaciones.setdefault(cfg.device_id, []).append({
            'config_id': cfg.id,
            'sucursal_id': cfg.sucursal_id,
            'sucursal': cfg.sucursal.alias or cfg.sucursal.nombre or f'Sucursal {cfg.sucursal_id}',
            'caja': cfg.nombre,
            'habilitado': cfg.habilitado,
            'es_principal': cfg.es_principal,
        })
    for d in devices:
        d['asignaciones'] = asignaciones.get(d.get('device_id'), [])
    sucursales = [{'id': suc.id, 'alias': suc.alias or suc.nombre or f'Sucursal {suc.id}'}
                  for suc in Sucursal.objects.order_by('alias')]
    return JsonResponse({'success': True, 'devices': devices, 'sucursales': sucursales})


@login_required
@require_POST
def gestion_modo_device_mp(request):
    """POST gestion/devices/modo/ — cambia PDV/STANDALONE de una Point.

    PDV: la máquina queda esclava del sistema (no cobra desde su pantalla).
    STANDALONE: vuelve a operar sola. El cambio es reversible al instante,
    pero MP exige REINICIAR la máquina para que lo tome.

    Dos formas de llamarlo:
    - admin: ``empresa_id`` + ``device_id`` (cualquier máquina de la cuenta),
      o ``config_id`` (la máquina de esa caja).
    - cualquier usuario logueado: ``config_id`` o nada → SOLO la máquina de
      la caja de su sucursal de sesión. Es la palanca de contingencia de la
      tienda: si el sistema no puede mandar el cobro, se pasa la máquina a
      Standalone, se cobra desde su pantalla y se registra en el POS con
      "MP manual" + N° de operación. Antes era solo-admin y la tienda
      quedaba sin poder cobrar hasta ubicar a uno.
    """
    modo = (request.POST.get('modo') or '').strip().upper()
    if modo not in ('PDV', 'STANDALONE'):
        return JsonResponse({'success': False, 'error': 'Modo inválido (PDV o STANDALONE).'}, status=400)
    device_id = (request.POST.get('device_id') or '').strip()

    if _es_admin(request) and request.POST.get('empresa_id'):
        cuenta = _cuenta_por_empresa(request)
        if not cuenta:
            return JsonResponse({'success': False, 'error': 'La empresa no tiene cuenta MP guardada.'}, status=404)
        if not device_id:
            return JsonResponse({'success': False, 'error': 'Falta el device_id.'}, status=400)
        config = None
    else:
        try:
            config_id = int(request.POST.get('config_id') or 0)
        except (TypeError, ValueError):
            config_id = 0
        config, err = _config_operable(request, config_id)
        if err:
            return err
        if not config.device_id:
            return JsonResponse({'success': False, 'error': 'Tu caja no tiene máquina Point asociada.'}, status=400)
        if device_id and device_id != config.device_id and not _es_admin(request):
            return JsonResponse({'success': False,
                                 'error': 'Solo puedes cambiar el modo de la máquina de tu propia caja.'},
                                status=403)
        # Un no-admin siempre opera la máquina de SU caja, mande lo que mande.
        device_id = config.device_id if not _es_admin(request) else (device_id or config.device_id)
        cuenta = mp._cuenta_de(config)
        if not cuenta:
            return JsonResponse({'success': False,
                                 'error': 'La empresa de la sucursal no tiene cuenta MP guardada.'}, status=404)
    try:
        modo_final = mp.cambiar_modo_device(cuenta, device_id, modo)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    logger.warning("MP gestión: device %s -> %s por %s (caja %s)",
                   device_id, modo_final, request.user.username,
                   config.id if config else 'por empresa')
    return JsonResponse({'success': True, 'operating_mode': modo_final, 'device_id': device_id})


@login_required
def gestion_maquinas_mp(request):
    """GET gestion/maquinas/ — TODAS las máquinas Point de TODAS las cuentas
    activas, con la sucursal/caja del sistema a la que está asignada cada una.

    Es lo que alimenta la tarjeta «Máquinas POS» de la pestaña Mercado Pago:
    antes, para saber dónde estaba una máquina había que entrar cuenta por
    cuenta. Solo admin: hace una llamada a Mercado Pago POR CUENTA (unos
    segundos), por eso se carga bajo demanda y no al abrir la página. Una
    cuenta que falla no tumba el resto: se informa en `errores`.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)

    maquinas, errores = [], []
    for cuenta in MercadoPagoCuenta.objects.filter(activo=True).select_related('empresa'):
        empresa = cuenta.empresa.nombre or cuenta.empresa.razon_social or cuenta.empresa.rut
        try:
            devices = mp.listar_devices_point(cuenta)
        except mp.MercadoPagoError as e:
            errores.append({'empresa': empresa, 'error': e.mensaje})
            continue
        for d in devices:
            maquinas.append({
                'device_id': d.get('device_id') or '',
                'operating_mode': (d.get('operating_mode') or '').upper(),
                'external_pos_id': d.get('external_pos_id') or '',
                'empresa_id': cuenta.empresa_id,
                'empresa': empresa,
                'asignaciones': [],
            })

    ids = [m['device_id'] for m in maquinas if m['device_id']]
    asignadas = {}
    for cfg in (MercadoPagoConfig.objects.filter(device_id__in=ids)
                .select_related('sucursal').order_by('-es_principal', 'id')):
        asignadas.setdefault(cfg.device_id, []).append({
            'config_id': cfg.id,
            'sucursal_id': cfg.sucursal_id,
            'sucursal': cfg.sucursal.alias or cfg.sucursal.nombre or f'Sucursal {cfg.sucursal_id}',
            'caja': cfg.nombre,
            'habilitado': cfg.habilitado,
            'es_principal': cfg.es_principal,
        })
    for m in maquinas:
        m['asignaciones'] = asignadas.get(m['device_id'], [])

    return JsonResponse({
        'success': True,
        'maquinas': maquinas,
        'errores': errores,
        'sucursales': [{'id': suc.id, 'alias': suc.alias or suc.nombre or f'Sucursal {suc.id}'}
                       for suc in Sucursal.objects.order_by('alias')],
    })


@login_required
@require_POST
def gestion_reasignar_device_mp(request):
    """POST gestion/devices/reasignar/ — mueve una máquina Point a otra
    sucursal/caja EN EL SISTEMA (qué caja del POS la usa). Solo admin.

    Body: empresa_id (cuenta dueña de la máquina), device_id, sucursal_id
    (destino), nombre (caja destino; vacío = su caja principal, se crea si no
    hay), external_pos_id (el que MP reporta para la máquina, opcional).

    - Las cajas que tenían la máquina la pierden (quedan solo QR).
    - La caja destino recibe la máquina, queda habilitada, con cuenta explícita
      = la dueña de la máquina (la sucursal puede ser de otra empresa).
    - Si la caja destino no tiene ID de caja QR, se le pone el que MP reporta
      para la máquina SOLO si ninguna otra caja lo usa (dos cajas con el mismo
      external_pos_id confunden la conciliación); si no, se avisa.
    NO cambia la asociación dentro de Mercado Pago (sucursal/caja MP de la
    máquina): eso se hace en el panel de MP.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    cuenta = _cuenta_por_empresa(request, requerir_activa=True)
    if not cuenta:
        return JsonResponse({'success': False, 'error': _msg_sin_cuenta(request)}, status=404)
    device_id = (request.POST.get('device_id') or '').strip()[:60]
    if not device_id:
        return JsonResponse({'success': False, 'error': 'Falta el device_id.'}, status=400)
    try:
        sucursal = Sucursal.objects.get(id=int(request.POST.get('sucursal_id') or 0))
    except (Sucursal.DoesNotExist, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Sucursal inválida'}, status=400)
    nombre = (request.POST.get('nombre') or '').strip()[:100]
    ext_pos_mp = (request.POST.get('external_pos_id') or '').strip()[:60]
    empresa_cuenta = cuenta.empresa.nombre or cuenta.empresa.razon_social or cuenta.empresa.rut

    avisos = []
    with transaction.atomic():
        previas = list(MercadoPagoConfig.objects.select_for_update()
                       .filter(device_id=device_id).select_related('sucursal'))
        if nombre:
            destino = MercadoPagoConfig.objects.filter(sucursal=sucursal, nombre=nombre).first()
        else:
            destino = (next((p for p in previas if p.sucursal_id == sucursal.id), None)
                       or MercadoPagoConfig.objects.filter(sucursal=sucursal)
                       .order_by('-es_principal', 'id').first())
        if destino is None:
            destino = MercadoPagoConfig(sucursal=sucursal, nombre=nombre or 'Caja principal')
        liberadas = [p for p in previas if p.id != destino.id]

        # ── Nada se mueve mientras una caja que pierde la máquina tenga un
        # cobro en curso en ella: la orden seguiría viva en la pantalla del
        # terminal, ocupando la única ranura, y la caja nueva no podría cobrar
        # (already_queued) sin que nadie sepa de dónde viene. Va ANTES de
        # cualquier escritura.
        for p in liberadas:
            en_curso = None
            vivas = (TransaccionMercadoPago.objects
                     .filter(config=p, canal='POINT', estado__in=('CREADA', 'PENDIENTE'))
                     .order_by('-creado_en'))
            for t in vivas:
                # Una reserva vencida (el worker murió antes del POST: nunca
                # existió en MP) no es un cobro en curso; se cierra igual que
                # hace cobros_vivos_de_ticket. Si bloqueara, «Liberar máquina»
                # tampoco la listaría (no tiene order_id): callejón sin salida.
                # Una incierta (ENVIADA sin order_id) sí sigue bloqueando.
                if mp.es_reserva(t) and mp._edad_seg(t) > mp.MP_RESERVA_MAX_SEG:
                    mp._aplicar_estado(t, 'ERROR', detalle='No se alcanzó a enviar a Mercado Pago')
                    continue
                en_curso = t
                break
            if en_curso:
                monto = f'{en_curso.monto:,}'.replace(',', '.')
                return JsonResponse({'success': False, 'error': (
                    f'«{p.sucursal.alias} · {p.nombre}» tiene un cobro en curso en esa máquina '
                    f'(ticket {en_curso.correlativo_ticket or "sin ticket"}, ${monto}). Termínalo o '
                    'cancélalo en la máquina, o usa «Liberar máquina», antes de moverla.')},
                    status=400)

        # ── Una caja = una cuenta MP. Si la caja destino ya cobra con OTRA
        # cuenta, sus cobros históricos resuelven el token por la config
        # (TransaccionMercadoPago no guarda la cuenta): cambiársela rompería
        # devoluciones por API y el control del cierre. Se exige una caja aparte.
        if destino.pk:
            # Cuenta "de registro" de la caja: el FK explícito aunque esté
            # inactivo (sus cobros se hicieron con esa cuenta), si no la que
            # resuelve el service por la empresa de la sucursal.
            cuenta_actual = (MercadoPagoCuenta.objects.select_related('empresa').filter(id=destino.cuenta_id).first()
                             if destino.cuenta_id else mp._cuenta_de(destino))
            if cuenta_actual and cuenta_actual.id != cuenta.id:
                if destino.transacciones.exists():
                    empresa_actual = (cuenta_actual.empresa.nombre or cuenta_actual.empresa.razon_social
                                      or cuenta_actual.empresa.rut)
                    return JsonResponse({'success': False, 'error': (
                        f'«{sucursal.alias} · {destino.nombre}» ya tiene cobros hechos con la cuenta de '
                        f'{empresa_actual}; no se puede pasar a la cuenta de {empresa_cuenta} sin romper las '
                        'devoluciones y el control del cierre de esos cobros. Escribe un nombre de caja NUEVO '
                        '(p.ej. "Point ' + empresa_cuenta[:20] + '") para que la máquina quede en una caja aparte.')},
                        status=400)
                if destino.external_pos_id or destino.external_store_id:
                    # IDs de caja/sucursal de la otra cuenta: con este token MP los rechaza.
                    avisos.append(f'La caja destino tenía el ID de caja QR «{destino.external_pos_id}» de otra '
                                  f'cuenta; se quitó. Para QR en pantalla asigna una caja creada en la cuenta de '
                                  f'{empresa_cuenta} desde el formulario.')
                    destino.external_pos_id = ''
                    destino.external_store_id = ''

        for p in liberadas:
            p.device_id = ''
            p.modo = 'QR'
            campos = ['device_id', 'modo', 'actualizado_en']
            # La cuenta explícita existía por la máquina (caja de OTRA empresa):
            # sin cobros hechos vuelve a automático, para no dejarla cobrando
            # QR con un token ajeno ni bloquear (PROTECT) el borrado de la cuenta.
            if (p.cuenta_id == cuenta.id and p.sucursal.empresa_id != cuenta.empresa_id
                    and not p.transacciones.exists()):
                p.cuenta = None
                campos.append('cuenta')
                if p.external_pos_id or p.external_store_id:
                    # Esos IDs existen en la cuenta de la máquina, no en la de
                    # su propia empresa: con el token propio MP los rechazaría.
                    avisos.append(f'{p.sucursal.alias} · {p.nombre} tenía el ID de caja QR «{p.external_pos_id}» '
                                  f'de la cuenta de {empresa_cuenta}; se quitó.')
                    p.external_pos_id = ''
                    p.external_store_id = ''
                    campos += ['external_pos_id', 'external_store_id']
            if not p.external_pos_id and p.habilitado:
                # Sin máquina ni ID de caja QR no puede cobrar por ningún canal:
                # habilitada, el POS mostraría "MP QR" y fallaría siempre.
                p.habilitado = False
                campos.append('habilitado')
                avisos.append(f'{p.sucursal.alias} · {p.nombre} quedó sin máquina y sin ID de caja QR: se '
                              'deshabilitó (el POS de esa sucursal no mostrará Mercado Pago hasta asignarle '
                              'una máquina o un ID de caja).')
            p.save(update_fields=campos)

        destino.device_id = device_id
        destino.modo = 'AMBOS'
        destino.cuenta = cuenta
        destino.habilitado = True
        if not destino.external_pos_id:
            if ext_pos_mp and not MercadoPagoConfig.objects.filter(external_pos_id=ext_pos_mp).exists():
                destino.external_pos_id = ext_pos_mp
            else:
                avisos.append('La caja destino no tiene ID de caja QR (external_pos_id): el cobro por máquina '
                              'funciona igual; para QR en pantalla asígnalo desde el formulario.')
        # La caja con la máquina es la que debe usar el POS (toma la principal):
        # siempre principal, destronando a la anterior de forma explícita.
        anterior = (MercadoPagoConfig.objects.filter(sucursal=sucursal, es_principal=True)
                    .exclude(id=destino.id).first())
        if anterior and anterior.external_pos_id and not destino.external_pos_id:
            avisos.append(f'«{anterior.nombre}» deja de ser la caja principal de {sucursal.alias}: el POS '
                          'cobrará por la máquina, pero pierde el QR en pantalla hasta que la caja nueva '
                          'tenga ID de caja QR.')
        destino.es_principal = True
        destino.save()
        _hacer_principal_mp(destino)
    logger.warning("MP gestión: máquina %s movida a %s · %s (config %s) por %s; liberadas: %s",
                   device_id, sucursal.alias, destino.nombre, destino.id, request.user.username,
                   [f'{p.sucursal.alias}·{p.nombre}' for p in liberadas] or '-')
    return JsonResponse({
        'success': True,
        'config_id': destino.id,
        'sucursal': sucursal.alias or sucursal.nombre or f'Sucursal {sucursal.id}',
        'caja': destino.nombre,
        'liberadas': [f'{p.sucursal.alias} · {p.nombre}' for p in liberadas],
        'liberadas_ids': [p.id for p in liberadas],
        'aviso': ' '.join(avisos),
    })


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
        return JsonResponse({'success': False, 'error': _msg_sin_cuenta(request)}, status=404)
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
    config = MercadoPagoConfig.objects.filter(id=_int_o_cero(request.POST.get('config_id'))).first()
    if not config:
        return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    from django.db.models import ProtectedError
    sucursal_id = config.sucursal_id
    try:
        config.delete()
    except ProtectedError:
        config.habilitado = False
        config.save(update_fields=['habilitado', 'actualizado_en'])
        return JsonResponse({'success': False,
                             'error': 'La caja ya tiene cobros registrados y no puede borrarse: quedó DESHABILITADA.'},
                            status=400)
    # Si se borró la principal, la siguiente por id pasa a serlo (es la que el
    # POS ya tomaba por desempate; así la tabla lo muestra).
    _asegurar_principal_mp(sucursal_id)
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
        id=_int_o_cero(request.POST.get('config_id'))).first()
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
    # Usuario normal: solo ve el cierre de SU sucursal de sesión
    if not _es_admin(request):
        sucursal_id = _sucursal_sesion(request)
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'No hay sucursal en sesión.'}, status=400)
        base = base.filter(sucursal_id=sucursal_id)

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

    # ── ?verificar=1 → cruce contra la API de Mercado Pago ──────────────────
    # Responde la pregunta que el resumen local no puede: ¿MP cobró lo mismo?
    # La búsqueda de pagos es POR CUENTA, así que se hace una sola vez por
    # token y se reparte entre las cajas que lo comparten.
    if str(request.GET.get('verificar') or '') in ('1', 'true', 'True'):
        configs = {c.id: c for c in MercadoPagoConfig.objects.filter(
            id__in=[k for k in cajas if k]).select_related('sucursal', 'cuenta')}
        pagos_por_token = {}
        for config_id, caja in cajas.items():
            config = configs.get(config_id)
            if not config:
                continue
            try:
                token = mp._token(config)
            except mp.MercadoPagoError as e:
                caja['control'] = {'ok': False, 'error': e.mensaje}
                continue
            if token not in pagos_por_token:
                try:
                    pagos_por_token[token] = mp.buscar_pagos_dia(config, fecha)
                except mp.MercadoPagoError as e:
                    pagos_por_token[token] = None
                    caja['control'] = {'ok': False, 'error': e.mensaje}
            pagos = pagos_por_token.get(token)
            if pagos is None:
                caja.setdefault('control', {'ok': False,
                                            'error': 'No se pudo consultar Mercado Pago.'})
                continue
            caja['control'] = mp.conciliar_cierre_mp(config, fecha, pagos=pagos)

    return JsonResponse({'success': True, 'fecha': fecha,
                         'cajas': list(cajas.values()), 'total': total})


@login_required
@require_POST
def gestion_imprimir_cierre_terminal_mp(request):
    """POST gestion/terminal/imprimir-cierre/ — imprime el cierre del día de
    una caja EN LA IMPRESORA de su máquina Point (API de Impresiones).

    Disponible para CUALQUIER usuario logueado (sacar el cierre es operación
    de tienda): un no-admin queda limitado a la caja de SU sucursal de sesión;
    el admin puede elegir caja (config_id) y, si no elige, sale la de su
    sesión. El ticket lleva fecha, hora y el usuario responsable que lo pidió.

    DE QUÉ SUCURSAL SALE EL PAPEL. El ticket mezcla dos fuentes y conviene no
    confundirlas:

      - cobros Mercado Pago (QR / Point, por medio, control contra la API):
        son de la CAJA (`config`);
      - «VENTA DEL DÍA — todos los medios de pago» y el TOTAL GLOBAL: son de
        la SUCURSAL de esa caja, calculados con la misma cuadratura del arqueo
        (`_calcular_cuadratura_data`), no solo con lo cobrado por esa caja.

    Por eso la caja elegida manda sobre TODO el papel: con la caja de otra
    tienda, el cierre sale con la venta de esa otra tienda. La respuesta
    devuelve `sucursal` y `es_de_tu_sucursal` para que la pantalla lo diga.
    """
    sucursal_id = _int_o_cero(_sucursal_sesion(request))
    config_id = _int_o_cero(request.POST.get('config_id'))
    if _es_admin(request) and config_id:
        config = MercadoPagoConfig.objects.select_related('sucursal').filter(id=config_id).first()
        if not config:
            return JsonResponse({'success': False, 'error': 'Caja no encontrada.'}, status=404)
    else:
        # Sin caja elegida (o usuario no admin) manda la sucursal de la SESIÓN.
        # Antes un admin sin `config_id` recibía "Caja no encontrada" en vez de
        # caer en su propia sucursal, y el selector podía traer la caja de otra
        # tienda: el cierre salía con la venta de esa otra tienda.
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'No hay sucursal en sesión.'}, status=400)
        config = caja_mp_de_sucursal(sucursal_id, con_maquina=True)
        if not config:
            return JsonResponse({'success': False,
                                 'error': 'Tu sucursal no tiene una caja con máquina Point asociada.'},
                                status=404)
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
    caja['responsable'] = (request.user.get_full_name() or request.user.username)

    # ── Venta del día de la sucursal: TODOS los medios de pago + total global.
    # Sale de la MISMA cuadratura del arqueo, así el papel siempre calza con
    # lo que la tienda cuadra en pantalla.
    try:
        from .views_modulo_ventas import _calcular_cuadratura_data
        cua = _calcular_cuadratura_data(config.sucursal, fecha)
        medios_dia = [
            ('EFECTIVO', cua.get('total_efectivo', 0)),
            ('TRANSBANK DEBITO', cua.get('total_tarjeta_debito', 0)),
            ('TRANSBANK CREDITO', cua.get('total_visa_mc_amex', 0)),
            # MP presencial desglosado igual que Transbank (débito/crédito);
            # QR sin dato / dinero en cuenta quedan en OTROS.
            ('MP POS DEBITO', cua.get('total_mercadopago_pos_debito', 0)),
            ('MP POS CREDITO', cua.get('total_mercadopago_pos_credito', 0)),
            ('MP POS QR/OTROS', cua.get('total_mercadopago_pos_otros', 0)),
            ('TRANSFERENCIA', cua.get('total_transferencia', 0)),
            ('TARJETA COMERCIAL', cua.get('total_tarjetas_comerciales', 0)),
            ('VENTA INTERNET', cua.get('total_venta_internet', 0)),
            ('GIFT CARD', cua.get('total_giftcard', 0)),
            ('CRED. TRABAJADOR', cua.get('total_credito_trabajador', 0)),
            ('CRED. EXTERNO', cua.get('total_credito_externo', 0)),
            ('CONVENIO', cua.get('total_convenio', 0)),
            ('ORDEN COMPRA', cua.get('total_orden_compra', 0)),
            ('CHEQUE', cua.get('total_cheque', 0)),
        ]
        caja['dia_sucursal'] = [(n, int(m or 0)) for n, m in medios_dia if int(m or 0)]
        caja['dia_nc'] = int(cua.get('total_notas_credito', 0) or 0)
        caja['dia_total_global'] = int(cua.get('venta_total', 0) or 0)
    except Exception as e:
        # El cierre MP sale igual aunque la cuadratura falle
        logger.warning(f"MP cierre: no se pudo calcular la venta del día: {e}")

    # ── Control contra la API de MP: lo que Mercado Pago cobró de verdad ────
    # Si falla la consulta, el ticket lo dice ("cierre SIN verificar") en vez
    # de salir aparentemente cuadrado.
    try:
        caja['control_mp'] = mp.conciliar_cierre_mp(config, fecha)
    except Exception as e:  # noqa: BLE001 — el cierre nunca se cae por esto
        logger.warning(f"MP cierre: no se pudo verificar contra la API: {e}")
        caja['control_mp'] = {'ok': False, 'error': 'error inesperado'}
    if caja['control_mp'].get('ok') and caja['control_mp'].get('diferencia'):
        logger.warning(
            "MP cierre %s caja %s: DIFERENCIA de $%s (sistema $%s vs MP $%s) — %s cobro(s) sin registrar",
            fecha, config.id, caja['control_mp']['diferencia'],
            caja['control_mp']['sistema_total'], caja['control_mp']['mp_total'],
            len(caja['control_mp']['sin_registro']),
        )

    try:
        contenido = mp.contenido_cierre_terminal(caja, fecha)
        mp.imprimir_en_terminal(
            config, contenido,
            f"CIERRE-{config.id}-{fecha}-{timezone.now():%H%M%S}",
        )
    except mp.MercadoPagoError as e:
        mensaje = e.mensaje
        if 'already_queued' in str(e.detalle):
            mensaje += _pendiente_en_terminal(config)
        return JsonResponse({'success': False, 'error': mensaje}, status=400)
    logger.info("MP gestión: cierre %s de caja %s (%s) impreso en terminal por %s",
                fecha, config.id, config.sucursal.alias, request.user.username)
    if sucursal_id and config.sucursal_id != sucursal_id:
        # No se bloquea (un admin puede sacar el cierre de otra tienda a
        # propósito), pero queda el rastro: el papel con la venta de otra
        # sucursal fue el síntoma que originó esta revisión.
        logger.warning("MP gestión: %s imprimió el cierre de %s estando en la sucursal %s",
                       request.user.username, config.sucursal.alias, sucursal_id)
    # La respuesta dice QUÉ se imprimió: la pantalla lo muestra para que un
    # cierre de otra sucursal se note antes de mirar el papel.
    return JsonResponse({'success': True, 'caja': config.nombre,
                         'sucursal': config.sucursal.alias,
                         'sucursal_id': config.sucursal_id,
                         'es_de_tu_sucursal': bool(sucursal_id) and config.sucursal_id == sucursal_id,
                         'fecha': fecha})


@login_required
@require_POST
def gestion_cobrar_terminal_mp(request):
    """POST gestion/terminal/cobrar/ — COBRO DIRECTO desde la pestaña Mercado
    Pago de /app/pos/transbank/, para CUALQUIER usuario logueado.

    Sirve para no perder la venta cuando el botón del POS no responde: el cobro
    sale por la máquina Point (canal POINT) o como QR en pantalla (canal QR).
    Un no-admin cobra SOLO con la caja habilitada de su sucursal de sesión; el
    admin elige caja con ``config_id``.

    - Con ``correlativo`` (N° de ticket del POS): el cobro queda ligado a esa
      venta y el POS lo detecta al entrar al cobro del ticket (en-curso/),
      exactamente igual que uno hecho con el botón M. Pago. Pasa por el mismo
      guard anti doble cobro que el POS (MP_COBRO_EN_CURSO).
    - Sin ticket: correlativo DIRECTO-* (fuera de la cuadratura de tickets;
      visible en Dineros y en el resumen por terminal) y se registra en el
      POS con "MP manual" + N° de operación.
    """
    try:
        config_id = int(request.POST.get('config_id') or 0)
    except (TypeError, ValueError):
        config_id = 0
    config, err = _config_operable(request, config_id,
                                   requerir_habilitada=not _es_admin(request))
    if err:
        return err
    try:
        monto = int(request.POST.get('monto', 0) or 0)
    except (TypeError, ValueError):
        monto = 0
    if monto < 50:
        return JsonResponse({'success': False, 'error': 'Monto mínimo $50.'}, status=400)
    canal = (request.POST.get('canal') or ('POINT' if config.device_id else 'QR')).strip().upper()
    if canal not in ('QR', 'POINT'):
        return JsonResponse({'success': False, 'error': 'Canal inválido (POINT o QR).'}, status=400)
    if canal == 'POINT' and not config.device_id:
        return JsonResponse({'success': False, 'error': 'Esa caja no tiene máquina Point asociada.'}, status=400)
    if canal == 'QR' and not config.external_pos_id:
        return JsonResponse({'success': False,
                             'error': 'Esa caja no tiene ID de caja QR (external_pos_id).'}, status=400)

    ticket = re.sub(r'[^A-Za-z0-9\-]', '', (request.POST.get('correlativo') or '').strip())
    ticket = ticket[:mp.MP_CORRELATIVO_MAX_LEN]
    if ticket.upper().startswith(mp.PREFIJOS_CORRELATIVO_SIN_TICKET):
        return JsonResponse({'success': False, 'error': 'Ese N° de ticket no es válido.'}, status=400)
    correlativo = ticket or f"DIRECTO-{timezone.now():%d%m-%H%M%S}"
    descripcion = (f'Venta {ticket}' if ticket
                   else f'Cobro directo {canal} {config.external_pos_id or config.nombre}')
    try:
        transaccion, qr_data = mp.crear_orden(
            config, correlativo, monto, canal=canal,
            descripcion=descripcion, usuario=request.user,
        )
    except mp.MercadoPagoError as e:
        cuerpo = {'success': False, 'error': e.mensaje}
        if 'already_queued' in str(e.detalle):
            # La máquina está ocupada con OTRA operación (puede ser de otro
            # ticket o una impresión): decir cuál, que se cancela en el terminal.
            cuerpo['error'] += _pendiente_en_terminal(config)
        # Ya hay un cobro vivo/incierto del MISMO ticket: la pantalla lo vigila
        # en vez de dejar cobrar de nuevo (mismo contrato que qr/crear/).
        previa = getattr(e, 'transaccion', None)
        if previa is not None:
            cuerpo['error_tipo'] = 'MP_COBRO_EN_CURSO'
            cuerpo['transaccion_id'] = previa.id
            cuerpo['cobro'] = mp.resumen_cobro(previa)
        elif _es_admin(request):
            # Payload crudo de MP solo al admin: es la única forma de
            # diagnosticar un 400 de la Orders API sin ir a los logs.
            try:
                cuerpo['detalle'] = json.dumps(e.detalle, ensure_ascii=False)[:800] if e.detalle else ''
            except (TypeError, ValueError):
                cuerpo['detalle'] = str(e.detalle)[:800]
        return JsonResponse(cuerpo, status=400)
    incierto = mp.es_incierta(transaccion)
    logger.warning("MP gestión: COBRO DIRECTO %s $%s caja %s (%s) por %s%s",
                   canal, monto, config.id, correlativo, request.user.username,
                   ' — SIN CONFIRMAR' if incierto else '')
    return JsonResponse({
        'success': True,
        'estado': 'INCIERTO' if incierto else 'OK',
        'transaccion_id': transaccion.id,
        'canal': canal,
        'monto': monto,
        'correlativo': correlativo,
        'con_ticket': bool(ticket),
        'qr_data': qr_data,
        'qr_base64': mp.qr_png_base64(qr_data) if qr_data else None,
        'expira_en_segundos': mp.QR_TIMEOUT_SEGUNDOS,
        'caja': f"{config.sucursal.alias} · {config.nombre}",
        'mensaje': ('No pudimos confirmar el envío a Mercado Pago. Estamos '
                    'verificando: NO cobres de nuevo todavía.') if incierto else '',
    })


@login_required
@require_POST
def gestion_liberar_terminal_mp(request):
    """POST gestion/terminal/liberar/ — qué tiene encolado Mercado Pago en la
    máquina de una caja y, con ``aplicar=1``, cancelar lo cancelable. Solo
    admin.

    Body: config_id (obligatorio; la caja cuya máquina se revisa), aplicar
    ('1' = cancelar; cualquier otra cosa = solo mirar), dias (default 10,
    tope 30), ids (con aplicar=1: transaccion_id separados por coma de las
    órdenes que el admin vio y confirmó; solo esas se cancelan). Con aplicar=0
    es solo lectura salvo dos cosas: si MP dice que una orden está PAGADA y
    localmente nunca registró plata, se refleja (``pagadas_detectadas``) — es
    plata real y tiene que verse en Dineros —, y si MP ya cerró una orden que
    el sistema tenía viva, se cierra local (``cerradas_local``).
    """
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo Administrador.'}, status=403)
    config_id = _int_o_cero(request.POST.get('config_id'))
    if not config_id:
        return JsonResponse({'success': False, 'error': 'Falta la caja (config_id).'}, status=400)
    config, err = _config_operable(request, config_id)
    if err:
        return err
    if not config.device_id:
        return JsonResponse({'success': False, 'error': 'Esa caja no tiene máquina Point asociada.'},
                            status=400)
    aplicar = (request.POST.get('aplicar') or '').strip() == '1'
    dias = min(max(_int_o_cero(request.POST.get('dias')) or 10, 1), 30)
    solo_ids = None
    if aplicar:
        # Sin lista (cliente viejo o vacía) no se cancela nada a ciegas: todo
        # lo cancelable va a no_cancelables con el motivo "vuelve a consultar".
        solo_ids = [i for i in (_int_o_cero(x) for x in
                                (request.POST.get('ids') or '').split(',')) if i > 0]
    try:
        informe = mp.liberar_terminal(config, dias=dias, aplicar=aplicar, usuario=request.user,
                                      solo_ids=solo_ids, max_ordenes=30)
    except mp.MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    logger.warning("MP gestión: liberar máquina %s (caja %s, %s días) por %s: %s/%s consultadas, "
                   "%s encontradas, %s canceladas, %s no cancelables, %s pagadas detectadas, "
                   "%s cerradas local, %s errores%s",
                   config.device_id, config.id, dias, request.user.username,
                   informe['consultadas'], informe['total_candidatas'],
                   len(informe['encontradas']), len(informe['canceladas']),
                   len(informe['no_cancelables']), len(informe['pagadas_detectadas']),
                   len(informe['cerradas_local']), len(informe['errores']),
                   '' if aplicar else ' (solo lectura)')
    return JsonResponse({'success': True, **informe})


@login_required
def gestion_mi_caja_mp(request):
    """GET gestion/mi-caja/?config_id=&vivo=1 — panel "Tu caja" de la pestaña
    Mercado Pago: identificación de la caja con la que opera el usuario, modo
    actual de su máquina Point (PDV = integrada al sistema / STANDALONE =
    cobra sola; consultado EN VIVO a MP con ``vivo=1``), cobros de hoy y los
    últimos movimientos. Cualquier usuario logueado; un no-admin ve SOLO la
    caja de su sucursal de sesión."""
    try:
        config_id = int(request.GET.get('config_id') or 0)
    except (TypeError, ValueError):
        config_id = 0
    config, err = _config_operable(request, config_id)
    if err:
        return err

    hoy = timezone.localdate()
    trxs = list(
        TransaccionMercadoPago.objects.filter(config=config, creado_en__date=hoy)
        .exclude(correlativo_ticket__startswith='PRUEBA-')
        .select_related('usuario').order_by('-creado_en')
    )
    aprobadas = [t for t in trxs if t.tipo == 'VENTA' and t.estado == 'APROBADA']
    ultimos = [{
        'id': t.id,
        'hora': timezone.localtime(t.creado_en).strftime('%H:%M'),
        'monto': t.monto,
        'tipo': t.tipo,
        'estado': t.estado,
        'estado_detalle': t.estado_detalle,
        'canal': t.canal,
        'correlativo': t.correlativo_ticket,
        'directo': t.correlativo_ticket.startswith('DIRECTO-'),
        'medio': mp.etiqueta_medio_mp(t.metodo_pago_mp),
        'payment_id_mp': t.payment_id_mp,
        'payment_id': t.payment_id,
        'consumida': t.consumida,
        'incierto': mp.es_incierta(t),
        'vivo': t.estado in ('CREADA', 'PENDIENTE'),
        'edad_seg': int((timezone.now() - t.creado_en).total_seconds()),
        'usuario': ((t.usuario.get_full_name() or t.usuario.username)
                    if t.usuario_id else ''),
    } for t in trxs[:12]]

    device = {'device_id': config.device_id, 'consultado': False, 'ok': False,
              'operating_mode': '', 'error': ''}
    if config.device_id and str(request.GET.get('vivo') or '') in ('1', 'true', 'True'):
        device['consultado'] = True
        cuenta = mp._cuenta_de(config)
        if not cuenta:
            device['error'] = 'La empresa de la sucursal no tiene cuenta MP guardada.'
        else:
            try:
                for d in mp.listar_devices_point(cuenta):
                    if d.get('device_id') == config.device_id:
                        device['ok'] = True
                        device['operating_mode'] = (d.get('operating_mode') or '').upper()
                        break
                else:
                    device['error'] = ('La máquina asociada a esta caja no aparece en la '
                                       'cuenta de Mercado Pago de la empresa.')
            except mp.MercadoPagoError as e:
                device['error'] = e.mensaje

    return JsonResponse({
        'success': True,
        'es_admin': _es_admin(request),
        'config': {
            'id': config.id,
            'nombre': config.nombre,
            'sucursal_id': config.sucursal_id,
            'sucursal': config.sucursal.alias or config.sucursal.nombre or f'Sucursal {config.sucursal_id}',
            'external_pos_id': config.external_pos_id,
            'external_store_id': config.external_store_id,
            'device_id': config.device_id,
            'habilitado': config.habilitado,
            'es_principal': config.es_principal,
            'puede_point': bool(config.device_id),
            'puede_qr': bool(config.external_pos_id),
        },
        'device': device,
        'hoy': {
            'cobros': len(aprobadas),
            'monto': sum(t.monto for t in aprobadas),
            'directos': sum(1 for t in aprobadas if t.correlativo_ticket.startswith('DIRECTO-')),
            'vivos': sum(1 for t in trxs if t.estado in ('CREADA', 'PENDIENTE')),
        },
        'ultimos': ultimos,
    })


@login_required
def verificar_pago_mp_dte(request, dte_id):
    """GET api/mercadopago/dte/<id>/pagos/ — "Verificar por Mercado Pago":
    dice si la venta original del DTE tiene cobros MP, cuánto se devolvió y
    cuánto queda devolvible a la tarjeta. Lo usa el modal de NC de
    gestión-DTE para habilitar/validar la devolución vía API."""
    dte = Dte.objects.filter(id=dte_id).select_related('sucursal').first()
    if not dte:
        return JsonResponse({'success': False, 'error': 'DTE no encontrado'}, status=404)
    resumen = mp.resumen_pagos_mp_de_dte(dte)
    return JsonResponse({
        'success': True,
        'tiene_mp': bool(resumen['transacciones']),
        'total_disponible': resumen['total_disponible'],
        'transacciones': resumen['transacciones'],
        'puede_devolver': _es_admin(request),
    })


# ==================== PANTALLA DINEROS ====================

@login_required
def dineros_mercadopago(request):
    """GET /app/ventas/dineros-mercadopago/ — página de Conciliación Mercado Pago."""
    es_admin = _es_admin(request)
    configs = list(MercadoPagoConfig.objects.select_related('sucursal')
                   .order_by('sucursal__alias', 'nombre')
                   .values('id', 'nombre', 'sucursal_id', 'sucursal__alias'))
    sucursales = []
    for c in configs:
        if c['sucursal_id'] not in [s['sucursal_id'] for s in sucursales]:
            sucursales.append({'sucursal_id': c['sucursal_id'], 'sucursal__alias': c['sucursal__alias']})
    # Retiros y reportes de Liberaciones son de la CUENTA (una por empresa/RUT),
    # no de la caja: el selector muestra una opción por cuenta con sus cajas.
    cuentas = {}
    if es_admin:
        from .services import conciliacion_mp_service as conc
        for i, info in enumerate(conc.cuentas_mp()):
            cuentas[i] = {'id': info['config'].id, 'nombre': info['nombre'], 'rut': info['rut'],
                          'cajas': ', '.join(sorted(info['cajas']))}
    return render(request, 'vistas/modulo_ventas/dinerosMercadoPago.html', {
        'es_admin': es_admin,
        'configs': configs if es_admin else [],
        'cuentas_mp': list(cuentas.values()),
        'sucursales': sucursales if es_admin else [],
    })


@login_required
def api_dineros_mercadopago(request):
    """GET /app/api/mercadopago/dineros/?fecha_desde=&fecha_hasta=

    KPIs y tablas del ciclo del dinero MP: cobrado → pendiente de liberación →
    liberado → depositado. Todo con datos locales (sin llamar a MP al pintar).
    """
    from .services import conciliacion_mp_service as conc
    # Mismo rango y sucursal que «Cobros y documentos»: antes estos KPIs usaban
    # 30 días y todas las sucursales, y no se podían comparar con los de al lado.
    d, h = conc.rango_fechas(request.GET.get('fecha_desde'), request.GET.get('fecha_hasta'))
    fecha_desde, fecha_hasta = str(d), str(h)
    sucursal_id = _sucursal_filtro_conciliacion(request)

    ahora = timezone.now()
    base = TransaccionMercadoPago.objects.filter(
        tipo='VENTA',
        creado_en__date__gte=fecha_desde,
        creado_en__date__lte=fecha_hasta,
    ).exclude(correlativo_ticket__startswith='PRUEBA-').select_related('sucursal', 'retiro')
    if sucursal_id:
        base = base.filter(sucursal_id=sucursal_id)

    aprobadas = base.filter(estado='APROBADA')

    def _suma(qs, campo='monto'):
        return int(qs.aggregate(total=Sum(campo))['total'] or 0)

    def _neto(qs):
        # Neto = lo que MP deja tras su comisión (es lo que llega al banco).
        return int(qs.aggregate(total=Sum(Coalesce('monto_neto', 'monto')))['total'] or 0)

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
        'pendiente_liberacion_neto': _neto(pendiente_liberacion),
        'liberado_sin_retirar_neto': _neto(liberado_sin_retirar),
        'depositado_neto': _neto(depositado),
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

    # Cuenta (empresa) de cada retiro y su desglose por caja, guardado al aplicar
    # el reporte de Liberaciones.
    nombre_cuenta = {}

    def _cuenta_de_retiro(r):
        if r.config_id not in nombre_cuenta:
            try:
                cuenta = mp._cuenta_de(r.config)
            except AttributeError:
                cuenta = None
            empresa = cuenta.empresa if cuenta else (r.config.sucursal.empresa if r.config.sucursal_id else None)
            nombre_cuenta[r.config_id] = getattr(empresa, 'nombre', '') or ''
        return nombre_cuenta[r.config_id]

    def _raw(r, clave, defecto):
        return (r.raw_reporte or {}).get(clave, defecto) if isinstance(r.raw_reporte, dict) else defecto

    retiros = []
    for r in (RetiroMercadoPago.objects.select_related('config__sucursal__empresa', 'config__cuenta__empresa')
              .annotate(n_trx=Count('transacciones')).order_by('-fecha', '-id')[:100]):
        instante = _raw(r, 'instante', '')
        try:
            hora = timezone.localtime(datetime.fromisoformat(instante)).strftime('%H:%M') if instante else ''
        except ValueError:
            hora = ''
        retiros.append({
            'withdrawal_id': r.withdrawal_id,
            'fecha': r.fecha.strftime('%d/%m/%Y'),
            'hora': hora,
            'cuenta': _cuenta_de_retiro(r),
            'monto': r.monto,
            'estado': r.estado,
            'visto_en_cartola': r.visto_en_cartola,
            'detalle': r.detalle_diferencia or '',
            'transacciones': r.n_trx,
            'por_caja': _raw(r, 'por_caja', []) or [],
        })

    return JsonResponse({
        'success': True,
        'fecha_desde': str(fecha_desde),
        'fecha_hasta': str(fecha_hasta),
        'kpis': kpis,
        'por_sucursal': por_sucursal,
        'cuadre': conc.cuadre_por_sucursal(fecha_desde, fecha_hasta, sucursal_id=sucursal_id),
        'transacciones': transacciones,
        'retiros': retiros,
        'configs': list(MercadoPagoConfig.objects.values(
            'id', 'sucursal__alias', 'nombre', 'modo', 'habilitado')),
    })


# ==================== CONCILIACIÓN MERCADO PAGO ====================
# Cobros ↔ documentos ↔ liberaciones ↔ banco. La lógica vive en
# services/conciliacion_mp_service.py; acá solo permisos y E/S.

def _sucursal_filtro_conciliacion(request):
    """Admin elige sucursal (o todas); el resto ve solo la de su sesión."""
    if _es_admin(request):
        valor = request.GET.get('sucursal_id') or request.POST.get('sucursal_id') or ''
        return int(valor) if str(valor).isdigit() else None
    return _sucursal_sesion(request)


@login_required
def api_conciliacion_cobros_mp(request):
    """GET /app/api/mercadopago/conciliacion/cobros/?desde=&hasta=&sucursal_id=

    Cada cobro MP del período con su venta y su documento (boleta/factura).
    Solo datos locales: rápido, sirve para el arqueo.
    """
    from .services import conciliacion_mp_service as conc
    data = conc.cobros_vs_documentos(
        request.GET.get('desde'), request.GET.get('hasta'),
        sucursal_id=_sucursal_filtro_conciliacion(request),
    )
    data['success'] = True
    data['es_admin'] = _es_admin(request)
    data['sucursales'] = list(
        MercadoPagoConfig.objects.values('sucursal_id', 'sucursal__alias').distinct()
    ) if _es_admin(request) else []
    return JsonResponse(data)


@login_required
def api_conciliacion_contra_mp(request):
    """GET /app/api/mercadopago/conciliacion/contra-mp/?desde=&hasta=&sucursal_id=

    Cruce contra la API de MP (máx. 7 días): cierre por caja y día, pagos que
    MP tiene y el sistema no, pagos "MP manual" cuyo N° no existe en MP.
    """
    from .services import conciliacion_mp_service as conc
    data = conc.diferencias_contra_mp(
        request.GET.get('desde'), request.GET.get('hasta'),
        sucursal_id=_sucursal_filtro_conciliacion(request),
    )
    data['success'] = True
    return JsonResponse(data)


def _config_conciliacion(request):
    """(config, error_response) para las acciones de liberaciones (solo admin)."""
    if not _es_admin(request):
        return None, JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    valor = request.POST.get('config_id') or request.GET.get('config_id')
    config = MercadoPagoConfig.objects.filter(pk=_int_o_cero(valor)).first()
    if config is None:
        return None, JsonResponse({'success': False, 'error': 'Elija la caja/cuenta de Mercado Pago.'}, status=400)
    return config, None


@login_required
@require_POST
def api_conciliacion_liberaciones_mp(request):
    """POST /app/api/mercadopago/conciliacion/liberaciones/  (solo admin)

    Procesa un reporte de Liberaciones de MP: crea los retiros al banco y
    amarra cada cobro a su retiro. Fuente (una de dos):
      - `archivo`: CSV descargado del panel de MP;
      - `file_name`: un reporte ya generado en MP (ver .../liberaciones/reportes/).
    `aplicar=1` escribe; sin él es una vista previa (dry-run).

    Ya NO pide ni espera el reporte acá: generarlo tarda minutos y el request
    moría esperando (ver .../liberaciones/pedir/).
    """
    from .services import conciliacion_mp_service as conc
    config, err = _config_conciliacion(request)
    if err:
        return err
    aplicar = str(request.POST.get('aplicar') or '') in ('1', 'true', 'True')
    archivo = request.FILES.get('archivo')
    file_name = (request.POST.get('file_name') or '').strip()
    try:
        if archivo is not None:
            if archivo.size > 20 * 1024 * 1024:
                return JsonResponse({'success': False, 'error': 'Archivo demasiado grande (máx. 20 MB).'}, status=400)
            contenido, origen = archivo.read(), archivo.name
        elif file_name:
            contenido, origen = conc.descargar_reporte_liberaciones(config, file_name), file_name
        else:
            return JsonResponse({
                'success': False,
                'error': 'Elija un reporte de la lista de Mercado Pago o suba el archivo CSV.'
            }, status=400)
        filas = conc.leer_csv(contenido)
        resultado = conc.procesar_reporte_liberaciones(filas, config, aplicar=False, archivo=origen)
        # Pagos del reporte que no se cruzaron: casi siempre es porque el cobro
        # no tiene guardado el N° de operación de MP. Se completa desde la API
        # (solo campos vacíos) y se vuelve a cruzar.
        completado = None
        dias = conc.dias_para_completar(config, resultado)
        if dias:
            completado = conc.completar_numeros_mp(config, dias)
        # Si MP no dejó terminar de completar N°/fechas, se aplica igual pero sin
        # marcar el reporte: «Detectar retiros» o un nuevo «Aplicar» lo rehace.
        completo = completado is None or (not completado['sin_tiempo'] and not completado.get('fallidos'))
        if aplicar or (completado and completado.get('actualizados')):
            resultado = conc.procesar_reporte_liberaciones(filas, config, aplicar=aplicar,
                                                           archivo=origen if completo else '')
        resultado['numeros_completados'] = completado
        if aplicar and not completo:
            resultado['aviso'] = ('Mercado Pago no respondió a tiempo al completar los N° de operación: '
                                  'el reporte se aplicó pero no quedó marcado. Vuelva a aplicarlo en unos minutos.')
    except MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    except Exception as e:  # noqa: BLE001 — archivo con formato inesperado
        logger.exception("Conciliación MP: error procesando liberaciones")
        return JsonResponse({'success': False, 'error': f'No se pudo leer el reporte: {e}'}, status=400)
    if not filas or not (resultado['retiros'] or resultado['liberado_sin_retirar']):
        # Nada reconocible: devolver los encabezados ayuda a ajustar el lector.
        resultado['encabezados'] = list(filas[0].keys()) if filas else []
    if aplicar:
        logger.info("Conciliación MP: liberaciones aplicadas config=%s origen=%s retiros=%s por %s",
                    config.id, origen, len(resultado['retiros']), request.user.username)
    return JsonResponse({'success': True, 'aplicado': aplicar, 'filas_leidas': len(filas),
                         'origen': origen, **resultado})


@login_required
@require_POST
def api_conciliacion_detectar_retiros_mp(request):
    """POST .../liberaciones/detectar/ (solo admin).

    Revisa TODAS las cuentas de Mercado Pago, aplica los reportes de
    Liberaciones nuevos que traen retiros y devuelve lo encontrado. No hay que
    elegir cuenta ni reporte. `pedir=0` (las vueltas automáticas de la página)
    no le pide reportes nuevos a MP: solo aplica los que ya terminaron.
    """
    from .services import conciliacion_mp_service as conc
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    pedir = str(request.POST.get('pedir', '1')) != '0'
    # Vueltas automáticas: cuentas a las que no se alcanzó a pedir el reporte, y
    # los pedidos que la página está esperando (la caché es de cada worker).
    pedir_ids = {int(x) for x in str(request.POST.get('pedir_ids') or '').split(',') if x.strip().isdigit()}
    try:
        crudo = json.loads(request.POST.get('pedidos') or '{}')
    except ValueError:
        crudo = {}
    pedidos = ({int(k): v for k, v in crudo.items() if str(k).isdigit() and isinstance(v, dict)}
               if isinstance(crudo, dict) else {})
    cuentas = conc.detectar_retiros(presupuesto_seg=45, pedir=pedir, pedir_ids=pedir_ids, pedidos=pedidos)
    logger.info("Conciliación MP: detectar retiros por %s → %s",
                request.user.username,
                [(c['cuenta'], c['reportes_aplicados'], len(c['retiros'])) for c in cuentas])
    return JsonResponse({'success': True, 'cuentas': cuentas})


@login_required
def api_conciliacion_retiro_detalle_mp(request, withdrawal_id):
    """GET .../conciliacion/retiro/<withdrawal_id>/ (solo admin): las ventas y
    documentos que se llevó un retiro, y lo que no se pudo explicar con ventas."""
    from .services import conciliacion_mp_service as conc
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    retiro = RetiroMercadoPago.objects.filter(withdrawal_id=withdrawal_id).first()
    if retiro is None:
        return JsonResponse({'success': False, 'error': 'No existe ese retiro.'}, status=404)
    return JsonResponse({'success': True, **conc.detalle_retiro(retiro)})


@login_required
@require_POST
def api_conciliacion_liberaciones_pedir_mp(request):
    """POST .../liberaciones/pedir/ {config_id, desde, hasta} (solo admin).

    Pide a MP que genere el reporte y responde al instante con el id de la
    tarea; la página consulta su estado cada pocos segundos.
    """
    from .services import conciliacion_mp_service as conc
    config, err = _config_conciliacion(request)
    if err:
        return err
    d, h = conc.rango_fechas(request.POST.get('desde'), request.POST.get('hasta'),
                             dias_defecto=1, max_dias=60)
    try:
        tarea = conc.pedir_reporte_liberaciones(config, d, h)
    except MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    return JsonResponse({'success': True, **tarea})


@login_required
def api_conciliacion_liberaciones_tarea_mp(request):
    """GET .../liberaciones/tarea/?config_id=&task_id= (solo admin): ¿ya está el reporte?"""
    from .services import conciliacion_mp_service as conc
    config, err = _config_conciliacion(request)
    if err:
        return err
    task_id = _int_o_cero(request.GET.get('task_id'))
    if not task_id:
        return JsonResponse({'success': False, 'error': 'Falta task_id'}, status=400)
    try:
        return JsonResponse({'success': True, **conc.estado_tarea_liberaciones(config, task_id)})
    except MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)


@login_required
def api_conciliacion_liberaciones_reportes_mp(request):
    """GET .../liberaciones/reportes/?config_id= (solo admin): reportes descargables en MP."""
    from .services import conciliacion_mp_service as conc
    config, err = _config_conciliacion(request)
    if err:
        return err
    try:
        reportes = conc.listar_reportes_liberaciones(config)
    except MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    # La configuración es informativa: si falla, igual se muestran los reportes.
    try:
        cfg = conc.leer_config_reporte(config)
        por_retiro = bool(cfg and cfg.get('execute_after_withdrawal'))
    except MercadoPagoError:
        por_retiro = None
    procesados = conc.reportes_aplicados() if reportes else set()
    for r in reportes:
        r['procesado'] = r['file_name'] in procesados
    return JsonResponse({
        'success': True,
        'reportes': reportes,
        'por_retiro_activo': por_retiro,
    })


@login_required
@require_POST
def api_conciliacion_liberaciones_config_mp(request):
    """POST .../liberaciones/config/ {config_id} (solo admin).

    Activa en MP la generación automática del reporte después de cada retiro.
    """
    from .services import conciliacion_mp_service as conc
    config, err = _config_conciliacion(request)
    if err:
        return err
    try:
        data = conc.activar_reporte_por_retiro(config)
    except MercadoPagoError as e:
        return JsonResponse({'success': False, 'error': e.mensaje}, status=400)
    logger.info("Conciliación MP: reporte por retiro activado config=%s por %s",
                config.id, request.user.username)
    return JsonResponse({'success': True,
                         'por_retiro_activo': bool(data.get('execute_after_withdrawal', True))})


@login_required
@require_POST
def api_conciliacion_cartola_mp(request):
    """POST /app/api/mercadopago/conciliacion/cartola/  (solo admin)

    `archivo`: CSV de la cartola del banco. Marca «visto en cartola» los retiros
    de MP cuyo abono aparece (mismo monto, fecha ±3 días). `aplicar=1` escribe.
    """
    from .services import conciliacion_mp_service as conc
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    archivo = request.FILES.get('archivo')
    if archivo is None:
        return JsonResponse({'success': False, 'error': 'Adjunte la cartola en CSV.'}, status=400)
    aplicar = str(request.POST.get('aplicar') or '') in ('1', 'true', 'True')
    try:
        movimientos = conc.leer_cartola(archivo.read())
    except Exception as e:  # noqa: BLE001
        return JsonResponse({'success': False, 'error': f'No se pudo leer la cartola: {e}'}, status=400)
    if not movimientos:
        return JsonResponse({
            'success': False,
            'error': 'La cartola no tiene abonos reconocibles (se esperan columnas Fecha y Abonos/Monto).'
        }, status=400)
    resultado = conc.conciliar_cartola(movimientos, aplicar=aplicar)
    return JsonResponse({'success': True, 'aplicado': aplicar,
                         'abonos_leidos': len(movimientos), **resultado})


@login_required
@require_POST
def api_retiro_visto_cartola_mp(request):
    """POST /app/api/mercadopago/conciliacion/retiro-visto/ {withdrawal_id, visto} (solo admin)."""
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
    retiro = RetiroMercadoPago.objects.filter(withdrawal_id=str(data.get('withdrawal_id') or '')).first()
    if retiro is None:
        return JsonResponse({'success': False, 'error': 'Retiro no encontrado'}, status=404)
    retiro.visto_en_cartola = bool(data.get('visto'))
    retiro.save(update_fields=['visto_en_cartola', 'actualizado_en'])
    return JsonResponse({'success': True, 'visto_en_cartola': retiro.visto_en_cartola})
