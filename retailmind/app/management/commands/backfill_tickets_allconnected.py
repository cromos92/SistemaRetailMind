"""
Rellena en AllConnected el `numero_ticket_rm` de los pedidos que le faltan.

Por qué: AllConnected solo guardaba el ticket cuando ÉL hacía el push a RM. Los
pedidos que RM trajo por PULL ("Traer pedidos") le quedaron con el ticket VACÍO
—9 de 21 pendientes en el diagnóstico del 2026-08-05— y eso rompe en silencio,
del lado de AllConnected:

  - `sync_tracking_tiendas_rm_task` (columna "Tienda (RM)"), que excluye los
    pedidos sin ticket → nunca muestran el avance de picking de la tienda;
  - el comando `sincronizar_cancelaciones_rm`, que matchea por ticket.

El flujo nuevo ya confirma el ticket en cada pull; este comando arregla los
históricos. Es IDEMPOTENTE y NO destructivo: AllConnected solo rellena los
vacíos y reporta (sin pisar) los que allá tienen otro ticket.

Uso (desde retailmind/):
    python manage.py backfill_tickets_allconnected                 # DRY-RUN
    python manage.py backfill_tickets_allconnected --aplicar
    python manage.py backfill_tickets_allconnected --dias 365 --aplicar
    python manage.py backfill_tickets_allconnected --estado PENDIENTE --aplicar
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import PedidoEcommerce
from app.services.allconnected_pedidos_service import confirmar_tickets_en_allconnected


class Command(BaseCommand):
    help = ("Informa a AllConnected el numero_ticket_rm de los pedidos que RM trajo "
            "por pull (allá quedaron sin ticket). DRY-RUN por defecto.")

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=180,
                            help='Lookback sobre fecha_recepcion (default 180).')
        parser.add_argument('--estado', default='',
                            help='Filtrar por estado local (PENDIENTE/FACTURADO/...). '
                                 'Por defecto: todos.')
        parser.add_argument('--canal', default='',
                            help='Filtrar por canal_origen (PARIS, WALMART, ...).')
        parser.add_argument('--max', type=int, default=None,
                            help='Tope de pedidos a enviar en esta corrida.')
        parser.add_argument('--aplicar', action='store_true',
                            help='Envía de verdad. Sin esto: solo muestra qué enviaría.')

    def handle(self, *args, **opts):
        desde = timezone.now() - timedelta(days=opts['dias'])
        qs = (
            PedidoEcommerce.objects
            .filter(fecha_recepcion__gte=desde)
            .exclude(numero_ticket_rm='')
            .only('numero_pedido_canal', 'canal_origen', 'numero_ticket_rm',
                  'estado', 'fecha_recepcion')
            .order_by('fecha_recepcion')
        )
        if opts['estado']:
            qs = qs.filter(estado=opts['estado'].upper())
        if opts['canal']:
            qs = qs.filter(canal_origen=opts['canal'].upper())

        pedidos = list(qs[:opts['max']] if opts['max'] else qs)
        modo = 'APLICAR' if opts['aplicar'] else 'DRY-RUN (usa --aplicar para enviar)'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Backfill de tickets → AllConnected · últimos {opts["dias"]} días · {modo}'))
        self.stdout.write(f'Pedidos locales con ticket asignado: {len(pedidos)}')

        if not pedidos:
            self.stdout.write('Nada que enviar.')
            return

        if not opts['aplicar']:
            for p in pedidos[:20]:
                self.stdout.write(
                    f'  {p.canal_origen:10} {p.numero_pedido_canal[:24]:24} → {p.numero_ticket_rm}')
            if len(pedidos) > 20:
                self.stdout.write(f'  … y {len(pedidos) - 20} más.')
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN: no se envió nada. AllConnected solo rellenaría los que allá '
                'están vacíos (nunca pisa un ticket ya asignado).'))
            return

        res = confirmar_tickets_en_allconnected(pedidos_qs=pedidos)

        self.stdout.write(self.style.MIGRATE_HEADING('\nResultado'))
        if not res.get('configurado'):
            self.stdout.write(self.style.ERROR(f"  {res.get('detalle') or 'AllConnected no configurado.'}"))
            return
        self.stdout.write(f"  Enviados:       {res['enviados']}")
        self.stdout.write(self.style.SUCCESS(f"  Actualizados:   {res['actualizados']}"))
        self.stdout.write(f"  Ya tenían:      {res['ya_tenian']}")
        if res['conflictos']:
            self.stdout.write(self.style.WARNING(
                f"  Conflictos:     {res['conflictos']} (allá tienen OTRO ticket — no se pisaron)"))
        if res['no_encontrados']:
            self.stdout.write(f"  No encontrados: {res['no_encontrados']} (no existen en AllConnected)")
        if res.get('deploy_pendiente'):
            self.stdout.write(self.style.WARNING(f"  {res['detalle']}"))
            self.stdout.write(
                '  → Deployá AllConnected y volvé a correr este comando (es idempotente).')
            return
        if res.get('detalle'):
            self.stdout.write(self.style.WARNING(f"  {res['detalle']}"))
        if res.get('lotes_fallidos'):
            self.stdout.write(self.style.WARNING(
                '  Hubo lotes sin respuesta: se puede volver a correr (es idempotente).'))
