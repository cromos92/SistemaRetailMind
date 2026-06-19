"""
Backfill de Productos_Recepcionados.movimiento_ingreso (traza recepción↔kardex).

El flujo de recepción crea el Movimientos_Producto y, por separado, vincula la
recepción a su Producto_Talla — pero históricamente NO seteaba `movimiento_ingreso`,
así que ese FK quedó NULL en todas las filas. El flujo nuevo ya lo vincula; este
comando rellena lo histórico.

Conservador: solo vincula cuando el match (Producto_Talla + DTE + ingreso) es
INEQUÍVOCO. Los casos ambiguos o sin match se reportan, no se tocan.

Uso:
    python manage.py backfill_movimiento_ingreso            # dry-run (no escribe)
    python manage.py backfill_movimiento_ingreso --apply    # aplica los cambios
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from app.models import Productos_Recepcionados, Movimientos_Producto


class Command(BaseCommand):
    help = "Vincula Productos_Recepcionados.movimiento_ingreso a su Movimientos_Producto (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica los cambios. Sin esta bandera solo simula (dry-run).',
        )

    def handle(self, *args, **opts):
        apply = opts['apply']
        modo = 'APLICANDO' if apply else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(f'== Backfill movimiento_ingreso — {modo} =='))

        # Solo recepciones ya convertidas (producto_talla) con DTE y sin vínculo.
        pendientes = (
            Productos_Recepcionados.objects
            .filter(
                movimiento_ingreso__isnull=True,
                producto_talla__isnull=False,
                dte__isnull=False,
            )
            .select_related('producto_talla', 'dte')
        )

        total = pendientes.count()
        self.stdout.write(f'Recepciones candidatas (con producto_talla + DTE, sin vínculo): {total}')

        vinculadas = ambiguas = sin_match = 0

        for rec in pendientes.iterator(chunk_size=500):
            candidatos = list(
                Movimientos_Producto.objects.filter(
                    ProductoTalla_id=rec.producto_talla_id,
                    dte_id=rec.dte_id,
                    tipo_movimiento='INGRESO',
                ).values_list('id', flat=True)
            )

            if len(candidatos) == 1:
                mov_id = candidatos[0]
            elif len(candidatos) > 1:
                # Desambiguar por cantidad == stockArribado; si sigue ambiguo, saltar.
                exactos = list(
                    Movimientos_Producto.objects.filter(
                        id__in=candidatos, cantidad=rec.stockArribado,
                    ).values_list('id', flat=True)
                )
                if len(exactos) == 1:
                    mov_id = exactos[0]
                else:
                    ambiguas += 1
                    continue
            else:
                sin_match += 1
                continue

            if apply:
                Productos_Recepcionados.objects.filter(id=rec.id).update(movimiento_ingreso_id=mov_id)
            vinculadas += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'  Vinculadas (match único): {vinculadas}'))
        self.stdout.write(f'  Ambiguas (saltadas):      {ambiguas}')
        self.stdout.write(f'  Sin match:                {sin_match}')
        # Recepciones sin DTE: no se pueden matchear por DTE (informativo).
        sin_dte = Productos_Recepcionados.objects.filter(
            movimiento_ingreso__isnull=True, producto_talla__isnull=False, dte__isnull=True,
        ).count()
        self.stdout.write(f'  (Sin DTE, no matcheables: {sin_dte})')

        if not apply:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribió nada. Re-corré con --apply para aplicar.'))
