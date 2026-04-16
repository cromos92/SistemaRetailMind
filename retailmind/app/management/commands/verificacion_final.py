"""
Verificacion final: compara MySQL vs PostgreSQL

Muestra por sucursal:
- Stock (unidades)
- SKUs (productos)
- Ventas (DTEs VENTA+VENTA_PUBLICO) y montos
- DTEs fantasma (en PG pero no en MySQL)

Uso:
    python manage.py verificacion_final
    python manage.py verificacion_final --fecha-desde 2026-04-01 --fecha-hasta 2026-04-15
    python manage.py verificacion_final --sucursal PAO1
"""
import os
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db.models import Sum, Count, F

from app.models import Dte, Dte_Detalle_Pago, Producto_Talla, Sucursal


class Command(BaseCommand):
    help = "Verificacion final: MySQL vs PostgreSQL (stock, ventas, DTEs)"

    def add_arguments(self, parser):
        parser.add_argument("--fecha-desde", type=str, default="", help="YYYY-MM-DD")
        parser.add_argument("--fecha-hasta", type=str, default="", help="YYYY-MM-DD")
        parser.add_argument("--sucursal", type=str, default="", help="Filtrar por alias")

    def handle(self, *args, **opts):
        fd = opts["fecha_desde"]
        fh = opts["fecha_hasta"]
        suc_filter = opts["sucursal"].strip()

        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
            database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            connection_timeout=300, autocommit=True,
        )

        suc_map = {s.direccion: s for s in Sucursal.objects.all() if s.direccion}

        try:
            self._stock_report(conn, suc_filter)
            if fd and fh:
                self._ventas_report(conn, suc_map, fd, fh, suc_filter)
            else:
                self._ventas_report(conn, suc_map, "2026-04-01", "2026-04-30", suc_filter)
            self._fantasmas_report(conn, suc_filter)
        finally:
            conn.close()

    # ================================================================
    # STOCK
    # ================================================================
    def _stock_report(self, conn, suc_filter):
        self.stdout.write("\n" + "=" * 130)
        self.stdout.write("STOCK Y PRODUCTOS (MySQL vs PostgreSQL)")
        self.stdout.write("=" * 130)

        cursor = conn.cursor(dictionary=True, buffered=True)
        where = f"AND alias = '{suc_filter}'" if suc_filter else ""
        cursor.execute(f"""
            SELECT alias, COUNT(*) AS skus, SUM(stock) AS stock,
                   SUM(stock * costo) AS val_costo,
                   SUM(stock * precioventapublico) AS val_venta
            FROM talla
            WHERE codigo_asociado IS NOT NULL {where}
            GROUP BY alias ORDER BY alias
        """)
        mysql_data = {r['alias']: r for r in cursor}
        cursor.close()

        self.stdout.write(
            f"\n{'Sucursal':<14} {'MySQL Stk':>10} {'PG Stk':>10} {'DiffStk':>8}  "
            f"{'MySQL Costo':>15} {'PG Costo':>15} {'Diff':>12}"
        )
        self.stdout.write("-" * 100)

        t_ms, t_ps, t_mc, t_pc = 0, 0, 0, 0

        for alias in sorted(mysql_data.keys()):
            if suc_filter and alias != suc_filter:
                continue
            m = mysql_data[alias]
            suc = Sucursal.objects.filter(alias=alias).first()
            if not suc:
                continue

            qs = Producto_Talla.objects.filter(producto__sucursal=suc)
            pg_stk = int(qs.aggregate(t=Sum('stock'))['t'] or 0)
            pg_costo = int(qs.aggregate(
                t=Sum(F('stock') * F('producto__costo'))
            )['t'] or 0)

            ms_stk = int(m['stock'] or 0)
            ms_costo = int(m['val_costo'] or 0)

            t_ms += ms_stk
            t_ps += pg_stk
            t_mc += ms_costo
            t_pc += pg_costo

            mark = "OK" if ms_stk == pg_stk else "!"
            self.stdout.write(
                f"{alias:<14} {ms_stk:>10,} {pg_stk:>10,} {ms_stk - pg_stk:>+7,} {mark:<2}  "
                f"${ms_costo:>14,} ${pg_costo:>14,} ${ms_costo - pg_costo:>+11,}"
            )

        self.stdout.write("-" * 100)
        self.stdout.write(
            f"{'TOTAL':<14} {t_ms:>10,} {t_ps:>10,} {t_ms - t_ps:>+7,}     "
            f"${t_mc:>14,} ${t_pc:>14,} ${t_mc - t_pc:>+11,}"
        )
        stock_pct = (abs(t_ms - t_ps) / t_ms * 100) if t_ms else 0
        costo_pct = (abs(t_mc - t_pc) / t_mc * 100) if t_mc else 0
        self.stdout.write(f"  Stock diff: {stock_pct:.3f}%  |  Costo diff: {costo_pct:.3f}%")

    # ================================================================
    # VENTAS
    # ================================================================
    def _ventas_report(self, conn, suc_map, fd, fh, suc_filter):
        self.stdout.write("\n" + "=" * 130)
        self.stdout.write(f"VENTAS DEL PERIODO: {fd} al {fh}")
        self.stdout.write("=" * 130)

        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT sucursal, COUNT(DISTINCT n_documento) as docs,
                   SUM(monto_pagado) as pagado
            FROM ventas
            WHERE DATE(fecha) BETWEEN %s AND %s
            GROUP BY sucursal
        """, (fd, fh))
        mysql_ventas = {}
        for r in cursor:
            suc = suc_map.get(r['sucursal'])
            if suc:
                mysql_ventas[suc.alias] = {
                    'docs': r['docs'], 'pagado': int(r['pagado'] or 0)
                }
        cursor.close()

        self.stdout.write(
            f"\n{'Sucursal':<14} {'MySQL docs':>10} {'PG docs':>10}  "
            f"{'MySQL Pagado':>15} {'PG Pagos':>15} {'Diff':>13}"
        )
        self.stdout.write("-" * 90)

        tm_d, tp_d, tm_p, tp_p = 0, 0, 0, 0

        for alias in sorted(mysql_ventas.keys()):
            if suc_filter and alias != suc_filter:
                continue
            m = mysql_ventas[alias]
            suc = Sucursal.objects.filter(alias=alias).first()
            if not suc:
                continue

            dtes = Dte.objects.filter(
                sucursal=suc, fecha_emision__gte=fd, fecha_emision__lte=fh,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
            )
            pg_docs = dtes.count()
            pg_pagos = int(
                Dte_Detalle_Pago.objects.filter(dte__in=dtes)
                .aggregate(t=Sum('monto'))['t'] or 0
            )

            tm_d += m['docs']; tp_d += pg_docs
            tm_p += m['pagado']; tp_p += pg_pagos
            diff = m['pagado'] - pg_pagos
            mark = "OK" if abs(diff) < 100 else "!"
            self.stdout.write(
                f"{alias:<14} {m['docs']:>10,} {pg_docs:>10,}  "
                f"${m['pagado']:>14,} ${pg_pagos:>14,} ${diff:>+12,} {mark}"
            )

        self.stdout.write("-" * 90)
        self.stdout.write(
            f"{'TOTAL':<14} {tm_d:>10,} {tp_d:>10,}  "
            f"${tm_p:>14,} ${tp_p:>14,} ${tm_p - tp_p:>+12,}"
        )

    # ================================================================
    # DTEs FANTASMA
    # ================================================================
    def _fantasmas_report(self, conn, suc_filter):
        self.stdout.write("\n" + "=" * 130)
        self.stdout.write("DTEs FANTASMA (en PG pero no en MySQL)")
        self.stdout.write("=" * 130)

        TIPO_MAP = {
            'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
            'DESPACHO ELECTRONICO': 'GUIA',
            'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
            'BOLETA': 'BOLETA PAPEL',
            'NOTA DE CREDITO': 'NOTA DE CREDITO',
            'FACTURA EXENTA': 'FACTURA EXENTA',
            'GUIA': 'GUIA',
        }

        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("SELECT n_documento, tipo_documento, bodega_inicio FROM dte")
        mysql_set = set()
        mysql_sin_bodega = set()
        for r in cursor:
            t = TIPO_MAP.get((r['tipo_documento'] or '').upper().strip(),
                             (r['tipo_documento'] or '').upper().strip())
            if r['bodega_inicio']:
                mysql_set.add((r['n_documento'], t, r['bodega_inicio']))
            mysql_sin_bodega.add((r['n_documento'], t))
        cursor.close()

        # Agregar desde ventas
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT DISTINCT n_documento, tipo_documento, sucursal
            FROM ventas WHERE n_documento > 0
        """)
        suc_dir_to_alias = {
            s.direccion: s.alias for s in Sucursal.objects.all() if s.direccion
        }
        for r in cursor:
            t = TIPO_MAP.get((r['tipo_documento'] or '').upper().strip(),
                             (r['tipo_documento'] or '').upper().strip())
            alias = suc_dir_to_alias.get(r['sucursal'])
            if alias:
                mysql_set.add((r['n_documento'], t, alias))
            mysql_sin_bodega.add((r['n_documento'], t))
        cursor.close()

        total_fantasma = 0
        total_monto = 0

        for suc in Sucursal.objects.all().order_by('alias'):
            if suc_filter and suc.alias != suc_filter:
                continue
            dtes = Dte.objects.filter(sucursal=suc).only(
                'id', 'numero_documento', 'tipo_documento', 'monto_con_iva'
            )
            fantasma_cnt = 0
            fantasma_monto = 0
            for d in dtes:
                key = (d.numero_documento, d.tipo_documento, suc.alias)
                if key in mysql_set:
                    continue
                if (d.numero_documento, d.tipo_documento) in mysql_sin_bodega:
                    continue
                fantasma_cnt += 1
                fantasma_monto += int(d.monto_con_iva or 0)

            total_fantasma += fantasma_cnt
            total_monto += fantasma_monto

            if fantasma_cnt > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {suc.alias:<14} {fantasma_cnt:,} fantasmas, ${fantasma_monto:,}"
                    )
                )

        if total_fantasma == 0:
            self.stdout.write(self.style.SUCCESS("  OK: 0 DTEs fantasma en PostgreSQL"))
        else:
            self.stdout.write(
                self.style.WARNING(f"\n  TOTAL: {total_fantasma:,} fantasmas, ${total_monto:,}")
            )
