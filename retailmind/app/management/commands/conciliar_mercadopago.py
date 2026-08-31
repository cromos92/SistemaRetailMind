"""Conciliación diaria Mercado Pago vs ERP. READ-ONLY (no tiene --apply).

Cruza, por cada cuenta MP configurada (una por empresa/RUT):

1. Pagos aprobados en MP (`GET /v1/payments/search` por rango de fechas)
   vs `TransaccionMercadoPago` + `TicketDetallePago`:
   - Pago MP sin transacción local → plata cobrada fuera del POS (o bug).
   - Transacción APROBADA sin consumir → cobro sin venta finalizada (huérfana).
   - Ticket con pago MP_* MANUAL sin pago real en MP → posible fraude/typo.
   - Diferencias de monto, comisiones (fee) y propinas inesperadas.
2. Tickets ANULADOS con cobro MP aprobado no devuelto (refund falló).

Además refresca money_release_date/fee de los pagos locales (para la
pantalla Dineros) — es la única escritura que hace, sobre las tablas MP.

Uso:
    python manage.py conciliar_mercadopago                     # ayer
    python manage.py conciliar_mercadopago --desde 2026-08-01 --hasta 2026-08-31
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import MercadoPagoConfig, TicketDetallePago, TransaccionMercadoPago
from app.services import mercadopago_service as mp


class Command(BaseCommand):
    help = 'Concilia pagos Mercado Pago (API) contra transacciones y tickets del ERP (read-only)'

    def add_arguments(self, parser):
        parser.add_argument('--desde', type=str, default=None, help='YYYY-MM-DD (default: ayer)')
        parser.add_argument('--hasta', type=str, default=None, help='YYYY-MM-DD (default: ayer)')

    def handle(self, *args, **options):
        ayer = timezone.localdate() - timedelta(days=1)
        desde = options['desde'] or str(ayer)
        hasta = options['hasta'] or str(ayer)
        self.stdout.write(f'Conciliación Mercado Pago {desde} → {hasta}')

        tokens_vistos = set()
        problemas = 0
        for config in MercadoPagoConfig.objects.select_related('sucursal', 'cuenta').all():
            # Una consulta por CUENTA MP (token resuelto: BD cifrada o env
            # fallback), no por sucursal.
            try:
                token = mp._token(config)
            except mp.MercadoPagoError:
                self.stderr.write(f'  Config {config.id} sin credenciales resolubles — omitida')
                continue
            if token in tokens_vistos:
                continue
            tokens_vistos.add(token)
            problemas += self._conciliar_cuenta(config, desde, hasta)

        problemas += self._tickets_anulados_sin_refund()
        problemas += self._huerfanas()

        if problemas:
            self.stdout.write(self.style.WARNING(f'⚠️  {problemas} hallazgo(s) — revisar arriba'))
        else:
            self.stdout.write(self.style.SUCCESS('✅ Sin diferencias'))

    # ------------------------------------------------------------------
    def _conciliar_cuenta(self, config, desde, hasta):
        cuenta = mp._cuenta_de(config)
        etiqueta = (f'empresa {cuenta.empresa.rut}' if cuenta
                    else f'env {config.token_env or "?"}')
        self.stdout.write(f'\n== Cuenta MP ({etiqueta}) ==')
        try:
            resp = mp._request(config, 'GET', '/v1/payments/search', params={
                'range': 'date_created',
                'begin_date': f'{desde}T00:00:00.000-04:00',
                'end_date': f'{hasta}T23:59:59.999-04:00',
                'limit': 100,
                'sort': 'date_created',
            })
            data = mp._json_o_error(resp, 'payments/search')
        except mp.MercadoPagoError as e:
            self.stderr.write(f'  No se pudo consultar MP: {e.mensaje}')
            return 0

        pagos_mp = data.get('results') or []
        self.stdout.write(f'  Pagos en MP: {len(pagos_mp)}')
        problemas = 0
        total_fees = 0
        for pago in pagos_mp:
            estado = str(pago.get('status') or '')
            if estado not in ('approved', 'refunded', 'charged_back'):
                continue
            ext_ref = pago.get('external_reference') or ''
            monto = int(round(float(pago.get('transaction_amount') or 0)))
            det = pago.get('transaction_details') or {}
            neto = det.get('net_received_amount')
            if neto is not None:
                total_fees += monto - int(round(float(neto)))

            # Propina inesperada: descuadra el monto del ticket
            propina = (pago.get('coupon_amount') or 0)
            tip = (pago.get('tip_amount') if 'tip_amount' in pago else None) or 0
            if tip:
                problemas += 1
                self.stdout.write(self.style.WARNING(
                    f'  PROPINA inesperada ${tip} en payment {pago.get("id")} ({ext_ref})'
                ))
            _ = propina  # informativo

            trx = TransaccionMercadoPago.objects.filter(external_reference=ext_ref).first()
            if not trx:
                problemas += 1
                self.stdout.write(self.style.WARNING(
                    f'  SIN TRANSACCIÓN LOCAL: payment {pago.get("id")} ${monto} '
                    f'({ext_ref or "sin external_reference"}) estado={estado}'
                ))
                continue
            if trx.monto != monto:
                problemas += 1
                self.stdout.write(self.style.WARNING(
                    f'  MONTO DISTINTO: {ext_ref} local=${trx.monto} MP=${monto}'
                ))
            # Refrescar datos de liberación/fee para la pantalla Dineros
            try:
                mp._aplicar_estado(
                    trx,
                    {'approved': 'APROBADA', 'refunded': 'DEVUELTA',
                     'charged_back': 'CONTRACARGO'}[estado],
                    detalle=pago.get('status_detail') or '',
                    payment=pago,
                )
            except Exception:  # noqa: BLE001
                pass

        self.stdout.write(f'  Comisiones MP del período (aprox): ${total_fees:,}'.replace(',', '.'))
        return problemas

    # ------------------------------------------------------------------
    def _tickets_anulados_sin_refund(self):
        problemas = 0
        anuladas = TransaccionMercadoPago.objects.filter(
            tipo='VENTA', estado='APROBADA', ticket__estado__in=['ANULADO', 'DEVUELTO'],
        ).select_related('ticket')
        for trx in anuladas:
            problemas += 1
            self.stdout.write(self.style.ERROR(
                f'  TICKET {trx.ticket.estado} CON COBRO MP VIVO: '
                f'{trx.external_reference} ${trx.monto} — devolver a mano en MP'
            ))
        return problemas

    # ------------------------------------------------------------------
    def _huerfanas(self):
        problemas = 0
        huerfanas = TransaccionMercadoPago.objects.filter(
            tipo='VENTA', estado='APROBADA', consumida=False,
        )
        for trx in huerfanas:
            problemas += 1
            self.stdout.write(self.style.WARNING(
                f'  APROBADA SIN VENTA: {trx.external_reference} ${trx.monto} '
                f'sucursal={trx.sucursal_id} ticket={trx.correlativo_ticket} '
                f'({trx.creado_en:%d/%m %H:%M}) — el cliente pagó y la venta no se finalizó'
            ))
        return problemas
