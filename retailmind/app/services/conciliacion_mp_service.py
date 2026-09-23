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
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
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
                ticket__estado='PAGADO',
                creado_en__date__gte=d, creado_en__date__lte=h)
        .select_related('ticket', 'ticket__sucursal')
        .order_by('-creado_en')
    )
    if sucursal_id:
        manuales = [p for p in manuales if p.ticket.sucursal_id == int(sucursal_id)]

    # DTE por la referencia exacta 'TICKET-<corr>' (una sola consulta).
    todos_tickets = [tk for tk in tickets.values() if tk] + [p.ticket for p in manuales]
    dtes_por_ref = {}
    if todos_tickets:
        refs = {f'TICKET-{tk.correlativo}' for tk in todos_tickets}
        sucs = {tk.sucursal_id for tk in todos_tickets}
        for dte in (Dte.objects.filter(referencias__in=refs, sucursal_id__in=sucs)
                    .exclude(descartado=True).only('id', 'tipo_documento', 'numero_documento',
                                                   'referencias', 'sucursal_id')):
            try:
                corr = int(str(dte.referencias).split('-', 1)[1])
            except (IndexError, ValueError):
                continue
            dtes_por_ref[(dte.sucursal_id, corr)] = dte

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
}
DESCRIPCIONES_RETIRO = ('payout', 'withdrawal', 'retiro', 'transferencia a cuenta bancaria')
DESCRIPCIONES_PAGO = ('payment', 'pago', 'cobro')
DESCRIPCIONES_NEGATIVAS = ('refund', 'chargeback', 'devolucion', 'contracargo', 'cancellation')


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


def procesar_reporte_liberaciones(filas, config, aplicar=False):
    """Crea/actualiza retiros y amarra cada cobro liberado a su retiro.

    `filas`: salida de ``leer_csv`` sobre el reporte de Liberaciones de MP
    (columnas DATE, SOURCE_ID, EXTERNAL_REFERENCE, RECORD_TYPE, DESCRIPTION,
    NET_CREDIT_AMOUNT, NET_DEBIT_AMOUNT; también acepta los encabezados en
    castellano del panel).

    Regla: con retiro automático, cada retiro (payout) se lleva el saldo
    disponible; los pagos liberados DESDE el retiro anterior hasta éste son los
    que lo componen. Si la suma neta de esos pagos coincide con el monto del
    retiro → CONCILIADO; si no → CON_DIFERENCIA con el detalle (saldo previo,
    ventas que no pasan por el POS, devoluciones…).

    Devuelve {'retiros': [...], 'pagos_sin_local': n, 'pagos_amarrados': n}.
    `aplicar=False` no escribe nada (dry-run).
    """
    registros = []
    for fila in filas:
        tipo = _sin_tildes(_col(fila, 'RECORD_TYPE')).lower()
        if tipo and tipo not in ('release', 'liberacion', 'liberado'):
            continue  # initial_available_balance, total, block, …
        fecha = _fecha_libre(_col(fila, 'DATE'))
        if fecha is None:
            continue
        registros.append({
            'fecha': fecha,
            'orden': _col(fila, 'DATE'),
            'source_id': str(_col(fila, 'SOURCE_ID')).split('.')[0],
            'external_reference': _col(fila, 'EXTERNAL_REFERENCE'),
            'descripcion': _sin_tildes(_col(fila, 'DESCRIPTION')).lower(),
            'credito': abs(_monto(_col(fila, 'NET_CREDIT_AMOUNT'))),
            'debito': abs(_monto(_col(fila, 'NET_DEBIT_AMOUNT'))),
        })
    registros.sort(key=lambda r: r['orden'])

    ids = [r['source_id'] for r in registros if r['source_id']]
    refs = [r['external_reference'] for r in registros if r['external_reference']]
    locales = {}
    for t in TransaccionMercadoPago.objects.filter(
            Q(payment_id_mp__in=ids) | Q(payment_id__in=ids) | Q(external_reference__in=refs)):
        for clave in (t.payment_id_mp, t.payment_id, t.external_reference):
            if clave:
                locales[str(clave)] = t

    resultado = {'retiros': [], 'pagos_sin_local': 0, 'pagos_amarrados': 0}
    pendientes = []   # (trx|None, neto_con_signo, source_id)

    def _es(desc, claves):
        return any(c in desc for c in claves)

    for r in registros:
        desc = r['descripcion']
        if _es(desc, DESCRIPCIONES_RETIRO) and r['debito']:
            suma = sum(n for _t, n, _s in pendientes)
            trxs = [t for t, _n, _s in pendientes if t is not None]
            sin_local = sum(1 for t, _n, _s in pendientes if t is None)
            dif = r['debito'] - suma
            estado = 'CONCILIADO' if dif == 0 else 'CON_DIFERENCIA'
            detalle = '' if dif == 0 else (
                f'Retiro ${r["debito"]:,} vs pagos liberados desde el retiro anterior ${suma:,} '
                f'(diferencia ${dif:,}). {sin_local} movimiento(s) sin cobro del POS '
                '(ventas online, saldo previo o ajustes de MP).'
            ).replace(',', '.')
            resultado['retiros'].append({
                'withdrawal_id': r['source_id'] or f'RET-{r["fecha"]:%Y%m%d}-{r["debito"]}',
                'fecha': str(r['fecha']),
                'monto': r['debito'],
                'pagos': len(pendientes),
                'pagos_pos': len(trxs),
                'suma_pagos': suma,
                'estado': estado,
                'detalle': detalle,
            })
            if aplicar:
                with transaction.atomic():
                    retiro, _ = RetiroMercadoPago.objects.update_or_create(
                        withdrawal_id=resultado['retiros'][-1]['withdrawal_id'],
                        defaults={
                            'config': config, 'fecha': r['fecha'], 'monto': r['debito'],
                            'estado': estado, 'detalle_diferencia': detalle,
                            'raw_reporte': {'pagos': [s for _t, _n, s in pendientes][:500]},
                        },
                    )
                    if trxs:
                        TransaccionMercadoPago.objects.filter(
                            id__in=[t.id for t in trxs]).update(retiro=retiro)
            resultado['pagos_amarrados'] += len(trxs)
            pendientes = []
            continue
        if not r['credito'] and not r['debito']:
            continue
        neto = r['credito'] - r['debito']
        if _es(desc, DESCRIPCIONES_NEGATIVAS):
            neto = -abs(neto)
        trx = locales.get(r['source_id']) or locales.get(r['external_reference'])
        if trx is None and _es(desc, DESCRIPCIONES_PAGO):
            resultado['pagos_sin_local'] += 1
        pendientes.append((trx if _es(desc, DESCRIPCIONES_PAGO) else None, neto, r['source_id']))

    resultado['liberado_sin_retirar'] = sum(n for _t, n, _s in pendientes)
    return resultado


def solicitar_y_descargar_liberaciones(config, desde, hasta, esperas=10, pausa=6):
    """Pide a MP el reporte de Liberaciones del rango y devuelve el CSV (texto).

    API de reportes de MP: POST /v1/account/release_report (crea),
    GET /v1/account/release_report/list (lista archivos), GET
    /v1/account/release_report/{file_name} (descarga). La generación es
    asíncrona: se sondea la lista hasta `esperas` veces.
    """
    import time as _time
    inicio = f'{desde:%Y-%m-%d}T00:00:00Z'
    fin = f'{hasta:%Y-%m-%d}T23:59:59Z'
    resp = mp._request(config, 'POST', '/v1/account/release_report',
                       json_body={'begin_date': inicio, 'end_date': fin}, cuenta_breaker=False)
    if resp.status_code >= 400 and resp.status_code != 409:
        mp._json_o_error(resp, 'release_report crear')
    for _ in range(esperas):
        resp = mp._request(config, 'GET', '/v1/account/release_report/list', cuenta_breaker=False)
        archivos = mp._json_o_error(resp, 'release_report list') or []
        if isinstance(archivos, dict):
            archivos = archivos.get('results') or archivos.get('files') or []
        candidatos = [
            a for a in archivos
            if str(a.get('begin_date', ''))[:10] == f'{desde:%Y-%m-%d}'
            and str(a.get('end_date', ''))[:10] == f'{hasta:%Y-%m-%d}'
            and a.get('file_name')
        ]
        if candidatos:
            nombre = sorted(candidatos, key=lambda a: str(a.get('date_created', '')))[-1]['file_name']
            resp = mp._request(config, 'GET', f'/v1/account/release_report/{nombre}',
                               cuenta_breaker=False)
            if resp.status_code >= 400:
                mp._json_o_error(resp, 'release_report descargar')
            return resp.text
        _time.sleep(pausa)
    raise mp.MercadoPagoError(
        'Mercado Pago todavía no generó el reporte de Liberaciones. Reintente en unos minutos.')


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
    for (_tok, _dia), pagos in pagos_por_token_dia.items():
        for p in pagos or []:
            todos[str(p.get('id'))] = p
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
        sin_registro.append({
            'payment_id': pid,
            'fecha': str(p.get('date_created') or '')[:16].replace('T', ' '),
            'monto': int(round(float(p.get('transaction_amount') or 0))),
            'medio': mp.etiqueta_medio_mp(p.get('payment_type_id')),
            'external_reference': ref,
            'descripcion': str(p.get('description') or '')[:60],
            'sucursal_ref': (mp._sucursal_de_referencia(ref) if ref else None),
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
