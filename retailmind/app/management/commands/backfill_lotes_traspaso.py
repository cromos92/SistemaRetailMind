"""Repara el stock que entró por traspaso y quedó sin lote FIFO.

Hasta el arreglo de 2026-07-26, la recepción de un traspaso sumaba
`Producto_Talla.stock` con un bulk update y nunca creaba el `LoteProducto`,
mientras que el despacho en el origen sí consumía lotes. Resultado: la
mercadería llegaba a la tienda sin costo FIFO, y el margen de tienda quedaba
inauditable.

Este comando detecta las tallas donde el stock supera lo respaldado por lotes
y crea un lote por la diferencia, costeándolo con el movimiento de entrada.

Uso (desde retailmind/):

    python manage.py backfill_lotes_traspaso                    # dry-run
    python manage.py backfill_lotes_traspaso --sucursal 3
    python manage.py backfill_lotes_traspaso --desde 2026-04-17
    python manage.py backfill_lotes_traspaso --apply            # escribe

Es idempotente: los lotes que crea llevan la marca BACKFILL_TRASPASO en sus
observaciones y, al recalcular la diferencia, una segunda corrida no encuentra
nada que reparar.
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from app.models import LoteProducto, Movimientos_Producto, Producto_Talla, Sucursal

MARCA = 'BACKFILL_TRASPASO'
CONCEPTOS_ENTRADA = ('TRASPASO_ENTRADA',)


class Command(BaseCommand):
    help = 'Crea los lotes FIFO faltantes del stock ingresado por traspaso'

    def add_arguments(self, parser):
        parser.add_argument('--sucursal', type=int, default=None,
                            help='ID de sucursal a reparar (por defecto, todas)')
        parser.add_argument('--desde', type=str, default=None,
                            help='Fecha mínima del movimiento de entrada (YYYY-MM-DD)')
        parser.add_argument('--hasta', type=str, default=None,
                            help='Fecha máxima del movimiento de entrada (YYYY-MM-DD)')
        parser.add_argument('--limite', type=int, default=None,
                            help='Procesar como máximo N tallas (para probar)')
        parser.add_argument('--apply', action='store_true',
                            help='Escribe en la base. Sin esta bandera solo informa.')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        desde = self._fecha(opts['desde'])
        hasta = self._fecha(opts['hasta'])

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\nBackfill de lotes FIFO por traspaso — '
            + ('APLICANDO CAMBIOS' if aplicar else 'DRY-RUN (no escribe nada)')
        ))

        # Universo: tallas que recibieron mercadería por traspaso.
        movs = Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_ENTRADA)
        if desde:
            movs = movs.filter(fecha__gte=desde)
        if hasta:
            movs = movs.filter(fecha__lte=hasta)
        if opts['sucursal']:
            movs = movs.filter(ProductoTalla__producto__sucursal_id=opts['sucursal'])

        talla_ids = list(movs.values_list('ProductoTalla_id', flat=True).distinct())
        if not talla_ids:
            self.stdout.write('No hay entradas por traspaso con esos filtros.')
            return

        self.stdout.write(f'Tallas que recibieron traspasos: {len(talla_ids):,}')

        # Stock actual vs stock respaldado por lotes vivos.
        tallas = (
            Producto_Talla.objects
            .filter(id__in=talla_ids, stock__gt=0)
            .select_related('producto', 'producto__sucursal')
        )
        respaldo = {
            r['producto_talla_id']: r['total'] or 0
            for r in LoteProducto.objects
            .filter(producto_talla_id__in=talla_ids, activo=True, agotado=False)
            .values('producto_talla_id').annotate(total=Sum('cantidad_disponible'))
        }

        pendientes = []
        for talla in tallas.iterator(chunk_size=2000):
            faltante = (talla.stock or 0) - respaldo.get(talla.id, 0)
            if faltante > 0:
                pendientes.append((talla, faltante))

        if opts['limite']:
            pendientes = pendientes[:opts['limite']]

        if not pendientes:
            self.stdout.write(self.style.SUCCESS(
                'Nada que reparar: todo el stock de traspaso ya tiene lote.'))
            return

        # Costo: el del último movimiento de entrada de esa talla; si no trae,
        # el costo del producto. Se informa cuántas unidades usan cada fuente.
        costos_mov = {}
        for m in (movs.filter(ProductoTalla_id__in=[t.id for t, _ in pendientes])
                  .order_by('ProductoTalla_id', '-id')
                  .values('ProductoTalla_id', 'costo', 'sobreprecio', 'precio')):
            costos_mov.setdefault(m['ProductoTalla_id'], m)

        lotes = []
        u_mov = u_prod = 0
        por_sucursal = {}
        for talla, faltante in pendientes:
            fuente = costos_mov.get(talla.id)
            if fuente and fuente.get('costo'):
                costo = fuente['costo']
                sobreprecio = fuente.get('sobreprecio') or 0
                precio = fuente.get('precio') or talla.producto.precioventa or 0
                u_mov += faltante
            else:
                costo = talla.producto.costo or 0
                sobreprecio = talla.producto.sobreprecio or 0
                precio = talla.producto.precioventa or 0
                u_prod += faltante

            lotes.append(LoteProducto(
                producto_talla=talla,
                cantidad_inicial=faltante,
                cantidad_disponible=faltante,
                costo_unitario=costo,
                sobreprecio_unitario=sobreprecio,
                precio_venta_unitario=precio,
                observaciones=f'{MARCA} — lote reconstruido para stock recibido sin lote',
            ))

            alias = talla.producto.sucursal.alias if talla.producto.sucursal else '(sin sucursal)'
            acc = por_sucursal.setdefault(alias, {'tallas': 0, 'unidades': 0, 'valor': 0})
            acc['tallas'] += 1
            acc['unidades'] += faltante
            acc['valor'] += faltante * costo

        self.stdout.write('\nResumen por sucursal:')
        self.stdout.write(f"  {'Sucursal':<18}{'Tallas':>8}{'Unidades':>12}{'Valorizado':>18}")
        for alias, d in sorted(por_sucursal.items(), key=lambda x: -x[1]['unidades']):
            self.stdout.write(
                f"  {alias:<18}{d['tallas']:>8,}{d['unidades']:>12,}{('$' + format(d['valor'], ',')):>18}")

        total_u = sum(d['unidades'] for d in por_sucursal.values())
        total_v = sum(d['valor'] for d in por_sucursal.values())
        self.stdout.write(
            f"  {'TOTAL':<18}{len(lotes):>8,}{total_u:>12,}{('$' + format(total_v, ',')):>18}")
        self.stdout.write(
            f'\nCosteo: {u_mov:,} unidades desde el movimiento de entrada, '
            f'{u_prod:,} desde el costo del producto (sin dato en el movimiento).')

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN: no se escribió nada. Repite con --apply para aplicar.'))
            return

        creados = 0
        with transaction.atomic():
            for i in range(0, len(lotes), 500):
                LoteProducto.objects.bulk_create(lotes[i:i + 500])
                creados += len(lotes[i:i + 500])

        self.stdout.write(self.style.SUCCESS(
            f'\nListo: {creados:,} lotes creados ({total_u:,} unidades).'))

    def _fecha(self, valor):
        if not valor:
            return None
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            raise SystemExit(f'Fecha inválida: {valor} (usa YYYY-MM-DD)')
