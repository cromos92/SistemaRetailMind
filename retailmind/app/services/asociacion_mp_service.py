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

Alcance: quien no es administrador solo asocia ventas y cobros de SU tienda
(`sucursal_permitida`); el servidor lo valida aunque se manipulen los ids.
"""
import datetime as _dt
import logging
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import (
    ArqueoCaja,
    Dte,
    Dte_Detalle_Pago,
    MercadoPagoConfig,
    Ticket,
    Ticket_Productos,
    TicketDetallePago,
    TransaccionMercadoPago,
    TransaccionPOS,
)
from app.services import conciliacion_mp_service as conc
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')

CODIGO_PERMISO = 'asociar_pagos_mercadopago'

# Métodos con los que un cajero registra una tarjeta "a mano" (Transbank o
# genérico). Son los que se pueden convertir a Mercado Pago, salvo que sean un
# cobro Transbank REAL hecho por el POS integrado (esa plata está en Transbank).
METODOS_TARJETA_MANUAL = (
    'TBK_CREDITO_POS', 'TBK_DEBITO_POS', 'TBK_PREPAGO_POS',
    'TBK_MANUAL', 'TBK_POS_INTEGRADO',
    'TARJETA_CREDITO', 'TARJETA_DEBITO',
)
ORIGENES_INTEGRADOS = ('POS_INTEGRADO', 'POS_WEB')
METODOS_MP = ('MP_QR', 'MP_POINT', 'MP_POINT_DEBITO', 'MP_POINT_CREDITO')
# Venta cobrada con la Point en modo manual pero anotada en efectivo o
# transferencia: solo un ADMINISTRADOR la pasa a Mercado Pago, confirmándolo
# (cambia el efectivo teórico del arqueo de ese día).
METODOS_OTROS_CONVERTIBLES = ('EFECTIVO', 'TRANSFERENCIA')
# Tope de pagos en «Asignar todas las sugeridas».
MAX_LOTE = 60

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


def _tbk_integrado(pago):
    """Cobro Transbank REAL del POS integrado (su plata está en Transbank):
    convertirlo a Mercado Pago descuadraría el cierre de Transbank."""
    if pago.metodo_pago not in METODOS_TARJETA_MANUAL:
        return False
    if pago.origen_pago:
        return pago.origen_pago in ORIGENES_INTEGRADOS
    anotado = getattr(pago, 'tbk_real', None)   # anotado por los listados (evita una consulta)
    if anotado is not None:
        return bool(anotado)
    return TransaccionPOS.objects.filter(detalle_pago_id=pago.pk).exists()


def motivo_no_asociable(pago, permitir_otros_medios=False):
    """'' si el pago de la venta se puede pasar a Mercado Pago; si no, por qué."""
    if pago.metodo_pago in METODOS_TARJETA_MANUAL:
        return 'es un cobro Transbank real del POS integrado' if _tbk_integrado(pago) else ''
    if pago.metodo_pago in METODOS_MP:
        return '' if pago.origen_pago == 'MANUAL' else 'ya es un cobro Mercado Pago del POS'
    if pago.metodo_pago in METODOS_OTROS_CONVERTIBLES:
        return '' if permitir_otros_medios else (
            f'está anotado en {pago.get_metodo_pago_display()}: solo un administrador puede pasarlo a Mercado Pago')
    return f'está anotado en {pago.get_metodo_pago_display()}'


def es_pago_asociable(pago, permitir_otros_medios=False):
    """Tarjeta manual (no Transbank integrado real) o MP digitado a mano; con
    `permitir_otros_medios`, también efectivo o transferencia."""
    return not motivo_no_asociable(pago, permitir_otros_medios)


def _pagos_posibles():
    """TicketDetallePago de ventas cerradas, sin cobro MP, que podrían ser un
    cobro de Mercado Pago: tarjeta manual (sin los Transbank integrados reales),
    «MP manual», y efectivo / transferencia (que solo un administrador convierte)."""
    return (TicketDetallePago.objects
            .filter(ticket__estado='PAGADO')
            .filter(Q(metodo_pago__in=METODOS_TARJETA_MANUAL)
                    | Q(metodo_pago__in=METODOS_MP, origen_pago='MANUAL')
                    | Q(metodo_pago__in=METODOS_OTROS_CONVERTIBLES))
            .filter(_sin_transaccion())
            .annotate(tbk_real=Exists(TransaccionPOS.objects.filter(detalle_pago_id=OuterRef('pk'))))
            .exclude(Q(metodo_pago__in=METODOS_TARJETA_MANUAL)
                     & (Q(origen_pago__in=ORIGENES_INTEGRADOS) | Q(origen_pago__isnull=True, tbk_real=True)))
            .select_related('ticket', 'ticket__sucursal', 'ticket__vendedor'))


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


def _productos_por_ticket(ticket_ids, maximo=2):
    """{ticket_id: 'Zapatilla X T40, Polera Y +2'} para reconocer la venta (una consulta)."""
    salida = defaultdict(list)
    for tp in (Ticket_Productos.objects.filter(idTicket_id__in=list(ticket_ids))
               .select_related('ProductoTalla__producto').order_by('id')):
        pt = tp.ProductoTalla
        salida[tp.idTicket_id].append(f'{pt.producto.articulo} T{pt.talla}' if pt else 'Ítem manual')
    return {tid: ', '.join(items[:maximo]) + (f' +{len(items) - maximo}' if len(items) > maximo else '')
            for tid, items in salida.items()}


def _fila_pago(pago, dtes=None, productos=None):
    ticket = pago.ticket
    creado = timezone.localtime(ticket.created_at) if ticket.created_at else None
    documento = ''
    if dtes is not None:
        documento, _dte_id = conc._documento_de_ticket(ticket, dtes)
    vendedor = getattr(ticket, 'vendedor', None) if ticket.vendedor_id else None
    return {
        'id': pago.id,
        'ticket_id': ticket.id,
        'correlativo': ticket.correlativo,
        'folio': ticket.folio_dte or '',
        'tipo_dte': ticket.tipo_dte or '',
        # Documento real (boleta/factura por la referencia TICKET-<corr>)
        'documento': documento,
        'fecha': ticket.fecha.strftime('%d/%m/%Y') if ticket.fecha else '',
        'hora': creado.strftime('%H:%M') if creado else '',
        # Cuándo se registró el pago en el POS (lo que se compara con la hora del cobro)
        'hora_pago': timezone.localtime(pago.creado_en).strftime('%H:%M') if pago.creado_en else '',
        'sucursal_id': ticket.sucursal_id,
        'sucursal': ticket.sucursal.alias if ticket.sucursal_id else '',
        'cliente': ticket.cliente_nombre or '',
        'vendedor': getattr(vendedor, 'nombre', '') or '',
        'productos': (productos or {}).get(ticket.id, ''),
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
                     correlativo='', n_operaciones=(), dias=1, permitir_otros_medios=False):
    """Pagos de ventas cerradas que podrían ser un cobro de `monto` hecho en
    `momento` (datetime con tz), del mismo monto y sin transacción.

    Trae también las ventas anotadas en efectivo / transferencia, marcadas
    `asociable=False` con su `motivo` salvo `permitir_otros_medios` (así quien
    busca sabe que la venta existe aunque no la pueda convertir). Orden: las
    asociables primero; luego mismo N°, N° parecido, mismo ticket y cercanía
    entre la hora del cobro y la hora en que se registró el pago en el POS.
    `buscar`: N° de ticket, folio de la boleta/factura o N° digitado."""
    dia = timezone.localtime(momento).date()
    qs = _pagos_posibles().filter(monto=int(monto))
    buscar = (buscar or '').strip()
    if buscar:
        filtro = Q(ticket__folio_dte__icontains=buscar) | Q(voucher__icontains=buscar)
        if buscar.isdigit():
            filtro |= Q(ticket__correlativo=int(buscar))
            # N° de la boleta / factura emitida (Dte con referencia TICKET-<corr>)
            refs = [str(r) for r in Dte.objects.filter(numero_documento=int(buscar), referencias__startswith='TICKET-')
                    .values_list('referencias', flat=True)[:20]]
            corrs = [int(r.split('-', 1)[1]) for r in refs if r.split('-', 1)[1].isdigit()]
            if corrs:
                filtro |= Q(ticket__correlativo__in=corrs)
        qs = qs.filter(filtro)
    else:
        qs = qs.filter(ticket__fecha__range=(dia - _dt.timedelta(days=dias), dia + _dt.timedelta(days=dias)))
    if sucursal_id and not todas_sucursales:
        qs = qs.filter(ticket__sucursal_id=sucursal_id)

    # Tope por separado: las ventas en efectivo / transferencia (muchas) no
    # pueden dejar fuera a las tarjetas y «MP manual», que son las probables.
    pagos = (list(qs.exclude(metodo_pago__in=METODOS_OTROS_CONVERTIBLES)[:200])
             + list(qs.filter(metodo_pago__in=METODOS_OTROS_CONVERTIBLES)[:60]))
    dtes = conc._dtes_por_ref([p.ticket for p in pagos])
    productos = _productos_por_ticket({p.ticket_id for p in pagos})
    n_operaciones = {conc._solo_digitos(n) for n in n_operaciones if n} - {''}
    filas = []
    for pago in pagos:
        fila = _fila_pago(pago, dtes, productos)
        fila['motivo'] = motivo_no_asociable(pago, permitir_otros_medios)
        fila['asociable'] = not fila['motivo']
        fila['otro_medio'] = pago.metodo_pago in METODOS_OTROS_CONVERTIBLES
        voucher = conc._solo_digitos(pago.voucher)
        fila['mismo_correlativo'] = bool(correlativo) and str(correlativo) == str(pago.ticket.correlativo)
        fila['mismo_voucher'] = bool(voucher) and voucher in n_operaciones
        fila['voucher_parecido'] = next((t for t in (conc.n_parecido(voucher, n) for n in n_operaciones) if t), '')
        # Minutos entre el cobro y el registro del pago en el POS (no la apertura
        # del ticket, que puede ser de horas antes si quedó pendiente).
        referencia = pago.creado_en or pago.ticket.created_at or momento
        fila['minutos'] = int(abs((referencia - momento).total_seconds()) // 60)
        filas.append(fila)
    filas.sort(key=lambda f: (not f['asociable'], not f['mismo_voucher'], not f['voucher_parecido'],
                              not f['mismo_correlativo'], f['minutos']))
    return filas[:MAX_CANDIDATOS]


def candidatos_para_cobro(trx, buscar='', todas_sucursales=False, dias=1, permitir_otros_medios=False):
    """Pagos de ventas cerradas que podrían ser ESTE cobro (mismo monto)."""
    return candidatos_pagos(
        trx.monto, trx.creado_en, trx.sucursal_id, buscar, todas_sucursales,
        correlativo=trx.correlativo_ticket, n_operaciones=(trx.payment_id_mp, trx.payment_id), dias=dias,
        permitir_otros_medios=permitir_otros_medios,
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
    # Un cobro del POS que todavía no tiene su N° se reconoce por la referencia.
    referencias = {str(p.get('external_reference') or '') for p in filas} - {''}
    refs_locales = set(TransaccionMercadoPago.objects.filter(external_reference__in=referencias)
                       .exclude(estado='CREADA').values_list('external_reference', flat=True))
    resultado = []
    for p in filas:
        pid = str(p.get('id'))
        if str(p.get('external_reference') or '') in refs_locales:
            registrados.add(pid)
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
            'mismo_voucher': pid == conc._solo_digitos(pago.voucher),
            # «falta un 8»: el N° digitado en el POS es este con un error de tipeo
            'parecido': conc.n_parecido(pago.voucher, pid),
        })
    resultado.sort(key=lambda f: (f['ya_registrado'], not f['mismo_voucher'], not f['parecido']))
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


ESTADOS_ARQUEO_CERRADOS_POR_SUPERVISOR = ('DEPOSITO_DECLARADO', 'DEPOSITO_CONFIRMADO', 'REVISADO')


def _recalcular_arqueo(ticket, usuario):
    """Rehace los teóricos del arqueo del día de la venta (queda en su bitácora).
    Si falla, la asociación ya quedó grabada: se avisa en vez de responder 500."""
    from app.views_modulo_ventas import _recalcular_teoricos_arqueo
    arqueo = ArqueoCaja.objects.filter(sucursal_id=ticket.sucursal_id, fecha_arqueo=ticket.fecha).first()
    if arqueo is None:
        return 'sin arqueo ese día'
    try:
        resultado = _recalcular_teoricos_arqueo(
            arqueo, usuario=usuario, registrar_bitacora=True,
            razon='asociación de cobro Mercado Pago',
        )
    except Exception:  # noqa: BLE001 — la asociación ya está hecha; el arqueo se rehace a mano
        logger.exception('MP: no se pudo recalcular el arqueo %s tras asociar', arqueo.id)
        return 'no se pudo recalcular el arqueo: recalcúlelo desde la Cuadratura'
    texto = 'arqueo recalculado' if resultado.get('hay_cambios') else 'arqueo sin cambios'
    if resultado.get('hay_cambios') and arqueo.estado in ESTADOS_ARQUEO_CERRADOS_POR_SUPERVISOR:
        texto += f' (ya estaba «{arqueo.get_estado_display()}»: avise al supervisor)'
    return texto


def _validar_pago(pago, permitir_otros_medios=False, sucursal_permitida=None):
    if sucursal_permitida is not None and pago.ticket.sucursal_id != sucursal_permitida:
        raise AsociacionError('Esa venta es de otra tienda: solo puede asignar pagos de su tienda.')
    if pago.ticket.estado != 'PAGADO':
        raise AsociacionError(f'El ticket #{pago.ticket.correlativo} no es una venta cerrada ({pago.ticket.estado}).')
    motivo = motivo_no_asociable(pago, permitir_otros_medios)
    if motivo:
        raise AsociacionError(f'El pago del ticket #{pago.ticket.correlativo} {motivo}.')
    if TransaccionMercadoPago.objects.filter(detalle_pago=pago).exists():
        raise AsociacionError('Ese pago ya tiene un cobro de Mercado Pago asociado.')


def asociar(trx_id, pago_id, usuario, recalcular_arqueo=True, sucursal_permitida=None,
            permitir_otros_medios=False):
    """Asocia un cobro MP sin venta al pago de un ticket (monto exacto)."""
    with transaction.atomic():
        trx = (TransaccionMercadoPago.objects.select_for_update()
               .select_related('sucursal', 'config').filter(id=trx_id).first())
        if trx is None:
            raise AsociacionError('El cobro de Mercado Pago no existe.')
        if sucursal_permitida is not None and trx.sucursal_id != sucursal_permitida:
            raise AsociacionError('Ese cobro es de otra tienda: solo puede asignar pagos de su tienda.')
        if trx.tipo != 'VENTA' or trx.estado != 'APROBADA':
            raise AsociacionError(f'El cobro está {trx.get_estado_display()}: solo se asocian cobros aprobados.')
        if trx.consumida:
            raise AsociacionError('Ese cobro ya está asociado a una venta.')
        pago = (TicketDetallePago.objects.select_for_update()
                .select_related('ticket', 'ticket__sucursal').filter(id=pago_id).first())
        if pago is None:
            raise AsociacionError('El pago del ticket no existe.')
        _validar_pago(pago, permitir_otros_medios, sucursal_permitida)
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


def _misma_cuenta(a, b):
    """¿Las dos cajas cobran con la misma cuenta de Mercado Pago? Misma regla que
    el cobro (la cuenta elegida en la caja o la de la empresa de la sucursal)."""
    if a.id == b.id:
        return True
    ca, cb = conc._cuenta_efectiva(a), conc._cuenta_efectiva(b)
    if ca is not None or cb is not None:
        return ca is not None and cb is not None and ca.id == cb.id
    try:   # entornos sin cuenta cargada: token por variable de entorno
        return mp._token(a) == mp._token(b)
    except mp.MercadoPagoError:
        return False


def _cajas_de_la_tienda(config, sucursal_id):
    """Cajas de la tienda `sucursal_id` que cobran con la misma cuenta MP que `config`."""
    return [c for c in (MercadoPagoConfig.objects.filter(sucursal_id=sucursal_id)
                        .select_related('sucursal', 'cuenta').order_by('-habilitado', '-es_principal', 'id'))
            if _misma_cuenta(c, config)]


def _caja_de_la_tienda(config, sucursal_id, payment):
    """Caja de la tienda de la venta en la MISMA cuenta MP que `config` (la cuenta
    por la que se leyó el pago), o None si esa tienda cobra con otra cuenta.

    Si `config` ya es de esa tienda (la caja que «Contra Mercado Pago» atribuyó
    al pago), se respeta. Si no, la del punto de venta que informa MP o la
    principal: antes quedaba en «la primera caja de la cuenta» (un pago de PAO4
    terminaba sumando en el cierre de PAO1)."""
    if config.sucursal_id == sucursal_id:
        return config
    cajas = _cajas_de_la_tienda(config, sucursal_id)
    if not cajas:
        return None
    caja_id = conc._caja_del_pago(payment, cajas)
    return next((c for c in cajas if c.id == caja_id), cajas[0])


def _pago_es_de_la_tienda(caja, payment, cache_dias=None):
    """¿El pago se cobró en la tienda de `caja`? Para quien no es administrador:
    no puede quedarse con un cobro de OTRA tienda de la misma cuenta.

    Sí si: la referencia propia es de esa tienda; o la cuenta la usa solo esa
    tienda; o el cierre de ese día (mismos datos que «Contra Mercado Pago»)
    atribuye el pago a una caja de esa tienda. `cache_dias`: {(token, día): pagos}
    para no releer el mismo día en un lote."""
    pid = str(payment.get('id') or '')
    suc_ref = mp._sucursal_de_referencia(str(payment.get('external_reference') or ''))
    if suc_ref is not None:
        return suc_ref == caja.sucursal_id
    cuenta = MercadoPagoConfig.objects.filter(id__in=conc._configs_de_la_cuenta(caja)).values_list('sucursal_id', flat=True)
    if set(cuenta) <= {caja.sucursal_id}:
        return True
    instante = conc._instante(payment.get('date_created'))
    if instante is None:
        return False
    dia = timezone.localtime(instante).date()
    clave = (caja.id, dia)
    cache_dias = cache_dias if cache_dias is not None else {}
    if clave not in cache_dias:
        cache_dias[clave] = mp.buscar_pagos_dia(caja, dia)
    for c in _cajas_de_la_tienda(caja, caja.sucursal_id):
        res = mp.conciliar_cierre_mp(c, dia, pagos=cache_dias[clave])
        if any(str(x.get('payment_id')) == pid and x.get('atribuible') for x in res.get('sin_registro') or []):
            return True
    return False


def _proteger_n_valido(pago, payment_id, config):
    """Un «MP manual» cuyo N° digitado YA es un pago aprobado de MP por el mismo
    monto tiene su cobro (solo falta registrarlo al aplicar liberaciones): no se
    le puede poner otro N° encima (la revisión del 25-09 lo reprodujo con el lote)."""
    if pago.metodo_pago not in METODOS_MP:
        return
    voucher = conc._solo_digitos(pago.voucher)
    if len(voucher) < 9 or voucher == payment_id:
        return
    try:
        propio = _obtener_pago_api(config, voucher)
    except AsociacionError:
        return          # el N° digitado no existe en la cuenta: se puede corregir
    if propio.get('status') == 'approved' and conc._monto(propio.get('transaction_amount')) == int(pago.monto or 0):
        raise AsociacionError(
            f'La venta #{pago.ticket.correlativo} ya tiene su N° de operación {voucher}, que es un pago válido de '
            f'Mercado Pago por el mismo monto: no se le puede asignar otro cobro.')


def importar_y_asociar(payment_id, pago_id, config_id, usuario, recalcular_arqueo=True,
                       sucursal_permitida=None, permitir_otros_medios=False, cache_dias=None):
    """Trae de la API un pago que el sistema no tiene y lo asocia a un pago del ticket.

    Sirve para: cobros hechos en la máquina en modo manual o en la app de MP
    fuera del POS, y pagos «MP manual» cuyo N° digitado no calza (se corrige al
    N° real). La transacción queda con la fecha REAL del cobro, en la caja de la
    tienda de la venta y amarrada al retiro que ya se llevó ese pago: así el
    «Cierre por caja y día» de ese día cuadra (antes nacía con la fecha de hoy).
    `config_id`: una caja de la cuenta MP del pago (se usa para leerlo).
    Antes de preguntarle nada a MP se valida la venta, la tienda y la cuenta.
    """
    payment_id = str(payment_id or '').strip()
    if not payment_id.isdigit():
        raise AsociacionError('El N° de operación de Mercado Pago debe ser numérico.')
    config = MercadoPagoConfig.objects.select_related('sucursal', 'cuenta').filter(id=config_id).first()
    if config is None:
        raise AsociacionError('Elige la caja / cuenta de Mercado Pago.')
    pago = TicketDetallePago.objects.select_related('ticket', 'ticket__sucursal').filter(id=pago_id).first()
    if pago is None:
        raise AsociacionError('El pago del ticket no existe.')
    _validar_pago(pago, permitir_otros_medios, sucursal_permitida)
    if not _cajas_de_la_tienda(config, pago.ticket.sucursal_id):
        raise AsociacionError(
            f'La caja elegida es de otra cuenta de Mercado Pago que la de la tienda {pago.ticket.sucursal.alias}.')

    existente = TransaccionMercadoPago.objects.filter(Q(payment_id_mp=payment_id) | Q(payment_id=payment_id)).first()
    if existente is not None:
        if existente.consumida:
            raise AsociacionError(f'El N° {payment_id} ya está asociado a otra venta.')
        return asociar(existente.id, pago_id, usuario, recalcular_arqueo,
                       sucursal_permitida=sucursal_permitida, permitir_otros_medios=permitir_otros_medios)

    payment = _obtener_pago_api(config, payment_id)
    if payment.get('status') != 'approved':
        raise AsociacionError(f'El pago {payment_id} está «{payment.get("status")}» en Mercado Pago, no aprobado.')
    if conc._monto(payment.get('transaction_amount_refunded') or 0) > 0 or payment.get('refunds'):
        raise AsociacionError(f'El pago {payment_id} tiene devoluciones en Mercado Pago: revíselo en el panel antes de asignarlo.')
    # Cobro del POS que el sistema ya tiene (aún sin su N°): se asocia ESE cobro,
    # no se registra otro (el cierre lo contaría dos veces).
    referencia = str(payment.get('external_reference') or '').strip()
    if referencia:
        local = (TransaccionMercadoPago.objects.filter(external_reference=referencia)
                 .exclude(estado='CREADA').first())
        if local is not None:
            if local.tipo == 'VENTA' and local.estado == 'APROBADA' and not local.consumida:
                return asociar(local.id, pago_id, usuario, recalcular_arqueo,
                               sucursal_permitida=sucursal_permitida, permitir_otros_medios=permitir_otros_medios)
            raise AsociacionError(f'El pago {payment_id} ya está registrado en el sistema como el cobro {referencia}.')
    monto = conc._monto(payment.get('transaction_amount'))
    if monto != int(pago.monto):
        raise AsociacionError(f'Los montos no calzan: Mercado Pago {_plata(monto)} vs pago {_plata(pago.monto)}.')
    caja = _caja_de_la_tienda(config, pago.ticket.sucursal_id, payment)
    if sucursal_permitida is not None and not _pago_es_de_la_tienda(caja, payment, cache_dias):
        raise AsociacionError(
            f'Mercado Pago no muestra que el pago {payment_id} se haya cobrado en {pago.ticket.sucursal.alias}: '
            'pídale a un administrador que lo asigne.')
    _proteger_n_valido(pago, payment_id, config)
    # Un retiro que ya se llevó este pago (lo contó por su N°) queda amarrado.
    retiro = conc._retiros_por_operacion(conc._configs_de_la_cuenta(caja)).get(payment_id)
    neto = (payment.get('transaction_details') or {}).get('net_received_amount')
    neto = conc._monto(neto) if neto is not None else None
    tipo_pago = payment.get('payment_type_id') or ''

    try:
        with transaction.atomic():
            pago = (TicketDetallePago.objects.select_for_update()
                    .select_related('ticket', 'ticket__sucursal').filter(id=pago_id).first())
            if pago is None:
                raise AsociacionError('El pago del ticket no existe.')
            _validar_pago(pago, permitir_otros_medios, sucursal_permitida)
            if monto != int(pago.monto):
                raise AsociacionError(
                    f'Los montos no calzan: Mercado Pago {_plata(monto)} vs pago {_plata(pago.monto)}.')
            ya = Q(payment_id_mp=payment_id) | Q(payment_id=payment_id) | Q(
                external_reference__in=(f'MANUAL-{payment_id}', f'ASOC-{payment_id}'))
            if TransaccionMercadoPago.objects.filter(ya).exists():
                raise AsociacionError(f'El N° {payment_id} lo acaba de registrar otra persona: actualice la pantalla.')
            trx = TransaccionMercadoPago.objects.create(
                config=caja,
                sucursal_id=pago.ticket.sucursal_id,
                ticket=pago.ticket,
                correlativo_ticket=str(pago.ticket.correlativo),
                tipo='VENTA',
                canal='POINT' if tipo_pago in ('debit_card', 'credit_card', 'prepaid_card') else 'QR',
                external_reference=f'ASOC-{payment_id}',
                payment_id=payment_id,
                payment_id_mp=payment_id,
                monto=monto,
                monto_neto=neto,
                # Comisión = lo que MP no le entrega al comercio (como en el resto del sistema)
                fee_mp=(monto - neto) if neto is not None else None,
                installments=int(payment.get('installments') or 1),
                estado='APROBADA',
                estado_detalle=(payment.get('status_detail') or '')[:120],
                metodo_pago_mp=tipo_pago,
                ultimos_4_digitos=((payment.get('card') or {}).get('last_four_digits') or '')[:4],
                codigo_autorizacion=(payment.get('authorization_code') or '')[:30],
                money_release_date=conc._instante(payment.get('money_release_date')),
                raw_response=payment,
                usuario=usuario,
                retiro=retiro,
            )
            # Fecha del cobro real (creado_en es auto_now_add: quedaría con la de hoy
            # y el cierre de caja de ese día seguiría descuadrado).
            TransaccionMercadoPago.objects.filter(pk=trx.pk).update(
                creado_en=conc._instante(payment.get('date_created')) or pago.creado_en)
            sello = (f'Asociado {timezone.localtime():%d-%m-%Y %H:%M} por {usuario.username}: pago Mercado Pago '
                     f'N° {payment_id} traído desde la API (caja {caja.sucursal.alias} · {caja.nombre})')
            anterior, nuevo, espejo = aplicar_asociacion(trx, pago, sello)
    except IntegrityError:
        raise AsociacionError(f'El N° {payment_id} lo acaba de registrar otra persona: actualice la pantalla.')

    arqueo = _recalcular_arqueo(pago.ticket, usuario) if recalcular_arqueo else 'no se recalculó'
    logger.warning('MP: %s importó el pago %s (%s) y lo asoció al ticket %s de %s (%s -> %s)',
                   usuario.username, payment_id, _plata(monto), pago.ticket.correlativo,
                   pago.ticket.sucursal.alias, anterior, nuevo)
    return {
        'mensaje': (f'Pago N° {payment_id} ({_plata(monto)}) asignado al ticket #{pago.ticket.correlativo} '
                    f'de {pago.ticket.sucursal.alias} ({anterior} → {nuevo}). Documento: {espejo}. Arqueo: {arqueo}.'),
        'ticket_id': pago.ticket_id,
        'transaccion_id': trx.id,
        'metodo_anterior': anterior,
        'metodo_nuevo': nuevo,
        'espejo': espejo,
        'arqueo': arqueo,
    }


# ---------------------------------------------------------------------------
# «Contra Mercado Pago»: venta sugerida y asignación por lote
# ---------------------------------------------------------------------------

# Dos cobros del mismo monto en la misma tienda y día se emparejan con sus
# ventas en orden de hora solo si cada par queda a menos de esto.
MINUTOS_PAR_ORDENADO = 45


def _confianza(minutos):
    return 'alta' if minutos <= 10 else ('media' if minutos <= 60 else 'baja')


def sugerir_ventas(filas, sucursal_permitida=None, permitir_otros_medios=False, vouchers_calzados=()):
    """Para cada pago de MP sin registro (filas de `diferencias_contra_mp`, ya
    atribuidas a su tienda), la venta que lo explica si hay UNA razonable.

    Candidatas: ventas cerradas de ESA tienda, del mismo día y monto EXACTO,
    anotadas como tarjeta manual o «MP manual» (nunca Transbank integrado real),
    sin cobro MP. Quedan fuera: un «MP manual» cuyo N° ya es un pago real de MP
    (`vouchers_calzados`: solo falta registrarlo) y las ventas que ya tienen su
    propio cobro local sin consumir (se asocian a ese cobro, no a otro).
    Primero se empareja por N° (igual o con un error de tipeo); lo que queda, si
    hay tantas ventas como cobros de ese monto, en orden de hora. Si no, no se
    sugiere nada y el usuario elige en «Buscar venta».

    Escribe en cada fila `sugerencia` (dict o None), `candidatos_n` y
    `otros_medios_n` (ventas del mismo monto anotadas en efectivo/transferencia).
    """
    calzados = {conc._solo_digitos(v) for v in vouchers_calzados} - {''}
    grupos = defaultdict(list)          # (sucursal_id, día, monto) -> [(instante, fila)]
    for f in filas:
        f['sugerencia'], f['candidatos_n'], f['otros_medios_n'] = None, 0, 0
        instante = conc._instante(f.get('instante'))
        if not f.get('sucursal_id') or instante is None:
            continue
        if sucursal_permitida is not None and f['sucursal_id'] != sucursal_permitida:
            continue
        grupos[(f['sucursal_id'], timezone.localtime(instante).date(), int(f['monto']))].append((instante, f))
    montos_por_dia = defaultdict(set)
    for suc, dia, monto in grupos:
        montos_por_dia[(suc, dia)].add(monto)

    for (suc, dia), montos in montos_por_dia.items():
        inicio, fin = _rango_local(dia, dia)
        pagos = list(_pagos_posibles().filter(ticket__sucursal_id=suc, monto__in=montos,
                                              creado_en__range=(inicio, fin)).order_by('creado_en'))
        # Cobros locales sin venta de esa tienda y día: la venta con su propio cobro
        # se asocia a ESE cobro; y si hay cobros sueltos del mismo monto, el
        # emparejamiento deja de ser uno a uno.
        sueltos = list(cobros_sin_venta(suc, dia, dia).filter(monto__in=montos)
                       .values_list('monto', 'correlativo_ticket', 'ticket_id'))
        con_cobro_propio = {str(c) for _m, c, _t in sueltos if c} | {str(t) for _m, _c, t in sueltos if t}
        dtes = conc._dtes_por_ref([p.ticket for p in pagos])
        productos = _productos_por_ticket({p.ticket_id for p in pagos})
        for monto in montos:
            cobros = sorted(grupos[(suc, dia, monto)], key=lambda x: x[0])
            del_monto = [p for p in pagos if int(p.monto) == monto]
            ventas = [p for p in del_monto
                      if p.metodo_pago not in METODOS_OTROS_CONVERTIBLES
                      and not motivo_no_asociable(p, permitir_otros_medios)
                      and not (p.metodo_pago in METODOS_MP and conc._solo_digitos(p.voucher) in calzados)
                      and str(p.ticket.correlativo) not in con_cobro_propio and str(p.ticket_id) not in con_cobro_propio]
            otros = sum(1 for p in del_monto if p.metodo_pago in METODOS_OTROS_CONVERTIBLES)
            for _inst, f in cobros:
                f['candidatos_n'], f['otros_medios_n'] = len(ventas), otros
            pares = []
            # 1) Por N°: el digitado en el POS es el del cobro o casi (error de tipeo).
            libres_c, libres_v = list(cobros), list(ventas)
            for inst, f in list(libres_c):
                por_n = [p for p in libres_v if p.voucher and (
                    conc._solo_digitos(p.voucher) == f['payment_id'] or conc.n_parecido(p.voucher, f['payment_id']))]
                if len(por_n) == 1:
                    pares.append(((inst, f), por_n[0]))
                    libres_c.remove((inst, f))
                    libres_v.remove(por_n[0])
            # 2) El resto en orden de hora, solo si calzan uno a uno y sin cobros sueltos del mismo monto.
            sueltos_monto = sum(1 for m, _c, _t in sueltos if int(m) == monto)
            if libres_v and len(libres_v) == len(libres_c) and not sueltos_monto:
                orden = list(zip(libres_c, libres_v))
                if len(orden) == 1 or all(abs((p.creado_en - inst).total_seconds()) <= MINUTOS_PAR_ORDENADO * 60
                                          for (inst, _f), p in orden):
                    pares.extend(orden)
            for (inst, f), p in pares:
                minutos = int(abs((p.creado_en - inst).total_seconds()) // 60)
                fila = _fila_pago(p, dtes, productos)
                f['sugerencia'] = {
                    'pago_id': p.id, 'ticket': fila['correlativo'], 'documento': fila['documento'],
                    'hora_pago': fila['hora_pago'], 'metodo': fila['metodo_display'],
                    'vendedor': fila['vendedor'], 'productos': fila['productos'], 'voucher': fila['voucher'],
                    'minutos': minutos, 'confianza': _confianza(minutos),
                    'parecido': conc.n_parecido(p.voucher, f['payment_id']) if p.metodo_pago in METODOS_MP else '',
                }
    return filas


def asociar_lote(items, usuario, sucursal_permitida=None):
    """«Asignar todas las sugeridas»: cada item {payment_id, pago_id, config_id}
    se importa y asocia por separado (uno que falla no frena a los demás) y al
    final se recalcula UNA vez el arqueo de cada tienda y día tocados, aunque
    algo falle a mitad. Nunca convierte efectivo/transferencia (eso va de a uno
    y con confirmación)."""
    resultados, ticket_ids, arqueos = [], [], []
    cache_dias = {}
    try:
        for item in list(items or [])[:MAX_LOTE]:
            pid = ''
            try:
                pid = str(item.get('payment_id') or '')
                r = importar_y_asociar(pid, int(item.get('pago_id') or 0), int(item.get('config_id') or 0),
                                       usuario, recalcular_arqueo=False, sucursal_permitida=sucursal_permitida,
                                       cache_dias=cache_dias)
            except AsociacionError as e:
                resultados.append({'payment_id': pid, 'ok': False, 'error': str(e)})
                continue
            except mp.MercadoPagoError as e:
                resultados.append({'payment_id': pid, 'ok': False, 'error': e.mensaje})
                continue
            except Exception:  # noqa: BLE001 — un ítem roto no deja a medias a los demás
                logger.exception('MP: lote de asignación, ítem %s', pid)
                resultados.append({'payment_id': pid, 'ok': False, 'error': 'Error inesperado (quedó en el log).'})
                continue
            ticket_ids.append(r['ticket_id'])
            resultados.append({'payment_id': pid, 'ok': True, 'ticket_id': r['ticket_id'],
                               'mensaje': r['mensaje'].split(' Arqueo:')[0]})
    finally:
        vistos = set()
        for ticket in Ticket.objects.filter(id__in=ticket_ids).select_related('sucursal'):
            clave = (ticket.sucursal_id, ticket.fecha)
            if clave in vistos:
                continue
            vistos.add(clave)
            arqueos.append(f'{ticket.sucursal.alias} {ticket.fecha:%d-%m}: {_recalcular_arqueo(ticket, usuario)}')
    return {'resultados': resultados, 'asignados': sum(1 for r in resultados if r['ok']),
            'fallidos': sum(1 for r in resultados if not r['ok']), 'arqueos': arqueos,
            'recortado': len(list(items or [])) > MAX_LOTE}
