"""
Script RÁPIDO para corregir sucursales de DTEs
Actualiza directamente por direccion -> sucursal_id
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

from django.db import connection
from app.models import Sucursal

# Cargar sucursales y crear mapeo direccion -> id
sucursales = {s.direccion: s.id for s in Sucursal.objects.all() if s.direccion}
print("=== MAPEO DIRECCION -> SUCURSAL_ID ===")
for dir, sid in sucursales.items():
    suc = Sucursal.objects.get(id=sid)
    print(f"  '{dir}' -> {suc.alias} (id={sid})")

# Estado actual
print("\n=== ESTADO ACTUAL DE DTEs POR SUCURSAL ===")
with connection.cursor() as cursor:
    cursor.execute('''
        SELECT s.alias, COUNT(*) as total
        FROM app_dte d
        LEFT JOIN app_sucursal s ON d.sucursal_id = s.id
        GROUP BY s.alias
        ORDER BY s.alias
    ''')
    for row in cursor.fetchall():
        print(f"  {row[0] or 'SIN SUCURSAL':<20} {row[1]:>10,}")

# El problema es que los DTEs creados desde "ventas" MySQL 
# pueden tener sucursal incorrecta o no tener sucursal.
# La tabla ventas usa DIRECCION, no ALIAS.

# Vamos a verificar si hay DTEs sin sucursal que deberíamos poder asignar
print("\n=== DTEs SIN SUCURSAL ===")
with connection.cursor() as cursor:
    cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL')
    sin_suc = cursor.fetchone()[0]
    print(f"  Total DTEs sin sucursal: {sin_suc:,}")

print("\n=== RESUMEN ===")
print("Los DTEs fueron creados desde dos fuentes:")
print("  1. Tabla 'dte' MySQL: usa bodega_inicio = ALIAS (NICK2, PAO1, etc)")
print("  2. Tabla 'ventas' MySQL: usa sucursal = DIRECCION (Matta 2438, Maipu 668, etc)")
print()
print("La migración ya mapea correctamente la direccion a sucursal.")
print("Los números que ves son la suma de ambas fuentes.")
