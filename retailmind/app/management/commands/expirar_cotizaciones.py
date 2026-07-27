"""
Marca en estado VENCIDA las cotizaciones a empresa cuya fecha de validez ya pasó.

Por qué existe
--------------
El ÚNICO lugar del sistema donde una cotización pasaba a VENCIDA era
`Cotizacion_Empresa.save()`: el estado solo se recalculaba si alguien volvía a
guardar la cotización. Como nadie vuelve a guardar una cotización que el cliente
no contestó, el campo `estado` quedaba permanentemente desactualizado (en
producción: 0 filas en VENCIDA con 4 cotizaciones ya vencidas).

La pantalla de gestión (`views_modulo_cotizaciones.py`) ya calcula la vigencia
por fecha y por eso "se ve bien", pero cualquier otro consumidor —reportes, API
móvil, dashboards— lee el campo y ve VIGENTE algo que caducó hace meses.

Qué hace exactamente
--------------------
Un único UPDATE acotado a:
    estado == VIGENTE  AND  facturada == False  AND  fecha_validez < hoy

Es IDEMPOTENTE: después del update esas filas quedan en VENCIDA y dejan de
casar con el filtro, así que una segunda pasada afecta 0 registros. No borra
nada, no toca montos, ítems ni despachos, y no reabre nada: reactivar una
cotización vencida sigue siendo una acción manual de la pantalla
(`views_modulo_cotizaciones.py`, que devuelve el estado a VIGENTE).

Se usa `.update()` a propósito, no `.save()`: evita disparar el `save()` del
modelo (que recalcularía `fecha_validez`) y preserva `updated_at`, que es
`auto_now` — así el histórico no queda marcado como "modificado hoy" por una
tarea automática.

SEGURO POR DEFECTO: sin `--apply` solo informa.

    python manage.py expirar_cotizaciones            # dry-run (no escribe)
    python manage.py expirar_cotizaciones --dry-run  # idem, explícito
    python manage.py expirar_cotizaciones --apply    # aplica
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Cotizacion_Empresa

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = ('Marca como VENCIDA las cotizaciones VIGENTES no facturadas cuya '
            'fecha de validez ya pasó. Sin --apply solo informa.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Aplica los cambios. Sin este flag el comando no escribe nada.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo informa (comportamiento por defecto). Se acepta por simetría.',
        )

    def handle(self, *args, **options):
        aplicar = options['apply'] and not options['dry_run']
        hoy = timezone.localdate()

        vencidas_qs = Cotizacion_Empresa.objects.filter(
            estado=Cotizacion_Empresa.ESTADO_VIGENTE,
            facturada=False,
            fecha_validez__isnull=False,
            fecha_validez__lt=hoy,
        )

        detalle = list(
            vencidas_qs.values_list(
                'id', 'numero_cotizacion', 'fecha_validez', 'sucursal__alias'
            ).order_by('fecha_validez')
        )

        if not detalle:
            self.stdout.write(self.style.SUCCESS(
                f'>> Nada que hacer: no hay cotizaciones VIGENTES vencidas al {hoy}.'
            ))
            return

        self.stdout.write(f'Cotizaciones VIGENTES con validez < {hoy}: {len(detalle)}')
        for cot_id, numero, validez, sucursal in detalle:
            self.stdout.write(
                f'   #{cot_id:<6} {numero:<22} validez={validez} sucursal={sucursal or "-"}'
            )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Se marcarían {len(detalle)} cotizaciones como VENCIDA. '
                f'Vuelve a correr con --apply para aplicarlo.'
            ))
            return

        # El filtro se vuelve a evaluar dentro del UPDATE, así que si otra
        # sesión facturó una de estas cotizaciones entre el listado y el
        # update, esa fila ya no casa y queda intacta.
        actualizadas = vencidas_qs.update(estado=Cotizacion_Empresa.ESTADO_VENCIDA)

        logger.info(
            "expirar_cotizaciones: %s cotizaciones marcadas VENCIDA (fecha_validez < %s)",
            actualizadas, hoy,
        )
        self.stdout.write(self.style.SUCCESS(
            f'>> {actualizadas} cotizaciones marcadas como VENCIDA.'
        ))
