"""Inspecciona los 2 productos que dieron SIN DATA para entender por qué."""

import os
import sys
import django
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env')

import mysql.connector
from app.models import Producto_Talla

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}

conn = mysql.connector.connect(**CONFIG)
cursor = conn.cursor()

for pid in [9942, 25817]:
    skus = list(Producto_Talla.objects.filter(producto_id=pid).values_list('sku', flat=True))
    print(f"\n=== Producto #{pid} ===")
    print(f"  SKUs en Postgres: {skus[:5]}{'...' if len(skus) > 5 else ''} (total: {len(skus)})")
    if not skus:
        continue

    placeholders = ','.join(['%s'] * len(skus))
    # Sin filtro de sucursal - ver TODOS los movimientos de estos SKUs
    cursor.execute(f"""
        SELECT alias, MIN(fecha) AS min_fecha, COUNT(*) AS movs
        FROM movimiento_productos
        WHERE codigo_asociado IN ({placeholders})
        GROUP BY alias
        ORDER BY min_fecha
    """, skus)
    rows = cursor.fetchall()
    print(f"  Movimientos en MySQL (TODAS las sucursales):")
    for alias, fecha, movs in rows:
        print(f"    [{alias}] desde {fecha} ({movs} movs)")

    # Verificar también en talla
    cursor.execute(f"""
        SELECT alias, MIN(fecha) AS min_fecha
        FROM talla
        WHERE codigo_asociado IN ({placeholders})
        GROUP BY alias
    """, skus)
    rows = cursor.fetchall()
    print(f"  Filas en talla:")
    for alias, fecha in rows:
        print(f"    [{alias}] talla.fecha = {fecha}")

cursor.close()
conn.close()
