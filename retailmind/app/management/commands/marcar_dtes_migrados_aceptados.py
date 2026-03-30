"""
Comando de corrección de datos migrados desde el sistema antiguo.

Problema:
    El script de migración importó DTEs con estado_dte='EMITIDO', lo que hace que
    el módulo de compras los trate como pendientes de recepción, mostrando métricas
    de 0% de cumplimiento y cientos de unidades faltantes.

Causa:
    En el sistema antiguo 'Vigente' significa que el DTE fue emitido y procesado.
    En el nuevo sistema 'EMITIDO' significa "enviado, aún no recepcionado".

Solución:
    Actualizar a 'ACEPTADO' todos los DTEs migrados que no tienen recepciones
    registradas en el nuevo sistema (Productos_Recepcionados). Esto los excluye
    del seguimiento de recepciones pendientes sin borrar el historial.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Marca como ACEPTADO los DTEs de compra/traspaso importados desde el sistema antiguo "
        "que quedaron en estado EMITIDO sin haber pasado por el flujo de recepción del nuevo sistema."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Muestra cuántos registros se actualizarían sin hacer cambios.',
        )
        parser.add_argument(
            '--tipo-transaccion',
            nargs='+',
            default=['COMPRA', 'TRASPASO'],
            help='Tipos de transacción a incluir (default: COMPRA TRASPASO).',
        )

    def handle(self, *args, **options):
        from app.models.dte import Dte
        from app.models.compras import Productos_Recepcionados

        dry_run = options['dry_run']
        tipos = options['tipo_transaccion']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Corrigiendo DTEs migrados con estado EMITIDO...\n"
        ))

        # IDs de DTEs que YA tienen recepciones en el nuevo sistema (no tocarlos)
        dtes_con_recepcion = (
            Productos_Recepcionados.objects
            .exclude(dte_id=None)
            .values_list('dte_id', flat=True)
            .distinct()
        )

        # DTEs candidatos: EMITIDO, sin recepción registrada, del tipo indicado
        qs = Dte.objects.filter(
            estado_dte='EMITIDO',
            tipo_transaccion__in=tipos,
        ).exclude(id__in=dtes_con_recepcion)

        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No hay DTEs para actualizar."))
            return

        # Desglose informativo por tipo
        for tipo in tipos:
            count = qs.filter(tipo_transaccion=tipo).count()
            if count:
                self.stdout.write(f"  {tipo}: {count} DTE(s)")

        self.stdout.write(f"\n  Total: {total} DTE(s) serán marcados como ACEPTADO")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY-RUN] No se realizaron cambios."))
            return

        with transaction.atomic():
            updated = qs.update(estado_dte='ACEPTADO')

        self.stdout.write(self.style.SUCCESS(
            f"\nOK: {updated} DTE(s) actualizados correctamente a estado ACEPTADO."
        ))
        self.stdout.write(
            "  Los DTEs migrados ya no aparecerán como pendientes de recepción.\n"
        )
