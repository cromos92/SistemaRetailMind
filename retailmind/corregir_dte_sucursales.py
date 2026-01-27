"""
Script para corregir sucursales de DTEs basándose en ventas MySQL
Mapea DIRECCION (MySQL ventas.sucursal) -> ALIAS (PostgreSQL Sucursal)
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
from app.models import Dte, Sucursal

# Mapeo DIRECCION -> ALIAS
DIRECCION_TO_ALIAS = {
    'Maipu 668': 'PAO1',
    'Matta 2422': 'PAO2',
    'Matta 2432': 'PAO3',
    'Matta 2458': 'PAO4',
    'Matta 2479': 'NICK1',
    'Matta 2438': 'NICK2',
    'Matta 2418': 'NICK3',
    # Casos especiales - sin ventas directas
    'Maipu 676': 'EDEL',  # Default para centro de distribución
    'Matta 2429': 'PAO3',  # Typo probable
    'Matta 279': 'NICK1',  # Typo probable
}

# Cargar sucursales
sucursales_by_alias = {s.alias: s for s in Sucursal.objects.all()}
sucursales_by_direccion = {s.direccion: s for s in Sucursal.objects.all() if s.direccion}

print("=== SUCURSALES DISPONIBLES ===")
for alias, suc in sucursales_by_alias.items():
    print(f"  {alias:<15} (id={suc.id})")

# MySQL connection
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

print("\n=== OBTENIENDO VENTAS DE MYSQL ===")
cursor.execute('''
    SELECT n_documento, sub_total, sucursal, tipo_documento
    FROM ventas
''')

# Crear diccionario de ventas: (folio, monto) -> direccion
ventas_map = {}
for row in cursor:
    key = (row['n_documento'], int(row['sub_total'] or 0))
    ventas_map[key] = row['sucursal']

print(f"  Total ventas cargadas: {len(ventas_map):,}")

cursor.close()
conn.close()

# Ahora actualizar DTEs en PostgreSQL
print("\n=== ANALIZANDO DTEs EN POSTGRESQL ===")

# DTEs sin sucursal o con sucursal incorrecta
dtes_a_corregir = []
dtes_ok = 0
dtes_sin_match = 0

pg_cursor = connection.cursor()

# Obtener todos los DTEs con su folio y monto
pg_cursor.execute('''
    SELECT d.id, d.numero_documento, d.monto_con_iva, d.sucursal_id, s.alias
    FROM app_dte d
    LEFT JOIN app_sucursal s ON d.sucursal_id = s.id
''')

for row in pg_cursor.fetchall():
    dte_id, folio, monto, sucursal_id, alias_actual = row
    
    # Buscar en ventas_map
    key = (folio, int(monto or 0))
    direccion_mysql = ventas_map.get(key)
    
    if not direccion_mysql:
        # Intentar solo con folio
        for k, v in ventas_map.items():
            if k[0] == folio:
                direccion_mysql = v
                break
    
    if direccion_mysql:
        # Obtener alias correcto
        alias_correcto = DIRECCION_TO_ALIAS.get(direccion_mysql)
        
        if alias_correcto and alias_correcto in sucursales_by_alias:
            sucursal_correcta = sucursales_by_alias[alias_correcto]
            
            if sucursal_id != sucursal_correcta.id:
                dtes_a_corregir.append((dte_id, sucursal_correcta.id, alias_actual, alias_correcto))
            else:
                dtes_ok += 1
        else:
            dtes_sin_match += 1
    else:
        dtes_sin_match += 1

print(f"  DTEs OK (sucursal correcta): {dtes_ok:,}")
print(f"  DTEs a corregir: {len(dtes_a_corregir):,}")
print(f"  DTEs sin match en ventas: {dtes_sin_match:,}")

if dtes_a_corregir:
    print("\n=== MUESTRA DE CORRECCIONES ===")
    for dte_id, suc_id, alias_ant, alias_nuevo in dtes_a_corregir[:10]:
        print(f"  DTE {dte_id}: {alias_ant or 'NULL'} -> {alias_nuevo}")
    
    respuesta = input("\n¿Aplicar correcciones? (s/n): ")
    
    if respuesta.lower() == 's':
        print("\n=== APLICANDO CORRECCIONES ===")
        
        # Actualizar en batches
        batch_size = 5000
        total_corregidos = 0
        
        for i in range(0, len(dtes_a_corregir), batch_size):
            batch = dtes_a_corregir[i:i+batch_size]
            
            # Construir UPDATE con CASE
            ids_by_sucursal = {}
            for dte_id, suc_id, _, _ in batch:
                if suc_id not in ids_by_sucursal:
                    ids_by_sucursal[suc_id] = []
                ids_by_sucursal[suc_id].append(str(dte_id))
            
            for suc_id, ids in ids_by_sucursal.items():
                pg_cursor.execute(f'''
                    UPDATE app_dte 
                    SET sucursal_id = {suc_id}
                    WHERE id IN ({','.join(ids)})
                ''')
                total_corregidos += len(ids)
            
            print(f"  Procesados: {min(i + batch_size, len(dtes_a_corregir)):,} / {len(dtes_a_corregir):,}")
        
        connection.commit()
        print(f"\n✓ {total_corregidos:,} DTEs corregidos")
    else:
        print("Operación cancelada")
else:
    print("\nNo hay DTEs que corregir")

pg_cursor.close()
