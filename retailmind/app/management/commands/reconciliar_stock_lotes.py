"""
Reconcilia el stock plano (Producto_Talla.stock) contra la suma de los lotes
FIFO activos (LoteProducto.cantidad_disponible).

SOLO LECTURA: este comando no modifica ningún dato. Reporta los SKUs cuyo
stock no cuadra con sus lotes (drift), útil para auditar la integridad del
inventario FIFO antes de tomar acciones manuales.

Uso:
    python manage.py reconciliar_stock_lotes
    python manage.py reconciliar_stock_lotes --sucursal 5
    python manage.py reconciliar_stock_lotes --solo-con-lotes
    python manage.py reconciliar_stock_lotes --limit 100
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum, Q

from app.models import Producto_Talla, LoteProducto


class Command(BaseCommand):
    help = (
        'Reporta SKUs donde Producto_Talla.stock != suma de '
        'LoteProducto.cantidad_disponible (lotes activos). Solo lectura.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal',
            type=int,
            help='Filtra por ID de sucursal del producto.',
        )
        parser.add_argument(
            '--solo-con-lotes',
            action='store_true',
            help='Ignora los SKUs que no tienen ningún lote (típico de data legacy).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Máximo de filas con descuadre a listar en detalle (0 = todas).',
        )

    def handle(self, *args, **options):
        sucursal_id = options.get('sucursal')
        solo_con_lotes = options['solo_con_lotes']
        limit = options['limit']

        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('RECONCILIACIÓN STOCK PLANO vs LOTES FIFO (solo lectura)'))
        self.stdout.write(self.style.SUCCESS('=' * 70))

        qs = Producto_Talla.objects.select_related('producto', 'producto__sucursal')
        if sucursal_id:
            qs = qs.filter(producto__sucursal_id=sucursal_id)

        # Suma de lotes activos por talla, en una sola consulta agregada.
        qs = qs.annotate(
            stock_lotes=Sum(
                'lotes__cantidad_disponible',
                filter=Q(lotes__activo=True),
            )
        )

        total_skus = 0
        sin_lotes = 0
        descuadrados = []

        for pt in qs.iterator():
            total_skus += 1
            stock_plano = pt.stock or 0
            stock_lotes = pt.stock_lotes  # None si no tiene lotes activos

            if stock_lotes is None:
                sin_lotes += 1
                if solo_con_lotes:
                    continue
                stock_lotes_val = 0
            else:
                stock_lotes_val = stock_lotes

            if stock_plano != stock_lotes_val:
                descuadrados.append({
                    'sku': pt.sku,
                    'articulo': pt.producto.articulo if pt.producto else '?',
                    'talla': pt.talla,
                    'sucursal': pt.producto.sucursal.alias if pt.producto and pt.producto.sucursal else '?',
                    'stock_plano': stock_plano,
                    'stock_lotes': stock_lotes_val,
                    'diff': stock_plano - stock_lotes_val,
                    'sin_lotes': stock_lotes is None,
                })

        # ----- Resumen -----
        self.stdout.write('')
        self.stdout.write(f'  SKUs analizados:        {total_skus}')
        self.stdout.write(f'  SKUs sin lotes activos: {sin_lotes}')
        self.stdout.write(
            self.style.WARNING(f'  SKUs con descuadre:     {len(descuadrados)}')
            if descuadrados
            else self.style.SUCCESS('  SKUs con descuadre:     0')
        )

        if not descuadrados:
            self.stdout.write(self.style.SUCCESS('\n✅ Stock plano y lotes FIFO cuadran.'))
            return

        # Diferencia total absoluta (magnitud del drift)
        drift_total = sum(abs(d['diff']) for d in descuadrados)
        self.stdout.write(self.style.WARNING(f'  Drift total (|Δ|):      {drift_total} unidades\n'))

        filas = descuadrados if limit <= 0 else descuadrados[:limit]
        self.stdout.write(
            f"  {'SKU':>12} {'SUC':>6} {'TALLA':>8} {'PLANO':>8} {'LOTES':>8} {'Δ':>8}  ARTÍCULO"
        )
        self.stdout.write('  ' + '-' * 68)
        for d in filas:
            nota = ' (sin lotes)' if d['sin_lotes'] else ''
            self.stdout.write(
                f"  {str(d['sku']):>12} {d['sucursal']:>6} {str(d['talla']):>8} "
                f"{d['stock_plano']:>8} {d['stock_lotes']:>8} {d['diff']:>8}  "
                f"{(d['articulo'] or '')[:30]}{nota}"
            )

        if limit > 0 and len(descuadrados) > limit:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  ... {len(descuadrados) - limit} filas más no mostradas "
                    f"(usa --limit 0 para verlas todas)."
                )
            )

        self.stdout.write(
            self.style.WARNING(
                '\n⚠️ Descuadres detectados. Este comando NO corrige nada; '
                'revisa los SKUs listados manualmente.'
            )
        )
