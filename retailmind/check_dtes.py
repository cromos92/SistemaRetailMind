"""Verificar DTEs por sucursal"""
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
from django.db.models import Count

# MySQL
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
)
cursor = conn.cursor(dictionary=True)

print('=== DTEs POR SUCURSAL EN MYSQL (ventas.sucursal) ===')
cursor.execute('''
    SELECT sucursal, COUNT(*) as total 
    FROM ventas 
    GROUP BY sucursal 
    ORDER BY sucursal
''')
mysql_dtes = {}
for row in cursor.fetchall():
    mysql_dtes[row['sucursal']] = row['total']
    print(f"  {row['sucursal']:<20} {row['total']:>10,}")

print()
print('=== DTEs POR SUCURSAL EN POSTGRESQL ===')
pg_dtes = Dte.objects.values('sucursal__alias').annotate(total=Count('id')).order_by('sucursal__alias')
pg_dict = {}
for item in pg_dtes:
    alias = item['sucursal__alias'] or 'SIN SUCURSAL'
    pg_dict[alias] = item['total']
    print(f"  {alias:<20} {item['total']:>10,}")

print()
print('=== COMPARACION ===')
print(f"  {'Sucursal':<20} {'MySQL':>12} {'PostgreSQL':>12} {'Diferencia':>12}")
print('  ' + '-' * 60)

# Get all aliases
all_aliases = set(mysql_dtes.keys()) | set(pg_dict.keys())

for alias in sorted(all_aliases):
    if alias == 'SIN SUCURSAL':
        continue
    my = mysql_dtes.get(alias, 0)
    pg = pg_dict.get(alias, 0)
    diff = pg - my
    status = 'OK' if diff == 0 else 'DIFF'
    print(f"  {alias:<20} {my:>12,} {pg:>12,} {diff:>+12,} {status}")

# Sin sucursal
if 'SIN SUCURSAL' in pg_dict or None in pg_dict:
    sin_suc = pg_dict.get('SIN SUCURSAL', 0) + pg_dict.get(None, 0)
    print(f"  {'SIN SUCURSAL':<20} {0:>12,} {sin_suc:>12,} {sin_suc:>+12,}")

cursor.close()
conn.close()
