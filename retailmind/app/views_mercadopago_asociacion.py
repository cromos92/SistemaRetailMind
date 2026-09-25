"""
Asociar cobros de Mercado Pago con su venta (pantalla Conciliación Mercado Pago).

Quien tenga el permiso `asociar_pagos_mercadopago` (el Maestro siempre;
Administrador y Administración desde la mig. 0235; otros roles si se les
enciende en Permisos). La lógica vive en services/asociacion_mp_service.py.

Alcance: el administrador asigna en todas las tiendas; cualquier otro rol solo
ventas y cobros de la tienda de su sesión, y nunca convierte efectivo o
transferencia a Mercado Pago. Se valida en el servidor, no solo en la pantalla.
"""
import datetime as _dt
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .decorators import requiere_permiso
from .models import MercadoPagoConfig, TicketDetallePago, TransaccionMercadoPago
from .services import asociacion_mp_service as asoc
from .services.mercadopago_service import MercadoPagoError
from .views_mercadopago import _es_admin, _sucursal_sesion

logger = logging.getLogger('app')

PERMISO = asoc.CODIGO_PERMISO
# Además hay que ver Conciliación Mercado Pago: sin la pantalla, sin sus APIs.
PANTALLA = 'dineros_mercadopago'


def _fecha(valor, defecto):
    try:
        return _dt.datetime.strptime(str(valor), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return defecto


def _rango(request, dias_defecto=7, max_dias=93):
    hoy = timezone.localdate()
    hasta = _fecha(request.GET.get('hasta'), hoy)
    desde = _fecha(request.GET.get('desde'), hasta - _dt.timedelta(days=dias_defecto - 1))
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days >= max_dias:
        desde = hasta - _dt.timedelta(days=max_dias - 1)
    return desde, hasta


def _int(valor):
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def _error(mensaje, status=400):
    return JsonResponse({'success': False, 'error': mensaje, 'mensaje': mensaje}, status=status)


def alcance(request):
    """None = todas las tiendas (administrador / administración); si no, la tienda
    de la sesión (-1 si no tiene: no alcanza a ninguna)."""
    if _es_admin(request):
        return None
    return _int(_sucursal_sesion(request)) or -1


def _cajas(sucursal=None):
    qs = MercadoPagoConfig.objects.select_related('sucursal').order_by('sucursal__alias', 'nombre')
    if sucursal is not None:
        qs = qs.filter(sucursal_id=sucursal)
    return [{
        'id': c.id, 'nombre': c.nombre, 'sucursal_id': c.sucursal_id,
        'sucursal': c.sucursal.alias if c.sucursal_id else '',
    } for c in qs]


@login_required
@requiere_permiso(PERMISO, 'puede_ver')
@requiere_permiso(PANTALLA, 'puede_ver')
@require_GET
def api_asociar_pendientes(request):
    """GET ?desde=&hasta=&sucursal_id= — cobros sin venta y pagos MP manuales sin respaldo."""
    desde, hasta = _rango(request)
    tienda = alcance(request)
    sucursal = tienda if tienda is not None else (_int(request.GET.get('sucursal_id')) or None)
    data = asoc.pendientes(sucursal, desde, hasta)
    return JsonResponse({'success': True, 'desde': str(desde), 'hasta': str(hasta), 'cajas': _cajas(tienda), **data})


@login_required
@requiere_permiso(PERMISO, 'puede_ver')
@requiere_permiso(PANTALLA, 'puede_ver')
@require_GET
def api_asociar_candidatos(request):
    """GET ?trx_id=&q=&todas=1 — pagos de ticket que pueden ser ese cobro,
    o ?pago_id=&todas=1[&buscar_mp=1&config_id=] — cobros (o pagos de la API) para ese pago."""
    tienda = alcance(request)
    todas = request.GET.get('todas') in ('1', 'true') and tienda is None
    otros_medios = tienda is None     # solo el administrador convierte efectivo / transferencia
    monto = _int(request.GET.get('monto'))
    if monto:
        # Pago que solo existe en Mercado Pago: ?monto=&instante=ISO (o fecha=YYYY-MM-DD HH:MM)&sucursal_id=&payment_id=
        momento = asoc.conc._instante(request.GET.get('instante'))
        texto = str(request.GET.get('fecha') or '')
        for formato in ('%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
            if momento is not None:
                break
            try:
                momento = timezone.make_aware(_dt.datetime.strptime(texto.strip()[:16], formato))
            except ValueError:
                continue
        momento = momento or timezone.now()
        sucursal = tienda if tienda is not None else (_int(request.GET.get('sucursal_id')) or None)
        return JsonResponse({
            'success': True,
            'cajas': _cajas(tienda),
            'candidatos': asoc.candidatos_pagos(
                monto, momento, sucursal, request.GET.get('q', ''), todas,
                n_operaciones=(request.GET.get('payment_id'),), permitir_otros_medios=otros_medios),
        })

    trx_id = _int(request.GET.get('trx_id'))
    if trx_id:
        trx = TransaccionMercadoPago.objects.select_related('sucursal', 'config').filter(id=trx_id).first()
        if trx is None or (tienda is not None and trx.sucursal_id != tienda):
            return _error('El cobro no existe.', 404)
        return JsonResponse({
            'success': True,
            'cobro': asoc._fila_cobro(trx),
            'candidatos': asoc.candidatos_para_cobro(trx, request.GET.get('q', ''), todas,
                                                     permitir_otros_medios=otros_medios),
        })

    pago = TicketDetallePago.objects.select_related('ticket', 'ticket__sucursal').filter(
        id=_int(request.GET.get('pago_id'))).first()
    if pago is None or (tienda is not None and pago.ticket.sucursal_id != tienda):
        return _error('El pago no existe.', 404)
    respuesta = {
        'success': True,
        'pago': asoc._fila_pago(pago),
        'candidatos': asoc.candidatos_para_pago(pago, todas),
        'cajas': _cajas(tienda),
        'pagos_mp': [],
    }
    if request.GET.get('buscar_mp') in ('1', 'true'):
        config = MercadoPagoConfig.objects.filter(id=_int(request.GET.get('config_id'))).first()
        if config is None or (tienda is not None and config.sucursal_id != tienda):
            return _error('Elige la caja / cuenta de Mercado Pago para buscar.')
        try:
            respuesta['pagos_mp'] = asoc.pagos_api_para_pago(pago, config)
        except MercadoPagoError as e:
            return _error(e.mensaje)
    return JsonResponse(respuesta)


def _leer(request):
    try:
        return json.loads(request.body or '{}')
    except ValueError:
        return None


def _otros_medios(request, data):
    """Pasar efectivo / transferencia a MP: solo administrador y confirmándolo."""
    return alcance(request) is None and data.get('confirmar_otro_medio') is True


@login_required
@requiere_permiso(PERMISO, 'puede_editar')
@requiere_permiso(PANTALLA, 'puede_ver')
@require_POST
def api_asociar_cobro(request):
    """POST {trx_id, pago_id, recalcular_arqueo, confirmar_otro_medio} — asocia un cobro MP sin venta."""
    data = _leer(request)
    if data is None:
        return _error('JSON inválido')
    try:
        resultado = asoc.asociar(_int(data.get('trx_id')), _int(data.get('pago_id')), request.user,
                                 recalcular_arqueo=data.get('recalcular_arqueo', True) is not False,
                                 sucursal_permitida=alcance(request),
                                 permitir_otros_medios=_otros_medios(request, data))
    except asoc.AsociacionError as e:
        return _error(str(e))
    return JsonResponse({'success': True, **resultado})


@login_required
@requiere_permiso(PERMISO, 'puede_editar')
@requiere_permiso(PANTALLA, 'puede_ver')
@require_POST
def api_asociar_importar(request):
    """POST {payment_id, pago_id, config_id, recalcular_arqueo, confirmar_otro_medio}
    — trae un pago de la API y lo asocia."""
    data = _leer(request)
    if data is None:
        return _error('JSON inválido')
    try:
        resultado = asoc.importar_y_asociar(
            data.get('payment_id'), _int(data.get('pago_id')), _int(data.get('config_id')), request.user,
            recalcular_arqueo=data.get('recalcular_arqueo', True) is not False,
            sucursal_permitida=alcance(request), permitir_otros_medios=_otros_medios(request, data))
    except asoc.AsociacionError as e:
        return _error(str(e))
    except MercadoPagoError as e:
        return _error(e.mensaje)
    return JsonResponse({'success': True, **resultado})


@login_required
@requiere_permiso(PERMISO, 'puede_editar')
@requiere_permiso(PANTALLA, 'puede_ver')
@require_POST
def api_asociar_lote(request):
    """POST {items: [{payment_id, pago_id, config_id}, ...]} — «Asignar todas las
    sugeridas» de «Contra Mercado Pago». Cada uno por separado; el arqueo de
    cada tienda y día se recalcula una sola vez al final."""
    data = _leer(request)
    if (data is None or not isinstance(data.get('items'), list)
            or not all(isinstance(i, dict) for i in data['items'])):
        return _error('JSON inválido')
    if not data['items']:
        return _error('No hay pagos para asignar.')
    resultado = asoc.asociar_lote(data['items'], request.user, sucursal_permitida=alcance(request))
    logger.warning('MP: %s asignó por lote %s pago(s) de Mercado Pago (%s fallidos)',
                   request.user.username, resultado['asignados'], resultado['fallidos'])
    return JsonResponse({'success': True, **resultado})
