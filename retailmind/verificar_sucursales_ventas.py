"""
Verificar sucursales en MySQL ventas vs Django
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from dotenv import load_dotenv
load_dotenv()

import mysql.connector
from app.models import Sucursal

print("="*70)
print("MAPEO SUCURSALES: MySQL ventas -> Django")
print("="*70)

# Sucursales en Django
print("\n1. Sucursales en Django (direccion -> alias):")
sucursales_django = {}
for suc in Sucursal.objects.all():
    print(f"   '{suc.direccion}' -> {suc.alias} (ID={suc.id})")
    sucursales_django[suc.direccion] = suc.alias

# Conexión MySQL
mysql_conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE'),
    port=int(os.getenv('MYSQL_PORT', 3306))
)
cursor = mysql_conn.cursor(dictionary=True)

# Sucursales únicas en MySQL ventas
print("\n2. Valores únicos de 'sucursal' en MySQL ventas:")
cursor.execute('''
    SELECT sucursal, COUNT(*) as total
    FROM ventas
    GROUP BY sucursal
    ORDER BY total DESC
''')
for row in cursor:
    suc = row['sucursal']
    total = row['total']
    # Verificar si existe en Django
    alias = sucursales_django.get(suc, 'NO MAPEADA')
    print(f"   '{suc}' -> {alias} ({total:,} registros)")

# Documentos faltantes por sucursal
print("\n3. Documentos faltantes en Django por sucursal (MySQL ventas):")
cursor.execute('''
    SELECT 
        sucursal,
        COUNT(DISTINCT n_documento) as docs_unicos
    FROM ventas
    WHERE n_documento IS NOT NULL
      AND n_documento != ''
      AND n_documento != '0'
    GROUP BY sucursal
    ORDER BY docs_unicos DESC
''')
for row in cursor:
    suc = row['sucursal']
    docs = row['docs_unicos']
    alias = sucursales_django.get(suc, 'NO MAPEADA')
    print(f"   '{suc}' -> {alias}: {docs:,} docs únicos")

cursor.close()
mysql_conn.close()
print("\n" + "="*70)
