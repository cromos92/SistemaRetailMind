"""
Actualizacion completa MySQL -> PostgreSQL

Este comando ejecuta en el orden correcto:
1. sync_productos_mysql    (stock, costos, precios, productos nuevos, eliminar huerfanos)
2. migrate_from_laravel    (DTEs, pagos, vendedores)
3. limpiar_dtes_fantasma   (elimina DTEs en PG que no existen en MySQL)
4. verificacion_final      (muestra diferencias)

Uso:
    python manage.py actualizar_desde_mysql
    python manage.py actualizar_desde_mysql --skip-productos   # Solo DTEs
    python manage.py actualizar_desde_mysql --skip-dtes        # Solo productos
    python manage.py actualizar_desde_mysql --skip-verificacion
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
        parser.add_argument("--skip-verificacion", action="store_true",
                            help="No ejecutar verificacion final")
        parser.add_argument("--fecha-desde", type=str, default="2026-04-01",
                            help="Fecha desde para verificacion (YYYY-MM-DD)")
        parser.add_argument("--fecha-hasta", type=str, default="2026-04-30",
                            help="Fecha hasta para verificacion (YYYY-MM-DD)")

    def handle(self, *args, **opts):
        skip_productos = opts["skip_productos"]
        skip_dtes = opts["skip_dtes"]
        skip_verif = opts["skip_verificacion"]
        fd = opts["fecha_desde"]
        fh = opts["fecha_hasta"]

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  ACTUALIZACION COMPLETA MySQL -> PostgreSQL"))
        self.stdout.write("=" * 80)

        # ============================================================
        # PASO 1: Productos, stock, costos, precios
        # ============================================================
        if not skip_productos:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 1/3: Sincronizar productos, stock, costos, precios"))
            self.stdout.write("-" * 80)
            call_command("sync_productos_mysql")
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 1/3: SALTADO (--skip-productos)"))

        # ============================================================
        # PASO 2: DTEs y ventas
        # ============================================================
        if not skip_dtes:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 2/3: Migrar DTEs, pagos, vendedores"))
            self.stdout.write("-" * 80)
            tablas = [
                "dtes",
                "dte_productos",
                "fix_dtes_duplicados",
                "corregir_descuentos_dte",
                "corregir_sucursales_dte",
                "corregir_tipo_transaccion",
                "crear_dtes_faltantes",
                "ventas_pagos",
                "asignar_vendedores_dte",
            ]
            call_command(
                "migrate_from_laravel",
                tables=tablas,
                no_input=True,
            )
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 2/3: SALTADO (--skip-dtes)"))

        # ============================================================
        # PASO 3: Limpieza de DTEs fantasma
        # ============================================================
        if not skip_dtes:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 3/4: Eliminar DTEs fantasma (existen en PG pero no en MySQL)"))
            self.stdout.write("-" * 80)
            self._limpiar_fantasmas()

        # ============================================================
        # PASO 4: Verificacion final
        # ============================================================
        if not skip_verif:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 4/4: Verificacion final"))
            self.stdout.write("-" * 80)
            call_command("verificacion_final", fecha_desde=fd, fecha_hasta=fh)
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 4/4: SALTADO (--skip-verificacion)"))

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  ACTUALIZACION COMPLETADA"))
        self.stdout.write("=" * 80)

    # ================================================================
    # LIMPIEZA DTEs FANTASMA (PG pero no en MySQL)
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

        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute(
            "SELECT DISTINCT n_documento, tipo_documento, sucursal "
            "FROM ventas WHERE n_documento > 0"
        )
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
        conn.close()

        fantasmas_ids = []
        for suc in Sucursal.objects.all():
            for d in Dte.objects.filter(sucursal=suc).only(
                'id', 'numero_documento', 'tipo_documento'
            ):
                key = (d.numero_documento, d.tipo_documento, suc.alias)
                if key in mysql_set:
                    continue
                if (d.numero_documento, d.tipo_documento) in mysql_sin_bodega:
                    continue
                fantasmas_ids.append(d.id)

        if not fantasmas_ids:
            self.stdout.write(self.style.SUCCESS("  OK: No hay DTEs fantasma"))
            return

        self.stdout.write(f"  Encontrados {len(fantasmas_ids):,} DTEs fantasma. Eliminando...")
        pagos = Dte_Detalle_Pago.objects.filter(dte_id__in=fantasmas_ids).delete()
        prods = Dte_Productos.objects.filter(dte_id__in=fantasmas_ids).delete()
        dtes = Dte.objects.filter(id__in=fantasmas_ids).delete()
        self.stdout.write(self.style.WARNING(
            f"  Eliminados: {dtes[0]:,} DTEs + {pagos[0]:,} pagos + {prods[0]:,} productos_dte"
        ))
