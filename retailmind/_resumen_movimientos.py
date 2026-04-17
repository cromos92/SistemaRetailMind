"""
Resumen comparativo de Movimientos: MySQL vs PostgreSQL
- Por año
- Por mes (ultimos 2 anios)
- Por sucursal (global y por año reciente)

Uso:
    python _resumen_movimientos.py
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()

import time
from collections import defaultdict
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db import connection


def fmt_diff(diff, total):
    pct = (diff / total * 100) if total else 0
    if abs(pct) < 1:
        return f"${diff:>+11,}  {pct:>+5.1f}%  OK"
    elif abs(pct) < 10:
        return f"${diff:>+11,}  {pct:>+5.1f}%  !"
    else:
        return f"${diff:>+11,}  {pct:>+5.1f}%  !!"


def fmt_diff_n(diff, total):
    pct = (diff / total * 100) if total else 0
    if abs(pct) < 1:
        return f"{diff:>+10,}  {pct:>+5.1f}%  OK"
    elif abs(pct) < 10:
        return f"{diff:>+10,}  {pct:>+5.1f}%  !"
    else:
        return f"{diff:>+10,}  {pct:>+5.1f}%  !!"


print("=" * 90)
print("RESUMEN MOVIMIENTOS - MySQL vs PostgreSQL")
print("=" * 90)

# ============================================================
# CONEXION MYSQL
# ============================================================
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"), connection_timeout=300)

# ============================================================
# 1) TOTAL GLOBAL
# ============================================================
print("\n[1] TOTAL GLOBAL")
print("-" * 90)
t0 = time.time()
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*), COALESCE(SUM(ABS(cantidad)),0) FROM movimiento_productos")
my_n, my_cant = cursor.fetchone()
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
        FROM app_movimientos_producto
    """)
    pg_n, pg_cant = cur.fetchone()

print(f"{'Métrica':<25} {'MySQL':>15} {'PostgreSQL':>15} {'Diff':>25}")
print("-" * 90)
print(f"{'Total movimientos':<25} {my_n:>15,} {pg_n:>15,} {fmt_diff_n(pg_n - my_n, my_n):>25}")
print(f"{'Suma cantidades (abs)':<25} {my_cant:>15,} {pg_cant:>15,} {fmt_diff(pg_cant - my_cant, my_cant):>25}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# 2) POR AÑO
# ============================================================
print("\n\n[2] POR AÑO (conteo de movimientos)")
print("-" * 90)
t0 = time.time()
cursor = conn.cursor()
cursor.execute("""
    SELECT YEAR(fecha) as y, COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
    FROM movimiento_productos WHERE fecha IS NOT NULL
    GROUP BY YEAR(fecha) ORDER BY y
""")
mysql_anio = {r[0]: (r[1], r[2]) for r in cursor}
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT EXTRACT(YEAR FROM fecha)::int, COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
        FROM app_movimientos_producto WHERE fecha IS NOT NULL
        GROUP BY EXTRACT(YEAR FROM fecha) ORDER BY 1
    """)
    pg_anio = {r[0]: (r[1], r[2]) for r in cur}

anios = sorted(set(list(mysql_anio.keys()) + list(pg_anio.keys())))
print(f"{'Año':<6} {'MySQL docs':>12} {'PG docs':>12} {'Diff docs':>10} {'%':>7}  "
      f"{'MySQL cant':>13} {'PG cant':>13} {'Diff cant':>10}")
print("-" * 90)
for a in anios:
    if a is None:
        continue
    m_n, m_c = mysql_anio.get(a, (0, 0))
    p_n, p_c = pg_anio.get(a, (0, 0))
    diff_n = p_n - m_n
    pct_n = (diff_n / m_n * 100) if m_n else 0
    mark = "OK" if abs(pct_n) < 5 else ("!" if abs(pct_n) < 15 else "!!")
    print(f"{a:<6} {m_n:>12,} {p_n:>12,} {diff_n:>+10,} {pct_n:>+6.1f}% "
          f"{m_c:>13,} {p_c:>13,} {p_c - m_c:>+10,} {mark}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# 3) POR MES (últimos 2 años)
# ============================================================
print("\n\n[3] POR MES (2025 y 2026)")
print("-" * 90)
t0 = time.time()
cursor = conn.cursor()
cursor.execute("""
    SELECT DATE_FORMAT(fecha, '%Y-%m') as mes, COUNT(*)
    FROM movimiento_productos
    WHERE fecha >= '2025-01-01' AND fecha < '2027-01-01'
    GROUP BY DATE_FORMAT(fecha, '%Y-%m') ORDER BY mes
""")
mysql_mes = {r[0]: r[1] for r in cursor}
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT TO_CHAR(fecha, 'YYYY-MM'), COUNT(*)
        FROM app_movimientos_producto
        WHERE fecha >= '2025-01-01' AND fecha < '2027-01-01'
        GROUP BY TO_CHAR(fecha, 'YYYY-MM') ORDER BY 1
    """)
    pg_mes = {r[0]: r[1] for r in cur}

meses = sorted(set(list(mysql_mes.keys()) + list(pg_mes.keys())))
print(f"{'Mes':<10} {'MySQL':>12} {'PG':>12} {'Diff':>10} {'%':>7}")
print("-" * 60)
for m in meses:
    mn = mysql_mes.get(m, 0)
    pn = pg_mes.get(m, 0)
    d = pn - mn
    pct = (d / mn * 100) if mn else 0
    mark = "OK" if abs(pct) < 5 else ("!" if abs(pct) < 15 else "!!")
    print(f"{m:<10} {mn:>12,} {pn:>12,} {d:>+10,} {pct:>+6.1f}% {mark}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# 4) POR SUCURSAL (GLOBAL)
# ============================================================
print("\n\n[4] POR SUCURSAL (TODO EL TIEMPO)")
print("-" * 90)
t0 = time.time()
cursor = conn.cursor()
cursor.execute("""
    SELECT alias, COUNT(*), COALESCE(SUM(ABS(cantidad)),0)
    FROM movimiento_productos WHERE alias IS NOT NULL
    GROUP BY alias ORDER BY alias
""")
mysql_suc = {r[0]: (r[1], r[2]) for r in cursor}
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT s.alias, COUNT(*), COALESCE(SUM(ABS(m.cantidad)),0)
        FROM app_movimientos_producto m
        JOIN app_sucursal s ON s.id = m.sucursal_origen_id
        GROUP BY s.alias ORDER BY s.alias
    """)
    pg_suc = {r[0]: (r[1], r[2]) for r in cur}

print(f"{'Sucursal':<10} {'MySQL':>12} {'PG':>12} {'Diff':>10} {'%':>7}  "
      f"{'MySQL cant':>13} {'PG cant':>13}")
print("-" * 90)
for alias in sorted(set(list(mysql_suc.keys()) + list(pg_suc.keys()))):
    m_n, m_c = mysql_suc.get(alias, (0, 0))
    p_n, p_c = pg_suc.get(alias, (0, 0))
    d = p_n - m_n
    pct = (d / m_n * 100) if m_n else 0
    mark = "OK" if abs(pct) < 5 else ("!" if abs(pct) < 15 else "!!")
    print(f"{alias:<10} {m_n:>12,} {p_n:>12,} {d:>+10,} {pct:>+6.1f}% "
          f"{m_c:>13,} {p_c:>13,} {mark}")
print(f"  [{time.time()-t0:.1f}s]")

# ============================================================
# 5) POR SUCURSAL - SOLO 2026
# ============================================================
print("\n\n[5] POR SUCURSAL (SOLO 2026)")
print("-" * 90)
t0 = time.time()
cursor = conn.cursor()
cursor.execute("""
    SELECT alias, COUNT(*)
    FROM movimiento_productos
    WHERE YEAR(fecha) = 2026 AND alias IS NOT NULL
    GROUP BY alias ORDER BY alias
""")
mysql_suc_26 = {r[0]: r[1] for r in cursor}
cursor.close()

with connection.cursor() as cur:
    cur.execute("""
        SELECT s.alias, COUNT(*)
        FROM app_movimientos_producto m
        JOIN app_sucursal s ON s.id = m.sucursal_origen_id
        WHERE EXTRACT(YEAR FROM m.fecha) = 2026
        GROUP BY s.alias ORDER BY s.alias
    """)
    pg_suc_26 = {r[0]: r[1] for r in cur}

print(f"{'Sucursal':<10} {'MySQL':>12} {'PG':>12} {'Diff':>10} {'%':>7}")
print("-" * 60)
for alias in sorted(set(list(mysql_suc_26.keys()) + list(pg_suc_26.keys()))):
    m = mysql_suc_26.get(alias, 0)
    p = pg_suc_26.get(alias, 0)
    d = p - m
    pct = (d / m * 100) if m else 0
    mark = "OK" if abs(pct) < 5 else ("!" if abs(pct) < 15 else "!!")
    print(f"{alias:<10} {m:>12,} {p:>12,} {d:>+10,} {pct:>+6.1f}% {mark}")
print(f"  [{time.time()-t0:.1f}s]")

conn.close()
print("\n" + "=" * 90)
print("FIN")
print("=" * 90)
