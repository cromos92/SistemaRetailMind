"""
Comparar DTEs de NICK1: Django vs MySQL
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
from app.models import Dte, Sucursal

print("="*70)
print("COMPARAR NICK1: Django vs MySQL")
print("="*70)

# Sucursal NICK1
nick1 = Sucursal.objects.get(alias='NICK1')
print(f"\nSucursal NICK1: ID={nick1.id}, direccion='{nick1.direccion}'")

# DTEs en Django para NICK1
dtes_django = set(
    str(n) for n in Dte.objects.filter(sucursal=nick1).values_list('numero_documento', flat=True)
)
print(f"\nDTEs de NICK1 en Django: {len(dtes_django):,}")

# Conexión MySQL
mysql_conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE'),
    port=int(os.getenv('MYSQL_PORT', 3306))
)
cursor = mysql_conn.cursor(dictionary=True)

# DTEs en MySQL ventas para NICK1 (Matta 2479)
cursor.execute('''
    SELECT DISTINCT n_documento
    FROM ventas
    WHERE sucursal = 'Matta 2479'
      AND n_documento IS NOT NULL
      AND n_documento != ''
      AND n_documento != '0'
''')
dtes_mysql = set(str(row['n_documento']) for row in cursor)
print(f"DTEs de NICK1 en MySQL ventas: {len(dtes_mysql):,}")

# Diferencias
solo_mysql = dtes_mysql - dtes_django
solo_django = dtes_django - dtes_mysql
ambos = dtes_mysql & dtes_django

print(f"\n  En ambos (OK): {len(ambos):,}")
print(f"  Solo en MySQL (FALTANTES en Django): {len(solo_mysql):,}")
print(f"  Solo en Django (extras): {len(solo_django):,}")

# Ejemplos de faltantes
if solo_mysql:
    ejemplos = list(solo_mysql)[:10]
    print(f"\n  Ejemplos de n_documento faltantes: {ejemplos}")
    
    # Ver detalles de esos en MySQL
    if ejemplos:
        placeholders = ','.join(['%s'] * len(ejemplos))
        cursor.execute(f'''
            SELECT n_documento, tipo_documento, fecha, sucursal, codigo_vendedor
            FROM ventas
            WHERE n_documento IN ({placeholders})
            AND sucursal = 'Matta 2479'
            GROUP BY n_documento, tipo_documento
            LIMIT 10
        ''', ejemplos)
        print("\n  Detalles de faltantes en MySQL:")
        for row in cursor:
            print(f"    n_doc={row['n_documento']}, tipo={row['tipo_documento']}, "
                  f"fecha={row['fecha']}, vend={row['codigo_vendedor']}")

cursor.close()
mysql_conn.close()
print("\n" + "="*70)
