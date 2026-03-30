import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Vendedor, Sucursal
import mysql.connector
from dotenv import load_dotenv
load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 3306)),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)

# Códigos que existen en Django
codigos_django = set()
for v in Vendedor.objects.all():
    if v.codigo_vendedor:
        codigos_django.add(str(v.codigo_vendedor))

print(f'Codigos vendedor en Django: {len(codigos_django)}')

# Sucursales: dir/alias → id
suc_por_dir = {}
suc_por_alias = {}
for suc in Sucursal.objects.all():
    if suc.direccion:
        suc_por_dir[suc.direccion] = suc.id
    suc_por_alias[suc.alias] = suc.id

# DTEs sin vendedor
dtes_sin = set()
for row in Dte.objects.filter(vendedor__isnull=True).values_list('numero_documento', 'sucursal_id'):
    dtes_sin.add((row[0], row[1]))
print(f'DTEs sin vendedor: {len(dtes_sin)}')

# Buscar qué codigos de MySQL faltan en Django
cursor = conn.cursor(dictionary=True, buffered=True)
cursor.execute('''
    SELECT n_documento, codigo_vendedor, nombre_vendedor, sucursal
    FROM ventas
    WHERE n_documento > 0
    AND codigo_vendedor IS NOT NULL
    AND codigo_vendedor != ''
    AND codigo_vendedor != '0'
    ORDER BY n_documento, ID
''')

codigos_faltantes = {}  # codigo → {nombre, count, sucursales}
encontrados = 0
no_encontrados = 0

for row in cursor:
    n_doc = row['n_documento']
    codigo = str(row['codigo_vendedor'])
    suc_mysql = row['sucursal']
    suc_id = suc_por_dir.get(suc_mysql) or suc_por_alias.get(suc_mysql) if suc_mysql else None

    if (n_doc, suc_id) not in dtes_sin:
        continue

    if codigo not in codigos_django:
        no_encontrados += 1
        if codigo not in codigos_faltantes:
            codigos_faltantes[codigo] = {
                'nombre': row['nombre_vendedor'],
                'count': 0,
                'sucursales': set()
            }
        codigos_faltantes[codigo]['count'] += 1
        if suc_mysql:
            codigos_faltantes[codigo]['sucursales'].add(suc_mysql)
    else:
        encontrados += 1

cursor.close()
conn.close()

print(f'\nVentas de DTEs sin vendedor: encontrados={encontrados}, no_encontrados={no_encontrados}')
print(f'\nCodigos FALTANTES en Django ({len(codigos_faltantes)} unicos):')
for codigo, info in sorted(codigos_faltantes.items(), key=lambda x: -x[1]['count'])[:30]:
    sucs = ', '.join(sorted(info['sucursales']))
    print(f'  codigo={codigo:10s} | nombre="{info["nombre"]}" | {info["count"]:>6,} ventas | sucursales: {sucs}')

# Ver muestra de codigos que SÍ existen en Django
print(f'\nMuestra de codigos Django:')
for v in Vendedor.objects.all()[:10]:
    print(f'  id={v.id} codigo="{v.codigo_vendedor}" nombre="{v.nombre}" empresa={v.empresa_id}')
