"""Conciliación Mercado Pago: cobros ↔ documentos ↔ liberaciones ↔ banco.

Cuatro preguntas, cuatro funciones:

1. ``cobros_vs_documentos``: cada cobro MP aprobado, ¿terminó en una venta con
   boleta/factura? Solo datos locales (no llama a MP): sirve para el arqueo.
2. ``procesar_reporte_liberaciones``: el reporte de Liberaciones de MP dice qué
   pagos componen cada retiro al banco. Crea ``RetiroMercadoPago`` y amarra
   cada ``TransaccionMercadoPago`` a su retiro.
3. ``conciliar_cartola``: marca "visto en cartola" los retiros cuyo abono
   aparece en el extracto del banco (mismo monto, fecha ±N días).
4. ``diferencias_contra_mp``: cruce contra la API (payments/search) para ver
   cobros que MP tiene y el sistema no, y el cierre por caja/día.

Todo es READ-ONLY salvo (2) y (3) con ``aplicar=True``.
"""
import csv
import io
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from app.models import (
    ArqueoCaja,
    Dte,
    MercadoPagoConfig,
    RetiroMercadoPago,
    Ticket,
    TicketDetallePago,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')

METODOS_MP = ('MP_QR', 'MP_POINT', 'MP_POINT_DEBITO', 'MP_POINT_CREDITO')
TIPOS_DTE_REINTENTABLES = ('BOLETA_ELECTRONICA', 'BOLETA_PAPEL', 'FACTURA_ELECTRONICA')

# Categorías de cobros_vs_documentos (orden = prioridad de revisión).
CAT_SIN_VENTA = 'SIN_VENTA'
CAT_TICKET_ANULADO = 'TICKET_ANULADO'
CAT_SIN_DOCUMENTO = 'SIN_DOCUMENTO'
CAT_COBRO_DIRECTO = 'COBRO_DIRECTO'
CAT_MANUAL = 'MANUAL'
CAT_CON_DOCUMENTO = 'CON_DOCUMENTO'
CATEGORIAS = {
    CAT_SIN_VENTA: 'Cobro aprobado que ninguna venta usa',
    CAT_TICKET_ANULADO: 'Cobrado con la venta anulada (devolver)',
    CAT_SIN_DOCUMENTO: 'Venta pagada sin boleta/factura',
    CAT_COBRO_DIRECTO: 'Cobro directo en terminal (sin ticket)',
    CAT_MANUAL: 'Registrado a mano (verificar contra MP)',
    CAT_CON_DOCUMENTO: 'Con documento',
}


def _fecha(valor, por_defecto):
    if isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return por_defecto


def rango_fechas(desde, hasta, dias_defecto=7, max_dias=93):
    """(desde, hasta) como date, con defaults y un tope de días."""
    hoy = timezone.localdate()
    h = _fecha(hasta, hoy)
    d = _fecha(desde, h - timedelta(days=dias_defecto - 1))
    if d > h:
        d, h = h, d
    if (h - d).days >= max_dias:
        d = h - timedelta(days=max_dias - 1)
    return d, h


# ───────────────────────────── 1. Cobros ↔ documentos ─────────────────────────

def _documento_de_ticket(ticket, dtes_por_ref):
    """(texto, dte_id) del documento tributario del ticket, o ('', None)."""
    if ticket is None:
        return '', None
    dte = dtes_por_ref.get((ticket.sucursal_id, ticket.correlativo))
    if dte is not None:
        return f'{dte.tipo_documento} #{dte.numero_documento}', dte.id
    if ticket.folio_dte:
        return f'{(ticket.tipo_dte or "DTE").replace("_", " ")} #{ticket.folio_dte}', None
    return '', None


def _dtes_por_ref(tickets):
    """{(sucursal_id, correlativo): Dte} por la referencia exacta 'TICKET-<corr>' (una consulta)."""
    dtes_por_ref = {}
    tickets = [tk for tk in tickets if tk is not None]
    if not tickets:
        return dtes_por_ref
    refs = {f'TICKET-{tk.correlativo}' for tk in tickets}
    sucs = {tk.sucursal_id for tk in tickets}
    for dte in (Dte.objects.filter(referencias__in=refs, sucursal_id__in=sucs)
                .exclude(descartado=True).only('id', 'tipo_documento', 'numero_documento',
                                               'referencias', 'sucursal_id')):
        try:
            corr = int(str(dte.referencias).split('-', 1)[1])
        except (IndexError, ValueError):
            continue
        dtes_por_ref[(dte.sucursal_id, corr)] = dte
    return dtes_por_ref


def _ticket_de_cobros(trxs):
    """{trx.id: Ticket|None}: FK directa, vía el pago, o por (sucursal, correlativo)."""
    salida, faltan = {}, {}
    for t in trxs:
        if t.ticket_id:
            salida[t.id] = t.ticket
        elif t.detalle_pago_id and t.detalle_pago.ticket_id:
            salida[t.id] = t.detalle_pago.ticket
        else:
            salida[t.id] = None
            corr = str(t.correlativo_ticket or '')
            if corr.isdigit():
                faltan[(t.sucursal_id, int(corr))] = None
    if faltan:
        q = Q()
        for suc, corr in faltan:
            q |= Q(sucursal_id=suc, correlativo=corr)
        for tk in Ticket.objects.filter(q):
            faltan[(tk.sucursal_id, tk.correlativo)] = tk
        for t in trxs:
            corr = str(t.correlativo_ticket or '')
            if salida[t.id] is None and corr.isdigit():
                salida[t.id] = faltan.get((t.sucursal_id, int(corr)))
    return salida


def cobros_vs_documentos(desde, hasta, sucursal_id=None):
    """Cada cobro MP del período clasificado según si terminó en documento.

    Devuelve {'filas': [...], 'resumen': {cat: {'cantidad', 'monto'}},
    'desde', 'hasta'}. Incluye los pagos MP registrados a MANO en el POS
    (origen MANUAL), que no tienen transacción: van en la categoría MANUAL.
    """
    d, h = rango_fechas(desde, hasta)
    trxs = list(
        TransaccionMercadoPago.objects
        .filter(tipo='VENTA', estado__in=('APROBADA', 'DEVUELTA', 'CONTRACARGO'),
                creado_en__date__gte=d, creado_en__date__lte=h)
        .exclude(correlativo_ticket__startswith='PRUEBA-')
        .select_related('sucursal', 'config', 'ticket', 'detalle_pago__ticket', 'retiro')
        .order_by('-creado_en')
    )
    if sucursal_id:
        trxs = [t for t in trxs if t.sucursal_id == int(sucursal_id)]

    # Ticket de cada cobro: FK directa, vía el pago, o por (sucursal, correlativo).
    faltan = {}
    for t in trxs:
        if t.ticket_id is None and not (t.detalle_pago_id and t.detalle_pago.ticket_id):
            corr = str(t.correlativo_ticket or '')
            if corr.isdigit():
                faltan[(t.sucursal_id, int(corr))] = None
    if faltan:
        q = Q()
        for suc, corr in faltan:
            q |= Q(sucursal_id=suc, correlativo=corr)
        for tk in Ticket.objects.filter(q):
            faltan[(tk.sucursal_id, tk.correlativo)] = tk

    def _ticket(t):
        if t.ticket_id:
            return t.ticket
        if t.detalle_pago_id and t.detalle_pago.ticket_id:
            return t.detalle_pago.ticket
        corr = str(t.correlativo_ticket or '')
        return faltan.get((t.sucursal_id, int(corr))) if corr.isdigit() else None

    tickets = {t.id: _ticket(t) for t in trxs}

    # Pagos MP registrados a mano en el período (sin transacción MP).
    manuales = list(
        TicketDetallePago.objects
        .filter(metodo_pago__in=METODOS_MP, origen_pago='MANUAL',
                ticket__estado='PAGADO', transacciones_mercadopago__isnull=True,
                creado_en__date__gte=d, creado_en__date__lte=h)
        .select_related('ticket', 'ticket__sucursal')
        .order_by('-creado_en')
    )
    if sucursal_id:
        manuales = [p for p in manuales if p.ticket.sucursal_id == int(sucursal_id)]

    dtes_por_ref = _dtes_por_ref([tk for tk in tickets.values() if tk] + [p.ticket for p in manuales])

    filas = []
    for t in trxs:
        tk = tickets[t.id]
        documento, dte_id = _documento_de_ticket(tk, dtes_por_ref)
        corr = str(t.correlativo_ticket or '')
        if t.estado != 'APROBADA':
            categoria = CAT_CON_DOCUMENTO if documento else CAT_SIN_VENTA
        elif corr.startswith('DIRECTO-') and not t.consumida:
            categoria = CAT_COBRO_DIRECTO
        elif tk is not None and tk.estado in ('ANULADO', 'DEVUELTO'):
            categoria = CAT_TICKET_ANULADO
        elif not t.consumida:
            # Aprobado y sin respaldar ningún pago: aunque el ticket esté PAGADO
            # (se cobró con otro medio), esta plata no está en la venta. Es el
            # caso NICK2 05-09: MP cobró y la venta se registró como tarjeta.
            categoria = CAT_SIN_VENTA
        elif documento:
            categoria = CAT_CON_DOCUMENTO
        else:
            categoria = CAT_SIN_DOCUMENTO
        filas.append({
            'origen': 'COBRO_MP',
            'id': t.id,
            'fecha': timezone.localtime(t.creado_en).strftime('%Y-%m-%d %H:%M'),
            'sucursal': t.sucursal.alias if t.sucursal_id else '',
            'sucursal_id': t.sucursal_id,
            'config_id': t.config_id,
            'caja': t.config.nombre if t.config_id else '',
            'correlativo': corr,
            'monto': t.monto,
            'monto_neto': t.monto_neto,
            'estado_mp': t.estado,
            'medio': mp.etiqueta_medio_mp(t.metodo_pago_mp),
            'payment_id_mp': t.payment_id_mp or '',
            'canal': t.canal,
            'ticket_id': tk.id if tk else None,
            'ticket_correlativo': tk.correlativo if tk else None,
            'ticket_estado': tk.estado if tk else '',
            'documento': documento,
            'dte_id': dte_id,
            'dte_fallido': bool(tk and tk.dte_generacion_fallida),
            'dte_error': (tk.dte_error_detalle or '')[:200] if tk else '',
            'puede_reintentar_dte': bool(
                categoria == CAT_SIN_DOCUMENTO and tk and tk.estado == 'PAGADO'
                and not tk.folio_dte and tk.tipo_dte in TIPOS_DTE_REINTENTABLES
            ),
            'liberacion': (timezone.localtime(t.money_release_date).strftime('%Y-%m-%d')
                           if t.money_release_date else ''),
            'retiro': t.retiro.withdrawal_id if t.retiro_id else '',
            'categoria': categoria,
        })
    for p in manuales:
        documento, dte_id = _documento_de_ticket(p.ticket, dtes_por_ref)
        filas.append({
            'origen': 'PAGO_MANUAL',
            'id': p.id,
            'fecha': timezone.localtime(p.creado_en).strftime('%Y-%m-%d %H:%M'),
            'sucursal': p.ticket.sucursal.alias if p.ticket.sucursal_id else '',
            'sucursal_id': p.ticket.sucursal_id,
            'config_id': None,
            'caja': '',
            'correlativo': str(p.ticket.correlativo),
            'monto': p.monto,
            'monto_neto': None,
            'estado_mp': 'MANUAL',
            'medio': p.tipo_tarjeta or p.metodo_pago,
            'payment_id_mp': p.voucher or '',
            'canal': 'MANUAL',
            'ticket_id': p.ticket_id,
            'ticket_correlativo': p.ticket.correlativo,
            'ticket_estado': p.ticket.estado,
            'documento': documento,
            'dte_id': dte_id,
            'dte_fallido': bool(p.ticket.dte_generacion_fallida),
            'dte_error': '',
            'puede_reintentar_dte': False,
            'liberacion': '',
            'retiro': '',
            'categoria': CAT_MANUAL,
        })

    orden = list(CATEGORIAS)
    filas.sort(key=lambda f: (orden.index(f['categoria']), f['fecha']), reverse=False)
    resumen = {c: {'etiqueta': CATEGORIAS[c], 'cantidad': 0, 'monto': 0} for c in CATEGORIAS}
    for f in filas:
        if f['estado_mp'] in ('DEVUELTA', 'CONTRACARGO'):
            continue
        resumen[f['categoria']]['cantidad'] += 1
        resumen[f['categoria']]['monto'] += int(f['monto'] or 0)
    return {'desde': str(d), 'hasta': str(h), 'filas': filas, 'resumen': resumen}


# ───────────────────────── 2. Reporte de Liberaciones → retiros ───────────────

_ALIAS_COLUMNAS = {
    'DATE': ('DATE', 'FECHA', 'FECHA DE LIBERACION', 'RELEASE_DATE'),
    'SOURCE_ID': ('SOURCE_ID', 'ID DE OPERACION', 'ID DE LA OPERACION', 'OPERATION_ID'),
    'EXTERNAL_REFERENCE': ('EXTERNAL_REFERENCE', 'REFERENCIA EXTERNA', 'CODIGO DE REFERENCIA'),
    'RECORD_TYPE': ('RECORD_TYPE', 'TIPO DE REGISTRO'),
    'DESCRIPTION': ('DESCRIPTION', 'DESCRIPCION', 'TIPO DE OPERACION'),
    'NET_CREDIT_AMOUNT': ('NET_CREDIT_AMOUNT', 'NET_CREDIT', 'CREDITO NETO', 'MONTO NETO ACREDITADO'),
    'NET_DEBIT_AMOUNT': ('NET_DEBIT_AMOUNT', 'NET_DEBIT', 'DEBITO NETO', 'MONTO NETO DEBITADO'),
    'GROSS_AMOUNT': ('GROSS_AMOUNT', 'MONTO BRUTO'),
    'EXTERNAL_POS_ID': ('EXTERNAL_POS_ID', 'ID EXTERNO DE LA CAJA', 'ID EXTERNO DEL PUNTO DE VENTA'),
    'POS_ID': ('POS_ID', 'ID DE LA CAJA', 'ID DEL PUNTO DE VENTA'),
    'POS_NAME': ('POS_NAME', 'NOMBRE DE LA CAJA', 'NOMBRE DEL PUNTO DE VENTA'),
}
# Comparación EXACTA (en minúsculas): 'tax_withholding_payout' es una
# retención sobre el retiro, NO el retiro; con búsqueda por substring contaba
# como retiro. Glosario oficial: payout = "withdrawal of available money".
DESCRIPCIONES_RETIRO = ('payout', 'withdrawal', 'retiro')
DESCRIPCIONES_PAGO = ('payment', 'pago')


def _sin_tildes(texto):
    return (str(texto or '').upper()
            .replace('Á', 'A').replace('É', 'E').replace('Í', 'I')
            .replace('Ó', 'O').replace('Ú', 'U').replace('Ñ', 'N').strip())


def _monto(valor):
    """'1.234.567', '1234567.00', '-5.000', '$ 12.000' → int (CLP)."""
    texto = str(valor or '').strip().replace('$', '').replace(' ', '')
    if not texto:
        return 0
    negativo = texto.startswith('-') or (texto.startswith('(') and texto.endswith(')'))
    texto = texto.strip('-()')
    if re.fullmatch(r'\d{1,3}(\.\d{3})+(,\d+)?', texto):      # 1.234.567,00
        texto = texto.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d+)?', texto):    # 1,234,567.00
        texto = texto.replace(',', '')
    else:
        texto = texto.replace(',', '.')
    try:
        # Half-up, nunca round()/int(): round(1234.5) da 1234 (redondeo bancario).
        n = int(Decimal(texto).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 0
    return -n if negativo else n


def _fecha_libre(valor):
    """ISO con/sin hora, dd/mm/yyyy o dd-mm-yyyy → date (o None)."""
    texto = str(valor or '').strip()
    if not texto:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', texto)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', texto)
    if m:
        anio = int(m.group(3))
        anio = anio + 2000 if anio < 100 else anio
        try:
            return date(anio, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def leer_csv(contenido):
    """Texto CSV (',' o ';') → lista de dicts con claves normalizadas (mayúsculas, sin tildes)."""
    if isinstance(contenido, bytes):
        for enc in ('utf-8-sig', 'latin-1'):
            try:
                contenido = contenido.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    muestra = contenido[:4096]
    delimitador = ';' if muestra.count(';') > muestra.count(',') else ','
    lector = csv.DictReader(io.StringIO(contenido), delimiter=delimitador)
    filas = []
    for fila in lector:
        filas.append({_sin_tildes(k): (v or '').strip() for k, v in fila.items() if k})
    return filas


def _col(fila, clave):
    for alias in _ALIAS_COLUMNAS[clave]:
        if alias in fila and fila[alias] != '':
            return fila[alias]
    return ''


def _instante(valor):
    """DATE del reporte ('2026-09-23T10:48:00.000-04:00') → datetime aware (o None).

    Se ordena por instante real y no por texto: el reporte puede mezclar
    offsets (-04:00 / -03:00 según horario de verano) y el orden alfabético
    pondría un retiro antes de los pagos que lo componen.
    """
    texto = str(valor or '').strip()
    if not texto:
        return None
    try:
        dt = datetime.fromisoformat(texto.replace('Z', '+00:00'))
    except ValueError:
        f = _fecha_libre(texto)
        if f is None:
            return None
        dt = datetime(f.year, f.month, f.day)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _cuenta_efectiva(config):
    """Cuenta MP de la caja con la misma regla que usa el cobro (`mp._cuenta_de`)."""
    try:
        return mp._cuenta_de(config)
    except AttributeError:   # caja sin sucursal ni cuenta
        return None


def _configs_de_la_cuenta(config):
    """Cajas que comparten la cuenta MP de `config` (el reporte es por cuenta).

    Misma regla que el cobro: la cuenta elegida en la caja o, si quedó en
    «automática» (FK vacío), la cuenta activa de la empresa. Antes solo miraba
    el FK y con «automática» quedaba UNA caja: las ventas de las otras cajas de
    la misma cuenta no se cruzaban ni se completaban.
    """
    cuenta = _cuenta_efectiva(config)
    if cuenta is None:
        return [config.id]
    ids = [config.id]
    for c in MercadoPagoConfig.objects.exclude(id=config.id).select_related('cuenta', 'sucursal'):
        otra = _cuenta_efectiva(c)
        if otra is not None and otra.id == cuenta.id:
            ids.append(c.id)
    return ids


def _remanente_previo(config, antes_de, monto, excluir_ids=(), retiros_del_reporte=(), piso=None):
    """Ventas liberadas ANTES del reporte y todavía sin retiro que explican el
    saldo inicial: las más recientes hacia atrás hasta cubrir `monto`.

    Sin esto, lo que quedó en MP después de un retiro parcial era un saldo
    anónimo en el reporte siguiente y esas ventas nunca se asociaban al retiro
    que finalmente se las llevó.

    Cuentan también las ventas que ya están amarradas a un retiro que viene en
    este mismo reporte: al reprocesar (el reporte pedido parte el día del
    último retiro) ese saldo ES lo que ese retiro se llevó. Si solo se miraban
    las sin retiro, el saldo se rellenaba con ventas viejas ajenas que quedaban
    amarradas a ese retiro, y el error crecía en cada pasada.

    `piso`: la liberación más antigua entre las ventas de esos retiros. Una
    venta sin retiro liberada ANTES ya salió de MP (FIFO: un retiro se lleva
    primero lo más antiguo), aunque en su momento no se haya podido cruzar:
    no sirve para rellenar el saldo. Lo que falte queda como «Saldo anterior».
    Devuelve [(trx, neto)] en orden de liberación (más antigua primero).
    """
    if monto <= 0 or antes_de is None:
        return []
    qs = (TransaccionMercadoPago.objects
          .filter(Q(retiro__isnull=True) | Q(retiro__withdrawal_id__in=list(retiros_del_reporte)),
                  config_id__in=_configs_de_la_cuenta(config), tipo='VENTA', estado='APROBADA',
                  money_release_date__isnull=False,
                  money_release_date__lt=antes_de,
                  money_release_date__gte=antes_de - timedelta(days=90))
          .exclude(id__in=list(excluir_ids))
          .select_related('config__sucursal')
          .order_by('-money_release_date', '-id'))
    if piso is not None:
        qs = qs.filter(Q(retiro__withdrawal_id__in=list(retiros_del_reporte)) | Q(money_release_date__gte=piso))
    elegidas, suma = [], 0
    for t in qs[:20000].iterator(chunk_size=500):
        neto = int(t.monto_neto if t.monto_neto is not None else t.monto)
        if neto <= 0:
            continue
        usa = min(neto, monto - suma)
        elegidas.append((t, usa))
        suma += usa
        if suma >= monto:
            break
    return list(reversed(elegidas))


def reportes_aplicados():
    """file_name de los reportes ya aplicados (lista acumulativa en los retiros)."""
    aplicados = set()
    for raw in (RetiroMercadoPago.objects.filter(raw_reporte__isnull=False)
                .order_by('-fecha').values_list('raw_reporte', flat=True)[:1000]):
        if not isinstance(raw, dict):
            continue
        aplicados.update(a for a in (raw.get('archivos') or []) if a)
        if raw.get('archivo'):
            aplicados.add(raw['archivo'])  # formato anterior
    return aplicados


CAJA_SALDO_ANTERIOR = 'Saldo anterior'
CAJA_NO_EXPLICADA = 'No explicado por el reporte'


def _no_explicado(por_caja):
    """Parte de un retiro que el reporte no alcanzó a explicar: el saldo con que
    partió («Saldo anterior») o un faltante («No explicado por el reporte»)."""
    return sum(int(x.get('monto') or 0) for x in (por_caja or [])
               if isinstance(x, dict) and x.get('caja') in (CAJA_SALDO_ANTERIOR, CAJA_NO_EXPLICADA))


def procesar_reporte_liberaciones(filas, config, aplicar=False, archivo=''):
    """Crea/actualiza retiros y asocia cada venta liberada a su retiro.

    `filas`: salida de ``leer_csv`` sobre el reporte de Liberaciones de MP
    (columnas DATE, SOURCE_ID, EXTERNAL_REFERENCE, RECORD_TYPE, DESCRIPTION,
    NET_CREDIT_AMOUNT, NET_DEBIT_AMOUNT; también acepta encabezados en
    castellano).

    Regla (FIFO de saldo): el saldo disponible es una cola en orden de
    liberación. Parte del saldo inicial del reporte, reconstruido con las ventas
    previas que siguen sin retiro. Cada retiro (DESCRIPTION 'payout' o
    'withdrawal') se lleva plata desde el frente; una venta queda asociada al
    retiro que se lleva su último peso. Funciona igual con el retiro automático
    (se lleva todo) y con un retiro manual parcial.

    Movimientos que restan y no son retiros (devolución, contracargo, comisión,
    retención): primero se descuentan de la MISMA operación si está en la cola
    (esa venta no llega al banco); el resto sale del frente, y las ventas que
    se agotan así se asocian al retiro siguiente. Si la cola está vacía, la
    diferencia queda como deuda que pagan los créditos siguientes.

    Idempotente y a prueba de reprocesos: al aplicar, las ventas que un retiro de
    este reporte NO se llevó pierden ese vínculo (quedan disponibles en MP).

    - CONCILIADO: el retiro está cubierto por lo liberado.
    - CON_DIFERENCIA: el retiro supera lo que el reporte muestra disponible.
    `aplicar=False` no escribe nada (vista previa).
    """
    registros = []
    saldo_inicial = 0
    for fila in filas:
        tipo = _sin_tildes(_col(fila, 'RECORD_TYPE')).lower()
        credito = abs(_monto(_col(fila, 'NET_CREDIT_AMOUNT')))
        debito = abs(_monto(_col(fila, 'NET_DEBIT_AMOUNT')))
        if tipo in ('initial_available_balance', 'saldo inicial disponible', 'saldo inicial'):
            saldo_inicial += (credito - debito) or _monto(_col(fila, 'GROSS_AMOUNT'))
            continue
        if tipo and tipo not in ('release', 'liberacion', 'liberado', 'liberaciones'):
            continue  # total, available_balance (pre_/pos_payout), block, …
        instante = _instante(_col(fila, 'DATE'))
        if instante is None:
            continue
        registros.append({
            'instante': instante,
            'source_id': str(_col(fila, 'SOURCE_ID')).split('.')[0],
            'external_reference': _col(fila, 'EXTERNAL_REFERENCE'),
            'descripcion': _sin_tildes(_col(fila, 'DESCRIPTION')).lower(),
            'credito': credito,
            'pos': (_col(fila, 'EXTERNAL_POS_ID'), _col(fila, 'POS_ID'), _col(fila, 'POS_NAME')),
            'debito': debito,
        })
    registros.sort(key=lambda r: r['instante'])

    ids = [r['source_id'] for r in registros if r['source_id']]
    refs = [r['external_reference'] for r in registros if r['external_reference']]
    locales = {}
    if ids or refs:
        for t in TransaccionMercadoPago.objects.filter(
                Q(payment_id_mp__in=ids) | Q(payment_id__in=ids) | Q(external_reference__in=refs)
        ).select_related('config__sucursal'):
            for clave in (t.payment_id_mp, t.payment_id, t.external_reference):
                if clave:
                    locales[str(clave)] = t

    # Pagos «MP manual» del POS: el N° de operación quedó en el voucher.
    manuales = {}
    ids_num = {i for i in ids if i.isdigit()}
    if ids_num:
        for pago in (TicketDetallePago.objects
                     .filter(metodo_pago__in=METODOS_MP, voucher__in=list(ids_num))
                     .select_related('ticket__sucursal')):
            manuales[str(pago.voucher).strip()] = pago

    # Caja por el punto de venta que informa el reporte (external_pos_id / pos_id).
    cajas_por_pos = {}
    for c in MercadoPagoConfig.objects.filter(id__in=_configs_de_la_cuenta(config)).select_related('sucursal'):
        etiqueta = f'{c.sucursal.alias} · {c.nombre}' if c.sucursal_id else c.nombre
        for clave in (c.external_pos_id, c.pos_id):
            if clave:
                cajas_por_pos[str(clave)] = etiqueta

    def _caja_de(item):
        trx = item.get('trx')
        if trx is not None and trx.config_id:
            cfg = trx.config
            return f'{cfg.sucursal.alias} · {cfg.nombre}' if cfg.sucursal_id else cfg.nombre
        manual = item.get('manual')
        if manual is not None:
            return f'{manual.ticket.sucursal.alias} · MP manual'
        ext_pos, pos_id, pos_nombre = item.get('pos') or ('', '', '')
        return (cajas_por_pos.get(ext_pos) or cajas_por_pos.get(pos_id)
                or (f'Caja MP «{pos_nombre}»' if pos_nombre else '')
                or (CAJA_SALDO_ANTERIOR if item.get('id') == 'saldo inicial' else 'Sin caja (online, link de pago u otro)'))

    resultado = {'retiros': [], 'pagos_sin_local': 0, 'pagos_amarrados': 0,
                 'pagos_manuales': 0, 'muestra_sin_local': [], 'dias_sin_local': []}
    dias_sin_local = set()
    dias_manuales = set()
    reembolsos = {}     # source_id -> total devuelto en este reporte
    # Cola FIFO del saldo disponible: {'trx': TransaccionMercadoPago|None, 'resto': $, 'id': str}
    cola = []
    deuda = 0
    pendientes_asociar = []   # ventas agotadas por débitos que no son retiros
    sin_banco = []            # ventas devueltas: su plata no llegó al banco

    # Retiros que trae este reporte (mismo id que se les guarda más abajo), los
    # que ya estaban registrados y las ventas que cada uno ya tiene amarradas.
    ids_retiros = [r['source_id'] or f'RET-{r["instante"]:%Y%m%d%H%M}-{r["debito"]}'
                   for r in registros if r['descripcion'] in DESCRIPCIONES_RETIRO and r['debito']]
    registrados = ({x.withdrawal_id: x for x in RetiroMercadoPago.objects.filter(withdrawal_id__in=ids_retiros)}
                   if ids_retiros else {})
    ya_registrados = set(registrados)
    # «Cerrado»: su desglose guardado explica todo el monto. Esos no se recalculan
    # al reprocesar; los que quedaron con algo sin explicar sí (FIFO de nuevo).
    cerrados = {wid for wid, x in registrados.items()
                if isinstance(x.raw_reporte, dict) and x.raw_reporte.get('por_caja')
                and _no_explicado(x.raw_reporte['por_caja']) == 0}
    # Retiros guardados antes de existir el desglose: cerrados si las ventas que
    # ya tienen amarradas cubren su monto (si no, al reprocesar se desamarraban).
    legado = [wid for wid, x in registrados.items()
              if not (isinstance(x.raw_reporte, dict) and x.raw_reporte.get('por_caja'))]
    if legado:
        sumas = dict(TransaccionMercadoPago.objects.filter(retiro__withdrawal_id__in=legado)
                     .values('retiro__withdrawal_id')
                     .annotate(s=Sum(Coalesce('monto_neto', 'monto')))
                     .values_list('retiro__withdrawal_id', 's'))
        cerrados |= {wid for wid in legado if (sumas.get(wid) or 0) >= registrados[wid].monto > 0}
    propias_de = {}
    if cerrados:
        for trx_id, wid in (TransaccionMercadoPago.objects.filter(retiro__withdrawal_id__in=list(cerrados))
                            .values_list('id', 'retiro__withdrawal_id')):
            propias_de.setdefault(wid, set()).add(trx_id)
    piso = (TransaccionMercadoPago.objects
            .filter(id__in=set().union(*propias_de.values()), money_release_date__isnull=False)
            .aggregate(m=Min('money_release_date'))['m'] if propias_de else None)
    # Ventas que el reporte trae como liberación (no las que solo aparecen en
    # una devolución: esas pueden ser de un retiro y deben poder rearmar el saldo).
    ids_credito = set()
    for r in registros:
        if r['credito'] and r['descripcion'] in DESCRIPCIONES_PAGO:
            t = locales.get(r['source_id']) or locales.get(r['external_reference'])
            if t is not None:
                ids_credito.add(t.id)

    if saldo_inicial < 0:
        deuda = -saldo_inicial
    elif saldo_inicial > 0:
        previas = _remanente_previo(
            config, registros[0]['instante'] if registros else None, saldo_inicial,
            excluir_ids=ids_credito, retiros_del_reporte=ids_retiros, piso=piso,
        )
        anonimo = saldo_inicial - sum(n for _t, n in previas)
        if anonimo > 0:
            cola.append({'trx': None, 'resto': anonimo, 'id': 'saldo inicial'})
        for trx, neto in previas:
            cola.append({'trx': trx, 'resto': neto, 'id': trx.payment_id_mp or trx.external_reference})
    resultado['remanente_previo'] = sum(it['resto'] for it in cola if it['trx'] is not None)

    tomado = []   # (item, monto) que se lleva el retiro en curso

    def _consumir(monto):
        """Saca `monto` del frente de la cola. Devuelve (faltante, ítems agotados)."""
        restante, agotados = monto, []
        while restante > 0 and cola:
            item = cola[0]
            usa = min(item['resto'], restante)
            item['resto'] -= usa
            restante -= usa
            tomado.append((item, usa))
            if item['resto'] <= 0:
                agotados.append(cola.pop(0))
        return restante, agotados

    retiros_ids = []   # RetiroMercadoPago escritos en esta pasada (solo con aplicar)
    liberadas = {}     # trx.id -> (trx, instante de la liberación) de las ventas del POS
    devueltas_fuera = []   # devueltas enteras cuya liberación quedó antes del reporte
    for r in registros:
        desc = r['descripcion']
        if desc in DESCRIPCIONES_RETIRO and r['debito']:
            monto = r['debito']
            withdrawal_id = r['source_id'] or f'RET-{r["instante"]:%Y%m%d%H%M}-{monto}'
            tomado.clear()
            # Un retiro cerrado se lleva primero SUS ventas: al reprocesar no se le
            # cambian por otras ni se le pasa una suya al retiro siguiente.
            propias, sacado = [], 0
            ids_propias = propias_de.get(withdrawal_id) or set()
            for it in list(cola):
                if sacado >= monto:
                    break
                if it['trx'] is not None and it['trx'].id in ids_propias:
                    usa = min(it['resto'], monto - sacado)
                    it['resto'] -= usa
                    sacado += usa
                    tomado.append((it, usa))
                    if it['resto'] <= 0:
                        cola.remove(it)
                        propias.append(it)
            faltante, agotados = _consumir(monto - sacado)
            agotados = propias + agotados
            por_caja = {}
            for item, usado in tomado:
                por_caja[_caja_de(item)] = por_caja.get(_caja_de(item), 0) + usado
            if faltante:
                por_caja[CAJA_NO_EXPLICADA] = faltante
            trxs = [it['trx'] for it in pendientes_asociar + agotados if it['trx'] is not None]
            pendientes_asociar = []
            quedan = sum(it['resto'] for it in cola)
            estado = 'CONCILIADO' if faltante == 0 else 'CON_DIFERENCIA'
            if faltante:
                detalle = (
                    f'Hay una diferencia de ${faltante:,}: el retiro de ${monto:,} supera lo liberado '
                    f'que muestra el reporte. Pida un reporte que empiece antes (saldo previo) '
                    'o revise movimientos que no pasan por el POS.'
                ).replace(',', '.')
            else:
                detalle = (f'Retiro parcial: quedaron ${quedan:,} disponibles en Mercado Pago.'
                           .replace(',', '.') if quedan else '')
            por_caja_lista = sorted(({'caja': k, 'monto': v} for k, v in por_caja.items()),
                                    key=lambda x: -x['monto'])
            # Retiro ya registrado: si su desglose guardado lo explica todo, o mejor
            # que esta pasada (este reporte parte más tarde), se conserva tal cual,
            # con sus vínculos. Solo se recalcula lo que quedó sin explicar.
            previo = registrados.get(withdrawal_id)
            raw_prev = previo.raw_reporte if previo is not None and isinstance(previo.raw_reporte, dict) else {}
            guardado = raw_prev.get('por_caja')
            conservar = bool(guardado) and (_no_explicado(guardado) == 0
                                            or _no_explicado(guardado) < _no_explicado(por_caja_lista))
            if conservar:
                trxs = []
                por_caja_lista = guardado
                estado = previo.estado
                detalle = previo.detalle_diferencia or detalle
            resultado['retiros'].append({
                'withdrawal_id': withdrawal_id,
                'fecha': str(timezone.localtime(r['instante']).date()),
                'hora': timezone.localtime(r['instante']).strftime('%H:%M'),
                'monto': monto,
                'pagos': len(agotados),
                'pagos_pos': raw_prev.get('pagos_pos', len(ids_propias)) if conservar else len(trxs),
                'suma_pagos': monto - faltante,
                'por_caja': por_caja_lista,
                'quedan_disponibles': quedan,
                'estado': estado,
                'detalle': detalle,
                'nuevo': withdrawal_id not in ya_registrados,
            })
            if aplicar:
                with transaction.atomic():
                    retiro = (RetiroMercadoPago.objects.select_for_update()
                              .filter(withdrawal_id=withdrawal_id).first())
                    raw = dict(retiro.raw_reporte or {}) if retiro and isinstance(retiro.raw_reporte, dict) else {}
                    archivos = set(raw.get('archivos') or [])
                    if raw.get('archivo'):
                        archivos.add(raw['archivo'])
                    if archivo:
                        archivos.add(archivo)
                    if retiro is not None and conservar:
                        raw['archivos'] = sorted(archivos)
                        raw.pop('archivo', None)
                        retiro.raw_reporte = raw
                        retiro.save(update_fields=['raw_reporte', 'actualizado_en'])
                        # Sus vínculos quedan como estaban; solo se amarran los cobros
                        # que ya había contado (por su N°) y que se registraron después,
                        # p. ej. un pago «MP manual» importado.
                        prev_ids = ({str(x) for x in raw.get('pagos') or []}
                                    | {str(x) for x in (raw.get('netos') or {})})
                        nuevas = [it['trx'].id for it, _u in tomado
                                  if it['trx'] is not None and it['trx'].retiro_id is None
                                  and str(it['id']) in prev_ids]
                        if nuevas:
                            TransaccionMercadoPago.objects.filter(
                                id__in=nuevas, retiro__isnull=True).update(retiro=retiro)
                        continue
                    raw_nuevo = {'archivos': sorted(archivos),
                                 'pagos': [it['id'] for it in agotados][:500],
                                 'instante': r['instante'].isoformat(),
                                 'por_caja': por_caja_lista,
                                 'pagos_pos': len(trxs),
                                 # neto liberado de cada pago «MP manual» que este retiro terminó
                                 # de llevarse (el pago entero, no solo el trozo de este retiro)
                                 'netos': {str(it['id']): it.get('total', usado) for it, usado in tomado[:2000]
                                           if it['trx'] is None and it.get('manual') is not None
                                           and it['resto'] <= 0},
                                 # lo que no se pudo explicar con una venta del sistema
                                 'sin_venta': faltante + sum(usado for it, usado in tomado
                                                             if it['trx'] is None and it.get('manual') is None),
                                 # y por operación, para descontarlo si después aparece su cobro
                                 'sin_local': {str(it['id']): usado for it, usado in tomado[:2000]
                                               if it['trx'] is None and it.get('manual') is None
                                               and it['id'] and it['id'] != 'saldo inicial'}}
                    datos = {'config': config, 'fecha': timezone.localtime(r['instante']).date(),
                             'monto': monto, 'estado': estado, 'detalle_diferencia': detalle,
                             'raw_reporte': raw_nuevo}
                    if retiro is None:
                        retiro = RetiroMercadoPago.objects.create(withdrawal_id=withdrawal_id, **datos)
                    else:
                        for campo, valor in datos.items():
                            setattr(retiro, campo, valor)
                        retiro.save()
                    retiros_ids.append(retiro.id)
                    if trxs:
                        TransaccionMercadoPago.objects.filter(
                            id__in=[t.id for t in trxs]).update(retiro=retiro)
            resultado['pagos_amarrados'] += len(trxs)
            continue

        neto = r['credito'] - r['debito']
        if not neto:
            continue
        if neto < 0:
            debito = -neto
            if r['source_id']:
                reembolsos[r['source_id']] = reembolsos.get(r['source_id'], 0) + debito
            # 1) La misma operación (devolución de esa venta): no llega al banco.
            en_cola = False
            for it in list(cola):
                if r['source_id'] and it['id'] == r['source_id'] and it['trx'] is not None:
                    en_cola = True
                    usa = min(it['resto'], debito)
                    it['resto'] -= usa
                    debito -= usa
                    if it['resto'] <= 0:
                        cola.remove(it)
                        sin_banco.append(it['trx'])
                    break
            # Devuelta entera una venta liberada antes de este reporte y que no se
            # llevó ningún retiro: su plata ya no está en MP. Se le borra la fecha de
            # liberación para que ningún reporte posterior la dé por depositada.
            t_dev = locales.get(r['source_id']) if (r['source_id'] and not en_cola) else None
            if (t_dev is not None and t_dev.retiro_id is None
                    and -neto >= int(t_dev.monto_neto if t_dev.monto_neto is not None else t_dev.monto)):
                devueltas_fuera.append(t_dev)
            # 2) El resto sale del frente; esas ventas van con el retiro siguiente.
            if debito:
                faltante, agotados = _consumir(debito)
                pendientes_asociar.extend(agotados)
                deuda += faltante
            continue
        # Crédito: primero paga la deuda (saldo negativo), después entra a la cola.
        es_pago = desc in DESCRIPCIONES_PAGO
        trx = (locales.get(r['source_id']) or locales.get(r['external_reference'])) if es_pago else None
        if trx is not None:
            liberadas.setdefault(trx.id, (trx, r['instante']))
        manual = manuales.get(r['source_id']) if (es_pago and trx is None) else None
        if manual is not None:
            resultado['pagos_manuales'] += 1
            # Días a leer para registrar este pago como cobro (el cobro en la
            # máquina fue minutos antes de registrarlo: si fue tras la medianoche, el anterior).
            local = timezone.localtime(manual.creado_en)
            dias_manuales.add(local.date())
            if (local.hour, local.minute) < (0, 30):
                dias_manuales.add(local.date() - timedelta(days=1))
        elif es_pago and trx is None:
            resultado['pagos_sin_local'] += 1
            dias_sin_local.add(timezone.localtime(r['instante']).date())
            if len(resultado['muestra_sin_local']) < 5:
                resultado['muestra_sin_local'].append({
                    'fecha': timezone.localtime(r['instante']).strftime('%Y-%m-%d %H:%M'),
                    'source_id': r['source_id'], 'external_reference': r['external_reference'],
                    'monto': neto, 'pos': ' / '.join(x for x in r['pos'] if x),
                })
        total = neto   # neto del pago entero (antes de pagar una deuda de saldo)
        if deuda:
            usa = min(deuda, neto)
            deuda -= usa
            neto -= usa
            if not neto:
                continue
        cola.append({'trx': trx, 'resto': neto, 'total': total, 'id': r['source_id'], 'pos': r['pos'],
                     'manual': manual})

    resultado['liberado_sin_retirar'] = sum(it['resto'] for it in cola)
    resultado['dias_sin_local'] = sorted(str(d) for d in dias_sin_local)
    resultado['dias_manuales'] = sorted(str(d) for d in dias_manuales)
    resultado['deuda'] = deuda
    resultado['rango'] = ([registros[0]['instante'].isoformat(), registros[-1]['instante'].isoformat()]
                          if registros else [])

    # La fila «release» ES la liberación: se guarda en la venta si no la tenía.
    # Sin esa fecha `_remanente_previo` no veía lo que dejó un retiro parcial y
    # esas ventas quedaban para siempre como «liberado sin retirar».
    for t in sin_banco + devueltas_fuera:
        liberadas.pop(t.id, None)
    if aplicar and (sin_banco or devueltas_fuera):
        ids_dev = [t.id for t in sin_banco + devueltas_fuera]
        TransaccionMercadoPago.objects.filter(id__in=ids_dev, retiro__isnull=True).update(money_release_date=None)
        # Un pago «MP manual» importado no recibe avisos de MP por referencia: si el
        # reporte muestra devuelto su neto entero, se marca DEVUELTA acá. Una
        # devolución parcial no: la venta sigue vigente por el resto.
        enteras = [t.id for t in sin_banco + devueltas_fuera
                   if reembolsos.get(t.payment_id_mp or t.payment_id or '', 0)
                   >= int(t.monto_neto if t.monto_neto is not None else t.monto)]
        TransaccionMercadoPago.objects.filter(
            id__in=enteras, external_reference__startswith='MANUAL-', estado='APROBADA',
        ).update(estado='DEVUELTA', estado_detalle='Devuelta en Mercado Pago (reporte de Liberaciones)')
    if aplicar and liberadas:
        sin_fecha = []
        for trx, instante in liberadas.values():
            if trx.money_release_date is None:
                trx.money_release_date = instante
                sin_fecha.append(trx)
        if sin_fecha:
            TransaccionMercadoPago.objects.bulk_update(sin_fecha, ['money_release_date'], batch_size=200)

    # Reproceso: lo que ningún retiro de ESTE reporte se llevó no puede quedar
    # asociado a esos retiros (quedaría "depositado" estando en MP).
    if aplicar and retiros_ids:
        sueltas = [it['trx'].id for it in cola + pendientes_asociar if it['trx'] is not None]
        sueltas += [t.id for t in sin_banco]
        if sueltas:
            resultado['desasociadas'] = (TransaccionMercadoPago.objects
                                         .filter(id__in=sueltas, retiro_id__in=retiros_ids)
                                         .update(retiro=None))
    return resultado


def _vouchers_mp_manual(configs, desde, hasta):
    """{N° de operación: TicketDetallePago} de los pagos «MP manual» de ventas
    cerradas (ticket PAGADO) de las tiendas de la cuenta, sin transacción
    todavía y con un N° con forma válida."""
    sucursales = set(MercadoPagoConfig.objects.filter(id__in=configs).values_list('sucursal_id', flat=True))
    salida = {}
    for pago in (TicketDetallePago.objects
                 .filter(Q(origen_pago='MANUAL') | Q(origen_pago__isnull=True),
                         metodo_pago__in=METODOS_MP, ticket__sucursal_id__in=sucursales,
                         ticket__estado='PAGADO', transacciones_mercadopago__isnull=True,
                         creado_en__date__gte=desde, creado_en__date__lte=hasta)
                 .select_related('ticket').order_by('creado_en')):
        voucher = str(pago.voucher or '').strip()
        if voucher.isdigit() and len(voucher) >= 9:
            salida[voucher] = pago
    # Un N° digitado en más de un pago (sin transacción, en cualquier fecha) es
    # ambiguo: no se elige uno, queda para «Asociar».
    if salida:
        repetidos = set(TicketDetallePago.objects
                        .filter(metodo_pago__in=METODOS_MP, voucher__in=list(salida),
                                transacciones_mercadopago__isnull=True)
                        .exclude(ticket__estado='ANULADO')
                        .values('voucher').annotate(n=Count('id')).filter(n__gt=1)
                        .values_list('voucher', flat=True))
        for voucher in repetidos:
            salida.pop(str(voucher).strip(), None)
    return salida


def _caja_del_pago(payment, cajas):
    """Caja (id de MercadoPagoConfig) de la tienda donde se cobró el pago: la única
    si hay una; si no, la que tiene el punto de venta que informa MP; si no, la
    habilitada y principal. Nunca «la de menor id» a ciegas: el cierre de cada
    caja aprende de estos cobros qué pos_id/store_id le pertenecen."""
    if len(cajas) == 1:
        return cajas[0].id
    claves = {str(payment.get(k) or '') for k in ('pos_id', 'store_id')} - {''}
    for c in cajas:
        if claves & ({c.pos_id, c.external_pos_id} - {''}):
            return c.id
    return cajas[0].id if cajas else None


def _retiros_por_operacion(configs, dias=180):
    """{N° de operación: RetiroMercadoPago} de los retiros recientes de la cuenta
    que ya contaron ese pago (raw_reporte 'pagos'/'netos'), para amarrar al
    registrarlo un pago «MP manual» que un retiro ya se llevó."""
    salida = {}
    desde = timezone.localdate() - timedelta(days=dias)
    for retiro in RetiroMercadoPago.objects.filter(config_id__in=configs, fecha__gte=desde).order_by('fecha', 'id'):
        raw = retiro.raw_reporte if isinstance(retiro.raw_reporte, dict) else {}
        for clave in list(raw.get('pagos') or []) + list((raw.get('netos') or {}).keys()):
            salida[str(clave)] = retiro
    return salida


def _importar_pago_manual(pago, payment, config_id, retiro=None):
    """Transacción (consumida) para un pago «MP manual» con los datos de MP.

    No se importa (queda para «Asociar») si: el pago no está aprobado, el monto
    no calza, ya tuvo devoluciones, o MP lo trae con la referencia de un cobro
    que el sistema ya tiene (cobro del POS o «Tu caja» que todavía no tiene su
    N°: importarlo lo duplicaría).
    """
    pid = str(payment.get('id') or '')
    monto = _monto(payment.get('transaction_amount'))
    if payment.get('status') != 'approved' or monto != int(pago.monto or 0):
        return None
    if _monto(payment.get('transaction_amount_refunded') or 0) > 0 or payment.get('refunds'):
        return None
    ya_existe = Q(payment_id_mp=pid) | Q(payment_id=pid) | Q(external_reference=f'MANUAL-{pid}')
    referencia = str(payment.get('external_reference') or '').strip()
    if referencia:
        ya_existe |= Q(external_reference=referencia)
    if TransaccionMercadoPago.objects.filter(ya_existe).exists():
        return None
    neto = (payment.get('transaction_details') or {}).get('net_received_amount')
    neto = _monto(neto) if neto is not None else None
    tipo_pago = str(payment.get('payment_type_id') or '')
    try:
        with transaction.atomic():
            # Lock del pago (lo toman también «Asociar» e importar_y_asociar) y
            # revisión de nuevo: otra pasada o un Maestro pudo haberlo resuelto.
            bloqueado = (TicketDetallePago.objects.select_for_update()
                         .filter(pk=pago.pk, ticket__estado='PAGADO').first())
            # Revalidar con el pago bloqueado (un Maestro pudo corregirlo mientras
            # corría la pasada): mismo N°, mismo monto y sigue siendo MP manual.
            if (bloqueado is None or bloqueado.metodo_pago not in METODOS_MP
                    or bloqueado.origen_pago not in ('MANUAL', None)
                    or str(bloqueado.voucher or '').strip() != pid or int(bloqueado.monto or 0) != monto
                    or TransaccionMercadoPago.objects.filter(ya_existe | Q(detalle_pago_id=pago.pk)).exists()):
                return None
            pago = bloqueado
            trx = TransaccionMercadoPago.objects.create(
                config_id=config_id, sucursal_id=pago.ticket.sucursal_id, ticket=pago.ticket,
                detalle_pago=pago, correlativo_ticket=str(pago.ticket.correlativo), tipo='VENTA',
                canal='POINT' if tipo_pago in ('debit_card', 'credit_card', 'prepaid_card') else 'QR',
                external_reference=f'MANUAL-{pid}', payment_id=pid, payment_id_mp=pid,
                monto=monto, monto_neto=neto, fee_mp=(monto - neto) if neto is not None else None,
                installments=int(payment.get('installments') or 1), estado='APROBADA',
                estado_detalle=f'Pago MP manual del POS, datos traídos de MP ({payment.get("status_detail") or ""})'[:120],
                metodo_pago_mp=tipo_pago[:40],
                ultimos_4_digitos=str((payment.get('card') or {}).get('last_four_digits') or '')[:4],
                codigo_autorizacion=str(payment.get('authorization_code') or '')[:30],
                money_release_date=_instante(payment.get('money_release_date')),
                consumida=True, raw_response=payment, retiro=retiro,
            )
            # Fecha del cobro real (creado_en es auto_now_add: quedaría con la de hoy).
            TransaccionMercadoPago.objects.filter(pk=trx.pk).update(
                creado_en=_instante(payment.get('date_created')) or pago.creado_en)
    except IntegrityError:
        return None   # otra pasada lo registró al mismo tiempo
    logger.info("Conciliación MP: pago MP manual N° %s ($%s) del ticket #%s registrado como cobro",
                pid, monto, pago.ticket.correlativo)
    return trx


def completar_numeros_mp(config, dias, presupuesto_seg=35, importar=False):
    """Rellena `payment_id_mp` (y neto/comisión si faltan) de los cobros de la
    cuenta leyendo `payments/search` de esos días, cruzado por external_reference.

    El reporte de Liberaciones identifica cada pago por ese número; el aviso de
    pagos (webhook) casi nunca lo dejó guardado, y sin él ninguna venta del POS
    se cruzaba con su retiro. También guarda la fecha de liberación (solo pagos
    aprobados), aunque el cobro ya tuviera el N°: sin ella el saldo con que
    parte un reporte no se puede explicar con ventas y un retiro quedaba como
    «Saldo anterior». Solo escribe campos vacíos. Devuelve
    {'dias': n, 'completados': n (N° nuevos), 'actualizados': n (filas tocadas),
    'importados': n (pagos «MP manual» registrados como cobro), 'sin_tiempo': bool,
    'fallidos': n}. Con `importar=True` (solo al APLICAR; nunca en una vista
    previa), los pagos «MP manual» cuyo N° aparece ese día en MP se registran
    como cobro (`_importar_pago_manual`).
    `fallidos` son los días que no se pudieron leer por un error de red o de
    MP saturado (429/5xx): quien llama debe tratarlo como «no terminó».
    """
    import time as _time
    fin = _time.monotonic() + presupuesto_seg
    completados, leidos, fallidos, seguidos, actualizados, importados = 0, 0, 0, 0, 0, 0
    configs = _configs_de_la_cuenta(config)
    hoy = timezone.localdate()
    dias = sorted(set(dias))
    manuales = (_vouchers_mp_manual(configs, dias[0] - timedelta(days=1), dias[-1] + timedelta(days=1))
                if dias and importar else {})
    cajas_de_tienda, retiro_de = {}, {}
    if manuales:
        # Todas las cajas de esas tiendas (el pago trae su punto de venta).
        tiendas = MercadoPagoConfig.objects.filter(id__in=configs).values_list('sucursal_id', flat=True)
        for c in (MercadoPagoConfig.objects.filter(sucursal_id__in=list(tiendas))
                  .order_by('-habilitado', '-es_principal', 'id')):
            cajas_de_tienda.setdefault(c.sucursal_id, []).append(c)
        retiro_de = _retiros_por_operacion(configs)
    for dia in dias:
        # Un día pasado ya leído hace poco no se vuelve a pedir: lo que no cruzó
        # entonces (pagos online, devueltos) tampoco va a cruzar ahora.
        # La marca es de una pasada que aplica (con importación): una vista previa
        # no la usa ni la deja, para que el «Aplicar» siguiente sí importe.
        clave_dia = f'conc_mp:dia_leido:{min(configs)}:{dia}'
        if importar and dia < hoy and cache.get(clave_dia):
            continue
        if _time.monotonic() > fin:
            return {'dias': leidos, 'completados': completados, 'sin_tiempo': True, 'fallidos': fallidos,
                    'actualizados': actualizados, 'importados': importados}
        try:
            pagos = mp.buscar_pagos_dia(config, dia)
        except mp.MercadoPagoError as e:
            logger.warning("Conciliación MP: no se pudo leer payments/search %s: %s", dia, e.mensaje)
            if getattr(e, 'red', False):
                fallidos += 1
                seguidos += 1
                if seguidos >= 2:   # breaker abierto o MP caído: no insistir día por día
                    break
            continue
        seguidos = 0
        leidos += 1
        if importar and dia < hoy:
            cache.set(clave_dia, 1, 60 * 30)
        por_ref = {str(p.get('external_reference') or ''): p for p in pagos
                   if p.get('external_reference') and str(p.get('id') or '').isdigit()}
        for trx in TransaccionMercadoPago.objects.filter(
                Q(payment_id_mp='') | Q(money_release_date__isnull=True),
                config_id__in=configs, external_reference__in=list(por_ref)):
            pago = por_ref[trx.external_reference]
            if trx.payment_id_mp and trx.payment_id_mp != str(pago['id']):
                continue   # es otro pago (reintento): no se mezclan sus datos
            campos = []
            if not trx.payment_id_mp:
                trx.payment_id_mp = str(pago['id'])[:40]
                campos.append('payment_id_mp')
            neto = (pago.get('transaction_details') or {}).get('net_received_amount')
            if trx.monto_neto is None and neto is not None:
                trx.monto_neto = _monto(neto)
                trx.fee_mp = trx.monto - trx.monto_neto
                campos += ['monto_neto', 'fee_mp']
            liberacion = _instante(pago.get('money_release_date'))
            if (trx.money_release_date is None and liberacion is not None
                    and pago.get('status') == 'approved'):
                trx.money_release_date = liberacion
                campos.append('money_release_date')
            if not campos:
                continue
            trx.save(update_fields=campos + ['actualizado_en'])
            actualizados += 1
            if 'payment_id_mp' in campos:
                completados += 1
        # Después de completar los N° por referencia: así un cobro del POS que
        # recién recibe su N° no se vuelve a crear como pago manual.
        for p in pagos:
            pid = str(p.get('id') or '')
            pago = manuales.pop(pid, None)
            if pago is None:
                continue
            caja = _caja_del_pago(p, cajas_de_tienda.get(pago.ticket.sucursal_id) or []) or config.id
            if _importar_pago_manual(pago, p, caja, retiro=retiro_de.get(pid)) is not None:
                importados += 1
    return {'dias': leidos, 'completados': completados, 'sin_tiempo': False, 'fallidos': fallidos,
            'actualizados': actualizados, 'importados': importados}


def dias_cobros_sin_numero(config, resultado, dias_antes=35):
    """Días de cobro a consultar en payments/search para cruzar un reporte.

    Son los días de las ventas de la cuenta que siguen sin N° de operación (o
    sin fecha de liberación) y sin retiro, desde `dias_antes` días antes del reporte (la liberación puede
    llegar semanas después del cobro) hasta su fin. Antes se miraban los pagos
    del reporte que no cruzaban: esos incluyen los online y de link de pago,
    que nunca cruzan, y el tope de 15 días dejaba fuera justo los días viejos,
    que son los que un retiro se lleva primero.
    """
    rango = resultado.get('rango') or []
    inicio = _instante(rango[0]) if rango else None
    fin = _instante(rango[-1]) if rango else None
    if inicio is None or fin is None:
        return []
    creados = (TransaccionMercadoPago.objects
               .filter(Q(estado='APROBADA', money_release_date__isnull=True)
                       | Q(estado__in=('APROBADA', 'DEVUELTA'), payment_id_mp='')
                       # aviso de pago perdido: MP cobró y acá quedó pendiente
                       | (Q(estado__in=('CREADA', 'PENDIENTE', 'EXPIRADA', 'ERROR'), payment_id_mp='')
                          & ~Q(order_id='')),
                       config_id__in=_configs_de_la_cuenta(config), tipo='VENTA', retiro__isnull=True,
                       creado_en__gte=inicio - timedelta(days=dias_antes), creado_en__lte=fin)
               .values_list('creado_en', flat=True))
    dias = set()
    for creado in creados:
        local = timezone.localtime(creado)
        dias.add(local.date())
        if (local.hour, local.minute) >= (23, 30):   # el pago puede quedar con fecha del día siguiente
            dias.add(local.date() + timedelta(days=1))
    return sorted(dias)


def dias_para_completar(config, resultado, importar=False):
    """Días a leer en payments/search antes de aplicar un reporte: los que
    tienen cobros propios por completar (los pagos online del reporte nunca
    cruzan y releerlos gastaba el tiempo de cada pasada) y, solo al aplicar,
    los de los pagos «MP manual» que trae el reporte (para registrarlos)."""
    dias = set(dias_cobros_sin_numero(config, resultado))
    if importar:
        dias.update(f for f in (_fecha_libre(x) for x in resultado.get('dias_manuales') or []) if f)
    return sorted(dias)


def dias_a_completar(resultado, margen=2, maximo=15):
    """Días de pago a consultar para los pagos del reporte que no se cruzaron
    (fecha de liberación y hasta `margen` días antes)."""
    dias = set()
    for texto in resultado.get('dias_sin_local') or []:
        base = _fecha_libre(texto)
        if base:
            dias.update(base - timedelta(days=k) for k in range(margen + 1))
    return sorted(dias)[-maximo:]


# ─────────────── API de reportes de Liberaciones (release_report) ───────────────
# Docs oficiales (mercadopago.cl/developers, verificadas 23-09-2026):
#   POST /v1/account/release_report          crea (202 = aceptado; 203 = NO se creó)
#   GET  /v1/account/release_report/task/{id} estado: pending|processing|processed|failed
#   GET  /v1/account/release_report/search    reportes generados (más reciente primero)
#   GET  /v1/account/release_report/{file}    descarga el CSV
#   GET/POST/PUT /v1/account/release_report/config   configuración (execute_after_withdrawal)
# La generación tarda "unos minutos": NUNCA se espera dentro del request.

_RE_ARCHIVO = re.compile(r'^[A-Za-z0-9._\-]{1,200}$')


def _utc(dt):
    return dt.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def rango_utc_reporte(desde, hasta):
    """(begin, end) en UTC para días de Chile, con fin nunca en el futuro y
    al menos 24 horas de largo (MP exige mínimo un día de información)."""
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(desde, datetime.min.time()), tz)
    fin = timezone.make_aware(datetime.combine(hasta, datetime.max.time().replace(microsecond=0)), tz)
    # En UTC: restar dos horas locales del mismo huso ignora el cambio de hora.
    inicio, fin = inicio.astimezone(dt_timezone.utc), fin.astimezone(dt_timezone.utc)
    ahora = timezone.now().astimezone(dt_timezone.utc) - timedelta(minutes=5)
    if fin > ahora:
        fin = ahora
    if fin - inicio < timedelta(days=1):
        inicio = fin - timedelta(days=1)
    maximo = timedelta(days=60) - timedelta(minutes=1)
    if fin - inicio > maximo:
        inicio = fin - maximo
    return _utc(inicio), _utc(fin)


def pedir_reporte_liberaciones(config, desde, hasta):
    """Pide a MP que genere el reporte. Devuelve al instante (no espera)."""
    begin, end = rango_utc_reporte(desde, hasta)
    resp = mp._request(config, 'POST', '/v1/account/release_report',
                       json_body={'begin_date': begin, 'end_date': end}, cuenta_breaker=False)
    try:
        cuerpo = resp.json()
    except ValueError:
        cuerpo = {}
    logger.info("MP release_report crear config=%s %s..%s status=%s cuerpo=%s",
                config.id, begin, end, resp.status_code, str(cuerpo)[:500])
    if resp.status_code == 203:
        raise mp.MercadoPagoError(
            'Mercado Pago recibió la solicitud pero NO creó el reporte con esas fechas. '
            f'Pruebe con un rango que termine ayer. Detalle: {str(cuerpo)[:200]}', detalle=cuerpo)
    if resp.status_code >= 400:
        mp._json_o_error(resp, 'release_report crear')
    tarea = cuerpo.get('id') if isinstance(cuerpo, dict) else None
    return {'task_id': tarea, 'begin_date': begin, 'end_date': end,
            'estado': (cuerpo.get('status') if isinstance(cuerpo, dict) else '') or 'pending'}


def estado_tarea_liberaciones(config, task_id):
    """{'estado', 'file_name', 'listo'} de una solicitud de reporte."""
    resp = mp._request(config, 'GET', f'/v1/account/release_report/task/{int(task_id)}',
                       cuenta_breaker=False)
    data = mp._json_o_error(resp, 'release_report task') or {}
    estado = str(data.get('status') or '')
    archivo = data.get('file_name') or ''
    # MP usa estados que no documenta ('data-ready' = juntó los datos y falta el
    # archivo; 'enabled' = listo): lo que manda es que ya exista el archivo.
    return {'estado': estado, 'estado_texto': texto_estado_reporte(estado, archivo), 'file_name': archivo,
            'listo': bool(archivo),
            'fallido': not archivo and estado in ESTADOS_REPORTE_FALLIDO}


ESTADOS_REPORTE_FALLIDO = ('failed', 'error', 'rejected', 'cancelled', 'canceled')


def texto_estado_reporte(estado, archivo=''):
    """Estado de un pedido de reporte en palabras."""
    if archivo:
        return 'listo'
    return {'pending': 'en cola en Mercado Pago', 'processing': 'Mercado Pago lo está procesando',
            'data-ready': 'datos listos, Mercado Pago está armando el archivo'}.get(
        estado, 'no se pudo generar' if estado in ESTADOS_REPORTE_FALLIDO else (estado or 'en proceso'))


def _normalizar_reporte(item):
    return {
        'id': item.get('id'),
        'file_name': item.get('file_name') or '',
        'begin_date': str(item.get('begin_date') or ''),
        'end_date': str(item.get('end_date') or ''),
        'creado': str(item.get('date_created') or item.get('generation_date')
                      or item.get('last_modified') or ''),
        'origen': str(item.get('created_from') or ''),
        'estado': str(item.get('status') or ''),
    }


def listar_reportes_liberaciones(config, limite=20, incluir_pendientes=False):
    """Reportes ya generados en MP, más recientes primero (solo los descargables).

    Usa /search (documentado: ordenado por creación descendente y con
    file_name); si falla, cae a /list, cuyo esquema actual no garantiza file_name.
    """
    reportes = []
    try:
        resp = mp._request(config, 'GET', '/v1/account/release_report/search',
                           params={'limit': limite, 'offset': 0}, cuenta_breaker=False)
        data = mp._json_o_error(resp, 'release_report search') or {}
        reportes = [_normalizar_reporte(i) for i in (data.get('results') or [])]
    except mp.MercadoPagoError as e:
        logger.warning("MP release_report search falló (%s); se usa /list", e.mensaje)
        resp = mp._request(config, 'GET', '/v1/account/release_report/list', cuenta_breaker=False)
        data = mp._json_o_error(resp, 'release_report list') or []
        if isinstance(data, dict):
            data = data.get('results') or []
        reportes = [_normalizar_reporte(i) for i in data]
        reportes.sort(key=lambda r: r['creado'], reverse=True)
    if incluir_pendientes:
        return reportes[:limite]
    return [r for r in reportes if r['file_name']][:limite]


def descargar_reporte_liberaciones(config, file_name):
    """Contenido (bytes) de un reporte ya generado."""
    if not _RE_ARCHIVO.match(str(file_name or '')):
        raise mp.MercadoPagoError('Nombre de reporte inválido.')
    resp = mp._request(config, 'GET', f'/v1/account/release_report/{file_name}',
                       cuenta_breaker=False)
    if resp.status_code >= 400:
        mp._json_o_error(resp, 'release_report descargar')
    return resp.content


COLUMNAS_REPORTE = (
    'DATE', 'SOURCE_ID', 'EXTERNAL_REFERENCE', 'RECORD_TYPE', 'DESCRIPTION',
    'NET_CREDIT_AMOUNT', 'NET_DEBIT_AMOUNT', 'GROSS_AMOUNT', 'MP_FEE_AMOUNT',
    'PAYMENT_METHOD', 'PAYMENT_METHOD_TYPE', 'POS_NAME', 'EXTERNAL_POS_ID',
    'PAYOUT_BANK_ACCOUNT_NUMBER',
)


def leer_config_reporte(config):
    """Configuración actual del reporte en MP, o None si nunca se creó."""
    resp = mp._request(config, 'GET', '/v1/account/release_report/config', cuenta_breaker=False)
    if resp.status_code == 404:
        return None
    return mp._json_o_error(resp, 'release_report config') or None


def activar_reporte_por_retiro(config):
    """Configura MP para generar el reporte de Liberaciones después de cada retiro.

    Conserva la configuración existente y solo agrega: las columnas que usa la
    conciliación, `execute_after_withdrawal=true`, `include_withdrawal_at_end=true`
    y encabezados en inglés (`report_translation='en'`, los nombres que lee el
    sistema). Devuelve la configuración resultante.
    """
    actual = leer_config_reporte(config)
    base = dict(actual or {})
    columnas = [c.get('key') for c in (base.get('columns') or []) if isinstance(c, dict)]
    for clave in COLUMNAS_REPORTE:
        if clave not in columnas:
            columnas.append(clave)
    offset = timezone.localtime().utcoffset()
    horas = int(offset.total_seconds() // 3600) if offset else -4
    cuerpo = {
        'file_name_prefix': base.get('file_name_prefix') or 'release-report-retailmind',
        'columns': [{'key': c} for c in columnas if c],
        'frequency': base.get('frequency') or {'hour': 0, 'type': 'monthly', 'value': 1},
        'include_withdrawal_at_end': True,
        'execute_after_withdrawal': True,
        'report_translation': 'en',
        'display_timezone': f'GMT{horas:+03d}',
    }
    for clave in ('separator', 'notification_email_list', 'sftp_info', 'check_available_balance',
                  'compensate_detail'):
        if base.get(clave) not in (None, ''):
            cuerpo[clave] = base[clave]
    metodo = 'PUT' if actual else 'POST'
    resp = mp._request(config, metodo, '/v1/account/release_report/config',
                       json_body=cuerpo, cuenta_breaker=False)
    if metodo == 'POST' and resp.status_code == 409:
        resp = mp._request(config, 'PUT', '/v1/account/release_report/config',
                           json_body=cuerpo, cuenta_breaker=False)
    data = mp._json_o_error(resp, f'release_report config {metodo}') or cuerpo
    logger.info("MP release_report config actualizada config=%s execute_after_withdrawal=%s",
                config.id, data.get('execute_after_withdrawal'))
    return data


def cuentas_mp():
    """[(config, cuenta, etiqueta)] una por CUENTA de Mercado Pago (empresa/RUT).

    Los retiros y los reportes de Liberaciones son de la cuenta: basta una caja
    cualquiera de cada cuenta para leerlos.
    """
    vistas = {}
    for cfg in (MercadoPagoConfig.objects.select_related('sucursal__empresa', 'cuenta__empresa')
                .order_by('-habilitado', 'id')):
        cuenta = mp._cuenta_de(cfg)
        clave = f'c{cuenta.id}' if cuenta else f'cfg{cfg.id}'
        if clave in vistas:
            vistas[clave]['cajas'].add(cfg.sucursal.alias if cfg.sucursal_id else cfg.nombre)
            continue
        empresa = cuenta.empresa if cuenta else (cfg.sucursal.empresa if cfg.sucursal_id else None)
        vistas[clave] = {
            'config': cfg,
            'nombre': (getattr(empresa, 'nombre', '') or 'Cuenta Mercado Pago'),
            'rut': getattr(empresa, 'rut', '') or '',
            'cajas': {cfg.sucursal.alias if cfg.sucursal_id else cfg.nombre},
        }
    return list(vistas.values())


# Un reporte que termina hace menos de esto se considera al día (no se pide otro).
FRESCURA_REPORTE_MIN = int(os.environ.get('MP_REPORTE_FRESCURA_MIN', '15'))
DIAS_MAX_REPORTE = 59          # MP acepta hasta 60 días por reporte
_CLAVE_PEDIDO = 'conc_mp:pedido:{}'
_CLAVE_SIN_RETIROS = 'conc_mp:sin_retiros:{}'


def inicio_reporte_cuenta(config, ahora=None):
    """Primer día del reporte que se pide para una cuenta.

    El día del último retiro registrado de la cuenta: el saldo que dejó ese
    retiro queda dentro y la cola FIFO parte bien (ese retiro se reprocesa y
    queda igual). Sin retiros registrados, lo máximo que acepta MP (59 días):
    el primer retiro se arma con toda la historia disponible.
    """
    ahora = ahora or timezone.now()
    tope = timezone.localtime(ahora).date() - timedelta(days=DIAS_MAX_REPORTE)
    ultimo = (RetiroMercadoPago.objects.filter(config_id__in=_configs_de_la_cuenta(config))
              .order_by('-fecha', '-id').values_list('fecha', flat=True).first())
    return max(ultimo, tope) if ultimo else tope


def _fin_de_reporte(reporte):
    """Hasta cuándo trae datos un reporte: su fin, pero nunca después de cuando
    se generó (MP pone de fin las 23:59 del día aunque se genere a mediodía; con
    eso el reporte parecía al día y no se pedía otro tras un retiro de la tarde).
    Sin fecha de creación y con fin en el futuro, no se sabe: None."""
    fin = _instante(reporte.get('end_date'))
    creado = _instante(reporte.get('creado'))
    if fin is None:
        return creado
    if creado is not None and creado < fin:
        return creado
    if creado is None and fin > timezone.now():
        return None
    return fin


def _pedido_valido(pedido):
    """Pedido de reporte que devuelve la página en las vueltas automáticas: solo
    los campos esperados y con forma válida (el N° de tarea lo valida MP con el
    token de la cuenta; el archivo, `_RE_ARCHIVO` al bajarlo)."""
    if not isinstance(pedido, dict):
        return None
    try:
        tarea = int(pedido.get('task_id') or 0)
    except (TypeError, ValueError):
        tarea = 0
    limpio = {k: str(pedido.get(k) or '')[:40] for k in ('begin_date', 'end_date', 'hasta', 'desde', 'pedido_a')}
    limpio['task_id'] = tarea or None
    archivo = str(pedido.get('file_name') or '')
    limpio['file_name'] = archivo if _RE_ARCHIVO.match(archivo) else ''
    return limpio if (limpio['task_id'] or limpio['file_name']) else None


def _pedido_en_cola_mp(pendientes, horas=6):
    """El pedido más reciente que MP todavía está generando (de las últimas
    `horas`), con la forma de un pedido propio. Así no se pide otro encima."""
    limite = timezone.now() - timedelta(hours=horas)
    for r in pendientes:
        creado = _instante(r.get('creado'))
        if (r.get('file_name') or not r.get('id') or r.get('estado') in ESTADOS_REPORTE_FALLIDO
                or creado is None or creado < limite):
            continue
        desde = _instante(r.get('begin_date'))
        return {'task_id': int(r['id']), 'begin_date': r.get('begin_date') or '', 'end_date': '',
                'desde': timezone.localtime(desde).strftime('%d/%m/%Y') if desde else '',
                'hasta': timezone.localtime(creado).strftime('%d/%m %H:%M'),
                'pedido_a': timezone.localtime(creado).strftime('%H:%M'),
                'estado_texto': texto_estado_reporte(r.get('estado'))}
    return None


def _pedido_en_curso(cfg, reportes, pedido_pagina=None, pedido_mp=None):
    """Qué pasó con el reporte que se le pidió antes a MP para esta cuenta.

    El pedido se busca en la caché del proceso y, si no está (otro worker de
    gunicorn), en el que devuelve la página. Devuelve
    (pendiente, extra, resuelto, fallido):
    - pendiente: el pedido, mientras MP lo genera o hasta aplicar su archivo.
    - extra: el reporte que MP ya terminó y /search todavía no lista; se procesa igual.
    - resuelto: el pedido llegó; aunque haya quedado viejo porque MP tardó, no
      se pide otro en esta pasada (eso encadenaba pedidos sin fin).
    - fallido: MP no pudo generarlo.
    """
    clave = _CLAVE_PEDIDO.format(cfg.id)
    pedido = cache.get(clave) or _pedido_valido(pedido_pagina) or pedido_mp
    if not pedido:
        return None, None, False, False
    fin_pedido = _instante(pedido.get('end_date'))
    if fin_pedido is not None and any(
            f is not None and f >= fin_pedido - timedelta(minutes=1)
            for f in (_fin_de_reporte(r) for r in reportes)):
        cache.delete(clave)
        return None, None, True, False
    archivo = pedido.get('file_name') or ''
    if not archivo and pedido.get('task_id'):
        try:
            estado = estado_tarea_liberaciones(cfg, pedido['task_id'])
        except mp.MercadoPagoError:
            return pedido, None, False, False
        if estado['fallido']:
            cache.delete(clave)
            return None, None, False, True
        if estado['listo']:
            archivo = estado['file_name']
        pedido = dict(pedido, estado_texto=estado.get('estado_texto') or '')
    if not archivo:
        return pedido, None, False, False
    pedido = dict(pedido, file_name=archivo)
    cache.set(clave, pedido, 60 * 20)   # hasta aplicarlo
    extra = {'file_name': archivo, 'begin_date': pedido.get('begin_date') or '',
             'end_date': pedido.get('end_date') or '', 'creado': '', 'origen': 'manual',
             'estado': 'processed'}
    return pedido, extra, True, False


def _pedir_si_hace_falta(cfg, reportes, fila, ahora=None):
    """Si el reporte más nuevo de la cuenta quedó viejo, pide uno hasta ahora.

    Sin esto «Detectar retiros» solo miraba reportes ya generados: con el
    reporte automático apagado en MP, un retiro recién hecho no aparecía nunca.
    Deja en `fila['pedido']` el pedido (para que la página lo espere).
    """
    ahora = ahora or timezone.now()
    ultimo_fin = max((f for f in (_fin_de_reporte(r) for r in reportes) if f), default=None)
    if ultimo_fin is not None and ultimo_fin >= ahora - timedelta(minutes=FRESCURA_REPORTE_MIN):
        return
    try:
        pedido = pedir_reporte_liberaciones(cfg, inicio_reporte_cuenta(cfg, ahora),
                                            timezone.localtime(ahora).date())
    except mp.MercadoPagoError as e:
        fila['error_pedido'] = e.mensaje
        return
    fin = _instante(pedido.get('end_date'))
    pedido['hasta'] = timezone.localtime(fin).strftime('%d/%m %H:%M') if fin else ''
    pedido['pedido_a'] = timezone.localtime(ahora).strftime('%H:%M')
    pedido['estado_texto'] = 'en cola en Mercado Pago'
    desde = _instante(pedido.get('begin_date'))
    pedido['desde'] = timezone.localtime(desde).strftime('%d/%m/%Y') if desde else ''
    cache.set(_CLAVE_PEDIDO.format(cfg.id), pedido, 60 * 20)
    fila['pedido'] = pedido


def detectar_retiros(presupuesto_seg=45, pedir=True, pedir_ids=(), pedidos=None):
    """Recorre TODAS las cuentas, aplica los reportes de Liberaciones nuevos y
    devuelve los retiros encontrados. Es lo que hace el botón «Detectar
    retiros» y el comando con --todas (para cron).

    - Solo aplica reportes que traen retiros (uno sin retiros no tiene nada que
      asociar) y que no estaban aplicados.
    - Antes de aplicar completa el N° de operación de los cobros del POS para
      que las ventas se crucen. Si no alcanzó el tiempo (o MP falló), el reporte
      se aplica igual (el retiro aparece) pero NO se marca aplicado: la próxima
      pasada lo rehace con más ventas cruzadas. Reprocesar es idempotente.
    - Si el reporte más nuevo de la cuenta quedó viejo y `pedir=True` (o la
      cuenta viene en `pedir_ids`), le pide a MP uno hasta ahora y lo devuelve
      en `fila['pedido']`: MP tarda unos minutos y la página vuelve a detectar
      (con `pedir=False` y los pedidos que espera en `pedidos`) cuando termina.
      Si no se alcanzó a pedir (faltó tiempo), `fila['pedir_pendiente']`.
    - Cada retiro trae `nuevo`: False si ya estaba registrado (el reporte pedido
      parte el día del último retiro, así que lo vuelve a traer).
    - Tiene techo de tiempo: lo que no alcance queda para la próxima pasada.
    """
    import time as _time
    fin = _time.monotonic() + presupuesto_seg
    aplicados = reportes_aplicados()
    salida = []
    for info in cuentas_mp():
        cfg = info['config']
        fila = {'cuenta': info['nombre'], 'rut': info['rut'], 'config_id': cfg.id,
                'cajas': ', '.join(sorted(info['cajas'])), 'reportes_revisados': 0,
                'reportes_aplicados': 0, 'retiros': [], 'por_retiro_activo': None,
                'error': '', 'falto_tiempo': False, 'incompleto': False,
                'pedido': None, 'error_pedido': '', 'revisado_hasta': '', 'pedir_pendiente': False}
        salida.append(fila)
        pedir_cuenta = pedir or cfg.id in pedir_ids
        pedido_pagina = (pedidos or {}).get(cfg.id)
        if _time.monotonic() > fin:
            fila['falto_tiempo'] = True
            fila['pedir_pendiente'] = pedir_cuenta
            if pedido_pagina:
                fila['pedido'] = _pedido_valido(pedido_pagina)
            continue
        try:
            cfg_reporte = leer_config_reporte(cfg)
            fila['por_retiro_activo'] = bool(cfg_reporte and cfg_reporte.get('execute_after_withdrawal'))
        except mp.MercadoPagoError:
            pass
        try:
            todos = listar_reportes_liberaciones(cfg, limite=10, incluir_pendientes=True)
        except mp.MercadoPagoError as e:
            fila['error'] = e.mensaje
            continue
        reportes = [r for r in todos if r['file_name']]
        pedido_mp = _pedido_en_cola_mp([r for r in todos if not r['file_name']])
        pendiente, extra, resuelto, fallido = _pedido_en_curso(cfg, reportes, pedido_pagina, pedido_mp)
        if extra and extra['file_name'] not in {r['file_name'] for r in reportes}:
            reportes = [extra] + reportes
        # Del más antiguo al más nuevo: los retiros se reconstruyen en orden.
        nuevos = [r for r in reportes if r['file_name'] not in aplicados
                  and not cache.get(_CLAVE_SIN_RETIROS.format(r['file_name']))]
        for rep in reversed(nuevos):
            if _time.monotonic() > fin:
                fila['falto_tiempo'] = True
                break
            try:
                filas = leer_csv(descargar_reporte_liberaciones(cfg, rep['file_name']))
            except mp.MercadoPagoError as e:
                fila['error'] = e.mensaje
                continue
            fila['reportes_revisados'] += 1
            previa = procesar_reporte_liberaciones(filas, cfg, aplicar=False, archivo=rep['file_name'])
            if not previa['retiros']:
                # Sin retiros no hay nada que asociar: no se vuelve a bajar en un día.
                cache.set(_CLAVE_SIN_RETIROS.format(rep['file_name']), 1, 60 * 60 * 24)
                continue
            completo = True
            dias = dias_para_completar(cfg, previa, importar=True)
            if dias:
                restante = max(5, int(fin - _time.monotonic()) - 5)
                comp = completar_numeros_mp(cfg, dias, presupuesto_seg=restante, importar=True)
                completo = not comp['sin_tiempo'] and not comp.get('fallidos')
            res = procesar_reporte_liberaciones(filas, cfg, aplicar=True,
                                                archivo=rep['file_name'] if completo else '')
            fila['reportes_aplicados'] += 1
            fila['retiros'].extend(res['retiros'])
            if completo:
                aplicados.add(rep['file_name'])
            else:
                fila['falto_tiempo'] = fila['incompleto'] = True
                break
        ultimo_fin = max((f for f in (_fin_de_reporte(r) for r in reportes) if f), default=None)
        if ultimo_fin is not None:
            fila['revisado_hasta'] = timezone.localtime(ultimo_fin).strftime('%d/%m %H:%M')
        if extra and (extra['file_name'] in aplicados
                      or cache.get(_CLAVE_SIN_RETIROS.format(extra['file_name']))):
            cache.delete(_CLAVE_PEDIDO.format(cfg.id))   # ya se aplicó entero
            pendiente = None
        if pendiente:
            fila['pedido'] = pendiente
        elif pedir_cuenta and not resuelto:
            if fila['incompleto'] or _time.monotonic() > fin:
                fila['pedir_pendiente'] = True   # la página lo pide en la vuelta siguiente
            else:
                _pedir_si_hace_falta(cfg, reportes, fila)
        if fallido and not fila['pedido']:
            fila['error_pedido'] = ('Mercado Pago no pudo generar el reporte pedido. '
                                    'Vuelva a apretar «Detectar retiros».')
    return salida


def detalle_retiro(retiro):
    """Qué se llevó un retiro: las ventas del POS amarradas, los pagos «MP
    manual» y lo que el reporte no pudo explicar con ventas (saldo anterior,
    online). Una venta partida entre dos retiros aparece en el que se llevó su
    último peso."""
    raw = retiro.raw_reporte if isinstance(retiro.raw_reporte, dict) else {}
    netos = raw.get('netos') or {}
    ids = [str(i) for i in (raw.get('pagos') or []) if str(i).isdigit()]
    # Amarradas + las que este retiro ya contó por su N° y se registraron después
    # (p. ej. un pago «MP manual» importado): todavía sin retiro.
    trxs = list(TransaccionMercadoPago.objects
                .filter(Q(retiro=retiro) | Q(retiro__isnull=True, tipo='VENTA', payment_id_mp__in=ids))
                .select_related('sucursal', 'config', 'ticket', 'detalle_pago__ticket')
                .order_by('money_release_date', 'creado_en')) if ids else list(
        retiro.transacciones.select_related('sucursal', 'config', 'ticket', 'detalle_pago__ticket')
        .order_by('money_release_date', 'creado_en'))
    tickets = _ticket_de_cobros(trxs)
    # «MP manual» con la misma regla que al procesar: solo si ninguna transacción
    # tiene ese N° (si no, la venta del POS salía dos veces).
    if ids:
        con_trx = {str(x) for par in (TransaccionMercadoPago.objects
                                      .filter(Q(payment_id_mp__in=ids) | Q(payment_id__in=ids))
                                      .values_list('payment_id_mp', 'payment_id')) for x in par if x}
        ids = [i for i in ids if i not in con_trx]
    lineas = {}
    if ids:
        for pago in (TicketDetallePago.objects
                     .filter(Q(origen_pago='MANUAL') | Q(origen_pago__isnull=True),
                             metodo_pago__in=METODOS_MP, voucher__in=ids, transacciones_mercadopago__isnull=True)
                     .exclude(ticket__estado='ANULADO')
                     .select_related('ticket__sucursal').order_by('creado_en')):
            lineas.setdefault(str(pago.voucher).strip(), []).append(pago)
    dtes = _dtes_por_ref(list(tickets.values()) + [p.ticket for ps in lineas.values() for p in ps])
    filas, suma_neto, bruto_sin_neto = [], 0, 0
    for t in trxs:
        neto = int(t.monto_neto if t.monto_neto is not None else t.monto)
        suma_neto += neto
        tk = tickets.get(t.id)
        documento, dte_id = _documento_de_ticket(tk, dtes)
        filas.append({
            'origen': 'MP manual' if str(t.external_reference).startswith('MANUAL-') else 'POS',
            'fecha': timezone.localtime(t.creado_en).strftime('%d/%m/%Y %H:%M'),
            'liberacion': (timezone.localtime(t.money_release_date).strftime('%d/%m/%Y')
                           if t.money_release_date else ''),
            'sucursal': t.sucursal.alias if t.sucursal_id else '',
            'caja': t.config.nombre if t.config_id else '',
            'ticket': tk.correlativo if tk else (t.correlativo_ticket or ''),
            'documento': documento, 'dte_id': dte_id,
            'bruto': int(t.monto), 'comision': int(t.monto) - neto, 'neto': neto,
            'payment_id_mp': t.payment_id_mp, 'estado': t.estado,
        })
    for voucher, pagos in lineas.items():
        # Mismo N° en varias líneas: si son copias (mismo monto) es un solo pago;
        # si no, es un pago repartido y el bruto es la suma.
        montos = [int(p.monto or 0) for p in pagos]
        bruto = montos[0] if len(set(montos)) == 1 else sum(montos)
        neto = netos.get(voucher)
        neto = int(neto) if neto is not None else None
        if neto is not None and len(montos) > 1 and bruto < neto <= sum(montos):
            bruto = sum(montos)          # un pago repartido en líneas del mismo monto
        if neto is None:
            bruto_sin_neto += bruto
            comision = None
        else:
            suma_neto += neto
            comision = bruto - neto if neto <= bruto else None   # N° mal digitado: sin comisión
        pago = pagos[-1]
        documento, dte_id = _documento_de_ticket(pago.ticket, dtes)
        filas.append({
            'origen': 'MP manual', 'fecha': timezone.localtime(pago.creado_en).strftime('%d/%m/%Y %H:%M'),
            'liberacion': '', 'sucursal': pago.ticket.sucursal.alias if pago.ticket.sucursal_id else '',
            'caja': 'MP manual', 'ticket': ', '.join(sorted({str(p.ticket.correlativo) for p in pagos})),
            'documento': documento, 'dte_id': dte_id,
            'bruto': bruto, 'comision': comision, 'neto': neto, 'payment_id_mp': voucher, 'estado': '',
        })
    por_caja = raw.get('por_caja') or []
    no_ventas = [c for c in por_caja if isinstance(c, dict) and (
        c.get('caja') in (CAJA_SALDO_ANTERIOR, CAJA_NO_EXPLICADA)
        or str(c.get('caja', '')).startswith(('Sin caja', 'Caja MP «')))]
    sin_venta = raw.get('sin_venta')
    if sin_venta is None:   # retiros guardados antes de este dato
        sin_venta = sum(int(c.get('monto') or 0) for c in no_ventas)
    else:
        # Lo que se contó «sin venta» y ya tiene su cobro (listado arriba) no se repite.
        sin_local = raw.get('sin_local') or {}
        listados = {str(t.payment_id_mp) for t in trxs if t.payment_id_mp}
        sin_venta = max(0, int(sin_venta) - sum(int(v) for k, v in sin_local.items() if k in listados))
    return {
        'withdrawal_id': retiro.withdrawal_id, 'fecha': retiro.fecha.strftime('%d/%m/%Y'),
        'monto': retiro.monto, 'estado': retiro.estado, 'detalle': retiro.detalle_diferencia or '',
        'por_caja': por_caja, 'filas': filas, 'suma_neto': suma_neto,
        'bruto': sum(f['bruto'] for f in filas), 'comision': sum(f['comision'] or 0 for f in filas),
        'bruto_sin_neto': bruto_sin_neto, 'no_ventas': no_ventas, 'sin_venta': int(sin_venta or 0),
    }


def cuadre_por_sucursal(desde, hasta, sucursal_id=None, configs=None):
    """Por tienda, los cobros MP del período: vendido (bruto), devuelto,
    comisión, neto y dónde está ese neto: ya en el banco (un retiro se lo
    llevó), por liberar (MP aún no lo suelta) o disponible en MP."""
    d, h = rango_fechas(desde, hasta)
    ahora = timezone.now()
    qs = (TransaccionMercadoPago.objects
          .filter(tipo='VENTA', estado__in=('APROBADA', 'DEVUELTA', 'CONTRACARGO'),
                  creado_en__date__gte=d, creado_en__date__lte=h)
          .exclude(correlativo_ticket__startswith='PRUEBA-')
          .select_related('sucursal'))
    if sucursal_id:
        qs = qs.filter(sucursal_id=int(sucursal_id))
    if configs is not None:
        qs = qs.filter(config_id__in=list(configs))
    filas = {}
    campos = ('cobros', 'vendido', 'devuelto', 'comision', 'neto', 'en_banco', 'por_liberar', 'en_mp',
              'sin_comision')
    for t in qs:
        f = filas.setdefault(t.sucursal_id, dict({'sucursal': t.sucursal.alias if t.sucursal_id else ''},
                                                 **{c: 0 for c in campos}))
        f['cobros'] += 1
        f['vendido'] += int(t.monto)
        if t.estado != 'APROBADA':
            f['devuelto'] += int(t.monto)   # la plata volvió al cliente
            continue
        neto = int(t.monto_neto if t.monto_neto is not None else t.monto)
        if t.monto_neto is None:
            f['sin_comision'] += 1
        f['comision'] += int(t.monto) - neto
        f['neto'] += neto
        if t.retiro_id:
            f['en_banco'] += neto
        elif t.money_release_date and t.money_release_date > ahora:
            f['por_liberar'] += neto
        else:
            f['en_mp'] += neto
    # Devoluciones parciales: la venta sigue APROBADA y la devolución es otra fila.
    devoluciones = (TransaccionMercadoPago.objects
                    .filter(tipo='DEVOLUCION', estado='DEVUELTA', transaccion_origen__tipo='VENTA',
                            transaccion_origen__estado='APROBADA',
                            transaccion_origen__creado_en__date__gte=d, transaccion_origen__creado_en__date__lte=h)
                    .exclude(transaccion_origen__correlativo_ticket__startswith='PRUEBA-')
                    .select_related('transaccion_origen__retiro'))
    if sucursal_id:
        devoluciones = devoluciones.filter(transaccion_origen__sucursal_id=int(sucursal_id))
    if configs is not None:
        devoluciones = devoluciones.filter(transaccion_origen__config_id__in=list(configs))
    for dv in devoluciones:
        origen = dv.transaccion_origen
        f = filas.get(origen.sucursal_id)
        if f is None:
            continue
        f['devuelto'] += int(dv.monto)
        f['neto'] -= int(dv.monto)
        # De dónde sale: si se devolvió antes del retiro que se llevó la venta, ese
        # retiro llevó menos (sale de «en el banco»); si fue después, sale del saldo
        # de MP; si la venta aún no se libera, de lo por liberar.
        if origen.retiro_id:
            raw_r = origen.retiro.raw_reporte if isinstance(origen.retiro.raw_reporte, dict) else {}
            corte = _instante(raw_r.get('instante')) or timezone.make_aware(
                datetime.combine(origen.retiro.fecha, datetime.max.time()))
            clave = 'en_banco' if dv.creado_en <= corte else 'en_mp'
        elif origen.money_release_date and origen.money_release_date > ahora:
            clave = 'por_liberar'
        else:
            clave = 'en_mp'
        f[clave] -= int(dv.monto)
    filas = sorted(filas.values(), key=lambda f: -f['vendido'])
    total = {c: sum(f[c] for f in filas) for c in campos}
    total['sucursal'] = 'Total'
    return {'filas': filas, 'total': total, 'desde': str(d), 'hasta': str(h)}


# ─────────────── Asignación de retiros: cobros del mes ↔ retiros ───────────────

ESTADOS_ASIGNACION = {
    'EN_BANCO': 'En el banco',
    'POR_LIBERAR': 'Por liberar',
    'EN_MP': 'En Mercado Pago',
    'DEVUELTA': 'Devuelto',
    'SIN_REGISTRAR': 'MP manual sin registrar',
}


def _primer_dia_mes(valor):
    """'2026-09' → date(2026, 9, 1); None si no calza."""
    m = re.match(r'^(\d{4})-(\d{2})$', str(valor or '').strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return date(int(m.group(1)), int(m.group(2)), 1)


def _fin_de_mes(primero):
    return (primero + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _pagos_manuales_sin_registrar(desde, hasta, sucursales=None):
    """Pagos «MP manual» de ventas cerradas que todavía no tienen su cobro: su
    plata no se puede asignar a un retiro hasta registrarlos («Detectar
    retiros» los registra por su N°; si no calzan, «Asociar»)."""
    qs = (TicketDetallePago.objects
          .filter(Q(origen_pago='MANUAL') | Q(origen_pago__isnull=True),
                  metodo_pago__in=METODOS_MP, ticket__estado='PAGADO',
                  transacciones_mercadopago__isnull=True,
                  creado_en__date__gte=desde, creado_en__date__lte=hasta)
          .select_related('ticket__sucursal'))
    if sucursales is not None:
        qs = qs.filter(ticket__sucursal_id__in=list(sucursales))
    return qs


def asignaciones_mp(mes=None, configs=None, sucursal_id=None, meses=6, max_cobros=5000):
    """Cuánto de lo cobrado con Mercado Pago ya se llevó un retiro al banco.

    - `serie`: los `meses` meses hasta el elegido, con neto cobrado, ya en el
      banco, por liberar, en MP, devuelto, pagos MP manual sin registrar y % completado.
    - `retiros`: los retiros con fecha en el mes y cuánto de cada uno está
      explicado con ventas.
    - `cobros`: cada cobro del mes y dónde está su plata (con su retiro).
    `configs`: cajas de UNA cuenta MP (None = todas). `sucursal_id` filtra la tienda.
    """
    hoy = timezone.localdate()
    primero = _primer_dia_mes(mes) or hoy.replace(day=1)
    ultimo = _fin_de_mes(primero)
    ahora = timezone.now()
    sucursales = None
    if configs is not None:
        sucursales = set(MercadoPagoConfig.objects.filter(id__in=list(configs)).values_list('sucursal_id', flat=True))
    if sucursal_id:
        sucursales = {int(sucursal_id)} if sucursales is None else (sucursales & {int(sucursal_id)})

    # ── Avance por mes ──
    serie = []
    m = primero
    for _ in range(max(1, int(meses))):
        fin = _fin_de_mes(m)
        tot = cuadre_por_sucursal(str(m), str(fin), sucursal_id=sucursal_id, configs=configs)['total']
        manuales = _pagos_manuales_sin_registrar(m, fin, sucursales)
        agg = manuales.aggregate(n=Count('id'), s=Sum('monto'))
        tot = {k: v for k, v in tot.items() if k != 'sucursal'}
        tot.update({
            'mes': m.strftime('%Y-%m'),
            'manual_sin_registrar': int(agg['s'] or 0), 'manual_sin_registrar_n': agg['n'] or 0,
            # % de lo cobrado (neto) que un retiro ya se llevó al banco
            'pct': round(100 * tot['en_banco'] / tot['neto']) if tot['neto'] > 0 else None,
        })
        serie.append(tot)
        m = (m - timedelta(days=1)).replace(day=1)
    serie.reverse()

    # ── Retiros del mes ──
    retiros_qs = RetiroMercadoPago.objects.filter(fecha__gte=primero, fecha__lte=ultimo)
    if configs is not None:
        retiros_qs = retiros_qs.filter(config_id__in=list(configs))
    elif sucursales is not None:
        cuentas = set()
        for cfg in MercadoPagoConfig.objects.filter(sucursal_id__in=list(sucursales)).select_related('sucursal', 'cuenta'):
            cuentas.update(_configs_de_la_cuenta(cfg))
        retiros_qs = retiros_qs.filter(config_id__in=list(cuentas))
    alias = {}
    if sucursal_id:
        from app.models import Sucursal
        alias_tienda = Sucursal.objects.filter(pk=int(sucursal_id)).values_list('alias', flat=True).first() or ''
    else:
        alias_tienda = ''
    retiros = []
    for r in (retiros_qs.select_related('config__sucursal__empresa', 'config__cuenta__empresa')
              .annotate(n_trx=Count('transacciones'),
                        neto_trx=Sum(Coalesce('transacciones__monto_neto', 'transacciones__monto')))
              .order_by('-fecha', '-id')):
        raw = r.raw_reporte if isinstance(r.raw_reporte, dict) else {}
        por_caja = raw.get('por_caja') or []
        if 'sin_venta' in raw:
            sin_venta = int(raw.get('sin_venta') or 0)
        else:
            sin_venta = sum(int(c.get('monto') or 0) for c in por_caja if isinstance(c, dict) and (
                c.get('caja') in (CAJA_SALDO_ANTERIOR, CAJA_NO_EXPLICADA)
                or str(c.get('caja', '')).startswith(('Sin caja', 'Caja MP «'))))
        if not por_caja:
            sin_venta = r.monto   # guardado antes del desglose: no se sabe qué se llevó
        explicado = max(0, r.monto - sin_venta)
        if r.config_id not in alias:
            cuenta = _cuenta_efectiva(r.config)
            empresa = cuenta.empresa if cuenta else (r.config.sucursal.empresa if r.config.sucursal_id else None)
            alias[r.config_id] = getattr(empresa, 'nombre', '') or ''
        instante = _instante(raw.get('instante'))
        retiros.append({
            'withdrawal_id': r.withdrawal_id, 'fecha': r.fecha.strftime('%d/%m/%Y'),
            'hora': timezone.localtime(instante).strftime('%H:%M') if instante else '',
            'cuenta': alias[r.config_id], 'monto': r.monto, 'estado': r.estado,
            'visto_en_cartola': r.visto_en_cartola,
            'ventas': r.n_trx, 'neto_ventas': int(r.neto_trx or 0),
            'explicado': explicado, 'sin_venta': sin_venta,
            'pct_explicado': round(100 * explicado / r.monto) if r.monto else None,
            # con una tienda elegida: cuánto de este retiro salió de ella
            'de_la_tienda': (sum(int(c.get('monto') or 0) for c in por_caja if isinstance(c, dict)
                                 and str(c.get('caja', '')).startswith(f'{alias_tienda} · '))
                             if alias_tienda else None),
            'por_caja': por_caja,
        })

    # ── Cobros del mes ──
    qs = (TransaccionMercadoPago.objects
          .filter(tipo='VENTA', estado__in=('APROBADA', 'DEVUELTA', 'CONTRACARGO'),
                  creado_en__date__gte=primero, creado_en__date__lte=ultimo)
          .exclude(correlativo_ticket__startswith='PRUEBA-')
          .select_related('sucursal', 'config', 'retiro', 'ticket', 'detalle_pago__ticket')
          .order_by('creado_en'))
    if sucursal_id:
        qs = qs.filter(sucursal_id=int(sucursal_id))
    if configs is not None:
        qs = qs.filter(config_id__in=list(configs))
    total_cobros = qs.count()
    trxs = list(qs[:max_cobros])
    manuales = list(_pagos_manuales_sin_registrar(primero, ultimo, sucursales).order_by('creado_en')[:max_cobros])
    tickets = _ticket_de_cobros(trxs)
    dtes = _dtes_por_ref(list(tickets.values()) + [p.ticket for p in manuales])
    cobros, conteo = [], {k: 0 for k in ESTADOS_ASIGNACION}
    for t in trxs:
        neto = int(t.monto_neto if t.monto_neto is not None else t.monto)
        if t.estado != 'APROBADA':
            estado = 'DEVUELTA'
        elif t.retiro_id:
            estado = 'EN_BANCO'
        elif t.money_release_date and t.money_release_date > ahora:
            estado = 'POR_LIBERAR'
        else:
            estado = 'EN_MP'
        conteo[estado] += 1
        tk = tickets.get(t.id)
        documento, _dte_id = _documento_de_ticket(tk, dtes)
        ref = str(t.external_reference or '')
        cobros.append({
            'fecha': timezone.localtime(t.creado_en).strftime('%d/%m/%Y %H:%M'),
            'sucursal': t.sucursal.alias if t.sucursal_id else '',
            'caja': t.config.nombre if t.config_id else '',
            'ticket': tk.correlativo if tk else (t.correlativo_ticket or ''),
            'documento': documento,
            'origen': 'MP manual' if ref.startswith('MANUAL-') else ('Tu caja' if 'DIRECTO-' in ref else 'POS'),
            'bruto': int(t.monto), 'neto': neto, 'payment_id_mp': t.payment_id_mp,
            'liberacion': (timezone.localtime(t.money_release_date).strftime('%d/%m/%Y')
                           if t.money_release_date else ''),
            'estado': estado,
            'retiro': t.retiro.withdrawal_id if t.retiro_id else '',
            'retiro_fecha': t.retiro.fecha.strftime('%d/%m/%Y') if t.retiro_id else '',
        })
    for pago in manuales:
        conteo['SIN_REGISTRAR'] += 1
        documento, _dte_id = _documento_de_ticket(pago.ticket, dtes)
        cobros.append({
            'fecha': timezone.localtime(pago.creado_en).strftime('%d/%m/%Y %H:%M'),
            'sucursal': pago.ticket.sucursal.alias if pago.ticket.sucursal_id else '',
            'caja': 'MP manual', 'ticket': pago.ticket.correlativo, 'documento': documento,
            'origen': 'MP manual', 'bruto': int(pago.monto or 0), 'neto': None,
            'payment_id_mp': str(pago.voucher or '').strip(), 'liberacion': '',
            'estado': 'SIN_REGISTRAR', 'retiro': '', 'retiro_fecha': '',
        })
    return {
        'mes': primero.strftime('%Y-%m'), 'desde': str(primero), 'hasta': str(ultimo),
        'serie': serie, 'resumen': serie[-1] if serie else {}, 'retiros': retiros,
        'cobros': cobros, 'conteo': conteo, 'total_cobros': total_cobros,
        'recortado': total_cobros > len(trxs), 'estados': ESTADOS_ASIGNACION,
    }


# ───────────────────────────── 3. Cartola del banco ───────────────────────────

def leer_cartola(contenido):
    """CSV del banco → [(fecha, monto_abono, descripcion)]. Toma la columna de
    abonos si existe (Abonos / Depósitos / Crédito), si no la de monto."""
    movimientos = []
    for fila in leer_csv(contenido):
        claves = list(fila)
        col_fecha = next((k for k in claves if 'FECHA' in k or k == 'DATE'), None)
        col_abono = next((k for k in claves if any(p in k for p in ('ABONO', 'DEPOSITO', 'CREDITO', 'HABER'))), None)
        col_monto = col_abono or next((k for k in claves if 'MONTO' in k or 'IMPORTE' in k or k == 'AMOUNT'), None)
        col_desc = next((k for k in claves if 'DESCRIP' in k or 'GLOSA' in k or 'DETALLE' in k), None)
        if not col_fecha or not col_monto:
            continue
        fecha = _fecha_libre(fila.get(col_fecha))
        monto = _monto(fila.get(col_monto))
        if fecha is None or monto <= 0:
            continue
        movimientos.append((fecha, monto, fila.get(col_desc, '') if col_desc else ''))
    return movimientos


def conciliar_cartola(movimientos, tolerancia_dias=3, aplicar=False):
    """Marca `visto_en_cartola` en los retiros que calzan con un abono del banco.

    Calce: mismo monto y fecha del abono entre la del retiro y `tolerancia_dias`
    después (el banco abona el mismo día o el hábil siguiente). Cada abono se
    usa una sola vez; ante empate gana el que menciona Mercado Pago.
    """
    if not movimientos:
        return {'calzados': [], 'sin_calce_banco': [], 'retiros_sin_abono': []}
    desde = min(m[0] for m in movimientos) - timedelta(days=tolerancia_dias)
    hasta = max(m[0] for m in movimientos)
    retiros = list(RetiroMercadoPago.objects.filter(fecha__gte=desde, fecha__lte=hasta)
                   .order_by('fecha'))
    usados = set()
    calzados = []
    for ret in retiros:
        candidatos = [
            (i, m) for i, m in enumerate(movimientos)
            if i not in usados and m[1] == ret.monto
            and 0 <= (m[0] - ret.fecha).days <= tolerancia_dias
        ]
        if not candidatos:
            continue
        candidatos.sort(key=lambda c: (0 if 'MERCADO' in _sin_tildes(c[1][2]) else 1,
                                       (c[1][0] - ret.fecha).days))
        i, mov = candidatos[0]
        usados.add(i)
        calzados.append({'withdrawal_id': ret.withdrawal_id, 'fecha_retiro': str(ret.fecha),
                         'fecha_banco': str(mov[0]), 'monto': ret.monto, 'glosa': mov[2][:80]})
        if aplicar and not ret.visto_en_cartola:
            ret.visto_en_cartola = True
            ret.save(update_fields=['visto_en_cartola', 'actualizado_en'])
    retiros_calzados = {c['withdrawal_id'] for c in calzados}
    return {
        'calzados': calzados,
        'sin_calce_banco': [
            {'fecha': str(m[0]), 'monto': m[1], 'glosa': m[2][:80]}
            for i, m in enumerate(movimientos)
            if i not in usados and 'MERCADO' in _sin_tildes(m[2])
        ],
        'retiros_sin_abono': [
            {'withdrawal_id': r.withdrawal_id, 'fecha': str(r.fecha), 'monto': r.monto}
            for r in retiros if r.withdrawal_id not in retiros_calzados and not r.visto_en_cartola
        ],
    }


# ─────────────────────────── 4. Cruce contra la API de MP ─────────────────────

def diferencias_contra_mp(desde, hasta, sucursal_id=None):
    """Cruza lo que MP cobró (payments/search, por cuenta y día) con el sistema.

    Devuelve:
      - `cajas`: por caja y día, sistema vs MP (reutiliza conciliar_cierre_mp)
        junto al teórico MP y el cierre físico del arqueo de esa sucursal/día.
      - `sin_registro`: pagos aprobados en MP que el sistema no tiene (ni como
        cobro integrado ni como pago manual con ese N° de operación).
      - `manuales_sin_pago`: pagos "MP manual" del POS cuyo N° no aparece en MP.
      - `errores`: cuentas que no se pudieron consultar.
    Tope: 7 días (cada día es 1+ llamadas por cuenta).
    """
    d, h = rango_fechas(desde, hasta, dias_defecto=1, max_dias=7)
    configs = list(MercadoPagoConfig.objects.select_related('sucursal', 'cuenta')
                   .filter(habilitado=True))
    if sucursal_id:
        configs = [c for c in configs if c.sucursal_id == int(sucursal_id)]

    pagos_por_token_dia = {}
    config_de_token = {}
    errores = []
    cajas = []
    ids_mp_vistos = set()
    sin_registro = []
    dias = [d + timedelta(days=i) for i in range((h - d).days + 1)]

    for cfg in configs:
        try:
            token = mp._token(cfg)
        except mp.MercadoPagoError as e:
            errores.append({'caja': cfg.nombre, 'sucursal': cfg.sucursal.alias, 'error': e.mensaje})
            continue
        config_de_token.setdefault(token, cfg)
        for dia in dias:
            clave = (token, dia)
            if clave not in pagos_por_token_dia:
                try:
                    pagos_por_token_dia[clave] = mp.buscar_pagos_dia(cfg, dia)
                except mp.MercadoPagoError as e:
                    pagos_por_token_dia[clave] = None
                    errores.append({'caja': cfg.nombre, 'sucursal': cfg.sucursal.alias,
                                    'dia': str(dia), 'error': e.mensaje})
            pagos = pagos_por_token_dia[clave]
            if pagos is None:
                continue
            res = mp.conciliar_cierre_mp(cfg, dia, pagos=pagos)
            arqueo = (ArqueoCaja.objects.filter(sucursal_id=cfg.sucursal_id, fecha_arqueo=dia)
                      .order_by('-id').values('total_mercadopago_pos_teorico', 'cierre_mp_fisico')
                      .first()) or {}
            cajas.append({
                'dia': str(dia),
                'sucursal': cfg.sucursal.alias,
                'caja': cfg.nombre,
                'ok': res.get('ok', False),
                'error': res.get('error', ''),
                'sistema': res.get('sistema_total', 0),
                'mp': res.get('mp_total', 0),
                'diferencia': res.get('diferencia', 0),
                'cuadra': res.get('cuadra', False),
                'sin_registro': len(res.get('sin_registro') or []),
                'sin_confirmar': len(res.get('sin_confirmar') or []),
                'arqueo_teorico_mp': int(arqueo.get('total_mercadopago_pos_teorico') or 0),
                'arqueo_cierre_fisico': int(arqueo.get('cierre_mp_fisico') or 0),
            })

    # Pagos de MP (todas las cuentas, sin duplicar) vs registros locales.
    todos = {}
    cfg_de_pago = {}
    for (tok, _dia), pagos in pagos_por_token_dia.items():
        for p in pagos or []:
            todos[str(p.get('id'))] = p
            cfg_de_pago.setdefault(str(p.get('id')), config_de_token.get(tok))
    ids_mp_vistos = set(todos)
    refs = {str(p.get('external_reference') or '') for p in todos.values()} - {''}
    refs_locales = set(TransaccionMercadoPago.objects.filter(external_reference__in=refs)
                       .values_list('external_reference', flat=True))
    ids_locales = set(filter(None, TransaccionMercadoPago.objects.filter(
        Q(payment_id_mp__in=ids_mp_vistos) | Q(payment_id__in=ids_mp_vistos))
        .values_list('payment_id_mp', flat=True)))
    vouchers_manuales = {}
    for pago in (TicketDetallePago.objects
                 .filter(metodo_pago__in=METODOS_MP,
                         creado_en__date__gte=d - timedelta(days=1),
                         creado_en__date__lte=h + timedelta(days=1))
                 .select_related('ticket', 'ticket__sucursal')):
        voucher = re.sub(r'\D', '', pago.voucher or '')
        if voucher:
            vouchers_manuales[voucher] = pago
    for pid, p in todos.items():
        if str(p.get('status') or '') != 'approved':
            continue
        ref = str(p.get('external_reference') or '')
        if ref in refs_locales or pid in ids_locales or pid in vouchers_manuales:
            continue
        cfg_pago = cfg_de_pago.get(pid)
        sucursal_ref = mp._sucursal_de_referencia(ref) if ref else None
        sin_registro.append({
            'tipo': 'SIN_REGISTRO',
            'payment_id': pid,
            'fecha': str(p.get('date_created') or '')[:16].replace('T', ' '),
            'monto': _monto(p.get('transaction_amount')),
            'medio': mp.etiqueta_medio_mp(p.get('payment_type_id')),
            'payment_type': str(p.get('payment_type_id') or ''),
            'external_reference': ref,
            'descripcion': str(p.get('description') or '')[:60],
            'sucursal_ref': sucursal_ref,
            # Cuenta por la que se leyó el pago (MP no dice la caja: si el
            # external_reference es propio, sucursal_ref sí la identifica).
            'config_id': cfg_pago.id if cfg_pago else None,
            'caja': cfg_pago.nombre if cfg_pago else '',
            'sucursal_id': sucursal_ref or (cfg_pago.sucursal_id if cfg_pago else None),
        })

    manuales_sin_pago = []
    for pago in (TicketDetallePago.objects
                 .filter(metodo_pago__in=METODOS_MP, origen_pago='MANUAL', ticket__estado='PAGADO',
                         creado_en__date__gte=d, creado_en__date__lte=h)
                 .select_related('ticket', 'ticket__sucursal')):
        if sucursal_id and pago.ticket.sucursal_id != int(sucursal_id):
            continue
        voucher = re.sub(r'\D', '', pago.voucher or '')
        if voucher and voucher in ids_mp_vistos:
            continue
        manuales_sin_pago.append({
            'tipo': 'MANUAL_SIN_PAGO',
            'pago_id': pago.id,
            'ticket_id': pago.ticket_id,
            'sucursal_id': pago.ticket.sucursal_id,
            'metodo_pago': pago.metodo_pago,
            'ticket': pago.ticket.correlativo,
            'sucursal': pago.ticket.sucursal.alias,
            'fecha': timezone.localtime(pago.creado_en).strftime('%Y-%m-%d %H:%M'),
            'monto': pago.monto,
            'voucher': pago.voucher or '',
            'motivo': 'sin N° de operación' if not voucher else 'N° no encontrado en Mercado Pago',
        })

    return {
        'desde': str(d), 'hasta': str(h),
        'cajas': cajas,
        'sin_registro': sorted(sin_registro, key=lambda x: x['fecha']),
        'manuales_sin_pago': manuales_sin_pago,
        'errores': errores,
        'pagos_mp_consultados': len(todos),
    }
