"""
Corrige productos de DTEs migrando correctamente desde MySQL
Usa mapeo IdDte_MySQL -> id_PostgreSQL por numero_documento + monto
"""
import os
import sys
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from app.models import Dte, Dte_Productos, Producto_Talla, Sucursal


MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Corrige productos de DTEs usando mapeo correcto'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limpiar', action='store_true', help='Eliminar productos existentes primero')
        parser.add_argument('--batch-size', type=int, default=1000)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.limpiar = options['limpiar']
        self.batch_size = options['batch_size']
        
        self.stdout.write('=' * 70)
        self.stdout.write('CORREGIR PRODUCTOS DE DTEs')
        self.stdout.write('=' * 70)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN]'))
        
        inicio = datetime.now()
        
        # 1. Crear mapeo IdDte MySQL -> id PostgreSQL
        mapeo_dte = self.crear_mapeo_dte()
        
        # 2. Cargar Producto_Talla
        cache_tallas = self.cargar_producto_tallas()
        
        # 3. Opcionalmente limpiar productos existentes
        if self.limpiar and not self.dry_run:
            self.limpiar_productos()
        
        # 4. Migrar productos
        self.migrar_productos(mapeo_dte, cache_tallas)
        
        tiempo = (datetime.now() - inicio).total_seconds()
        self.stdout.write(self.style.SUCCESS(f'\n[OK] Completado en {tiempo:.1f}s'))

    def progress_bar(self, current, total, width=40, extra=''):
        pct = current / total if total > 0 else 0
        filled = int(width * pct)
        bar = '#' * filled + '-' * (width - filled)
        return f'[{bar}] {pct*100:.1f}% ({current:,}/{total:,}) {extra}'

    def crear_mapeo_dte(self):
        """Crea mapeo IdDte_MySQL -> id_PostgreSQL usando numero+monto"""
        self.stdout.write('\n[1/4] Creando mapeo IdDte MySQL -> PostgreSQL...')
        
        # Cargar DTEs de PostgreSQL indexados por numero+monto
        pg_dtes = {}
        for dte in Dte.objects.all():
            monto = int(dte.monto_con_iva or 0)
            key = (dte.numero_documento, monto)
            pg_dtes[key] = dte.id
        self.stdout.write(f'   DTEs PostgreSQL: {len(pg_dtes):,}')
        
        # Conectar a MySQL y crear mapeo
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute('SELECT id, n_documento, monto_total FROM dte WHERE n_documento > 0')
        
        mapeo = {}  # IdDte_MySQL -> id_PostgreSQL
        sin_match = 0
        
        for row in cursor:
            mysql_id = row['id']
            n_doc = row['n_documento']
            monto = int(row['monto_total'] or 0)
            
            key = (n_doc, monto)
            pg_id = pg_dtes.get(key)
            
            if pg_id:
                mapeo[mysql_id] = pg_id
            else:
                sin_match += 1
        
        cursor.close()
        conn.close()
        
        self.stdout.write(f'   Mapeos creados: {len(mapeo):,}')
        self.stdout.write(f'   Sin match: {sin_match:,}')
        
        return mapeo

    def cargar_producto_tallas(self):
        """Carga Producto_Talla por SKU"""
        self.stdout.write('\n[2/4] Cargando Producto_Talla...')
        
        cache = {}
        for pt in Producto_Talla.objects.select_related('sucursal').all():
            # Por SKU
            if pt.sku:
                cache[str(pt.sku)] = pt
        
        self.stdout.write(f'   Producto_Talla: {len(cache):,}')
        return cache

    def limpiar_productos(self):
        """Elimina productos de DTE existentes"""
        self.stdout.write('\n[3/4] Limpiando productos existentes...')
        
        with connection.cursor() as c:
            c.execute('SELECT COUNT(*) FROM app_dte_productos')
            total = c.fetchone()[0]
            self.stdout.write(f'   Productos actuales: {total:,}')
            
            c.execute('DELETE FROM app_dte_productos')
            self.stdout.write(f'   Eliminados: {c.rowcount:,}')

    def migrar_productos(self, mapeo_dte, cache_tallas):
        """Migra productos desde MySQL"""
        self.stdout.write('\n[4/4] Migrando productos...')
        
        # Cargar existentes para evitar duplicados
        existentes = set(
            Dte_Productos.objects.values_list('dte_id', 'productoTalla_id', 'stock')
        )
        self.stdout.write(f'   Productos existentes: {len(existentes):,}')
        
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True, buffered=True)
        
        cursor.execute('SELECT COUNT(*) as total FROM productos_dte')
        total = cursor.fetchone()['total']
        self.stdout.write(f'   Productos en MySQL: {total:,}')
        
        cursor.execute('''
            SELECT 
                ID, factura_asociada, codigo_asociado, articulo, descripcion,
                talla, cantidad, precio_interno, precio_publico, costo,
                tipo_documento, codigo_barra, IdDte, estado
            FROM productos_dte
            ORDER BY ID
        ''')
        
        batch = []
        count = 0
        sin_dte = 0
        sin_talla = 0
        duplicados = 0
        
        for idx, row in enumerate(cursor, 1):
            # Buscar DTE usando el mapeo
            pg_dte_id = None
            
            if row['IdDte']:
                pg_dte_id = mapeo_dte.get(row['IdDte'])
            
            if not pg_dte_id:
                sin_dte += 1
                continue
            
            # Buscar Producto_Talla por codigo_asociado (SKU)
            sku = str(row['codigo_asociado']) if row['codigo_asociado'] else None
            producto_talla = cache_tallas.get(sku) if sku else None
            
            if not producto_talla:
                sin_talla += 1
                continue
            
            # Calcular valores
            costo = int(row['costo'] or 0)
            precio_interno = int(row['precio_interno'] or 0)
            sobreprecio = max(0, precio_interno - costo)
            precio = int(row['precio_publico'] or 0) or precio_interno
            stock = int(row['cantidad'] or 0)
            
            # Verificar duplicado
            dup_key = (pg_dte_id, producto_talla.id, stock)
            if dup_key in existentes:
                duplicados += 1
                continue
            
            if not self.dry_run:
                batch.append(Dte_Productos(
                    dte_id=pg_dte_id,
                    productoTalla=producto_talla,
                    descripcion=row['descripcion'] or row['articulo'] or '',
                    costo=costo,
                    sobreprecio=sobreprecio,
                    precio=precio,
                    stock=stock,
                    activo=(row['estado'] or '').upper() != 'ANULADO'
                ))
                existentes.add(dup_key)
                
                if len(batch) >= self.batch_size:
                    Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1
            
            if idx % 50000 == 0:
                sys.stdout.write(f'\r   {self.progress_bar(idx, total, extra=f"{count:,} OK")}')
                sys.stdout.flush()
        
        # Guardar batch final
        if batch and not self.dry_run:
            Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)
        
        sys.stdout.write(f'\r   {self.progress_bar(total, total, extra=f"{count:,} OK")}\n')
        
        cursor.close()
        conn.close()
        
        self.stdout.write(self.style.SUCCESS(f'\n   Productos migrados: {count:,}'))
        self.stdout.write(f'   Sin DTE: {sin_dte:,}')
        self.stdout.write(f'   Sin talla: {sin_talla:,}')
        self.stdout.write(f'   Duplicados: {duplicados:,}')
