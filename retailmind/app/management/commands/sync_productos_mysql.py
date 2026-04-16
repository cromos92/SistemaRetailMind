"""
Sincronizacion Productos/Stock/Costos/Precios: MySQL -> PostgreSQL

MySQL es la fuente de verdad. Este comando:
1. ACTUALIZA stock, costo, precio de SKUs existentes (que coinciden por sku+sucursal)
2. CREA en PostgreSQL los SKUs que existen en MySQL pero no en PG
3. ELIMINA de PostgreSQL los SKUs/productos que NO existen en MySQL

Uso:
    python manage.py sync_productos_mysql                    # Sync completo
    python manage.py sync_productos_mysql --dry-run          # Solo simulacion
    python manage.py sync_productos_mysql --sucursal PAO1    # Solo una sucursal
    python manage.py sync_productos_mysql --no-delete        # No eliminar huerfanos
    python manage.py sync_productos_mysql --no-create        # No crear faltantes
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
from django.db.models import F, Sum, Count

from app.models import (
    AtributoOpcion, Categoria, Producto, Producto_Talla,
    Productos_Atributos, Sucursal,
)

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")


class Command(BaseCommand):
    help = "Sincroniza productos/stock/costos/precios desde MySQL a PostgreSQL (MySQL manda)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Solo simular, no modificar")
        parser.add_argument("--sucursal", type=str, default="", help="Filtrar por alias de sucursal")
        parser.add_argument("--no-delete", action="store_true", help="No eliminar SKUs/productos huerfanos")
        parser.add_argument("--no-create", action="store_true", help="No crear SKUs/productos faltantes")
        parser.add_argument("--no-update", action="store_true", help="No actualizar stock/costo/precio")
        parser.add_argument("--batch-size", type=int, default=1000, help="Tamano de batch")

    def handle(self, *args, **opts):
        self.dry_run = opts["dry_run"]
        self.sucursal_filter = opts["sucursal"].strip()
        self.do_delete = not opts["no_delete"]
        self.do_create = not opts["no_create"]
        self.do_update = not opts["no_update"]
        self.batch_size = opts["batch_size"]

        if self.dry_run:
            self.stdout.write(self.style.WARNING("=== MODO DRY-RUN: No se modificaran datos ==="))

        self.mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
            user=MYSQL_USER, password=MYSQL_PASSWORD,
            connection_timeout=300, autocommit=True,
        )

        try:
            self._cargar_caches()
            self._cargar_mysql()
            self._sync_productos_y_tallas()
            if self.do_delete:
                self._eliminar_huerfanos()
            self._resumen()
        finally:
            self.mysql_conn.close()

    # ================================================================
    # CACHES
    # ================================================================

    def _cargar_caches(self):
        self.stdout.write("\n[1/4] Cargando caches de PostgreSQL...")

        self.cache_sucursales = {s.alias: s for s in Sucursal.objects.all()}
        self.cache_sucursales_id = {s.id: s for s in Sucursal.objects.all()}
        self.stdout.write(f"  {len(self.cache_sucursales)} sucursales")

        self.cache_categorias = {c.nombre: c for c in Categoria.objects.all()}

        self.cache_opciones = {}
        for op in AtributoOpcion.objects.select_related("atributo").all():
            self.cache_opciones[(op.atributo.nombre, op.valor)] = op

        # Productos por clave (articulo, alias, marca, color)
        self.pg_productos = {}
        for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
            if not p.sucursal:
                continue
            key = (
                p.articulo,
                p.sucursal.alias,
                p.atributo1.valor if p.atributo1 else "SIN ESPECIFICAR",
                p.atributo2.valor if p.atributo2 else "SIN ESPECIFICAR",
            )
            self.pg_productos[key] = p

        # Tallas por (sku, sucursal_id)
        self.pg_tallas = {}
        for pt in Producto_Talla.objects.select_related("producto__sucursal").all():
            if pt.producto.sucursal_id:
                self.pg_tallas[(pt.sku, pt.producto.sucursal_id)] = pt

        self.stdout.write(f"  {len(self.pg_productos):,} productos en PG")
        self.stdout.write(f"  {len(self.pg_tallas):,} tallas/SKUs en PG")

    def _cargar_mysql(self):
        self.stdout.write("\n[2/4] Descargando tabla 'talla' de MySQL...")
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        where = f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
        cursor.execute(f"""
            SELECT codigo_asociado, articulo, descripcion,
                   COALESCE(marca, 'SIN ESPECIFICAR') as marca,
                   COALESCE(color, 'SIN ESPECIFICAR') as color,
                   COALESCE(sexo, 'SIN ESPECIFICAR') as sexo,
                   familia, alias, size, stock,
                   costo, preciointerno, precioventapublico
            FROM talla
            WHERE codigo_asociado IS NOT NULL
              AND articulo IS NOT NULL
              {where}
        """)
        self.mysql_rows = cursor.fetchall()
        cursor.close()
        self.stdout.write(f"  {len(self.mysql_rows):,} filas descargadas")

    # ================================================================
    # SYNC
    # ================================================================

    def _sync_productos_y_tallas(self):
        self.stdout.write("\n[3/4] Sincronizando productos, stock, costos y precios...")

        stats = defaultdict(int)

        # Pre-agrupar productos por clave
        productos_mysql = {}  # clave -> datos del primer row
        for row in self.mysql_rows:
            marca = row["marca"] or "SIN ESPECIFICAR"
            color = row["color"] or "SIN ESPECIFICAR"
            key = (row["articulo"], row["alias"], marca, color)
            if key not in productos_mysql:
                productos_mysql[key] = row
            else:
                # Actualizar costo/precio si es menor/mayor en este row
                existing = productos_mysql[key]
                if int(row["costo"] or 0) > 0 and int(existing["costo"] or 0) == 0:
                    existing["costo"] = row["costo"]
                if int(row["precioventapublico"] or 0) > int(existing["precioventapublico"] or 0):
                    existing["precioventapublico"] = row["precioventapublico"]

        # Paso 3a: Procesar productos (crear/actualizar)
        productos_por_crear = []
        productos_por_actualizar = []

        for key, row in productos_mysql.items():
            articulo, alias, marca, color = key
            suc = self.cache_sucursales.get(alias)
            if not suc:
                stats["sin_sucursal"] += 1
                continue

            pg_prod = self.pg_productos.get(key)
            costo = int(row["costo"] or 0)
            preciointerno = int(row["preciointerno"] or 0)
            precioventa = int(row["precioventapublico"] or 0)
            sobreprecio = max(0, preciointerno - costo) if preciointerno > 0 else max(0, precioventa - costo)

            if pg_prod:
                # Actualizar costos/precios si cambiaron
                if self.do_update:
                    need_update = False
                    if int(pg_prod.costo or 0) != costo:
                        pg_prod.costo = costo
                        need_update = True
                    if int(pg_prod.precioventa or 0) != precioventa:
                        pg_prod.precioventa = precioventa
                        need_update = True
                    if int(pg_prod.sobreprecio or 0) != sobreprecio:
                        pg_prod.sobreprecio = sobreprecio
                        need_update = True
                    if need_update:
                        productos_por_actualizar.append(pg_prod)
                        stats["productos_actualizados"] += 1
            else:
                if self.do_create:
                    marca_op = self._get_or_create_opcion("Marca", marca)
                    color_op = self._get_or_create_opcion("Color", color)
                    sexo_op = self._get_or_create_opcion("Sexo", row["sexo"] or "SIN ESPECIFICAR")
                    cat = self.cache_categorias.get(row["familia"])

                    productos_por_crear.append(Producto(
                        articulo=articulo,
                        descripcion=row["descripcion"] or articulo,
                        precioventa=precioventa,
                        costo=costo,
                        sobreprecio=sobreprecio,
                        sucursal_id=suc.id,
                        categoria_id=cat.id if cat else None,
                        atributo1_id=marca_op.id,
                        atributo2_id=color_op.id,
                        atributo3_id=sexo_op.id,
                    ))
                    stats["productos_nuevos"] += 1

        # Aplicar cambios de productos
        if productos_por_actualizar and not self.dry_run:
            self.stdout.write(f"  Actualizando {len(productos_por_actualizar):,} productos (costo/precio)...")
            for i in range(0, len(productos_por_actualizar), self.batch_size):
                batch = productos_por_actualizar[i:i + self.batch_size]
                Producto.objects.bulk_update(batch, ["costo", "precioventa", "sobreprecio"])

        if productos_por_crear and not self.dry_run:
            self.stdout.write(f"  Creando {len(productos_por_crear):,} productos nuevos...")
            Producto.objects.bulk_create(productos_por_crear, batch_size=self.batch_size)
            # Recargar cache de productos nuevos
            for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
                if not p.sucursal:
                    continue
                key = (
                    p.articulo,
                    p.sucursal.alias,
                    p.atributo1.valor if p.atributo1 else "SIN ESPECIFICAR",
                    p.atributo2.valor if p.atributo2 else "SIN ESPECIFICAR",
                )
                self.pg_productos[key] = p

        self.stdout.write(f"  Productos: {stats['productos_actualizados']:,} actualizados, "
                          f"{stats['productos_nuevos']:,} nuevos, {stats['sin_sucursal']:,} sin sucursal")

        # Paso 3b: Procesar tallas/SKUs (crear/actualizar stock)
        tallas_por_crear = []
        tallas_por_actualizar = []
        tallas_vistas = set()  # Para evitar duplicados en batch de creacion

        for row in self.mysql_rows:
            sku = int(row["codigo_asociado"])
            alias = row["alias"]
            suc = self.cache_sucursales.get(alias)
            if not suc:
                continue

            marca = row["marca"] or "SIN ESPECIFICAR"
            color = row["color"] or "SIN ESPECIFICAR"
            prod_key = (row["articulo"], alias, marca, color)
            pg_prod = self.pg_productos.get(prod_key)
            if not pg_prod:
                stats["sku_sin_producto"] += 1
                continue

            stock_mysql = int(row["stock"] or 0)
            talla_val = row["size"] or "U"

            pg_talla = self.pg_tallas.get((sku, suc.id))

            if pg_talla:
                if self.do_update and pg_talla.stock != stock_mysql:
                    pg_talla.stock = stock_mysql
                    pg_talla.talla = talla_val
                    tallas_por_actualizar.append(pg_talla)
                    stats["stock_actualizado"] += 1
            else:
                if self.do_create:
                    key_pair = (sku, suc.id)
                    if key_pair in tallas_vistas:
                        continue
                    tallas_vistas.add(key_pair)
                    tallas_por_crear.append(Producto_Talla(
                        sku=sku,
                        producto_id=pg_prod.id,
                        stock=stock_mysql,
                        talla=talla_val,
                    ))
                    stats["skus_nuevos"] += 1

        # Aplicar cambios de tallas
        if tallas_por_actualizar and not self.dry_run:
            self.stdout.write(f"  Actualizando stock de {len(tallas_por_actualizar):,} SKUs...")
            for i in range(0, len(tallas_por_actualizar), self.batch_size):
                batch = tallas_por_actualizar[i:i + self.batch_size]
                Producto_Talla.objects.bulk_update(batch, ["stock", "talla"])

        if tallas_por_crear and not self.dry_run:
            self.stdout.write(f"  Creando {len(tallas_por_crear):,} SKUs nuevos...")
            Producto_Talla.objects.bulk_create(tallas_por_crear, batch_size=self.batch_size, ignore_conflicts=True)

        self.stdout.write(f"  SKUs: {stats['stock_actualizado']:,} stock actualizado, "
                          f"{stats['skus_nuevos']:,} nuevos, {stats['sku_sin_producto']:,} sin producto")

        self.stats = stats

    # ================================================================
    # ELIMINAR HUERFANOS (en PG pero no en MySQL)
    # ================================================================

    def _eliminar_huerfanos(self):
        self.stdout.write("\n[4/4] Eliminando huerfanos de PostgreSQL...")

        # Recargar MySQL set
        mysql_skus = set()  # (sku, alias)
        mysql_productos_keys = set()  # (articulo, alias, marca, color)

        for row in self.mysql_rows:
            sku = int(row["codigo_asociado"])
            alias = row["alias"]
            mysql_skus.add((sku, alias))
            marca = row["marca"] or "SIN ESPECIFICAR"
            color = row["color"] or "SIN ESPECIFICAR"
            mysql_productos_keys.add((row["articulo"], alias, marca, color))

        # a) Eliminar SKUs huerfanos (existen en PG pero no en MySQL)
        ids_skus_huerfanos = []
        for pt in Producto_Talla.objects.select_related("producto__sucursal").all():
            if not pt.producto.sucursal:
                continue
            alias = pt.producto.sucursal.alias
            if self.sucursal_filter and alias != self.sucursal_filter:
                continue
            if (pt.sku, alias) not in mysql_skus:
                ids_skus_huerfanos.append(pt.id)

        if ids_skus_huerfanos:
            self.stdout.write(f"  SKUs huerfanos: {len(ids_skus_huerfanos):,}")
            if not self.dry_run:
                deleted, _ = Producto_Talla.objects.filter(id__in=ids_skus_huerfanos).delete()
                self.stdout.write(self.style.WARNING(f"    Eliminados {deleted:,} SKUs"))
            else:
                self.stdout.write(f"    [DRY-RUN] Se eliminarian {len(ids_skus_huerfanos):,} SKUs")
            self.stats["skus_eliminados"] = len(ids_skus_huerfanos)
        else:
            self.stdout.write("  Sin SKUs huerfanos")
            self.stats["skus_eliminados"] = 0

        # b) Eliminar productos huerfanos (existen en PG pero no en MySQL)
        ids_prod_huerfanos = []
        for p in Producto.objects.select_related("sucursal", "atributo1", "atributo2").all():
            if not p.sucursal:
                continue
            alias = p.sucursal.alias
            if self.sucursal_filter and alias != self.sucursal_filter:
                continue
            marca = p.atributo1.valor if p.atributo1 else "SIN ESPECIFICAR"
            color = p.atributo2.valor if p.atributo2 else "SIN ESPECIFICAR"
            key = (p.articulo, alias, marca, color)
            if key not in mysql_productos_keys:
                ids_prod_huerfanos.append(p.id)

        if ids_prod_huerfanos:
            self.stdout.write(f"  Productos huerfanos: {len(ids_prod_huerfanos):,}")
            if not self.dry_run:
                deleted, _ = Producto.objects.filter(id__in=ids_prod_huerfanos).delete()
                self.stdout.write(self.style.WARNING(
                    f"    Eliminados {deleted:,} productos (cascada elimina tallas/lotes/movimientos)"
                ))
            else:
                self.stdout.write(f"    [DRY-RUN] Se eliminarian {len(ids_prod_huerfanos):,} productos")
            self.stats["productos_eliminados"] = len(ids_prod_huerfanos)
        else:
            self.stdout.write("  Sin productos huerfanos")
            self.stats["productos_eliminados"] = 0

    # ================================================================
    # HELPERS
    # ================================================================

    def _get_or_create_opcion(self, atributo_nombre, valor):
        key = (atributo_nombre, valor)
        if key in self.cache_opciones:
            return self.cache_opciones[key]
        attr, _ = Productos_Atributos.objects.get_or_create(
            nombre=atributo_nombre,
            defaults={"descripcion": atributo_nombre}
        )
        op, _ = AtributoOpcion.objects.get_or_create(atributo=attr, valor=valor)
        self.cache_opciones[key] = op
        return op

    # ================================================================
    # RESUMEN
    # ================================================================

    def _resumen(self):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("RESUMEN DE SINCRONIZACION")
        self.stdout.write("=" * 70)
        for key, val in self.stats.items():
            self.stdout.write(f"  {key:<30} : {val:>10,}")

        # Verificar conteos finales
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("VERIFICACION FINAL (MySQL vs PostgreSQL)")
        self.stdout.write("-" * 70)

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        where = f"WHERE alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""
        cursor.execute(f"""
            SELECT alias, COUNT(*) as skus, SUM(stock) as stock
            FROM talla
            WHERE codigo_asociado IS NOT NULL {f"AND alias = '{self.sucursal_filter}'" if self.sucursal_filter else ""}
            GROUP BY alias ORDER BY alias
        """)
        mysql_data = {r["alias"]: r for r in cursor}
        cursor.close()

        self.stdout.write(f"  {'Suc':<8} {'MySQL SKUs':>12} {'PG SKUs':>12} {'MySQL Stock':>14} {'PG Stock':>14} {'Diff':>10}")

        total_ms, total_ps = 0, 0
        for alias in sorted(mysql_data.keys()):
            m = mysql_data[alias]
            suc = self.cache_sucursales.get(alias)
            if suc:
                pg_data = Producto_Talla.objects.filter(producto__sucursal=suc).aggregate(
                    skus=Count("id"), stock=Sum("stock"),
                )
                pg_skus = pg_data["skus"] or 0
                pg_stock = int(pg_data["stock"] or 0)
            else:
                pg_skus = 0
                pg_stock = 0

            m_stock = int(m["stock"] or 0)
            diff = m_stock - pg_stock
            total_ms += m_stock
            total_ps += pg_stock
            mark = " OK" if diff == 0 else f" <--"
            self.stdout.write(
                f"  {alias:<8} {m['skus']:>12,} {pg_skus:>12,} "
                f"{m_stock:>14,} {pg_stock:>14,} {diff:>10,}{mark}"
            )
        self.stdout.write(
            f"  {'TOTAL':<8} {'':>12} {'':>12} {total_ms:>14,} {total_ps:>14,} {total_ms - total_ps:>10,}"
        )
