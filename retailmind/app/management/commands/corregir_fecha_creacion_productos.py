"""
Corrige Producto.fecha_creacion hacia la fecha de alta REAL del modelo. Solo
baja fechas, nunca las sube. Dos fuentes, se toma la más antigua:

  1. MIN(fecha) de Movimientos_Producto de las tallas de la ficha, excluyendo
     el saldo de apertura sintético de la migración (referencia_externa =
     MIGRACION_LARAVEL, fechado 2026-01-22: no es un movimiento real y dejaba
     fichas viejas "nacidas" en 2026).
  2. Herencia por MODELO: MIN(fecha_creacion) entre las fichas de la misma
     identidad (artículo normalizado + marca + color + género) en cualquier
     sucursal o empresa del holding. Repara las fichas re-creadas por traspaso a
     una sucursal nueva, recepción o consolidación (consolidar_cueca), cuyo
     primer movimiento es de hoy aunque el modelo sea de 2022.

Contexto:
    migrate_from_laravel importó productos desde MySQL sin preservar la fecha
    original (talla.fecha nunca se leyó). Como Producto.fecha_creacion tiene
    auto_now_add=True, quedó la fecha de la migración de esquema (2026-04-15).
    Los movimientos sí conservan fecha real, así que el alta se reconstruye
    desde el primer movimiento. Hacia adelante la señal
    `heredar_fecha_creacion_del_modelo` (app/signals.py) evita que una ficha
    nueva rejuvenezca al modelo; este comando repara el histórico y sirve de
    red de seguridad si se agenda (es idempotente y solo baja fechas).

Uso:
    python manage.py corregir_fecha_creacion_productos                  # dry-run
    python manage.py corregir_fecha_creacion_productos --limit 50       # prueba 50
    python manage.py corregir_fecha_creacion_productos --producto-id 123
    python manage.py corregir_fecha_creacion_productos --apply          # aplica
    python manage.py corregir_fecha_creacion_productos --apply --backup-dir /tmp/bk

Con --apply se escribe ANTES un CSV de respaldo (id, articulo, fecha previa,
fecha nueva, fuente) en --backup-dir (default: backups_fecha_creacion/ junto al
manage.py) y cada cambio queda en el log de la app. Es idempotente: una segunda
corrida no encuentra nada que bajar.
"""

import csv
import logging
import os
from datetime import datetime, time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Min
from django.utils import timezone

from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.models import Movimientos_Producto, Producto
from app.utils_producto_match import normalizar_articulo

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = (
        'Corrige Producto.fecha_creacion con MIN(movimientos reales) y herencia '
        'por modelo. Solo baja fechas. Por defecto dry-run; --apply para escribir.'
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
            help='Procesar solo los primeros N productos (para pruebas).',
        )
        parser.add_argument(
            '--producto-id',
            type=int,
            default=None,
            help='Procesar solo el producto con este ID.',
        )
        parser.add_argument(
            '--margen-dias',
            type=int,
            default=1,
            help=(
                'Solo actualizar si la fecha nueva es anterior a la actual '
                'fecha_creacion en al menos N días. Default: 1.'
            ),
        )
        parser.add_argument(
            '--mostrar-muestras',
            type=int,
            default=15,
            help='Cantidad de muestras a mostrar en el reporte. Default: 15.',
        )
        parser.add_argument(
            '--backup-dir',
            default=None,
            help=(
                'Carpeta del CSV de respaldo que se escribe antes de --apply. '
                'Default: backups_fecha_creacion/ junto al manage.py.'
            ),
        )
        parser.add_argument(
            '--sin-herencia',
            action='store_true',
            help='No aplicar la herencia por modelo (solo MIN de movimientos).',
        )

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        limit = opts['limit']
        producto_id = opts['producto_id']
        margen_dias = opts['margen_dias']
        n_muestras = opts['mostrar_muestras']
        con_herencia = not opts['sin_herencia']

        modo = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(
            f'\n=== Corrección de Producto.fecha_creacion (modo {modo}) ==='
        ))
        self.stdout.write(f'  Margen mínimo de diferencia : {margen_dias} día(s)')
        self.stdout.write(f'  Herencia por modelo         : {"sí" if con_herencia else "no"}')
        if producto_id:
            self.stdout.write(f'  Filtrado a producto ID      : {producto_id}')
        if limit:
            self.stdout.write(f'  Límite de productos         : {limit}')
        self.stdout.write('')

        # ── 1. MIN(fecha) de movimientos REALES por producto ──
        self.stdout.write('[1/4] Calculando MIN(fecha) de movimientos reales por producto...')
        qs_min = (
            Movimientos_Producto.objects
            # El saldo sintético de la migración tiene la fecha de la carga, no
            # la del alta: sin excluirlo, una ficha vieja "nace" el 2026-01-22.
            .exclude(referencia_externa=REF_SALDO_INICIAL_SINTETICO)
            .values('ProductoTalla__producto_id')
            .annotate(min_fecha=Min('fecha'))
        )
        min_por_producto = {
            r['ProductoTalla__producto_id']: r['min_fecha']
            for r in qs_min
            if r['ProductoTalla__producto_id'] and r['min_fecha']
        }
        self.stdout.write(self.style.SUCCESS(
            f'      → {len(min_por_producto):,} productos con movimientos reales'
        ))

        # ── 2. Herencia por MODELO ──
        # La fecha candidata de cada ficha es min(su fecha_creacion, su MIN de
        # movimientos); la del modelo es la menor entre todas sus fichas del
        # holding. Se calcula sobre TODO el catálogo aunque se filtre por
        # --producto-id/--limit: la herencia necesita a las hermanas.
        self.stdout.write('[2/4] Calculando la fecha más antigua por modelo...')
        clave_por_producto = {}
        min_por_modelo = {}
        if con_herencia:
            for p in (
                Producto.objects
                .values('id', 'articulo', 'atributo1_id', 'atributo2_id',
                        'atributo3_id', 'fecha_creacion')
                .iterator(chunk_size=5000)
            ):
                clave = (normalizar_articulo(p['articulo']), p['atributo1_id'],
                         p['atributo2_id'], p['atributo3_id'])
                clave_por_producto[p['id']] = clave
                candidatas = []
                if p['fecha_creacion']:
                    candidatas.append(timezone.localtime(p['fecha_creacion']).date())
                if p['id'] in min_por_producto:
                    candidatas.append(min_por_producto[p['id']])
                if not candidatas:
                    continue
                cand = min(candidatas)
                prev = min_por_modelo.get(clave)
                if prev is None or cand < prev:
                    min_por_modelo[clave] = cand
            self.stdout.write(self.style.SUCCESS(
                f'      → {len(min_por_modelo):,} modelos con fecha'
            ))
        else:
            self.stdout.write('      → omitida (--sin-herencia)')

        if not min_por_producto and not min_por_modelo:
            self.stdout.write(self.style.WARNING('No hay nada que procesar.'))
            return

        # ── 3. Comparar con la fecha_creacion actual ──
        self.stdout.write('[3/4] Comparando con fecha_creacion actual...')
        productos_qs = Producto.objects.only('id', 'articulo', 'fecha_creacion')
        if producto_id:
            productos_qs = productos_qs.filter(id=producto_id)
        elif not con_herencia:
            productos_qs = productos_qs.filter(id__in=min_por_producto.keys())
        productos_qs = productos_qs.order_by('id')
        if limit:
            productos_qs = productos_qs[:limit]

        total = 0
        a_actualizar_lista = []   # (producto, articulo, fecha_antigua, fecha_nueva, fuente)
        sin_cambio = 0
        sin_fecha_creacion = 0
        por_fuente = {'movimientos': 0, 'modelo': 0}

        for p in productos_qs.iterator(chunk_size=1000):
            total += 1
            candidatas = []
            min_mov = min_por_producto.get(p.id)
            if min_mov:
                candidatas.append((min_mov, 'movimientos'))
            if con_herencia:
                min_mod = min_por_modelo.get(clave_por_producto.get(p.id))
                if min_mod:
                    candidatas.append((min_mod, 'modelo'))
            if not candidatas:
                continue
            nueva_fecha, fuente = min(candidatas, key=lambda x: x[0])
            nueva_dt = timezone.make_aware(datetime.combine(nueva_fecha, time.min))

            if not p.fecha_creacion:
                sin_fecha_creacion += 1
                por_fuente[fuente] += 1
                a_actualizar_lista.append((p, p.articulo, None, nueva_dt, fuente))
                continue

            diff_dias = (timezone.localtime(p.fecha_creacion).date() - nueva_fecha).days
            if diff_dias >= margen_dias:
                por_fuente[fuente] += 1
                a_actualizar_lista.append((p, p.articulo, p.fecha_creacion, nueva_dt, fuente))
            else:
                sin_cambio += 1

        self.stdout.write(self.style.SUCCESS(
            f'      → {total:,} productos analizados, '
            f'{len(a_actualizar_lista):,} candidatos a actualizar '
            f'(movimientos: {por_fuente["movimientos"]:,} · modelo: {por_fuente["modelo"]:,})'
        ))

        if n_muestras:
            self.stdout.write(f'\n--- Muestra (primeros {n_muestras}) ---')
            for p, art, antigua, nueva, fuente in a_actualizar_lista[:n_muestras]:
                antigua_str = antigua.strftime('%Y-%m-%d') if antigua else 'NULL'
                self.stdout.write(
                    f'  #{p.id:>7} ({art}): {antigua_str} → {nueva.strftime("%Y-%m-%d")} [{fuente}]'
                )

        # ── 4. Aplicar ──
        self.stdout.write('\n[4/4] ' + ('Aplicando cambios...' if apply_changes else 'Dry-run, no se escribirá nada.'))

        actualizados = 0
        backup_path = None
        if apply_changes and a_actualizar_lista:
            backup_path = self._escribir_respaldo(opts['backup_dir'], a_actualizar_lista)
            self.stdout.write(f'      respaldo: {backup_path}')

            # bulk_update salta auto_now_add (pre_save con add=False devuelve el
            # valor seteado) → 1 UPDATE por batch en vez de 1 por producto.
            batch = []
            for p, art, antigua, nueva, fuente in a_actualizar_lista:
                logger.info(
                    'corregir_fecha_creacion: #%s %s %s -> %s [%s]',
                    p.id, art, antigua.strftime('%Y-%m-%d') if antigua else 'NULL',
                    nueva.strftime('%Y-%m-%d'), fuente,
                )
                p.fecha_creacion = nueva
                batch.append(p)
                if len(batch) >= 1000:
                    Producto.objects.bulk_update(batch, ['fecha_creacion'])
                    actualizados += len(batch)
                    batch = []
            if batch:
                Producto.objects.bulk_update(batch, ['fecha_creacion'])
                actualizados += len(batch)
            logger.info(
                'corregir_fecha_creacion_productos: actualizados %d productos '
                '(movimientos=%d, modelo=%d, margen=%d días, producto_id=%s, limit=%s)',
                actualizados, por_fuente['movimientos'], por_fuente['modelo'],
                margen_dias, producto_id, limit
            )

        self.stdout.write(self.style.WARNING('\n=== Resumen ==='))
        self.stdout.write(f'  Productos analizados        : {total:,}')
        self.stdout.write(f'  Sin fecha_creacion (NULL)   : {sin_fecha_creacion:,}')
        self.stdout.write(f'  Con diferencia ≥ {margen_dias}d        : {len(a_actualizar_lista) - sin_fecha_creacion:,}')
        self.stdout.write(f'  Sin cambio (margen < {margen_dias}d)   : {sin_cambio:,}')
        self.stdout.write(f'  Corregidos por movimientos  : {por_fuente["movimientos"]:,}')
        self.stdout.write(f'  Corregidos por modelo       : {por_fuente["modelo"]:,}')
        self.stdout.write(f'  Total candidatos            : {len(a_actualizar_lista):,}')

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(
                f'\n  ✓ {actualizados:,} productos actualizados.\n'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '\n  ⚠ DRY-RUN — no se aplicaron cambios. Para aplicar agrega --apply\n'
            ))

    def _escribir_respaldo(self, backup_dir, cambios):
        """CSV con el estado previo de cada ficha que se va a tocar."""
        carpeta = backup_dir or os.path.join(str(settings.BASE_DIR), 'backups_fecha_creacion')
        os.makedirs(carpeta, exist_ok=True)
        stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(carpeta, f'fecha_creacion_{stamp}.csv')
        with open(path, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['producto_id', 'articulo', 'fecha_creacion_previa', 'fecha_creacion_nueva', 'fuente'])
            for p, art, antigua, nueva, fuente in cambios:
                w.writerow([p.id, art, antigua.isoformat() if antigua else '',
                            nueva.isoformat(), fuente])
        return path
