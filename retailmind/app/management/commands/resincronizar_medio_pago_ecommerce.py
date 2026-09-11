"""
Resincroniza el `tipo_tarjeta` del pago VENTA_INTERNET de los pedidos ecommerce
YA FACTURADOS con el `medio_pago` que tiene hoy el pedido.

Por qué hace falta
------------------
`PedidoEcommerce.medio_pago` se llena de tres formas: en la ingesta (si
AllConnected lo manda), a mano desde el listado de pedidos, o nunca. Las dos
primeras escriben el pago del ticket en el momento — pero un pull posterior que
RELLENA el medio de un pedido viejo sólo actualiza el pedido: el
`TicketDetallePago` que ya existe se queda con el `tipo_tarjeta` anterior, y la
cuadratura de caja lee de ahí. Sin este comando, el día que AllConnected empiece
a informar el medio, todo el histórico seguiría cayendo en la fila
"ECOMMERCE OTROS / S/DEF." del Resumen de Caja.

También repara los pedidos de MARKETPLACE mal clasificados: cuando un canal se
declara marketplace después de que ya entraron pedidos (caso MercadoLibre), esos
pagos quedaron grabados como ecommerce propio y siguen cayendo en "OTROS /
S/DEF." hasta que se les reescribe el `tipo_tarjeta`.

Qué NO hace
-----------
NO adivina el medio de pago. Un pedido de ECOMMERCE PROPIO sin `medio_pago` se
salta (queda como 'Ecommerce' pelado, que es la verdad: no sabemos con qué se
pagó). El único modo de resolverlos es que el canal lo informe o que alguien lo
fije en Ecommerce → Pedidos (filtro "⚠ Sin definir").

En un MARKETPLACE, en cambio, `medio_pago` es irrelevante — la plataforma manda
—, así que esos pedidos SÍ se procesan aunque lo tengan vacío.

Por seguridad NO escribe por defecto (la BD local apunta a producción):
muestra el preview y sólo aplica con --apply.

    python manage.py resincronizar_medio_pago_ecommerce                     # preview
    python manage.py resincronizar_medio_pago_ecommerce --apply
    python manage.py resincronizar_medio_pago_ecommerce --canal REALSPORT --apply
    python manage.py resincronizar_medio_pago_ecommerce --desde 2026-06-01 --apply
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import PedidoEcommerce, TicketDetallePago
from app.views_ecommerce import (
    PREFIJO_TIPO_TARJETA_ECOMMERCE,
    tipo_tarjeta_para_pedido,
)


class Command(BaseCommand):
    help = ('Reescribe el tipo_tarjeta del pago de pedidos ecommerce ya facturados '
            'segun el medio_pago actual del pedido.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin este flag solo muestra el preview.')
        parser.add_argument('--canal', metavar='CANAL',
                            help='Acota a un canal_origen (ej. REALSPORT).')
        parser.add_argument('--desde', metavar='YYYY-MM-DD',
                            help='Acota a pedidos recibidos desde esa fecha.')
        parser.add_argument('--limite', type=int, default=0,
                            help='Procesa como maximo N pedidos (0 = sin limite).')

    def handle(self, *args, **opts):
        aplicar = opts['apply']

        # No se filtra por `medio_pago` en la query: un pedido de marketplace lo
        # tiene vacío por diseño y aun así hay que reclasificarlo. El descarte
        # de "no sabemos con qué se pagó" se hace abajo, mirando el
        # `tipo_tarjeta` esperado.
        qs = (PedidoEcommerce.objects
              .filter(ticket__isnull=False)
              .select_related('ticket')
              .order_by('fecha_recepcion'))

        if opts['canal']:
            qs = qs.filter(canal_origen=opts['canal'].strip().upper())
        if opts['desde']:
            try:
                desde = datetime.strptime(opts['desde'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--desde debe ser YYYY-MM-DD')
            qs = qs.filter(fecha_recepcion__date__gte=desde)
        if opts['limite']:
            qs = qs[:opts['limite']]

        cambios = []
        candidatos = 0
        for pedido in qs:
            esperado = tipo_tarjeta_para_pedido(pedido)
            # 'Ecommerce' pelado = ecommerce propio sin medio informado. No se
            # toca: reescribirlo no aportaría nada y borraría un dato mejor si
            # alguien ya lo había corregido a mano.
            if esperado == PREFIJO_TIPO_TARJETA_ECOMMERCE:
                continue
            candidatos += 1
            pagos = TicketDetallePago.objects.filter(
                ticket_id=pedido.ticket_id, metodo_pago='VENTA_INTERNET',
            ).exclude(tipo_tarjeta=esperado)
            for pago in pagos:
                cambios.append((pedido, pago, pago.tipo_tarjeta, esperado))

        self.stdout.write('=' * 78)
        self.stdout.write(
            f'Pedidos facturados con plataforma resoluble: {candidatos}')
        self.stdout.write(f'Pagos con tipo_tarjeta desactualizado: {len(cambios)}')
        self.stdout.write('=' * 78)

        for pedido, pago, actual, esperado in cambios[:40]:
            self.stdout.write(
                f'  {pedido.numero_ticket_rm:>14}  {pedido.canal_origen:<10} '
                f'{(pedido.medio_pago or "-"):<14} "{actual}" -> "{esperado}"')
        if len(cambios) > 40:
            self.stdout.write(f'  ... y {len(cambios) - 40} mas.')

        if not cambios:
            self.stdout.write(self.style.SUCCESS('Nada que resincronizar.'))
            return

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '[PREVIEW] No se escribio nada. Usa --apply para aplicar.'))
            return

        with transaction.atomic():
            for _pedido, pago, _actual, esperado in cambios:
                pago.tipo_tarjeta = esperado
                pago.save(update_fields=['tipo_tarjeta'])

        self.stdout.write(self.style.SUCCESS(
            f'{len(cambios)} pago(s) reclasificado(s). '
            'Regenera la cuadratura de los dias afectados para verlo en el Resumen de Caja.'))
