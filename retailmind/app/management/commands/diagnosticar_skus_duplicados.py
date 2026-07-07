"""
diagnosticar_skus_duplicados — Diagnostica (y opcionalmente corrige) SKUs
duplicados en Producto_Talla.

El campo Producto_Talla.sku NO es único en la BD (dato legacy de la migración:
muchos productos comparten un "SKU placeholder"). Eso rompía la trazabilidad
(`.get(sku=)` → MultipleObjectsReturned) y cualquier lookup por SKU.

Modo por defecto = SOLO LECTURA (diagnóstico + Excel). Seguro contra producción.

Uso:
    # Diagnóstico read-only + Excel con los peores casos
    python manage.py diagnosticar_skus_duplicados
    python manage.py diagnosticar_skus_duplicados --top 500 --excel skus_dup.xlsx

    # CORRECCIÓN (destructivo): reasigna SKUs únicos a los duplicados.
    # Mantiene como "dueño" del SKU la talla con más movimientos y le da SKUs
    # nuevos al resto. REVISAR el Excel antes. Pide confirmación.
    python manage.py diagnosticar_skus_duplicados --aplicar
    python manage.py diagnosticar_skus_duplicados --aplicar --force   # sin prompt

Notas:
  - Los movimientos/lotes/traspasos referencian Producto_Talla por FK, así que
    reasignar el valor `sku` NO rompe esas relaciones. SÍ cambia el código que
    se imprime en etiquetas/códigos de barra y el texto de SKU en líneas de DTE
    antiguas; por eso es opt-in y con confirmación.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from app.models import Movimientos_Producto, Producto_Talla


def _es_placeholder(sku, ocurrencias):
    """Heurística: SKU compartido por muchos productos o número 'redondo'."""
    s = str(sku)
    return ocurrencias >= 10 or s.endswith('00000')


class Command(BaseCommand):
    help = 'Diagnostica (y opcionalmente corrige) SKUs duplicados en Producto_Talla.'

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=200,
                            help='Nº de SKUs peores a detallar en el Excel (default 200).')
        parser.add_argument('--excel', default='skus_duplicados.xlsx',
                            help='Ruta del Excel de salida (default skus_duplicados.xlsx).')
        parser.add_argument('--sin-excel', action='store_true',
                            help='No generar Excel, solo el resumen en consola.')
        parser.add_argument('--aplicar', action='store_true',
                            help='DESTRUCTIVO: reasigna SKUs únicos a los duplicados.')
        parser.add_argument('--force', action='store_true',
                            help='Con --aplicar, no pedir confirmación interactiva.')
        parser.add_argument('--solo-placeholder', action='store_true',
                            help='Actuar SOLO sobre los SKUs "placeholder" (>=10 ocurrencias '
                                 'o redondos): el ~80%% del daño con el menor riesgo.')

    # -------------------------------------------------------------- handle
    def handle(self, *args, **opts):
        dups_qs = (Producto_Talla.objects.values('sku')
                   .annotate(n=Count('id')).filter(n__gt=1).order_by('-n'))

        total_skus = 0
        total_filas = 0
        placeholder_skus = 0
        top = []
        top_n = opts['top']
        for d in dups_qs.iterator():
            total_skus += 1
            total_filas += d['n']
            if _es_placeholder(d['sku'], d['n']):
                placeholder_skus += 1
            if len(top) < top_n:
                top.append(d)

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== DIAGNÓSTICO DE SKUs DUPLICADOS ==='))
        self.stdout.write(f'SKUs duplicados (>1 Producto_Talla): {total_skus:,}')
        self.stdout.write(f'Filas Producto_Talla involucradas:   {total_filas:,}')
        self.stdout.write(f'  · a reasignar (todas menos 1/SKU): {total_filas - total_skus:,}')
        self.stdout.write(f'SKUs "placeholder" (>=10 ocurr. o redondos): {placeholder_skus:,}')
        if top:
            self.stdout.write('\nTop peores:')
            for d in top[:15]:
                self.stdout.write(f'   sku={d["sku"]:<16} ocurrencias={d["n"]}')

        if total_skus == 0:
            self.stdout.write(self.style.SUCCESS('\nNo hay SKUs duplicados. Nada que hacer.'))
            return

        if not opts['sin_excel'] and not opts['aplicar']:
            self._exportar_excel(top, opts['excel'])

        if opts['aplicar']:
            self._aplicar(dups_qs, total_skus, total_filas, opts['force'],
                          solo_placeholder=opts['solo_placeholder'])

    # -------------------------------------------------------------- excel
    def _exportar_excel(self, top, ruta):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.stdout.write(self.style.ERROR('openpyxl no disponible; omito Excel.'))
            return

        skus = [d['sku'] for d in top]
        pts = (Producto_Talla.objects
               .filter(sku__in=skus)
               .select_related('producto', 'producto__sucursal', 'producto__atributo1',
                               'producto__atributo2')
               .annotate(n_mov=Count('movimientos_productos_talla')))
        ocurr = {d['sku']: d['n'] for d in top}

        wb = Workbook()
        ws = wb.active
        ws.title = 'SKUs duplicados'
        ws.append(['SKU', 'Ocurrencias', 'Placeholder', 'ProductoTalla_id', 'Producto_id',
                   'Articulo', 'Marca', 'Color', 'Sucursal', 'Talla', 'Stock', 'N_movimientos'])
        filas = sorted(pts, key=lambda p: (-ocurr.get(p.sku, 0), p.sku, p.id))
        for p in filas:
            prod = p.producto
            ws.append([
                p.sku, ocurr.get(p.sku, 0),
                'SI' if _es_placeholder(p.sku, ocurr.get(p.sku, 0)) else '',
                p.id, prod.id, prod.articulo,
                prod.atributo1.valor if prod.atributo1 else '',
                prod.atributo2.valor if prod.atributo2 else '',
                prod.sucursal.alias if prod.sucursal else '',
                p.talla, p.stock, p.n_mov,
            ])
        wb.save(ruta)
        self.stdout.write(self.style.SUCCESS(
            f'\nExcel generado: {ruta}  ({ws.max_row - 1} filas de los {len(top)} peores SKUs)'))

    # -------------------------------------------------------------- aplicar
    def _aplicar(self, dups_qs, total_skus, total_filas, force, solo_placeholder=False):
        alcance = ('SOLO los SKUs placeholder (>=10 ocurr. o redondos)'
                   if solo_placeholder else f'{total_filas - total_skus:,} tallas')
        self.stdout.write(self.style.WARNING(
            f'\n⚠️  --aplicar reasignará SKUs únicos — alcance: {alcance} '
            f'(mantiene 1 por SKU). Esto CAMBIA el código de barra/etiqueta de esas tallas.'))
        if not force:
            resp = input('Escribe "SI" para continuar: ').strip()
            if resp != 'SI':
                self.stdout.write('Cancelado.')
                return

        # Generador de SKU único (correlativo canónico del sistema)
        from app.views import obtener_siguiente_sku

        procesados = 0
        reasignados = 0
        for d in dups_qs.iterator():
            sku = d['sku']
            if solo_placeholder and not _es_placeholder(sku, d['n']):
                continue
            pts = list(Producto_Talla.objects
                       .filter(sku=sku)
                       .annotate(n_mov=Count('movimientos_productos_talla')))
            # "Dueño" = el de más movimientos; desempate por stock y menor id.
            pts.sort(key=lambda p: (-p.n_mov, -(p.stock or 0), p.id))
            resto = pts[1:]
            with transaction.atomic():
                for p in resto:
                    nuevo = obtener_siguiente_sku()
                    Producto_Talla.objects.filter(pk=p.pk).update(sku=nuevo)
                    reasignados += 1
            procesados += 1
            if procesados % 500 == 0:
                self.stdout.write(f'   ... {procesados:,}/{total_skus:,} SKUs procesados '
                                  f'({reasignados:,} tallas reasignadas)')

        self.stdout.write(self.style.SUCCESS(
            f'\nListo. {procesados:,} SKUs desduplicados, {reasignados:,} tallas con SKU nuevo.'))
