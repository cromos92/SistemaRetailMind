"""
Diagnóstico (SOLO LECTURA) de devoluciones al origen mal rotuladas por
"Regularizar con NC" (hallazgo B8-01).

Hasta sep-2026, "Regularizar con NC" creaba el movimiento de devolución al
origen con concepto DEVOLUCION_NC cuando el usuario PEDÍA NC (`hacer_nc`), aunque
no se emitiera ninguna (en una GUIA nunca se emite). "Cancelar regularización"
solo revertía REGULARIZACION_TRASPASO, así que reabría la línea sin descontar esa
devolución, y al volver a regularizar el origen sumaba otra vez: stock fantasma
en la bodega de origen.

La vista ya está corregida (el concepto sigue a lo que ocurrió y cancelar
revierte también DEVOLUCION_NC colgado del documento original). Este comando
lista lo histórico para revisión MANUAL; no escribe nada, porque corregir el
stock exige confirmar en bodega si la unidad está o no.

Reporta:
  1. Movimientos DEVOLUCION_NC COMPLETADO cuyo `dte` NO es una NC (el único
     escritor así era "Regularizar con NC"), marcando si el documento tiene NC
     vigente. Sin NC vigente = devolución sin documento que la respalde.
  2. Líneas con "Regularización CANCELADA" en observaciones cuyo documento
     conserva un DEVOLUCION_NC COMPLETADO: ahí el stock del origen quedó
     inflado (la cancelación no lo revirtió).

Uso:
    python manage.py regularizacion_diagnostico_devolucion_nc [--muestra 50]
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Sum

from app.models import Dte, Movimientos_Producto, Productos_Recepcionados


class Command(BaseCommand):
    help = 'Lista devoluciones DEVOLUCION_NC colgadas del documento original (solo lectura).'

    def add_arguments(self, parser):
        parser.add_argument('--muestra', type=int, default=50, help='Filas a listar por sección.')

    def handle(self, *args, **opts):
        muestra = opts['muestra']
        movs = (
            Movimientos_Producto.objects
            .filter(concepto='DEVOLUCION_NC', estado='COMPLETADO', dte__isnull=False,
                    dte__es_nota_credito=False)
            .select_related('dte', 'ProductoTalla', 'ProductoTalla__producto__sucursal')
            .order_by('dte_id', 'id')
        )
        total = movs.count()
        unidades = movs.aggregate(t=Sum('cantidad'))['t'] or 0
        self.stdout.write(
            f'[SOLO LECTURA] DEVOLUCION_NC colgados del documento original: {total} '
            f'movimientos, {unidades} unidades.'
        )

        dte_ids = {m.dte_id for m in movs}
        con_nc_vigente = set(
            Dte.objects.filter(
                es_nota_credito=True, documento_afectado_id__in=dte_ids,
                estado_dte__in=['EMITIDO', 'ACEPTADO'],
            ).values_list('documento_afectado_id', flat=True)
        )
        por_dte = defaultdict(list)
        for m in movs:
            por_dte[m.dte_id].append(m)

        self.stdout.write('\n1) Por documento (sin NC vigente = sin respaldo tributario):')
        for n, (dte_id, lista) in enumerate(por_dte.items()):
            if n >= muestra:
                self.stdout.write(f'  … y {len(por_dte) - muestra} documentos más')
                break
            d = lista[0].dte
            respaldo = 'con NC vigente' if dte_id in con_nc_vigente else 'SIN NC vigente'
            detalle = ', '.join(
                f'{m.ProductoTalla.sku if m.ProductoTalla else "?"}'
                f'@{m.ProductoTalla.producto.sucursal.alias if m.ProductoTalla and m.ProductoTalla.producto and m.ProductoTalla.producto.sucursal else "?"}'
                f' +{m.cantidad} ({m.fecha})'
                for m in lista
            )
            self.stdout.write(
                f'  DTE id={d.id} {d.tipo_documento} #{d.numero_documento} [{respaldo}]: {detalle}'
            )

        canceladas = (
            Productos_Recepcionados.objects
            .filter(dte_id__in=dte_ids, observaciones__icontains='Regularización CANCELADA')
            .select_related('dte', 'producto_talla')
            .order_by('dte_id', 'id')
        )
        self.stdout.write(
            f'\n2) Líneas canceladas cuyo documento conserva un DEVOLUCION_NC '
            f'(origen posiblemente inflado): {canceladas.count()}'
        )
        for rec in canceladas[:muestra]:
            sku = rec.producto_talla.sku if rec.producto_talla else None
            u = sum(
                m.cantidad for m in por_dte.get(rec.dte_id, [])
                if sku is not None and m.ProductoTalla and m.ProductoTalla.sku == sku
            )
            self.stdout.write(
                f'  línea id={rec.id} DTE #{rec.dte.numero_documento} sku={sku} '
                f'estado={rec.estado} unidades DEVOLUCION_NC del sku={u}'
            )
        self.stdout.write('\nNada se modificó. Revisar a mano en bodega antes de ajustar stock.')
