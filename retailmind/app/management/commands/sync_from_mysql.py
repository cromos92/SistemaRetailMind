"""
Comando para SINCRONIZAR datos desde MySQL (producción) a PostgreSQL (Django)

Este comando:
1. Actualiza stock de Producto_Talla desde MySQL
2. Actualiza precios de Producto desde MySQL
3. ELIMINA registros que no existan en MySQL (son datos de prueba)

Uso:
    python manage.py sync_from_mysql --dry-run          # Ver qué se haría sin ejecutar
    python manage.py sync_from_mysql                    # Ejecutar sincronización completa
    python manage.py sync_from_mysql --only-stock       # Solo actualizar stock
    python manage.py sync_from_mysql --only-delete      # Solo eliminar huérfanos
"""

import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction, connection

from app.models import (
    Producto, Producto_Talla, Movimientos_Producto, 
    Dte_Productos, Sucursal
)


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Sincroniza datos desde MySQL (producción) y elimina datos de prueba en PostgreSQL'

    def __init__(self):
        super().__init__()
        self.mysql_conn = None
        self.dry_run = False
        self.stats = defaultdict(int)
        self.start_time = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostrar qué se haría sin ejecutar cambios'
        )
        parser.add_argument(
            '--only-stock',
            action='store_true',
            help='Solo actualizar stock, no eliminar nada'
        )
        parser.add_argument(
            '--only-delete',
            action='store_true',
            help='Solo eliminar huérfanos, no actualizar stock'
        )
        parser.add_argument(
            '--skip-movimientos',
            action='store_true',
            help='No tocar movimientos (mantener histórico)'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        only_stock = options.get('only_stock', False)
        only_delete = options.get('only_delete', False)
        skip_movimientos = options.get('skip_movimientos', False)
        
        self.start_time = datetime.now()
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 [DRY-RUN] Modo simulación - no se harán cambios'))
        
        # Conectar a MySQL
        try:
            self.mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                connection_timeout=300,
                autocommit=True,
            )
            self.stdout.write(self.style.SUCCESS('✓ Conexión MySQL establecida'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error MySQL: {e}'))
            return

        try:
            with transaction.atomic():
                # ================================================================
                # PASO 1: Cargar datos de MySQL (fuente de verdad)
                # ================================================================
                self.stdout.write('\n' + '='*70)
                self.stdout.write('📥 CARGANDO DATOS DESDE MySQL (PRODUCCIÓN)')
                self.stdout.write('='*70)
                
                mysql_skus, mysql_productos, mysql_movimientos = self.cargar_datos_mysql()
                
                if not only_delete:
                    # ============================================================
                    # PASO 2: Actualizar stock
                    # ============================================================
                    self.stdout.write('\n' + '='*70)
                    self.stdout.write('📊 ACTUALIZANDO STOCK DESDE MySQL')
                    self.stdout.write('='*70)
                    self.sync_stock(mysql_skus)
                
                if not only_stock:
                    # ============================================================
                    # PASO 3: Eliminar huérfanos (en orden correcto)
                    # ============================================================
                    self.stdout.write('\n' + '='*70)
                    self.stdout.write('🗑️  ELIMINANDO DATOS DE PRUEBA (no existen en MySQL)')
                    self.stdout.write('='*70)
                    
                    # 3.1 Eliminar DTE_Productos huérfanos
                    self.eliminar_dte_productos_huerfanos(mysql_skus)
                    
                    # 3.2 Eliminar Movimientos huérfanos
                    if not skip_movimientos:
                        self.eliminar_movimientos_huerfanos(mysql_movimientos)
                    
                    # 3.3 Eliminar Producto_Talla huérfanos
                    self.eliminar_skus_huerfanos(mysql_skus)
                    
                    # 3.4 Eliminar Producto huérfanos
                    self.eliminar_productos_huerfanos(mysql_productos)
                
                # ================================================================
                # RESUMEN
                # ================================================================
                self.mostrar_resumen()
                
                if self.dry_run:
                    self.stdout.write(self.style.WARNING('\n⚠️  DRY-RUN: No se guardaron cambios. Ejecuta sin --dry-run para aplicar.'))
                    raise Exception("Rollback dry-run")  # Forzar rollback

        except Exception as e:
            if "Rollback dry-run" not in str(e):
                self.stdout.write(self.style.ERROR(f'❌ Error: {e}'))
        finally:
            if self.mysql_conn:
                self.mysql_conn.close()

    def cargar_datos_mysql(self):
        """Carga todos los datos relevantes de MySQL"""
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        # ====================================================================
        # SKUs (codigo_asociado) con su stock actual
        # ====================================================================
        self.stdout.write('  ⏳ Cargando SKUs de MySQL...')
        cursor.execute('''
            SELECT codigo_asociado, stock, alias, articulo, marca, color
            FROM talla 
            WHERE codigo_asociado IS NOT NULL
        ''')
        
        mysql_skus = {}
        for row in cursor:
            sku = int(row['codigo_asociado'])
            alias = row['alias'] or ''
            # Clave compuesta para evitar colisiones entre sucursales
            key = f"{sku}:{alias}"
            mysql_skus[key] = {
                'sku': sku,
                'stock': int(row['stock'] or 0),
                'alias': alias,
                'articulo': row['articulo'],
                'marca': row['marca'],
                'color': row['color'],
            }
        self.stdout.write(f'  ✓ {len(mysql_skus):,} SKUs en MySQL')
        
        # ====================================================================
        # Productos agrupados (articulo + sucursal + marca + color)
        # ====================================================================
        self.stdout.write('  ⏳ Cargando productos agrupados de MySQL...')
        cursor.execute('''
            SELECT DISTINCT articulo, alias, marca, color
            FROM talla 
            WHERE articulo IS NOT NULL
        ''')
        
        mysql_productos = set()
        for row in cursor:
            # Clave: (articulo, alias, marca, color)
            key = (
                row['articulo'],
                row['alias'] or '',
                row['marca'] or 'SIN ESPECIFICAR',
                row['color'] or 'SIN ESPECIFICAR'
            )
            mysql_productos.add(key)
        self.stdout.write(f'  ✓ {len(mysql_productos):,} productos agrupados en MySQL')
        
        # ====================================================================
        # Movimientos (por referencia externa MIG:id)
        # ====================================================================
        self.stdout.write('  ⏳ Cargando IDs de movimientos de MySQL...')
        cursor.execute('SELECT id FROM movimiento_productos')
        
        mysql_movimientos = set()
        for row in cursor:
            mysql_movimientos.add(f"MIG:{row['id']}")
        self.stdout.write(f'  ✓ {len(mysql_movimientos):,} movimientos en MySQL')
        
        cursor.close()
        return mysql_skus, mysql_productos, mysql_movimientos

    def sync_stock(self, mysql_skus):
        """Actualiza el stock de todos los SKUs desde MySQL"""
        
        # Cargar sucursales
        cache_sucursales = {s.alias: s.id for s in Sucursal.objects.all()}
        
        # Cargar todos los Producto_Talla de PostgreSQL
        self.stdout.write('  ⏳ Cargando SKUs de PostgreSQL...')
        pg_skus = {}
        for pt in Producto_Talla.objects.select_related('producto__sucursal').all():
            alias = pt.producto.sucursal.alias if pt.producto.sucursal else ''
            key = f"{pt.sku}:{alias}"
            pg_skus[key] = {
                'id': pt.id,
                'stock_actual': pt.stock,
            }
        self.stdout.write(f'  ✓ {len(pg_skus):,} SKUs en PostgreSQL')
        
        # Comparar y actualizar
        actualizaciones = []
        sin_cambio = 0
        no_encontrado = 0
        
        for key, mysql_data in mysql_skus.items():
            pg_data = pg_skus.get(key)
            
            if not pg_data:
                # SKU existe en MySQL pero no en PostgreSQL (se creará en migración normal)
                no_encontrado += 1
                continue
            
            stock_mysql = mysql_data['stock']
            stock_pg = pg_data['stock_actual']
            
            if stock_mysql != stock_pg:
                actualizaciones.append((pg_data['id'], stock_mysql, stock_pg))
            else:
                sin_cambio += 1
        
        self.stdout.write(f'\n  📊 Resumen de stock:')
        self.stdout.write(f'      - Sin cambio: {sin_cambio:,}')
        self.stdout.write(f'      - Para actualizar: {len(actualizaciones):,}')
        self.stdout.write(f'      - No encontrados en PG: {no_encontrado:,}')
        
        if not actualizaciones:
            self.stdout.write(self.style.SUCCESS('  ✓ Stock ya está sincronizado'))
            return
        
        # Mostrar ejemplos
        self.stdout.write(f'\n  📋 Ejemplos de cambios:')
        for pt_id, nuevo_stock, viejo_stock in actualizaciones[:5]:
            self.stdout.write(f'      ID {pt_id}: {viejo_stock} → {nuevo_stock}')
        if len(actualizaciones) > 5:
            self.stdout.write(f'      ... y {len(actualizaciones) - 5} más')
        
        if self.dry_run:
            self.stats['stock_actualizado'] = len(actualizaciones)
            return
        
        # Ejecutar actualizaciones en batch
        self.stdout.write(f'\n  ⏳ Actualizando stock...')
        batch_size = 5000
        total_actualizado = 0
        
        with connection.cursor() as cursor:
            for i in range(0, len(actualizaciones), batch_size):
                batch = actualizaciones[i:i + batch_size]
                
                # Construir UPDATE con CASE
                cases = ' '.join([f"WHEN {pt_id} THEN {nuevo}" for pt_id, nuevo, _ in batch])
                ids = ','.join([str(pt_id) for pt_id, _, _ in batch])
                
                cursor.execute(f'''
                    UPDATE app_producto_talla 
                    SET stock = CASE id {cases} END
                    WHERE id IN ({ids})
                ''')
                total_actualizado += cursor.rowcount
        
        self.stats['stock_actualizado'] = total_actualizado
        self.stdout.write(self.style.SUCCESS(f'  ✓ {total_actualizado:,} stocks actualizados'))

    def eliminar_dte_productos_huerfanos(self, mysql_skus):
        """Elimina Dte_Productos que referencian SKUs que no existen en MySQL"""
        
        # Obtener set de SKUs válidos (solo el número)
        skus_validos = set()
        for key in mysql_skus.keys():
            sku = int(key.split(':')[0])
            skus_validos.add(sku)
        
        # Buscar Dte_Productos con SKUs huérfanos
        self.stdout.write('  ⏳ Buscando DTE_Productos huérfanos...')
        
        dte_productos_huerfanos = Dte_Productos.objects.exclude(
            productoTalla__sku__in=skus_validos
        )
        count = dte_productos_huerfanos.count()
        
        self.stdout.write(f'  📊 DTE_Productos huérfanos: {count:,}')
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ No hay DTE_Productos huérfanos'))
            return
        
        if self.dry_run:
            self.stats['dte_productos_eliminados'] = count
            return
        
        # Eliminar
        deleted, _ = dte_productos_huerfanos.delete()
        self.stats['dte_productos_eliminados'] = deleted
        self.stdout.write(self.style.SUCCESS(f'  ✓ {deleted:,} DTE_Productos eliminados'))

    def eliminar_movimientos_huerfanos(self, mysql_movimientos):
        """Elimina movimientos que no existen en MySQL (solo los migrados MIG:*)"""
        
        self.stdout.write('  ⏳ Buscando movimientos huérfanos...')
        
        # Solo considerar movimientos migrados (con referencia MIG:*)
        movimientos_migrados = Movimientos_Producto.objects.filter(
            referencia_externa__startswith='MIG:'
        )
        
        total_migrados = movimientos_migrados.count()
        self.stdout.write(f'      Total movimientos migrados en PG: {total_migrados:,}')
        self.stdout.write(f'      Total movimientos en MySQL: {len(mysql_movimientos):,}')
        
        # Buscar huérfanos
        refs_pg = set(movimientos_migrados.values_list('referencia_externa', flat=True))
        refs_huerfanas = refs_pg - mysql_movimientos
        
        count = len(refs_huerfanas)
        self.stdout.write(f'  📊 Movimientos huérfanos: {count:,}')
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ No hay movimientos huérfanos'))
            return
        
        if self.dry_run:
            self.stats['movimientos_eliminados'] = count
            return
        
        # Eliminar en batches
        deleted = 0
        batch_size = 10000
        refs_list = list(refs_huerfanas)
        
        for i in range(0, len(refs_list), batch_size):
            batch = refs_list[i:i + batch_size]
            del_count, _ = Movimientos_Producto.objects.filter(
                referencia_externa__in=batch
            ).delete()
            deleted += del_count
        
        self.stats['movimientos_eliminados'] = deleted
        self.stdout.write(self.style.SUCCESS(f'  ✓ {deleted:,} movimientos eliminados'))

    def eliminar_skus_huerfanos(self, mysql_skus):
        """Elimina Producto_Talla que no existen en MySQL"""
        
        # Crear set de claves válidas
        self.stdout.write('  ⏳ Buscando SKUs huérfanos...')
        
        # Cargar todos los SKUs de PostgreSQL con su clave compuesta
        skus_pg = []
        for pt in Producto_Talla.objects.select_related('producto__sucursal').all():
            alias = pt.producto.sucursal.alias if pt.producto.sucursal else ''
            key = f"{pt.sku}:{alias}"
            skus_pg.append((pt.id, key))
        
        # Identificar huérfanos
        ids_huerfanos = [pt_id for pt_id, key in skus_pg if key not in mysql_skus]
        
        count = len(ids_huerfanos)
        self.stdout.write(f'  📊 SKUs huérfanos: {count:,} de {len(skus_pg):,} totales')
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ No hay SKUs huérfanos'))
            return
        
        # Mostrar ejemplos
        if count <= 10:
            for pt_id, key in skus_pg[:10]:
                if key not in mysql_skus:
                    self.stdout.write(f'      - ID {pt_id}: {key}')
        
        if self.dry_run:
            self.stats['skus_eliminados'] = count
            return
        
        # Eliminar en batches
        deleted = 0
        batch_size = 5000
        
        for i in range(0, len(ids_huerfanos), batch_size):
            batch = ids_huerfanos[i:i + batch_size]
            del_count, _ = Producto_Talla.objects.filter(id__in=batch).delete()
            deleted += del_count
        
        self.stats['skus_eliminados'] = deleted
        self.stdout.write(self.style.SUCCESS(f'  ✓ {deleted:,} SKUs eliminados'))

    def eliminar_productos_huerfanos(self, mysql_productos):
        """Elimina Producto que no existen en MySQL"""
        
        self.stdout.write('  ⏳ Buscando productos huérfanos...')
        
        # Cargar todos los productos de PostgreSQL
        productos_pg = []
        for p in Producto.objects.select_related('sucursal', 'atributo1', 'atributo2').all():
            alias = p.sucursal.alias if p.sucursal else ''
            marca = p.atributo1.valor if p.atributo1 else 'SIN ESPECIFICAR'
            color = p.atributo2.valor if p.atributo2 else 'SIN ESPECIFICAR'
            key = (p.articulo, alias, marca, color)
            productos_pg.append((p.id, key))
        
        # Identificar huérfanos
        ids_huerfanos = [p_id for p_id, key in productos_pg if key not in mysql_productos]
        
        count = len(ids_huerfanos)
        self.stdout.write(f'  📊 Productos huérfanos: {count:,} de {len(productos_pg):,} totales')
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ No hay productos huérfanos'))
            return
        
        if self.dry_run:
            self.stats['productos_eliminados'] = count
            return
        
        # Eliminar en batches
        deleted = 0
        batch_size = 2000
        
        for i in range(0, len(ids_huerfanos), batch_size):
            batch = ids_huerfanos[i:i + batch_size]
            del_count, _ = Producto.objects.filter(id__in=batch).delete()
            deleted += del_count
        
        self.stats['productos_eliminados'] = deleted
        self.stdout.write(self.style.SUCCESS(f'  ✓ {deleted:,} productos eliminados'))

    def mostrar_resumen(self):
        """Muestra resumen de la sincronización"""
        elapsed = datetime.now() - self.start_time
        
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('📊 RESUMEN DE SINCRONIZACIÓN'))
        self.stdout.write('='*70)
        
        resumen = [
            ('Stock actualizado', self.stats.get('stock_actualizado', 0)),
            ('DTE_Productos eliminados', self.stats.get('dte_productos_eliminados', 0)),
            ('Movimientos eliminados', self.stats.get('movimientos_eliminados', 0)),
            ('SKUs eliminados', self.stats.get('skus_eliminados', 0)),
            ('Productos eliminados', self.stats.get('productos_eliminados', 0)),
        ]
        
        for nombre, valor in resumen:
            status = '🔴' if valor > 0 else '🟢'
            self.stdout.write(f'  {status} {nombre:30s}: {valor:>8,}')
        
        self.stdout.write('-'*70)
        self.stdout.write(f'  ⏱️  Tiempo: {elapsed}')
        self.stdout.write('='*70)
