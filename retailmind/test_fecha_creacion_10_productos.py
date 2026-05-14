"""
Script ad-hoc para validar la corrección de fecha_creacion sobre 10 productos
variados (distintos años y sucursales). Compara Postgres local vs MySQL legacy.

Uso:
    python test_fecha_creacion_10_productos.py
"""

import os
import sys
import django
from pathlib import Path
from datetime import datetime

# Setup Django
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env')

import mysql.connector
from app.models import Producto, Producto_Talla


MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


def pick_test_products():
    """Selecciona 10 productos diversos: varios años, varias sucursales."""
    from django.db.models.functions import ExtractYear

    samples = []
    seen_ids = set()
    target_years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

    for year in target_years:
        candidates = (
            Producto.objects
            .filter(fecha_creacion__year=year)
            .filter(producto_talla__isnull=False)
            .select_related('sucursal')
            .distinct()
            .order_by('?')[:2]
        )
        for p in candidates:
            if p.id not in seen_ids and len(samples) < 10:
                samples.append(p)
                seen_ids.add(p.id)
        if len(samples) >= 10:
            break

    return samples[:10]


def get_sku_min_per_sucursal(skus, alias):
    """Consulta MySQL: MIN(fecha) por (sku, alias)."""
    if not skus:
        return None

    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()

    placeholders = ','.join(['%s'] * len(skus))
    query = f"""
        SELECT codigo_asociado, MIN(fecha) AS primer_fecha, COUNT(*) AS movs,
               (SELECT concepto FROM movimiento_productos mp2
                WHERE mp2.codigo_asociado = mp.codigo_asociado AND mp2.alias = mp.alias
                ORDER BY mp2.fecha ASC LIMIT 1) AS primer_concepto
        FROM movimiento_productos mp
        WHERE codigo_asociado IN ({placeholders})
          AND alias = %s
        GROUP BY codigo_asociado, alias
        ORDER BY primer_fecha ASC
        LIMIT 1
    """
    params = list(skus) + [alias]
    cursor.execute(query, params)
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if row:
        return {
            'sku': row[0],
            'primer_fecha': row[1],
            'movs': row[2],
            'primer_concepto': row[3],
        }
    return None


def main():
    # Force UTF-8 stdout on Windows
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 80)
    print("TEST DE 10 PRODUCTOS - fecha_creacion (Postgres) vs MySQL legacy")
    print("=" * 80)

    productos = pick_test_products()
    print(f"\nSeleccionados: {len(productos)} productos\n")

    resultados = []
    for i, p in enumerate(productos, 1):
        skus = list(
            Producto_Talla.objects.filter(producto_id=p.id).values_list('sku', flat=True)
        )
        sucursal_alias = p.sucursal.alias if p.sucursal else None

        if not skus or not sucursal_alias:
            continue

        mysql_data = get_sku_min_per_sucursal(skus, sucursal_alias)

        pg_fecha = p.fecha_creacion.date() if p.fecha_creacion else None
        mysql_fecha = mysql_data['primer_fecha'] if mysql_data else None
        if mysql_fecha and hasattr(mysql_fecha, 'date'):
            mysql_fecha = mysql_fecha.date()

        if pg_fecha and mysql_fecha:
            diff_dias = abs((pg_fecha - mysql_fecha).days)
        else:
            diff_dias = None

        if diff_dias is None:
            estado = '[!] SIN DATA'
        elif diff_dias == 0:
            estado = '[OK] MATCH EXACTO'
        elif diff_dias <= 1:
            estado = '[OK] MATCH +-1d'
        else:
            estado = f'[X] MISMATCH ({diff_dias}d)'

        resultados.append({
            'producto_id': p.id,
            'articulo': p.articulo,
            'sucursal': sucursal_alias,
            'tipo_sucursal': p.sucursal.tipo_sucursal if p.sucursal else '?',
            'pg_fecha': pg_fecha,
            'mysql_fecha': mysql_fecha,
            'mysql_concepto': mysql_data['primer_concepto'] if mysql_data else None,
            'mysql_movs': mysql_data['movs'] if mysql_data else 0,
            'diff_dias': diff_dias,
            'estado': estado,
            'num_skus': len(skus),
        })

        print(f"{i:2}. #{p.id:>6} [{sucursal_alias:>5}] ({p.articulo[:30]:<30}) "
              f"PG={pg_fecha} MySQL={mysql_fecha} -> {estado}")

    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    matches = sum(1 for r in resultados if 'MATCH' in r['estado'])
    print(f"Match exacto/±1d : {matches}/{len(resultados)} ({matches*100//max(len(resultados),1)}%)")

    # Guardar JSON para el .md
    import json
    out_path = Path(__file__).resolve().parent.parent / 'test_fecha_creacion_resultados.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump([{**r,
                    'pg_fecha': str(r['pg_fecha']),
                    'mysql_fecha': str(r['mysql_fecha']) if r['mysql_fecha'] else None}
                   for r in resultados], f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResultados guardados en: {out_path}")


if __name__ == '__main__':
    main()
