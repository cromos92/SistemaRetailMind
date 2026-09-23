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
from datetime import date, datetime, timedelta, timezone as dt_timezone
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


def _configs_de_la_cuenta(config):
    """Cajas que comparten la cuenta MP de `config` (el reporte es por cuenta)."""
    if getattr(config, 'cuenta_id', None):
        return list(MercadoPagoConfig.objects.filter(cuenta_id=config.cuenta_id).values_list('id', flat=True))
    return [config.id]


def _remanente_previo(config, antes_de, monto, excluir_ids=()):
    """Ventas liberadas ANTES del reporte y todavía sin retiro que explican el
    saldo inicial: las más recientes hacia atrás hasta cubrir `monto`.

    Sin esto, lo que quedó en MP después de un retiro parcial era un saldo
    anónimo en el reporte siguiente y esas ventas nunca se asociaban al retiro
    que finalmente se las llevó.
    Devuelve [(trx, neto)] en orden de liberación (más antigua primero).
    """
    if monto <= 0 or antes_de is None:
        return []
    qs = (TransaccionMercadoPago.objects
          .filter(config_id__in=_configs_de_la_cuenta(config), tipo='VENTA', estado='APROBADA',
                  retiro__isnull=True, money_release_date__isnull=False,
                  money_release_date__lt=antes_de,
                  money_release_date__gte=antes_de - timedelta(days=90))
          .exclude(id__in=list(excluir_ids))
          .order_by('-money_release_date', '-id'))
    elegidas, suma = [], 0
    for t in qs[:500]:
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
            'debito': debito,
        })
    registros.sort(key=lambda r: r['instante'])

    ids = [r['source_id'] for r in registros if r['source_id']]
    refs = [r['external_reference'] for r in registros if r['external_reference']]
    locales = {}
    if ids or refs:
        for t in TransaccionMercadoPago.objects.filter(
                Q(payment_id_mp__in=ids) | Q(payment_id__in=ids) | Q(external_reference__in=refs)):
            for clave in (t.payment_id_mp, t.payment_id, t.external_reference):
                if clave:
                    locales[str(clave)] = t

    resultado = {'retiros': [], 'pagos_sin_local': 0, 'pagos_amarrados': 0}
    # Cola FIFO del saldo disponible: {'trx': TransaccionMercadoPago|None, 'resto': $, 'id': str}
    cola = []
    deuda = 0
    pendientes_asociar = []   # ventas agotadas por débitos que no son retiros
    sin_banco = []            # ventas devueltas: su plata no llegó al banco

    if saldo_inicial < 0:
        deuda = -saldo_inicial
    elif saldo_inicial > 0:
        previas = _remanente_previo(
            config, registros[0]['instante'] if registros else None, saldo_inicial,
            excluir_ids={t.id for t in locales.values()},
        )
        anonimo = saldo_inicial - sum(n for _t, n in previas)
        if anonimo > 0:
            cola.append({'trx': None, 'resto': anonimo, 'id': 'saldo inicial'})
        for trx, neto in previas:
            cola.append({'trx': trx, 'resto': neto, 'id': trx.payment_id_mp or trx.external_reference})
    resultado['remanente_previo'] = sum(it['resto'] for it in cola if it['trx'] is not None)

    def _consumir(monto):
        """Saca `monto` del frente de la cola. Devuelve (faltante, ítems agotados)."""
        restante, agotados = monto, []
        while restante > 0 and cola:
            item = cola[0]
            usa = min(item['resto'], restante)
            item['resto'] -= usa
            restante -= usa
            if item['resto'] <= 0:
                agotados.append(cola.pop(0))
        return restante, agotados

    retiros_ids = []   # RetiroMercadoPago escritos en esta pasada (solo con aplicar)
    for r in registros:
        desc = r['descripcion']
        if desc in DESCRIPCIONES_RETIRO and r['debito']:
            monto = r['debito']
            faltante, agotados = _consumir(monto)
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
            withdrawal_id = r['source_id'] or f'RET-{r["instante"]:%Y%m%d%H%M}-{monto}'
            resultado['retiros'].append({
                'withdrawal_id': withdrawal_id,
                'fecha': str(timezone.localtime(r['instante']).date()),
                'hora': timezone.localtime(r['instante']).strftime('%H:%M'),
                'monto': monto,
                'pagos': len(agotados),
                'pagos_pos': len(trxs),
                'suma_pagos': monto - faltante,
                'quedan_disponibles': quedan,
                'estado': estado,
                'detalle': detalle,
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
                    raw_nuevo = {'archivos': sorted(archivos),
                                 'pagos': [it['id'] for it in agotados][:500]}
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
            # 1) La misma operación (devolución de esa venta): no llega al banco.
            for it in list(cola):
                if r['source_id'] and it['id'] == r['source_id'] and it['trx'] is not None:
                    usa = min(it['resto'], debito)
                    it['resto'] -= usa
                    debito -= usa
                    if it['resto'] <= 0:
                        cola.remove(it)
                        sin_banco.append(it['trx'])
                    break
            # 2) El resto sale del frente; esas ventas van con el retiro siguiente.
            if debito:
                faltante, agotados = _consumir(debito)
                pendientes_asociar.extend(agotados)
                deuda += faltante
            continue
        # Crédito: primero paga la deuda (saldo negativo), después entra a la cola.
        es_pago = desc in DESCRIPCIONES_PAGO
        trx = (locales.get(r['source_id']) or locales.get(r['external_reference'])) if es_pago else None
        if es_pago and trx is None:
            resultado['pagos_sin_local'] += 1
        if deuda:
            usa = min(deuda, neto)
            deuda -= usa
            neto -= usa
            if not neto:
                continue
        cola.append({'trx': trx, 'resto': neto, 'id': r['source_id']})

    resultado['liberado_sin_retirar'] = sum(it['resto'] for it in cola)
    resultado['deuda'] = deuda

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
    ahora = timezone.now() - timedelta(minutes=5)
    if fin > ahora:
        fin = ahora
    if fin - inicio < timedelta(days=1):
        inicio = fin - timedelta(days=1)
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
    return {'estado': estado, 'file_name': data.get('file_name') or '',
            'listo': estado == 'processed' and bool(data.get('file_name')),
            'fallido': estado == 'failed'}


def _normalizar_reporte(item):
    return {
        'file_name': item.get('file_name') or '',
        'begin_date': str(item.get('begin_date') or ''),
        'end_date': str(item.get('end_date') or ''),
        'creado': str(item.get('date_created') or item.get('generation_date')
                      or item.get('last_modified') or ''),
        'origen': str(item.get('created_from') or ''),
        'estado': str(item.get('status') or ''),
    }


def listar_reportes_liberaciones(config, limite=20):
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
