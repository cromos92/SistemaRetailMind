"""
Corrige Producto.fecha_creacion usando la fecha del primer Movimientos_Producto
asociado (vía Producto_Talla).

Contexto:
    El comando migrate_from_vicent.py importó productos desde MySQL sin preservar
    la fecha original. Como Producto.fecha_creacion tiene auto_now_add=True,
    Django asignó timezone.now() (la fecha de la migración).

    Sin embargo, migrate_from_laravel.py SÍ preservó las fechas originales en
    Movimientos_Producto.fecha. Por lo tanto, la fecha real de "alta" de cada
    producto puede recuperarse del primer movimiento.

Uso:
    python manage.py corregir_fecha_creacion_productos                  # dry-run
    python manage.py corregir_fecha_creacion_productos --limit 50       # prueba 50
    python manage.py corregir_fecha_creacion_productos --producto-id 123
    python manage.py corregir_fecha_creacion_productos --apply          # aplica
"""

import logging
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db.models import Min
from django.utils import timezone

from app.models import Movimientos_Producto, Producto

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = (
        'Corrige Producto.fecha_creacion usando MIN(Movimientos_Producto.fecha). '
        'Por defecto opera en dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplicar cambios. Sin este flag corre en modo dry-run.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Procesar solo los primeros N productos (para pruebas).',
        )
        parser.add_argument(
            '--producto-id',
            type=int,
            default=None,
            help='Procesar solo el producto con este ID.',
        )
        parser.add_argument(
            '--margen-dias',
            type=int,
            default=1,
            help=(
                'Solo actualizar si la fecha del movimiento es anterior a la actual '
                'fecha_creacion en al menos N días. Default: 1.'
            ),
        )
        parser.add_argument(
            '--mostrar-muestras',
            type=int,
            default=15,
            help='Cantidad de muestras a mostrar en el reporte. Default: 15.',
        )

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        limit = opts['limit']
        producto_id = opts['producto_id']
        margen_dias = opts['margen_dias']
        n_muestras = opts['mostrar_muestras']

        modo = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(
            f'\n=== Corrección de Producto.fecha_creacion (modo {modo}) ==='
        ))
        self.stdout.write(f'  Margen mínimo de diferencia : {margen_dias} día(s)')
        if producto_id:
            self.stdout.write(f'  Filtrado a producto ID      : {producto_id}')
        if limit:
            self.stdout.write(f'  Límite de productos         : {limit}')
        self.stdout.write('')

        self.stdout.write('[1/3] Calculando MIN(fecha) de movimientos por producto...')

        qs_min = (
            Movimientos_Producto.objects
            .values('ProductoTalla__producto_id')
            .annotate(min_fecha=Min('fecha'))
        )
        if producto_id:
            qs_min = qs_min.filter(ProductoTalla__producto_id=producto_id)

        min_por_producto = {
            r['ProductoTalla__producto_id']: r['min_fecha']
            for r in qs_min
            if r['ProductoTalla__producto_id'] and r['min_fecha']
        }

        self.stdout.write(self.style.SUCCESS(
            f'      → {len(min_por_producto):,} productos con movimientos asociados'
        ))

        if not min_por_producto:
            self.stdout.write(self.style.WARNING('No hay nada que procesar.'))
            return

        self.stdout.write('[2/3] Comparando con fecha_creacion actual...')

        productos_qs = Producto.objects.filter(
            id__in=min_por_producto.keys()
        ).only('id', 'articulo', 'fecha_creacion')

        if producto_id:
            productos_qs = productos_qs.filter(id=producto_id)
        if limit:
            productos_qs = productos_qs[:limit]

        total = 0
        a_actualizar_lista = []
        sin_cambio = 0
        sin_fecha_creacion = 0

        for p in productos_qs.iterator(chunk_size=1000):
            total += 1
            min_fecha = min_por_producto.get(p.id)
            if not min_fecha:
                continue

            min_dt = timezone.make_aware(datetime.combine(min_fecha, time.min))

            if not p.fecha_creacion:
                sin_fecha_creacion += 1
                a_actualizar_lista.append((p.id, p.articulo, None, min_dt))
                continue

            diff_dias = (p.fecha_creacion.date() - min_fecha).days
            if diff_dias >= margen_dias:
                a_actualizar_lista.append((p.id, p.articulo, p.fecha_creacion, min_dt))
            else:
                sin_cambio += 1

        self.stdout.write(self.style.SUCCESS(
            f'      → {total:,} productos analizados, '
            f'{len(a_actualizar_lista):,} candidatos a actualizar'
        ))

        self.stdout.write(f'\n--- Muestra (primeros {n_muestras}) ---')
        for pid, art, antigua, nueva in a_actualizar_lista[:n_muestras]:
            antigua_str = antigua.strftime('%Y-%m-%d') if antigua else 'NULL'
            self.stdout.write(
                f'  #{pid:>7} ({art}): {antigua_str} → {nueva.strftime("%Y-%m-%d")}'
            )

        self.stdout.write('\n[3/3] ' + ('Aplicando cambios...' if apply_changes else 'Dry-run, no se escribirá nada.'))

        actualizados = 0
        if apply_changes and a_actualizar_lista:
            for pid, _art, _antigua, nueva in a_actualizar_lista:
                Producto.objects.filter(pk=pid).update(fecha_creacion=nueva)
                actualizados += 1
            logger.info(
                'corregir_fecha_creacion_productos: actualizados %d productos '
                '(margen=%d días, producto_id=%s, limit=%s)',
                actualizados, margen_dias, producto_id, limit
            )

        self.stdout.write(self.style.WARNING('\n=== Resumen ==='))
        self.stdout.write(f'  Productos analizados        : {total:,}')
        self.stdout.write(f'  Sin fecha_creacion (NULL)   : {sin_fecha_creacion:,}')
        self.stdout.write(f'  Con diferencia ≥ {margen_dias}d        : {len(a_actualizar_lista) - sin_fecha_creacion:,}')
        self.stdout.write(f'  Sin cambio (margen < {margen_dias}d)   : {sin_cambio:,}')
        self.stdout.write(f'  Total candidatos            : {len(a_actualizar_lista):,}')

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f'\n  ✓ {actualizados:,} productos actualizados.\n'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '\n  ⚠ DRY-RUN — no se aplicaron cambios. Para aplicar agrega --apply\n'
            ))
