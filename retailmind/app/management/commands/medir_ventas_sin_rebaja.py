# -*- coding: utf-8 -*-
"""
Mide (y opcionalmente repara) ventas POS pagadas cuyo stock no se rebajó, o se
rebajó de más.

CONTEXTO (verificación del 26-09-2026, copia local de producción): entre el
13-04 y el 11-05-2026, al pagar un ticket solo se consumía el lote FIFO; el
stock plano y el kardex no bajaban. Se corrigió el 11-05 y un script del 12-05
reparó 3.315 líneas, pero solo miró tickets SIN NINGÚN egreso: en la copia
local quedaron 1.170 líneas / 1.214 u / 899 tickets sin rebajar, y el mismo
script descontó dos veces 17 u en 16 líneas. Como a esas ventas les falta a la
vez el stock y el kardex, los dos siguen cuadrando entre sí y el error no se
ve en ningún reporte.

QUÉ MIDE, por (ticket, talla) de tickets PAGADOS (sin tickets de cambio ni
líneas despachadas por guía de cotización):

    vendidas  = unidades de la línea
    rebajadas = -SUM(kardex de venta ligado al ticket) + reparaciones previas
                de este comando

    ok        rebajadas == vendidas
    sin_mov   rebajadas == 0
    parcial   0 < rebajadas < vendidas
    doble     rebajadas > vendidas
    invertida rebajadas < 0 (el kardex SUMÓ stock en una venta)

Una línea con diferencia queda ABSORBIDA si después de la venta hubo un ajuste
de inventario de ese SKU (toma de inventario, corrección de stock, ajuste
positivo/negativo): el conteo físico ya dejó el stock en la realidad y
repararla lo descuadraría otra vez.

--apply repara solo lo NO absorbido, en una transacción:
  - sin_mov / parcial: egreso CORRECCION_STOCK por el faltante (baja stock,
    consume lotes FIFO, escribe kardex) si el stock actual alcanza; si no, la
    línea queda para revisión a mano. Los lotes de esas ventas se alinearon
    al stock plano con `reconciliar_lotes_a_stock` (jun-jul), por eso el
    egreso también los consume; correr `reconciliar_stock_lotes` después
    para confirmar que no quedó drift.
  - doble: SOLO con --reparar-dobles. Casi todas son un VENTA real del
    11-05 más un VENTA_DIRECTA del script de reconciliación del 12-05, y el
    kardex no dice si ese script además bajó el stock: revisar el CSV antes.
    Con el flag se hace un ingreso CORRECCION_STOCK por el exceso, con lote.
  - invertida: se informa, no se toca (revisar a mano).
Los movimientos quedan ligados al ticket, con responsable
`reparacion_ventas_sin_rebaja`: correr el comando otra vez no repite nada.

La fecha del movimiento es la de HOY (el stock se corrige hoy). Se usa
CORRECCION_STOCK y no VENTA_PUBLICO para que la reparación no aparezca como
venta de hoy en reportes ni en la predicción de compras.

USO:
    # Medir el tramo del problema (no escribe nada)
    python manage.py medir_ventas_sin_rebaja --desde 2026-04-01 --hasta 2026-06-01

    # Todo lo pagado desde abril, detalle a CSV
    python manage.py medir_ventas_sin_rebaja --csv ventas_sin_rebaja.csv

    # Una sucursal
    python manage.py medir_ventas_sin_rebaja --sucursal NICK2

    # Reparar lo que falta rebajar (tras revisar el dry-run)
    python manage.py medir_ventas_sin_rebaja --desde 2026-04-01 --hasta 2026-06-01 --apply

    # Incluir también las rebajadas de más (tras confirmarlas a mano)
    python manage.py medir_ventas_sin_rebaja --desde 2026-04-01 --hasta 2026-06-01 --apply --reparar-dobles
"""
import csv
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from app.constants_kardex import CONCEPTOS_AJUSTE, CONCEPTOS_VENTA
from app.models import Producto_Talla, Sucursal, Ticket
from app.services import inventario_service

logger = logging.getLogger('app')

RESPONSABLE = 'reparacion_ventas_sin_rebaja'
CONCEPTO_REPARACION = 'CORRECCION_STOCK'

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
      and tp."ProductoTalla_id" is not null
      and not coalesce(tp.despachado_por_guia, false)
      and t.created_at >= %(desde)s and t.created_at < %(hasta)s
      {filtro_sucursal}
    group by 1, 2, 3, 4, 5
),
kardex as (
    select m.ticket_id, m."ProductoTalla_id" as pt_id,
           sum(case when m.concepto = any(%(conceptos_venta)s) then m.cantidad else 0 end) as venta,
           sum(case when m.responsable = %(responsable)s then m.cantidad else 0 end) as reparado
    from app_movimientos_producto m
    where m.ticket_id in (select ticket_id from lineas)
      and (m.concepto = any(%(conceptos_venta)s) or m.responsable = %(responsable)s)
      and coalesce(m.estado, '') <> 'CANCELADO'
    group by 1, 2
)
select l.ticket_id, l.correlativo, l.sucursal_id, l.creado, l.pt_id, l.vendidas, l.precio,
       coalesce(k.venta, 0) as venta, coalesce(k.reparado, 0) as reparado,
       exists (
           select 1 from app_movimientos_producto a
           where a."ProductoTalla_id" = l.pt_id
             and a.concepto = any(%(conceptos_ajuste)s)
             and a.responsable <> %(responsable)s
             and (a.fecha > l.creado::date
                  or (a.fecha = l.creado::date and a.hora > l.creado::time))
       ) as absorbida
from lineas l
left join kardex k on k.ticket_id = l.ticket_id and k.pt_id = l.pt_id
order by l.creado, l.ticket_id, l.pt_id
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
        parser.add_argument('--desde', default='2026-04-01',
                            help='Fecha inicial (creación del ticket), YYYY-MM-DD. Default 2026-04-01.')
        parser.add_argument('--hasta', default=None,
                            help='Fecha final EXCLUSIVA, YYYY-MM-DD. Default: mañana.')
        parser.add_argument('--sucursal', default=None,
                            help='ID o alias de la sucursal (default: todas).')
        parser.add_argument('--csv', default=None,
                            help='Ruta de un CSV con el detalle de las líneas con diferencia.')
        parser.add_argument('--apply', action='store_true',
                            help='Aplica la reparación de lo no absorbido. Sin esto no escribe nada.')
        parser.add_argument('--reparar-dobles', action='store_true',
                            help='Con --apply, repara también las líneas rebajadas de más (revisar antes).')

    def handle(self, *args, **opts):
        if connection.vendor != 'postgresql':
            raise CommandError('Este comando usa SQL de PostgreSQL.')

        desde = self._fecha(opts['desde'], 'desde')
        hasta = (self._fecha(opts['hasta'], 'hasta') if opts['hasta']
                 else timezone.localdate() + timedelta(days=1))
        if hasta <= desde:
            raise CommandError('--hasta debe ser posterior a --desde.')
        sucursal = self._sucursal(opts['sucursal'])

        self.reparar_dobles = bool(opts['reparar_dobles'])
        filas = self._leer(desde, hasta, sucursal)
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

    def _leer(self, desde, hasta, sucursal):
        tz = timezone.get_current_timezone()
        params = {
            'desde': timezone.make_aware(datetime.combine(desde, datetime.min.time()), tz),
            'hasta': timezone.make_aware(datetime.combine(hasta, datetime.min.time()), tz),
            'conceptos_venta': list(CONCEPTOS_VENTA),
            'conceptos_ajuste': list(CONCEPTOS_AJUSTE),
            'responsable': RESPONSABLE,
        }
        filtro = ''
        if sucursal is not None:
            filtro = 'and t.sucursal_id = %(sucursal_id)s'
            params['sucursal_id'] = sucursal.id
        with connection.cursor() as cursor:
            cursor.execute(SQL_LINEAS.format(filtro_sucursal=filtro), params)
            columnas = [c[0] for c in cursor.description]
            filas = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
        for f in filas:
            f['vendidas'] = int(f['vendidas'] or 0)
            f['rebajadas'] = -int(f['venta'] or 0) - int(f['reparado'] or 0)
            f['estado'] = _clasificar(f['vendidas'], f['rebajadas'])
            f['diferencia'] = f['vendidas'] - f['rebajadas']  # >0 falta rebajar
        return filas

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

        por_estado = defaultdict(lambda: {'lineas': 0, 'unidades': 0, 'tickets': set(),
                                          'absorbidas': 0, 'u_absorbidas': 0})
        for f in filas:
            e = por_estado[f['estado']]
            e['lineas'] += 1
            e['unidades'] += abs(f['diferencia'])
            e['tickets'].add(f['ticket_id'])
            if f['estado'] != 'ok' and f['absorbida']:
                e['absorbidas'] += 1
                e['u_absorbidas'] += abs(f['diferencia'])

        total = len(filas)
        w(f'\n{"estado":<10} {"líneas":>8} {"%":>6} {"tickets":>8} {"u. dif.":>8} '
          f'{"absorbidas":>11} {"u. abs.":>8}')
        for estado in ('ok', 'sin_mov', 'parcial', 'doble', 'invertida'):
            e = por_estado.get(estado)
            if not e:
                continue
            w(f'{estado:<10} {e["lineas"]:>8} {100 * e["lineas"] / total:>5.1f}% '
              f'{len(e["tickets"]):>8} {e["unidades"] if estado != "ok" else "-":>8} '
              f'{e["absorbidas"] if estado != "ok" else "-":>11} '
              f'{e["u_absorbidas"] if estado != "ok" else "-":>8}')

        # Por mes y sucursal, solo lo que tiene diferencia.
        por_mes = defaultdict(lambda: defaultdict(int))
        for f in filas:
            if f['estado'] == 'ok':
                continue
            clave = (f['creado'].strftime('%Y-%m'), alias.get(f['sucursal_id'], f['sucursal_id']))
            por_mes[clave][f['estado']] += 1
            por_mes[clave]['u'] += f['diferencia']
        if por_mes:
            w('\nCon diferencia, por mes y sucursal (u = faltante neto: + falta rebajar, - rebajado de más):')
            for (mes, suc), d in sorted(por_mes.items()):
                detalle = ', '.join(f'{k}={v}' for k, v in sorted(d.items()) if k != 'u')
                w(f'  {mes} {suc:<10} u={d["u"]:>5}  {detalle}')

        reparables = self._reparables(filas)
        w(f'\nReparables con --apply: {len(reparables)} línea(s) '
          f'({sum(abs(f["diferencia"]) for f in reparables)} u). '
          f'Absorbidas por un ajuste posterior (no se tocan): '
          f'{sum(1 for f in filas if f["estado"] != "ok" and f["absorbida"])}. '
          f'Invertidas (revisar a mano): {por_estado["invertida"]["lineas"] if "invertida" in por_estado else 0}.')
        dobles = [f for f in filas if f['estado'] == 'doble' and not f['absorbida']]
        if dobles and not self.reparar_dobles:
            w(f'Rebajadas de más sin absorber: {len(dobles)} línea(s) '
              f'({sum(-f["diferencia"] for f in dobles)} u) — no se tocan sin --reparar-dobles.')

    def _reparables(self, filas):
        estados = ('sin_mov', 'parcial', 'doble') if self.reparar_dobles else ('sin_mov', 'parcial')
        return [f for f in filas if f['estado'] in estados and not f['absorbida']]

    def _csv(self, ruta, filas):
        alias = {s.id: s.alias for s in Sucursal.objects.all()}
        skus = dict(Producto_Talla.objects.filter(
            id__in={f['pt_id'] for f in filas if f['estado'] != 'ok'}
        ).values_list('id', 'sku'))
        with open(ruta, 'w', newline='', encoding='utf-8-sig') as fh:
            escritor = csv.writer(fh, delimiter=';')
            escritor.writerow(['fecha', 'sucursal', 'ticket', 'ticket_id', 'sku', 'producto_talla_id',
                               'vendidas', 'rebajadas', 'diferencia', 'estado', 'absorbida'])
            for f in filas:
                if f['estado'] == 'ok':
                    continue
                escritor.writerow([
                    f['creado'].strftime('%Y-%m-%d %H:%M'), alias.get(f['sucursal_id'], ''),
                    f['correlativo'], f['ticket_id'], skus.get(f['pt_id'], ''), f['pt_id'],
                    f['vendidas'], f['rebajadas'], f['diferencia'], f['estado'],
                    'si' if f['absorbida'] else 'no',
                ])
        self.stdout.write(self.style.SUCCESS(f'Detalle escrito en {ruta}'))

    # --------------------------------------------------------------- reparación
    def _aplicar(self, filas):
        reparables = self._reparables(filas)
        if not reparables:
            self.stdout.write('Nada que reparar.')
            return
        tickets = Ticket.objects.select_related('sucursal').in_bulk(
            {f['ticket_id'] for f in reparables})
        hechas, pendientes = 0, []
        with transaction.atomic():
            for f in reparables:
                ticket = tickets[f['ticket_id']]
                pt = Producto_Talla.objects.select_for_update().get(id=f['pt_id'])
                fecha_venta = f['creado'].strftime('%d-%m-%Y')
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
                            f'{fecha_venta} no rebajó {f["diferencia"]} u (hueco abr-may 2026).'
                        ),
                        referencia_externa=f'TICKET_{ticket.correlativo}',
                    )
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
                            f'{fecha_venta} rebajó {exceso} u de más.'
                        ),
                        referencia_externa=f'TICKET_{ticket.correlativo}',
                    )
                hechas += 1
        self.stdout.write(self.style.SUCCESS(f'\nReparadas {hechas} línea(s).'))
        if pendientes:
            self.stdout.write(self.style.WARNING(
                f'{len(pendientes)} línea(s) sin reparar (revisar a mano):'))
            for f, motivo in pendientes[:50]:
                self.stdout.write(
                    f'  ticket #{f["correlativo"]} (id {f["ticket_id"]}) talla {f["pt_id"]}: {motivo}')
