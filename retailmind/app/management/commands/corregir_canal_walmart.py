"""
Diagnostica y corrige el canal_origen de pedidos ecommerce mal etiquetados
(caso reportado: pedidos de Walmart guardados como SHOPIFY) y, para los pedidos
ya facturados, actualiza el `tipo_tarjeta` del pago VENTA_INTERNET para que la
cuadratura de caja histórica clasifique el marketplace correcto.

El valor de canal_origen se guarda tal cual lo manda AllConnected; este comando
permite corregir lo ya almacenado. Por seguridad NO escribe por defecto (la BD
local apunta a producción): muestra un preview y sólo aplica con --apply.

Modos (elige uno):

  1) Diagnóstico (default, sin otros flags):
       python manage.py corregir_canal_walmart
       python manage.py corregir_canal_walmart --inspeccionar SHOPIFY
     Muestra la distribución de canal_origen y, con --inspeccionar, ejemplos de
     ese canal (numero_pedido_canal + primer item) para identificar la señal de
     Walmart.

  2) Re-normalizar alias (corrige WALLMART/WALMART_CL/LIDER/... ya almacenados):
       python manage.py corregir_canal_walmart --normalizar-alias            # preview
       python manage.py corregir_canal_walmart --normalizar-alias --apply

  3) Reasignación explícita (cuando el canal entrante era válido pero equivocado,
     ej. SHOPIFY → WALMART, acotado por un patrón del nº de pedido o por ids):
       python manage.py corregir_canal_walmart --from SHOPIFY --to WALMART --numero-contiene WMT     # preview
       python manage.py corregir_canal_walmart --from SHOPIFY --to WALMART --ids 12,15,20 --apply
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import PedidoEcommerce, TicketDetallePago
from app.views_ecommerce import _normalizar_canal, PLATAFORMA_INTERNET_POR_CANAL


class Command(BaseCommand):
    help = 'Diagnostica/corrige el canal_origen de pedidos ecommerce (caso Walmart->Shopify).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin este flag solo muestra el preview.')
        parser.add_argument('--inspeccionar', metavar='CANAL',
                            help='Modo diagnóstico: muestra ejemplos de pedidos de ese canal.')
        parser.add_argument('--normalizar-alias', action='store_true',
                            help='Re-aplica la normalización de alias a los pedidos existentes.')
        parser.add_argument('--from', dest='canal_from', metavar='CANAL',
                            help='Reasignación: canal de origen a corregir (ej. SHOPIFY).')
        parser.add_argument('--to', dest='canal_to', metavar='CANAL',
                            help='Reasignación: canal destino (ej. WALMART).')
        parser.add_argument('--numero-contiene', metavar='STR',
                            help='Reasignación: acota a pedidos cuyo numero_pedido_canal contenga STR.')
        parser.add_argument('--ids', metavar='1,2,3',
                            help='Reasignación: acota a estos ids de pedido (coma-separados).')

    def handle(self, *args, **opts):
        self.apply = opts['apply']

        if opts['normalizar_alias']:
            return self._normalizar_alias()
        if opts['canal_from'] or opts['canal_to']:
            return self._reasignar(opts)
        # Default: diagnóstico
        return self._diagnostico(opts.get('inspeccionar'))

    # ------------------------------------------------------------------ utils
    def _banner(self, titulo):
        self.stdout.write('=' * 70)
        self.stdout.write(titulo)
        self.stdout.write('=' * 70)
        if not self.apply:
            self.stdout.write(self.style.WARNING('[PREVIEW] No se escribe nada. Usa --apply para aplicar.'))

    def _actualizar_pago_internet(self, pedido, nuevo_canal):
        """Actualiza tipo_tarjeta del pago VENTA_INTERNET del ticket facturado."""
        if not pedido.ticket_id:
            return 0
        plataforma = PLATAFORMA_INTERNET_POR_CANAL.get(nuevo_canal, 'Internet')
        qs = TicketDetallePago.objects.filter(
            ticket_id=pedido.ticket_id, metodo_pago='VENTA_INTERNET'
        )
        if self.apply:
            return qs.update(tipo_tarjeta=plataforma)
        return qs.count()

    # -------------------------------------------------------------- diagnóstico
    def _diagnostico(self, inspeccionar):
        from django.db.models import Count
        self._banner('DIAGNÓSTICO canal_origen DE PEDIDOS ECOMMERCE')
        dist = (PedidoEcommerce.objects.values('canal_origen')
                .annotate(n=Count('id')).order_by('-n'))
        self.stdout.write('\nDistribución de canal_origen:')
        for row in dist:
            self.stdout.write(f"  {row['canal_origen'] or '(vacío)':<15} {row['n']:>6}")

        if inspeccionar:
            canal = inspeccionar.strip().upper()
            self.stdout.write(f'\nEjemplos de canal_origen = {canal} (más recientes):')
            ejemplos = (PedidoEcommerce.objects
                        .filter(canal_origen=canal)
                        .order_by('-fecha_recepcion')[:15])
            if not ejemplos:
                self.stdout.write('  (sin pedidos)')
            for p in ejemplos:
                primer_item = (p.items or [{}])[0] if p.items else {}
                sku = primer_item.get('sku', '')
                nombre = (primer_item.get('nombre', '') or '')[:30]
                self.stdout.write(
                    f"  RM={p.numero_ticket_rm}  nro_canal={p.numero_pedido_canal!r:<22} "
                    f"estado={p.estado:<10} item0=[{sku}] {nombre}"
                )
        self.stdout.write(
            '\nUsa --inspeccionar CANAL para ver ejemplos y detectar la señal de Walmart, '
            'luego --normalizar-alias o --from/--to para corregir.'
        )

    # ---------------------------------------------------------- normalizar alias
    def _normalizar_alias(self):
        self._banner('RE-NORMALIZACIÓN DE ALIAS DE canal_origen')
        cambios = []
        for p in PedidoEcommerce.objects.all().only('id', 'canal_origen', 'numero_ticket_rm', 'ticket_id'):
            nuevo = _normalizar_canal(p.canal_origen)
            if nuevo != (p.canal_origen or ''):
                cambios.append((p, nuevo))

        if not cambios:
            self.stdout.write(self.style.SUCCESS('\n✅ No hay alias por normalizar.'))
            return

        self.stdout.write(f'\nPedidos a normalizar: {len(cambios)}')
        for p, nuevo in cambios[:50]:
            self.stdout.write(f"  RM={p.numero_ticket_rm}  {p.canal_origen} → {nuevo}")
        if len(cambios) > 50:
            self.stdout.write(f"  ... (+{len(cambios) - 50} más)")

        if not self.apply:
            return

        pagos_tot = 0
        with transaction.atomic():
            for p, nuevo in cambios:
                p.canal_origen = nuevo
                p.save(update_fields=['canal_origen'])
                pagos_tot += self._actualizar_pago_internet(p, nuevo)
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ {len(cambios)} pedidos normalizados; {pagos_tot} pago(s) VENTA_INTERNET actualizado(s).'
        ))

    # -------------------------------------------------------------- reasignación
    def _reasignar(self, opts):
        canal_from = (opts.get('canal_from') or '').strip().upper()
        canal_to = (opts.get('canal_to') or '').strip().upper()
        if not canal_from or not canal_to:
            raise CommandError('Reasignación requiere --from y --to.')

        self._banner(f'REASIGNACIÓN DE CANAL: {canal_from} → {canal_to}')
        qs = PedidoEcommerce.objects.filter(canal_origen=canal_from)

        numero_contiene = opts.get('numero_contiene')
        if numero_contiene:
            qs = qs.filter(numero_pedido_canal__icontains=numero_contiene)
            self.stdout.write(f'Filtro: numero_pedido_canal contiene {numero_contiene!r}')

        ids_raw = opts.get('ids')
        if ids_raw:
            try:
                ids = [int(x) for x in ids_raw.split(',') if x.strip()]
            except ValueError:
                raise CommandError('--ids debe ser una lista de enteros separada por comas.')
            qs = qs.filter(id__in=ids)
            self.stdout.write(f'Filtro: ids {ids}')

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING('\nNingún pedido coincide con el filtro.'))
            return

        if not numero_contiene and not ids_raw:
            self.stdout.write(self.style.WARNING(
                f'\n⚠️ Sin --numero-contiene ni --ids: se reasignarían TODOS los {total} '
                f'pedidos {canal_from}. Acota el filtro salvo que sea intencional.'
            ))

        self.stdout.write(f'\nPedidos a reasignar: {total}')
        for p in qs.order_by('-fecha_recepcion')[:50]:
            self.stdout.write(
                f"  RM={p.numero_ticket_rm}  nro_canal={p.numero_pedido_canal!r:<22} estado={p.estado}"
            )
        if total > 50:
            self.stdout.write(f"  ... (+{total - 50} más)")

        if not self.apply:
            return

        pagos_tot = 0
        with transaction.atomic():
            for p in qs:
                p.canal_origen = canal_to
                p.save(update_fields=['canal_origen'])
                pagos_tot += self._actualizar_pago_internet(p, canal_to)
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ {total} pedidos reasignados a {canal_to}; '
            f'{pagos_tot} pago(s) VENTA_INTERNET actualizado(s).'
        ))
