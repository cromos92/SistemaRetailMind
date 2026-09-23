"""Reporte de Liberaciones de Mercado Pago → retiros al banco (RetiroMercadoPago).

Amarra cada cobro del POS (TransaccionMercadoPago) al retiro que lo llevó al
banco. Por defecto es DRY-RUN: muestra qué haría. Con --apply escribe.

Fuentes:
  --archivo reporte.csv   CSV descargado del panel de MP (Reportes → Liberaciones)
  (sin --archivo)         lo pide a la API de MP para --desde/--hasta

Uso:
    python manage.py sincronizar_liberaciones_mp --config 3 --archivo liberaciones.csv
    python manage.py sincronizar_liberaciones_mp --config 3 --desde 2026-09-01 --hasta 2026-09-21 --apply

`--config` es cualquier caja (MercadoPagoConfig) de la CUENTA del reporte: el
reporte es por cuenta (RUT), no por caja.
"""
from django.core.management.base import BaseCommand, CommandError

from app.models import MercadoPagoConfig
from app.services import conciliacion_mp_service as conc
from app.services import mercadopago_service as mp


class Command(BaseCommand):
    help = 'Procesa el reporte de Liberaciones de Mercado Pago y amarra cobros a retiros (dry-run por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--config', type=int, required=True,
                            help='ID de MercadoPagoConfig (cualquier caja de la cuenta)')
        parser.add_argument('--archivo', type=str, default='')
        parser.add_argument('--desde', type=str, default=None)
        parser.add_argument('--hasta', type=str, default=None)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        config = MercadoPagoConfig.objects.filter(pk=opts['config']).first()
        if config is None:
            raise CommandError(f'No existe MercadoPagoConfig id={opts["config"]}')
        if opts['archivo']:
            with open(opts['archivo'], 'rb') as fh:
                contenido = fh.read()
        else:
            d, h = conc.rango_fechas(opts['desde'], opts['hasta'], dias_defecto=7, max_dias=31)
            self.stdout.write(f'Pidiendo a Mercado Pago el reporte de Liberaciones {d} → {h}…')
            try:
                contenido = conc.solicitar_y_descargar_liberaciones(config, d, h)
            except mp.MercadoPagoError as e:
                raise CommandError(e.mensaje)

        filas = conc.leer_csv(contenido)
        res = conc.procesar_reporte_liberaciones(filas, config, aplicar=opts['apply'])
        modo = 'APLICADO' if opts['apply'] else 'DRY-RUN (use --apply para escribir)'
        self.stdout.write(f'{modo} · filas leídas {len(filas)} · retiros {len(res["retiros"])} · '
                          f'cobros amarrados {res["pagos_amarrados"]} · pagos sin cobro del POS '
                          f'{res["pagos_sin_local"]} · liberado sin retirar ${res["liberado_sin_retirar"]:,}'
                          .replace(',', '.'))
        for r in res['retiros']:
            marca = self.style.SUCCESS('OK ') if r['estado'] == 'CONCILIADO' else self.style.WARNING('DIF')
            self.stdout.write(f'  {marca} {r["fecha"]} retiro {r["withdrawal_id"]} ${r["monto"]:,} · '
                              f'{r["pagos_pos"]}/{r["pagos"]} cobros del POS'.replace(',', '.'))
            if r['detalle']:
                self.stdout.write(f'      {r["detalle"]}')
