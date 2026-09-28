"""
Cierra los traspasos que quedaron en RECEPCIONADO_PARCIAL sin ninguna línea
abierta (hallazgo B8-03).

`regularizar_dte_masivo` (NC masiva de "Regularizar DTE completo") era la única
ruta de regularización que no llamaba al cierre canónico
`_recalcular_estado_dte`: marcaba todas las líneas REGULARIZADO pero dejaba el
documento en RECEPCIONADO_PARCIAL. Esos DTE aparecen como "con diferencias
pendientes" en los KPI de traspasos, en Recepción DTE y en los reportes de
recepciones con problemas. La vista ya está corregida; este comando repara lo
histórico.

Qué hace: para cada Dte TRASPASO en RECEPCIONADO_PARCIAL aplica la MISMA regla
de la vista (`app.views._recalcular_estado_dte`): si no queda ninguna línea en
OPEN_LINE_STATES pasa a RECEPCIONADO_COMPLETO. Solo cambia `estado_dte`; no
toca montos, stock, líneas ni NC. Estados fijados por acción explícita
(EMITIDO/ACEPTADO/RECHAZADO/ANULADO/CANCELADO) no se tocan.

Uso:
    python manage.py regularizacion_cerrar_dtes_sin_lineas_abiertas            # dry-run
    python manage.py regularizacion_cerrar_dtes_sin_lineas_abiertas --apply

En producción: correr primero el dry-run y pedir OK antes de --apply.

DUPLICADO (revisión 26-sep): `traspaso_recalcular_estado` (unidad V3,
B7-06/B14-12) repara lo mismo con la misma regla (OPEN_LINE_STATES), cubre
además EN_REGULARIZACION y deja marca en `referencias`. En el runbook de
producción usar SOLO ese. Este comando queda en pendientes_para_limpieza para
que la unidad D lo retire; ambos son idempotentes, así que correr los dos no
rompe nada, pero no hace falta.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from app.models import Dte, Productos_Recepcionados


class Command(BaseCommand):
    help = ('Pasa a RECEPCIONADO_COMPLETO los traspasos en RECEPCIONADO_PARCIAL '
            'sin líneas abiertas (dry-run por defecto).')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Aplica los cambios.')
        parser.add_argument('--muestra', type=int, default=50,
                            help='Cuántos documentos listar en el informe.')

    def handle(self, *args, **opts):
        # Misma fuente de verdad que las vistas (no se duplica la regla).
        from app.views import OPEN_LINE_STATES, _recalcular_estado_dte

        self.stdout.write(self.style.WARNING(
            'AVISO: comando duplicado; usar `traspaso_recalcular_estado` '
            '(misma regla, cubre también EN_REGULARIZACION).'
        ))
        aplicar = opts['apply']
        parciales = (
            Dte.objects.filter(tipo_transaccion='TRASPASO', estado_dte='RECEPCIONADO_PARCIAL')
            .select_related('sucursal')
            .annotate(
                n_lineas=Count('recepciones', distinct=True),
                n_abiertas=Count(
                    'recepciones',
                    filter=Q(recepciones__estado__in=OPEN_LINE_STATES),
                    distinct=True,
                ),
            )
            .order_by('fecha_emision', 'id')
        )
        candidatos = [d for d in parciales if d.n_lineas > 0 and d.n_abiertas == 0]

        modo = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(
            f'[{modo}] Traspasos en RECEPCIONADO_PARCIAL: {parciales.count()} — '
            f'sin líneas abiertas (a cerrar): {len(candidatos)}'
        )
        ncs_por_dte = dict(
            Dte.objects.filter(
                es_nota_credito=True, documento_afectado_id__in=[d.id for d in candidatos],
            ).values('documento_afectado_id').annotate(n=Count('id'))
            .values_list('documento_afectado_id', 'n')
        )
        for d in candidatos[:opts['muestra']]:
            self.stdout.write(
                f'  DTE id={d.id} {d.tipo_documento} #{d.numero_documento} '
                f'origen={d.sucursal.alias if d.sucursal else "-"} '
                f'fecha={d.fecha_emision} líneas={d.n_lineas} NC hijas={ncs_por_dte.get(d.id, 0)}'
            )
        if len(candidatos) > opts['muestra']:
            self.stdout.write(f'  … y {len(candidatos) - opts["muestra"]} más')

        if not aplicar:
            self.stdout.write('Dry-run: no se escribió nada. Usa --apply para aplicar.')
            return

        cerrados = 0
        with transaction.atomic():
            for d in candidatos:
                dte = Dte.objects.select_for_update().get(id=d.id)
                # Revalidar dentro del lock: alguien pudo reabrir una línea.
                if Productos_Recepcionados.objects.filter(
                    dte=dte, estado__in=OPEN_LINE_STATES,
                ).exists():
                    continue
                if _recalcular_estado_dte(dte) == 'RECEPCIONADO_COMPLETO':
                    cerrados += 1
        self.stdout.write(self.style.SUCCESS(f'Cerrados: {cerrados}'))
