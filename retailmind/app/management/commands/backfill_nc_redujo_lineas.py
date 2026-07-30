# -*- coding: utf-8 -*-
"""Backfill de `Dte.redujo_lineas_documento` para las NC/ajustes históricos.

El campo nace en la migración 0196 con default False. Este comando lo corrige
para los documentos ya emitidos, deduciendo por qué flujo salió cada uno.

Reglas (en orden de confianza):

  1. `referencias` del hijo dice "(pre-recepción)"  → True
     `referencias` del hijo dice "(post-recepción)" → False
     Lo escribe `ajustar_dte_emisor_api`, que es el único flujo que deja
     marca textual explícita de la fase.

  2. El hijo tiene movimientos con concepto DEVOLUCION_NC → True.
     Ese concepto SOLO lo crea `anular_factura_dte` en sus dos ramas de NC
     parcial por línea, y ambas reducen `Dte_Productos.stock` del original.

  3. El hijo tiene movimientos post-recepción
     (DEVOLUCION_NC_POST_RECEPCION / DEVOLUCION_NC_PENDIENTE_DESPACHO /
      SOBRANTE_ABSORBIDO_ORIGEN) → False. El original se preserva intacto.

  4. Sin marca ni movimientos → False. Es la NC "por monto" (legacy), la de
     corrección de monto o una NC contable: no tocaron las líneas.

Por defecto corre en seco. Para escribir: --aplicar

    python manage.py backfill_nc_redujo_lineas
    python manage.py backfill_nc_redujo_lineas --aplicar
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Dte, Movimientos_Producto

CONCEPTOS_REDUCEN = {'DEVOLUCION_NC'}
CONCEPTOS_POST = {
    'DEVOLUCION_NC_POST_RECEPCION',
    'DEVOLUCION_NC_PENDIENTE_DESPACHO',
    'SOBRANTE_ABSORBIDO_ORIGEN',
}


class Command(BaseCommand):
    help = 'Deduce y escribe Dte.redujo_lineas_documento en las NC/ajustes ya emitidas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Escribe los cambios. Sin este flag solo informa (dry-run).',
        )
        parser.add_argument(
            '--solo-traspasos', action='store_true',
            help='Limita a NC sobre DTEs de traspaso (lo que ve recepción-DTE).',
        )

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        solo_traspasos = options['solo_traspasos']

        qs = Dte.objects.filter(documento_afectado__isnull=False)
        if solo_traspasos:
            qs = qs.filter(documento_afectado__tipo_transaccion='TRASPASO')
        qs = qs.select_related('documento_afectado').order_by('id')

        total = qs.count()
        self.stdout.write(f'NC/ajustes a evaluar: {total}')
        if not total:
            return

        # Conceptos por documento hijo en una sola query (evita N+1: son miles).
        conceptos_por_dte = {}
        for row in (
            Movimientos_Producto.objects
            .filter(dte__documento_afectado__isnull=False)
            .values('dte_id', 'concepto')
            .distinct()
        ):
            conceptos_por_dte.setdefault(row['dte_id'], set()).add(row['concepto'])

        a_true, a_false, sin_cambio = [], [], 0
        motivos = {}

        for nc in qs.iterator(chunk_size=500):
            ref = (nc.referencias or '').lower()
            conceptos = conceptos_por_dte.get(nc.id, set())

            if 'pre-recepción' in ref or 'pre-recepcion' in ref:
                valor, motivo = True, 'marca textual pre-recepcion'
            elif 'post-recepción' in ref or 'post-recepcion' in ref:
                valor, motivo = False, 'marca textual post-recepcion'
            elif conceptos & CONCEPTOS_REDUCEN:
                valor, motivo = True, 'movimiento DEVOLUCION_NC (NC por linea)'
            elif conceptos & CONCEPTOS_POST:
                valor, motivo = False, 'movimientos post-recepcion'
            else:
                valor, motivo = False, 'sin marca ni movimientos (NC por monto / contable)'

            motivos[motivo] = motivos.get(motivo, 0) + 1

            if bool(nc.redujo_lineas_documento) == valor:
                sin_cambio += 1
                continue
            (a_true if valor else a_false).append(nc.id)

        self.stdout.write('')
        self.stdout.write('Deduccion por regla:')
        for motivo, n in sorted(motivos.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {n:6}  {motivo}')

        self.stdout.write('')
        self.stdout.write(f'  ya correctos      : {sin_cambio}')
        self.stdout.write(f'  pasan a True      : {len(a_true)}')
        self.stdout.write(f'  pasan a False     : {len(a_false)}')

        if not aplicar:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: no se escribio nada. Repite con --aplicar para persistir.'
            ))
            return

        with transaction.atomic():
            if a_true:
                Dte.objects.filter(id__in=a_true).update(redujo_lineas_documento=True)
            if a_false:
                Dte.objects.filter(id__in=a_false).update(redujo_lineas_documento=False)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Backfill aplicado: {len(a_true)} a True, {len(a_false)} a False.'
        ))
