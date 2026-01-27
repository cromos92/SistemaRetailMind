"""
Django management command para limpiar datos de PostgreSQL que NO existen en MySQL

Este comando sincroniza la base de datos local (PostgreSQL) con MySQL de producción,
eliminando registros que fueron creados localmente para testing y no existen en MySQL.

IMPORTANTE: Este comando es DESTRUCTIVO. Siempre ejecutar primero con --dry-run

Uso:
    python manage.py sync_cleanup_from_mysql --dry-run          # Ver qué se eliminaría
    python manage.py sync_cleanup_from_mysql                    # Ejecutar limpieza
    python manage.py sync_cleanup_from_mysql --tables productos # Solo tabla específica
    python manage.py sync_cleanup_from_mysql --confirm          # Sin pedir confirmación
"""

import os
from decimal import Decimal
from datetime import datetime
from collections import defaultdict
import sys
from pathlib import Path

# Cargar variables de entorno
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction, connection

from app.models import (
    Empresa, Sucursal, Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla, Movimientos_Producto, Dte, Dte_Productos,
    Dte_Detalle_Pago, Vendedor
)


# ============================================================================
# CONFIGURACIÓN MYSQL
# ============================================================================

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Limpia datos de PostgreSQL que NO existen en MySQL (sincronización inversa)'

    def __init__(self):
        super().__init__()
        self.mysql_conn = None
        self.dry_run = False
        self.auto_confirm = False
        self.stats = defaultdict(int)
        self.start_time = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular limpieza sin eliminar datos (RECOMENDADO ejecutar primero)'
        )
        parser.add_argument(
            '--tables',
            nargs='+',
            help='Limpiar solo tablas específicas (ej: productos movimientos dtes)'
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='No pedir confirmación antes de eliminar'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.auto_confirm = options.get('confirm', False)
        specific_tables = options.get('tables', None)
        
        self.start_time = datetime.now()

        # Banner de advertencia
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.WARNING('⚠️  SYNC CLEANUP - ELIMINAR DATOS QUE NO EXISTEN EN MYSQL'))
        self.stdout.write('='*70)
        
        if self.dry_run:
            self.stdout.write(self.style.SUCCESS('[DRY-RUN] Modo simulación - NO se eliminarán datos'))
        else:
            self.stdout.write(self.style.ERROR('[PRODUCCIÓN] Los datos SE ELIMINARÁN'))

        # Validar configuración MySQL
        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            self.stdout.write(self.style.ERROR('[ERROR] Faltan variables MySQL en .env'))
            return

        # Conectar a MySQL
        try:
            self.mysql_conn = self.connect_mysql()
            self.stdout.write(self.style.SUCCESS('✓ Conexión MySQL establecida'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error MySQL: {e}'))
            return

        # Orden de limpieza (inverso a la creación - primero dependientes)
        cleanup_order = [
            ('dte_detalle_pago', self.cleanup_dte_detalle_pago),
            ('dte_productos', self.cleanup_dte_productos),
            ('movimientos', self.cleanup_movimientos),
            ('dtes', self.cleanup_dtes),
            ('producto_talla', self.cleanup_producto_talla),
            ('productos', self.cleanup_productos),
            ('vendedores', self.cleanup_vendedores),
            ('sucursales', self.cleanup_sucursales),
            ('clientes', self.cleanup_clientes),
        ]

        if specific_tables:
            cleanup_order = [
                (name, func) for name, func in cleanup_order 
                if name in specific_tables
            ]

        # Primera pasada: Analizar qué se eliminaría
        self.stdout.write('\n📊 ANÁLISIS DE DATOS A ELIMINAR:')
        self.stdout.write('-'*70)
        
        total_a_eliminar = 0
        for table_name, cleanup_func in cleanup_order:
            count = cleanup_func(analyze_only=True)
            self.stats[f'{table_name}_a_eliminar'] = count
            total_a_eliminar += count
            
            if count > 0:
                self.stdout.write(self.style.WARNING(f'  • {table_name:25s}: {count:>8,} registros'))
            else:
                self.stdout.write(f'  • {table_name:25s}: {count:>8,} registros')

        self.stdout.write('-'*70)
        self.stdout.write(f'  TOTAL A ELIMINAR: {total_a_eliminar:>8,} registros')
        self.stdout.write('')

        if total_a_eliminar == 0:
            self.stdout.write(self.style.SUCCESS('✓ No hay datos para eliminar. Base de datos sincronizada.'))
            self.mysql_conn.close()
            return

        if self.dry_run:
            self.stdout.write(self.style.SUCCESS('\n[DRY-RUN] Simulación completada. Ejecuta sin --dry-run para eliminar.'))
            self.mysql_conn.close()
            return

        # Pedir confirmación
        if not self.auto_confirm:
            self.stdout.write(self.style.ERROR(f'\n⚠️  Se eliminarán {total_a_eliminar:,} registros de PostgreSQL'))
            confirm = input('¿Continuar? Escribe "SI" para confirmar: ')
            if confirm.upper() != 'SI':
                self.stdout.write(self.style.WARNING('Operación cancelada.'))
                self.mysql_conn.close()
                return

        # Ejecutar limpieza
        self.stdout.write('\n🗑️  EJECUTANDO LIMPIEZA:')
        self.stdout.write('-'*70)
        
        try:
            with transaction.atomic():
                for table_name, cleanup_func in cleanup_order:
                    count = cleanup_func(analyze_only=False)
                    self.stats[f'{table_name}_eliminados'] = count
                    if count > 0:
                        self.stdout.write(self.style.SUCCESS(f'  ✓ {table_name:25s}: {count:>8,} eliminados'))

            self.show_statistics()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'[ERROR] {e}'))
        finally:
            if self.mysql_conn:
                self.mysql_conn.close()

    def connect_mysql(self):
        """Conexión MySQL"""
        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=300,
            autocommit=True,
        )

    # ========================================================================
    # FUNCIONES DE LIMPIEZA POR TABLA
    # ========================================================================

    def cleanup_clientes(self, analyze_only=True):
        """
        Elimina empresas/clientes de PostgreSQL que no existen en MySQL.
        PROTEGE las empresas principales (emisoras).
        """
        # RUTs protegidos (empresas emisoras, no deben eliminarse)
        ruts_protegidos = {'78503140-7', '76104936-4', '7397811-4'}
        
        # Obtener RUTs de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT DISTINCT rut FROM cliente WHERE rut IS NOT NULL')
        ruts_mysql = set(row[0] for row in cursor.fetchall())
        cursor.close()
        
        # Obtener empresas de PostgreSQL que NO están en MySQL y NO son protegidas
        empresas_pg = Empresa.objects.exclude(rut__in=ruts_mysql).exclude(rut__in=ruts_protegidos)
        
        # Excluir empresas que tienen sucursales (son emisoras)
        # La relación inversa se llama 'sucursales_app'
        empresas_pg = empresas_pg.filter(sucursales_app__isnull=True)
        
        count = empresas_pg.count()
        
        if not analyze_only and count > 0:
            empresas_pg.delete()
        
        return count

    def cleanup_sucursales(self, analyze_only=True):
        """
        Elimina sucursales de PostgreSQL que no existen en MySQL.
        """
        # Obtener alias de sucursales de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT DISTINCT bodega FROM bodegas WHERE bodega IS NOT NULL')
        alias_mysql = set(row[0] for row in cursor.fetchall())
        cursor.close()
        
        # Sucursales de PostgreSQL que NO están en MySQL
        sucursales_pg = Sucursal.objects.exclude(alias__in=alias_mysql)
        count = sucursales_pg.count()
        
        if not analyze_only and count > 0:
            sucursales_pg.delete()
        
        return count

    def cleanup_vendedores(self, analyze_only=True):
        """
        Elimina vendedores de PostgreSQL que no existen en MySQL.
        """
        # Obtener IDs/RUTs de vendedores de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute("SELECT DISTINCT rut FROM vendedores WHERE rut IS NOT NULL AND rut != ''")
        ruts_mysql = set(row[0] for row in cursor.fetchall())
        
        cursor.execute('SELECT DISTINCT codigo_interno FROM vendedores WHERE codigo_interno IS NOT NULL')
        codigos_mysql = set(str(row[0]) for row in cursor.fetchall())
        cursor.close()
        
        # Vendedores de PostgreSQL que NO están en MySQL
        # (ni por RUT ni por codigo_vendedor)
        vendedores_pg = Vendedor.objects.exclude(
            rut__in=ruts_mysql
        ).exclude(
            codigo_vendedor__in=codigos_mysql
        ).exclude(
            codigo_vendedor__startswith='MIG-'  # Migrados sin código
        )
        
        count = vendedores_pg.count()
        
        if not analyze_only and count > 0:
            vendedores_pg.delete()
        
        return count

    def cleanup_productos(self, analyze_only=True):
        """
        Elimina productos de PostgreSQL que no existen en MySQL.
        """
        # Obtener artículos únicos de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT DISTINCT articulo FROM talla WHERE articulo IS NOT NULL')
        articulos_mysql = set(row[0] for row in cursor.fetchall())
        cursor.close()
        
        # Productos de PostgreSQL que NO están en MySQL
        productos_pg = Producto.objects.exclude(articulo__in=articulos_mysql)
        count = productos_pg.count()
        
        if not analyze_only and count > 0:
            productos_pg.delete()
        
        return count

    def cleanup_producto_talla(self, analyze_only=True):
        """
        Elimina producto_talla (SKUs) de PostgreSQL que no existen en MySQL.
        """
        # Obtener SKUs de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT DISTINCT codigo_asociado FROM talla WHERE codigo_asociado IS NOT NULL')
        skus_mysql = set(int(row[0]) for row in cursor.fetchall() if row[0])
        cursor.close()
        
        # Producto_Talla de PostgreSQL que NO están en MySQL
        producto_talla_pg = Producto_Talla.objects.exclude(sku__in=skus_mysql)
        count = producto_talla_pg.count()
        
        if not analyze_only and count > 0:
            producto_talla_pg.delete()
        
        return count

    def cleanup_movimientos(self, analyze_only=True):
        """
        Elimina movimientos de PostgreSQL que no existen en MySQL.
        Solo elimina los que NO tienen referencia_externa (no fueron migrados).
        """
        # Obtener IDs de movimientos de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT DISTINCT id FROM movimiento_productos')
        ids_mysql = set(f'MIG:{row[0]}' for row in cursor.fetchall())
        cursor.close()
        
        # Movimientos de PostgreSQL que:
        # 1. Tienen referencia_externa pero NO está en MySQL, O
        # 2. NO tienen referencia_externa (creados localmente)
        movimientos_migrados = Movimientos_Producto.objects.filter(
            referencia_externa__startswith='MIG:'
        ).exclude(
            referencia_externa__in=ids_mysql
        )
        
        movimientos_locales = Movimientos_Producto.objects.filter(
            referencia_externa__isnull=True
        ) | Movimientos_Producto.objects.filter(
            referencia_externa=''
        )
        
        count_migrados = movimientos_migrados.count()
        count_locales = movimientos_locales.count()
        count = count_migrados + count_locales
        
        if not analyze_only and count > 0:
            movimientos_migrados.delete()
            movimientos_locales.delete()
        
        return count

    def cleanup_dtes(self, analyze_only=True):
        """
        Elimina DTEs de PostgreSQL que no existen en MySQL.
        Compara por (numero_documento, monto_total).
        """
        # Obtener DTEs de MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT n_documento, monto_total FROM dte')
        dtes_mysql = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        
        # También incluir de ventas (DTEs creados desde ventas)
        cursor.execute('SELECT DISTINCT n_documento, sub_total FROM ventas')
        dtes_ventas = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        cursor.close()
        
        dtes_mysql = dtes_mysql | dtes_ventas
        
        # DTEs de PostgreSQL
        dtes_pg = Dte.objects.all()
        
        # Encontrar los que NO están en MySQL
        ids_a_eliminar = []
        for dte in dtes_pg.iterator():
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            if key not in dtes_mysql:
                ids_a_eliminar.append(dte.id)
        
        count = len(ids_a_eliminar)
        
        if not analyze_only and count > 0:
            Dte.objects.filter(id__in=ids_a_eliminar).delete()
        
        return count

    def cleanup_dte_productos(self, analyze_only=True):
        """
        Elimina DTE_Productos de PostgreSQL cuyos DTEs no existen en MySQL.
        """
        # Primero identificar DTEs que no existen en MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT n_documento, monto_total FROM dte')
        dtes_mysql = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        
        cursor.execute('SELECT DISTINCT n_documento, sub_total FROM ventas')
        dtes_ventas = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        cursor.close()
        
        dtes_mysql = dtes_mysql | dtes_ventas
        
        # DTEs de PostgreSQL que NO están en MySQL
        dtes_pg_invalidos = []
        for dte in Dte.objects.all().iterator():
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            if key not in dtes_mysql:
                dtes_pg_invalidos.append(dte.id)
        
        # DTE_Productos asociados a esos DTEs
        dte_productos_pg = Dte_Productos.objects.filter(dte_id__in=dtes_pg_invalidos)
        count = dte_productos_pg.count()
        
        if not analyze_only and count > 0:
            dte_productos_pg.delete()
        
        return count

    def cleanup_dte_detalle_pago(self, analyze_only=True):
        """
        Elimina Dte_Detalle_Pago de PostgreSQL cuyos DTEs no existen en MySQL.
        """
        # Identificar DTEs que no existen en MySQL
        cursor = self.mysql_conn.cursor()
        cursor.execute('SELECT n_documento, monto_total FROM dte')
        dtes_mysql = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        
        cursor.execute('SELECT DISTINCT n_documento, sub_total FROM ventas')
        dtes_ventas = set((row[0], int(row[1] or 0)) for row in cursor.fetchall())
        cursor.close()
        
        dtes_mysql = dtes_mysql | dtes_ventas
        
        # DTEs de PostgreSQL que NO están en MySQL
        dtes_pg_invalidos = []
        for dte in Dte.objects.all().iterator():
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            if key not in dtes_mysql:
                dtes_pg_invalidos.append(dte.id)
        
        # Pagos asociados a esos DTEs
        pagos_pg = Dte_Detalle_Pago.objects.filter(dte_id__in=dtes_pg_invalidos)
        count = pagos_pg.count()
        
        if not analyze_only and count > 0:
            pagos_pg.delete()
        
        return count

    # ========================================================================
    # ESTADÍSTICAS
    # ========================================================================

    def show_statistics(self):
        """Muestra resumen final"""
        elapsed = datetime.now() - self.start_time
        
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS('✓ LIMPIEZA COMPLETADA'))
        self.stdout.write('='*70)
        
        total_eliminados = sum(
            self.stats.get(f'{t}_eliminados', 0) 
            for t in ['clientes', 'sucursales', 'vendedores', 'productos', 
                      'producto_talla', 'movimientos', 'dtes', 'dte_productos', 
                      'dte_detalle_pago']
        )
        
        self.stdout.write(f'  Total registros eliminados: {total_eliminados:,}')
        self.stdout.write(f'  Tiempo total: {elapsed}')
        self.stdout.write('='*70)
