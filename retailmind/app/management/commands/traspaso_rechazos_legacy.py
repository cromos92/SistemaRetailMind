# -*- coding: utf-8 -*-
"""
Reporte (SOLO LECTURA) de traspasos rechazados ANTES del fix de jul-2026 cuyo
stock nunca volvió al origen (hallazgo B14-11).

Desde el fix, rechazar_recepcion_api devuelve el stock y deja los
TRASPASO_SALIDA en CANCELADO. Los rechazos anteriores quedaron con los
TRASPASO_SALIDA en estado RECHAZADO: las unidades están fuera de las dos
bodegas. En la copia local (datos al 2026-06-12) hay un solo caso: DTE
2181689, GUIA 4254 de NICK2, 14 SKU / 46 u.

Este comando NO corrige nada. La resolución es operativa y la decide el
negocio, con conteo físico en la sucursal emisora:
  - la mercadería está en el emisor → "Anular" desde Recepción DTE / Limbo
    (cancelar_dte_traspaso_api devuelve el stock y repone lotes);
  - se va a reenviar → "Rehabilitar" (el destino recepciona);
  - se perdió → no anular; registrar la merma con un ajuste justificado.

Uso:
    python manage.py traspaso_rechazos_legacy
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from app.models import Dte, Movimientos_Producto


class Command(BaseCommand):
    help = 'Lista traspasos rechazados antes del fix cuyo stock nunca volvió al origen (solo lectura).'

    def handle(self, *args, **opts):
        self.stdout.write(self.style.WARNING('== traspaso_rechazos_legacy — SOLO LECTURA =='))
        filas = (
            Movimientos_Producto.objects
            .filter(concepto='TRASPASO_SALIDA', estado='RECHAZADO', dte__isnull=False)
            .order_by()
            .values('dte_id')
            .annotate(skus=Count('id'), unidades=Sum('cantidad'))
        )
        filas = list(filas)
        if not filas:
            self.stdout.write(self.style.SUCCESS('Sin traspasos rechazados con stock pendiente de devolver.'))
            return
        dtes = {d.id: d for d in Dte.objects.filter(id__in=[f['dte_id'] for f in filas]).select_related('sucursal')}
        total = 0
        for f in filas:
            dte = dtes.get(f['dte_id'])
            uds = abs(int(f['unidades'] or 0))
            total += uds
            if dte is None:
                continue
            self.stdout.write(
                f"  DTE id {dte.id} {dte.tipo_documento} #{dte.numero_documento} "
                f"emisor {dte.sucursal.alias if dte.sucursal else '-'} "
                f"emitido {dte.fecha_emision} estado {dte.estado_dte}: "
                f"{f['skus']} SKU / {uds} u fuera de stock. "
                f"Motivo: {(dte.motivo_rechazo or '-')[:80]}"
            )
        self.stdout.write(f'Total: {len(filas)} DTE, {total} u.')
