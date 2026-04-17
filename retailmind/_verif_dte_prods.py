"""Verificar Dte_Productos (lineas de detalle de DTEs)."""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db.models import Count, Sum, F
from app.models import Dte, Dte_Productos, Sucursal

conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"))

# ============================================================
# MySQL: productos_dte
# ============================================================
print("=" * 80)
print("DTE_PRODUCTOS - MySQL vs PostgreSQL")
print("=" * 80)

cursor = conn.cursor(dictionary=True, buffered=True)
cursor.execute("SELECT COUNT(*) as total, SUM(stock) as unidades FROM productos_dte")
m = cursor.fetchone()
mysql_total = m['total']
mysql_uni = int(m['unidades'] or 0)
cursor.close()

pg_total = Dte_Productos.objects.count()
pg_uni = int(Dte_Productos.objects.aggregate(t=Sum('stock'))['t'] or 0)

print(f"\n{'Concepto':<25} {'MySQL':>14} {'PostgreSQL':>14} {'Diff':>10}")
print("-" * 70)
print(f"{'Lineas DTE_Productos':<25} {mysql_total:>14,} {pg_total:>14,} {mysql_total - pg_total:>+10,}")
print(f"{'Unidades (stock)':<25} {mysql_uni:>14,} {pg_uni:>14,} {mysql_uni - pg_uni:>+10,}")

# ============================================================
# 2026 por sucursal - lineas de DTEs vendidos
# ============================================================
print("\n" + "=" * 80)
print("DTE_PRODUCTOS 2026 - Por sucursal (lineas vendidas)")
print("=" * 80)

suc_by_alias = {s.alias: s for s in Sucursal.objects.all()}

# MySQL
cursor = conn.cursor(dictionary=True, buffered=True)
cursor.execute("""
    SELECT d.bodega_inicio as alias,
           COUNT(pd.ID) as lineas,
           SUM(pd.stock) as unidades
    FROM productos_dte pd
    JOIN dte d ON pd.factura_asociada = d.n_documento
    WHERE d.fecha_emision >= '2026-01-01' AND d.fecha_emision < '2026-05-01'
      AND d.tipo_documento IN ('Boleta Electronica', 'Boleta', 'Factura Electronica')
      AND d.bodega_inicio IS NOT NULL
    GROUP BY d.bodega_inicio
    ORDER BY d.bodega_inicio
""")
mysql_2026 = {r['alias']: {'lineas': r['lineas'], 'unidades': int(r['unidades'] or 0)}
              for r in cursor}
cursor.close()

# PG
from django.db.models.functions import ExtractYear
pg_2026 = {}
for suc in Sucursal.objects.all():
    qs = Dte_Productos.objects.filter(
        dte__sucursal=suc,
        dte__fecha_emision__year=2026,
        dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    )
    n = qs.count()
    u = int(qs.aggregate(t=Sum('stock'))['t'] or 0)
    if n > 0:
        pg_2026[suc.alias] = {'lineas': n, 'unidades': u}

print(f"\n{'Sucursal':<10} {'MySQL lin':>10} {'PG lin':>10} {'DiffLin':>8}  {'MySQL und':>10} {'PG und':>10} {'DiffUnd':>8}")
print("-" * 80)
aliases_all = sorted(set(list(mysql_2026.keys()) + list(pg_2026.keys())))
tml, tpl, tmu, tpu = 0, 0, 0, 0
for alias in aliases_all:
    m = mysql_2026.get(alias, {'lineas': 0, 'unidades': 0})
    p = pg_2026.get(alias, {'lineas': 0, 'unidades': 0})
    diff_l = m['lineas'] - p['lineas']
    diff_u = m['unidades'] - p['unidades']
    tml += m['lineas']; tpl += p['lineas']; tmu += m['unidades']; tpu += p['unidades']
    mark = "OK" if diff_l == 0 else "!"
    print(f"{alias:<10} {m['lineas']:>10,} {p['lineas']:>10,} {diff_l:>+8,}  {m['unidades']:>10,} {p['unidades']:>10,} {diff_u:>+8,} {mark}")
print("-" * 80)
print(f"{'TOTAL':<10} {tml:>10,} {tpl:>10,} {tml-tpl:>+8,}  {tmu:>10,} {tpu:>10,} {tmu-tpu:>+8,}")

conn.close()
