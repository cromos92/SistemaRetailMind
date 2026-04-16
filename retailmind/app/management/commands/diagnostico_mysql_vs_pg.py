"""
Diagnóstico detallado MySQL vs PostgreSQL — Productos y Tallas.

Compara fila a fila qué existe en MySQL que no está en PostgreSQL y viceversa,
para detectar exactamente por qué los conteos difieren.

Uso:
  python manage.py diagnostico_mysql_vs_pg
  python manage.py diagnostico_mysql_vs_pg --sucursal PAO1
  python manage.py diagnostico_mysql_vs_pg --fix          # Aplica correcciones
  python manage.py diagnostico_mysql_vs_pg --max-detalle 50
"""

import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import (
    AtributoOpcion,
    Categoria,
    Producto,
    Producto_Talla,
    Sucursal,
)

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")


class Command(BaseCommand):
    help = "Compara productos y tallas entre MySQL y PostgreSQL fila a fila."

    def add_arguments(self, parser):
        parser.add_argument("--sucursal", type=str, default="", help="Filtrar por alias de sucursal")
        parser.add_argument("--max-detalle", type=int, default=20, help="Max filas de detalle por sección")
        parser.add_argument("--fix", action="store_true", help="Crear productos/tallas faltantes en PG y eliminar huérfanos")

    def handle(self, *args, **options):
        self.sucursal_filter = options["sucursal"].strip()
        self.max_detalle = options["max_detalle"]
        self.fix_mode = options["fix"]

        self.mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
            user=MYSQL_USER, password=MYSQL_PASSWORD,
            connection_timeout=300, autocommit=True, get_warnings=False,
        )

        try:
            self._cargar_caches_pg()
            self._diagnostico_productos()
            self._diagnostico_tallas()
            self._diagnostico_normalizacion()
        finally:
            self.mysql_conn.close()

    # ================================================================
    # CACHES
    # ================================================================

    def _cargar_caches_pg(self):
        self.stdout.write("\n--- Cargando datos de PostgreSQL ---")
        self.cache_sucursales = {s.alias: s.id for s in Sucursal.objects.all()}
        self.stdout.write(f"  {len(self.cache_sucursales)} sucursales")

        self.cache_opciones = {}
        for op in AtributoOpcion.objects.select_related("atributo").all():
            self.cache_opciones[(op.atributo.nombre, op.valor)] = op.id

        self.pg_productos = {}
        for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
            key = (
                p.articulo,
                p.sucursal.alias if p.sucursal else "???",
                p.atributo1.valor if p.atributo1 else None,
                p.atributo2.valor if p.atributo2 else None,
            )
            self.pg_productos[key] = p.id
        self.stdout.write(f"  {len(self.pg_productos):,} productos en PG")

        self.pg_tallas = {}
        for pt_id, sku, pid in Producto_Talla.objects.values_list("id", "sku", "producto_id"):
            self.pg_tallas[(sku, pid)] = pt_id
        self.stdout.write(f"  {len(self.pg_tallas):,} tallas en PG")

    # ================================================================
    # PRODUCTOS
    # ================================================================

    def _diagnostico_productos(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("DIAGNOSTICO DE PRODUCTOS (agrupación MySQL vs PG)")
        self.stdout.write("=" * 70)

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        where = f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
        cursor.execute(f"""
            SELECT articulo, alias,
                   COALESCE(marca, '') as marca,
                   COALESCE(color, '') as color,
                   COUNT(*) as n_tallas
            FROM talla
            WHERE articulo IS NOT NULL {where}
            GROUP BY articulo, alias, marca, color
            ORDER BY alias, articulo
        """)
        mysql_productos = {}
        for row in cursor:
            marca = row["marca"] or "SIN ESPECIFICAR"
            color = row["color"] or "SIN ESPECIFICAR"
            key = (row["articulo"], row["alias"], marca, color)
            mysql_productos[key] = row["n_tallas"]
        cursor.close()

        self.stdout.write(f"\n  MySQL: {len(mysql_productos):,} productos agrupados")
        self.stdout.write(f"  PG:    {len(self.pg_productos):,} productos")

        solo_mysql = set(mysql_productos.keys()) - set(self.pg_productos.keys())
        solo_pg = set(self.pg_productos.keys()) - set(mysql_productos.keys())
        en_ambos = set(mysql_productos.keys()) & set(self.pg_productos.keys())

        self.stdout.write(f"\n  En ambos:    {len(en_ambos):,}")
        self.stdout.write(self.style.WARNING(f"  Solo MySQL:  {len(solo_mysql):,} (faltan en PG)"))
        self.stdout.write(self.style.ERROR(f"  Solo PG:     {len(solo_pg):,} (huérfanos en PG)"))

        # Desglose por sucursal
        por_suc_mysql = defaultdict(int)
        por_suc_pg = defaultdict(int)
        for k in solo_mysql:
            por_suc_mysql[k[1]] += 1
        for k in solo_pg:
            por_suc_pg[k[1]] += 1

        if por_suc_mysql:
            self.stdout.write("\n  Solo en MySQL (por sucursal):")
            for alias, cnt in sorted(por_suc_mysql.items()):
                self.stdout.write(f"    {alias:15s} → {cnt:,} productos faltan en PG")

        if por_suc_pg:
            self.stdout.write("\n  Solo en PG (por sucursal):")
            for alias, cnt in sorted(por_suc_pg.items()):
                self.stdout.write(f"    {alias:15s} → {cnt:,} productos huérfanos en PG")

        # Detalle
        if solo_mysql:
            self.stdout.write(f"\n  Detalle SOLO MYSQL (primeros {self.max_detalle}):")
            for i, k in enumerate(sorted(solo_mysql)):
                if i >= self.max_detalle:
                    self.stdout.write(f"    ... y {len(solo_mysql) - self.max_detalle} más")
                    break
                self.stdout.write(f"    articulo={k[0]}, alias={k[1]}, marca={k[2]}, color={k[3]}")

        if solo_pg:
            self.stdout.write(f"\n  Detalle SOLO PG (primeros {self.max_detalle}):")
            for i, k in enumerate(sorted(solo_pg)):
                if i >= self.max_detalle:
                    self.stdout.write(f"    ... y {len(solo_pg) - self.max_detalle} más")
                    break
                self.stdout.write(f"    articulo={k[0]}, alias={k[1]}, marca={k[2]}, color={k[3]} (PG id={self.pg_productos[k]})")

        # FIX: crear faltantes y eliminar huérfanos
        if self.fix_mode:
            self._fix_productos(solo_mysql, solo_pg, mysql_productos)

    def _fix_productos(self, solo_mysql, solo_pg, mysql_productos):
        self.stdout.write(self.style.WARNING("\n  --- MODO FIX: Corrigiendo productos ---"))

        if solo_pg:
            ids_huerfanos = [self.pg_productos[k] for k in solo_pg]
            eliminados, _ = Producto.objects.filter(id__in=ids_huerfanos).delete()
            self.stdout.write(self.style.SUCCESS(f"    Eliminados {eliminados} productos huérfanos de PG (cascada)"))
            for k in solo_pg:
                del self.pg_productos[k]

        if solo_mysql:
            cache_categorias = {c.nombre: c.id for c in Categoria.objects.all()}
            creados = 0
            sin_sucursal = 0

            cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
            where_suc = f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
            cursor.execute(f"""
                SELECT articulo, descripcion, marca, color, sexo, familia, alias,
                       MIN(costo) as costo, MIN(preciointerno) as preciointerno,
                       MIN(precioventapublico) as precioventa
                FROM talla
                WHERE articulo IS NOT NULL {where_suc}
                GROUP BY articulo, marca, color, descripcion, sexo, familia, alias
                ORDER BY articulo
            """)
            batch = []
            for row in cursor:
                marca = row["marca"] or "SIN ESPECIFICAR"
                color = row["color"] or "SIN ESPECIFICAR"
                key = (row["articulo"], row["alias"], marca, color)
                if key not in solo_mysql:
                    continue

                suc_id = self.cache_sucursales.get(row["alias"])
                if not suc_id:
                    sin_sucursal += 1
                    continue

                marca_op_id = self._get_or_create_opcion("Marca", marca)
                color_op_id = self._get_or_create_opcion("Color", color)
                sexo_op_id = self._get_or_create_opcion("Sexo", row["sexo"] or "SIN ESPECIFICAR")
                cat_id = cache_categorias.get(row["familia"])

                costo = int(row["costo"] or 0)
                preciointerno = int(row["preciointerno"] or 0)
                precioventa = int(row["precioventa"] or 0)
                sobreprecio = max(0, preciointerno - costo) if preciointerno > 0 else max(0, precioventa - costo)

                batch.append(Producto(
                    articulo=row["articulo"],
                    descripcion=row["descripcion"] or row["articulo"],
                    precioventa=precioventa,
                    costo=costo,
                    sobreprecio=sobreprecio,
                    sucursal_id=suc_id,
                    categoria_id=cat_id,
                    atributo1_id=marca_op_id,
                    atributo2_id=color_op_id,
                    atributo3_id=sexo_op_id,
                ))

                if len(batch) >= 500:
                    Producto.objects.bulk_create(batch, ignore_conflicts=True)
                    creados += len(batch)
                    batch = []

            if batch:
                Producto.objects.bulk_create(batch, ignore_conflicts=True)
                creados += len(batch)

            cursor.close()
            self.stdout.write(self.style.SUCCESS(f"    Creados {creados} productos faltantes en PG (sin_sucursal={sin_sucursal})"))

    # ================================================================
    # TALLAS
    # ================================================================

    def _diagnostico_tallas(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("DIAGNOSTICO DE TALLAS / SKUs")
        self.stdout.write("=" * 70)

        # Recargar pg_productos por si fix cambió algo
        prod_by_key = {}
        for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
            key = (
                p.articulo,
                p.sucursal.alias if p.sucursal else "???",
                p.atributo1.valor if p.atributo1 else None,
                p.atributo2.valor if p.atributo2 else None,
            )
            prod_by_key[key] = p.id

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        where = f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
        cursor.execute(f"""
            SELECT codigo_asociado, articulo,
                   COALESCE(marca, '') as marca,
                   COALESCE(color, '') as color,
                   size, stock, alias
            FROM talla
            WHERE codigo_asociado IS NOT NULL {where}
            ORDER BY alias, articulo
        """)

        mysql_tallas = set()
        sin_producto_mysql = 0
        sin_sucursal = 0
        mysql_por_suc = defaultdict(int)

        for row in cursor:
            sku = int(row["codigo_asociado"]) if row["codigo_asociado"] else None
            if not sku:
                continue

            alias = row["alias"]
            suc_id = self.cache_sucursales.get(alias)
            if not suc_id:
                sin_sucursal += 1
                continue

            marca = row["marca"] or "SIN ESPECIFICAR"
            color = row["color"] or "SIN ESPECIFICAR"
            prod_key = (row["articulo"], alias, marca, color)
            prod_id = prod_by_key.get(prod_key)

            if not prod_id:
                sin_producto_mysql += 1
                continue

            mysql_tallas.add((sku, prod_id))
            mysql_por_suc[alias] += 1

        cursor.close()

        pg_tallas_set = set(self.pg_tallas.keys())

        solo_mysql = mysql_tallas - pg_tallas_set
        solo_pg = pg_tallas_set - mysql_tallas
        en_ambos = mysql_tallas & pg_tallas_set

        self.stdout.write(f"\n  MySQL tallas (con producto): {len(mysql_tallas):,}")
        self.stdout.write(f"  PG tallas:                   {len(pg_tallas_set):,}")
        self.stdout.write(f"  En ambos:                    {len(en_ambos):,}")
        self.stdout.write(self.style.WARNING(f"  Solo MySQL:                  {len(solo_mysql):,} (faltan en PG)"))
        self.stdout.write(self.style.ERROR(f"  Solo PG:                     {len(solo_pg):,} (huérfanos en PG)"))
        self.stdout.write(f"  MySQL sin producto en PG:    {sin_producto_mysql:,}")
        self.stdout.write(f"  MySQL sin sucursal:          {sin_sucursal:,}")

        if solo_mysql:
            self.stdout.write(f"\n  Detalle SOLO MYSQL (primeros {self.max_detalle}):")
            for i, (sku, pid) in enumerate(sorted(solo_mysql)):
                if i >= self.max_detalle:
                    self.stdout.write(f"    ... y {len(solo_mysql) - self.max_detalle} más")
                    break
                self.stdout.write(f"    sku={sku}, producto_id={pid}")

        if solo_pg:
            self.stdout.write(f"\n  Detalle SOLO PG (primeros {self.max_detalle}):")
            for i, (sku, pid) in enumerate(sorted(solo_pg)):
                if i >= self.max_detalle:
                    self.stdout.write(f"    ... y {len(solo_pg) - self.max_detalle} más")
                    break
                pt_id = self.pg_tallas[(sku, pid)]
                self.stdout.write(f"    sku={sku}, producto_id={pid}, pt_id={pt_id}")

        if self.fix_mode:
            self._fix_tallas(solo_mysql, solo_pg)

    def _fix_tallas(self, solo_mysql, solo_pg):
        self.stdout.write(self.style.WARNING("\n  --- MODO FIX: Corrigiendo tallas ---"))

        if solo_pg:
            ids_huerfanos = [self.pg_tallas[k] for k in solo_pg if self.pg_tallas.get(k)]
            if ids_huerfanos:
                eliminados, _ = Producto_Talla.objects.filter(id__in=ids_huerfanos).delete()
                self.stdout.write(self.style.SUCCESS(f"    Eliminados {eliminados} tallas huérfanas de PG"))

        if solo_mysql:
            cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
            where = f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
            cursor.execute(f"""
                SELECT codigo_asociado, articulo,
                       COALESCE(marca, '') as marca,
                       COALESCE(color, '') as color,
                       size, stock, alias
                FROM talla
                WHERE codigo_asociado IS NOT NULL {where}
            """)

            prod_by_key = {}
            for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
                key = (
                    p.articulo,
                    p.sucursal.alias if p.sucursal else "???",
                    p.atributo1.valor if p.atributo1 else None,
                    p.atributo2.valor if p.atributo2 else None,
                )
                prod_by_key[key] = p.id

            batch = []
            for row in cursor:
                sku = int(row["codigo_asociado"]) if row["codigo_asociado"] else None
                if not sku:
                    continue
                suc_id = self.cache_sucursales.get(row["alias"])
                if not suc_id:
                    continue
                marca = row["marca"] or "SIN ESPECIFICAR"
                color = row["color"] or "SIN ESPECIFICAR"
                prod_id = prod_by_key.get((row["articulo"], row["alias"], marca, color))
                if not prod_id:
                    continue
                if (sku, prod_id) not in solo_mysql:
                    continue

                batch.append(Producto_Talla(
                    sku=sku, producto_id=prod_id,
                    talla=row["size"] or "U",
                    stock=int(row["stock"] or 0),
                ))

                if len(batch) >= 1000:
                    Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)
                    batch = []

            if batch:
                Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)

            cursor.close()
            self.stdout.write(self.style.SUCCESS(f"    Insertados hasta {len(solo_mysql)} tallas faltantes en PG"))

    # ================================================================
    # NORMALIZACIÓN — detectar por qué claves no coinciden
    # ================================================================

    def _diagnostico_normalizacion(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("DIAGNOSTICO DE NORMALIZACIÓN")
        self.stdout.write("=" * 70)

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)

        # Marcas NULL vs vacías en MySQL
        cursor.execute("""
            SELECT
                SUM(CASE WHEN marca IS NULL THEN 1 ELSE 0 END) as marca_null,
                SUM(CASE WHEN marca = '' THEN 1 ELSE 0 END) as marca_vacia,
                SUM(CASE WHEN marca IS NOT NULL AND marca != '' THEN 1 ELSE 0 END) as marca_ok
            FROM talla WHERE articulo IS NOT NULL
        """)
        row = cursor.fetchone()
        self.stdout.write(f"\n  MySQL marca: NULL={row['marca_null']:,}  vacia={row['marca_vacia']:,}  con_valor={row['marca_ok']:,}")

        cursor.execute("""
            SELECT
                SUM(CASE WHEN color IS NULL THEN 1 ELSE 0 END) as color_null,
                SUM(CASE WHEN color = '' THEN 1 ELSE 0 END) as color_vacia,
                SUM(CASE WHEN color IS NOT NULL AND color != '' THEN 1 ELSE 0 END) as color_ok
            FROM talla WHERE articulo IS NOT NULL
        """)
        row = cursor.fetchone()
        self.stdout.write(f"  MySQL color: NULL={row['color_null']:,}  vacia={row['color_vacia']:,}  con_valor={row['color_ok']:,}")

        # Detectar artículos con múltiples combinaciones NULL/vacío
        cursor.execute("""
            SELECT articulo, alias, COUNT(DISTINCT COALESCE(marca, '')) as n_marcas,
                   COUNT(DISTINCT COALESCE(color, '')) as n_colores
            FROM talla
            WHERE articulo IS NOT NULL
            GROUP BY articulo, alias
            HAVING (COUNT(DISTINCT COALESCE(marca, '')) != COUNT(DISTINCT COALESCE(NULLIF(marca, ''), 'SIN ESPECIFICAR')))
                OR (COUNT(DISTINCT COALESCE(color, '')) != COUNT(DISTINCT COALESCE(NULLIF(color, ''), 'SIN ESPECIFICAR')))
            LIMIT 20
        """)
        conflictos = cursor.fetchall()
        if conflictos:
            self.stdout.write(self.style.WARNING(f"\n  Artículos con conflicto NULL vs '' en marca/color ({len(conflictos)}):"))
            for c in conflictos[:10]:
                self.stdout.write(f"    articulo={c['articulo']}, alias={c['alias']}, n_marcas={c['n_marcas']}, n_colores={c['n_colores']}")
        else:
            self.stdout.write(self.style.SUCCESS("\n  Sin conflictos NULL vs '' en marca/color"))

        # Alias desconocidos
        cursor.execute("SELECT DISTINCT alias FROM talla WHERE alias IS NOT NULL")
        mysql_aliases = {r["alias"] for r in cursor}
        desconocidos = mysql_aliases - set(self.cache_sucursales.keys())
        if desconocidos:
            self.stdout.write(self.style.ERROR(f"\n  Alias en MySQL sin Sucursal en PG: {desconocidos}"))
        else:
            self.stdout.write(self.style.SUCCESS("\n  Todos los alias MySQL tienen Sucursal en PG"))

        cursor.close()

    # ================================================================
    # HELPERS
    # ================================================================

    def _get_or_create_opcion(self, atributo_nombre, valor):
        key = (atributo_nombre, valor)
        if key in self.cache_opciones:
            return self.cache_opciones[key]
        from app.models import Productos_Atributos
        attr, _ = Productos_Atributos.objects.get_or_create(nombre=atributo_nombre)
        op, _ = AtributoOpcion.objects.get_or_create(atributo=attr, valor=valor)
        self.cache_opciones[key] = op.id
        return op.id
