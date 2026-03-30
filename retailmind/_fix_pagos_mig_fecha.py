import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

import mysql.connector
from dotenv import load_dotenv
load_dotenv()
from app.models import Dte_Detalle_Pago

conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'), port=int(os.getenv('MYSQL_PORT', 3306)),
    user=os.getenv('MYSQL_USER'), password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)
cur = conn.cursor(dictionary=True)

# 1) Cargar todos los pagos MIG- de Django
print('Cargando pagos MIG- de Django...')
pagos_mig = list(
    Dte_Detalle_Pago.objects
    .filter(voucher__startswith='MIG-')
    .select_related('dte')
    .values('id', 'voucher', 'dte__id', 'dte__numero_documento', 'dte__fecha_emision')
)
print(f'  Total pagos MIG-: {len(pagos_mig):,}')

# Extraer IDs de MySQL ventas
ventas_ids = []
pago_map = {}
for p in pagos_mig:
    vid_str = p['voucher'].replace('MIG-', '')
    try:
        vid = int(vid_str)
        ventas_ids.append(vid)
        pago_map[vid] = p
    except ValueError:
        pass

print(f'  IDs de ventas MySQL a consultar: {len(ventas_ids):,}')

# 2) Cargar datos de MySQL en batch
print('Cargando datos de MySQL ventas en batch...')
ventas_data = {}
batch_size = 10000
for i in range(0, len(ventas_ids), batch_size):
    batch = ventas_ids[i:i + batch_size]
    placeholders = ','.join(['%s'] * len(batch))
    cur.execute(f'SELECT ID, fecha, n_documento FROM ventas WHERE ID IN ({placeholders})', batch)
    for row in cur.fetchall():
        ventas_data[row['ID']] = row
    print(f'  Procesado {min(i + batch_size, len(ventas_ids)):,}/{len(ventas_ids):,}', end='\r')

print(f'\n  Ventas cargadas: {len(ventas_data):,}')

# 3) Detectar pagos problemáticos
print('Analizando incompatibilidades...')
problemas = []

for vid, pago in pago_map.items():
    venta = ventas_data.get(vid)
    dte_num = pago['dte__numero_documento']
    dte_fecha = pago['dte__fecha_emision']

    if not venta:
        continue

    venta_fecha = venta['fecha']
    venta_ndoc = venta['n_documento']

    # Caso 1: el n_documento de la venta NO coincide con el DTE al que fue asignado
    # Esto significa que fue asignado por el fallback de numero solo a un DTE equivocado
    if venta_ndoc and dte_num and int(venta_ndoc) != int(dte_num):
        problemas.append({
            'pago_id': pago['id'],
            'voucher': pago['voucher'],
            'dte_num': dte_num,
            'dte_fecha': dte_fecha,
            'venta_fecha': venta_fecha,
            'venta_ndoc': venta_ndoc,
            'diff_dias': None,
            'razon': 'ndoc_distinto',
        })
        continue

    # Caso 2: mismo n_documento pero fecha incompatible (>1 año de diferencia)
    if venta_fecha and dte_fecha:
        vf = venta_fecha.date() if hasattr(venta_fecha, 'date') else venta_fecha
        df = dte_fecha.date() if hasattr(dte_fecha, 'date') else dte_fecha
        diff = abs((df - vf).days)
        if diff > 365:
            problemas.append({
                'pago_id': pago['id'],
                'voucher': pago['voucher'],
                'dte_num': dte_num,
                'dte_fecha': df,
                'venta_fecha': vf,
                'venta_ndoc': venta_ndoc,
                'diff_dias': diff,
                'razon': 'fecha_incompatible',
            })

print(f'\nPagos MIG- mal asignados: {len(problemas):,}')
for p in sorted(problemas, key=lambda x: x['diff_dias'] or 9999, reverse=True)[:20]:
    print(f"  pago#{p['pago_id']} {p['voucher']} -> DTE#{p['dte_num']} ({p['dte_fecha']}) | venta_ndoc={p['venta_ndoc']} venta_fecha={p['venta_fecha']} | razon={p['razon']}")

if problemas:
    print(f'\nEliminar {len(problemas):,} pagos mal asignados? (s/n)')
    resp = input()
    if resp.lower() == 's':
        ids_borrar = [p['pago_id'] for p in problemas]
        deleted, _ = Dte_Detalle_Pago.objects.filter(id__in=ids_borrar).delete()
        print(f'  Eliminados {deleted:,} pagos mal asignados')
        print('  Ahora corre: python manage.py migrate_from_laravel --tables ventas_pagos --no-input')
    else:
        print('  Cancelado')
else:
    print('  No se encontraron pagos mal asignados')

cur.close()
conn.close()
