import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
from django.db.models import Sum
from app.models import Dte, Dte_Detalle_Pago, Sucursal
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"), connection_timeout=300, autocommit=True)
suc_map = {}
for s in Sucursal.objects.all():
    if s.direccion: suc_map[s.direccion] = s

cursor = conn.cursor(dictionary=True, buffered=True)
cursor.execute("SELECT sucursal, COUNT(DISTINCT n_documento) as docs, SUM(monto_pagado) as pagado FROM ventas GROUP BY sucursal")
mysql_all = {}
for r in cursor:
    suc = suc_map.get(r['sucursal'])
    if suc: mysql_all[suc.alias] = {'docs': r['docs'], 'pagado': int(r['pagado'] or 0)}
cursor.close()
conn.close()

print(f"{'Suc':<8} {'MySQL docs':>10} {'MySQL pagado':>16} {'PG docs':>10} {'PG pagos':>16} {'Diff':>14} {'%':>7}")
print("=" * 90)
tm, tp = 0, 0
for alias in sorted(mysql_all.keys()):
    m = mysql_all[alias]
    suc = Sucursal.objects.get(alias=alias)
    dtes = Dte.objects.filter(sucursal=suc, tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])
    dc = dtes.count()
    dp = int(Dte_Detalle_Pago.objects.filter(
        dte__sucursal=suc, dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
    ).aggregate(t=Sum('monto'))['t'] or 0)
    diff = m['pagado'] - dp
    pct = (diff / m['pagado'] * 100) if m['pagado'] else 0
    mark = " <--" if abs(pct) > 1 else ""
    print(f"{alias:<8} {m['docs']:>10,} ${m['pagado']:>15,} {dc:>10,} ${dp:>15,} ${diff:>13,} {pct:>6.1f}%{mark}")
    tm += m['pagado']; tp += dp
print("=" * 90)
print(f"{'TOTAL':<8} {'':>10} ${tm:>15,} {'':>10} ${tp:>15,} ${tm - tp:>13,} {((tm-tp)/tm*100) if tm else 0:>6.1f}%")
