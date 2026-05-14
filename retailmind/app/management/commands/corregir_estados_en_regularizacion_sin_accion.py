"""
Reclasifica Productos_Recepcionados que están en EN_REGULARIZACION pero a los
que NUNCA se inició un flujo de regularización real (sin NC emitida y sin
Solicitud_Regularizacion activa).

Contexto:
    Hasta el fix de hoy, la vista de recepción asignaba EN_REGULARIZACION
    directamente al detectar cualquier problema (faltante, dañado, parcial).
    Eso confundía los tabs "Pendientes" y "En regularización" del panel de
    Por regularizar porque ambos mostraban exactamente los mismos registros.

    El estado correcto al DETECTAR el problema es el tipo específico
    (FALTANTE / RECEPCIONADO_DANADO / RECEPCIONADO_PARCIAL). EN_REGULARIZACION
    solo se asigna cuando alguien inicia el flujo (NC o solicitud al emisor).

    Este comando recorre los registros viejos en EN_REGULARIZACION y los
    reclasifica al estado correcto SOLO si:
      - No tienen NC asociada (no existe un Dte hijo es_nota_credito=True
        con documento_afectado=recepcion.dte y referencias que mencione
        "regularización").
      - No tienen una Solicitud_Regularizacion activa
        (estado in {PENDIENTE, EN_REVISION, APROBADA, EJECUTADA}).

    Los registros que SÍ tienen NC o solicitud activa NO se tocan.

Uso:
    python manage.py corregir_estados_en_regularizacion_sin_accion           # dry-run
    python manage.py corregir_estados_en_regularizacion_sin_accion --limit 50
    python manage.py corregir_estados_en_regularizacion_sin_accion --apply   # escribe
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from app.models import Productos_Recepcionados, Dte, Solicitud_Regularizacion

logger = logging.getLogger('app')


SOLICITUD_ACTIVA_STATES = ('PENDIENTE', 'EN_REVISION', 'APROBADA', 'EJECUTADA')


def estado_correcto_para(recepcion):
    """Devuelve el estado que correspondería según las cantidades del registro.

    Misma lógica que la vista de recepción usa al detectar el problema.
    """
    cantidad_recibida = recepcion.stockArribado or 0
    cantidad_faltante = recepcion.cantidad_faltante or 0
    cantidad_danada = recepcion.cantidad_danada or 0
    cantidad_sobrante = recepcion.cantidad_sobrante or 0

    if cantidad_sobrante > 0 and cantidad_faltante == 0 and cantidad_danada == 0:
        return 'RECEPCIONADO_SOBRANTE'
    if cantidad_recibida == 0 and cantidad_faltante > 0:
        return 'FALTANTE'
    if cantidad_danada > 0 and cantidad_faltante == 0:
        return 'RECEPCIONADO_DANADO'
    return 'RECEPCIONADO_PARCIAL'


class Command(BaseCommand):
    help = (
        'Reclasifica Productos_Recepcionados en EN_REGULARIZACION sin NC ni '
        'solicitud activa al estado de problema correcto (FALTANTE / '
        'RECEPCIONADO_DANADO / RECEPCIONADO_PARCIAL / RECEPCIONADO_SOBRANTE). '
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
            help='Procesar solo los primeros N registros (para pruebas).',
        )
        parser.add_argument(
            '--verbose-list',
            action='store_true',
            help='Imprime una línea por cada registro reclasificado.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        limit = options['limit']
        verbose_list = options['verbose_list']

        modo = 'APPLY (escribiendo)' if apply_changes else 'DRY-RUN (sin escribir)'
        self.stdout.write(self.style.NOTICE(f'Modo: {modo}'))

        # IDs de DTEs originales que tienen una NC de regularización emitida.
        ncs_emitidas_dte_ids = set(
            Dte.objects.filter(
                es_nota_credito=True,
                documento_afectado__isnull=False,
                referencias__icontains='regularización',
            ).values_list('documento_afectado_id', flat=True).distinct()
        )

        # IDs de Productos_Recepcionados con solicitud activa.
        recepciones_con_solicitud_activa_ids = set(
            Solicitud_Regularizacion.objects.filter(
                estado__in=SOLICITUD_ACTIVA_STATES,
            ).values_list('producto_recepcionado_id', flat=True).distinct()
        )

        candidatos_qs = (
            Productos_Recepcionados.objects
            .filter(estado='EN_REGULARIZACION')
            .exclude(dte_id__in=ncs_emitidas_dte_ids)
            .exclude(id__in=recepciones_con_solicitud_activa_ids)
            .select_related('dte', 'producto_talla')
            .order_by('id')
        )

        total_en_regularizacion = Productos_Recepcionados.objects.filter(
            estado='EN_REGULARIZACION'
        ).count()
        total_candidatos = candidatos_qs.count()
        protegidos = total_en_regularizacion - total_candidatos

        self.stdout.write(self.style.NOTICE(
            f'Total en EN_REGULARIZACION: {total_en_regularizacion}'
        ))
        self.stdout.write(self.style.NOTICE(
            f'  - Con NC emitida o solicitud activa (protegidos, NO se tocan): {protegidos}'
        ))
        self.stdout.write(self.style.NOTICE(
            f'  - Reclasificables (sin acción real): {total_candidatos}'
        ))

        if limit:
            candidatos_qs = candidatos_qs[:limit]
            self.stdout.write(self.style.NOTICE(f'Limitado a los primeros {limit} registros.'))

        # Conteos por estado destino.
        conteo_destino = {
            'FALTANTE': 0,
            'RECEPCIONADO_DANADO': 0,
            'RECEPCIONADO_PARCIAL': 0,
            'RECEPCIONADO_SOBRANTE': 0,
        }

        registros_a_actualizar = []
        for r in candidatos_qs:
            nuevo = estado_correcto_para(r)
            conteo_destino[nuevo] = conteo_destino.get(nuevo, 0) + 1
            registros_a_actualizar.append((r, nuevo))
            if verbose_list:
                dte_num = r.dte.numero_documento if r.dte else '-'
                sku = r.producto_talla.sku if r.producto_talla else '-'
                self.stdout.write(
                    f'  #{r.id} (DTE {dte_num}, SKU {sku}, '
                    f'esp={r.cantidad_esperada} rec={r.stockArribado} '
                    f'falt={r.cantidad_faltante} dan={r.cantidad_danada} '
                    f'sob={r.cantidad_sobrante}) → {nuevo}'
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Resumen de reclasificación:'))
        for estado, cnt in conteo_destino.items():
            self.stdout.write(f'  {estado}: {cnt}')
        self.stdout.write(f'  Total a actualizar: {len(registros_a_actualizar)}')

        if not apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Dry-run: no se escribió nada. Corre con --apply para aplicar.'
            ))
            return

        if not registros_a_actualizar:
            self.stdout.write(self.style.SUCCESS('Nada que actualizar.'))
            return

        with transaction.atomic():
            for r, nuevo in registros_a_actualizar:
                r.estado = nuevo
                r.save(update_fields=['estado'])

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Listo. Se actualizaron {len(registros_a_actualizar)} registros.'
        ))
