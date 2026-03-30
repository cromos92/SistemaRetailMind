import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Dte_Detalle_Pago
from django.db.models import Count, Exists, OuterRef
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

print('=== METODOS DE PAGO EN DJANGO (Dte_Detalle_Pago) ===')
for row in Dte_Detalle_Pago.objects.values('metodo_pago').annotate(c=Count('id')).order_by('-c'):
    print(f'  {row["metodo_pago"]:30s}: {row["c"]:>10,}')

print(f'\n  TOTAL pagos Django: {Dte_Detalle_Pago.objects.count():,}')

print('\n=== METODOS DE PAGO EN MySQL (ventas) ===')
cur = conn.cursor(dictionary=True, buffered=True)
cur.execute('''
    SELECT metodo_pago, COUNT(*) as c
    FROM ventas
    GROUP BY metodo_pago
    ORDER BY c DESC
''')
total_mysql = 0
for row in cur:
    mp = row['metodo_pago'] or '(NULL)'
    total_mysql += row['c']
    print(f'  {mp:30s}: {row["c"]:>10,}')
print(f'\n  TOTAL ventas MySQL: {total_mysql:,}')

print('\n=== TARJETAS EN MySQL (ventas.tarjeta) ===')
cur.execute('''
    SELECT tarjeta, COUNT(*) as c
    FROM ventas
    WHERE tarjeta IS NOT NULL AND tarjeta != ''
    GROUP BY tarjeta
    ORDER BY c DESC
''')
for row in cur:
    print(f'  {row["tarjeta"]:30s}: {row["c"]:>10,}')

print('\n=== DTEs VENTA SIN NINGUN PAGO ===')
dtes_venta_sin_pago = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
).exclude(
    Exists(Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk')))
).count()
dtes_venta_total = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
).count()
print(f'  DTEs venta total: {dtes_venta_total:,}')
print(f'  Sin pago: {dtes_venta_sin_pago:,}')
print(f'  Con pago: {dtes_venta_total - dtes_venta_sin_pago:,}')

# Muestra de ventas MySQL que no tienen pago en Django
print('\n=== MUESTRA: ventas MySQL sin match en Django ===')
cur.execute('''
    SELECT n_documento, metodo_pago, tarjeta, monto_pagado, sucursal, fecha
    FROM ventas
    WHERE n_documento > 0
    ORDER BY ID DESC
    LIMIT 20
''')
for row in cur:
    n_doc = row['n_documento']
    tiene_pago = Dte_Detalle_Pago.objects.filter(dte__numero_documento=n_doc).exists()
    marca = 'OK' if tiene_pago else 'SIN PAGO'
    print(f'  n_doc={n_doc} | {row["metodo_pago"]} | tarjeta={row["tarjeta"]} | ${row["monto_pagado"]} | {row["sucursal"]} | {marca}')

cur.close()
conn.close()
