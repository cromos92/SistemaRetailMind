"""
Analiza los movimientos MySQL que NO estan en PG.
Identifica los TOP SKUs problematicos y verifica si existen en PG.

Rapido: usa SQL directo, no carga todo a memoria.
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
import time
from collections import Counter, defaultdict
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db import connection

print("=" * 90)
print("ANALISIS: SKUs problematicos en movimientos faltantes")
print("=" * 90)

# ============================================================
# [1] Cargar MIG ids de PG
# ============================================================
print("\n[1] Cargando IDs PG migrados...")
t0 = time.time()
with connection.cursor() as cur:
    cur.execute("""
        SELECT substring(referencia_externa, 5)::int
        FROM app_movimientos_producto
        WHERE referencia_externa LIKE 'MIG:%'
    """)
    pg_ids = {r[0] for r in cur}
print(f"  [{time.time()-t0:.1f}s] {len(pg_ids):,} IDs en PG")

# ============================================================
# [2] Leer MySQL e identificar faltantes + agrupar por SKU
# ============================================================
print("\n[2] Leyendo MySQL e identificando faltantes...")
t0 = time.time()
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"), connection_timeout=300)
cursor = conn.cursor()
cursor.execute("""
    SELECT id, codigo_asociado, alias, YEAR(fecha)
    FROM movimiento_productos
    WHERE fecha IS NOT NULL
""")

sku_counter = Counter()  # sku -> cantidad de faltantes
sku_alias_counter = Counter()  # (sku, alias) -> cantidad
sku_por_anio = defaultdict(lambda: Counter())  # anio -> Counter(sku)

total_mysql = 0
total_faltan = 0
for mid, sku, alias, anio in cursor:
    total_mysql += 1
    if mid in pg_ids:
        continue
    total_faltan += 1
    if sku:
        sku_counter[sku] += 1
        sku_alias_counter[(sku, alias)] += 1
        if anio:
            sku_por_anio[anio][sku] += 1
cursor.close()
print(f"  [{time.time()-t0:.1f}s] MySQL: {total_mysql:,}, faltantes: {total_faltan:,}")
print(f"  SKUs unicos en faltantes: {len(sku_counter):,}")

# ============================================================
# [3] Top SKUs mas problematicos
# ============================================================
print("\n[3] TOP 25 SKUs con mas movimientos faltantes:")
print(f"{'#':>3} {'SKU':>12} {'Movs faltantes':>15} {'% del total':>12}")
print("-" * 50)
for i, (sku, n) in enumerate(sku_counter.most_common(25), 1):
    pct = n / total_faltan * 100 if total_faltan else 0
    print(f"{i:>3} {sku:>12} {n:>15,} {pct:>11.2f}%")

# Acumulado TOP 25
top25_total = sum(n for _, n in sku_counter.most_common(25))
print(f"\n  Los TOP 25 SKUs = {top25_total:,} movimientos ({top25_total/total_faltan*100:.1f}% del total faltante)")

# ============================================================
# [4] Verificar si esos TOP SKUs existen en PG
# ============================================================
print("\n[4] Verificando si los TOP 50 SKUs existen en PG...")
t0 = time.time()
top_skus = [sku for sku, _ in sku_counter.most_common(50)]

with connection.cursor() as cur:
    cur.execute("""
        SELECT pt.sku, COUNT(*) as n_pts,
               ARRAY_AGG(DISTINCT s.alias) as sucursales
        FROM app_producto_talla pt
        JOIN app_producto p ON p.id = pt.producto_id
        LEFT JOIN app_sucursal s ON s.id = p.sucursal_id
        WHERE pt.sku = ANY(%s)
        GROUP BY pt.sku
    """, [top_skus])

    sku_en_pg = {r[0]: (r[1], r[2]) for r in cur}

print(f"  [{time.time()-t0:.1f}s] {len(sku_en_pg)}/{len(top_skus)} SKUs encontrados en PG")

print(f"\n{'SKU':>12} {'Movs falt':>10} {'PG pt_count':>12} {'Sucursales en PG':<60}")
print("-" * 100)
for sku, n in sku_counter.most_common(50):
    if sku in sku_en_pg:
        n_pts, sucs = sku_en_pg[sku]
        sucs_str = ', '.join(str(s) for s in sucs if s) if sucs else 'sin sucursal'
        print(f"{sku:>12} {n:>10,} {n_pts:>12,} {sucs_str[:60]:<60}")
    else:
        print(f"{sku:>12} {n:>10,} {'NO EXISTE':>12} {'(no esta en PG)':<60}")

# ============================================================
# [5] SKUs que NO existen en PG
# ============================================================
print("\n[5] SKUs que faltan TOTALMENTE en PG (sampleo):")
no_existen = []
for sku, n in sku_counter.most_common(500):
    if sku not in sku_en_pg:
        no_existen.append((sku, n))

if no_existen:
    # Verificar los primeros 30
    skus_check = [s for s, _ in no_existen[:30]]
    with connection.cursor() as cur:
        cur.execute("SELECT sku FROM app_producto_talla WHERE sku = ANY(%s)", [skus_check])
        exist_set = {r[0] for r in cur}

    print(f"\n  Top SKUs que NO estan en PG:")
    cnt_impact = 0
    for sku, n in no_existen[:20]:
        if sku in exist_set:
            continue
        cnt_impact += n
        # Ver si esta en MySQL.talla
        mcursor = conn.cursor()
        mcursor.execute("SELECT articulo, alias FROM talla WHERE codigo_asociado = %s LIMIT 3", [sku])
        mysql_info = list(mcursor)
        mcursor.close()
        existe_mysql = f"MySQL.talla: {len(mysql_info)} registros" if mysql_info else "NO existe en MySQL.talla"
        print(f"    SKU {sku}: {n:,} movs faltantes - {existe_mysql}")

    total_no_existen = sum(n for _, n in no_existen)
    print(f"\n  Total SKUs sin match: {len(no_existen)} → {total_no_existen:,} movimientos "
          f"({total_no_existen/total_faltan*100:.1f}% del total faltante)")

conn.close()
print("\n" + "=" * 90)
print("RECOMENDACION: si los TOP SKUs existen en PG, ejecutar _migrar_faltantes_mov.py")
print("=" * 90)
