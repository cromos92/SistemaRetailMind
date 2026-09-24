"""Reporte de Liberaciones de Mercado Pago → retiros al banco (RetiroMercadoPago).

Asocia cada venta del POS (TransaccionMercadoPago) al retiro que la llevó al
banco. Por defecto es DRY-RUN: muestra qué haría. Con --apply escribe.

Uso (`--config` = cualquier caja de la CUENTA MP; el reporte es por cuenta):

    # Detectar y registrar los retiros de TODAS las cuentas (cron cada mañana).
    # Si MP no tiene un reporte reciente lo pide y espera hasta 15 min a que esté.
    python manage.py sincronizar_liberaciones_mp --todas
    python manage.py sincronizar_liberaciones_mp --todas --esperar 0   # no esperar

    # Ver los reportes que Mercado Pago ya generó
    python manage.py sincronizar_liberaciones_mp --config 3 --listar

    # Procesar los reportes aún no aplicados (ideal en cron cada mañana)
    python manage.py sincronizar_liberaciones_mp --config 3 --pendientes --apply

    # Un reporte puntual (de la lista) o un CSV descargado del panel
    python manage.py sincronizar_liberaciones_mp --config 3 --reporte release-report-123-2026-09-23-120000.csv
    python manage.py sincronizar_liberaciones_mp --config 3 --archivo liberaciones.csv --apply

    # Pedir a MP un reporte nuevo (tarda unos minutos; después usar --pendientes)
    python manage.py sincronizar_liberaciones_mp --config 3 --pedir --desde 2026-09-22 --hasta 2026-09-23

    # Que MP genere el reporte solo después de cada retiro
    python manage.py sincronizar_liberaciones_mp --config 3 --activar-por-retiro
"""
import time

from django.core.management.base import BaseCommand, CommandError

from app.models import MercadoPagoConfig
from app.services import conciliacion_mp_service as conc
from app.services import mercadopago_service as mp


class Command(BaseCommand):
    help = 'Procesa reportes de Liberaciones de Mercado Pago y asocia ventas a retiros (dry-run por defecto)'

    def add_arguments(self, parser):
        parser.add_argument('--config', type=int, default=None,
                            help='ID de MercadoPagoConfig (cualquier caja de la cuenta)')
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument('--todas', action='store_true',
                           help='Detecta y registra los retiros de TODAS las cuentas (para cron; siempre aplica)')
        grupo.add_argument('--archivo', type=str, help='CSV descargado del panel de MP')
        grupo.add_argument('--reporte', type=str, help='file_name de un reporte ya generado en MP')
        grupo.add_argument('--pendientes', action='store_true',
                           help='Procesa los reportes de MP que aún no se aplicaron')
        grupo.add_argument('--listar', action='store_true', help='Lista los reportes generados en MP')
        grupo.add_argument('--pedir', action='store_true', help='Pide a MP un reporte nuevo (no espera)')
        grupo.add_argument('--activar-por-retiro', action='store_true',
                           help='Configura MP para generar el reporte después de cada retiro')
        parser.add_argument('--esperar', type=int, default=15,
                            help='Con --todas: minutos a esperar el reporte que se pidió a MP (0 = no esperar)')
        parser.add_argument('--desde', type=str, default=None)
        parser.add_argument('--hasta', type=str, default=None)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        if opts['todas']:
            cuentas = conc.detectar_retiros(presupuesto_seg=600)
            self._mostrar(cuentas)
            pedidos = [c for c in cuentas if c['pedido']]
            if pedidos and opts['esperar'] > 0:
                self.stdout.write(f'Esperando los reportes pedidos a Mercado Pago (hasta {opts["esperar"]} min)…')
                limite = time.monotonic() + opts['esperar'] * 60
                while pedidos and time.monotonic() < limite:
                    time.sleep(30)
                    pendientes = []
                    for c in pedidos:
                        cfg = MercadoPagoConfig.objects.get(pk=c['config_id'])
                        tarea = c['pedido'].get('task_id')
                        try:
                            listo = (conc.estado_tarea_liberaciones(cfg, tarea) if tarea else {'listo': False, 'fallido': False})
                        except mp.MercadoPagoError:
                            listo = {'listo': False, 'fallido': False}
                        if not (listo['listo'] or listo['fallido']):
                            pendientes.append(c)
                    pedidos = pendientes
                self._mostrar(conc.detectar_retiros(presupuesto_seg=600, pedir=False))
            self._avisar_sin_abono()
            return
        config = MercadoPagoConfig.objects.filter(pk=opts['config']).first() if opts['config'] else None
        if config is None:
            raise CommandError('Indique --config <id de una caja de la cuenta> (o use --todas)')
        try:
            if opts['activar_por_retiro']:
                data = conc.activar_reporte_por_retiro(config)
                self.stdout.write(self.style.SUCCESS(
                    f'Listo: execute_after_withdrawal={data.get("execute_after_withdrawal")}'))
                return
            if opts['pedir']:
                d, h = conc.rango_fechas(opts['desde'], opts['hasta'], dias_defecto=1, max_dias=60)
                tarea = conc.pedir_reporte_liberaciones(config, d, h)
                self.stdout.write(f'Pedido a MP: {tarea["begin_date"]} → {tarea["end_date"]} '
                                  f'(tarea {tarea["task_id"] or "sin id"}). Tarda unos minutos; '
                                  'después use --pendientes.')
                return
            if opts['listar'] or opts['pendientes']:
                reportes = conc.listar_reportes_liberaciones(config, limite=30)
                aplicados = conc.reportes_aplicados()
                if opts['listar']:
                    for r in reportes:
                        marca = 'APLICADO ' if r['file_name'] in aplicados else ''
                        self.stdout.write(f'{marca}{r["file_name"]}  {r["begin_date"]} → {r["end_date"]} '
                                          f'({r["origen"] or "?"}, creado {r["creado"]})')
                    if not reportes:
                        self.stdout.write('Mercado Pago no tiene reportes generados.')
                    return
                pendientes = [r for r in reportes if r['file_name'] not in aplicados]
                if not pendientes:
                    self.stdout.write(self.style.SUCCESS('No hay reportes nuevos.'))
                    return
                # Del más antiguo al más nuevo: los retiros se reconstruyen en orden.
                for r in reversed(pendientes):
                    self._procesar(config, conc.descargar_reporte_liberaciones(config, r['file_name']),
                                   r['file_name'], opts['apply'])
                return
            if opts['reporte']:
                contenido = conc.descargar_reporte_liberaciones(config, opts['reporte'])
                self._procesar(config, contenido, opts['reporte'], opts['apply'])
                return
            with open(opts['archivo'], 'rb') as fh:
                self._procesar(config, fh.read(), opts['archivo'], opts['apply'])
        except mp.MercadoPagoError as e:
            raise CommandError(e.mensaje)

    def _avisar_sin_abono(self):
        """Retiros que MP ya envió y siguen sin abono confirmado en la cartola
        (más de 2 días hábiles): queda en el log para el aviso diario."""
        from app.models import RetiroMercadoPago
        import logging
        logger = logging.getLogger('app')
        pendientes = [r for r in RetiroMercadoPago.objects.filter(visto_en_cartola=False).exclude(estado='REVERTIDO')
                      .order_by('fecha') if conc.etapa_bancaria(r)['alerta_transito']]
        if not pendientes:
            return
        detalle = ', '.join(f'{r.withdrawal_id} {r.fecha} ${r.monto:,}'.replace(',', '.') for r in pendientes[:20])
        logger.warning("Conciliación MP: %s retiro(s) enviados al banco sin abono confirmado (>2 días hábiles): %s",
                       len(pendientes), detalle)
        self.stdout.write(self.style.WARNING(f'Sin abono confirmado ({len(pendientes)}): {detalle}'))

    def _mostrar(self, cuentas):
        for c in cuentas:
            nuevos = [r for r in c['retiros'] if r.get('nuevo', True)]
            estado = c['error'] or (f'{len(nuevos)} retiro(s) nuevo(s) registrado(s)' if nuevos else 'sin retiros nuevos')
            if c['revisado_hasta']:
                estado += f' (revisado hasta {c["revisado_hasta"]})'
            if c['pedido']:
                estado += f' · pedido a MP un reporte hasta {c["pedido"].get("hasta") or "ahora"}'
            if c['error_pedido']:
                estado += f' · no se pudo pedir el reporte: {c["error_pedido"]}'
            if c['incompleto']:
                estado += ' · faltó tiempo para cruzar todas las ventas: se completa en la próxima pasada'
            self.stdout.write(f'{c["cuenta"]} ({c["cajas"]}): {estado}')
            for r in c['retiros']:
                cajas = ', '.join(f'{x["caja"]} ${x["monto"]:,}' for x in r.get('por_caja') or []).replace(',', '.')
                self.stdout.write(f'  {r["estado"]} {r["fecha"]} {r["hora"]} ${r["monto"]:,} · {cajas}'.replace(',', '.'))

    def _procesar(self, config, contenido, origen, aplicar):
        filas = conc.leer_csv(contenido)
        res = conc.procesar_reporte_liberaciones(filas, config, aplicar=False, archivo=origen)
        dias = conc.dias_para_completar(config, res, importar=aplicar)
        completo = True
        if dias:
            comp = conc.completar_numeros_mp(config, dias, presupuesto_seg=300, importar=aplicar)
            completo = not comp['sin_tiempo'] and not comp.get('fallidos')
            self.stdout.write(f'  N° de operación completados desde MP: {comp["completados"]} '
                              f'({comp["dias"]} día(s) consultados)')
            if not completo:
                self.stdout.write(self.style.WARNING(
                    '  Mercado Pago no respondió a tiempo: se aplica sin marcar el reporte (vuelva a correrlo).'))
        res = conc.procesar_reporte_liberaciones(filas, config, aplicar=aplicar,
                                                 archivo=origen if completo else '')
        modo = 'APLICADO' if aplicar else 'DRY-RUN (use --apply para escribir)'
        self.stdout.write(f'{modo} · {origen} · filas {len(filas)} · retiros {len(res["retiros"])} · '
                          f'ventas POS asociadas {res["pagos_amarrados"]} · pagos que no son del POS '
                          f'{res["pagos_sin_local"]} · quedan en MP ${res["liberado_sin_retirar"]:,}'
                          .replace(',', '.'))
        if filas and not res['retiros'] and not res['liberado_sin_retirar']:
            self.stdout.write(self.style.WARNING(f'  Nada reconocible. Columnas: {", ".join(filas[0].keys())}'))
        for r in res['retiros']:
            marca = self.style.SUCCESS('OK ') if r['estado'] == 'CONCILIADO' else self.style.WARNING('DIF')
            self.stdout.write(f'  {marca} {r["fecha"]} {r["hora"]} retiro {r["withdrawal_id"]} ${r["monto"]:,} · '
                              f'{r["pagos_pos"]}/{r["pagos"]} ventas del POS'.replace(',', '.'))
            if r['detalle']:
                self.stdout.write(f'      {r["detalle"]}')
