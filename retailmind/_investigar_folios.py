"""Investigar los 31 folios que faltan en PG."""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db.models import Sum
from app.models import Dte, Dte_Detalle_Pago, Sucursal

conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"))

# Folios problematicos
folios_nick1 = [254955, 254411, 254338, 255273, 255995, 254600, 255342, 254360, 255258, 255172]
folios_nick2 = [278440, 280874, 277485, 278757, 280468, 277633, 277263, 277159, 277872, 279546]

print("=" * 100)
print("INVESTIGACION: Folios que existen en MySQL ventas pero NO en PG DTEs")
print("=" * 100)

for alias, direccion, folios in [('NICK1', 'Matta 2479', folios_nick1),
                                  ('NICK2', 'Matta 2438', folios_nick2)]:
    print(f"\n\n--- {alias} ({direccion}) ---\n")

    for folio in folios:
        # 1) MySQL.ventas
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT n_documento, tipo_documento, sucursal, DATE(fecha) as dia,
                   monto_pagado, ID_dte, metodo_pago, estado
            FROM ventas WHERE n_documento = %s AND fecha >= '2026-01-01' AND fecha < '2026-05-01'
        """, (folio,))
        ventas_mysql = cursor.fetchall()
        cursor.close()

        # 2) MySQL.dte
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ID, n_documento, tipo_documento, bodega_inicio, bodega_destino,
                   DATE(fecha_emision) as dia, monto_total, estado
            FROM dte WHERE n_documento = %s AND fecha_emision >= '2026-01-01' AND fecha_emision < '2026-05-01'
        """, (folio,))
        dtes_mysql = cursor.fetchall()
        cursor.close()

        # 3) PG: cualquier sucursal
        dtes_pg = Dte.objects.filter(
            numero_documento=folio,
            fecha_emision__gte='2026-01-01', fecha_emision__lt='2026-05-01',
        ).select_related('sucursal')

        print(f"\n  Folio {folio}:")
        print(f"    MySQL ventas:")
        for v in ventas_mysql:
            print(f"      tipo={v['tipo_documento']:<22} sucursal={v['sucursal']:<18} fecha={v['dia']} "
                  f"monto=${int(v['monto_pagado'] or 0):>10,} ID_dte={v['ID_dte'] or 'NULL':<10} "
                  f"estado={v['estado']}")
        print(f"    MySQL dte:")
        for d in dtes_mysql:
            print(f"      ID={d['ID']} tipo={d['tipo_documento']:<22} bodega={d['bodega_inicio']:<10} "
                  f"destino={d['bodega_destino'] or 'NULL':<10} fecha={d['dia']} "
                  f"monto=${int(d['monto_total'] or 0):>10,} estado={d['estado']}")
        if not dtes_mysql:
            print(f"      (no existe en MySQL dte)")

        print(f"    PG DTEs:")
        if dtes_pg.exists():
            for d in dtes_pg:
                pagos = int(Dte_Detalle_Pago.objects.filter(dte=d).aggregate(t=Sum('monto'))['t'] or 0)
                print(f"      id={d.id} tipo={d.tipo_documento:<22} sucursal={d.sucursal.alias if d.sucursal else 'NULL':<10} "
                      f"trans={d.tipo_transaccion:<15} fecha={d.fecha_emision} monto=${int(d.monto_con_iva or 0):>10,} pagos=${pagos:,}")
        else:
            print(f"      (no existe en PG)")

conn.close()
