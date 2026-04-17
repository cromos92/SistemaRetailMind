"""
Recrea pagos desde cero con match preciso: (n_doc + sucursal + mes).

Para cada (n_documento, sucursal_id, mes) en MySQL ventas:
  - Encuentra el DTE correspondiente en PG
  - Crea los pagos exactos desde las ventas MySQL

Este comando asume que NO hay pagos en PG para el rango especificado
(es decir, primero se deben borrar).

Uso:
    python manage.py recrear_pagos_exactos --fecha-desde 2024-01-01
"""
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand

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
    help = "Re-crea pagos desde MySQL con match exacto (n_doc+sucursal+mes)"

    def add_arguments(self, parser):
        parser.add_argument("--fecha-desde", type=str, default="2024-01-01")

    def handle(self, *args, **opts):
        fd = opts["fecha_desde"]

        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
            database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            connection_timeout=300, autocommit=True,
        )

        try:
            # ============================================================
            # 1) Cargar DTEs de PG indexados por (n_doc, sucursal, mes)
            # ============================================================
            self.stdout.write("[1/3] Indexando DTEs PG por (n_doc, sucursal, mes)...")
            dte_index = {}  # key -> dte_id (primero que aparece en ese mes)
            dte_count = 0
            for d in Dte.objects.filter(
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                fecha_emision__gte=fd,
                sucursal__isnull=False,
            ).values('id', 'numero_documento', 'sucursal_id', 'fecha_emision'):
                mes = d['fecha_emision'].strftime('%Y-%m')
                key = (d['numero_documento'], d['sucursal_id'], mes)
                if key not in dte_index:  # tomar el primero
                    dte_index[key] = d['id']
                    dte_count += 1
            self.stdout.write(f"  {dte_count:,} DTEs unicos indexados")

            # ============================================================
            # 2) Cargar ventas de MySQL
            # ============================================================
            self.stdout.write("\n[2/3] Descargando ventas MySQL...")
            suc_dir_to_id = {
                s.direccion: s.id for s in Sucursal.objects.all() if s.direccion
            }

            cursor = conn.cursor(dictionary=True, buffered=True)
            cursor.execute("""
                SELECT n_documento, sucursal, fecha,
                       metodo_pago, tarjeta, monto_pagado, voucher,
                       n_convenio, rut_convenio, nombre_vendedor, correlativo_ticket,
                       ID
                FROM ventas
                WHERE fecha >= %s AND n_documento > 0
            """, (fd,))

            total_ventas = cursor.rowcount
            self.stdout.write(f"  {total_ventas:,} ventas MySQL")

            # ============================================================
            # 3) Crear pagos en batch
            # ============================================================
            self.stdout.write("\n[3/3] Creando pagos...")

            batch = []
            batch_size = 5000
            creados = 0
            sin_dte = 0

            for row in cursor:
                suc_id = suc_dir_to_id.get(row['sucursal'])
                if not suc_id or not row['fecha']:
                    sin_dte += 1
                    continue

                mes = row['fecha'].strftime('%Y-%m')
                key = (row['n_documento'], suc_id, mes)
                dte_id = dte_index.get(key)

                if not dte_id:
                    sin_dte += 1
                    continue

                metodo = mapear_metodo(row)
                notas_partes = []
                if row['n_convenio']:
                    notas_partes.append(f"Convenio: {row['n_convenio']}")
                if row['rut_convenio']:
                    notas_partes.append(f"RUT Conv: {row['rut_convenio']}")
                if row['nombre_vendedor']:
                    notas_partes.append(f"Vendedor: {row['nombre_vendedor']}")
                if row['correlativo_ticket']:
                    notas_partes.append(f"Ticket: {row['correlativo_ticket']}")

                batch.append(Dte_Detalle_Pago(
                    dte_id=dte_id,
                    metodo_pago=metodo,
                    tipo_tarjeta=row['tarjeta'] or None,
                    voucher=str(row['voucher']) if row['voucher'] else f"MIG-{row['ID']}",
                    monto=int(row['monto_pagado'] or 0),
                    notas=' | '.join(notas_partes) if notas_partes else None,
                ))

                if len(batch) >= batch_size:
                    Dte_Detalle_Pago.objects.bulk_create(batch)
                    creados += len(batch)
                    batch = []
                    if creados % 50000 == 0:
                        self.stdout.write(f"  Creados {creados:,}...")

            if batch:
                Dte_Detalle_Pago.objects.bulk_create(batch)
                creados += len(batch)

            cursor.close()

            self.stdout.write(f"\n  Pagos creados:  {creados:,}")
            self.stdout.write(f"  Sin DTE match:  {sin_dte:,}")

        finally:
            conn.close()
