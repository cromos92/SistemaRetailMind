"""
Actualizacion completa MySQL -> PostgreSQL

Este comando ejecuta en el orden correcto:
1. sync_productos_mysql  (stock, costos, precios, productos nuevos, eliminar huerfanos)
2. migrate_from_laravel con los pasos de DTEs (actualiza ventas)
3. verificacion_final    (muestra diferencias)

Uso:
    python manage.py actualizar_desde_mysql
    python manage.py actualizar_desde_mysql --skip-productos   # Solo DTEs
    python manage.py actualizar_desde_mysql --skip-dtes        # Solo productos
    python manage.py actualizar_desde_mysql --skip-verificacion
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


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
        # PASO 3: Verificacion final
        # ============================================================
        if not skip_verif:
            self.stdout.write("\n" + self.style.NOTICE(">>> PASO 3/3: Verificacion final"))
            self.stdout.write("-" * 80)
            call_command("verificacion_final", fecha_desde=fd, fecha_hasta=fh)
        else:
            self.stdout.write(self.style.WARNING("\n>>> PASO 3/3: SALTADO (--skip-verificacion)"))

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  ACTUALIZACION COMPLETADA"))
        self.stdout.write("=" * 80)
