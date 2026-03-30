import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Dte_Detalle_Pago, Vendedor
from django.db.models import Count, Exists, OuterRef

print('=== DTEs por tipo_transaccion ===')
for row in Dte.objects.values('tipo_transaccion').annotate(c=Count('id')).order_by('-c'):
    tt = row['tipo_transaccion'] or '(NULL)'
    print(f'  {tt:25s}: {row["c"]:>10,}')

print()
print('=== DTEs con vendedor vs sin vendedor ===')
con = Dte.objects.filter(vendedor__isnull=False).count()
sin = Dte.objects.filter(vendedor__isnull=True).count()
print(f'  Con vendedor: {con:,}')
print(f'  Sin vendedor: {sin:,}')

print()
print('=== Pagos (Dte_Detalle_Pago) ===')
total_pagos = Dte_Detalle_Pago.objects.count()
print(f'  Total pagos: {total_pagos:,}')

dtes_venta = Dte.objects.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])
dtes_con_pago = dtes_venta.filter(
    Exists(Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk')))
).count()
dtes_sin_pago = dtes_venta.exclude(
    Exists(Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk')))
).count()
print(f'  DTEs venta CON pago: {dtes_con_pago:,}')
print(f'  DTEs venta SIN pago: {dtes_sin_pago:,}')

print()
print('=== Vendedores ===')
print(f'  Total: {Vendedor.objects.count()}')
print(f'  Con codigo: {Vendedor.objects.exclude(codigo_vendedor__isnull=True).exclude(codigo_vendedor="").count()}')

print()
print('=== Muestra DTEs tipo VENTA (factura) sin pago ===')
muestra = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
).exclude(
    Exists(Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk')))
).values('id', 'numero_documento', 'tipo_documento', 'tipo_transaccion', 'sucursal__alias', 'vendedor__nombre', 'fecha_emision', 'monto_con_iva')[:20]
for d in muestra:
    print(f'  DTE#{d["numero_documento"]} | {d["tipo_documento"]} | {d["tipo_transaccion"]} | suc={d["sucursal__alias"]} | vend={d["vendedor__nombre"]} | {d["fecha_emision"]} | ${d["monto_con_iva"]}')

print()
print('=== DTEs VENTA por tipo_documento ===')
for row in dtes_venta.values('tipo_documento').annotate(c=Count('id')).order_by('-c'):
    print(f'  {row["tipo_documento"]:30s}: {row["c"]:>10,}')

print()
print('=== Total ventas en MySQL vs pagos vinculados ===')
import mysql.connector
from dotenv import load_dotenv
load_dotenv()
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', 25060)),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DB')
)
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ventas')
print(f'  Ventas MySQL total: {cur.fetchone()[0]:,}')
cur.execute('SELECT COUNT(DISTINCT n_documento) FROM ventas WHERE n_documento > 0')
print(f'  Documentos unicos MySQL: {cur.fetchone()[0]:,}')
cur.close()
conn.close()
