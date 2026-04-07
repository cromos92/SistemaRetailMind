"""
Corrige el signo de la cantidad en movimientos de tipo EGRESO importados desde Laravel.

En Laravel las cantidades se almacenaban como positivas para todos los movimientos.
La convención de Django es: positiva para ingresos, NEGATIVA para egresos.
El script migrate_from_laravel.py no aplicaba esta corrección antes del fix,
por lo que los egresos migrados quedaron con cantidad positiva.

Este comando busca todos los Movimientos_Producto con:
  - tipo_movimiento = 'EGRESO'
  - referencia_externa comienza con 'MIG:' (indica origen Laravel)
  - cantidad > 0  (aún no corregidos)

Y los corrige a cantidad negativa.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import Movimientos_Producto


class Command(BaseCommand):
    help = 'Corrige el signo de cantidad en egresos importados desde Laravel (MIG:*)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra cuántos registros se corregirían sin modificar nada',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Registros por batch (default: 5000)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        self.stdout.write('=' * 70)
        self.stdout.write('CORRECCIÓN DE SIGNO EN EGRESOS MIGRADOS DESDE LARAVEL')
        self.stdout.write('=' * 70)

        # Sólo egresos importados (MIG:) que aún tienen cantidad positiva
        qs = Movimientos_Producto.objects.filter(
            tipo_movimiento='EGRESO',
            referencia_externa__startswith='MIG:',
            cantidad__gt=0,
        )

        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                '\n✅ No hay egresos migrados con cantidad positiva. No se requiere corrección.'
            ))
            return

        self.stdout.write(f'\n📋 Egresos migrados con cantidad positiva encontrados: {total:,}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\n[DRY-RUN] Se negaría la cantidad de {total:,} movimientos. '
                'Ejecuta sin --dry-run para aplicar.'
            ))
            return

        # Procesar en batches para no bloquear la DB
        corregidos = 0
        ids_batch = []

        self.stdout.write('\nProcesando...')

        for mov in qs.only('id', 'cantidad').iterator(chunk_size=batch_size):
            ids_batch.append(mov.id)

            if len(ids_batch) >= batch_size:
                corregidos += self._aplicar_batch(ids_batch)
                ids_batch = []
                self.stdout.write(f'  → {corregidos:,} / {total:,} corregidos...')

        # Último batch parcial
        if ids_batch:
            corregidos += self._aplicar_batch(ids_batch)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'✅ Corrección completada: {corregidos:,} movimientos actualizados.'
        ))
        self.stdout.write(
            'Las cantidades de egresos migrados ahora son negativas, '
            'consistentes con la convención del sistema.'
        )

    def _aplicar_batch(self, ids):
        """Negar la cantidad de todos los IDs del batch en una sola query."""
        with transaction.atomic():
            from django.db.models import F
            updated = Movimientos_Producto.objects.filter(
                id__in=ids
            ).update(cantidad=-F('cantidad'))
        return updated
