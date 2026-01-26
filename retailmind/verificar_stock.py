"""Script para comparar stock entre MySQL y PostgreSQL"""
import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')

import django
django.setup()

import mysql.connector
from dotenv import load_dotenv
from pathlib import Path
from app.models import Producto_Talla

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(env_path)

# Conectar a MySQL
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

articulo = sys.argv[1] if len(sys.argv) > 1 else '5BM00539-125'

print(f"\n{'='*70}")
print(f"COMPARANDO ARTÍCULO: {articulo}")
print(f"{'='*70}")

# === MYSQL ===
cursor.execute("""
    SELECT codigo_asociado, stock, alias, size 
    FROM talla 
    WHERE articulo = %s
    ORDER BY alias, size
""", (articulo,))

mysql_data = {}
print("\n[MYSQL - PRODUCCION]:")
print("-"*50)
total_mysql = {}
for row in cursor:
    sku = row['codigo_asociado']
    stock = row['stock'] or 0
    alias = row['alias'] or 'SIN'
    talla = row['size'] or 'U'
    key = f"{sku}:{alias}"
    mysql_data[key] = {'stock': stock, 'talla': talla, 'alias': alias}
    print(f"  SKU: {sku:>10}, Stock: {stock:>3}, Alias: {alias}, Talla: {talla}")
    
    if alias not in total_mysql:
        total_mysql[alias] = 0
    total_mysql[alias] += stock

print("-"*50)
for alias, total in total_mysql.items():
    print(f"  TOTAL {alias}: {total}")

# === POSTGRESQL ===
print("\n[POSTGRESQL - DJANGO]:")
print("-"*50)

pts = Producto_Talla.objects.filter(
    producto__articulo=articulo
).select_related('producto__sucursal')

pg_data = {}
total_pg = {}
for pt in pts:
    alias = pt.producto.sucursal.alias if pt.producto.sucursal else 'SIN'
    key = f"{pt.sku}:{alias}"
    pg_data[key] = {'stock': pt.stock, 'talla': pt.talla, 'alias': alias, 'id': pt.id}
    print(f"  SKU: {pt.sku:>10}, Stock: {pt.stock:>3}, Alias: {alias}, Talla: {pt.talla}, ID: {pt.id}")
    
    if alias not in total_pg:
        total_pg[alias] = 0
    total_pg[alias] += pt.stock

print("-"*50)
for alias, total in total_pg.items():
    print(f"  TOTAL {alias}: {total}")

# === DIFERENCIAS ===
print(f"\n{'='*70}")
print("DIFERENCIAS:")
print("-"*50)

diferencias = 0
for key in set(mysql_data.keys()) | set(pg_data.keys()):
    mysql_stock = mysql_data.get(key, {}).get('stock', 'NO EXISTE')
    pg_stock = pg_data.get(key, {}).get('stock', 'NO EXISTE')
    
    if mysql_stock != pg_stock:
        diferencias += 1
        sku = key.split(':')[0]
        alias = key.split(':')[1]
        print(f"  SKU {sku} ({alias}): MySQL={mysql_stock}, PG={pg_stock}")

if diferencias == 0:
    print("  ✓ No hay diferencias")
else:
    print(f"\n  ⚠️  Total diferencias: {diferencias}")

cursor.close()
conn.close()
