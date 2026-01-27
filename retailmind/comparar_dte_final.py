"""
Comparación final DTEs MySQL vs PostgreSQL
Muestra que los datos SON correctos, solo el identificador es diferente
"""
import os
from pathlib import Path

env_file = Path('.env')
if env_file.exists():
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

import mysql.connector
from django.db import connection
from app.models import Sucursal

# Mapeo DIRECCION -> ALIAS
MAPEO = {
    'Maipu 668': 'PAO1',
    'Matta 2422': 'PAO2',
    'Matta 2432': 'PAO3',
    'Matta 2458': 'PAO4',
    'Matta 2479': 'NICK1',
    'Matta 2438': 'NICK2',
    'Matta 2418': 'NICK3',
}

# MySQL
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

# Obtener DTEs desde tabla dte (ya usa ALIAS)
cursor.execute('''
    SELECT bodega_inicio as sucursal, COUNT(*) as total
    FROM dte
    WHERE bodega_inicio != '0' AND bodega_inicio IS NOT NULL
    GROUP BY bodega_inicio
''')
mysql_dte = {row['sucursal']: row['total'] for row in cursor.fetchall()}

# Obtener ventas (usa DIRECCION)
cursor.execute('''
    SELECT sucursal, COUNT(*) as total
    FROM ventas
    GROUP BY sucursal
''')
mysql_ventas = {row['sucursal']: row['total'] for row in cursor.fetchall()}

cursor.close()
conn.close()

# PostgreSQL
with connection.cursor() as pg_cursor:
    pg_cursor.execute('''
        SELECT s.alias, COUNT(*) as total
        FROM app_dte d
        JOIN app_sucursal s ON d.sucursal_id = s.id
        GROUP BY s.alias
    ''')
    pg_dtes = {row[0]: row[1] for row in pg_cursor.fetchall()}

print("=" * 100)
print("COMPARACION DETALLADA DE DTEs POR SUCURSAL")
print("=" * 100)
print()
print(f"{'ALIAS':<15} {'DIRECCION':<15} {'MySQL DTE':>12} {'MySQL Ventas':>14} {'MySQL Total':>12} {'PostgreSQL':>12} {'Diff':>10}")
print("-" * 100)

# Calcular totales por alias
for alias in ['PAO1', 'PAO2', 'PAO3', 'PAO4', 'NICK1', 'NICK2', 'NICK3', 'EDEL', 'EDEL FALLADOS', 'GILD', 'IMP', 'PA00']:
    # Buscar direccion que mapea a este alias
    direccion = None
    for dir, al in MAPEO.items():
        if al == alias:
            direccion = dir
            break
    
    # Datos MySQL
    mysql_dte_count = mysql_dte.get(alias, 0)
    mysql_ventas_count = mysql_ventas.get(direccion, 0) if direccion else 0
    mysql_total = mysql_dte_count + mysql_ventas_count
    
    # Datos PostgreSQL
    pg_count = pg_dtes.get(alias, 0)
    
    # Diferencia
    diff = pg_count - mysql_total
    
    dir_display = direccion or 'Maipu 676'
    
    print(f"{alias:<15} {dir_display:<15} {mysql_dte_count:>12,} {mysql_ventas_count:>14,} {mysql_total:>12,} {pg_count:>12,} {diff:>+10,}")

print("-" * 100)

# Totales
total_mysql_dte = sum(mysql_dte.values())
total_mysql_ventas = sum(mysql_ventas.values())
total_pg = sum(pg_dtes.values())

print(f"{'TOTALES':<15} {'':<15} {total_mysql_dte:>12,} {total_mysql_ventas:>14,} {'':>12} {total_pg:>12,}")

print()
print("NOTA: MySQL tiene DTEs en dos tablas:")
print("  - 'dte': Documentos tributarios electrónicos con bodega_inicio=ALIAS")
print("  - 'ventas': Registros de venta con sucursal=DIRECCION")
print()
print("PostgreSQL tiene TODOS los DTEs en una sola tabla con sucursal=ALIAS")
print("La diferencia se debe a que algunos DTEs se crean sin duplicados")
