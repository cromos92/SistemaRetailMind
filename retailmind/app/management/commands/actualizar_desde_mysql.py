"""
Actualizacion completa MySQL -> PostgreSQL

Este comando ejecuta en el orden correcto:
1. sync_productos_mysql    (stock, costos, precios, productos nuevos, eliminar huerfanos)
2. migrate_from_laravel    (DTEs, pagos, vendedores)
3. sync_pagos_exactos      (corrige pagos con match exacto n_doc+sucursal+mes)
4. limpiar_dtes_fantasma   (elimina DTEs en PG que no existen en MySQL)
5. verificacion_final      (muestra diferencias)

Uso:
    python manage.py actualizar_desde_mysql
    python manage.py actualizar_desde_mysql --skip-productos   # Solo DTEs
    python manage.py actualizar_desde_mysql --skip-dtes        # Solo productos
    python manage.py actualizar_desde_mysql --skip-verificacion
    python manage.py actualizar_desde_mysql --fecha-sync-pagos 2024-01-01
"""
import os
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management import call_command
from django.core.management.base import BaseCommand

from app.models import Dte, Dte_Detalle_Pago, Dte_Productos, Sucursal


class Command(BaseCommand):
    help = "Actualiza PostgreSQL desde MySQL: productos, stock, DTEs y ventas"

    def add_arguments(self, parser):
        parser.add_argument("--skip-productos", action="store_true",
                            help="No actualizar productos/stock")
        parser.add_argument("--skip-dtes", action="store_true",
                            help="No actualizar DTEs/ventas")
        parser.add_argument("--skip-sync-pagos", action="store_true",
                            help="No ejecutar sync_pagos_exactos")
        parser.add_argument("--skip-verificacion", action="store_true",
                            help="No ejecutar verificacion final")
        parser.add_argument("--fecha-desde", type=str, default="2026-04-01",
                            help="Fecha desde para verificacion (YYYY-MM-DD)")
        parser.add_argument("--fecha-hasta", type=str, default="2026-04-30",
                            help="Fecha hasta para verificacion (YYYY-MM-DD)")
        parser.add_argument("--fecha-sync-pagos", type=str, default="2024-01-01",
                            help="Desde que fecha sync_pagos_exactos procesa (default: 2024-01-01)")

    def handle(self, *args, **opts):
        from datetime import datetime
        skip_productos = opts["skip_productos"]
        skip_dtes = opts["skip_dtes"]
        skip_sync_pagos = opts["skip_sync_pagos"]
        skip_verif = opts["skip_verificacion"]
        fd = opts["fecha_desde"]
        fh = opts["fecha_hasta"]
        fd_pagos = opts["fecha_sync_pagos"]

        t_inicio = datetime.now()

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  ACTUALIZACION COMPLETA MySQL -> PostgreSQL"))
        self.stdout.write(f"  Inicio: {t_inicio.strftime('%Y-%m-%d %H:%M:%S')}")
        self.stdout.write("=" * 80)

        # ============================================================
        # PASO 1: Productos, stock, costos, precios
        # ============================================================
        if not skip_productos:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 1/5: Sincronizar productos, stock, costos, precios"))
            self.stdout.write("-" * 80)
            call_command("sync_productos_mysql")
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 1/5: SALTADO (--skip-productos)"))

        # ============================================================
        # PASO 2: Migracion completa desde MySQL
        # Ejecuta TODOS los pasos salvo productos/producto_talla
        # (ya cubiertos por sync_productos_mysql) y cleanup_test_data
        # Todos los pasos son IDEMPOTENTES (no duplican lo ya migrado)
        # ============================================================
        if not skip_dtes:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 2/5: Migrar TODO lo nuevo de MySQL"))
            self.stdout.write("-" * 80)
            tablas = [
                # Datos maestros (crean solo nuevos, no duplican)
                "empresas",
                "clientes",
                "sucursales",
                "vendedores",
                "atributos",
                "categorias",
                # Movimientos de stock (solo nuevos)
                "movimientos",
                # DTEs y ventas
                "dtes",
                "dte_productos",
                "fix_dtes_duplicados",
                "corregir_descuentos_dte",
                "corregir_fechas_dte",
                "corregir_sucursales_dte",
                "corregir_tipo_transaccion",
                "crear_dtes_faltantes",
                "ventas_pagos",
                "asignar_vendedores_dte",
                # Adicionales
                "creditos_personal",
                "parametros_maestros",
                "correlativos",
                "nc_vinculacion",
                "enriquecer_prediccion",
            ]
            call_command(
                "migrate_from_laravel",
                tables=tablas,
                no_input=True,
            )
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 2/5: SALTADO (--skip-dtes)"))

        # ============================================================
        # PASO 3: Sincronizar pagos exactos (corrige overpayments y mal vinculados)
        # ============================================================
        if not skip_dtes and not skip_sync_pagos:
            self.stdout.write("\n" + self.style.NOTICE(
                f">>> PASO 3/5: Sincronizar pagos exactos (desde {fd_pagos})"))
            self.stdout.write("-" * 80)
            try:
                call_command("sync_pagos_exactos", fecha_desde=fd_pagos, match_mode="mes")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error en sync_pagos_exactos: {e}"))
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 3/5: SALTADO"))

        # ============================================================
        # PASO 4: Limpieza de DTEs fantasma
        # ============================================================
        if not skip_dtes:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 4/5: Eliminar DTEs fantasma (en PG pero no en MySQL)"))
            self.stdout.write("-" * 80)
            self._limpiar_fantasmas()

        # ============================================================
        # PASO 5: Verificacion final
        # ============================================================
        if not skip_verif:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 5/5: Verificacion final"))
            self.stdout.write("-" * 80)
            call_command("verificacion_final", fecha_desde=fd, fecha_hasta=fh)
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 5/5: SALTADO (--skip-verificacion)"))

        t_fin = datetime.now()
        duracion = t_fin - t_inicio

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  ACTUALIZACION COMPLETADA"))
        self.stdout.write(f"  Duracion: {duracion}")
        self.stdout.write("=" * 80)

    # ================================================================
    # LIMPIEZA DTEs FANTASMA (PG pero no en MySQL)
    # Match SUPER-ESTRICTO por (folio + tipo + sucursal + mes): si MySQL
    # no tiene esa combinacion exacta, el DTE PG es fantasma.
    # Esto detecta DTEs duplicados que reciclan folios entre sucursales/meses.
    # ================================================================
    def _limpiar_fantasmas(self):
        TIPO_MAP = {
            'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
            'DESPACHO ELECTRONICO': 'GUIA',
            'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
            'BOLETA': 'BOLETA PAPEL',
            'NOTA DE CREDITO': 'NOTA DE CREDITO',
            'FACTURA EXENTA': 'FACTURA EXENTA',
            'GUIA': 'GUIA',
        }

        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
            database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            connection_timeout=300, autocommit=True,
        )

        suc_dir_to_alias = {
            s.direccion: s.alias for s in Sucursal.objects.all() if s.direccion
        }

        # mysql_por_mes: (folio, tipo, alias_sucursal, mes) que existe en MySQL
        mysql_por_mes = set()

        # Fuente 1: tabla MySQL.dte con bodega_inicio y fecha_emision
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT n_documento, tipo_documento, bodega_inicio,
                   DATE_FORMAT(fecha_emision, '%Y-%m') as mes
            FROM dte
            WHERE bodega_inicio IS NOT NULL AND fecha_emision IS NOT NULL
        """)
        for r in cursor:
            t = TIPO_MAP.get((r['tipo_documento'] or '').upper().strip(),
                             (r['tipo_documento'] or '').upper().strip())
            mysql_por_mes.add((r['n_documento'], t, r['bodega_inicio'], r['mes']))
        cursor.close()

        # Fuente 2: tabla MySQL.ventas
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT DISTINCT n_documento, tipo_documento, sucursal,
                   DATE_FORMAT(fecha, '%Y-%m') as mes
            FROM ventas
            WHERE n_documento > 0 AND sucursal IS NOT NULL AND fecha IS NOT NULL
        """)
        for r in cursor:
            t = TIPO_MAP.get((r['tipo_documento'] or '').upper().strip(),
                             (r['tipo_documento'] or '').upper().strip())
            alias = suc_dir_to_alias.get(r['sucursal'])
            if alias:
                mysql_por_mes.add((r['n_documento'], t, alias, r['mes']))
        cursor.close()
        conn.close()

        self.stdout.write(f"  MySQL (folio+tipo+sucursal+mes): {len(mysql_por_mes):,} combinaciones")

        # Buscar fantasmas
        fantasmas_ids = []
        fantasmas_por_suc = {}
        for suc in Sucursal.objects.all():
            cnt = 0
            for d in Dte.objects.filter(sucursal=suc).only(
                'id', 'numero_documento', 'tipo_documento', 'fecha_emision'
            ):
                if not d.fecha_emision:
                    continue
                mes = d.fecha_emision.strftime('%Y-%m')
                key = (d.numero_documento, d.tipo_documento, suc.alias, mes)
                if key not in mysql_por_mes:
                    fantasmas_ids.append(d.id)
                    cnt += 1
            if cnt > 0:
                fantasmas_por_suc[suc.alias] = cnt

        if not fantasmas_ids:
            self.stdout.write(self.style.SUCCESS("  OK: No hay DTEs fantasma"))
            return

        self.stdout.write(f"  Encontrados {len(fantasmas_ids):,} DTEs fantasma:")
        for alias, cnt in sorted(fantasmas_por_suc.items()):
            self.stdout.write(f"    {alias}: {cnt:,}")

        self.stdout.write(f"  Eliminando...")
        total_dtes = 0
        total_pagos = 0
        total_prods = 0
        for i in range(0, len(fantasmas_ids), 10000):
            lote = fantasmas_ids[i:i + 10000]
            pagos = Dte_Detalle_Pago.objects.filter(dte_id__in=lote).delete()
            prods = Dte_Productos.objects.filter(dte_id__in=lote).delete()
            dtes = Dte.objects.filter(id__in=lote).delete()
            total_pagos += pagos[0]
            total_prods += prods[0]
            total_dtes += dtes[0]

        self.stdout.write(self.style.WARNING(
            f"  Eliminados: {total_dtes:,} DTEs + {total_pagos:,} pagos + {total_prods:,} productos_dte"
        ))
