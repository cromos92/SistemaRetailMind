"""
Diagnóstico detallado MySQL ventas vs PostgreSQL DTEs.

Compara los registros de la tabla MySQL `ventas` con los DTEs en PostgreSQL
para detectar por qué los totales y conteos difieren entre sistemas.

Uso:
  python manage.py diagnostico_ventas_dtes --sucursal PAO1 --fecha 2026-04-01
  python manage.py diagnostico_ventas_dtes --sucursal PAO1 --fecha-desde 2026-04-01 --fecha-hasta 2026-04-01
  python manage.py diagnostico_ventas_dtes --sucursal PAO1 --fecha 2026-04-01 --fix
"""

import os
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection

from app.models import Dte, Dte_Detalle_Pago, Sucursal, Empresa

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")


class Command(BaseCommand):
    help = "Compara ventas MySQL vs DTEs PostgreSQL para una sucursal y fecha."

    def add_arguments(self, parser):
        parser.add_argument("--sucursal", type=str, required=True, help="Alias de sucursal (ej: PAO1)")
        parser.add_argument("--fecha", type=str, default="", help="Fecha exacta YYYY-MM-DD")
        parser.add_argument("--fecha-desde", type=str, default="", help="Fecha inicio YYYY-MM-DD")
        parser.add_argument("--fecha-hasta", type=str, default="", help="Fecha fin YYYY-MM-DD")
        parser.add_argument("--fix", action="store_true", help="Crear DTEs faltantes en PostgreSQL")
        parser.add_argument("--max-detalle", type=int, default=20, help="Max filas de detalle")

    def handle(self, *args, **options):
        self.alias = options["sucursal"].strip()
        self.max_detalle = options["max_detalle"]
        self.fix_mode = options["fix"]

        if options["fecha"]:
            self.fecha_desde = options["fecha"]
            self.fecha_hasta = options["fecha"]
        else:
            self.fecha_desde = options["fecha_desde"]
            self.fecha_hasta = options["fecha_hasta"]

        if not self.fecha_desde or not self.fecha_hasta:
            self.stderr.write("Debe indicar --fecha o --fecha-desde + --fecha-hasta")
            return

        self.sucursal = Sucursal.objects.filter(alias=self.alias).first()
        if not self.sucursal:
            self.stderr.write(f"Sucursal '{self.alias}' no existe en PostgreSQL")
            return

        self.mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
            user=MYSQL_USER, password=MYSQL_PASSWORD,
            connection_timeout=300, autocommit=True, get_warnings=False,
        )

        try:
            self._paso1_ventas_mysql()
            self._paso2_dtes_mysql()
            self._paso3_cruce_ventas_dte_mysql()
            self._paso4_dtes_postgresql()
            self._paso5_comparacion()
            if self.fix_mode:
                self._paso6_fix()
        finally:
            self.mysql_conn.close()

    # ================================================================
    # PASO 1: Ventas en MySQL
    # ================================================================

    def _paso1_ventas_mysql(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(f"PASO 1: VENTAS MySQL — sucursal para '{self.alias}'")
        self.stdout.write("=" * 70)

        # En MySQL, ventas.sucursal puede ser la dirección o el alias
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)

        # Primero, identificar qué valor tiene ventas.sucursal para esta sucursal
        cursor.execute("""
            SELECT DISTINCT v.sucursal
            FROM ventas v
            WHERE (v.sucursal = %s OR v.sucursal = %s)
            LIMIT 5
        """, (self.alias, self.sucursal.direccion))
        sucursal_values = [r['sucursal'] for r in cursor]
        self.stdout.write(f"  Valores de ventas.sucursal que coinciden: {sucursal_values}")

        if not sucursal_values:
            self.stdout.write(self.style.WARNING("  [!] No se encontraron ventas para esta sucursal"))
            self.ventas_mysql = []
            return

        placeholders = ', '.join(['%s'] * len(sucursal_values))
        cursor.execute(f"""
            SELECT
                ID, n_documento, tipo_documento, metodo_pago, tarjeta,
                sub_total, descuento, monto_pagado, sucursal, fecha,
                voucher, hora, codigo_vendedor, nombre_vendedor,
                estado, ID_dte, correlativo_ticket
            FROM ventas
            WHERE sucursal IN ({placeholders})
              AND DATE(fecha) BETWEEN %s AND %s
            ORDER BY n_documento, ID
        """, (*sucursal_values, self.fecha_desde, self.fecha_hasta))

        self.ventas_mysql = cursor.fetchall()
        cursor.close()

        self.stdout.write(f"\n  Total registros ventas: {len(self.ventas_mysql)}")

        # Agrupar por n_documento (cada n_documento puede tener múltiples pagos)
        by_ndoc = defaultdict(list)
        for v in self.ventas_mysql:
            by_ndoc[v['n_documento']].append(v)

        self.ventas_by_ndoc = by_ndoc
        self.stdout.write(f"  Documentos únicos (n_documento): {len(by_ndoc)}")

        # Totales
        total_sub = sum(int(v['sub_total'] or 0) for v in self.ventas_mysql)
        total_desc = sum(int(v['descuento'] or 0) for v in self.ventas_mysql)
        total_pagado = sum(int(v['monto_pagado'] or 0) for v in self.ventas_mysql)
        self.stdout.write(f"  Total sub_total:    ${total_sub:,.0f}")
        self.stdout.write(f"  Total descuento:    ${total_desc:,.0f}")
        self.stdout.write(f"  Total monto_pagado: ${total_pagado:,.0f}")

        # Desglose por tipo_documento
        by_tipo = defaultdict(int)
        for v in self.ventas_mysql:
            by_tipo[v['tipo_documento'] or 'NULL'] += 1
        for tipo, cnt in sorted(by_tipo.items()):
            self.stdout.write(f"    {tipo}: {cnt}")

        # IDs de dte vinculados
        con_id_dte = [v for v in self.ventas_mysql if v['ID_dte']]
        sin_id_dte = [v for v in self.ventas_mysql if not v['ID_dte']]
        self.stdout.write(f"\n  Con ID_dte: {len(con_id_dte)}")
        self.stdout.write(f"  Sin ID_dte: {len(sin_id_dte)}")

        # Mostrar detalle
        self.stdout.write(f"\n  Detalle (primeros {self.max_detalle}):")
        for i, (ndoc, rows) in enumerate(sorted(by_ndoc.items())):
            if i >= self.max_detalle:
                self.stdout.write(f"    ... y {len(by_ndoc) - self.max_detalle} documentos más")
                break
            first = rows[0]
            total_doc = sum(int(r['monto_pagado'] or 0) for r in rows)
            desc_doc = max(int(r['descuento'] or 0) for r in rows)
            metodos = ', '.join(set(f"{r['metodo_pago']}" for r in rows))
            self.stdout.write(
                f"    NºDoc={ndoc:>8}  ID_dte={first['ID_dte'] or 'NULL':>8}  "
                f"Total=${total_doc:>10,}  Desc=${desc_doc:>6,}  "
                f"Fecha={first['fecha']}  Vend={first['codigo_vendedor']}  "
                f"Pago={metodos}"
            )

    # ================================================================
    # PASO 2: DTEs en MySQL
    # ================================================================

    def _paso2_dtes_mysql(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(f"PASO 2: DTEs MySQL — bodega_inicio='{self.alias}'")
        self.stdout.write("=" * 70)

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT
                ID, n_documento, tipo_documento, monto_total, neto, descuento,
                fecha_emision, bodega_inicio, bodega_destino, rut_emisor,
                rut_cliente, vendedor, estado, forma_pago
            FROM dte
            WHERE bodega_inicio = %s
              AND DATE(fecha_emision) BETWEEN %s AND %s
            ORDER BY n_documento
        """, (self.alias, self.fecha_desde, self.fecha_hasta))

        self.dtes_mysql = cursor.fetchall()
        cursor.close()

        self.stdout.write(f"\n  Total DTEs en MySQL para {self.alias}: {len(self.dtes_mysql)}")

        if self.dtes_mysql:
            total_monto = sum(int(d['monto_total'] or 0) for d in self.dtes_mysql)
            total_desc = sum(int(d['descuento'] or 0) for d in self.dtes_mysql)
            self.stdout.write(f"  Total monto_total: ${total_monto:,.0f}")
            self.stdout.write(f"  Total descuento:   ${total_desc:,.0f}")

            # Index by ID for cross-reference
            self.dtes_mysql_by_id = {d['ID']: d for d in self.dtes_mysql}

            self.stdout.write(f"\n  Detalle (primeros {self.max_detalle}):")
            for i, d in enumerate(self.dtes_mysql):
                if i >= self.max_detalle:
                    self.stdout.write(f"    ... y {len(self.dtes_mysql) - self.max_detalle} más")
                    break
                self.stdout.write(
                    f"    ID={d['ID']:>8}  NºDoc={d['n_documento']:>8}  "
                    f"Tipo={d['tipo_documento']:<25}  Total=${int(d['monto_total'] or 0):>10,}  "
                    f"Desc=${int(d['descuento'] or 0):>6,}  Fecha={d['fecha_emision']}  "
                    f"Estado={d['estado']}"
                )
        else:
            self.dtes_mysql_by_id = {}

        # También buscar DTEs por ID_dte de las ventas
        id_dtes_from_ventas = set()
        for v in self.ventas_mysql:
            if v['ID_dte']:
                id_dtes_from_ventas.add(v['ID_dte'])

        if id_dtes_from_ventas:
            ids_not_in_date = id_dtes_from_ventas - set(self.dtes_mysql_by_id.keys())
            if ids_not_in_date:
                self.stdout.write(f"\n  [!] {len(ids_not_in_date)} ID_dte de ventas NO están en DTEs del rango de fecha/sucursal")
                cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
                placeholders = ', '.join(['%s'] * len(ids_not_in_date))
                cursor.execute(f"""
                    SELECT ID, n_documento, tipo_documento, monto_total, fecha_emision,
                           bodega_inicio, bodega_destino, estado
                    FROM dte
                    WHERE ID IN ({placeholders})
                """, tuple(ids_not_in_date))
                dtes_extra = cursor.fetchall()
                cursor.close()

                self.dtes_mysql_extra = dtes_extra
                for d in dtes_extra[:self.max_detalle]:
                    self.stdout.write(
                        f"    DTE ID={d['ID']:>8}  NºDoc={d['n_documento']:>8}  "
                        f"bodega={d['bodega_inicio']}  Fecha={d['fecha_emision']}  "
                        f"Total=${int(d['monto_total'] or 0):>10,}  Estado={d['estado']}"
                    )
                # Add to the index
                for d in dtes_extra:
                    self.dtes_mysql_by_id[d['ID']] = d
            else:
                self.stdout.write(f"\n  [OK] Todos los ID_dte de ventas coinciden con DTEs en rango")
                self.dtes_mysql_extra = []
        else:
            self.dtes_mysql_extra = []

    # ================================================================
    # PASO 3: Cruce ventas → dte en MySQL
    # ================================================================

    def _paso3_cruce_ventas_dte_mysql(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("PASO 3: CRUCE ventas.n_documento vs dte.n_documento en MySQL")
        self.stdout.write("=" * 70)

        mismos_ndoc = 0
        diferentes_ndoc = 0
        sin_dte = 0
        mapping = {}  # ventas.n_documento → dte.n_documento

        for ndoc, rows in self.ventas_by_ndoc.items():
            first = rows[0]
            id_dte = first['ID_dte']

            if not id_dte:
                sin_dte += 1
                continue

            dte_row = self.dtes_mysql_by_id.get(id_dte)
            if not dte_row:
                sin_dte += 1
                continue

            dte_ndoc = dte_row['n_documento']
            mapping[ndoc] = dte_ndoc

            if ndoc == dte_ndoc:
                mismos_ndoc += 1
            else:
                diferentes_ndoc += 1

        self.ventas_to_dte_ndoc = mapping

        self.stdout.write(f"\n  Documentos con ventas.n_documento == dte.n_documento: {mismos_ndoc}")
        self.stdout.write(f"  Documentos con ventas.n_documento != dte.n_documento: {diferentes_ndoc}")
        self.stdout.write(f"  Ventas sin DTE vinculado: {sin_dte}")

        if diferentes_ndoc > 0:
            self.stdout.write(self.style.WARNING(
                f"\n  [!] ALERTA: {diferentes_ndoc} documentos tienen diferente n_documento "
                f"entre ventas y dte. Esto causa que crear_dtes_faltantes no funcione correctamente."
            ))
            self.stdout.write(f"\n  Ejemplos (primeros {min(10, diferentes_ndoc)}):")
            cnt = 0
            for vndoc, dndoc in mapping.items():
                if vndoc != dndoc:
                    first_venta = self.ventas_by_ndoc[vndoc][0]
                    self.stdout.write(
                        f"    ventas.n_doc={vndoc:>8} → dte.n_doc={dndoc:>8}  "
                        f"(ID_dte={first_venta['ID_dte']})"
                    )
                    cnt += 1
                    if cnt >= 10:
                        break

    # ================================================================
    # PASO 4: DTEs en PostgreSQL
    # ================================================================

    def _paso4_dtes_postgresql(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(f"PASO 4: DTEs PostgreSQL — sucursal '{self.alias}'")
        self.stdout.write("=" * 70)

        self.dtes_pg = list(
            Dte.objects.filter(
                sucursal=self.sucursal,
                fecha_emision__gte=self.fecha_desde,
                fecha_emision__lte=self.fecha_hasta,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            ).select_related('vendedor', 'receptor')
            .order_by('numero_documento')
        )

        self.stdout.write(f"\n  Total DTEs en PostgreSQL: {len(self.dtes_pg)}")

        if self.dtes_pg:
            total_monto = sum(int(d.monto_con_iva or 0) for d in self.dtes_pg)
            total_desc = sum(int(d.descuento or 0) for d in self.dtes_pg)
            self.stdout.write(f"  Total monto_con_iva: ${total_monto:,.0f}")
            self.stdout.write(f"  Total descuento:     ${total_desc:,.0f}")

            # Check payments
            pg_ids = [d.id for d in self.dtes_pg]
            pagos = Dte_Detalle_Pago.objects.filter(dte_id__in=pg_ids)
            total_pagos = sum(int(p.monto or 0) for p in pagos)
            self.stdout.write(f"  Total pagos (Dte_Detalle_Pago): ${total_pagos:,.0f}")

            self.stdout.write(f"\n  Detalle:")
            for d in self.dtes_pg[:self.max_detalle]:
                pagos_dte = Dte_Detalle_Pago.objects.filter(dte_id=d.id)
                total_pago = sum(int(p.monto or 0) for p in pagos_dte)
                metodos = ', '.join(set(p.metodo_pago for p in pagos_dte)) or 'SIN PAGOS'
                self.stdout.write(
                    f"    Folio={d.numero_documento:>8}  tipo={d.tipo_documento:<22}  "
                    f"monto=${int(d.monto_con_iva or 0):>10,}  desc=${int(d.descuento or 0):>6,}  "
                    f"pagos=${total_pago:>10,}  pago={metodos}  "
                    f"vend={d.vendedor.codigo_vendedor if d.vendedor else 'N/A'}"
                )

        # Also show DTEs with NULL sucursal or different sucursal but matching folios
        folios_mysql = set()
        for d in self.dtes_mysql:
            folios_mysql.add(d['n_documento'])
        for v in self.ventas_mysql:
            if v['ID_dte'] and v['ID_dte'] in self.dtes_mysql_by_id:
                folios_mysql.add(self.dtes_mysql_by_id[v['ID_dte']]['n_documento'])

        if folios_mysql:
            otros = Dte.objects.filter(
                numero_documento__in=folios_mysql,
                fecha_emision__gte=self.fecha_desde,
                fecha_emision__lte=self.fecha_hasta,
            ).exclude(
                sucursal=self.sucursal,
            ).select_related('sucursal')

            if otros.exists():
                self.stdout.write(f"\n  [!] DTEs con folios de MySQL pero en OTRA sucursal o sin sucursal:")
                for d in otros[:self.max_detalle]:
                    suc_alias = d.sucursal.alias if d.sucursal else 'NULL'
                    self.stdout.write(
                        f"    Folio={d.numero_documento:>8}  sucursal={suc_alias}  "
                        f"monto=${int(d.monto_con_iva or 0):>10,}"
                    )

    # ================================================================
    # PASO 5: Comparación
    # ================================================================

    def _paso5_comparacion(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("PASO 5: COMPARACIÓN Y DIAGNÓSTICO")
        self.stdout.write("=" * 70)

        # Folio numbers in PostgreSQL
        pg_folios = {d.numero_documento for d in self.dtes_pg}
        # DTE folio numbers from MySQL dte table
        mysql_dte_folios = {d['n_documento'] for d in self.dtes_mysql}
        # All DTE folios referenced by ventas (via ID_dte mapping)
        mysql_dte_folios_from_ventas = set(self.ventas_to_dte_ndoc.values())
        # Ventas n_documento
        ventas_ndocs = set(self.ventas_by_ndoc.keys())

        all_dte_folios_mysql = mysql_dte_folios | mysql_dte_folios_from_ventas

        self.stdout.write(f"\n  Resumen de folios:")
        self.stdout.write(f"    ventas.n_documento únicos:     {len(ventas_ndocs)}")
        self.stdout.write(f"    dte.n_documento en MySQL:       {len(mysql_dte_folios)}")
        self.stdout.write(f"    dte.n_doc vía ventas.ID_dte:    {len(mysql_dte_folios_from_ventas)}")
        self.stdout.write(f"    DTEs en PostgreSQL:             {len(pg_folios)}")

        # Check which MySQL DTE folios exist in PostgreSQL
        en_pg = all_dte_folios_mysql & pg_folios
        faltan_en_pg = all_dte_folios_mysql - pg_folios
        solo_pg = pg_folios - all_dte_folios_mysql

        self.stdout.write(f"\n  MySQL DTE folios -> PostgreSQL:")
        self.stdout.write(f"    En ambos:        {len(en_pg)}")
        self.stdout.write(self.style.WARNING(f"    Faltan en PG:    {len(faltan_en_pg)}"))
        self.stdout.write(f"    Solo en PG:      {len(solo_pg)}")

        if faltan_en_pg:
            self.stdout.write(f"\n  Folios que faltan en PostgreSQL: {sorted(faltan_en_pg)[:20]}")

        if solo_pg:
            self.stdout.write(f"\n  Folios solo en PostgreSQL (no en MySQL para esta sucursal): {sorted(solo_pg)[:20]}")

        # Ventas sin DTE en ningún sistema
        ventas_sin_dte_mysql = set()
        for ndoc, rows in self.ventas_by_ndoc.items():
            if not rows[0]['ID_dte']:
                ventas_sin_dte_mysql.add(ndoc)
        if ventas_sin_dte_mysql:
            self.stdout.write(self.style.WARNING(
                f"\n  ventas.n_documento SIN ID_dte (sin DTE en MySQL): {len(ventas_sin_dte_mysql)}"
            ))
            self.stdout.write(f"    Ejemplos: {sorted(ventas_sin_dte_mysql)[:10]}")

        # TOTAL comparison
        self.stdout.write("\n" + "-" * 50)
        self.stdout.write("COMPARACIÓN DE TOTALES")
        self.stdout.write("-" * 50)

        # MySQL: sum monto_pagado from ventas
        mysql_total_pagado = sum(int(v['monto_pagado'] or 0) for v in self.ventas_mysql)
        mysql_total_sub = sum(int(v['sub_total'] or 0) for v in self.ventas_mysql)
        mysql_total_desc = sum(int(v['descuento'] or 0) for v in self.ventas_mysql)

        # PostgreSQL: what gestionVentasDocumentos calculates
        pg_total = 0
        for d in self.dtes_pg:
            pagos = Dte_Detalle_Pago.objects.filter(dte_id=d.id)
            total_pagos = sum(int(p.monto or 0) for p in pagos)
            monto_lista = int(d.monto_con_iva or 0)
            total_real = total_pagos if total_pagos > 0 else monto_lista
            pg_total += total_real

        self.stdout.write(f"\n  MySQL ventas total monto_pagado: ${mysql_total_pagado:,.0f}")
        self.stdout.write(f"  MySQL ventas total sub_total:    ${mysql_total_sub:,.0f}")
        self.stdout.write(f"  MySQL ventas total descuento:    ${mysql_total_desc:,.0f}")
        self.stdout.write(f"  PostgreSQL total (como lo ve la UI): ${pg_total:,.0f}")
        self.stdout.write(f"\n  Diferencia (MySQL pagado - PG): ${mysql_total_pagado - pg_total:,.0f}")

        # Guard 3 analysis
        self.stdout.write("\n" + "-" * 50)
        self.stdout.write("ANÁLISIS Guard 3 de crear_dtes_faltantes")
        self.stdout.write("-" * 50)

        max_folio_pg = 0
        for d in Dte.objects.filter(
            sucursal=self.sucursal,
            tipo_documento__in=['BOLETA ELECTRONICA', 'BOLETA PAPEL']
        ).values_list('numero_documento', flat=True):
            if d and d > max_folio_pg:
                max_folio_pg = d

        self.stdout.write(f"  Max folio BOLETA en PG para {self.alias}: {max_folio_pg}")
        if ventas_ndocs:
            max_venta_ndoc = max(ventas_ndocs)
            min_venta_ndoc = min(ventas_ndocs)
            self.stdout.write(f"  Rango ventas.n_documento: {min_venta_ndoc} - {max_venta_ndoc}")

            bloqueados_guard3 = sum(1 for n in ventas_ndocs if n < max_folio_pg)
            self.stdout.write(f"  Documentos que Guard 3 bloquearía: {bloqueados_guard3} de {len(ventas_ndocs)}")

            if bloqueados_guard3 > 0 and max_folio_pg > max_venta_ndoc:
                self.stdout.write(self.style.ERROR(
                    f"\n  [BUG] Guard 3 bloquea TODOS los documentos porque "
                    f"max_folio_pg ({max_folio_pg}) > max ventas.n_doc ({max_venta_ndoc}).\n"
                    f"  Esto ocurre porque ventas.n_documento y dte.n_documento usan "
                    f"numeraciones diferentes."
                ))

    # ================================================================
    # PASO 6: Fix
    # ================================================================

    def _paso6_fix(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("PASO 6: CREANDO DTEs FALTANTES")
        self.stdout.write("=" * 70)

        from app.models import Vendedor

        # Build set of existing folio numbers in PG
        pg_folios = {d.numero_documento for d in self.dtes_pg}
        pg_all_folios = set(
            Dte.objects.filter(sucursal=self.sucursal)
            .values_list('numero_documento', flat=True)
        )

        # Emisor from sucursal's empresa
        emisor = self.sucursal.empresa

        # Cache vendedores by codigo_vendedor
        cache_vendedores = {}
        for v in Vendedor.objects.all():
            if v.codigo_vendedor:
                cache_vendedores[str(v.codigo_vendedor)] = v

        TIPO_DOC_MAP = {
            'Factura Electronica': 'FACTURA ELECTRONICA',
            'Boleta Electronica': 'BOLETA ELECTRONICA',
            'Boleta': 'BOLETA PAPEL',
            'Nota de Credito': 'NOTA DE CREDITO',
        }

        created = 0
        skipped = 0

        for ndoc, rows in self.ventas_by_ndoc.items():
            first = rows[0]

            # Determine the correct DTE folio number
            dte_folio = self.ventas_to_dte_ndoc.get(ndoc)
            if not dte_folio:
                # No DTE in MySQL either; use ventas.n_documento as fallback
                dte_folio = ndoc

            # Skip if already exists in PG
            if dte_folio in pg_all_folios:
                skipped += 1
                continue

            tipo_mysql = first['tipo_documento'] or 'Boleta Electronica'
            tipo_pg = TIPO_DOC_MAP.get(tipo_mysql, 'BOLETA ELECTRONICA')

            # Calculate totals from all payment rows for this document
            total_pagado = sum(int(r['monto_pagado'] or 0) for r in rows)
            total_sub = max(int(r['sub_total'] or 0) for r in rows)
            descuento = max(int(r['descuento'] or 0) for r in rows)

            monto_con_iva = total_sub if total_sub > 0 else total_pagado

            if 'NOTA' in tipo_pg:
                tipo_transaccion = 'NOTA_CREDITO'
            elif 'BOLETA' in tipo_pg:
                tipo_transaccion = 'VENTA_PUBLICO'
            else:
                tipo_transaccion = 'VENTA'

            vendedor = None
            cod = first.get('codigo_vendedor')
            if cod:
                vendedor = cache_vendedores.get(str(cod))

            dte = Dte(
                numero_documento=dte_folio,
                tipo_documento=tipo_pg,
                tipo_transaccion=tipo_transaccion,
                monto_neto=int(monto_con_iva / 1.19),
                monto_con_iva=monto_con_iva,
                descuento=descuento,
                fecha_emision=first['fecha'],
                fecha_vencimiento=first['fecha'],
                sucursal=self.sucursal,
                vendedor=vendedor,
                emisor=emisor,
                estado_dte='EMITIDO',
                estado_pago='PAGADO',
                bultos=0,
                unidades_productos=0,
                diasCredito=0,
            )
            dte.save()
            pg_all_folios.add(dte_folio)

            # Create payment records
            metodo_pago_map = {
                'Efectivo': 'EFECTIVO',
                'Tarjeta TBK': 'TBK_MANUAL',
                'Tarjeta TBK Pos Integrado': 'TBK_POS_INTEGRADO',
                'Tarjeta Comercial': 'TARJETA_COMERCIAL',
                'Convenio': 'CONVENIO',
                'Credito': 'CREDITO_EXTERNO',
                'Credito Trabajador': 'CREDITO_TRABAJADOR',
                'Orden Compra': 'ORDEN_COMPRA',
                'Transferencia': 'TRANSFERENCIA',
                'Venta Internet': 'VENTA_INTERNET',
            }
            tarjeta_metodo_map = {
                'REDCOMPRA DEBITO': 'TBK_DEBITO_POS',
                'VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
                ' VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
                'Mercado Pago': 'VENTA_INTERNET',
                'Ripley': 'VENTA_INTERNET',
                'Paris': 'VENTA_INTERNET',
                'Falabella': 'VENTA_INTERNET',
            }

            for r in rows:
                metodo = metodo_pago_map.get(r['metodo_pago'] or '', 'EFECTIVO')
                tarjeta = (r['tarjeta'] or '').strip()
                if tarjeta in tarjeta_metodo_map:
                    metodo = tarjeta_metodo_map[tarjeta]

                Dte_Detalle_Pago.objects.create(
                    dte=dte,
                    metodo_pago=metodo,
                    tipo_tarjeta=r['tarjeta'] or None,
                    voucher=str(r['voucher']) if r['voucher'] else f"MIG-{r['ID']}",
                    monto=int(r['monto_pagado'] or 0),
                )

            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n  ✓ {created} DTEs creados, {skipped} ya existían"
        ))
