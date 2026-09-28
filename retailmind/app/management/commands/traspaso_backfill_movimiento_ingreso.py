# -*- coding: utf-8 -*-
"""
Enlaza `Productos_Recepcionados.movimiento_ingreso` en las recepciones de
TRASPASO históricas (hallazgo B14-05).

`backfill_movimiento_ingreso` busca el movimiento por
`ProductoTalla_id = recepcion.producto_talla_id`, pero en un traspaso la
recepción apunta al SKU del ORIGEN y el TRASPASO_ENTRADA se escribe sobre el
SKU del DESTINO: nunca matchea (en la copia local, 6 únicos y 2.153 sin match).
Desde el 26-sep-2026 confirmar_recepcion_api ya enlaza al crear; esto cubre
el histórico.

Regla:
- Universo: movimiento_ingreso NULL, compra_producto_talla NULL, producto_talla
  NO NULL y DTE de TRASPASO.
- Candidatos: TRASPASO_ENTRADA COMPLETADO con cantidad > 0 del mismo DTE y el
  mismo SKU (el de la recepción).
- Lo que debió ingresar = min(stockArribado, cantidad_esperada) - cantidad_danada.
  Si es <= 0 no entró nada: se salta (no hay movimiento que enlazar).
- Un candidato único se enlaza; si hay varios, se desambigua por cantidad ==
  lo que debió ingresar. Un movimiento nunca se enlaza a dos recepciones.
- Escribe con QuerySet.update().

Uso:
    python manage.py traspaso_backfill_movimiento_ingreso           # dry-run
    python manage.py traspaso_backfill_movimiento_ingreso --apply
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Movimientos_Producto, Productos_Recepcionados


class Command(BaseCommand):
    help = ('Enlaza movimiento_ingreso de recepciones de traspaso con su TRASPASO_ENTRADA '
            '(dry-run por defecto; --apply para escribir).')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin esta bandera solo simula.')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(f'== traspaso_backfill_movimiento_ingreso — {modo} =='))

        recepciones = list(
            Productos_Recepcionados.objects
            .filter(
                movimiento_ingreso__isnull=True,
                compra_producto_talla__isnull=True,
                producto_talla__isnull=False,
                dte__isnull=False,
                dte__tipo_transaccion='TRASPASO',
            )
            .values('id', 'dte_id', 'producto_talla__sku', 'stockArribado',
                    'cantidad_esperada', 'cantidad_danada')
            .order_by('dte_id', 'id')
        )
        self.stdout.write(f'Recepciones candidatas: {len(recepciones)}')
        if not recepciones:
            return

        dte_ids = {r['dte_id'] for r in recepciones}
        # Movimientos ya enlazados por otra recepción: no se reutilizan.
        ya_enlazados = set(
            Productos_Recepcionados.objects
            .filter(dte_id__in=dte_ids, movimiento_ingreso__isnull=False)
            .values_list('movimiento_ingreso_id', flat=True)
        )
        entradas = defaultdict(list)   # (dte_id, sku) -> [(mov_id, cantidad)]
        for row in (
            Movimientos_Producto.objects
            .filter(dte_id__in=dte_ids, concepto='TRASPASO_ENTRADA',
                    estado='COMPLETADO', cantidad__gt=0)
            .order_by('id')
            .values('id', 'dte_id', 'ProductoTalla__sku', 'cantidad')
        ):
            if row['id'] in ya_enlazados:
                continue
            entradas[(row['dte_id'], row['ProductoTalla__sku'])].append((row['id'], row['cantidad']))

        usados = set()
        enlaces = []                  # (recepcion_id, mov_id)
        sin_ingreso = ambiguas = sin_match = 0
        for r in recepciones:
            debio_ingresar = (
                min(int(r['stockArribado'] or 0), int(r['cantidad_esperada'] or 0))
                - int(r['cantidad_danada'] or 0)
            )
            if debio_ingresar <= 0:
                sin_ingreso += 1
                continue
            candidatos = [
                (mid, cant) for mid, cant in entradas.get((r['dte_id'], r['producto_talla__sku']), [])
                if mid not in usados
            ]
            if not candidatos:
                sin_match += 1
                continue
            if len(candidatos) == 1:
                elegido = candidatos[0][0]
            else:
                exactos = [mid for mid, cant in candidatos if cant == debio_ingresar]
                if len(exactos) != 1:
                    ambiguas += 1
                    continue
                elegido = exactos[0]
            usados.add(elegido)
            enlaces.append((r['id'], elegido))

        self.stdout.write(f'  Enlazables (únicos): {len(enlaces)}')
        self.stdout.write(f'  Sin ingreso (no entró nada, se saltan): {sin_ingreso}')
        self.stdout.write(f'  Ambiguas (no se tocan): {ambiguas}')
        self.stdout.write(f'  Sin match (no se tocan): {sin_match}')

        if not aplicar:
            self.stdout.write(self.style.NOTICE('Dry-run: no se escribió nada. Usa --apply para aplicar.'))
            return

        actualizadas = 0
        with transaction.atomic():
            for rec_id, mov_id in enlaces:
                actualizadas += Productos_Recepcionados.objects.filter(
                    id=rec_id, movimiento_ingreso__isnull=True,
                ).update(movimiento_ingreso_id=mov_id)
        self.stdout.write(self.style.SUCCESS(f'Enlazadas: {actualizadas} recepciones.'))
