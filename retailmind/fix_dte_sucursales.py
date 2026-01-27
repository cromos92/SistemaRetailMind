"""
Script para corregir la asignación de sucursales en DTEs
El problema: MySQL usa DIRECCION, PostgreSQL usa ALIAS
"""
import os
from pathlib import Path

env_file = Path('.env')
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

import mysql.connector
from app.models import Dte, Sucursal
from django.db import connection

# Mapeo DIRECCION (MySQL) -> ALIAS (PostgreSQL)
DIRECCION_TO_ALIAS = {
    'Maipu 676': ['EDEL', 'EDEL FALLADOS', 'GILD', 'IMP', 'PA00'],  # Centro distribución
    'Maipu 668': 'PAO1',
    'Matta 2422': 'PAO2',
    'Matta 2432': 'PAO3',
    'Matta 2458': 'PAO4',
    'Matta 2479': 'NICK1',
    'Matta 2438': 'NICK2',
    'Matta 2418': 'NICK3',
}

# Cargar sucursales
sucursales_by_alias = {s.alias: s for s in Sucursal.objects.all()}
print(f"Sucursales cargadas: {list(sucursales_by_alias.keys())}")

# MySQL connection
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

print("\n=== ANALIZANDO VENTAS EN MYSQL ===")
cursor.execute('''
    SELECT sucursal, n_documento, tipo_documento, fecha
    FROM ventas 
    ORDER BY ID
    LIMIT 20
''')
print("Muestra de datos MySQL (ventas):")
for row in cursor.fetchall():
    print(f"  sucursal={row['sucursal']}, folio={row['n_documento']}, tipo={row['tipo_documento']}")

# Verificar qué direcciones únicas hay
cursor.execute('SELECT DISTINCT sucursal FROM ventas ORDER BY sucursal')
print("\nDirecciones únicas en MySQL ventas:")
for row in cursor.fetchall():
    print(f"  - '{row['sucursal']}'")

print("\n=== PROPUESTA DE CORRECCION ===")
print("Mapeo DIRECCION -> ALIAS:")
for direccion, alias in DIRECCION_TO_ALIAS.items():
    if isinstance(alias, list):
        print(f"  '{direccion}' -> {alias} (múltiples, necesita lógica adicional)")
    else:
        print(f"  '{direccion}' -> '{alias}'")

print("\n=== DIRECCIONES EN POSTGRESQL SUCURSALES ===")
for s in Sucursal.objects.all():
    print(f"  {s.alias:<15} -> '{s.direccion}'")

print("\n=== CREANDO MAPEO INVERSO (DIRECCION -> ALIAS) ===")
DIRECCION_TO_ALIAS_EXACT = {}
for s in Sucursal.objects.all():
    if s.direccion:
        DIRECCION_TO_ALIAS_EXACT[s.direccion] = s.alias
        print(f"  '{s.direccion}' -> {s.alias}")

cursor.close()
conn.close()
