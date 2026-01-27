"""Verificar fuentes de DTEs"""
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

conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

print("=== TABLA DTE EN MYSQL (bodega_inicio) ===")
cursor.execute('''
    SELECT bodega_inicio, COUNT(*) as total 
    FROM dte 
    GROUP BY bodega_inicio 
    ORDER BY bodega_inicio
''')
for row in cursor.fetchall():
    print(f"  {row['bodega_inicio'] or 'NULL':<20} {row['total']:>10,}")

cursor.execute('SELECT COUNT(*) as total FROM dte')
total_dte = cursor.fetchone()['total']
print(f"\n  TOTAL DTEs en tabla dte: {total_dte:,}")

print("\n=== TABLA VENTAS EN MYSQL (sucursal) ===")
cursor.execute('''
    SELECT sucursal, COUNT(*) as total 
    FROM ventas 
    GROUP BY sucursal 
    ORDER BY sucursal
''')
for row in cursor.fetchall():
    print(f"  {row['sucursal'] or 'NULL':<20} {row['total']:>10,}")

cursor.execute('SELECT COUNT(*) as total FROM ventas')
total_ventas = cursor.fetchone()['total']
print(f"\n  TOTAL registros en tabla ventas: {total_ventas:,}")

# Verificar cuántos ventas NO tienen DTE asociado
cursor.execute('''
    SELECT COUNT(*) as total FROM ventas WHERE ID_dte IS NULL OR ID_dte = 0
''')
sin_dte = cursor.fetchone()['total']
print(f"  Ventas SIN DTE asociado: {sin_dte:,}")

cursor.close()
conn.close()
