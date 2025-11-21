"""
Django management command para migrar datos desde MySQL (Laravel) a PostgreSQL (Django)
VERSIÓN OPTIMIZADA - Mayor velocidad y feedback detallado

Sistema origen: Laravel + MySQL (inventario de calzado/ropa)
Sistema destino: Django + PostgreSQL (RetailMind)

Uso:
    python manage.py migrate_from_laravel
    python manage.py migrate_from_laravel --dry-run
    python manage.py migrate_from_laravel --tables productos movimientos
    python manage.py migrate_from_laravel --batch-size 2000
"""

import os
import logging
from decimal import Decimal
from datetime import datetime, time
from collections import defaultdict
import sys

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app.models import (
    Empresa, Sucursal, Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla, Movimientos_Producto, Dte, Dte_Productos
)


logger = logging.getLogger(__name__)

from django.conf import settings
import pathlib

PROJECT_ROOT = pathlib.Path(settings.BASE_DIR).parent
ERROR_LOG_FILE = PROJECT_ROOT / 'migration_errors.log'


# ============================================================================
# CONFIGURACIÓN MYSQL
# ============================================================================

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


# ============================================================================
# MAPEOS HARDCODED
# ============================================================================

EMPRESA_RUT_MAP = {
    'PAO1': '78503140-7',
    'PAO2': '78503140-7',
    'PAO3': '78503140-7',
    'PAO4': '78503140-7',
    'EDEL': '78503140-7',
    'GILD': '78503140-7',
    'NICK1': '76104936-4',
    'NICK2': '76104936-4',
}

TIPO_MOVIMIENTO_MAP = {
    'Ingreso': 'INGRESO',
    'Egreso': 'EGRESO',
    'Egresso': 'EGRESO',
    'Traspaso': 'TRASPASO',
}

CONCEPTO_MAP = {
    'Creacion': 'INGRESO_INICIAL',
    'VentaXInterna': 'TRASPASO_SUCURSAL',
    'Venta': 'VENTA_PUBLICO',
    'Compra': 'RECEPCION_COMPRA',
    'Ajuste': 'AJUSTE_INVENTARIO',
    'Devolucion': 'DEVOLUCION_CLIENTE',
}

TIPO_DOCUMENTO_MAP = {
    '33': 'FACTURA ELECTRONICA',
    '39': 'BOLETA ELECTRONICA',
    '52': 'GUIA',
    '61': 'NOTA DE CREDITO',
    '56': 'NOTA DE DEBITO',
    '34': 'FACTURA EXENTA',
}

ESTADO_DTE_MAP = {
    'Vigente': 'EMITIDO',
    'Anulado': 'ANULADO',
    'Recepcionado': 'RECEPCIONADO_COMPLETO',
}


# ============================================================================
# COMANDO PRINCIPAL
# ============================================================================

class Command(BaseCommand):
    help = 'Migra datos desde MySQL Laravel a PostgreSQL Django (OPTIMIZADO)'

    def __init__(self):
        super().__init__()
        self.mysql_conn = None
        self.dry_run = False
        self.batch_size = 2000  # ⚡ Aumentado a 2000
        self.limit = None
        self.stats = defaultdict(int)
        self.errors = []
        self.start_time = None
        self.error_file = None
        
        # 🚀 CACHÉS OPTIMIZADOS
        self.cache_sucursales = {}
        self.cache_atributos = {}
        self.cache_categorias = {}
        self.cache_productos = {}
        self.cache_producto_talla = {}  # ✅ NUEVO
        self.cache_empresas_rut = {}  # ✅ NUEVO
        
        # 📊 ESTADÍSTICAS DE PROGRESO
        self.current_table = ''
        self.table_start_time = None
        self.last_progress_update = None

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular migración sin guardar datos'
        )
        parser.add_argument(
            '--tables',
            nargs='+',
            help='Migrar solo tablas específicas'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=2000,
            help='Tamaño de lote para inserción'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limitar cantidad de registros por tabla'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.batch_size = options.get('batch_size', 2000)
        self.limit = options.get('limit', None)
        specific_tables = options.get('tables', None)
        
        self.start_time = datetime.now()
        
        # Abrir archivo de errores
        self.error_file = open(ERROR_LOG_FILE, 'w', encoding='utf-8')
        self.error_file.write(f'=== LOG DE ERRORES - MIGRACIÓN OPTIMIZADA ===\n')
        self.error_file.write(f'Fecha: {self.start_time}\n')
        self.error_file.write(f'{"="*70}\n\n')

        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] Modo simulación activado'))
        
        if self.limit:
            self.stdout.write(self.style.WARNING(f'[LÍMITE] Máximo {self.limit} registros/tabla'))

        # Validar configuración
        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            self.stdout.write(self.style.ERROR('[ERROR] Faltan variables MySQL'))
            return

        # Conectar a MySQL
        try:
            self.mysql_conn = self.connect_mysql()
            self.stdout.write(self.style.SUCCESS('✓ Conexión MySQL establecida'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error MySQL: {e}'))
            return
        
        # Pre-cargar cachés
        self.stdout.write('\n📦 Cargando cachés...')
        self.preload_caches()

        # Orden de migración
        migration_order = [
            ('empresas', self.migrate_empresas_principales),
            ('clientes', self.migrate_clientes),
            ('sucursales', self.migrate_sucursales),
            ('atributos', self.migrate_atributos),
            ('categorias', self.migrate_categorias),
            ('productos', self.migrate_productos),
            ('producto_talla', self.migrate_producto_talla),
            ('movimientos', self.migrate_movimientos),
            ('dtes', self.migrate_dtes),
        ]

        if specific_tables:
            migration_order = [
                (name, func) for name, func in migration_order 
                if name in specific_tables
            ]

        # Ejecutar migraciones
        try:
            if not self.dry_run:
                with transaction.atomic():
                    for table_name, migrate_func in migration_order:
                        self.current_table = table_name
                        self.table_start_time = datetime.now()
                        self.stdout.write(f'\n{"="*70}')
                        migrate_func()
            else:
                for table_name, migrate_func in migration_order:
                    self.current_table = table_name
                    self.table_start_time = datetime.now()
                    self.stdout.write(f'\n{"="*70}')
                    migrate_func()

            self.stdout.write(f'\n{"="*70}')
            self.show_statistics()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'[ERROR CRÍTICO] {e}'))
            logger.exception('Error durante migración')
        finally:
            if self.mysql_conn:
                self.mysql_conn.close()
            if self.error_file:
                self.error_file.close()
                self.stdout.write(self.style.SUCCESS(f'\n📄 Errores: {ERROR_LOG_FILE}'))

    # ========================================================================
    # CONEXIÓN Y CACHÉS
    # ========================================================================

    def connect_mysql(self):
        """Conexión MySQL optimizada"""
        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=600,  # 10 minutos
            autocommit=True,
            get_warnings=False,
            use_pure=False,  # ⚡ Usa C extension (más rápido)
        )

    def preload_caches(self):
        """Pre-carga TODOS los cachés necesarios"""
        try:
            # Sucursales
            for sucursal in Sucursal.objects.select_related('empresa').all():
                self.cache_sucursales[sucursal.alias] = sucursal
            self.stdout.write(f'  ✓ {len(self.cache_sucursales)} sucursales')
            
            # Categorías
            for categoria in Categoria.objects.all():
                self.cache_categorias[categoria.nombre] = categoria
            self.stdout.write(f'  ✓ {len(self.cache_categorias)} categorías')
            
            # Empresas por RUT
            for empresa in Empresa.objects.all():
                self.cache_empresas_rut[empresa.rut] = empresa
            self.stdout.write(f'  ✓ {len(self.cache_empresas_rut)} empresas')
            
            # ⚡ CRITICAL: Pre-cargar TODOS los producto_talla en memoria
            self.stdout.write('  ⏳ Cargando productos_talla... (puede tardar)')
            for pt in Producto_Talla.objects.select_related(
                'producto__sucursal',
                'producto__atributo1__atributo',
                'producto__atributo2__atributo',
                'producto__categoria'
            ).all():
                self.cache_producto_talla[str(pt.sku)] = pt
            self.stdout.write(f'  ✓ {len(self.cache_producto_talla)} productos_talla')
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠ Error cachés: {e}'))

    # ========================================================================
    # PROGRESS TRACKING
    # ========================================================================

    def show_progress(self, current, total, extra_info=''):
        """
        Muestra progreso detallado con:
        - Porcentaje con 2 decimales
        - Barra ASCII
        - Velocidad (reg/seg)
        - ETA
        """
        now = datetime.now()
        
        # Solo actualizar cada 0.5 segundos
        if self.last_progress_update:
            if (now - self.last_progress_update).total_seconds() < 0.5:
                return
        self.last_progress_update = now
        
        percentage = (current / total) * 100 if total > 0 else 0
        
        # Barra de progreso ASCII
        bar_length = 40
        filled = int(bar_length * current / total) if total > 0 else 0
        bar = '█' * filled + '░' * (bar_length - filled)
        
        # Velocidad y ETA
        elapsed = (now - self.table_start_time).total_seconds()
        speed = current / elapsed if elapsed > 0 else 0
        eta_seconds = (total - current) / speed if speed > 0 else 0
        eta = f'{int(eta_seconds)}s' if eta_seconds < 60 else f'{int(eta_seconds/60)}m'
        
        # Limpiar línea y escribir
        sys.stdout.write('\r' + ' ' * 120 + '\r')
        progress_line = (
            f'[{bar}] {percentage:5.2f}% '
            f'({current:,}/{total:,}) '
            f'│ {speed:.0f} reg/s '
            f'│ ETA: {eta} '
            f'{extra_info}'
        )
        sys.stdout.write(progress_line)
        sys.stdout.flush()

    # ========================================================================
    # FUNCIONES AUXILIARES
    # ========================================================================

    def get_or_create_atributo_opcion(self, atributo_nombre, valor):
        """Busca o crea atributo con caché"""
        if not valor:
            valor = 'SIN ESPECIFICAR'
        
        # Usar caché
        cache_key = f'{atributo_nombre}:{valor}'
        if cache_key in self.cache_atributos:
            return self.cache_atributos[cache_key]

        atributo, _ = Productos_Atributos.objects.get_or_create(
            nombre=atributo_nombre,
            defaults={'descripcion': f'Atributo {atributo_nombre}'}
        )
        opcion, _ = AtributoOpcion.objects.get_or_create(
            atributo=atributo,
            valor=valor
        )
        
        self.cache_atributos[cache_key] = opcion
        return opcion

    def find_producto_talla_fast(self, codigo_asociado):
        """⚡ Búsqueda ultra-rápida usando caché en memoria"""
        return self.cache_producto_talla.get(str(codigo_asociado))

    def safe_decimal(self, value, default=Decimal('0')):
        if value is None:
            return default
        try:
            return Decimal(str(value))
        except:
            return default

    def safe_int(self, value, default=0):
        if value is None:
            return default
        try:
            return int(value)
        except:
            return default

    def safe_date(self, value):
        if value is None:
            return timezone.now()
        if isinstance(value, datetime):
            return value
        try:
            return timezone.make_aware(value) if timezone.is_naive(value) else value
        except:
            return timezone.now()

    def log_error(self, message, log_every=100):
        """Registra error (throttled)"""
        self.errors.append(message)
        
        # Solo escribir cada N errores
        if len(self.errors) % log_every == 0:
            if self.error_file:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.error_file.write(f'[{timestamp}] {message}\n')
                self.error_file.flush()

    # ========================================================================
    # MIGRACIONES
    # ========================================================================

    def migrate_empresas_principales(self):
        """Crea empresas principales"""
        self.stdout.write('🏢 Migrando empresas principales...')

        empresas_data = [
            {
                'rut': '78503140-7',
                'nombre': 'Vicent Paola',
                'razon_social': 'Vicent Paola',
                'nombre_fantasia': 'Vicent Paola',
                'giro': 'Comercio',
                'direccion': '',
                'comuna': '',
                'ciudad': '',
                'correoVendedor': '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
            },
            {
                'rut': '76104936-4',
                'nombre': 'Nicolas',
                'razon_social': 'Nicolas',
                'nombre_fantasia': 'Nicolas',
                'giro': 'Comercio',
                'direccion': '',
                'comuna': '',
                'ciudad': '',
                'correoVendedor': '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
            }
        ]

        count = 0
        for data in empresas_data:
            if not self.dry_run:
                empresa, created = Empresa.objects.get_or_create(
                    rut=data['rut'],
                    defaults=data
                )
                if created:
                    count += 1
                    self.cache_empresas_rut[data['rut']] = empresa
            else:
                count += 1

        self.stats['empresas'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} empresas creadas'))

    def migrate_clientes(self):
        """Migra clientes (OPTIMIZADO)"""
        self.stdout.write('👥 Migrando clientes...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM cliente')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT rut, razon_social, nombre_fantasia, giro, direccion, comuna, fono, email
            FROM cliente
            ORDER BY ID
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        batch = []
        ruts_existentes = set(self.cache_empresas_rut.keys())

        for idx, row in enumerate(cursor, 1):
            if not row['rut'] or row['rut'] in ruts_existentes:
                continue

            cliente_data = {
                'rut': row['rut'],
                'razon_social': row['razon_social'] or 'Sin nombre',
                'nombre_fantasia': row['nombre_fantasia'] or 'Sin nombre',
                'nombre': row['razon_social'] or 'Sin nombre',
                'giro': row['giro'] or '',
                'direccion': row['direccion'] or '',
                'comuna': row['comuna'] or '',
                'ciudad': '',
                'contacto1': row['fono'] or '',
                'correoVendedor': row['email'] or '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
            }

            if not self.dry_run:
                batch.append(Empresa(**cliente_data))
                ruts_existentes.add(row['rut'])
                
                if len(batch) >= self.batch_size:
                    Empresa.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 100 == 0:
                self.show_progress(idx, total, f'│ {count} insertados')

        if batch and not self.dry_run:
            Empresa.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['clientes'] = count
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} clientes migrados'))

    def migrate_sucursales(self):
        """Migra sucursales"""
        self.stdout.write('🏪 Migrando sucursales...')

        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('SELECT bodega, sucursal, rut_empresa FROM bodegas ORDER BY ID')

        count = 0
        for row in cursor:
            alias = row['bodega'] or row['sucursal']
            if not alias:
                continue

            rut_empresa = EMPRESA_RUT_MAP.get(alias, '78503140-7')
            empresa = self.cache_empresas_rut.get(rut_empresa)
            
            if not empresa:
                continue

            if not self.dry_run:
                sucursal, created = Sucursal.objects.get_or_create(
                    alias=alias,
                    empresa=empresa,
                    defaults={'direccion': alias}
                )
                if created:
                    count += 1
                    self.cache_sucursales[alias] = sucursal
            else:
                count += 1

        cursor.close()
        self.stats['sucursales'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} sucursales creadas'))

    def migrate_atributos(self):
        """Migra atributos"""
        self.stdout.write('🏷️  Migrando atributos...')

        cursor = self.mysql_conn.cursor(dictionary=True)

        # Obtener valores únicos
        cursor.execute('SELECT DISTINCT marca FROM talla WHERE marca IS NOT NULL')
        marcas = [row['marca'] for row in cursor.fetchall()]
        
        cursor.execute('SELECT DISTINCT color FROM talla WHERE color IS NOT NULL')
        colores = [row['color'] for row in cursor.fetchall()]
        
        cursor.execute('SELECT DISTINCT sexo FROM talla WHERE sexo IS NOT NULL')
        sexos = [row['sexo'] for row in cursor.fetchall()]

        count = 0
        for marca in marcas:
            self.get_or_create_atributo_opcion('Marca', marca)
            count += 1
        
        for color in colores:
            self.get_or_create_atributo_opcion('Color', color)
            count += 1
        
        for sexo in sexos:
            self.get_or_create_atributo_opcion('Sexo', sexo)
            count += 1

        cursor.close()
        self.stats['atributos'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} opciones creadas'))

    def migrate_categorias(self):
        """Migra categorías"""
        self.stdout.write('📂 Migrando categorías...')

        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('SELECT DISTINCT familia FROM talla WHERE familia IS NOT NULL')

        count = 0
        for row in cursor:
            familia = row['familia']
            if not self.dry_run:
                categoria, created = Categoria.objects.get_or_create(nombre=familia)
                if created:
                    count += 1
                    self.cache_categorias[familia] = categoria
            else:
                count += 1

        cursor.close()
        self.stats['categorias'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} categorías creadas'))

    def migrate_productos(self):
        """Migra productos (OPTIMIZADO con bulk)"""
        self.stdout.write('📦 Migrando productos (agrupación)...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        query = '''
            SELECT 
                articulo, descripcion, marca, color, sexo, familia, alias,
                MIN(costo) as costo, MIN(precioventapublico) as precioventa
            FROM talla
            WHERE articulo IS NOT NULL
            GROUP BY articulo, marca, color, descripcion, sexo, familia, alias
            ORDER BY articulo
        '''
        
        if self.limit:
            query += f' LIMIT {self.limit}'
        
        cursor.execute(query)
        productos = cursor.fetchall()
        total = len(productos)

        count = 0
        batch = []
        productos_existentes = set(
            Producto.objects.values_list('articulo', 'sucursal__alias', 'atributo1__valor', 'atributo2__valor')
        )

        for idx, row in enumerate(productos, 1):
            articulo = row['articulo']
            alias = row['alias']
            
            sucursal = self.cache_sucursales.get(alias)
            if not sucursal:
                continue

            marca_opcion = self.get_or_create_atributo_opcion('Marca', row['marca'])
            color_opcion = self.get_or_create_atributo_opcion('Color', row['color'])
            sexo_opcion = self.get_or_create_atributo_opcion('Sexo', row['sexo'])
            categoria = self.cache_categorias.get(row['familia'])

            # Verificar existencia
            prod_key = (articulo, alias, marca_opcion.valor, color_opcion.valor)
            if prod_key in productos_existentes:
                continue

            if not self.dry_run:
                producto = Producto(
                    articulo=articulo,
                    descripcion=row['descripcion'] or articulo,
                    precioventa=self.safe_int(row['precioventa']),
                    costo=self.safe_int(row['costo']),
                    sobreprecio=0,
                    sucursal=sucursal,
                    categoria=categoria,
                    atributo1=marca_opcion,
                    atributo2=color_opcion,
                    atributo3=sexo_opcion,
                )
                batch.append(producto)
                productos_existentes.add(prod_key)
                
                if len(batch) >= self.batch_size:
                    Producto.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 100 == 0:
                self.show_progress(idx, total, f'│ {count} creados')

        if batch and not self.dry_run:
            Producto.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['productos'] = count
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} productos creados'))

    def migrate_producto_talla(self):
        """Migra producto_talla (OPTIMIZADO - PERMITE SKUs EN MÚLTIPLES SUCURSALES)"""
        self.stdout.write('🔢 Migrando productos_talla (SKUs)...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM talla WHERE codigo_asociado IS NOT NULL')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT codigo_asociado, articulo, marca, color, size, stock, alias
            FROM talla
            WHERE codigo_asociado IS NOT NULL
            ORDER BY articulo, size
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        skipped = 0
        batch = []

        for idx, row in enumerate(cursor, 1):
            sku = self.safe_int(row['codigo_asociado'])
            alias = row['alias']
            
            if not sku:
                skipped += 1
                continue

            # Buscar producto
            marca_opcion = self.get_or_create_atributo_opcion('Marca', row['marca'])
            color_opcion = self.get_or_create_atributo_opcion('Color', row['color'])
            sucursal = self.cache_sucursales.get(alias)
            
            if not sucursal:
                skipped += 1
                continue

            producto = Producto.objects.filter(
                articulo=row['articulo'],
                sucursal=sucursal,
                atributo1=marca_opcion,
                atributo2=color_opcion
            ).first()

            if not producto:
                if skipped % 500 == 0:
                    self.log_error(f'Producto no encontrado para SKU {sku}', log_every=500)
                skipped += 1
                continue

            if not self.dry_run:
                # ✅ CORRECCIÓN: Verificar si existe esta combinación específica (SKU + Producto)
                # Permite el mismo SKU en diferentes sucursales
                if not Producto_Talla.objects.filter(sku=sku, producto=producto).exists():
                    talla_obj = Producto_Talla(
                        sku=sku,
                        producto=producto,
                        talla=row['size'] or 'U',
                        stock=self.safe_int(row['stock']),
                    )
                    batch.append(talla_obj)
                    
                    if len(batch) >= self.batch_size:
                        Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)
                        count += len(batch)
                        batch = []
                else:
                    skipped += 1
            else:
                count += 1

            if idx % 100 == 0:
                self.show_progress(idx, total, f'│ {count} OK, {skipped} skip')

        if batch and not self.dry_run:
            Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['producto_talla'] = count
        self.stats['producto_talla_skipped'] = skipped
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} SKUs migrados ({skipped} omitidos)'))

    def migrate_movimientos(self):
        """⚡ Migra movimientos (ULTRA-OPTIMIZADO con caché en memoria)"""
        self.stdout.write('📊 Migrando movimientos...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM movimiento_productos')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT 
                codigo_asociado, cantidad, fecha, responsable,
                tipo_movimiento, concepto, costo, precio_salida, N_documento
            FROM movimiento_productos
            ORDER BY fecha, id
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        skipped = 0
        batch = []
        error_log_interval = 500  # Logear cada 500 errores

        for idx, row in enumerate(cursor, 1):
            codigo_asociado = self.safe_int(row['codigo_asociado'])
            
            # ⚡ BÚSQUEDA ULTRA-RÁPIDA en caché de memoria
            producto_talla = self.find_producto_talla_fast(codigo_asociado)

            if not producto_talla:
                skipped += 1
                if skipped % error_log_interval == 0:
                    self.log_error(f'SKU no encontrado (error #{skipped}): {codigo_asociado}', log_every=error_log_interval)
                continue

            tipo_movimiento = TIPO_MOVIMIENTO_MAP.get(row['tipo_movimiento'], 'INGRESO')
            concepto = CONCEPTO_MAP.get(row['concepto'], 'AJUSTE_INVENTARIO')

            if not self.dry_run:
                movimiento = Movimientos_Producto(
                    ProductoTalla=producto_talla,
                    tipo_movimiento=tipo_movimiento,
                    concepto=concepto,
                    cantidad=self.safe_int(row['cantidad']),
                    costo=self.safe_int(row['costo']),
                    precio=self.safe_int(row['precio_salida']),
                    fecha=self.safe_date(row['fecha']),
                    responsable=row['responsable'] or 'Sistema',
                    referencia_externa=row['N_documento'] or '',
                    estado='COMPLETADO',
                )
                batch.append(movimiento)
                
                if len(batch) >= self.batch_size:
                    Movimientos_Producto.objects.bulk_create(batch)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 100 == 0:
                self.show_progress(idx, total, f'│ {count} OK, {skipped} skip')

        if batch and not self.dry_run:
            Movimientos_Producto.objects.bulk_create(batch)
            count += len(batch)

        cursor.close()
        self.stats['movimientos'] = count
        self.stats['movimientos_skipped'] = skipped
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} movimientos migrados ({skipped} omitidos)'))

    def migrate_dtes(self):
        """Migra DTEs"""
        self.stdout.write('📄 Migrando DTEs...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM dte')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT 
                tipo_dte, folio, fecha_emision, rut_receptor, razon_social_receptor,
                monto_neto, monto_exento, monto_iva, monto_total, estado, alias
            FROM dte
            ORDER BY fecha_emision, folio
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        skipped = 0

        for idx, row in enumerate(cursor, 1):
            alias = row['alias']
            sucursal = self.cache_sucursales.get(alias)
            
            if not sucursal:
                skipped += 1
                continue

            empresa_receptora = self.cache_empresas_rut.get(row['rut_receptor'])
            tipo_documento = TIPO_DOCUMENTO_MAP.get(str(row['tipo_dte']), 'BOLETA ELECTRONICA')
            estado = ESTADO_DTE_MAP.get(row['estado'], 'EMITIDO')

            if not self.dry_run:
                dte, created = Dte.objects.get_or_create(
                    tipo_documento=tipo_documento,
                    folio=self.safe_int(row['folio']),
                    sucursal=sucursal,
                    defaults={
                        'fecha_emision': self.safe_date(row['fecha_emision']),
                        'empresa_receptora': empresa_receptora,
                        'razon_social_receptor': row['razon_social_receptor'] or '',
                        'monto_neto': self.safe_decimal(row['monto_neto']),
                        'monto_exento': self.safe_decimal(row['monto_exento']),
                        'monto_iva': self.safe_decimal(row['monto_iva']),
                        'monto_total': self.safe_decimal(row['monto_total']),
                        'estado': estado,
                    }
                )
                if created:
                    count += 1
                else:
                    skipped += 1
            else:
                count += 1

            if idx % 100 == 0:
                self.show_progress(idx, total, f'│ {count} OK, {skipped} skip')

        cursor.close()
        self.stats['dtes'] = count
        self.stats['dtes_skipped'] = skipped
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} DTEs migrados ({skipped} omitidos)'))

    # ========================================================================
    # ESTADÍSTICAS FINALES
    # ========================================================================

    def show_statistics(self):
        """Muestra estadísticas detalladas"""
        elapsed = datetime.now() - self.start_time
        
        self.stdout.write(self.style.SUCCESS('\n\n📊 RESUMEN DE MIGRACIÓN'))
        self.stdout.write('='*70)
        
        tabla_stats = [
            ('Empresas principales', self.stats.get('empresas', 0)),
            ('Clientes', self.stats.get('clientes', 0)),
            ('Sucursales', self.stats.get('sucursales', 0)),
            ('Atributos', self.stats.get('atributos', 0)),
            ('Categorías', self.stats.get('categorias', 0)),
            ('Productos (agrupados)', self.stats.get('productos', 0)),
            ('Productos_Talla (SKUs)', self.stats.get('producto_talla', 0)),
            ('Movimientos', self.stats.get('movimientos', 0)),
            ('DTEs', self.stats.get('dtes', 0)),
        ]
        
        total_migrado = sum(count for _, count in tabla_stats)
        
        for nombre, count in tabla_stats:
            skip = self.stats.get(f'{nombre.lower()}_skipped', 0)
            skip_text = f' ({skip} omitidos)' if skip > 0 else ''
            self.stdout.write(f'  ✓ {nombre:30s}: {count:>8,}{skip_text}')
        
        self.stdout.write('-'*70)
        self.stdout.write(f'  TOTAL REGISTROS MIGRADOS: {total_migrado:>8,}')
        
        if self.errors:
            self.stdout.write(f'\n⚠️  Total errores: {len(self.errors):,}')
            self.stdout.write(f'📄 Ver detalles en: {ERROR_LOG_FILE}')
        
        # Velocidad promedio
        avg_speed = total_migrado / elapsed.total_seconds() if elapsed.total_seconds() > 0 else 0
        
        self.stdout.write(f'\n⏱️  Tiempo total: {elapsed}')
        self.stdout.write(f'⚡ Velocidad promedio: {avg_speed:.0f} registros/segundo')
        self.stdout.write('='*70)