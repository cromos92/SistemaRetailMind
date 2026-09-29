# -*- coding: utf-8 -*-
"""
Mide (y opcionalmente repara) ventas POS pagadas cuyo stock no se rebajó, o se
rebajó de más.

CONTEXTO. Entre el 30-03 (commit 371c2dfc) y el 12-05-2026 (e49b9fc9), al pagar
un ticket `consumir_stock_fifo` solo consumía el lote FIFO: el stock plano y el
kardex no bajaban. El script `_reconciliacion_stock_fifo.py` del 12-05 reparó
3.354 líneas, pero descartaba el TICKET COMPLETO si alguna de sus líneas ya
tenía un egreso; como casi todos tenían la bolsa u otro par descontado por el
respaldo «FIFO no disponible», 1.148 líneas quedaron sin rebajar. Otras ~63
son del bug del prefetch de la caja (corregido el 28-09, 8b325bf8). Como a esas
ventas les falta a la vez el stock y el kardex, los dos cuadran entre sí y el
error no se ve en ningún reporte.

Verificado en producción el 29-09-2026 (solo lectura + conteo físico de 17
productos en 5 tiendas: 15 con exactamente una unidad menos que el sistema, 0
que cuadren con el sistema, 4/4 controles correctos).

QUÉ MIDE, por (ticket, talla) de tickets PAGADOS (sin tickets de cambio, sin
«tickets de referencia» de Cambios y sin líneas despachadas por guía):

    vendidas  = unidades de la línea
    rebajadas = -SUM(kardex de venta ligado al ticket) + reparaciones previas
                de este comando

    ok        rebajadas == vendidas
    sin_mov   rebajadas == 0
    parcial   0 < rebajadas < vendidas
    doble     rebajadas > vendidas
    invertida rebajadas < 0 (el kardex SUMÓ stock en una venta)

QUÉ NO TOCA (queda en el reporte con su motivo):
  - línea cuya venta fue anulada con NC (el egreso quedó CANCELADO y el
    reingreso ya volvió: el stock está bien);
  - línea ABSORBIDA: después de la venta hubo un ajuste de inventario del SKU
    (toma, corrección, ajuste rápido): el conteo ya dejó el stock en la
    realidad;
  - insumos (bolsas, envíos, cinta, papel, 45-1/45-2): su kardex no cuadra y
    no se cuentan; se revisan aparte (`--incluir-insumos` los incluye);
  - SKU que mezcla líneas sin rebajar y rebajadas de más;
  - SKU cuyo kardex NO explica el stock actual (clase C) o donde el stock ya
    está rebajado sin kardex (clase B): sin esa prueba no se repara;
  - SKU cuyo saldo, después de la venta, llegó por debajo de lo que falta
    rebajar (la unidad «fantasma» no pudo estar): contradice;
  - stock actual menor que el faltante.

PRUEBA DE SALDO (por SKU, antes de reparar). saldo = kardex legacy (MIG:<id>,
fecha <= 22-01-2026) sumado desde 0 + todos los movimientos posteriores, sin
la apertura MIGRACION_LARAVEL (salvo que el legacy al 22-01 sea negativo) y
sin ANULACION_TICKET (reingreso fantasma de tickets PENDIENTES anulados, que
nunca tocó el stock). Cuadra con el stock en el 98-99 % de los SKUs de control.
  clase A: stock == saldo            -> la venta sin kardex tampoco bajó el
                                        stock: SE REPARA
  clase B: stock == saldo - faltante -> ya bajó sin kardex: NO se toca
  clase C: otra diferencia           -> no concluyente: NO se toca

--apply repara, UNA TRANSACCIÓN POR LÍNEA (se puede cortar y retomar):
  - sin_mov / parcial en clase A: egreso CORRECCION_STOCK por el faltante
    (baja stock, consume lotes FIFO, escribe kardex).
  - doble, solo con --reparar-dobles: si el stock bajó dos veces (clase A)
    ingreso CORRECCION_STOCK por el exceso, con lote; si el doble quedó solo en
    el kardex (clase B_doble: el script del 12-05 dejó el stock en 0) se marca
    CANCELADO el movimiento del script, sin tocar el stock.
Los movimientos quedan ligados al ticket, con responsable
`reparacion_ventas_sin_rebaja`: correr el comando otra vez no repite nada.

La fecha del movimiento es la de HOY. Se usa CORRECCION_STOCK y no
VENTA_PUBLICO para que la reparación no aparezca como venta de hoy en reportes
ni en la predicción de compras. Correrlo en la consola del servidor (la
latencia desde un PC remoto multiplica el tiempo) y fuera de horario.

USO:
    # Medir (no escribe nada), detalle a CSV
    python manage.py medir_ventas_sin_rebaja --csv ventas_sin_rebaja.csv

    # Una sucursal
    python manage.py medir_ventas_sin_rebaja --sucursal NICK2

    # Reparar (tras revisar el dry-run)
    python manage.py medir_ventas_sin_rebaja --apply --csv reparacion.csv

    # Incluir también las rebajadas de más
    python manage.py medir_ventas_sin_rebaja --apply --reparar-dobles
"""
import csv
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from app.constants_kardex import CONCEPTOS_AJUSTE, CONCEPTOS_VENTA
from app.models import Movimientos_Producto, Producto_Talla, Sucursal, Ticket
from app.services import inventario_service

logger = logging.getLogger('app')

RESPONSABLE = 'reparacion_ventas_sin_rebaja'
CONCEPTO_REPARACION = 'CORRECCION_STOCK'
SCRIPT_12_05 = 'reconciliacion_script'
# Ajustes técnicos que no son un conteo de la tienda: no absorben nada.
RESPONSABLES_TECNICOS = (RESPONSABLE, SCRIPT_12_05, 'reconciliacion')
FECHA_APERTURA = date(2026, 1, 22)
# Antes de esta fecha el stock lo fijó la última carga desde Laravel (15-04) y
# Laravel seguía vendiendo en paralelo: esas líneas no son este bug.
DESDE_DEFAULT = '2026-04-17'
INSUMO = re.compile(r'BOLSA|ENVIO|CINTA|PAPEL|^45-[12]\b', re.I)

SQL_LINEAS = """
with lineas as (
    select t.id as ticket_id, t.correlativo, t.sucursal_id,
           (t.created_at at time zone 'America/Santiago') as creado,
           tp."ProductoTalla_id" as pt_id,
           sum(tp.stock) as vendidas,
           max(tp.precio) as precio
    from app_ticket t
    join app_ticket_productos tp on tp."idTicket_id" = t.id
    where t.estado = 'PAGADO'
      and coalesce(t.modulo_origen, '') <> 'CAMBIO_DEVOLUCION'
      and coalesce(t.observaciones, '') not like 'Ticket de referencia para DTE%%'
      and tp."ProductoTalla_id" is not null
      and not coalesce(tp.despachado_por_guia, false)
      and t.created_at >= %(desde)s and t.created_at < %(hasta)s
      {filtro_sucursal}
    group by 1, 2, 3, 4, 5
),
kardex as (
    select m.ticket_id, m."ProductoTalla_id" as pt_id,
           sum(case when m.concepto = any(%(conceptos_venta)s)
                     and coalesce(m.estado, '') <> 'CANCELADO' then m.cantidad else 0 end) as venta,
           sum(case when m.responsable = %(responsable)s then m.cantidad else 0 end) as reparado,
           bool_or(m.concepto = any(%(conceptos_venta)s)
                   and coalesce(m.estado, '') = 'CANCELADO') as cancelado
    from app_movimientos_producto m
    where m.ticket_id in (select ticket_id from lineas)
      and (m.concepto = any(%(conceptos_venta)s) or m.responsable = %(responsable)s)
    group by 1, 2
)
select l.ticket_id, l.correlativo, l.sucursal_id, l.creado, l.pt_id, l.vendidas, l.precio,
       coalesce(k.venta, 0) as venta, coalesce(k.reparado, 0) as reparado,
       coalesce(k.cancelado, false) as cancelado,
       exists (
           select 1 from app_movimientos_producto a
           where a."ProductoTalla_id" = l.pt_id
             and (a.concepto = any(%(conceptos_ajuste)s)
                  or coalesce(a.referencia_externa, '') like 'AJUSTE_STOCK_RAPIDO%%')
             and coalesce(a.responsable, '') <> all(%(responsables_tecnicos)s)
             and (a.fecha > l.creado::date
                  or (a.fecha = l.creado::date and a.hora > l.creado::time))
       ) as absorbida
from lineas l
left join kardex k on k.ticket_id = l.ticket_id and k.pt_id = l.pt_id
order by l.creado, l.ticket_id, l.pt_id
"""

# Saldo por SKU según el modelo validado (ver docstring). Los movimientos
# CANCELADO del script del 12-05 se excluyen porque los cancela --reparar-dobles
# precisamente para que el kardex vuelva a explicar el stock.
SQL_SALDO = """
select m."ProductoTalla_id" as pt_id,
       coalesce(sum(case when m.referencia_externa like 'MIG:%%' and m.fecha <= %(apertura)s
                         then m.cantidad end), 0) as legacy,
       coalesce(sum(case when m.referencia_externa = 'MIGRACION_LARAVEL' then m.cantidad end), 0) as apertura,
       bool_or(m.referencia_externa = 'MIGRACION_LARAVEL') as tiene_apertura,
       coalesce(sum(case when coalesce(m.referencia_externa, '') <> 'MIGRACION_LARAVEL'
                          and not (m.referencia_externa like 'MIG:%%' and m.fecha <= %(apertura)s)
                          and m.concepto <> 'ANULACION_TICKET'
                          and not (coalesce(m.estado, '') = 'CANCELADO' and m.responsable = %(script)s)
                         then m.cantidad end), 0) as posterior
from app_movimientos_producto m
where m."ProductoTalla_id" = any(%(pt_ids)s)
group by 1
"""

# Movimientos que cuentan en el saldo, posteriores a una fecha, para recorrer
# el saldo hacia atrás y detectar si llegó a ser menor que el faltante.
SQL_POSTERIORES = """
select m."ProductoTalla_id" as pt_id, m.fecha, m.hora, m.id, m.cantidad
from app_movimientos_producto m
where m."ProductoTalla_id" = any(%(pt_ids)s)
  and m.fecha >= %(desde)s
  and coalesce(m.referencia_externa, '') <> 'MIGRACION_LARAVEL'
  and not (m.referencia_externa like 'MIG:%%' and m.fecha <= %(apertura)s)
  and m.concepto <> 'ANULACION_TICKET'
  and not (coalesce(m.estado, '') = 'CANCELADO' and m.responsable = %(script)s)
order by 1, 2, 3, 4
"""


def _clasificar(vendidas, rebajadas):
    if rebajadas == vendidas:
        return 'ok'
    if rebajadas < 0:
        return 'invertida'
    if rebajadas == 0:
        return 'sin_mov'
    if rebajadas < vendidas:
        return 'parcial'
    return 'doble'


class Command(BaseCommand):
    help = 'Mide (y con --apply repara) ventas POS pagadas cuyo stock no se rebajó o se rebajó de más.'

    def add_arguments(self, parser):
        parser.add_argument('--desde', default=DESDE_DEFAULT,
                            help=f'Fecha inicial (creación del ticket), YYYY-MM-DD. Default {DESDE_DEFAULT}.')
        parser.add_argument('--hasta', default=None,
                            help='Fecha final EXCLUSIVA, YYYY-MM-DD. Default: hoy (los tickets de hoy quedan fuera).')
        parser.add_argument('--sucursal', default=None,
                            help='ID o alias de la sucursal (default: todas).')
        parser.add_argument('--csv', default=None,
                            help='Ruta de un CSV con el detalle de las líneas con diferencia.')
        parser.add_argument('--incluir-insumos', action='store_true',
                            help='Incluye bolsas, envíos, cinta y papel (por defecto se reportan y no se reparan).')
        parser.add_argument('--apply', action='store_true',
                            help='Aplica la reparación. Sin esto no escribe nada.')
        parser.add_argument('--reparar-dobles', action='store_true',
                            help='Con --apply, corrige también las líneas rebajadas de más.')

    def handle(self, *args, **opts):
        if connection.vendor != 'postgresql':
            raise CommandError('Este comando usa SQL de PostgreSQL.')

        desde = self._fecha(opts['desde'], 'desde')
        hasta = self._fecha(opts['hasta'], 'hasta') if opts['hasta'] else timezone.localdate()
        if hasta <= desde:
            raise CommandError('--hasta debe ser posterior a --desde.')
        sucursal = self._sucursal(opts['sucursal'])
        self.reparar_dobles = bool(opts['reparar_dobles'])
        self.incluir_insumos = bool(opts['incluir_insumos'])

        filas = self._leer(desde, hasta, sucursal)
        self._evaluar(filas)
        self._resumen(filas, desde, hasta, sucursal)

        if opts['csv']:
            self._csv(opts['csv'], filas)

        if not opts['apply']:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN: no se escribió nada. Revise el detalle y repita con --apply.'))
            return
        self._aplicar(filas)

    # ------------------------------------------------------------------ lectura
    @staticmethod
    def _fecha(texto, nombre):
        try:
            return datetime.strptime(texto, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            raise CommandError(f'--{nombre} inválida: {texto!r} (use YYYY-MM-DD).')

    @staticmethod
    def _sucursal(valor):
        if not valor:
            return None
        qs = Sucursal.objects.filter(id=int(valor)) if str(valor).isdigit() else \
            Sucursal.objects.filter(alias__iexact=valor)
        sucursal = qs.first()
        if sucursal is None:
            raise CommandError(f'Sucursal {valor!r} no encontrada.')
        return sucursal

    @staticmethod
    def _sql(sql, params):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columnas = [c[0] for c in cursor.description]
            return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]

    def _leer(self, desde, hasta, sucursal):
        tz = timezone.get_current_timezone()
        params = {
            'desde': timezone.make_aware(datetime.combine(desde, datetime.min.time()), tz),
            'hasta': timezone.make_aware(datetime.combine(hasta, datetime.min.time()), tz),
            'conceptos_venta': list(CONCEPTOS_VENTA),
            'conceptos_ajuste': list(CONCEPTOS_AJUSTE),
            'responsable': RESPONSABLE,
            'responsables_tecnicos': list(RESPONSABLES_TECNICOS),
        }
        filtro = ''
        if sucursal is not None:
            filtro = 'and t.sucursal_id = %(sucursal_id)s'
            params['sucursal_id'] = sucursal.id
        filas = self._sql(SQL_LINEAS.format(filtro_sucursal=filtro), params)
        for f in filas:
            f['vendidas'] = int(f['vendidas'] or 0)
            f['rebajadas'] = -int(f['venta'] or 0) - int(f['reparado'] or 0)
            f['estado'] = _clasificar(f['vendidas'], f['rebajadas'])
            f['diferencia'] = f['vendidas'] - f['rebajadas']  # >0 falta rebajar
            f['motivo'] = None      # por qué NO se repara (None = reparable)
            f['clase'] = ''
        return filas

    # ------------------------------------------------------------- evaluación
    def _evaluar(self, filas):
        """Decide línea por línea si se puede reparar y por qué no."""
        con_dif = [f for f in filas if f['estado'] != 'ok']
        if not con_dif:
            return
        pt_ids = sorted({f['pt_id'] for f in con_dif})
        self.productos = {
            p.id: p for p in Producto_Talla.objects.filter(id__in=pt_ids).select_related('producto')
        }

        # 1) motivos que dependen de la línea
        for f in con_dif:
            p = self.productos.get(f['pt_id'])
            texto = f"{getattr(p.producto, 'articulo', '')} {getattr(p.producto, 'descripcion', '')}" if p else ''
            f['sku'] = p.sku if p else ''
            f['articulo'] = (getattr(p.producto, 'articulo', '') or '').strip() if p else ''
            f['es_insumo'] = bool(INSUMO.search(texto))
            if f['estado'] == 'invertida':
                f['motivo'] = 'kardex con signo invertido: revisar a mano'
            elif f['cancelado']:
                f['motivo'] = 'venta anulada con NC: el stock ya está bien'
            elif f['absorbida']:
                f['motivo'] = 'ya corregida con un ajuste posterior de la tienda'
            elif f['es_insumo'] and not self.incluir_insumos:
                f['motivo'] = 'insumo (bolsa/envío): se revisa aparte'
            elif f['estado'] == 'doble' and not self.reparar_dobles:
                f['motivo'] = 'rebajada de más: solo con --reparar-dobles'

        # 2) por SKU: faltante, exceso, mezcla
        por_pt = defaultdict(lambda: {'F': 0, 'E': 0, 'primera': None, 'lineas': []})
        for f in con_dif:
            if f['motivo']:
                continue
            g = por_pt[f['pt_id']]
            g['lineas'].append(f)
            if f['estado'] in ('sin_mov', 'parcial'):
                g['F'] += f['diferencia']
            elif f['estado'] == 'doble':
                g['E'] += -f['diferencia']
            if g['primera'] is None or f['creado'] < g['primera']:
                g['primera'] = f['creado']
        for pt_id, g in por_pt.items():
            if g['F'] > 0 and g['E'] > 0:
                for f in g['lineas']:
                    f['motivo'] = 'el SKU mezcla ventas sin rebajar y rebajadas de más: revisar a mano'

        # 3) prueba de saldo por SKU
        candidatos = [pt for pt, g in por_pt.items() if any(not f['motivo'] for f in g['lineas'])]
        if not candidatos:
            return
        params = {'pt_ids': candidatos, 'apertura': FECHA_APERTURA, 'script': SCRIPT_12_05}
        saldos = {}
        for r in self._sql(SQL_SALDO, params):
            inicio = int(r['apertura']) if (r['tiene_apertura'] and int(r['legacy']) < 0) else int(r['legacy'])
            saldos[r['pt_id']] = inicio + int(r['posterior'])
        posteriores = defaultdict(list)
        primera_fecha = min(g['primera'] for pt, g in por_pt.items() if pt in candidatos).date()
        for r in self._sql(SQL_POSTERIORES, dict(params, desde=primera_fecha)):
            posteriores[r['pt_id']].append(r)

        for pt_id in candidatos:
            g = por_pt[pt_id]
            p = self.productos[pt_id]
            stock = int(p.stock or 0)
            saldo = saldos.get(pt_id, 0)
            r = stock - saldo
            if r == 0:
                clase = 'A'
            elif g['F'] > 0 and r == -g['F']:
                clase = 'B'
            elif g['E'] > 0 and r == g['E']:
                clase = 'B_doble'
            else:
                clase = 'C'
            motivo = None
            if clase == 'B':
                motivo = 'el stock ya está rebajado sin kardex (clase B): no se toca'
            elif clase == 'C':
                motivo = f'el kardex no explica el stock (clase C: stock {stock}, saldo {saldo}): revisar a mano'
            elif clase == 'A' and g['F'] > 0:
                # ¿El saldo llegó a ser menor que el faltante después de la venta?
                # Entonces la unidad «fantasma» no pudo estar en la tienda.
                minimo = saldo
                acumulado = saldo
                primera = g['primera']
                for m in reversed(posteriores.get(pt_id, [])):
                    if (m['fecha'], m['hora']) <= (primera.date(), primera.time()):
                        break
                    acumulado -= int(m['cantidad'])
                    minimo = min(minimo, acumulado)
                if minimo < g['F']:
                    motivo = (f'después de la venta el saldo llegó a {minimo} (< faltante {g["F"]}): '
                              'contradice, revisar a mano')
            for f in g['lineas']:
                f['clase'] = clase
                f['saldo'] = saldo
                if motivo and not f['motivo']:
                    f['motivo'] = motivo

    def _reparables(self, filas):
        return [f for f in filas if f['estado'] != 'ok' and not f['motivo']]

    # ----------------------------------------------------------------- reporte
    def _resumen(self, filas, desde, hasta, sucursal):
        alias = {s.id: s.alias for s in Sucursal.objects.all()}
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(
            f'Ventas POS pagadas del {desde:%d-%m-%Y} al {hasta - timedelta(days=1):%d-%m-%Y}'
            + (f' · {sucursal.alias}' if sucursal else ' · todas las sucursales')))
        if not filas:
            w('Sin líneas en el rango.')
            return

        por_estado = defaultdict(lambda: {'lineas': 0, 'unidades': 0, 'tickets': set()})
        for f in filas:
            e = por_estado[f['estado']]
            e['lineas'] += 1
            e['unidades'] += abs(f['diferencia'])
            e['tickets'].add(f['ticket_id'])
        total = len(filas)
        w(f'\n{"estado":<10} {"líneas":>8} {"%":>6} {"tickets":>8} {"u. dif.":>8}')
        for estado in ('ok', 'sin_mov', 'parcial', 'doble', 'invertida'):
            e = por_estado.get(estado)
            if e:
                w(f'{estado:<10} {e["lineas"]:>8} {100 * e["lineas"] / total:>5.1f}% '
                  f'{len(e["tickets"]):>8} {e["unidades"] if estado != "ok" else "-":>8}')

        reparables = self._reparables(filas)
        w('\nSe reparan con --apply (mercadería clase A):')
        por_suc = defaultdict(lambda: [0, 0, set()])
        for f in reparables:
            d = por_suc[alias.get(f['sucursal_id'], f['sucursal_id'])]
            d[0] += 1
            d[1] += abs(f['diferencia'])
            d[2].add(f['pt_id'])
        for suc, d in sorted(por_suc.items()):
            w(f'  {suc:<10} {d[0]:>5} líneas  {d[1]:>5} u  {len(d[2]):>4} SKUs')
        w(f'  {"TOTAL":<10} {len(reparables):>5} líneas  {sum(abs(f["diferencia"]) for f in reparables):>5} u  '
          f'{len({f["pt_id"] for f in reparables}):>4} SKUs')

        motivos = defaultdict(lambda: [0, 0])
        for f in filas:
            if f['estado'] != 'ok' and f['motivo']:
                m = f['motivo'].split(' (clase C')[0].split(' llegó a ')[0]
                motivos[m][0] += 1
                motivos[m][1] += abs(f['diferencia'])
        if motivos:
            w('\nNo se tocan (en el CSV van con su motivo):')
            for m, d in sorted(motivos.items(), key=lambda x: -x[1][0]):
                w(f'  {d[0]:>5} líneas {d[1]:>5} u  {m}')

    def _csv(self, ruta, filas):
        alias = {s.id: s.alias for s in Sucursal.objects.all()}
        with open(ruta, 'w', newline='', encoding='utf-8-sig') as fh:
            escritor = csv.writer(fh, delimiter=';')
            escritor.writerow(['fecha', 'sucursal', 'ticket', 'ticket_id', 'sku', 'producto_talla_id',
                               'articulo', 'vendidas', 'rebajadas', 'diferencia', 'estado', 'clase',
                               'stock_actual', 'saldo_kardex', 'accion', 'motivo'])
            for f in filas:
                if f['estado'] == 'ok':
                    continue
                p = self.productos.get(f['pt_id'])
                escritor.writerow([
                    f['creado'].strftime('%Y-%m-%d %H:%M'), alias.get(f['sucursal_id'], ''),
                    f['correlativo'], f['ticket_id'], f.get('sku', ''), f['pt_id'], f.get('articulo', ''),
                    f['vendidas'], f['rebajadas'], f['diferencia'], f['estado'], f.get('clase', ''),
                    int(p.stock or 0) if p else '', f.get('saldo', ''),
                    'REPARAR' if not f['motivo'] else 'NO', f['motivo'] or '',
                ])
        self.stdout.write(self.style.SUCCESS(f'Detalle escrito en {ruta}'))

    # --------------------------------------------------------------- reparación
    def _aplicar(self, filas):
        reparables = self._reparables(filas)
        if not reparables:
            self.stdout.write('Nada que reparar.')
            return
        tickets = Ticket.objects.select_related('sucursal').in_bulk({f['ticket_id'] for f in reparables})
        hechas, pendientes = 0, []
        for f in reparables:
            ticket = tickets[f['ticket_id']]
            fecha_venta = f['creado'].strftime('%d-%m-%Y')
            try:
                with transaction.atomic():
                    pt = Producto_Talla.objects.select_for_update().select_related('producto').get(id=f['pt_id'])
                    if f['diferencia'] > 0:
                        if pt.stock < f['diferencia']:
                            pendientes.append((f, f'stock actual {pt.stock} < faltante {f["diferencia"]}'))
                            continue
                        inventario_service.egresar(
                            pt, f['diferencia'], CONCEPTO_REPARACION, RESPONSABLE,
                            sucursal_origen=ticket.sucursal,
                            ticket=ticket,
                            precio_unitario=int(f['precio'] or 0),
                            observaciones=(
                                f'Reparación: la venta del ticket #{ticket.correlativo} del '
                                f'{fecha_venta} no rebajó {f["diferencia"]} u (cobro POS sin egreso, '
                                'abr-sep 2026).'
                            ),
                            referencia_externa=f'TICKET_{ticket.correlativo}',
                        )
                    elif f['clase'] == 'B_doble':
                        # El doble descuento quedó solo en el kardex: se cancela el
                        # movimiento del script del 12-05 y el stock no se toca.
                        n = Movimientos_Producto.objects.filter(
                            ticket=ticket, ProductoTalla=pt, responsable=SCRIPT_12_05,
                            concepto__in=CONCEPTOS_VENTA,
                        ).exclude(estado='CANCELADO').update(
                            estado='CANCELADO',
                            observaciones=(
                                f'[CANCELADO {timezone.localdate():%d-%m-%Y} por {RESPONSABLE}: '
                                'venta ya descontada por el cobro; este movimiento del script del '
                                '12-05 duplicaba el egreso en el kardex sin haber bajado el stock] '
                            ),
                        )
                        if not n:
                            pendientes.append((f, 'no se encontró el movimiento del script para cancelar'))
                            continue
                    else:
                        exceso = -f['diferencia']
                        inventario_service.ingresar(
                            pt, exceso, CONCEPTO_REPARACION, RESPONSABLE,
                            sucursal_destino=ticket.sucursal,
                            ticket=ticket,
                            costo_unitario=int(getattr(pt.producto, 'costo', 0) or 0),
                            precio_unitario=int(f['precio'] or 0),
                            observaciones=(
                                f'Reparación: la venta del ticket #{ticket.correlativo} del '
                                f'{fecha_venta} se rebajó {exceso} u de más (script del 12-05-2026).'
                            ),
                            referencia_externa=f'TICKET_{ticket.correlativo}',
                        )
                    hechas += 1
            except Exception as e:  # una línea no detiene el resto
                logger.exception('medir_ventas_sin_rebaja: error reparando ticket=%s pt=%s',
                                 f['correlativo'], f['pt_id'])
                pendientes.append((f, f'error: {e}'))
        self.stdout.write(self.style.SUCCESS(f'\nReparadas {hechas} línea(s).'))
        if pendientes:
            self.stdout.write(self.style.WARNING(
                f'{len(pendientes)} línea(s) sin reparar (revisar a mano):'))
            for f, motivo in pendientes[:80]:
                self.stdout.write(
                    f'  ticket #{f["correlativo"]} (id {f["ticket_id"]}) SKU {f.get("sku")}: {motivo}')
