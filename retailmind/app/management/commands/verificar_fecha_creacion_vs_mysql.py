"""
Verifica que Producto.fecha_creacion (en Postgres) coincide con
MIN(movimiento_productos.fecha) en MySQL legacy (holdingtebes / Vicent).

Toma una muestra aleatoria de productos en Postgres, recupera TODOS los SKUs
de cada producto, consulta MySQL para obtener el primer movimiento de cada SKU,
agrega al nivel de producto (MIN de MINs) y compara contra Postgres.

Uso:
    python manage.py verificar_fecha_creacion_vs_mysql              # 100 productos
    python manage.py verificar_fecha_creacion_vs_mysql --sample 500
    python manage.py verificar_fecha_creacion_vs_mysql --por-anio 30
    python manage.py verificar_fecha_creacion_vs_mysql --mostrar-mismatches 50
    python manage.py verificar_fecha_creacion_vs_mysql --fecha-max 2026-03-01
"""

import os
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector  # noqa: E402
from django.core.management.base import BaseCommand  # noqa: E402
from django.db.models import F  # noqa: E402

from app.models import Producto, Producto_Talla  # noqa: E402


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = (
        'Verifica Producto.fecha_creacion contra MIN(movimiento_productos.fecha) '
        'en MySQL legacy. Solo lectura.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sample',
            type=int,
            default=100,
            help='Cantidad de productos a muestrear aleatoriamente. Default: 100.',
        )
        parser.add_argument(
            '--por-anio',
            type=int,
            default=None,
            help='En lugar de muestra global, tomar N productos por cada año. '
                 'Ej: --por-anio 20 toma 20 productos de 2018, 20 de 2019, etc.',
        )
        parser.add_argument(
            '--mostrar-mismatches',
            type=int,
            default=20,
            help='Cantidad de mismatches a mostrar en detalle. Default: 20.',
        )
        parser.add_argument(
            '--tolerancia-dias',
            type=int,
            default=1,
            help='Diferencia máxima en días para considerar match. Default: 1.',
        )
        parser.add_argument(
            '--fecha-max',
            type=str,
            default=None,
            help='Acota la comparación a productos con fecha_creacion <= esta fecha '
                 'Y solo considera movimientos en MySQL con fecha <= esta fecha. '
                 'Útil cuando local no tiene toda la data reciente. Formato: YYYY-MM-DD',
        )

    def handle(self, *args, **opts):
        sample_size = opts['sample']
        por_anio = opts['por_anio']
        n_mismatches = opts['mostrar_mismatches']
        tolerancia = opts['tolerancia_dias']
        fecha_max_str = opts['fecha_max']

        self.fecha_max = None
        if fecha_max_str:
            try:
                self.fecha_max = datetime.strptime(fecha_max_str, '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR(
                    f'--fecha-max inválido: {fecha_max_str} (debe ser YYYY-MM-DD)'
                ))
                return

        self.stdout.write(self.style.WARNING(
            '\n=== Verificación fecha_creacion (Postgres) vs MySQL legacy ===\n'
        ))
        if self.fecha_max:
            self.stdout.write(self.style.WARNING(
                f'  Filtro temporal aplicado: fecha <= {self.fecha_max}\n'
            ))

        # 1. Conectar a MySQL
        self.stdout.write('[1/4] Conectando a MySQL...')
        try:
            mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
            )
            self.stdout.write(self.style.SUCCESS('      ✓ Conectado'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'      ✗ Error MySQL: {e}'))
            return

        # 2. Seleccionar muestra de productos
        self.stdout.write('[2/4] Seleccionando muestra de productos...')

        if por_anio:
            productos_muestra = self._muestra_por_anio(por_anio)
        else:
            productos_muestra = self._muestra_random(sample_size)

        self.stdout.write(self.style.SUCCESS(
            f'      ✓ {len(productos_muestra):,} productos seleccionados'
        ))

        # 3. Recuperar todos los SKUs de la muestra (con sucursal del producto)
        producto_ids = [p['id'] for p in productos_muestra]
        tallas = Producto_Talla.objects.filter(producto_id__in=producto_ids).values(
            'producto_id', 'sku', 'producto__sucursal__alias'
        )

        producto_to_skus = defaultdict(list)
        for t in tallas:
            producto_to_skus[t['producto_id']].append(t['sku'])

        # Sucursal por producto (cada producto vive en una sola sucursal)
        producto_sucursal = {p['id']: p.get('sucursal_alias') for p in productos_muestra}

        todos_los_skus = list({t['sku'] for t in tallas})
        self.stdout.write(f'      ✓ {len(todos_los_skus):,} SKUs únicos en la muestra')

        if not todos_los_skus:
            self.stdout.write(self.style.WARNING('No hay SKUs para verificar.'))
            return

        # 4. Consultar MySQL: MIN(fecha) por (SKU, sucursal/alias)
        self.stdout.write('[3/4] Consultando MySQL (agrupado por SKU + sucursal)...')
        cursor = mysql_conn.cursor()

        placeholders = ','.join(['%s'] * len(todos_los_skus))
        params = list(todos_los_skus)
        where_extra = ''
        if self.fecha_max:
            where_extra = ' AND fecha <= %s'
            params.append(self.fecha_max)

        query = f"""
            SELECT codigo_asociado, alias, MIN(fecha) AS primer_fecha
            FROM movimiento_productos
            WHERE codigo_asociado IN ({placeholders}){where_extra}
            GROUP BY codigo_asociado, alias
        """
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        mysql_conn.close()

        # Mapa (sku, alias_sucursal) -> min_fecha
        sku_sucursal_min = {(sku, alias): fecha for sku, alias, fecha in rows}
        self.stdout.write(self.style.SUCCESS(
            f'      ✓ {len(sku_sucursal_min):,} combinaciones (SKU, sucursal) en MySQL'
        ))

        # 5. Agregar a nivel producto: MIN sobre los SKUs del producto FILTRADO por su sucursal
        producto_min_mysql = {}
        for producto_id, skus in producto_to_skus.items():
            sucursal_alias = producto_sucursal.get(producto_id)
            if not sucursal_alias:
                continue
            fechas = [
                sku_sucursal_min[(sku, sucursal_alias)]
                for sku in skus
                if (sku, sucursal_alias) in sku_sucursal_min
            ]
            if fechas:
                producto_min_mysql[producto_id] = min(fechas)

        # 6. Comparar y reportar
        self.stdout.write('[4/4] Comparando fechas...')

        match_exacto = 0
        match_tolerancia = 0
        mismatches = []
        sin_mysql = []

        for p in productos_muestra:
            pid = p['id']
            fecha_pg = p['fecha_creacion'].date() if p['fecha_creacion'] else None
            fecha_mysql_min = producto_min_mysql.get(pid)

            if fecha_mysql_min is None:
                sin_mysql.append(p)
                continue

            # Normalizar a date
            fecha_mysql_date = (
                fecha_mysql_min.date()
                if hasattr(fecha_mysql_min, 'date')
                else fecha_mysql_min
            )

            if fecha_pg == fecha_mysql_date:
                match_exacto += 1
            else:
                diff = abs((fecha_pg - fecha_mysql_date).days) if fecha_pg else None
                if diff is not None and diff <= tolerancia:
                    match_tolerancia += 1
                else:
                    mismatches.append({
                        'producto_id': pid,
                        'articulo': p['articulo'],
                        'sucursal': p.get('sucursal_alias') or '?',
                        'fecha_pg': fecha_pg,
                        'fecha_mysql': fecha_mysql_date,
                        'diff_dias': diff,
                    })

        total_comparables = len(productos_muestra) - len(sin_mysql)

        self.stdout.write('\n=== Resultado ===')
        self.stdout.write(f'  Productos en muestra            : {len(productos_muestra):,}')
        self.stdout.write(f'  Sin movimientos en MySQL        : {len(sin_mysql):,}')
        self.stdout.write(f'  Comparables                     : {total_comparables:,}')
        if total_comparables > 0:
            pct_exacto = match_exacto / total_comparables * 100
            pct_tol = (match_exacto + match_tolerancia) / total_comparables * 100
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Match exacto                  : {match_exacto:,} ({pct_exacto:.1f}%)'
            ))
            self.stdout.write(
                f'  ≈ Match con tolerancia ±{tolerancia}d      : {match_tolerancia:,} '
                f'(acumulado: {pct_tol:.1f}%)'
            )
            self.stdout.write(self.style.ERROR(
                f'  ✗ Mismatches > {tolerancia}d              : {len(mismatches):,}'
            ))

        # 7. Mostrar mismatches en detalle
        if mismatches:
            self.stdout.write(f'\n--- Top {n_mismatches} mismatches (peor primero) ---')
            mismatches.sort(key=lambda x: x['diff_dias'] or 0, reverse=True)
            for m in mismatches[:n_mismatches]:
                self.stdout.write(
                    f'  #{m["producto_id"]:>7} [{m["sucursal"]:>5}] ({m["articulo"]}): '
                    f'PG={m["fecha_pg"]} vs MySQL={m["fecha_mysql"]} '
                    f'(diff: {m["diff_dias"]}d)'
                )

        if sin_mysql:
            self.stdout.write(f'\n--- Productos sin movimientos en MySQL ({len(sin_mysql)}) ---')
            for p in sin_mysql[:10]:
                self.stdout.write(
                    f'  #{p["id"]:>7} [{p.get("sucursal_alias") or "?":>5}] '
                    f'({p["articulo"]}): PG={p["fecha_creacion"].date()}'
                )
            if len(sin_mysql) > 10:
                self.stdout.write(f'  ... y {len(sin_mysql) - 10} más')

        self.stdout.write('')

    def _muestra_random(self, n):
        """Muestra aleatoria global de N productos con fecha_creacion y al menos 1 talla."""
        qs = (
            Producto.objects
            .filter(fecha_creacion__isnull=False)
            .filter(producto_talla__isnull=False)
        )
        if self.fecha_max:
            qs = qs.filter(fecha_creacion__date__lte=self.fecha_max)
        candidatos = list(
            qs.distinct().values(
                'id', 'articulo', 'fecha_creacion', sucursal_alias=F('sucursal__alias')
            )
        )
        return random.sample(candidatos, min(n, len(candidatos)))

    def _muestra_por_anio(self, n_por_anio):
        """Toma N productos aleatorios por cada año."""
        from django.db.models.functions import ExtractYear

        anios_qs = (
            Producto.objects
            .filter(fecha_creacion__isnull=False)
            .annotate(anio=ExtractYear('fecha_creacion'))
        )
        if self.fecha_max:
            anios_qs = anios_qs.filter(fecha_creacion__date__lte=self.fecha_max)
        anios = (
            anios_qs.values_list('anio', flat=True).distinct().order_by('anio')
        )

        muestra = []
        for anio in anios:
            qs = (
                Producto.objects
                .filter(fecha_creacion__year=anio)
                .filter(producto_talla__isnull=False)
            )
            if self.fecha_max:
                qs = qs.filter(fecha_creacion__date__lte=self.fecha_max)
            candidatos = list(
                qs.distinct().values(
                    'id', 'articulo', 'fecha_creacion',
                    sucursal_alias=F('sucursal__alias'),
                )
            )
            sample = random.sample(candidatos, min(n_por_anio, len(candidatos)))
            muestra.extend(sample)
            self.stdout.write(f'      • {anio}: {len(sample)} productos')

        return muestra
