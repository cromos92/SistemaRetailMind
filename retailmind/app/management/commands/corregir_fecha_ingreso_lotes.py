"""
Corrige LoteProducto.fecha_ingreso con la fecha histórica real del legacy.

Contexto:
    generar_lotes_faltantes.py creó los LoteProducto SIN pasar fecha_ingreso.
    Como LoteProducto.fecha_ingreso tiene auto_now_add=True, Django asignó
    timezone.now() (la fecha de generación de los lotes, ~abril 2026), NO la
    fecha real de ingreso del stock.

    Esto rompe la antigüedad de stock que /api/precios-actuales/ expone como
    `ultima_fecha_ingreso` y `dias_antiguedad_stock` (consumidos por AllConnected
    para calcular descuentos por antigüedad): TODO el stock migrado parece
    "recién llegado".

    La fecha real se recupera, por orden de preferencia:
      1. lote.dte.fecha_emision           (si el lote quedó ligado a un DTE)
      2. MIN(Movimientos_Producto.fecha)  de movimientos de INGRESO de ese
         Producto_Talla  (migrate_from_laravel.py SÍ preservó esa fecha)
      3. producto.fecha_creacion          (fallback; ya corregida por el comando
         corregir_fecha_creacion_productos.py)

    update() salta auto_now_add, por eso se usa para escribir.

Uso:
    python manage.py corregir_fecha_ingreso_lotes                 # dry-run
    python manage.py corregir_fecha_ingreso_lotes --limit 50      # prueba 50
    python manage.py corregir_fecha_ingreso_lotes --sucursal 1    # una sucursal
    python manage.py corregir_fecha_ingreso_lotes --apply         # aplica
"""

import logging
from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db.models import Min
from django.utils import timezone

from app.models import LoteProducto, Movimientos_Producto

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = (
        'Corrige LoteProducto.fecha_ingreso usando dte.fecha_emision / '
        'MIN(Movimientos_Producto.fecha de INGRESO) / producto.fecha_creacion. '
        'Por defecto dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplicar cambios. Sin este flag corre en dry-run.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Procesar solo los primeros N lotes (para pruebas).')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Procesar solo lotes de esta sucursal.')
        parser.add_argument('--margen-dias', type=int, default=1,
                            help='Solo actualizar si la fecha real es anterior a la '
                                 'fecha_ingreso actual en al menos N días. Default: 1.')
        parser.add_argument('--mostrar-muestras', type=int, default=15,
                            help='Cantidad de muestras en el reporte. Default: 15.')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        limit = opts['limit']
        sucursal_id = opts['sucursal']
        margen_dias = opts['margen_dias']
        n_muestras = opts['mostrar_muestras']

        modo = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(
            f'\n=== Corrección de LoteProducto.fecha_ingreso (modo {modo}) ==='
        ))
        self.stdout.write(f'  Margen mínimo de diferencia : {margen_dias} día(s)')
        if sucursal_id:
            self.stdout.write(f'  Filtrado a sucursal ID      : {sucursal_id}')
        if limit:
            self.stdout.write(f'  Límite de lotes             : {limit}')
        self.stdout.write('')

        # Base de lotes a procesar (se reutiliza para acotar el MIN y para iterar).
        lotes_base = LoteProducto.objects.all()
        if sucursal_id:
            lotes_base = lotes_base.filter(producto_talla__producto__sucursal_id=sucursal_id)

        # ── 1. MIN(fecha) de movimientos de INGRESO por Producto_Talla ──
        # Acotado vía SUBQUERY server-side a SOLO los Producto_Talla que tienen
        # lotes a procesar. Sin esto se traían cientos de miles de filas (todos
        # los PT con movimientos) cuando solo hacen falta las de los lotes →
        # clave contra una DB remota (menos filas por la red).
        self.stdout.write('[1/3] Calculando MIN(fecha) de movimientos de INGRESO (acotado a PT con lotes)...')
        qs_min = (
            Movimientos_Producto.objects
            .filter(
                tipo_movimiento='INGRESO',
                ProductoTalla_id__in=lotes_base.values('producto_talla_id'),
            )
            .values('ProductoTalla_id')
            .annotate(min_fecha=Min('fecha'))
        )
        min_por_pt = {
            r['ProductoTalla_id']: r['min_fecha']
            for r in qs_min
            if r['ProductoTalla_id'] and r['min_fecha']
        }
        self.stdout.write(self.style.SUCCESS(
            f'      → {len(min_por_pt):,} Producto_Talla con lotes y movimientos de INGRESO'
        ))

        # ── 2. Comparar con fecha_ingreso actual ──
        self.stdout.write('[2/3] Comparando con fecha_ingreso actual...')
        lotes_qs = (
            lotes_base
            .select_related('dte', 'producto_talla__producto')
            .only(
                'id', 'fecha_ingreso', 'dte__fecha_emision',
                'producto_talla_id', 'producto_talla__producto__fecha_creacion',
                'producto_talla__producto__articulo',
            )
        )
        if limit:
            lotes_qs = lotes_qs[:limit]

        total = 0
        a_actualizar = []   # (lote_obj, articulo, fecha_actual, fecha_nueva_dt, origen)
        sin_origen = 0
        sin_cambio = 0

        for lote in lotes_qs.iterator(chunk_size=1000):
            total += 1

            # Determinar la fecha histórica real (DateField/DateTime → date)
            fecha_real = None
            origen = None
            if lote.dte_id and lote.dte and lote.dte.fecha_emision:
                fecha_real, origen = lote.dte.fecha_emision, 'dte'
            elif min_por_pt.get(lote.producto_talla_id):
                fecha_real, origen = min_por_pt[lote.producto_talla_id], 'movimiento'
            elif lote.producto_talla.producto.fecha_creacion:
                fecha_real = lote.producto_talla.producto.fecha_creacion.date()
                origen = 'producto'

            if not fecha_real:
                sin_origen += 1
                continue

            nueva_dt = timezone.make_aware(datetime.combine(fecha_real, time.min))
            actual = lote.fecha_ingreso
            diff_dias = (actual.date() - fecha_real).days if actual else margen_dias
            if diff_dias >= margen_dias:
                a_actualizar.append((
                    lote, lote.producto_talla.producto.articulo,
                    actual, nueva_dt, origen,
                ))
            else:
                sin_cambio += 1

        self.stdout.write(self.style.SUCCESS(
            f'      → {total:,} lotes analizados, {len(a_actualizar):,} candidatos a actualizar'
        ))

        self.stdout.write(f'\n--- Muestra (primeros {n_muestras}) ---')
        for lote, art, antigua, nueva, origen in a_actualizar[:n_muestras]:
            antigua_str = antigua.strftime('%Y-%m-%d') if antigua else 'NULL'
            self.stdout.write(
                f'  lote #{lote.id:>7} ({art}): {antigua_str} → '
                f'{nueva.strftime("%Y-%m-%d")}  [{origen}]'
            )

        # ── 3. Aplicar ──
        self.stdout.write('\n[3/3] ' + (
            'Aplicando cambios...' if apply_changes else 'Dry-run, no se escribirá nada.'
        ))
        actualizados = 0
        if apply_changes and a_actualizar:
            # bulk_update salta auto_now_add (pre_save con add=False devuelve el
            # valor seteado) → 1 UPDATE por batch en vez de 1 por lote.
            batch = []
            for lote, _art, _antigua, nueva, _origen in a_actualizar:
                lote.fecha_ingreso = nueva
                batch.append(lote)
                if len(batch) >= 1000:
                    LoteProducto.objects.bulk_update(batch, ['fecha_ingreso'])
                    actualizados += len(batch)
                    batch = []
            if batch:
                LoteProducto.objects.bulk_update(batch, ['fecha_ingreso'])
                actualizados += len(batch)
            logger.info(
                'corregir_fecha_ingreso_lotes: actualizados %d lotes '
                '(margen=%d días, sucursal=%s, limit=%s)',
                actualizados, margen_dias, sucursal_id, limit,
            )

        self.stdout.write(self.style.WARNING('\n=== Resumen ==='))
        self.stdout.write(f'  Lotes analizados            : {total:,}')
        self.stdout.write(f'  Sin origen de fecha          : {sin_origen:,}')
        self.stdout.write(f'  Sin cambio (margen < {margen_dias}d)   : {sin_cambio:,}')
        self.stdout.write(f'  Total candidatos            : {len(a_actualizar):,}')

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f'\n  ✓ {actualizados:,} lotes actualizados.\n'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '\n  ⚠ DRY-RUN — no se aplicaron cambios. Para aplicar agrega --apply\n'
            ))
