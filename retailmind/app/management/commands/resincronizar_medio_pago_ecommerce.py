"""
Resincroniza el `tipo_tarjeta` del pago VENTA_INTERNET de los pedidos ecommerce
YA FACTURADOS con la plataforma/medio que tiene hoy el pedido.

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

Costo de las consultas
----------------------
Acota SIEMPRE que puedas: la BD de trabajo es la de producción (DigitalOcean) y
cada round-trip se paga en latencia de red. El comando ya trabaja por lotes
(unas pocas consultas en total, no una por pedido), pero además:

  * ``--canal`` / ``--desde`` / ``--hasta`` / ``--dias`` recortan en SQL, sobre
    columnas indexadas (`canal_origen`, `fecha_recepcion`, `medio_pago`).
  * Los pedidos de ecommerce propio SIN medio definido se descartan en la
    propia query: son la mayor parte del histórico y no hay nada que hacerles.

MercadoLibre entró recién en **septiembre de 2026**, así que repararlo NO
necesita barrer el año entero:

    python manage.py resincronizar_medio_pago_ecommerce --canal MERCADOLIBRE --desde 2026-09-01
    python manage.py resincronizar_medio_pago_ecommerce --canal MERCADOLIBRE --desde 2026-09-01 --apply

Por seguridad NO escribe por defecto (la BD local apunta a producción):
muestra el preview y sólo aplica con --apply.

    python manage.py resincronizar_medio_pago_ecommerce                     # preview (todo)
    python manage.py resincronizar_medio_pago_ecommerce --dias 30           # preview del último mes
    python manage.py resincronizar_medio_pago_ecommerce --canal REALSPORT --apply
"""
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.models import (
    PLATAFORMA_INTERNET_POR_CANAL,
    PedidoEcommerce,
    TicketDetallePago,
)
from app.views_ecommerce import (
    PREFIJO_TIPO_TARJETA_ECOMMERCE,
    _normalizar_canal,
    tipo_tarjeta_venta_internet,
)

# Tamaño de los `IN (...)` que se mandan a Postgres. Ni uno por pedido (miles de
# round-trips contra DigitalOcean: era lo que hacía lento al comando) ni todo de
# una (un IN de 20k parámetros hace que el planner descarte el índice).
CHUNK_LECTURA = 2000
CHUNK_ESCRITURA = 1000


def _lotes(secuencia, tamano):
    for i in range(0, len(secuencia), tamano):
        yield secuencia[i:i + tamano]


class Command(BaseCommand):
    help = ('Reescribe el tipo_tarjeta del pago de pedidos ecommerce ya facturados '
            'segun la plataforma/medio actual del pedido.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin este flag solo muestra el preview.')
        parser.add_argument('--canal', metavar='CANAL',
                            help='Acota a un canal_origen. Acepta alias '
                                 '(MERCADOLIBRE -> MERCADO, WALLMART -> WALMART).')
        parser.add_argument('--desde', metavar='YYYY-MM-DD',
                            help='Acota a pedidos recibidos desde esa fecha (inclusive).')
        parser.add_argument('--hasta', metavar='YYYY-MM-DD',
                            help='Acota a pedidos recibidos hasta esa fecha (inclusive).')
        parser.add_argument('--dias', type=int, metavar='N',
                            help='Atajo de --desde: ultimos N dias. Ignorado si das --desde.')
        parser.add_argument('--limite', type=int, default=0,
                            help='Procesa como maximo N pedidos (0 = sin limite).')

    # ------------------------------------------------------------------ scope

    def _fecha(self, valor, flag):
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'{flag} debe ser YYYY-MM-DD')

    def _queryset_pedidos(self, opts):
        """Pedidos facturados que PUEDEN tener algo que reclasificar.

        El descarte de "ecommerce propio sin medio informado" se hace acá, en
        SQL, y no iterando en Python: es la mayor parte del histórico (todo lo
        anterior al campo `medio_pago`) y traerlo sólo para saltarlo era
        gratis en CPU pero carísimo en red.
        """
        qs = PedidoEcommerce.objects.filter(ticket__isnull=False)

        if opts['canal']:
            # `_normalizar_canal` para que --canal MERCADOLIBRE encuentre los
            # pedidos guardados como 'MERCADO' (es el alias que aplica la
            # ingesta). Sin esto el comando devolvía 0 y parecía que no había
            # nada que reparar.
            qs = qs.filter(canal_origen=_normalizar_canal(opts['canal']))

        desde = None
        if opts['desde']:
            desde = self._fecha(opts['desde'], '--desde')
        elif opts['dias']:
            desde = timezone.localdate() - timedelta(days=opts['dias'])
        if desde:
            qs = qs.filter(fecha_recepcion__date__gte=desde)
        if opts['hasta']:
            qs = qs.filter(fecha_recepcion__date__lte=self._fecha(opts['hasta'], '--hasta'))

        # Prefiltro: sólo marketplace (la plataforma manda) o ecommerce propio
        # CON medio definido. El resto resuelve a 'Ecommerce' pelado y se
        # saltaría igual más abajo.
        #
        # Se listan las variantes de caja del canal en vez de usar `iexact`
        # para no perder el índice de `canal_origen`: la ingesta normaliza a
        # mayúsculas, las otras son red de seguridad por si quedó algo viejo.
        canales_marketplace = [
            variante
            for canal in PLATAFORMA_INTERNET_POR_CANAL
            for variante in {canal, canal.lower(), canal.title()}
        ]
        qs = qs.filter(Q(canal_origen__in=canales_marketplace) | ~Q(medio_pago=''))

        qs = qs.order_by('fecha_recepcion')
        if opts['limite']:
            qs = qs[:opts['limite']]
        return qs

    # ------------------------------------------------------------------ handle

    def handle(self, *args, **opts):
        t0 = time.monotonic()
        consultas = 0

        # ── 1 consulta: el scope completo, sin instanciar modelos ──────────
        filas = list(
            self._queryset_pedidos(opts).values_list(
                'ticket_id', 'numero_ticket_rm', 'canal_origen', 'medio_pago')
        )
        consultas += 1

        # ── `tipo_tarjeta` esperado por ticket (puro Python, sin BD) ───────
        esperado_por_ticket = {}
        conflictivos = set()
        for ticket_id, rm, canal, medio in filas:
            esperado = tipo_tarjeta_venta_internet(canal, medio)
            if esperado == PREFIJO_TIPO_TARJETA_ECOMMERCE:
                continue  # red de seguridad: el prefiltro SQL ya los sacó
            previo = esperado_por_ticket.get(ticket_id)
            if previo is not None and previo[0] != esperado:
                # Dos pedidos distintos apuntando al MISMO ticket y pidiendo
                # plataformas distintas. Reescribir cualquiera de las dos sería
                # inventar: se deja como está y se reporta.
                conflictivos.add(ticket_id)
                continue
            esperado_por_ticket[ticket_id] = (esperado, rm, canal, medio)
        for ticket_id in conflictivos:
            esperado_por_ticket.pop(ticket_id, None)

        # ── N/2000 consultas: los pagos de esos tickets ───────────────────
        cambios = []          # (pago_id, actual, esperado, rm, canal, medio)
        ticket_ids = list(esperado_por_ticket)
        for lote in _lotes(ticket_ids, CHUNK_LECTURA):
            pagos = TicketDetallePago.objects.filter(
                ticket_id__in=lote, metodo_pago='VENTA_INTERNET',
            ).values_list('id', 'ticket_id', 'tipo_tarjeta')
            consultas += 1
            for pago_id, ticket_id, actual in pagos:
                esperado, rm, canal, medio = esperado_por_ticket[ticket_id]
                if (actual or '') != esperado:
                    cambios.append((pago_id, actual or '', esperado, rm, canal, medio))

        self._imprimir_resumen(filas, esperado_por_ticket, conflictivos, cambios,
                               t0, consultas)

        if not cambios:
            self.stdout.write(self.style.SUCCESS('Nada que resincronizar.'))
            return

        if not opts['apply']:
            self.stdout.write(self.style.WARNING(
                '[PREVIEW] No se escribio nada. Usa --apply para aplicar.'))
            return

        # ── 1 UPDATE por plataforma destino (no uno por pago) ──────────────
        por_esperado = defaultdict(list)
        for pago_id, _actual, esperado, *_resto in cambios:
            por_esperado[esperado].append(pago_id)

        escritos = 0
        with transaction.atomic():
            for esperado, pago_ids in por_esperado.items():
                for lote in _lotes(pago_ids, CHUNK_ESCRITURA):
                    escritos += TicketDetallePago.objects.filter(
                        id__in=lote).update(tipo_tarjeta=esperado)
                    consultas += 1

        self.stdout.write(self.style.SUCCESS(
            f'{escritos} pago(s) reclasificado(s) en {len(por_esperado)} grupo(s). '
            f'{consultas} consultas, {time.monotonic() - t0:.1f}s.'))
        self.stdout.write(
            'Regenera la cuadratura de los dias afectados para verlo en el Resumen de Caja.')

    # ----------------------------------------------------------------- salida

    def _imprimir_resumen(self, filas, esperado_por_ticket, conflictivos, cambios,
                          t0, consultas):
        self.stdout.write('=' * 78)
        self.stdout.write(f'Pedidos facturados en el scope:        {len(filas)}')
        self.stdout.write(f'Con plataforma/medio resoluble:        {len(esperado_por_ticket)}')
        self.stdout.write(f'Pagos con tipo_tarjeta desactualizado: {len(cambios)}')
        self.stdout.write(
            f'({consultas} consultas, {time.monotonic() - t0:.1f}s de lectura)')
        self.stdout.write('=' * 78)

        if conflictivos:
            self.stdout.write(self.style.WARNING(
                f'{len(conflictivos)} ticket(s) con 2+ pedidos que piden plataformas '
                f'distintas: se OMITEN. Ids de ticket: '
                f'{sorted(conflictivos)[:20]}'))

        if not cambios:
            return

        # Resumen por canal: es lo que dice DÓNDE está el trabajo y permite
        # volver a correr acotado (p. ej. MercadoLibre es todo de septiembre).
        por_canal = Counter(canal for *_x, canal, _m in cambios)
        self.stdout.write('Por canal:')
        for canal, total in por_canal.most_common():
            self.stdout.write(f'  {canal or "(sin canal)":<14} {total:>6}')
        self.stdout.write('-' * 78)

        for pago_id, actual, esperado, rm, canal, medio in cambios[:40]:
            self.stdout.write(
                f'  {rm:>14}  {canal:<12} {(medio or "-"):<14} '
                f'"{actual}" -> "{esperado}"')
        if len(cambios) > 40:
            self.stdout.write(f'  ... y {len(cambios) - 40} mas.')
