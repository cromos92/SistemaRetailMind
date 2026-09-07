# -*- coding: utf-8 -*-
"""
Repara ventas cobradas por Mercado Pago que quedaron registradas como TARJETA
MANUAL (Transbank), dejando el cobro MP huérfano.

CONTEXTO (caso NICK2, 05-09-2026): el cobro se mandó a la máquina Point, el
terminal pidió REINTENTE, el cajero cerró la ventana de espera del POS —MP no
permite cancelar una orden que ya está en la pantalla del terminal (409
cannot_cancel_order)— y el cliente igual pasó la tarjeta. La venta se cerró
ingresando un CRÉDITO MANUAL. Resultado:

  * la plata entró de verdad por Mercado Pago (llega en el depósito MP);
  * la cuadratura la muestra en VISA-MC-AMEX, o sea como un Transbank que
    nunca va a depositar;
  * la `TransaccionMercadoPago` quedó APROBADA sin consumir (huérfana), así
    que también aparece en el cierre de la máquina → el mismo dinero se ve
    dos veces y el arqueo no cierra por ningún lado.

QUÉ HACE: por cada transacción MP APROBADA sin consumir, busca el ticket de su
`correlativo_ticket` y, dentro de él, el pago con tarjeta manual del MISMO
monto. Si lo encuentra, cambia ese pago al método Mercado Pago que corresponde
(MP_POINT_DEBITO / MP_POINT_CREDITO / MP_POINT / MP_QR según el
`payment_type_id` real de MP), replica el cambio en el `Dte_Detalle_Pago` del
documento emitido y marca la transacción como consumida vinculándola al pago.

NO inventa pagos: si el ticket no tiene una contraparte manual por el mismo
monto, la transacción se reporta para revisión a mano (puede ser plata cobrada
para una venta que nunca se cerró, y eso se resuelve con una devolución en MP,
no tocando la cuadratura).

Después de aplicar, el Resumen de Caja (que se calcula en vivo) queda correcto;
los `total_*_teorico` del ArqueoCaja son un snapshot congelado y hay que
recalcularlos: usar `--recalcular-arqueo` o el botón "Actualizar Teórico".

USO:
    # Diagnóstico del día del problema (no escribe nada)
    python manage.py reparar_cobro_mp_huerfano --sucursal NICK2 --fecha 2026-09-05

    # Aplicar
    python manage.py reparar_cobro_mp_huerfano --sucursal NICK2 --fecha 2026-09-05 --apply

    # Un caso puntual, forzando el ticket
    python manage.py reparar_cobro_mp_huerfano --transaccion 87 --ticket 12345 --apply

    # Barrido de los últimos 30 días, todas las sucursales
    python manage.py reparar_cobro_mp_huerfano --dias 30
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    ArqueoCaja,
    Dte,
    Dte_Detalle_Pago,
    Sucursal,
    Ticket,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')

# Métodos con los que un cajero registra una tarjeta "a mano". Son los únicos
# que esta reparación puede convertir a Mercado Pago: efectivo, transferencia o
# crédito son otra historia y se dejan para revisión manual.
METODOS_TARJETA_MANUAL = [
    'TBK_CREDITO_POS', 'TBK_DEBITO_POS', 'TBK_PREPAGO_POS',
    'TBK_MANUAL', 'TBK_POS_INTEGRADO',
    'TARJETA_CREDITO', 'TARJETA_DEBITO',
]

# Ticket.tipo_dte -> Dte.tipo_documento (para ubicar el espejo del pago)
TIPO_DTE_A_DOCUMENTO = {
    'BOLETA_ELECTRONICA': 'BOLETA ELECTRONICA',
    'BOLETA': 'BOLETA PAPEL',
    'FACTURA_ELECTRONICA': 'FACTURA ELECTRONICA',
    'FACTURA_EXENTA': 'FACTURA EXENTA',
}


def _plata(v):
    return f"${int(v or 0):,}".replace(',', '.')


class Command(BaseCommand):
    help = ('Repara ventas cobradas por Mercado Pago que quedaron registradas '
            'como tarjeta manual (cobro MP huérfano)')

    def add_arguments(self, parser):
        parser.add_argument('--sucursal', type=str, default=None,
                            help='Alias o id de la sucursal (default: todas)')
        parser.add_argument('--fecha', type=str, default=None,
                            help='YYYY-MM-DD; día del cobro MP (default: hoy)')
        parser.add_argument('--dias', type=int, default=1,
                            help='Cantidad de días hacia atrás desde --fecha (default: 1)')
        parser.add_argument('--transaccion', type=int, default=None,
                            help='Id de una TransaccionMercadoPago puntual (ignora fechas)')
        parser.add_argument('--ticket', type=str, default=None,
                            help='Correlativo del ticket a emparejar (solo con --transaccion)')
        parser.add_argument('--apply', action='store_true',
                            help='ESCRIBE los cambios (sin esto es dry-run)')
        parser.add_argument('--recalcular-arqueo', action='store_true',
                            help='Recalcula los teóricos del arqueo de los días afectados')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        aplicar = options['apply']
        etiqueta = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{etiqueta}] Reparacion de cobros Mercado Pago huerfanos'
        ))

        huerfanas = self._buscar_huerfanas(options)
        if not huerfanas:
            self.stdout.write(self.style.SUCCESS(
                'Sin cobros Mercado Pago aprobados sin usar en el rango pedido.'))
            return

        self.stdout.write(f'Cobros MP aprobados sin usar: {len(huerfanas)}\n')

        reparados, sin_contraparte, saltados = [], [], []
        afectados = set()   # (sucursal_id, fecha) para el recálculo del arqueo

        for trx in huerfanas:
            ticket = self._resolver_ticket(trx, options.get('ticket'))
            cabecera = (f"  MP #{trx.id} {trx.external_reference} {_plata(trx.monto)} "
                        f"{trx.canal} {mp.etiqueta_medio_mp(trx.metodo_pago_mp)} "
                        f"({timezone.localtime(trx.creado_en):%d-%m %H:%M}) "
                        f"ticket={trx.correlativo_ticket}")
            if not ticket:
                sin_contraparte.append((trx, 'no existe el ticket del correlativo'))
                self.stdout.write(self.style.WARNING(cabecera + ' -> SIN TICKET'))
                continue
            if ticket.estado != 'PAGADO':
                sin_contraparte.append((trx, f'ticket en estado {ticket.estado}'))
                self.stdout.write(self.style.WARNING(
                    cabecera + f' -> ticket {ticket.estado} (no es una venta cerrada)'))
                continue

            pago = self._pago_manual_equivalente(ticket, trx)
            if not pago:
                metodos = ', '.join(
                    f'{p.metodo_pago} {_plata(p.monto)}' for p in ticket.pagos.all()
                ) or 'sin pagos'
                sin_contraparte.append((trx, f'sin tarjeta manual por {_plata(trx.monto)}'))
                self.stdout.write(self.style.WARNING(
                    cabecera + f' -> SIN CONTRAPARTE (pagos del ticket: {metodos})'))
                continue

            metodo_nuevo = mp.metodo_pago_ticket_de(trx)
            self.stdout.write(self.style.SUCCESS(
                cabecera + f' -> pago #{pago.id} {pago.metodo_pago} => {metodo_nuevo}'))

            if not aplicar:
                reparados.append((trx, ticket, pago))
                afectados.add((ticket.sucursal_id, ticket.fecha))
                continue

            try:
                espejos = self._aplicar(trx, ticket, pago, metodo_nuevo)
            except Exception as e:  # noqa: BLE001 — un caso malo no bota la corrida
                saltados.append((trx, str(e)))
                self.stdout.write(self.style.ERROR(f'      ERROR al reparar: {e}'))
                continue
            reparados.append((trx, ticket, pago))
            afectados.add((ticket.sucursal_id, ticket.fecha))
            self.stdout.write(
                f'      OK: pago del ticket actualizado; espejo DTE: {espejos}')

        self._resumen(reparados, sin_contraparte, saltados, aplicar)

        if aplicar and options['recalcular_arqueo']:
            self._recalcular_arqueos(afectados)
        elif afectados:
            self.stdout.write(
                '\nEl Resumen de Caja se calcula en vivo y ya queda corregido. '
                'Los teoricos del ARQUEO son un snapshot: correr de nuevo con '
                '--recalcular-arqueo o usar "Actualizar Teorico" en el arqueo de '
                + ', '.join(f'{s}/{f}' for s, f in sorted(afectados, key=str)))

    # ------------------------------------------------------------------
    def _buscar_huerfanas(self, options):
        qs = (TransaccionMercadoPago.objects
              .filter(tipo='VENTA', estado='APROBADA', consumida=False)
              .exclude(correlativo_ticket='')
              .exclude(correlativo_ticket__startswith='PRUEBA-')
              .exclude(correlativo_ticket__startswith='DIRECTO-')
              .select_related('sucursal', 'config')
              .order_by('creado_en'))

        if options['transaccion']:
            trx = qs.filter(id=options['transaccion']).first()
            if not trx:
                raise CommandError(
                    f"La transaccion {options['transaccion']} no existe, ya esta "
                    'consumida o no esta APROBADA.')
            return [trx]

        if options['ticket']:
            raise CommandError('--ticket solo se usa junto con --transaccion')

        if options['sucursal']:
            sucursal = self._resolver_sucursal(options['sucursal'])
            qs = qs.filter(sucursal=sucursal)

        hasta = timezone.localdate()
        if options['fecha']:
            try:
                hasta = datetime.strptime(options['fecha'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--fecha debe tener formato AAAA-MM-DD')
        dias = max(1, int(options['dias']))
        desde = hasta - timedelta(days=dias - 1)
        # `creado_en` es datetime con tz: se filtra por el día local del cobro.
        inicio = timezone.make_aware(datetime.combine(desde, datetime.min.time()))
        fin = timezone.make_aware(datetime.combine(hasta, datetime.max.time()))
        self.stdout.write(f'Rango: {desde} -> {hasta}')
        return list(qs.filter(creado_en__range=(inicio, fin)))

    def _resolver_sucursal(self, valor):
        sucursal = None
        if str(valor).isdigit():
            sucursal = Sucursal.objects.filter(id=int(valor)).first()
        if not sucursal:
            sucursal = Sucursal.objects.filter(alias__iexact=str(valor)).first()
        if not sucursal:
            raise CommandError(f"Sucursal '{valor}' no encontrada (usa alias o id)")
        return sucursal

    def _resolver_ticket(self, trx, correlativo_forzado=None):
        correlativo = correlativo_forzado or trx.correlativo_ticket
        if not correlativo:
            return None
        return (Ticket.objects
                .filter(sucursal_id=trx.sucursal_id, correlativo=correlativo)
                .prefetch_related('pagos')
                .first())

    def _pago_manual_equivalente(self, ticket, trx):
        """Pago con tarjeta manual del ticket por el MISMO monto del cobro MP.

        El monto exacto es la garantía de que se está corrigiendo el medio de
        pago de ESE cobro y no repartiendo plata entre pagos distintos.
        """
        candidatos = [
            p for p in ticket.pagos.all()
            if p.metodo_pago in METODOS_TARJETA_MANUAL and int(p.monto or 0) == int(trx.monto)
        ]
        if not candidatos:
            return None
        # Si hay varios idénticos, el que no tenga ya una transacción MP encima.
        for pago in candidatos:
            if not pago.transacciones_mercadopago.exists():
                return pago
        return candidatos[0]

    # ------------------------------------------------------------------
    def _aplicar(self, trx, ticket, pago, metodo_nuevo):
        """Escribe la corrección. Devuelve la descripción del espejo en el DTE."""
        metodo_anterior = pago.metodo_pago
        sello = (f'Corregido {timezone.localdate():%d-%m-%Y}: se habia registrado como '
                 f'{metodo_anterior} manual, pero el cobro real fue Mercado Pago '
                 f'{trx.canal} (pago {trx.payment_id or "s/n"}, ref {trx.external_reference})')

        with transaction.atomic():
            pago.metodo_pago = metodo_nuevo
            pago.tipo_tarjeta = trx.metodo_pago_mp or 'MERCADO PAGO'
            pago.voucher = trx.payment_id or pago.voucher
            pago.origen_pago = 'POS_INTEGRADO'
            pago.notas = f'{(pago.notas or "").strip()} | {sello}'.strip(' |')
            pago.save(update_fields=['metodo_pago', 'tipo_tarjeta', 'voucher',
                                     'origen_pago', 'notas', 'actualizado_en'])

            espejos = self._actualizar_espejo_dte(
                ticket, pago, trx, metodo_anterior, metodo_nuevo, sello)

            trx.consumida = True
            trx.ticket = ticket
            trx.detalle_pago = pago
            trx.save(update_fields=['consumida', 'ticket', 'detalle_pago', 'actualizado_en'])

        logger.warning(
            'MP: cobro huerfano %s reparado sobre ticket %s (%s -> %s, %s)',
            trx.external_reference, ticket.correlativo, metodo_anterior,
            metodo_nuevo, _plata(trx.monto),
        )
        return espejos

    def _actualizar_espejo_dte(self, ticket, pago, trx, metodo_anterior,
                               metodo_nuevo, sello):
        """El DTE guarda su propia copia de los pagos (`Dte_Detalle_Pago`); si no
        se actualiza, la cuadratura de un día sin ticket asociado y los reportes
        por documento seguirían mostrando la tarjeta."""
        if not ticket.folio_dte:
            return 'ticket sin DTE'
        tipo_documento = TIPO_DTE_A_DOCUMENTO.get(ticket.tipo_dte or '')
        dtes = Dte.objects.filter(sucursal_id=ticket.sucursal_id,
                                  numero_documento=ticket.folio_dte)
        if tipo_documento:
            dtes = dtes.filter(tipo_documento=tipo_documento)
        dte = dtes.first()
        if not dte:
            return f'no se encontro el DTE {ticket.folio_dte}'

        # El espejo se escribe con el código crudo (boletas) o con el display
        # (facturas), así que se acepta cualquiera de los dos.
        etiquetas = {metodo_anterior, metodo_anterior.replace('_', ' ')}
        display = dict(pago._meta.get_field('metodo_pago').choices or {}).get(metodo_anterior)
        if display:
            etiquetas.add(display)
        filas = [
            p for p in Dte_Detalle_Pago.objects.filter(dte=dte, monto=pago.monto)
            if (p.metodo_pago or '') in etiquetas
        ]
        if not filas:
            return f'DTE {dte.numero_documento}: sin fila espejo por {_plata(pago.monto)}'
        fila = filas[0]
        fila.metodo_pago = metodo_nuevo
        fila.tipo_tarjeta = trx.metodo_pago_mp or 'MERCADO PAGO'
        fila.voucher = (trx.payment_id or fila.voucher or '')[:50]
        fila.notas = f'{(fila.notas or "").strip()} | {sello}'.strip(' |')
        fila.save(update_fields=['metodo_pago', 'tipo_tarjeta', 'voucher', 'notas'])
        return f'DTE {dte.tipo_documento} {dte.numero_documento} actualizado'

    # ------------------------------------------------------------------
    def _recalcular_arqueos(self, afectados):
        from app.views_modulo_ventas import _recalcular_teoricos_arqueo
        for sucursal_id, fecha in sorted(afectados, key=str):
            arqueo = ArqueoCaja.objects.filter(
                sucursal_id=sucursal_id, fecha_arqueo=fecha).first()
            if not arqueo:
                self.stdout.write(f'  Sin arqueo para sucursal {sucursal_id} {fecha}')
                continue
            resultado = _recalcular_teoricos_arqueo(
                arqueo, registrar_bitacora=True,
                razon='reparacion de cobro Mercado Pago huerfano',
            )
            if not resultado.get('hay_cambios'):
                self.stdout.write(f'  Arqueo {arqueo.id} ({fecha}): sin cambios')
                continue
            self.stdout.write(self.style.SUCCESS(
                f'  Arqueo {arqueo.id} ({fecha}, {arqueo.estado}) recalculado:'))
            for campo, valores in resultado['cambios'].items():
                self.stdout.write(
                    f"    {campo}: {_plata(valores['antes'])} -> {_plata(valores['despues'])}")

    def _resumen(self, reparados, sin_contraparte, saltados, aplicar):
        total = sum(t.monto for t, _tk, _p in reparados)
        self.stdout.write('')
        verbo = 'Reparados' if aplicar else 'Reparables'
        self.stdout.write(self.style.SUCCESS(
            f'{verbo}: {len(reparados)} cobro(s) por {_plata(total)}'))
        if sin_contraparte:
            self.stdout.write(self.style.WARNING(
                f'Requieren revision manual: {len(sin_contraparte)}'))
            for trx, motivo in sin_contraparte:
                self.stdout.write(
                    f'  MP #{trx.id} {_plata(trx.monto)} ticket={trx.correlativo_ticket} '
                    f'sucursal={trx.sucursal_id}: {motivo}')
        if saltados:
            self.stdout.write(self.style.ERROR(f'Con error: {len(saltados)}'))
            for trx, motivo in saltados:
                self.stdout.write(f'  MP #{trx.id}: {motivo}')
        if reparados and not aplicar:
            self.stdout.write('\nPara aplicar, repetir el comando con --apply')
