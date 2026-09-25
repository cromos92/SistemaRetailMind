"""
Corrige los cobros de Mercado Pago asignados a mano a su venta («ASOC-<N°>»)
antes del 25-09-2026: quedaron con la fecha del día en que se asignaron (no la
del cobro) y en «la primera caja de la cuenta» (no la de la tienda de la venta).
Por eso el «Cierre por caja y día» del día del cobro no cuadraba y el día de la
asignación mostraba un cobro que Mercado Pago no tenía.

Solo toca `creado_en`, `config` y `sucursal` de esas transacciones (no el pago
de la venta, el documento ni el arqueo). Vista previa por defecto.

    python manage.py reparar_asociaciones_mp_fecha
    python manage.py reparar_asociaciones_mp_fecha --aplicar
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import TransaccionMercadoPago
from app.services import asociacion_mp_service as asoc
from app.services import conciliacion_mp_service as conc


class Command(BaseCommand):
    help = 'Pone la fecha real del cobro y la caja de la tienda de la venta en las transacciones ASOC- antiguas.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribir los cambios (sin esto, solo muestra).')

    def handle(self, *args, **opciones):
        aplicar = opciones['aplicar']
        qs = (TransaccionMercadoPago.objects.filter(external_reference__startswith='ASOC-')
              .select_related('config__sucursal', 'ticket__sucursal').order_by('id'))
        revisadas = cambiadas = 0
        for trx in qs:
            revisadas += 1
            raw = trx.raw_response if isinstance(trx.raw_response, dict) else {}
            cambios = {}
            fecha_real = conc._instante(raw.get('date_created'))
            if fecha_real is not None and abs(fecha_real - trx.creado_en) > timedelta(minutes=1):
                cambios['creado_en'] = fecha_real
            if trx.ticket_id and trx.config_id:
                caja = asoc._caja_de_la_tienda(trx.config, trx.ticket.sucursal_id, raw)
                if caja is not None and caja.id != trx.config_id:
                    cambios['config_id'] = caja.id
                    cambios['sucursal_id'] = caja.sucursal_id
            if not cambios:
                continue
            cambiadas += 1
            detalle = []
            if 'creado_en' in cambios:
                detalle.append(f"fecha {timezone.localtime(trx.creado_en):%d-%m-%Y %H:%M} → "
                               f"{timezone.localtime(cambios['creado_en']):%d-%m-%Y %H:%M}")
            if 'config_id' in cambios:
                detalle.append(f'caja {trx.config.sucursal.alias} · {trx.config.nombre} → config {cambios["config_id"]}')
            self.stdout.write(f'{trx.external_reference} ticket #{trx.correlativo_ticket} ${trx.monto:,}: '
                              .replace(',', '.') + '; '.join(detalle))
            if aplicar:
                TransaccionMercadoPago.objects.filter(pk=trx.pk).update(**cambios)
        modo = 'APLICADO' if aplicar else 'VISTA PREVIA (use --aplicar para escribir)'
        self.stdout.write(self.style.SUCCESS(f'{modo}: {revisadas} revisadas, {cambiadas} con cambios.'))
