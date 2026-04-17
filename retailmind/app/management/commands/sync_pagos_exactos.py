"""
Sincronizacion EXACTA de pagos: MySQL manda siempre.

Para cada DTE en PG, los pagos deben coincidir EXACTAMENTE con lo que
MySQL tiene para ese (n_documento, sucursal). Si hay diferencia, se
borran los pagos del DTE y se recrean con los datos exactos de MySQL.

Uso:
    python manage.py sync_pagos_exactos              # Todos los anios
    python manage.py sync_pagos_exactos --fecha-desde 2024-01-01
    python manage.py sync_pagos_exactos --dry-run    # Solo simular
    python manage.py sync_pagos_exactos --solo-overpaid  # Solo corregir DTEs inflados
"""
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db.models import Sum

from app.models import Dte, Dte_Detalle_Pago, Sucursal


METODO_PAGO_MAP = {
    'Efectivo': 'EFECTIVO',
    'Tarjeta TBK': 'TBK_MANUAL',
    'Tarjeta TBK Pos Integrado': 'TBK_POS_INTEGRADO',
    'Tarjeta Comercial': 'TARJETA_COMERCIAL',
    'Convenio': 'CONVENIO',
    'Credito': 'CREDITO_EXTERNO',
    'Credito Trabajador': 'CREDITO_TRABAJADOR',
    'Credito Orden Compra': 'ORDEN_COMPRA',
    'Orden Compra': 'ORDEN_COMPRA',
    'Transferencia': 'TRANSFERENCIA',
    'Venta Internet': 'VENTA_INTERNET',
    'Nota de Credito': 'EFECTIVO',
}

TARJETA_METODO_MAP = {
    'REDCOMPRA DEBITO': 'TBK_DEBITO_POS',
    'VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
    ' VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
    'Tarjeta TBK': 'TBK_CREDITO_POS',
    'HITES': 'TARJETA_COMERCIAL',
    'RIPLEY': 'TARJETA_COMERCIAL',
    'ABCDIN': 'TARJETA_COMERCIAL',
    'PRESTO': 'TARJETA_COMERCIAL',
    'TRICOT': 'TARJETA_COMERCIAL',
    'Mercado Pago': 'VENTA_INTERNET',
    'Mercado Libre': 'VENTA_INTERNET',
    'Paris': 'VENTA_INTERNET',
    'Falabella': 'VENTA_INTERNET',
    'Shopify': 'VENTA_INTERNET',
    'Credito': 'CREDITO_EXTERNO',
    'Web Pay': 'TBK_CREDITO_POS',
    'Transferencia': 'TRANSFERENCIA',
}


def mapear_metodo(row):
    metodo = METODO_PAGO_MAP.get(row['metodo_pago'] or '', 'EFECTIVO')
    tarjeta = (row['tarjeta'] or '').strip()
    if tarjeta in TARJETA_METODO_MAP:
        return TARJETA_METODO_MAP[tarjeta]
    if tarjeta.startswith('OrdenCompra'):
        return 'ORDEN_COMPRA'
    return metodo


class Command(BaseCommand):
    help = "Sincroniza pagos MySQL -> PG EXACTAMENTE. Corrige overpayments."

    def add_arguments(self, parser):
        parser.add_argument("--fecha-desde", type=str, default="2024-01-01",
                            help="Desde que fecha procesar (default: 2024-01-01)")
        parser.add_argument("--dry-run", action="store_true", help="Solo simular")
        parser.add_argument("--solo-overpaid", action="store_true",
                            help="Solo corregir DTEs con pagos > monto")
        parser.add_argument("--sucursal", type=str, default="",
                            help="Filtrar una sucursal")
        parser.add_argument("--match-mode", type=str, default="mes",
                            choices=["mes", "anio", "flexible"],
                            help="Match: mes (estricto), anio, flexible (n_doc+sucursal sin fecha)")

    def handle(self, *args, **opts):
        self.dry_run = opts["dry_run"]
        solo_overpaid = opts["solo_overpaid"]
        fd = opts["fecha_desde"]
        suc_filter = opts["sucursal"].strip()
        match_mode = opts["match_mode"]

        if self.dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN ==="))

        self.mysql_conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
            database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            connection_timeout=300, autocommit=True,
        )

        try:
            # ============================================================
            # PASO 1: Cargar ventas de MySQL agrupadas por (n_doc, sucursal, fecha)
            # ============================================================
            self.stdout.write(f"\n[1/4] Cargando ventas MySQL desde {fd}...")

            suc_dir_to_id = {
                s.direccion: s.id for s in Sucursal.objects.all() if s.direccion
            }

            cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
            where_suc = ""
            if suc_filter:
                suc = Sucursal.objects.filter(alias=suc_filter).first()
                if suc and suc.direccion:
                    where_suc = f" AND sucursal = '{suc.direccion}'"
            cursor.execute(f"""
                SELECT n_documento, sucursal, DATE(fecha) as dia, fecha,
                       metodo_pago, tarjeta, monto_pagado, voucher,
                       n_convenio, rut_convenio, nombre_vendedor, correlativo_ticket,
                       ID
                FROM ventas
                WHERE fecha >= %s AND n_documento > 0 {where_suc}
                ORDER BY n_documento, fecha
            """, (fd,))

            # Agrupar por (n_documento, sucursal_id, <key_fecha>)
            ventas_por_key = defaultdict(list)
            for r in cursor:
                suc_id = suc_dir_to_id.get(r['sucursal'])
                if not suc_id:
                    continue
                if match_mode == "flexible":
                    key_fecha = "ALL"
                elif match_mode == "anio":
                    key_fecha = r['dia'].strftime('%Y') if r['dia'] else '?'
                else:  # mes
                    key_fecha = r['dia'].strftime('%Y-%m') if r['dia'] else '?'
                key = (r['n_documento'], suc_id, key_fecha)
                ventas_por_key[key].append(r)
            cursor.close()

            self.stdout.write(f"  {len(ventas_por_key):,} grupos (match_mode={match_mode})")

            # ============================================================
            # PASO 2: Para cada DTE en PG, buscar sus ventas en MySQL
            # ============================================================
            self.stdout.write("\n[2/4] Comparando DTEs en PG con ventas MySQL...")

            dtes_qs = Dte.objects.filter(
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                fecha_emision__gte=fd,
                sucursal__isnull=False,
            )
            if suc_filter:
                dtes_qs = dtes_qs.filter(sucursal__alias=suc_filter)

            total_dtes = dtes_qs.count()
            self.stdout.write(f"  {total_dtes:,} DTEs a revisar")

            a_corregir = []
            sin_venta_mysql = 0
            ok_count = 0

            for i, dte in enumerate(dtes_qs.only(
                'id', 'numero_documento', 'sucursal_id', 'fecha_emision', 'monto_con_iva'
            ).iterator()):
                if i and i % 20000 == 0:
                    self.stdout.write(f"  Procesado {i:,}/{total_dtes:,}...")

                if match_mode == "flexible":
                    key_fecha = "ALL"
                elif match_mode == "anio":
                    key_fecha = dte.fecha_emision.strftime('%Y') if dte.fecha_emision else '?'
                else:
                    key_fecha = dte.fecha_emision.strftime('%Y-%m') if dte.fecha_emision else '?'
                key = (dte.numero_documento, dte.sucursal_id, key_fecha)
                ventas = ventas_por_key.get(key, [])

                if not ventas:
                    sin_venta_mysql += 1
                    continue

                mysql_total = sum(int(v['monto_pagado'] or 0) for v in ventas)
                pg_total = int(
                    Dte_Detalle_Pago.objects.filter(dte=dte)
                    .aggregate(t=Sum('monto'))['t'] or 0
                )

                if solo_overpaid:
                    # Solo procesar si PG > MySQL + 10%
                    monto_dte = int(dte.monto_con_iva or 0)
                    if pg_total <= monto_dte * 1.1:
                        if pg_total == mysql_total:
                            ok_count += 1
                        continue

                if abs(pg_total - mysql_total) > 10:
                    a_corregir.append({
                        'dte_id': dte.id,
                        'pg': pg_total,
                        'mysql': mysql_total,
                        'diff': pg_total - mysql_total,
                        'ventas': ventas,
                    })
                else:
                    ok_count += 1

            self.stdout.write(f"  OK (pagos ya coinciden): {ok_count:,}")
            self.stdout.write(f"  A corregir (diff > 10): {len(a_corregir):,}")
            self.stdout.write(f"  Sin venta MySQL: {sin_venta_mysql:,}")

            # ============================================================
            # PASO 3: Corregir
            # ============================================================
            self.stdout.write("\n[3/4] Corrigiendo pagos...")

            corregidos = 0
            total_reduccion = 0
            total_aumento = 0

            for item in a_corregir:
                dte_id = item['dte_id']
                ventas = item['ventas']

                if item['diff'] > 0:
                    total_reduccion += item['diff']
                else:
                    total_aumento += abs(item['diff'])

                if self.dry_run:
                    corregidos += 1
                    continue

                # Borrar todos los pagos del DTE
                Dte_Detalle_Pago.objects.filter(dte_id=dte_id).delete()

                # Crear los pagos exactos desde MySQL
                batch = []
                for v in ventas:
                    metodo = mapear_metodo(v)
                    notas_partes = []
                    if v['n_convenio']:
                        notas_partes.append(f"Convenio: {v['n_convenio']}")
                    if v['rut_convenio']:
                        notas_partes.append(f"RUT Conv: {v['rut_convenio']}")
                    if v['nombre_vendedor']:
                        notas_partes.append(f"Vendedor: {v['nombre_vendedor']}")
                    if v['correlativo_ticket']:
                        notas_partes.append(f"Ticket: {v['correlativo_ticket']}")

                    batch.append(Dte_Detalle_Pago(
                        dte_id=dte_id,
                        metodo_pago=metodo,
                        tipo_tarjeta=v['tarjeta'] or None,
                        voucher=str(v['voucher']) if v['voucher'] else f"MIG-{v['ID']}",
                        monto=int(v['monto_pagado'] or 0),
                        notas=' | '.join(notas_partes) if notas_partes else None,
                    ))

                Dte_Detalle_Pago.objects.bulk_create(batch)
                corregidos += 1

                if corregidos % 1000 == 0:
                    self.stdout.write(f"  Corregidos {corregidos:,}...")

            self.stdout.write(f"\n  DTEs corregidos: {corregidos:,}")
            self.stdout.write(f"  Reduccion total (PG > MySQL): ${total_reduccion:,}")
            self.stdout.write(f"  Aumento total (PG < MySQL):   ${total_aumento:,}")
            self.stdout.write(f"  Diferencia neta aplicada:     ${total_reduccion - total_aumento:+,}")

            # ============================================================
            # PASO 4: Resumen final
            # ============================================================
            self.stdout.write("\n[4/4] Verificacion...")
            total_pg = int(
                Dte_Detalle_Pago.objects.filter(
                    dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                    dte__fecha_emision__gte=fd,
                ).aggregate(t=Sum('monto'))['t'] or 0
            )

            cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
            cursor.execute(
                "SELECT SUM(monto_pagado) as t FROM ventas WHERE fecha >= %s", (fd,)
            )
            total_mysql = int(cursor.fetchone()['t'] or 0)
            cursor.close()

            self.stdout.write(f"  MySQL total: ${total_mysql:,}")
            self.stdout.write(f"  PG total:    ${total_pg:,}")
            self.stdout.write(f"  Diferencia:  ${total_mysql - total_pg:+,} "
                              f"({((total_mysql - total_pg)/total_mysql*100) if total_mysql else 0:+.3f}%)")

        finally:
            self.mysql_conn.close()
