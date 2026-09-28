# -*- coding: utf-8 -*-
"""
Rellena `Productos_Recepcionados.sucursal_destino` en las recepciones de
TRASPASO históricas (hallazgo B14-06).

El escritor (confirmar_recepcion_api) se corrigió el 2026-07-28, pero las
recepciones anteriores quedaron con sucursal_destino NULL y el reporte de
diferencias por sucursal las agrupa en "Sin asignar": el jefe de bodega que
filtra por su tienda no ve sus faltantes/dañados previos.

Regla (la misma de `_sucursal_destino_traspaso`):
- Universo: recepciones con compra_producto_talla NULL, dte NO NULL y
  sucursal_destino NULL, cuyo DTE es tipo_transaccion='TRASPASO'.
- Destino = sucursal_destino de los TRASPASO_SALIDA del DTE, solo si hay UNO
  distinto de NULL. Si además existe TRASPASO_ENTRADA, la sucursal dueña de su
  SKU debe coincidir; si no coincide, se reporta y NO se toca.
- Sin TRASPASO_SALIDA: se usa la sucursal dueña del SKU de la(s)
  TRASPASO_ENTRADA si es única.
- Ambiguas y sin fuente: se reportan, no se tocan.
- Escribe con QuerySet.update() (no save()) para no pisar `fecha` (auto_now),
  que el reporte usa como fecha de respaldo.

Uso:
    python manage.py traspaso_backfill_destino_recepciones           # dry-run
    python manage.py traspaso_backfill_destino_recepciones --apply
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Movimientos_Producto, Productos_Recepcionados


class Command(BaseCommand):
    help = ('Backfill de Productos_Recepcionados.sucursal_destino en recepciones de '
            'traspaso (dry-run por defecto; --apply para escribir).')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin esta bandera solo simula.')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(f'== traspaso_backfill_destino_recepciones — {modo} =='))

        recepciones = (
            Productos_Recepcionados.objects
            .filter(
                compra_producto_talla__isnull=True,
                dte__isnull=False,
                dte__tipo_transaccion='TRASPASO',
                sucursal_destino__isnull=True,
            )
            .values('id', 'dte_id')
        )
        por_dte = defaultdict(list)
        for r in recepciones:
            por_dte[r['dte_id']].append(r['id'])
        total = sum(len(v) for v in por_dte.values())
        self.stdout.write(f'Recepciones candidatas: {total} en {len(por_dte)} DTE')
        if not por_dte:
            return

        dte_ids = list(por_dte)
        salidas = defaultdict(set)
        for row in (
            Movimientos_Producto.objects
            .filter(dte_id__in=dte_ids, concepto='TRASPASO_SALIDA', sucursal_destino__isnull=False)
            .order_by()
            .values('dte_id', 'sucursal_destino_id')
            .distinct()
        ):
            salidas[row['dte_id']].add(row['sucursal_destino_id'])
        entradas = defaultdict(set)
        for row in (
            Movimientos_Producto.objects
            .filter(dte_id__in=dte_ids, concepto='TRASPASO_ENTRADA')
            .order_by()
            .values('dte_id', 'ProductoTalla__producto__sucursal_id')
            .distinct()
        ):
            if row['ProductoTalla__producto__sucursal_id']:
                entradas[row['dte_id']].add(row['ProductoTalla__producto__sucursal_id'])

        asignaciones = {}          # dte_id -> sucursal_id
        conflicto, ambiguas, sin_fuente = [], [], []
        via_entrada = 0
        for dte_id in dte_ids:
            dest_sal = salidas.get(dte_id, set())
            dest_ent = entradas.get(dte_id, set())
            if len(dest_sal) == 1:
                destino = next(iter(dest_sal))
                if dest_ent and dest_ent != {destino}:
                    conflicto.append((dte_id, destino, sorted(dest_ent)))
                    continue
                asignaciones[dte_id] = destino
            elif len(dest_sal) > 1:
                ambiguas.append((dte_id, sorted(dest_sal)))
            elif len(dest_ent) == 1:
                asignaciones[dte_id] = next(iter(dest_ent))
                via_entrada += 1
            elif len(dest_ent) > 1:
                ambiguas.append((dte_id, sorted(dest_ent)))
            else:
                sin_fuente.append(dte_id)

        filas_asignables = sum(len(por_dte[d]) for d in asignaciones)
        self.stdout.write(f'  Asignables: {filas_asignables} recepciones en {len(asignaciones)} DTE '
                          f'({via_entrada} DTE resueltos por TRASPASO_ENTRADA)')
        self.stdout.write(f'  Conflicto salida vs entrada (no se tocan): {len(conflicto)} DTE')
        for dte_id, dest, ent in conflicto[:20]:
            self.stdout.write(f'    dte {dte_id}: salida->{dest} entrada->{ent}')
        self.stdout.write(f'  Ambiguas (no se tocan): {len(ambiguas)} DTE')
        for dte_id, dests in ambiguas[:20]:
            self.stdout.write(f'    dte {dte_id}: destinos {dests}')
        self.stdout.write(f'  Sin fuente (no se tocan): {len(sin_fuente)} DTE {sin_fuente[:20]}')

        resumen = defaultdict(int)
        for dte_id, suc in asignaciones.items():
            resumen[suc] += len(por_dte[dte_id])
        self.stdout.write('  Por sucursal destino: ' + ', '.join(
            f'{suc}: {n}' for suc, n in sorted(resumen.items())))

        if not aplicar:
            self.stdout.write(self.style.NOTICE('Dry-run: no se escribió nada. Usa --apply para aplicar.'))
            return

        actualizadas = 0
        with transaction.atomic():
            for dte_id, suc in asignaciones.items():
                actualizadas += Productos_Recepcionados.objects.filter(
                    id__in=por_dte[dte_id], sucursal_destino__isnull=True,
                ).update(sucursal_destino_id=suc)
        self.stdout.write(self.style.SUCCESS(f'Actualizadas: {actualizadas} recepciones.'))
