"""
Resumen movimientos por TIPO (INGRESO/EGRESO) y CONCEPTO.
Enfocado en detectar diferencias por tipo de movimiento.
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
import time
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db import connection

TIPO_MAP = {
    'Ingreso': 'INGRESO', 'Egreso': 'EGRESO',
    'Traspaso': 'INGRESO', 'Ajuste': 'INGRESO',
    'Venta': 'EGRESO', 'INGRESO': 'INGRESO', 'EGRESO': 'EGRESO',
}

def mark(pct):
    a = abs(pct)
    if a < 5: return "OK"
    if a < 15: return "!"
    return "!!"


print("=" * 95)
print("RESUMEN MOVIMIENTOS POR TIPO Y CONCEPTO - MySQL vs PostgreSQL")
print("=" * 95)

conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"), connection_timeout=300)

# ============================================================
# [1] POR TIPO_MOVIMIENTO (global)
# ============================================================
print("\n[1] POR TIPO_MOVIMIENTO (global)")
print("-" * 95)
t0 = time.time()

# MySQL: mapear tipos
cursor = conn.cursor()
cursor.execute("""
    SELECT tipo_movimiento, COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
    FROM movimiento_productos
    GROUP BY tipo_movimiento
""")
mysql_tipos_raw = list(cursor)
cursor.close()

# Normalizar
mysql_tipos = {'INGRESO': [0, 0], 'EGRESO': [0, 0], 'OTROS': [0, 0]}
for tipo, n, cant in mysql_tipos_raw:
    t = TIPO_MAP.get(tipo, 'OTROS')
    mysql_tipos[t][0] += n
    mysql_tipos[t][1] += int(cant or 0)

# PG
with connection.cursor() as cur:
    cur.execute("""
        SELECT tipo_movimiento, COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
        FROM app_movimientos_producto GROUP BY tipo_movimiento
    """)
    pg_tipos = {r[0]: [r[1], int(r[2])] for r in cur}

print(f"{'Tipo':<10} {'MySQL docs':>12} {'PG docs':>12} {'Diff docs':>10} {'%':>7}  "
      f"{'MySQL cant':>13} {'PG cant':>13}  {'Estado'}")
print("-" * 95)
for tipo in ['INGRESO', 'EGRESO']:
    mn, mc = mysql_tipos.get(tipo, [0, 0])
    pn, pc = pg_tipos.get(tipo, [0, 0])
    diff_n = pn - mn
    pct = (diff_n / mn * 100) if mn else 0
    print(f"{tipo:<10} {mn:>12,} {pn:>12,} {diff_n:>+10,} {pct:>+6.1f}% "
          f"{mc:>13,} {pc:>13,}  {mark(pct)}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# [2] POR CONCEPTO (global)
# ============================================================
print("\n\n[2] POR CONCEPTO (global)")
print("-" * 95)
t0 = time.time()

cursor = conn.cursor()
cursor.execute("""
    SELECT LOWER(TRIM(REPLACE(concepto, ' ', '_'))) as c, COUNT(*)
    FROM movimiento_productos
    GROUP BY c ORDER BY COUNT(*) DESC LIMIT 20
""")
mysql_concepto = {r[0]: r[1] for r in cursor}
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT LOWER(concepto) as c, COUNT(*)
        FROM app_movimientos_producto GROUP BY c ORDER BY COUNT(*) DESC
    """)
    pg_concepto = {r[0]: r[1] for r in cur}

# Conceptos equivalentes
EQUIV = {
    'venta_publico': 'venta_publico',
    'traspaso_sucursal': 'traspaso_sucursal',
    'ingreso_inicial': 'ingreso_inicial',
    'ajuste_inventario': 'ajuste_inventario',
    'ajuste_positivo': 'ajuste_positivo',
    'ajuste_negativo': 'ajuste_negativo',
    'correccion_stock': 'correccion_stock',
    'cambio_producto_salida': 'cambio_producto_salida',
    'cambio_producto_entrada': 'cambio_producto_entrada',
    'anulacion_ticket': 'anulacion_ticket',
}

print(f"{'Concepto':<30} {'MySQL':>12} {'PG':>12} {'Diff':>10} {'%':>7}")
print("-" * 80)
conceptos_all = sorted(set(list(mysql_concepto.keys()) + list(pg_concepto.keys())),
                      key=lambda x: -(mysql_concepto.get(x, 0) + pg_concepto.get(x, 0)))
for c in conceptos_all[:15]:
    mn = mysql_concepto.get(c, 0)
    pn = pg_concepto.get(c, 0)
    diff = pn - mn
    pct = (diff / mn * 100) if mn else 0
    print(f"{c:<30} {mn:>12,} {pn:>12,} {diff:>+10,} {pct:>+6.1f}% {mark(pct)}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# [3] INGRESO/EGRESO por año
# ============================================================
print("\n\n[3] INGRESO y EGRESO por año")
print("-" * 95)
t0 = time.time()

# MySQL por año y tipo
cursor = conn.cursor()
cursor.execute("""
    SELECT YEAR(fecha), tipo_movimiento, COUNT(*)
    FROM movimiento_productos WHERE fecha IS NOT NULL
    GROUP BY YEAR(fecha), tipo_movimiento
""")
mysql_anio_tipo = {}
for y, tipo, n in cursor:
    t = TIPO_MAP.get(tipo, 'OTROS')
    mysql_anio_tipo.setdefault(y, {'INGRESO': 0, 'EGRESO': 0})
    mysql_anio_tipo[y][t] = mysql_anio_tipo[y].get(t, 0) + n
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT EXTRACT(YEAR FROM fecha)::int, tipo_movimiento, COUNT(*)
        FROM app_movimientos_producto WHERE fecha IS NOT NULL
        GROUP BY EXTRACT(YEAR FROM fecha), tipo_movimiento
    """)
    pg_anio_tipo = {}
    for y, tipo, n in cur:
        pg_anio_tipo.setdefault(y, {}).update({tipo: n})

print(f"{'Año':<6}  {'INGRESO':<30}  {'EGRESO':<30}")
print(f"{'':<6}  {'MySQL':>9} {'PG':>9} {'Diff':>8} {'%':>6}  "
      f"{'MySQL':>9} {'PG':>9} {'Diff':>8} {'%':>6}")
print("-" * 95)
anios = sorted([a for a in set(list(mysql_anio_tipo.keys()) + list(pg_anio_tipo.keys())) if a is not None])
for a in anios:
    m = mysql_anio_tipo.get(a, {})
    p = pg_anio_tipo.get(a, {})
    mi, me = m.get('INGRESO', 0), m.get('EGRESO', 0)
    pi, pe = p.get('INGRESO', 0), p.get('EGRESO', 0)
    di, de = pi - mi, pe - me
    pcti = (di / mi * 100) if mi else 0
    pcte = (de / me * 100) if me else 0
    print(f"{a:<6}  {mi:>9,} {pi:>9,} {di:>+8,} {pcti:>+5.1f}%  "
          f"{me:>9,} {pe:>9,} {de:>+8,} {pcte:>+5.1f}%")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# [4] Diferencia específica en INGRESO_INICIAL y AJUSTE_INVENTARIO
# ============================================================
print("\n\n[4] Conceptos de 'ajuste' vs 'venta/traspaso' (donde suelen haber problemas)")
print("-" * 95)
t0 = time.time()

# MySQL por concepto agrupado
cursor = conn.cursor()
cursor.execute("""
    SELECT YEAR(fecha) as y,
           LOWER(TRIM(REPLACE(concepto, ' ', '_'))) as c,
           COUNT(*)
    FROM movimiento_productos
    WHERE fecha IS NOT NULL
    GROUP BY YEAR(fecha), c
""")
mysql_ac = {}
for y, c, n in cursor:
    mysql_ac.setdefault(y, {})[c] = n
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT EXTRACT(YEAR FROM fecha)::int, LOWER(concepto), COUNT(*)
        FROM app_movimientos_producto WHERE fecha IS NOT NULL
        GROUP BY EXTRACT(YEAR FROM fecha), LOWER(concepto)
    """)
    pg_ac = {}
    for y, c, n in cur:
        pg_ac.setdefault(y, {})[c] = n

# Agrupar en: VENTAS (venta_publico), TRASPASOS (traspaso_sucursal), INVENTARIO (ingreso_inicial + ajustes)
def get_agrupado(dic):
    v = dic.get('venta_publico', 0)
    t = dic.get('traspaso_sucursal', 0)
    inv = (dic.get('ingreso_inicial', 0) + dic.get('ajuste_inventario', 0) +
           dic.get('ajuste_positivo', 0) + dic.get('ajuste_negativo', 0) +
           dic.get('correccion_stock', 0))
    return v, t, inv

print(f"{'Año':<6}  {'VENTAS (venta_publico)':<28}  {'TRASPASOS':<28}  {'INVENTARIO/AJUSTES':<28}")
print(f"{'':<6}  {'MySQL':>8} {'PG':>8} {'Diff':>6} {'%':>5}  "
      f"{'MySQL':>8} {'PG':>8} {'Diff':>6} {'%':>5}  "
      f"{'MySQL':>8} {'PG':>8} {'Diff':>6} {'%':>5}")
print("-" * 120)
for a in anios:
    m = mysql_ac.get(a, {})
    p = pg_ac.get(a, {})
    mv, mt, mi = get_agrupado(m)
    pv, pt, pi = get_agrupado(p)

    def r(me, p):
        d = p - me
        pct = (d / me * 100) if me else 0
        return f"{me:>8,} {p:>8,} {d:>+6,} {pct:>+4.0f}%"

    print(f"{a:<6}  {r(mv, pv)}  {r(mt, pt)}  {r(mi, pi)}")
print(f"  [{time.time()-t0:.1f}s]")

conn.close()
print("\n" + "=" * 95)
print("DIAGNOSTICO:")
print("  - INGRESO/EGRESO → lo importante (deberian estar OK)")
print("  - Ventas/Traspasos → criticos operacionales")
print("  - Inventario/Ajustes → donde suele haber mas ruido")
print("=" * 95)
