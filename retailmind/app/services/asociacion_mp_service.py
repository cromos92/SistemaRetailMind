"""
Asociación de cobros de Mercado Pago con el pago de su venta.

Dos descalces que la cuadratura y el cierre de la máquina no pueden resolver
solos:

1. **Cobro MP sin venta**: una `TransaccionMercadoPago` APROBADA que ningún
   pago respalda (`consumida=False`). Pasa cuando el cajero cierra la ventana
   de espera y termina la venta como tarjeta manual (caso NICK2 05-09), o
   cuando cobra desde el panel «Tu caja» (`DIRECTO-…`) y registra la venta
   aparte como «MP manual».
2. **Pago «MP manual» sin respaldo**: un pago `MP_*` con `origen_pago='MANUAL'`
   (N° de operación digitado) que no tiene transacción detrás. Si el N° está
   mal escrito, la plata no calza con nada.

Asociar = el pago del ticket pasa al método MP real del cobro, el espejo del
documento (`Dte_Detalle_Pago`) se corrige igual, y la transacción queda
consumida y amarrada al ticket. Es la misma operación que hace
`reparar_cobro_mp_huerfano`, pero elegida a mano por el
Maestro en vez de exigir el mismo correlativo.

Para un pago que Mercado Pago tiene y el sistema no (cobrado fuera del POS),
`importar_y_asociar` lo trae de la API y crea la transacción local ya
consumida.

Regla de oro: **monto EXACTO**. No se reparte plata entre pagos distintos.
"""
import datetime as _dt
import logging

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import (
    ArqueoCaja,
    Dte,
    Dte_Detalle_Pago,
    MercadoPagoConfig,
    Ticket,
    TicketDetallePago,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')

CODIGO_PERMISO = 'asociar_pagos_mercadopago'

# Métodos con los que un cajero registra una tarjeta "a mano" (Transbank o
# genérico). Son los que se pueden convertir a Mercado Pago.
METODOS_TARJETA_MANUAL = (
    'TBK_CREDITO_POS', 'TBK_DEBITO_POS', 'TBK_PREPAGO_POS',
    'TBK_MANUAL', 'TBK_POS_INTEGRADO',
    'TARJETA_CREDITO', 'TARJETA_DEBITO',
)
METODOS_MP = ('MP_QR', 'MP_POINT', 'MP_POINT_DEBITO', 'MP_POINT_CREDITO')

# Ticket.tipo_dte -> Dte.tipo_documento (para ubicar el espejo del pago)
TIPO_DTE_A_DOCUMENTO = {
    'BOLETA_ELECTRONICA': 'BOLETA ELECTRONICA',
    'BOLETA': 'BOLETA PAPEL',
    'FACTURA_ELECTRONICA': 'FACTURA ELECTRONICA',
    'FACTURA_EXENTA': 'FACTURA EXENTA',
}

MAX_CANDIDATOS = 40


class AsociacionError(Exception):
    """Error de negocio: el mensaje se muestra tal cual al usuario."""


def _plata(v):
    return f"${int(v or 0):,}".replace(',', '.')


def _rango_local(desde, hasta):
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(_dt.datetime.combine(desde, _dt.time.min), tz)
    fin = timezone.make_aware(_dt.datetime.combine(hasta, _dt.time.max), tz)
    return inicio, fin


def _n_operacion(trx):
    """N° de operación que el usuario ve en el panel de MP (nunca el ULID)."""
    return trx.payment_id_mp or (trx.payment_id if not str(trx.payment_id or '').startswith('PAY') else '')


def _sin_transaccion():
    return ~Exists(TransaccionMercadoPago.objects.filter(detalle_pago_id=OuterRef('pk')))


def es_pago_asociable(pago):
    """Tarjeta manual, o MP digitado a mano que todavía no tiene transacción."""
    if pago.metodo_pago in METODOS_TARJETA_MANUAL:
        return True
    return pago.metodo_pago in METODOS_MP and pago.origen_pago == 'MANUAL'


# ---------------------------------------------------------------------------
# Listados
# ---------------------------------------------------------------------------

def _fila_cobro(trx):
    creado = timezone.localtime(trx.creado_en)
    return {
        'id': trx.id,
        'fecha': creado.strftime('%d/%m/%Y'),
        'hora': creado.strftime('%H:%M'),
        'sucursal_id': trx.sucursal_id,
        'sucursal': trx.sucursal.alias if trx.sucursal_id else '',
        'caja': trx.config.nombre if trx.config_id else '',
        'correlativo': trx.correlativo_ticket,
        'es_directo': str(trx.correlativo_ticket or '').startswith('DIRECTO-'),
        'monto': trx.monto,
        'medio': mp.etiqueta_medio_mp(trx.metodo_pago_mp),
        'canal': trx.canal,
        'n_operacion': _n_operacion(trx),
        'ultimos_4': trx.ultimos_4_digitos,
    }


def _fila_pago(pago):
    ticket = pago.ticket
    creado = timezone.localtime(ticket.created_at) if ticket.created_at else None
    return {
        'id': pago.id,
        'ticket_id': ticket.id,
        'correlativo': ticket.correlativo,
        'folio': ticket.folio_dte or '',
        'tipo_dte': ticket.tipo_dte or '',
        'fecha': ticket.fecha.strftime('%d/%m/%Y') if ticket.fecha else '',
        'hora': creado.strftime('%H:%M') if creado else '',
        'sucursal_id': ticket.sucursal_id,
        'sucursal': ticket.sucursal.alias if ticket.sucursal_id else '',
        'cliente': ticket.cliente_nombre or '',
        'metodo': pago.metodo_pago,
        'metodo_display': pago.get_metodo_pago_display(),
        'origen': pago.origen_pago or '',
        'voucher': pago.voucher or '',
        'monto': pago.monto,
        'total_ticket': ticket.total,
    }


def cobros_sin_venta(sucursal_id=None, desde=None, hasta=None):
    """Cobros MP aprobados que ningún pago respalda (incluye «cobro directo»)."""
    qs = (TransaccionMercadoPago.objects
          .filter(tipo='VENTA', estado='APROBADA', consumida=False)
          .exclude(correlativo_ticket__startswith='PRUEBA-')
          .select_related('sucursal', 'config')
          .order_by('-creado_en'))
    if sucursal_id:
        qs = qs.filter(sucursal_id=sucursal_id)
    if desde and hasta:
        qs = qs.filter(creado_en__range=_rango_local(desde, hasta))
    return qs


def pagos_manuales_sin_respaldo(sucursal_id=None, desde=None, hasta=None):
    """Pagos «MP manual» de ventas cerradas sin transacción que los respalde."""
    qs = (TicketDetallePago.objects
          .filter(metodo_pago__in=METODOS_MP, origen_pago='MANUAL', ticket__estado='PAGADO')
          .filter(_sin_transaccion())
          .select_related('ticket', 'ticket__sucursal')
          .order_by('-ticket__fecha', '-id'))
    if sucursal_id:
        qs = qs.filter(ticket__sucursal_id=sucursal_id)
    if desde and hasta:
        qs = qs.filter(ticket__fecha__range=(desde, hasta))
    return qs


def pendientes(sucursal_id=None, desde=None, hasta=None):
    cobros = [_fila_cobro(t) for t in cobros_sin_venta(sucursal_id, desde, hasta)[:500]]
    manuales = [_fila_pago(p) for p in pagos_manuales_sin_respaldo(sucursal_id, desde, hasta)[:500]]
    return {
        'cobros': cobros,
        'manuales': manuales,
        'resumen': {
            'cobros_cantidad': len(cobros),
            'cobros_monto': sum(c['monto'] for c in cobros),
            'manuales_cantidad': len(manuales),
            'manuales_monto': sum(m['monto'] for m in manuales),
        },
    }


def resumen_alerta_caja(sucursal_id, fecha):
    """Lo que la Cuadratura muestra como alerta para una sucursal y día."""
    cobros = list(cobros_sin_venta(sucursal_id, fecha, fecha))
    manuales = list(pagos_manuales_sin_respaldo(sucursal_id, fecha, fecha))
    return {
        'cobros_sin_venta': len(cobros),
        'cobros_sin_venta_monto': sum(t.monto for t in cobros),
        'cobros_directos': sum(1 for t in cobros if str(t.correlativo_ticket or '').startswith('DIRECTO-')),
        'manuales_sin_respaldo': len(manuales),
        'manuales_sin_respaldo_monto': sum(p.monto for p in manuales),
        'hay_alerta': bool(cobros or manuales),
    }


# ---------------------------------------------------------------------------
# Candidatos
# ---------------------------------------------------------------------------

def candidatos_pagos(monto, momento, sucursal_id, buscar='', todas_sucursales=False,
                     correlativo='', n_operaciones=(), dias=1):
    """Pagos de ventas cerradas que podrían ser un cobro de `monto` hecho en
    `momento` (datetime con tz): tarjetas manuales o «MP manual» del mismo
    monto, sin transacción, ordenados por coincidencia de N° / correlativo y
    cercanía en el tiempo."""
    dia = timezone.localtime(momento).date()
    qs = (TicketDetallePago.objects
          .filter(ticket__estado='PAGADO', monto=int(monto))
          .filter(Q(metodo_pago__in=METODOS_TARJETA_MANUAL)
                  | Q(metodo_pago__in=METODOS_MP, origen_pago='MANUAL'))
          .filter(_sin_transaccion())
          .select_related('ticket', 'ticket__sucursal'))
    buscar = (buscar or '').strip()
    if buscar:
        filtro = Q(ticket__folio_dte__icontains=buscar) | Q(voucher__icontains=buscar)
        if buscar.isdigit():
            filtro |= Q(ticket__correlativo=int(buscar))
        qs = qs.filter(filtro)
    else:
        qs = qs.filter(ticket__fecha__range=(dia - _dt.timedelta(days=dias), dia + _dt.timedelta(days=dias)))
    if sucursal_id and not todas_sucursales:
        qs = qs.filter(ticket__sucursal_id=sucursal_id)

    n_operaciones = {str(n).strip() for n in n_operaciones if n}
    filas = []
    for pago in qs[:200]:
        fila = _fila_pago(pago)
        fila['mismo_correlativo'] = bool(correlativo) and str(correlativo) == str(pago.ticket.correlativo)
        fila['mismo_voucher'] = bool(pago.voucher) and pago.voucher.strip() in n_operaciones
        creado = pago.ticket.created_at or momento
        fila['minutos'] = int(abs((creado - momento).total_seconds()) // 60)
        filas.append(fila)
    filas.sort(key=lambda f: (not f['mismo_voucher'], not f['mismo_correlativo'], f['minutos']))
    return filas[:MAX_CANDIDATOS]


def candidatos_para_cobro(trx, buscar='', todas_sucursales=False, dias=1):
    """Pagos de ventas cerradas que podrían ser ESTE cobro (mismo monto)."""
    return candidatos_pagos(
        trx.monto, trx.creado_en, trx.sucursal_id, buscar, todas_sucursales,
        correlativo=trx.correlativo_ticket, n_operaciones=(trx.payment_id_mp, trx.payment_id), dias=dias,
    )


def candidatos_para_pago(pago, todas_sucursales=False, dias=1):
    """Cobros MP sin venta que podrían ser el respaldo de ESTE pago."""
    ticket = pago.ticket
    dia = ticket.fecha or timezone.localdate()
    qs = cobros_sin_venta(
        None if todas_sucursales else ticket.sucursal_id,
        dia - _dt.timedelta(days=dias), dia + _dt.timedelta(days=dias),
    ).filter(monto=pago.monto)
    voucher = (pago.voucher or '').strip()
    filas = []
    for trx in qs[:200]:
        fila = _fila_cobro(trx)
        fila['mismo_voucher'] = bool(voucher) and voucher in (str(trx.payment_id_mp or ''), str(trx.payment_id or ''))
        fila['mismo_correlativo'] = str(trx.correlativo_ticket or '') == str(ticket.correlativo)
        creado = ticket.created_at or trx.creado_en
        fila['minutos'] = int(abs((trx.creado_en - creado).total_seconds()) // 60)
        filas.append(fila)
    filas.sort(key=lambda f: (not f['mismo_voucher'], not f['mismo_correlativo'], f['minutos']))
    return filas[:MAX_CANDIDATOS]


def pagos_api_para_pago(pago, config):
    """Pagos APROBADOS de la cuenta MP de `config`, del día del ticket y del
    mismo monto, que el sistema no tiene registrados. Llama a la API."""
    ticket = pago.ticket
    dia = ticket.fecha or timezone.localdate()
    vistos = set()
    filas = []
    for fecha in (dia - _dt.timedelta(days=1), dia, dia + _dt.timedelta(days=1)):
        for p in mp.buscar_pagos_dia(config, fecha):
            pid = str(p.get('id') or '')
            if not pid or pid in vistos:
                continue
            vistos.add(pid)
            if p.get('status') != 'approved':
                continue
            if int(round(float(p.get('transaction_amount') or 0))) != int(pago.monto):
                continue
            filas.append(p)
    ids = [str(p.get('id')) for p in filas]
    registrados = {
        v for par in TransaccionMercadoPago.objects
        .filter(Q(payment_id_mp__in=ids) | Q(payment_id__in=ids))
        .values_list('payment_id_mp', 'payment_id')
        for v in par if v
    }
    resultado = []
    for p in filas:
        pid = str(p.get('id'))
        creado = parse_datetime(p.get('date_created') or '')
        resultado.append({
            'payment_id': pid,
            'fecha': timezone.localtime(creado).strftime('%d/%m/%Y %H:%M') if creado else '',
            'monto': int(round(float(p.get('transaction_amount') or 0))),
            'medio': mp.etiqueta_medio_mp(p.get('payment_type_id')),
            'ultimos_4': ((p.get('card') or {}).get('last_four_digits') or ''),
            'descripcion': p.get('description') or '',
            'external_reference': p.get('external_reference') or '',
            'ya_registrado': pid in registrados,
            'mismo_voucher': pid == (pago.voucher or '').strip(),
        })
    resultado.sort(key=lambda f: (f['ya_registrado'], not f['mismo_voucher']))
    return resultado


# ---------------------------------------------------------------------------
# Asociar
# ---------------------------------------------------------------------------

def _actualizar_espejo_dte(ticket, pago, metodo_anterior, metodo_nuevo, tipo_tarjeta, voucher, sello):
    """El DTE guarda su propia copia de los pagos (`Dte_Detalle_Pago`); si no se
    corrige, los reportes por documento seguirían mostrando el método viejo."""
    dte = None
    if ticket.folio_dte:
        dtes = Dte.objects.filter(sucursal_id=ticket.sucursal_id, numero_documento=ticket.folio_dte)
        tipo_documento = TIPO_DTE_A_DOCUMENTO.get(ticket.tipo_dte or '')
        if tipo_documento:
            dtes = dtes.filter(tipo_documento=tipo_documento)
        dte = dtes.first()
    if dte is None:
        dte = (Dte.objects.filter(sucursal_id=ticket.sucursal_id, referencias=f'TICKET-{ticket.correlativo}')
               .order_by('-id').first())
    if dte is None:
        return 'ticket sin documento'

    # El espejo se escribe con el código crudo (boletas) o con el display
    # (facturas): se acepta cualquiera de los dos.
    etiquetas = {metodo_anterior, metodo_anterior.replace('_', ' ')}
    display = dict(pago._meta.get_field('metodo_pago').choices or {}).get(metodo_anterior)
    if display:
        etiquetas.add(display)
    filas = [p for p in Dte_Detalle_Pago.objects.filter(dte=dte, monto=pago.monto)
             if (p.metodo_pago or '') in etiquetas]
    if not filas:
        return f'{dte.tipo_documento} {dte.numero_documento}: sin fila espejo por {_plata(pago.monto)}'
    fila = filas[0]
    fila.metodo_pago = metodo_nuevo
    fila.tipo_tarjeta = tipo_tarjeta
    fila.voucher = (voucher or fila.voucher or '')[:50]
    fila.notas = f'{(fila.notas or "").strip()} | {sello}'.strip(' |')
    fila.save(update_fields=['metodo_pago', 'tipo_tarjeta', 'voucher', 'notas'])
    return f'{dte.tipo_documento} {dte.numero_documento} actualizado'


def aplicar_asociacion(trx, pago, sello):
    """Escribe la asociación (sin validaciones de negocio: las hace el llamador).

    Devuelve (metodo_anterior, metodo_nuevo, espejo). Debe correr dentro de
    una transacción.
    """
    ticket = pago.ticket
    metodo_anterior = pago.metodo_pago
    metodo_nuevo = mp.metodo_pago_ticket_de(trx)
    tipo_tarjeta = trx.metodo_pago_mp or 'MERCADO PAGO'
    voucher = _n_operacion(trx) or trx.payment_id or pago.voucher

    pago.metodo_pago = metodo_nuevo
    pago.tipo_tarjeta = tipo_tarjeta
    pago.voucher = voucher
    pago.origen_pago = 'POS_INTEGRADO'
    pago.notas = f'{(pago.notas or "").strip()} | {sello}'.strip(' |')
    pago.save(update_fields=['metodo_pago', 'tipo_tarjeta', 'voucher', 'origen_pago', 'notas', 'actualizado_en'])

    espejo = _actualizar_espejo_dte(ticket, pago, metodo_anterior, metodo_nuevo, tipo_tarjeta, voucher, sello)

    trx.consumida = True
    trx.ticket = ticket
    trx.detalle_pago = pago
    trx.save(update_fields=['consumida', 'ticket', 'detalle_pago', 'actualizado_en'])
    return metodo_anterior, metodo_nuevo, espejo


def _recalcular_arqueo(ticket, usuario):
    from app.views_modulo_ventas import _recalcular_teoricos_arqueo
    arqueo = ArqueoCaja.objects.filter(sucursal_id=ticket.sucursal_id, fecha_arqueo=ticket.fecha).first()
    if arqueo is None:
        return 'sin arqueo ese día'
    resultado = _recalcular_teoricos_arqueo(
        arqueo, usuario=usuario, registrar_bitacora=True,
        razon='asociación de cobro Mercado Pago',
    )
    return 'arqueo recalculado' if resultado.get('hay_cambios') else 'arqueo sin cambios'


def _validar_pago(pago):
    if pago.ticket.estado != 'PAGADO':
        raise AsociacionError(f'El ticket #{pago.ticket.correlativo} no es una venta cerrada ({pago.ticket.estado}).')
    if not es_pago_asociable(pago):
        raise AsociacionError(
            f'El pago es {pago.get_metodo_pago_display()}: solo se asocian tarjetas manuales o «MP manual».')
    if TransaccionMercadoPago.objects.filter(detalle_pago=pago).exists():
        raise AsociacionError('Ese pago ya tiene un cobro de Mercado Pago asociado.')


def asociar(trx_id, pago_id, usuario, recalcular_arqueo=True):
    """Asocia un cobro MP sin venta al pago de un ticket (monto exacto)."""
    with transaction.atomic():
        trx = (TransaccionMercadoPago.objects.select_for_update()
               .select_related('sucursal', 'config').filter(id=trx_id).first())
        if trx is None:
            raise AsociacionError('El cobro de Mercado Pago no existe.')
        if trx.tipo != 'VENTA' or trx.estado != 'APROBADA':
            raise AsociacionError(f'El cobro está {trx.get_estado_display()}: solo se asocian cobros aprobados.')
        if trx.consumida:
            raise AsociacionError('Ese cobro ya está asociado a una venta.')
        pago = (TicketDetallePago.objects.select_for_update()
                .select_related('ticket', 'ticket__sucursal').filter(id=pago_id).first())
        if pago is None:
            raise AsociacionError('El pago del ticket no existe.')
        _validar_pago(pago)
        if int(pago.monto) != int(trx.monto):
            raise AsociacionError(
                f'Los montos no calzan: cobro {_plata(trx.monto)} vs pago {_plata(pago.monto)}. '
                'Solo se asocia con monto exacto.')

        sello = (f'Asociado {timezone.localtime():%d-%m-%Y %H:%M} por {usuario.username}: cobro Mercado Pago '
                 f'{trx.canal} N° {_n_operacion(trx) or trx.payment_id or "s/n"} (ref {trx.external_reference})')
        anterior, nuevo, espejo = aplicar_asociacion(trx, pago, sello)

    arqueo = _recalcular_arqueo(pago.ticket, usuario) if recalcular_arqueo else 'no se recalculó'
    logger.warning('MP: %s asoció el cobro %s (%s) al pago %s del ticket %s (%s -> %s)',
                   usuario.username, trx.id, _plata(trx.monto), pago.id, pago.ticket.correlativo, anterior, nuevo)
    return {
        'mensaje': (f'Cobro {_plata(trx.monto)} asociado al ticket #{pago.ticket.correlativo} '
                    f'({anterior} → {nuevo}). Documento: {espejo}. Arqueo: {arqueo}.'),
        'ticket_id': pago.ticket_id,
        'metodo_anterior': anterior,
        'metodo_nuevo': nuevo,
        'espejo': espejo,
        'arqueo': arqueo,
    }


def _obtener_pago_api(config, payment_id):
    resp = mp._request(config, 'GET', f'/v1/payments/{payment_id}')
    if resp.status_code == 404:
        raise AsociacionError(f'El N° {payment_id} no existe en la cuenta Mercado Pago de la caja {config.nombre}.')
    try:
        return mp._json_o_error(resp, f'payments/{payment_id}')
    except mp.MercadoPagoError as e:
        raise AsociacionError(e.mensaje)


def importar_y_asociar(payment_id, pago_id, config_id, usuario, recalcular_arqueo=True):
    """Trae de la API un pago que el sistema no tiene y lo asocia a un pago del ticket.

    Sirve para: cobros hechos en la máquina o la app de MP fuera del POS, y
    pagos «MP manual» cuyo N° digitado no calza (se corrige al N° real).
    """
    payment_id = str(payment_id or '').strip()
    if not payment_id.isdigit():
        raise AsociacionError('El N° de operación de Mercado Pago debe ser numérico.')
    config = MercadoPagoConfig.objects.select_related('sucursal').filter(id=config_id).first()
    if config is None:
        raise AsociacionError('Elige la caja / cuenta de Mercado Pago.')

    existente = TransaccionMercadoPago.objects.filter(Q(payment_id_mp=payment_id) | Q(payment_id=payment_id)).first()
    if existente is not None:
        if existente.consumida:
            raise AsociacionError(f'El N° {payment_id} ya está asociado a otra venta.')
        return asociar(existente.id, pago_id, usuario, recalcular_arqueo)

    payment = _obtener_pago_api(config, payment_id)
    if payment.get('status') != 'approved':
        raise AsociacionError(f'El pago {payment_id} está «{payment.get("status")}» en Mercado Pago, no aprobado.')
    monto = int(round(float(payment.get('transaction_amount') or 0)))

    with transaction.atomic():
        pago = (TicketDetallePago.objects.select_for_update()
                .select_related('ticket', 'ticket__sucursal').filter(id=pago_id).first())
        if pago is None:
            raise AsociacionError('El pago del ticket no existe.')
        _validar_pago(pago)
        if monto != int(pago.monto):
            raise AsociacionError(
                f'Los montos no calzan: Mercado Pago {_plata(monto)} vs pago {_plata(pago.monto)}.')
        detalles = payment.get('transaction_details') or {}
        neto = detalles.get('net_received_amount')
        fee = sum(float(f.get('amount') or 0) for f in (payment.get('fee_details') or []))
        tipo_pago = payment.get('payment_type_id') or ''
        liberacion = parse_datetime(payment.get('money_release_date') or '') if payment.get('money_release_date') else None
        trx = TransaccionMercadoPago.objects.create(
            config=config,
            sucursal=config.sucursal,
            ticket=pago.ticket,
            correlativo_ticket=str(pago.ticket.correlativo),
            tipo='VENTA',
            canal='POINT' if tipo_pago in ('debit_card', 'credit_card', 'prepaid_card') else 'QR',
            external_reference=f'ASOC-{payment_id}',
            payment_id=payment_id,
            payment_id_mp=payment_id,
            monto=monto,
            monto_neto=int(round(float(neto))) if neto is not None else None,
            fee_mp=int(round(fee)) if fee else None,
            installments=int(payment.get('installments') or 1),
            estado='APROBADA',
            estado_detalle=(payment.get('status_detail') or '')[:120],
            metodo_pago_mp=tipo_pago,
            ultimos_4_digitos=((payment.get('card') or {}).get('last_four_digits') or '')[:4],
            codigo_autorizacion=(payment.get('authorization_code') or '')[:30],
            money_release_date=liberacion,
            raw_response=payment,
            usuario=usuario,
        )
        sello = (f'Asociado {timezone.localtime():%d-%m-%Y %H:%M} por {usuario.username}: pago Mercado Pago '
                 f'N° {payment_id} traído desde la API (caja {config.nombre})')
        anterior, nuevo, espejo = aplicar_asociacion(trx, pago, sello)

    arqueo = _recalcular_arqueo(pago.ticket, usuario) if recalcular_arqueo else 'no se recalculó'
    logger.warning('MP: %s importó el pago %s (%s) y lo asoció al ticket %s (%s -> %s)',
                   usuario.username, payment_id, _plata(monto), pago.ticket.correlativo, anterior, nuevo)
    return {
        'mensaje': (f'Pago N° {payment_id} ({_plata(monto)}) traído de Mercado Pago y asociado al ticket '
                    f'#{pago.ticket.correlativo} ({anterior} → {nuevo}). Documento: {espejo}. Arqueo: {arqueo}.'),
        'ticket_id': pago.ticket_id,
        'transaccion_id': trx.id,
        'metodo_anterior': anterior,
        'metodo_nuevo': nuevo,
        'espejo': espejo,
        'arqueo': arqueo,
    }
