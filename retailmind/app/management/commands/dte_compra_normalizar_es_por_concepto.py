"""
Corrige Dte.es_por_concepto en DTE de COMPRA (hallazgo B14-08).

Entre jul-2026 y sep-2026 crearDteCompras marcaba es_por_concepto=True en TODO
documento creado desde «Nuevo DTE Compra», incluidas las facturas que después
se recepcionan con mercadería. El KPI «Compras no inventariables» del dashboard
de compras las contaba como gasto.

Este comando pone es_por_concepto=False SOLO a los DTE de COMPRA marcados True
que tienen evidencia de inventario: Productos_Recepcionados, líneas
Dte_Productos o Movimientos_Producto con cantidad > 0. El resto (sin evidencia)
se lista para revisión manual: pueden ser gastos reales o facturas que aún no
se recepcionan.

Uso:
    python manage.py dte_compra_normalizar_es_por_concepto            # dry-run
    python manage.py dte_compra_normalizar_es_por_concepto --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from app.models import Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados


class Command(BaseCommand):
    help = 'es_por_concepto=False en compras con recepción/líneas/stock (dry-run por defecto).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Aplica los cambios.')
        parser.add_argument('--muestra', type=int, default=15, help='Filas de ejemplo.')

    def handle(self, *args, **opts):
        base = Dte.objects.filter(tipo_transaccion='COMPRA', es_por_concepto=True)
        con_inventario = base.annotate(
            _rec=Exists(Productos_Recepcionados.objects.filter(dte=OuterRef('pk'))),
            _lin=Exists(Dte_Productos.objects.filter(dte=OuterRef('pk'))),
            _mov=Exists(Movimientos_Producto.objects.filter(dte=OuterRef('pk'), cantidad__gt=0)),
        ).filter(Q(_rec=True) | Q(_lin=True) | Q(_mov=True))
        ids_corregir = list(con_inventario.values_list('id', flat=True))
        sin_evidencia = base.exclude(id__in=ids_corregir)

        modo = 'APLICAR' if opts['apply'] else 'DRY-RUN'
        self.stdout.write(f'[{modo}] COMPRA con es_por_concepto=True: {base.count()}')
        self.stdout.write(f'  con recepción/líneas/stock (-> False): {len(ids_corregir)}')
        for d in con_inventario.order_by('-fecha_emision')[:opts['muestra']]:
            self.stdout.write(f'    DTE #{d.id} {d.tipo_documento} N°{d.numero_documento} {d.fecha_emision} ${d.monto_con_iva}')
        self.stdout.write(f'  sin evidencia de inventario (revisión manual, no se tocan): {sin_evidencia.count()}')
        for d in sin_evidencia.order_by('-fecha_emision')[:opts['muestra']]:
            self.stdout.write(f'    DTE #{d.id} {d.tipo_documento} N°{d.numero_documento} {d.fecha_emision} ${d.monto_con_iva}')

        if not opts['apply']:
            self.stdout.write(self.style.WARNING('Dry-run: no se escribió nada. Usa --apply para corregir.'))
            return
        with transaction.atomic():
            n = Dte.objects.filter(id__in=ids_corregir, es_por_concepto=True).update(es_por_concepto=False)
        self.stdout.write(self.style.SUCCESS(f'Actualizados: {n}'))
