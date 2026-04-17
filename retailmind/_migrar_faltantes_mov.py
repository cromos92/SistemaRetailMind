"""
Migrar movimientos MySQL faltantes (optimizado con SQL puro).

Estrategia:
1. Cargar SOLO los MIG ids faltantes (comparando con PG) - rapido
2. Cache sku -> [pt_id por sucursal] (solo SKUs que necesitamos)
3. INSERT masivo con psycopg2.execute_values (20x mas rapido que bulk_create)
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
import time
from datetime import time as dt_time
from collections import defaultdict
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db import connection
from psycopg2.extras import execute_values

HORA_DEFAULT = dt_time(0, 0, 0)  # 00:00:00 para movimientos sin hora

TIPO_MAP = {
    'Ingreso': 'INGRESO', 'Egreso': 'EGRESO', 'Traspaso': 'INGRESO',
    'Ajuste': 'INGRESO', 'Venta': 'EGRESO',
    'INGRESO': 'INGRESO', 'EGRESO': 'EGRESO',
}
CONCEPTO_MAP = {
    'ingreso_inicial': 'INGRESO_INICIAL',
    'venta_publico': 'VENTA_PUBLICO',
    'traspaso_sucursal': 'TRASPASO_SUCURSAL',
    'correccion_stock': 'CORRECCION_STOCK',
    'ajuste_inventario': 'AJUSTE_INVENTARIO',
    'anulacion_ticket': 'ANULACION_TICKET',
    'cambio_producto_salida': 'CAMBIO_PRODUCTO_SALIDA',
    'cambio_producto_entrada': 'CAMBIO_PRODUCTO_ENTRADA',
    'ajuste_positivo': 'AJUSTE_POSITIVO',
    'ajuste_negativo': 'AJUSTE_NEGATIVO',
}

print("=" * 80)
print("MIGRAR MOVIMIENTOS FALTANTES (OPTIMIZADO)")
print("=" * 80)

# ============================================================
# [1] Cargar MIG:ids existentes en PG (SQL directo)
# ============================================================
print("\n[1] IDs PG existentes...")
t0 = time.time()
pg_ids = set()
with connection.cursor() as cur:
    cur.execute("""
        SELECT substring(referencia_externa, 5)::int
        FROM app_movimientos_producto
        WHERE referencia_externa LIKE 'MIG:%'
    """)
    pg_ids = {r[0] for r in cur}
print(f"  [{time.time()-t0:.0f}s] {len(pg_ids):,} MIG:ids")

# ============================================================
# [2] Cargar sucursales
# ============================================================
print("\n[2] Sucursales...")
sucursales = {}
with connection.cursor() as cur:
    cur.execute("SELECT id, alias FROM app_sucursal")
    for sid, alias in cur:
        sucursales[alias] = sid

# ============================================================
# [3] Leer MySQL faltantes con cursor streaming
# ============================================================
print("\n[3] Identificando faltantes en MySQL (streaming)...")
t0 = time.time()
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    connection_timeout=600, autocommit=True)
cursor = conn.cursor(dictionary=True)
cursor.execute("""
    SELECT id, codigo_asociado, cantidad, fecha, responsable,
           tipo_movimiento, concepto, costo, precio_salida, alias
    FROM movimiento_productos
    WHERE fecha IS NOT NULL
""")

faltantes = []
skus_necesarios = set()
total_mysql = 0
for r in cursor:
    total_mysql += 1
    if r['id'] not in pg_ids:
        faltantes.append(r)
        if r['codigo_asociado']:
            skus_necesarios.add(r['codigo_asociado'])
cursor.close()
print(f"  [{time.time()-t0:.0f}s] MySQL total: {total_mysql:,}, faltantes: {len(faltantes):,}, SKUs unicos: {len(skus_necesarios):,}")

# Liberar set grande de pg_ids
del pg_ids

if not faltantes:
    print("\nNo hay faltantes. Proceso completado.")
    conn.close()
    sys.exit(0)

# ============================================================
# [4] Cache SOLO de los SKUs que necesitamos (optimizado)
# ============================================================
print(f"\n[4] Cache de {len(skus_necesarios):,} SKUs...")
t0 = time.time()
cache_sku = defaultdict(dict)  # sku -> {alias: pt_id}
cache_sku_first = {}  # sku -> primer pt_id (fallback)

with connection.cursor() as cur:
    # Usar UNNEST para pasar todos los SKUs en una query
    sku_list = list(skus_necesarios)
    cur.execute("""
        SELECT pt.sku, pt.id as pt_id, s.alias
        FROM app_producto_talla pt
        JOIN app_producto p ON p.id = pt.producto_id
        LEFT JOIN app_sucursal s ON s.id = p.sucursal_id
        WHERE pt.sku = ANY(%s)
    """, [sku_list])
    for sku, pt_id, alias in cur:
        if alias:
            cache_sku[sku][alias] = pt_id
        if sku not in cache_sku_first:
            cache_sku_first[sku] = pt_id
print(f"  [{time.time()-t0:.0f}s] {len(cache_sku):,} SKUs con al menos 1 producto")

# ============================================================
# [5] Preparar rows para INSERT masivo
# ============================================================
print(f"\n[5] Preparando {len(faltantes):,} registros...")
t0 = time.time()
rows = []
sin_sku = 0

for r in faltantes:
    sku = r['codigo_asociado']
    alias = r['alias']

    # Match: exacto por (sku, alias) o fallback al primero del sku
    pt_id = cache_sku.get(sku, {}).get(alias) or cache_sku_first.get(sku)
    if not pt_id:
        sin_sku += 1
        continue

    tipo_mov = TIPO_MAP.get(r['tipo_movimiento'], 'INGRESO')
    c = (r['concepto'] or '').lower().strip().replace(' ', '_')
    concepto = CONCEPTO_MAP.get(c, 'AJUSTE_INVENTARIO')

    cantidad = int(r['cantidad'] or 0)
    cantidad = -abs(cantidad) if tipo_mov == 'EGRESO' else abs(cantidad)

    fecha_dt = r['fecha']
    if hasattr(fecha_dt, 'date') and hasattr(fecha_dt, 'time'):
        fecha = fecha_dt.date()
        hora = fecha_dt.time()
    else:
        fecha = fecha_dt
        hora = HORA_DEFAULT
    if hora is None:
        hora = HORA_DEFAULT

    rows.append((
        pt_id, tipo_mov, concepto, cantidad,
        int(r['costo'] or 0), int(r['precio_salida'] or 0),
        fecha, hora,
        (r['responsable'] or 'Sistema')[:50],
        f"MIG:{r['id']}", 'COMPLETADO',
        sucursales.get(alias),
    ))

print(f"  [{time.time()-t0:.0f}s] {len(rows):,} para insertar, {sin_sku:,} sin SKU en PG")

# ============================================================
# [6] INSERT masivo con execute_values (rapidisimo)
# ============================================================
print(f"\n[6] INSERT masivo...")
t0 = time.time()
with connection.cursor() as cur:
    inner_cur = cur.cursor  # psycopg2 cursor subyacente
    sql = """
        INSERT INTO app_movimientos_producto
        ("ProductoTalla_id", tipo_movimiento, concepto, cantidad,
         costo, precio, fecha, hora, responsable, referencia_externa,
         estado, sucursal_origen_id, sobreprecio, created_at, updated_at)
        VALUES %s
    """
    template = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,NOW(),NOW())"

    batch_size = 5000
    creados = 0
    for i in range(0, len(rows), batch_size):
        lote = rows[i:i+batch_size]
        execute_values(inner_cur, sql, lote, template=template, page_size=batch_size)
        creados += len(lote)
        if creados % 25000 == 0:
            print(f"  Creados {creados:,} [{time.time()-t0:.0f}s]")

print(f"  [{time.time()-t0:.0f}s] Total insertados: {creados:,}")

conn.close()
print(f"\nOK - Migracion completada")
