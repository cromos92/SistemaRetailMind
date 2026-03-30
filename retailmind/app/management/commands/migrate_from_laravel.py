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
from datetime import datetime, time, date
from collections import defaultdict
import sys
from pathlib import Path

# Cargar variables de entorno ANTES de cualquier otra importación
from dotenv import load_dotenv
# Buscar .env en el directorio del proyecto Django
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app.models import (
    Empresa, Sucursal, Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla, Movimientos_Producto, Dte, Dte_Productos,
    Dte_Detalle_Pago, Vendedor, Correlativo, ParametroGlobal,
    StockInicialTemporada,
)
from app.management.commands.importar_creditos_personal import Command as ImportCreditosCommand


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

RUTS_EMPRESAS_PROPIAS = {'78503140-7', '76104936-4', '76337843-8', '7397811-4'}

# ============================================================================
# MAPEOS HARDCODED
# ============================================================================

# ============================================================================
# MAPEO FIJO DE SUCURSALES -> EMPRESAS (NO USAR MYSQL)
# ============================================================================
EMPRESA_RUT_MAP = {
    # Vicent Paola - RUT 78503140-7
    'PA00': '78503140-7',
    'PAO0': '78503140-7',  # Alias alternativo
    'PAO1': '78503140-7',
    'PAO2': '78503140-7',
    'PAO3': '78503140-7',
    'PAO4': '78503140-7',
    
    # Edelmira Tebes y Cia Ltda - RUT 76337843-8
    'EDEL': '76337843-8',
    'EDEL FALLADOS': '76337843-8',
    
    # Edelmira Gilda Tebes - RUT 7397811-4
    'GILD': '7397811-4',
    
    # Importadora Nicolas - RUT 76104936-4
    'IMP': '76104936-4',
    'NICK1': '76104936-4',
    'NICK2': '76104936-4',
    'NICK3': '76104936-4',
}

# Direcciones fijas para sucursales
SUCURSAL_DIRECCION_MAP = {
    'PA00': 'Maipu 676',
    'PAO0': 'Maipu 676',
    'PAO1': 'Maipu 668',
    'PAO2': 'Matta 2422',
    'PAO3': 'Matta 2432',
    'PAO4': 'Matta 2458',
    'EDEL': 'Maipu 676',
    'EDEL FALLADOS': 'Maipu 676',
    'GILD': 'Maipu 676',
    'IMP': 'Maipu 676',
    'NICK1': 'Matta 2479',
    'NICK2': 'Matta 2438',
    'NICK3': 'Matta 2418',
}

TIPO_MOVIMIENTO_MAP = {
    'Ingreso': 'INGRESO',
    'Egreso': 'EGRESO',
    'Egresso': 'EGRESO',
    'Traspaso': 'TRASPASO',
}

CONCEPTO_MAP = {
    # === INGRESOS ===
    'Creacion': 'INGRESO_INICIAL',
    'Creacion Productos': 'INGRESO_INICIAL',
    'Compra': 'RECEPCION_COMPRA',
    'Devolucion': 'DEVOLUCION_CLIENTE',

    # === EGRESOS / VENTAS ===
    'Venta': 'VENTA_PUBLICO',
    'VentaXPublico': 'VENTA_PUBLICO',
    'VentaxBoleta': 'VENTA_PUBLICO',
    'VentaxFactura': 'VENTA_MAYORISTA',

    # === TRASPASOS ===
    'VentaXInterna': 'TRASPASO_SUCURSAL',
    'Concepto': 'TRASPASO_SUCURSAL',

    # === CAMBIOS / DEVOLUCIONES ===
    # En Laravel genera 2 movimientos: Ingreso (devuelto) + Egreso (nuevo)
    # Se determina CAMBIO_PRODUCTO_ENTRADA o CAMBIO_PRODUCTO_SALIDA según tipo_movimiento
    'MovimientoxCambioProducto': '_CAMBIO_PRODUCTO',  # Prefijo especial, se resuelve abajo
    'MovimientoxNotaDeCredito': 'DEVOLUCION_CLIENTE',

    # === CORRECCIONES / AJUSTES ===
    # 'Ajuste' se resuelve a AJUSTE_POSITIVO o AJUSTE_NEGATIVO según tipo_movimiento
    'Ajuste': '_AJUSTE',  # Prefijo especial, se resuelve abajo
    'MovimientoxParametrosxMaestros': 'CORRECCION_STOCK',
    'EdicionxConcepto': 'CORRECCION_STOCK',
}

def resolver_concepto(concepto_mysql, tipo_movimiento_mysql):
    """
    Resuelve el concepto Django final considerando el tipo_movimiento.
    Necesario porque 'Ajuste' y 'MovimientoxCambioProducto' dependen
    de si es Ingreso o Egreso para mapear al choice correcto.
    """
    concepto_base = CONCEPTO_MAP.get(concepto_mysql, 'CORRECCION_STOCK')
    tipo = TIPO_MOVIMIENTO_MAP.get(tipo_movimiento_mysql, 'INGRESO')

    if concepto_base == '_AJUSTE':
        return 'AJUSTE_POSITIVO' if tipo == 'INGRESO' else 'AJUSTE_NEGATIVO'

    if concepto_base == '_CAMBIO_PRODUCTO':
        return 'CAMBIO_PRODUCTO_ENTRADA' if tipo == 'INGRESO' else 'CAMBIO_PRODUCTO_SALIDA'

    return concepto_base

TIPO_DOCUMENTO_MAP = {
    '33': 'FACTURA ELECTRONICA',
    '39': 'BOLETA ELECTRONICA',
    '52': 'GUIA',
    '61': 'NOTA DE CREDITO',
    '56': 'NOTA DE DEBITO',
    '34': 'FACTURA EXENTA',
}

ESTADO_DTE_MAP = {
    'Vigente': 'ACEPTADO',   # Ya procesado en sistema antiguo → no requiere recepción en el nuevo
    'vigente': 'ACEPTADO',
    'Anulado': 'ANULADO',
    'anulado': 'ANULADO',
    'AnuladoxParcial': 'ANULADO',
    'Recepcionado': 'RECEPCIONADO_COMPLETO',
    'recepcionado': 'RECEPCIONADO_COMPLETO',
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
        self.cache_producto_talla = {}  # Clave: SKU:ALIAS (para SKUs duplicados entre sucursales)
        self.cache_producto_talla_by_sku = {}  # Clave: SKU simple (para compatibilidad)
        self.cache_empresas_rut = {}
        
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
        parser.add_argument(
            '--fast-mode',
            action='store_true',
            help='Modo ultra-rapido: deshabilita triggers y constraints temporalmente'
        )
        parser.add_argument(
            '--no-input',
            action='store_true',
            help='Saltar confirmacion interactiva'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.batch_size = options.get('batch_size', 2000)
        self.limit = options.get('limit', None)
        self.fast_mode = options.get('fast_mode', False)
        specific_tables = options.get('tables', None)
        
        self.start_time = datetime.now()
        
        # =====================================================================
        # CONFIRMACIÓN DE BASES DE DATOS
        # =====================================================================
        pg_db = settings.DATABASES['default']
        pg_name = pg_db.get('NAME', 'desconocida')
        pg_host = pg_db.get('HOST', 'localhost')
        pg_user = pg_db.get('USER', 'postgres')
        
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.WARNING('  🔄 MIGRACIÓN MySQL → PostgreSQL'))
        self.stdout.write('='*70)
        
        self.stdout.write(self.style.HTTP_INFO('\n  📤 ORIGEN (MySQL - Laravel):'))
        self.stdout.write(f'     Host: {MYSQL_HOST}:{MYSQL_PORT}')
        self.stdout.write(f'     Base de datos: {MYSQL_DATABASE}')
        self.stdout.write(f'     Usuario: {MYSQL_USER}')
        
        self.stdout.write(self.style.SUCCESS('\n  📥 DESTINO (PostgreSQL - Django):'))
        self.stdout.write(f'     Host: {pg_host}')
        self.stdout.write(f'     Base de datos: {self.style.WARNING(pg_name)}')
        self.stdout.write(f'     Usuario: {pg_user}')
        
        self.stdout.write('\n' + '-'*70)
        
        # Verificar si la DB tiene datos
        from app.models import Producto, Dte
        productos_count = Producto.objects.count()
        dtes_count = Dte.objects.count()
        
        if productos_count > 0 or dtes_count > 0:
            self.stdout.write(self.style.WARNING(f'  ⚠️  La DB destino tiene datos:'))
            self.stdout.write(f'     - Productos: {productos_count:,}')
            self.stdout.write(f'     - DTEs: {dtes_count:,}')
            self.stdout.write(self.style.HTTP_INFO('     (Se saltarán registros duplicados)'))
        else:
            self.stdout.write(self.style.SUCCESS('  ✓ La DB destino está vacía (migración rápida)'))
        
        self.stdout.write('\n' + '-'*70)
        
        no_input = options.get('no_input', False)

        if no_input:
            respuesta = ''
        else:
            self.stdout.write(self.style.HTTP_INFO('\n  Opciones:'))
            self.stdout.write(f'    [ENTER] = Continuar con "{pg_name}"')
            self.stdout.write(f'    [nombre] = Usar otra base de datos')
            self.stdout.write(f'    [N] = Cancelar')
            respuesta = input(f'\n  Tu elección: ').strip()
        
        if respuesta.upper() in ['N', 'NO', 'CANCELAR']:
            self.stdout.write(self.style.ERROR('\n  ❌ Migración cancelada por el usuario.'))
            return
        
        # Si ingresó un nombre de DB diferente, cambiar la conexión
        if respuesta and respuesta.upper() not in ['S', 'SI', 'Y', 'YES', '']:
            nueva_db = respuesta
            self.stdout.write(self.style.WARNING(f'\n  🔄 Cambiando base de datos a: {nueva_db}'))
            
            # Actualizar la configuración de la DB en tiempo de ejecución
            from django.db import connections
            settings.DATABASES['default']['NAME'] = nueva_db
            
            # Cerrar conexiones existentes para forzar reconexión
            connections['default'].close()
            
            # Verificar conexión a la nueva DB
            try:
                from django.db import connection
                connection.ensure_connection()
                self.stdout.write(self.style.SUCCESS(f'  ✓ Conectado a "{nueva_db}"'))
                
                # Actualizar contadores
                productos_count = Producto.objects.count()
                dtes_count = Dte.objects.count()
                
                if productos_count > 0 or dtes_count > 0:
                    self.stdout.write(self.style.WARNING(f'  ⚠️  Esta DB tiene datos:'))
                    self.stdout.write(f'     - Productos: {productos_count:,}')
                    self.stdout.write(f'     - DTEs: {dtes_count:,}')
                else:
                    self.stdout.write(self.style.SUCCESS('  ✓ La DB está vacía (migración rápida)'))
                
                pg_name = nueva_db
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ Error al conectar a "{nueva_db}": {e}'))
                self.stdout.write(self.style.HTTP_INFO('  Asegúrate de que la base de datos existe.'))
                self.stdout.write(self.style.HTTP_INFO('  Puedes crearla con: CREATE DATABASE nombre_db;'))
                return
        
        self.stdout.write(self.style.SUCCESS(f'\n  ✓ Iniciando migración a "{pg_name}"...\n'))
        
        # =====================================================================
        # CONTINÚA LA MIGRACIÓN
        # =====================================================================
        
        # Abrir archivo de errores
        self.error_file = open(ERROR_LOG_FILE, 'w', encoding='utf-8')
        self.error_file.write(f'=== LOG DE ERRORES - MIGRACIÓN OPTIMIZADA ===\n')
        self.error_file.write(f'Fecha: {self.start_time}\n')
        self.error_file.write(f'DB Destino: {pg_name}\n')
        self.error_file.write(f'{"="*70}\n\n')

        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] Modo simulación activado'))
        
        if self.limit:
            self.stdout.write(self.style.WARNING(f'[LÍMITE] Máximo {self.limit} registros/tabla'))

        # Validar configuración
        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            self.stdout.write(self.style.ERROR('[ERROR] Faltan variables MySQL'))
            return

        # Operaciones que solo necesitan PostgreSQL (sin MySQL)
        POSTGRES_ONLY_TABLES = {'fix_dtes_duplicados', 'corregir_tipo_transaccion'}
        skip_mysql = specific_tables and all(t in POSTGRES_ONLY_TABLES for t in specific_tables)

        # Conectar a MySQL
        if skip_mysql:
            self.mysql_conn = None
            self.stdout.write(self.style.WARNING('  (Operación solo-PostgreSQL, se omite conexión MySQL)'))
        else:
            try:
                self.mysql_conn = self.connect_mysql()
                self.stdout.write(self.style.SUCCESS('✓ Conexión MySQL establecida'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Error MySQL: {e}'))
                return
        
        # Pre-cargar cachés
        self.stdout.write('\n📦 Cargando cachés...')
        if not skip_mysql:
            self.preload_caches()
        else:
            self.cache_sucursales = {}
            self.cache_empresas_rut = {}

        # Orden de migración
        migration_order = [
            ('empresas', self.migrate_empresas_principales),
            ('clientes', self.migrate_clientes),
            ('sucursales', self.migrate_sucursales),
            ('vendedores', self.migrate_vendedores),
            ('atributos', self.migrate_atributos),
            ('categorias', self.migrate_categorias),
            ('productos', self.migrate_productos),
            ('producto_talla', self.migrate_producto_talla),
            ('movimientos', self.migrate_movimientos),
            ('dtes', self.migrate_dtes),
            ('dte_productos', self.migrate_dte_productos),
            ('crear_dtes_faltantes', self.crear_dtes_faltantes),
            ('fix_dtes_duplicados', self.fix_dtes_duplicados),
            ('corregir_descuentos_dte', self.corregir_descuentos_dte),
            ('corregir_fechas_dte', self.corregir_fechas_dte),
            ('corregir_sucursales_dte', self.corregir_sucursales_dte),
            ('corregir_tipo_transaccion', self.corregir_tipo_transaccion_dte),
            ('ventas_pagos', self.migrate_ventas_pagos),
            ('asignar_vendedores_dte', self.asignar_vendedores_a_dtes),
            ('creditos_personal', self.migrate_creditos_personal),
            ('parametros_maestros', self.migrate_parametros_maestros),
            ('correlativos', self.migrate_correlativos),
            ('nc_vinculacion', self.migrate_nc_vinculacion),
            ('enriquecer_prediccion', self.enriquecer_datos_prediccion),
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
            
            # Inicializar permisos del menú
            self.inicializar_permisos_menu()

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

    def migrate_creditos_personal(self):
        """Migrar créditos desde creditos_personal usando el comando dedicado.
        Los créditos se asocian a Cliente (no a Vendedor)."""
        self.stdout.write(self.style.SUCCESS('🔹 Importando créditos desde creditos_personal'))
        cmd = ImportCreditosCommand()
        cmd.stdout = self.stdout
        cmd.stderr = self.stderr
        cmd.style = self.style
        cmd.handle(
            table='creditos_personal',
            dry_run=self.dry_run,
            user_id=None,
            empresa_id=None,
            actualizar=False,
            solo_internos=False,
            solo_externos=False,
            limit=None,
            crear_vendedor=False,
            externo_en_creditos=False,
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
            # CORRECCIÓN: Usar clave compuesta SKU:ALIAS para manejar SKUs duplicados entre sucursales
            self.stdout.write('  ⏳ Cargando productos_talla... (puede tardar)')
            skus_duplicados = 0
            for pt in Producto_Talla.objects.select_related(
                'producto__sucursal',
                'producto__atributo1__atributo',
                'producto__atributo2__atributo',
                'producto__categoria'
            ).all():
                # Clave compuesta: SKU:ALIAS para permitir mismo SKU en diferentes sucursales
                alias = pt.producto.sucursal.alias if pt.producto.sucursal else 'SIN_SUCURSAL'
                cache_key = f"{pt.sku}:{alias}"
                self.cache_producto_talla[cache_key] = pt
                
                # También guardar por SKU simple (para compatibilidad)
                # Si ya existe, significa que hay SKUs duplicados entre sucursales
                sku_key = str(pt.sku)
                if sku_key in self.cache_producto_talla_by_sku:
                    skus_duplicados += 1
                self.cache_producto_talla_by_sku[sku_key] = pt
                
            self.stdout.write(f'  ✓ {len(self.cache_producto_talla)} productos_talla')
            if skus_duplicados > 0:
                self.stdout.write(self.style.WARNING(f'  ⚠️ {skus_duplicados} SKUs duplicados entre sucursales'))
            
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

    def find_producto_talla_fast(self, codigo_asociado, alias=None):
        """
        ⚡ Búsqueda ultra-rápida usando caché en memoria

        Estrategia:
        1. Si hay alias, intenta clave compuesta SKU:ALIAS (más preciso)
        2. Si no encuentra o no hay alias, usa SKU simple (fallback)
        """
        if alias:
            # Primero intentar búsqueda precisa con clave compuesta
            cache_key = f"{codigo_asociado}:{alias}"
            result = self.cache_producto_talla.get(cache_key)
            if result:
                return result
        
        # Fallback: búsqueda simple por SKU (puede haber colisiones entre sucursales)
        return self.cache_producto_talla_by_sku.get(str(codigo_asociado))

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
        if isinstance(value, date) and not isinstance(value, datetime):
            return timezone.make_aware(datetime.combine(value, datetime.min.time()))
        if isinstance(value, datetime):
            return timezone.make_aware(value) if timezone.is_naive(value) else value
        if isinstance(value, str):
            from django.utils.dateparse import parse_datetime, parse_date
            parsed = parse_datetime(value)
            if parsed:
                return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
            parsed_d = parse_date(value)
            if parsed_d:
                return timezone.make_aware(datetime.combine(parsed_d, datetime.min.time()))
        return timezone.now()

    def safe_date_only(self, value):
        """Extrae solo la fecha (date) de un datetime"""
        if value is None:
            return timezone.now().date()
        # Si ya es un date, devolverlo
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        # Si es datetime, extraer date
        if isinstance(value, datetime):
            return value.date()
        # Intentar convertir string
        if isinstance(value, str):
            try:
                from django.utils.dateparse import parse_date
                parsed = parse_date(value)
                return parsed if parsed else timezone.now().date()
            except:
                return timezone.now().date()
        return timezone.now().date()

    def safe_time_only(self, value):
        """Extrae solo la hora (time) de un datetime"""
        from datetime import timedelta
        
        if value is None:
            return timezone.now().time()
        # Si ya es un time, devolverlo
        if isinstance(value, time) and not isinstance(value, datetime):
            return value
        # Si es datetime, extraer time
        if isinstance(value, datetime):
            return value.time()
        # Si es timedelta (MySQL a veces devuelve esto), convertir a time
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
            hours = (total_seconds // 3600) % 24
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return time(hours, minutes, seconds)
        # Intentar convertir string
        if isinstance(value, str):
            try:
                from django.utils.dateparse import parse_time
                parsed = parse_time(value)
                return parsed if parsed else timezone.now().time()
            except:
                return timezone.now().time()
        # Último intento: convertir a string y parsear
        try:
            return time.fromisoformat(str(value))
        except:
            return timezone.now().time()

    # ========================================================================
    # FAST MODE - Optimizaciones PostgreSQL
    # ========================================================================
    
    def enable_fast_mode(self, tables):
        """⚡ Deshabilita triggers y constraints para inserción masiva"""
        if not self.fast_mode:
            return
        
        from django.db import connection
        self.stdout.write(self.style.WARNING('  ⚡ FAST MODE: Deshabilitando triggers...'))
        
        with connection.cursor() as cursor:
            for table in tables:
                try:
                    cursor.execute(f'ALTER TABLE {table} DISABLE TRIGGER ALL;')
                except Exception as e:
                    self.stdout.write(f'    ⚠️ No se pudo deshabilitar triggers en {table}: {e}')
        
        self.stdout.write(self.style.SUCCESS('  ✓ Triggers deshabilitados'))
    
    def disable_fast_mode(self, tables):
        """⚡ Re-habilita triggers y constraints después de inserción"""
        if not self.fast_mode:
            return
        
        from django.db import connection
        self.stdout.write(self.style.WARNING('  ⚡ FAST MODE: Re-habilitando triggers...'))
        
        with connection.cursor() as cursor:
            for table in tables:
                try:
                    cursor.execute(f'ALTER TABLE {table} ENABLE TRIGGER ALL;')
                except Exception as e:
                    self.stdout.write(f'    ⚠️ No se pudo habilitar triggers en {table}: {e}')
        
        self.stdout.write(self.style.SUCCESS('  ✓ Triggers re-habilitados'))

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
                'acteco': '469000',
            },
            {
                'rut': '76104936-4',
                'nombre': 'Importadora Nicolas',
                'razon_social': 'Importadora Nicolas',
                'nombre_fantasia': 'Importadora Nicolas',
                'giro': 'Comercio',
                'direccion': '',
                'comuna': '',
                'ciudad': '',
                'correoVendedor': '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
                'acteco': '469000',
            },
            {
                'rut': '76337843-8',
                'nombre': 'Edelmira Tebes y Cia Ltda',
                'razon_social': 'Edelmira Tebes y Cia Ltda',
                'nombre_fantasia': 'EDEL',
                'giro': 'Comercio',
                'direccion': 'Maipu 676',
                'comuna': '',
                'ciudad': '',
                'correoVendedor': '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
                'acteco': '469000',
            },
            {
                'rut': '7397811-4',
                'nombre': 'Edelmira Gilda Tebes',
                'razon_social': 'Edelmira Gilda Tebes',
                'nombre_fantasia': 'Gilda Tebes',
                'giro': 'Comercio',
                'direccion': '',
                'comuna': '',
                'ciudad': '',
                'correoVendedor': '',
                'correoIntercambio': '',
                'correoAdministrador': '',
                'esProveedor': False,
                'acteco': '469000',
            },
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
                'acteco': '469000',  # Código actividad económica
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
        """Migra sucursales usando MAPEO FIJO (no MySQL) para evitar errores"""
        self.stdout.write('🏪 Migrando sucursales (mapeo fijo)...')

        count = 0
        
        # Usar el mapeo hardcoded en lugar de MySQL
        for alias, rut_empresa in EMPRESA_RUT_MAP.items():
            empresa = self.cache_empresas_rut.get(rut_empresa)
            
            if not empresa:
                self.stdout.write(self.style.WARNING(f'  ⚠️ Empresa no encontrada para {alias}: {rut_empresa}'))
                continue
            
            # Obtener dirección del mapeo o usar alias como default
            direccion = SUCURSAL_DIRECCION_MAP.get(alias, alias)

            if not self.dry_run:
                sucursal, created = Sucursal.objects.get_or_create(
                    alias=alias,
                    defaults={
                        'empresa': empresa,
                        'direccion': direccion
                    }
                )
                
                # Si ya existe pero tiene empresa incorrecta, corregir
                if not created and sucursal.empresa_id != empresa.id:
                    sucursal.empresa = empresa
                    sucursal.direccion = direccion
                    sucursal.save()
                    self.stdout.write(f'  🔄 Corregida: {alias} -> {rut_empresa}')
                
                if created:
                    count += 1
                    self.stdout.write(f'  ✓ Creada: {alias} -> {empresa.nombre} ({rut_empresa})')
                
                self.cache_sucursales[alias] = sucursal
            else:
                count += 1

        self.stats['sucursales'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} sucursales creadas/verificadas'))

    def migrate_vendedores(self):
        """Migra vendedores desde MySQL (tabla: vendedores)"""
        self.stdout.write('👥 Migrando vendedores...')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        # Contar total
        cursor.execute('SELECT COUNT(*) as total FROM vendedores')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        self.stdout.write(f'  📊 Total en MySQL: {total} vendedores')

        # Obtener vendedores
        cursor.execute(f'''
            SELECT ID, nombres, rut, sucursal, bodega, codigo_interno, Fecha
            FROM vendedores
            ORDER BY ID
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        updated = 0
        skipped = 0

        for idx, row in enumerate(cursor, 1):
            mysql_id = row['ID']
            nombres = (row['nombres'] or '').strip()
            rut = (row['rut'] or '').strip()
            bodega = (row['bodega'] or '').strip()
            codigo_interno = row['codigo_interno']
            fecha = row['Fecha']

            # Validaciones básicas
            if not nombres and not rut:
                skipped += 1
                continue

            # Buscar sucursal por bodega (alias)
            sucursal = self.cache_sucursales.get(bodega)
            
            # Determinar empresa
            empresa = None
            if sucursal:
                empresa = sucursal.empresa
            else:
                # Intentar por mapeo directo
                rut_empresa = EMPRESA_RUT_MAP.get(bodega)
                if rut_empresa:
                    empresa = self.cache_empresas_rut.get(rut_empresa)

            # Generar codigo_vendedor
            codigo_vendedor = str(codigo_interno) if codigo_interno else f'MIG-{mysql_id}'

            if not self.dry_run:
                # Buscar si ya existe (por RUT o codigo_vendedor)
                vendedor_existente = None
                if rut:
                    vendedor_existente = Vendedor.objects.filter(rut=rut).first()
                if not vendedor_existente and codigo_vendedor:
                    vendedor_existente = Vendedor.objects.filter(codigo_vendedor=codigo_vendedor).first()

                if vendedor_existente:
                    # Actualizar existente si faltan datos
                    needs_save = False
                    
                    if not vendedor_existente.nombre and nombres:
                        vendedor_existente.nombre = nombres
                        needs_save = True
                    
                    if not vendedor_existente.fecha_nacimiento and fecha:
                        vendedor_existente.fecha_nacimiento = fecha
                        needs_save = True
                    
                    if empresa and not vendedor_existente.empresa:
                        vendedor_existente.empresa = empresa
                        needs_save = True

                    # Agregar sucursal si no está asignada
                    if sucursal and not vendedor_existente.sucursales.filter(id=sucursal.id).exists():
                        vendedor_existente.sucursales.add(sucursal)
                        needs_save = True

                    if needs_save:
                        vendedor_existente.save()
                        updated += 1
                    else:
                        skipped += 1
                else:
                    # Crear nuevo vendedor
                    vendedor = Vendedor.objects.create(
                        codigo_vendedor=codigo_vendedor,
                        nombre=nombres or f'Vendedor {mysql_id}',
                        rut=rut or None,
                        fecha_nacimiento=fecha,
                        empresa=empresa,
                        activo=True,
                        comision=0,
                        correo=None,
                    )

                    # Asignar sucursal (M2M)
                    if sucursal:
                        vendedor.sucursales.add(sucursal)

                    count += 1
            else:
                count += 1

            if idx % 20 == 0:
                self.show_progress(idx, total, f'│ {count} nuevos, {updated} act.')

        cursor.close()
        self.stats['vendedores'] = count
        self.stats['vendedores_actualizados'] = updated
        self.stats['vendedores_omitidos'] = skipped
        self.stdout.write('\n' + self.style.SUCCESS(
            f'  ✓ {count} vendedores creados, {updated} actualizados ({skipped} omitidos)'
        ))

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
        """Migra productos (ULTRA-OPTIMIZADO - Todo en memoria)"""
        self.stdout.write('📦 Migrando productos (agrupación)...')

        # =====================================================================
        # PASO 1: Pre-cargar atributos existentes de PostgreSQL
        # =====================================================================
        self.stdout.write('  ⏳ Cargando atributos existentes...')
        
        # Cache de opciones de atributo: (atributo_nombre, valor) -> opcion_id
        cache_opciones = {}
        for opcion in AtributoOpcion.objects.select_related('atributo').all():
            key = (opcion.atributo.nombre, opcion.valor)
            cache_opciones[key] = opcion.id
        self.stdout.write(f'  ✓ {len(cache_opciones):,} opciones de atributo')

        # =====================================================================
        # PASO 2: Pre-cargar productos existentes de PostgreSQL
        # =====================================================================
        self.stdout.write('  ⏳ Cargando productos existentes...')
        
        productos_existentes = set(
            Producto.objects.select_related('sucursal', 'atributo1', 'atributo2').values_list(
                'articulo', 'sucursal__alias', 'atributo1__valor', 'atributo2__valor'
            )
        )
        self.stdout.write(f'  ✓ {len(productos_existentes):,} productos existentes')

        # =====================================================================
        # PASO 3: Pre-cargar categorías
        # =====================================================================
        cache_categorias_id = {c.nombre: c.id for c in Categoria.objects.all()}

        # =====================================================================
        # PASO 4: Pre-cargar sucursales
        # =====================================================================
        cache_sucursales_id = {s.alias: s.id for s in Sucursal.objects.all()}

        # =====================================================================
        # PASO 5: Obtener datos de MySQL
        # =====================================================================
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        query = '''
            SELECT 
                articulo, descripcion, marca, color, sexo, familia, alias,
                MIN(costo) as costo, 
                MIN(preciointerno) as preciointerno,
                MIN(precioventapublico) as precioventa
            FROM talla
            WHERE articulo IS NOT NULL
            GROUP BY articulo, marca, color, descripcion, sexo, familia, alias
            ORDER BY articulo
        '''
        
        if self.limit:
            query += f' LIMIT {self.limit}'
        
        cursor.execute(query)
        productos_mysql = cursor.fetchall()
        total = len(productos_mysql)
        self.stdout.write(f'  📊 Total en MySQL: {total:,} productos agrupados')

        # =====================================================================
        # PASO 6: Procesar en memoria
        # =====================================================================
        count = 0
        ya_existe = 0
        sin_sucursal = 0
        batch = []

        # Función auxiliar para obtener o crear opción (minimiza queries)
        def get_opcion_id(atributo_nombre, valor):
            if not valor:
                valor = 'SIN ESPECIFICAR'
            key = (atributo_nombre, valor)
            if key not in cache_opciones:
                # Solo crear si no existe
                opcion = self.get_or_create_atributo_opcion(atributo_nombre, valor)
                cache_opciones[key] = opcion.id
            return cache_opciones[key]

        for idx, row in enumerate(productos_mysql, 1):
            articulo = row['articulo']
            alias = row['alias']
            
            # Buscar sucursal
            sucursal_id = cache_sucursales_id.get(alias)
            if not sucursal_id:
                sin_sucursal += 1
                continue

            # Normalizar valores
            marca = row['marca'] or 'SIN ESPECIFICAR'
            color = row['color'] or 'SIN ESPECIFICAR'

            # Verificar existencia (sin query!)
            prod_key = (articulo, alias, marca, color)
            if prod_key in productos_existentes:
                ya_existe += 1
                continue

            if not self.dry_run:
                # Obtener IDs de opciones
                marca_opcion_id = get_opcion_id('Marca', marca)
                color_opcion_id = get_opcion_id('Color', color)
                sexo_opcion_id = get_opcion_id('Sexo', row['sexo'])
                categoria_id = cache_categorias_id.get(row['familia'])

                # Calcular sobreprecio
                costo = self.safe_int(row['costo'])
                preciointerno = self.safe_int(row['preciointerno'])
                sobreprecio = max(0, preciointerno - costo)
                
                producto = Producto(
                    articulo=articulo,
                    descripcion=row['descripcion'] or articulo,
                    precioventa=self.safe_int(row['precioventa']),
                    costo=costo,
                    sobreprecio=sobreprecio,
                    sucursal_id=sucursal_id,
                    categoria_id=categoria_id,
                    atributo1_id=marca_opcion_id,
                    atributo2_id=color_opcion_id,
                    atributo3_id=sexo_opcion_id,
                )
                batch.append(producto)
                productos_existentes.add(prod_key)
                
                if len(batch) >= self.batch_size:
                    Producto.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 1000 == 0:
                self.show_progress(idx, total, f'│ {count} nuevos, {ya_existe} exist')

        if batch and not self.dry_run:
            Producto.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['productos'] = count
        self.stats['productos_ya_existe'] = ya_existe
        self.stats['productos_sin_sucursal'] = sin_sucursal
        self.stdout.write('\n' + self.style.SUCCESS(
            f'  ✓ {count:,} productos nuevos migrados\n'
            f'    - Ya existían: {ya_existe:,}\n'
            f'    - Sin sucursal: {sin_sucursal:,}'
        ))

    def migrate_producto_talla(self):
        """Migra producto_talla (ULTRA-OPTIMIZADO - Todo en memoria)"""
        self.stdout.write('🔢 Migrando productos_talla (SKUs)...')

        # =====================================================================
        # PASO 1: Cargar TODOS los productos de PostgreSQL en memoria
        # =====================================================================
        self.stdout.write('  ⏳ Cargando productos de PostgreSQL en memoria...')
        
        # Diccionario: (articulo, sucursal_id, marca_valor, color_valor) -> producto_id
        cache_productos_pg = {}
        for p in Producto.objects.select_related('sucursal', 'atributo1', 'atributo2').all():
            key = (
                p.articulo,
                p.sucursal_id,
                p.atributo1.valor if p.atributo1 else None,
                p.atributo2.valor if p.atributo2 else None
            )
            cache_productos_pg[key] = p.id
        self.stdout.write(f'  ✓ {len(cache_productos_pg):,} productos en memoria')

        # =====================================================================
        # PASO 2: Cargar TODOS los producto_talla existentes de PostgreSQL
        # =====================================================================
        self.stdout.write('  ⏳ Cargando SKUs existentes de PostgreSQL...')
        
        # Dict (sku, producto_id) -> pt_id para poder actualizar stock de registros existentes
        skus_existentes_map = {
            (sku, pid): pt_id
            for pt_id, sku, pid in Producto_Talla.objects.values_list('id', 'sku', 'producto_id')
        }
        skus_existentes = set(skus_existentes_map.keys())
        self.stdout.write(f'  ✓ {len(skus_existentes):,} SKUs existentes')

        # =====================================================================
        # PASO 3: Cargar sucursales en memoria (alias -> id)
        # =====================================================================
        cache_sucursales_id = {s.alias: s.id for s in Sucursal.objects.all()}

        # =====================================================================
        # PASO 4: Obtener datos de MySQL
        # =====================================================================
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM talla WHERE codigo_asociado IS NOT NULL')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        self.stdout.write(f'  📊 Total en MySQL: {total:,} registros')

        cursor.execute(f'''
            SELECT codigo_asociado, articulo, marca, color, size, stock, alias
            FROM talla
            WHERE codigo_asociado IS NOT NULL
            ORDER BY articulo, size
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        # =====================================================================
        # PASO 5: Procesar en memoria (sin queries individuales)
        # =====================================================================
        count = 0
        skipped = 0
        sin_producto = 0
        sin_sucursal = 0
        ya_existe = 0
        stock_actualizado = 0
        batch = []
        # Dict (sku, producto_id) -> nuevo_stock para actualizar registros existentes
        stock_updates = {}

        for idx, row in enumerate(cursor, 1):
            sku = self.safe_int(row['codigo_asociado'])
            alias = row['alias']
            
            if not sku:
                skipped += 1
                continue

            # Buscar sucursal en caché
            sucursal_id = cache_sucursales_id.get(alias)
            if not sucursal_id:
                sin_sucursal += 1
                continue

            # Buscar producto en caché (sin query!)
            marca = row['marca'] or 'SIN ESPECIFICAR'
            color = row['color'] or 'SIN ESPECIFICAR'
            key = (row['articulo'], sucursal_id, marca, color)
            producto_id = cache_productos_pg.get(key)

            if not producto_id:
                sin_producto += 1
                continue

            nuevo_stock = self.safe_int(row['stock'])

            # Verificar si ya existe (sin query!)
            if (sku, producto_id) in skus_existentes:
                # Acumular para sincronizar stock (puede haber cambiado desde el último import)
                stock_updates[(sku, producto_id)] = nuevo_stock
                ya_existe += 1
                continue

            if not self.dry_run:
                talla_obj = Producto_Talla(
                    sku=sku,
                    producto_id=producto_id,
                    talla=row['size'] or 'U',
                    stock=nuevo_stock,
                )
                batch.append(talla_obj)
                skus_existentes.add((sku, producto_id))
                skus_existentes_map[(sku, producto_id)] = None  # ID se asigna tras bulk_create
                
                if len(batch) >= self.batch_size:
                    Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 5000 == 0:
                self.show_progress(idx, total, f'│ {count} nuevos, {ya_existe} exist')

        if batch and not self.dry_run:
            Producto_Talla.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        # =====================================================================
        # PASO 6: Sincronizar stock de registros existentes (bulk_update)
        # =====================================================================
        if stock_updates and not self.dry_run:
            self.stdout.write(f'\n  ⏳ Sincronizando stock de {len(stock_updates):,} SKUs existentes...')
            update_objs = []
            for (sku, producto_id), nuevo_stock in stock_updates.items():
                pt_id = skus_existentes_map.get((sku, producto_id))
                if pt_id:
                    update_objs.append(Producto_Talla(id=pt_id, stock=nuevo_stock))

            for i in range(0, len(update_objs), self.batch_size):
                chunk = update_objs[i:i + self.batch_size]
                Producto_Talla.objects.bulk_update(chunk, ['stock'])
                stock_actualizado += len(chunk)

        cursor.close()
        self.stats['producto_talla'] = count
        self.stats['producto_talla_ya_existe'] = ya_existe
        self.stats['producto_talla_sin_producto'] = sin_producto
        self.stats['producto_talla_sin_sucursal'] = sin_sucursal
        self.stdout.write('\n' + self.style.SUCCESS(
            f'  ✓ {count:,} SKUs nuevos migrados\n'
            f'    - Stock actualizado: {stock_actualizado:,}\n'
            f'    - Ya existían (sin cambio): {ya_existe - stock_actualizado:,}\n'
            f'    - Sin producto: {sin_producto:,}\n'
            f'    - Sin sucursal: {sin_sucursal:,}'
        ))

    def migrate_movimientos(self):
        """⚡ Migra movimientos (ULTRA-OPTIMIZADO con caché en memoria y protección contra duplicados)"""
        self.stdout.write('📊 Migrando movimientos...')
        
        # ⚡ FAST MODE: Deshabilitar triggers para inserción masiva
        self.enable_fast_mode(['app_movimientos_producto'])

        # ✅ CRÍTICO: Recargar caché de producto_talla porque se crearon nuevos en esta sesión
        self.stdout.write('  ⏳ Recargando caché de productos_talla...')
        self.cache_producto_talla = {}
        self.cache_producto_talla_by_sku = {}
        for pt in Producto_Talla.objects.select_related('producto__sucursal').all():
            alias = pt.producto.sucursal.alias if pt.producto.sucursal else 'SIN_SUCURSAL'
            cache_key = f"{pt.sku}:{alias}"
            self.cache_producto_talla[cache_key] = pt
            self.cache_producto_talla_by_sku[str(pt.sku)] = pt
        self.stdout.write(f'  ✓ {len(self.cache_producto_talla_by_sku)} productos_talla en caché')

        # ✅ Pre-cargar IDs de movimientos ya migrados para evitar duplicados
        self.stdout.write('  ⏳ Cargando movimientos existentes...')
        movimientos_existentes = set(
            Movimientos_Producto.objects.filter(
                referencia_externa__startswith='MIG:'
            ).values_list('referencia_externa', flat=True)
        )
        self.stdout.write(f'  ✓ {len(movimientos_existentes)} movimientos ya migrados')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM movimiento_productos')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        # ✅ CORRECCIÓN: La tabla movimiento_productos YA TIENE campo alias directamente
        cursor.execute(f'''
            SELECT 
                id as mysql_id, codigo_asociado, cantidad, fecha, responsable,
                tipo_movimiento, concepto, costo, precio_salida, N_documento, alias
            FROM movimiento_productos
            ORDER BY fecha, id
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        skipped = 0
        duplicados = 0
        batch = []
        batch_size = 5000  # ⚡ Batch grande para movimientos
        error_log_interval = 1000

        for idx, row in enumerate(cursor, 1):
            # ✅ Verificar si ya fue migrado (por ID de MySQL)
            ref_externa = f"MIG:{row['mysql_id']}"
            if ref_externa in movimientos_existentes:
                duplicados += 1
                continue

            codigo_asociado = self.safe_int(row['codigo_asociado'])
            alias = row.get('alias')
            
            # ⚡ BÚSQUEDA ULTRA-RÁPIDA en caché de memoria
            producto_talla = self.find_producto_talla_fast(codigo_asociado, alias)

            if not producto_talla:
                skipped += 1
                if skipped % error_log_interval == 0:
                    self.log_error(f'SKU no encontrado (error #{skipped}): {codigo_asociado} alias:{alias}', log_every=error_log_interval)
                continue

            tipo_movimiento = TIPO_MOVIMIENTO_MAP.get(row['tipo_movimiento'], 'INGRESO')
            concepto = resolver_concepto(row['concepto'], row['tipo_movimiento'])

            if not self.dry_run:
                # ✅ Buscar sucursal por alias
                sucursal_origen = self.cache_sucursales.get(alias) if alias else None
                
                # ✅ Extraer fecha y hora por separado del datetime de MySQL
                fecha_mysql = row['fecha']
                
                movimiento = Movimientos_Producto(
                    ProductoTalla=producto_talla,
                    tipo_movimiento=tipo_movimiento,
                    concepto=concepto,
                    cantidad=self.safe_int(row['cantidad']),
                    costo=self.safe_int(row['costo']),
                    precio=self.safe_int(row['precio_salida']),
                    fecha=self.safe_date_only(fecha_mysql),
                    hora=self.safe_time_only(fecha_mysql),
                    responsable=row['responsable'] or 'Sistema',
                    referencia_externa=ref_externa,  # ✅ Guardar ID de MySQL
                    estado='COMPLETADO',
                    sucursal_origen=sucursal_origen,  # ✅ NUEVO: Asignar sucursal origen
                )
                batch.append(movimiento)
                movimientos_existentes.add(ref_externa)  # ✅ Agregar al set para esta sesión
                
                # ⚡ Insertar en batches grandes
                if len(batch) >= batch_size:
                    Movimientos_Producto.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            # ⚡ Reducir frecuencia de actualizaciones de progreso
            if idx % 500 == 0:
                self.show_progress(idx, total, f'│ {count:,} OK, {skipped} skip, {duplicados} dup')

        if batch and not self.dry_run:
            Movimientos_Producto.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['movimientos'] = count
        self.stats['movimientos_skipped'] = skipped
        self.stats['movimientos_duplicados'] = duplicados
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} movimientos migrados ({skipped} omitidos, {duplicados} duplicados)'))
        
        # ⚡ FAST MODE: Re-habilitar triggers
        self.disable_fast_mode(['app_movimientos_producto'])

    def migrate_dtes(self):
        """⚡ Migra DTEs desde MySQL (OPTIMIZADO con bulk_create)"""
        self.stdout.write('📄 Migrando DTEs...')
        
        # ⚡ FAST MODE: Deshabilitar triggers para inserción masiva
        self.enable_fast_mode(['app_dte'])

        # ✅ PASO 1: Pre-cargar DTEs existentes para evitar duplicados
        self.stdout.write('  ⏳ Cargando DTEs existentes...')
        dtes_existentes = set(
            Dte.objects.values_list('numero_documento', 'tipo_documento', 'emisor_id')
        )
        self.stdout.write(f'  ✓ {len(dtes_existentes):,} DTEs ya en BD')

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM dte')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT 
                ID, rut_emisor, rut_cliente, tipo_documento, n_documento,
                forma_pago, monto_total, iva, neto, fecha_emision, fecha_vence,
                vendedor, responsable, estado, factura_asociada_nc, referencia,
                bodega_inicio, bodega_destino, descuento, monto_nc
            FROM dte
            ORDER BY fecha_emision, n_documento
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        skipped = 0
        duplicados = 0
        batch = []
        batch_size = 5000  # ⚡ Batch grande para DTEs

        for idx, row in enumerate(cursor, 1):
            # ✅ Determinar sucursal - bodega_inicio es la sucursal principal del DTE
            alias_origen = row['bodega_inicio']
            alias_destino = row['bodega_destino'] or ''
            sucursal = self.cache_sucursales.get(alias_origen) if alias_origen else None
            if not sucursal and alias_destino and alias_destino != '0':
                sucursal = self.cache_sucursales.get(alias_destino)
            
            # Buscar empresas emisor y receptor
            emisor = self.cache_empresas_rut.get(row['rut_emisor'])
            receptor = self.cache_empresas_rut.get(row['rut_cliente'])
            
            # Si no hay emisor, usar la empresa de la sucursal
            if not emisor and sucursal:
                emisor = sucursal.empresa
            
            if not emisor:
                skipped += 1
                continue

            # Mapear tipo de documento
            tipo_doc_mysql = row['tipo_documento'] or ''
            tipo_documento = self._mapear_tipo_documento(tipo_doc_mysql)
            
            # ✅ Verificar duplicado ANTES de crear objeto
            n_documento = self.safe_int(row['n_documento'])
            dte_key = (n_documento, tipo_documento, emisor.id)
            if dte_key in dtes_existentes:
                duplicados += 1
                continue
            
            # Mapear estado
            estado_dte = ESTADO_DTE_MAP.get(row['estado'], 'EMITIDO')
            
            # Mapear forma de pago a estado_pago
            estado_pago = self._mapear_estado_pago(row['forma_pago'])
            
            # Calcular días de crédito
            fecha_emision = self.safe_date(row['fecha_emision'])
            fecha_vence = self.safe_date(row['fecha_vence'])
            if fecha_emision and fecha_vence:
                # Normalizar ambas fechas a date para evitar errores de tipos
                f_emision = fecha_emision.date() if hasattr(fecha_emision, 'date') else fecha_emision
                f_vence = fecha_vence.date() if hasattr(fecha_vence, 'date') else fecha_vence
                dias_credito = max(0, (f_vence - f_emision).days)
            else:
                dias_credito = 0
            
            # Determinar tipo de transacción
            rut_emisor = row['rut_emisor'] or ''
            es_emisor_propio = rut_emisor in RUTS_EMPRESAS_PROPIAS

            bodega_dest = row['bodega_destino'] or ''
            es_traspaso_real = (
                row['bodega_inicio'] and bodega_dest
                and bodega_dest != '0' and bodega_dest != row['bodega_inicio']
                and tipo_documento in ('DESPACHO ELECTRONICO', 'GUIA')
            )

            if es_traspaso_real:
                tipo_transaccion = 'TRASPASO'
            elif 'BOLETA' in tipo_doc_mysql.upper() or tipo_documento in ('BOLETA ELECTRONICA', 'BOLETA PAPEL'):
                tipo_transaccion = 'VENTA_PUBLICO'
            elif 'NOTA DE CREDITO' in tipo_doc_mysql.upper() or tipo_documento == 'NOTA DE CREDITO':
                tipo_transaccion = 'NOTA_CREDITO'
            elif es_emisor_propio:
                tipo_transaccion = 'VENTA'
            else:
                tipo_transaccion = 'COMPRA'

            if not self.dry_run:
                batch.append(Dte(
                    numero_documento=n_documento,
                    tipo_documento=tipo_documento,
                    emisor=emisor,
                    receptor=receptor,
                    monto_con_iva=self.safe_decimal(row['monto_total']),
                    monto_neto=self.safe_decimal(row['neto']),
                    estado_pago=estado_pago,
                    estado_dte=estado_dte,
                    responsable=row['responsable'] or 'Sistema',
                    fecha_emision=fecha_emision,
                    fecha_vencimiento=fecha_vence or fecha_emision,
                    diasCredito=dias_credito,
                    bultos=0,
                    unidades_productos=0,
                    descuento=self.safe_decimal(row['descuento'] or 0),
                    sucursal=sucursal,
                    tipo_transaccion=tipo_transaccion,
                    referencias=row['referencia'] or '',
                ))
                dtes_existentes.add(dte_key)  # ✅ Agregar al set para esta sesión
                
                # ⚡ Insertar en batches grandes
                if len(batch) >= batch_size:
                    Dte.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 500 == 0:
                self.show_progress(idx, total, f'│ {count:,} OK, {skipped} skip, {duplicados} dup')

        # ⚡ Insertar batch final
        if batch and not self.dry_run:
            Dte.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['dtes'] = count
        self.stats['dtes_skipped'] = skipped
        self.stats['dtes_duplicados'] = duplicados
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count:,} DTEs migrados ({skipped} omitidos, {duplicados} duplicados)'))
        
        # ⚡ FAST MODE: Re-habilitar triggers
        self.disable_fast_mode(['app_dte'])

    def _mapear_tipo_documento(self, tipo_mysql):
        """Mapea tipo de documento de MySQL a Django"""
        tipo_upper = (tipo_mysql or '').upper().strip()
        
        # Mapeo directo de valores de MySQL
        mapeo_directo = {
            'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
            'DESPACHO ELECTRONICO': 'GUIA',  # Guía de despacho
            'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
            'BOLETA': 'BOLETA PAPEL',  # ✅ Boleta manual/papel
            'NOTA DE CREDITO': 'NOTA DE CREDITO',
            'NOTA DE DEBITO': 'NOTA DE DEBITO',
            'FACTURA EXENTA': 'FACTURA EXENTA',
            'GUIA': 'GUIA',
            'GUIA DESPACHO': 'GUIA',
        }
        
        # Buscar coincidencia directa
        if tipo_upper in mapeo_directo:
            return mapeo_directo[tipo_upper]
        
        # Buscar por contenido
        if 'FACTURA' in tipo_upper and 'EXENTA' not in tipo_upper:
            return 'FACTURA ELECTRONICA'
        elif 'EXENTA' in tipo_upper:
            return 'FACTURA EXENTA'
        elif 'BOLETA' in tipo_upper and 'ELECTRONICA' in tipo_upper:
            return 'BOLETA ELECTRONICA'
        elif 'BOLETA' in tipo_upper:
            return 'BOLETA PAPEL'  # ✅ Boleta sin "ELECTRONICA" = papel
        elif 'DESPACHO' in tipo_upper or 'GUIA' in tipo_upper:
            return 'GUIA'
        elif 'CREDITO' in tipo_upper or 'NC' in tipo_upper:
            return 'NOTA DE CREDITO'
        elif 'DEBITO' in tipo_upper or 'ND' in tipo_upper:
            return 'NOTA DE DEBITO'
        else:
            return 'BOLETA ELECTRONICA'  # Default

    def _mapear_estado_pago(self, forma_pago):
        """Mapea forma de pago de MySQL a estado_pago de Django"""
        forma = (forma_pago or '').upper()
        
        if 'CONTADO' in forma or 'EFECTIVO' in forma or 'PAGADO' in forma:
            return 'PAGADO'
        elif 'CREDITO' in forma:
            return 'PENDIENTE'
        elif 'PARCIAL' in forma:
            return 'PARCIAL'
        else:
            return 'PENDIENTE'  # Default

    def migrate_dte_productos(self):
        """⚡ Migra detalle de productos de DTEs desde MySQL (OPTIMIZADO)"""
        self.stdout.write('📦 Migrando DTE Productos...')
        
        # ⚡ FAST MODE: Deshabilitar triggers para inserción masiva
        self.enable_fast_mode(['app_dte_productos'])

        def normalize_date(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return None

        # ✅ Pre-cargar DTE_Productos existentes para evitar duplicados
        self.stdout.write('  ⏳ Cargando DTE productos existentes...')
        dte_productos_existentes = set(
            Dte_Productos.objects.values_list('dte_id', 'productoTalla_id', 'stock')
        )
        self.stdout.write(f'  ✓ {len(dte_productos_existentes)} DTE productos ya migrados')

        # ✅ Cargar DTEs MySQL para mapear IdDte (ID real de MySQL)
        self.stdout.write('  ⏳ Cargando DTEs MySQL para mapeo por IdDte...')
        mysql_dtes_by_id = {}
        cursor_mysql_dte = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor_mysql_dte.execute('''
            SELECT ID, n_documento, tipo_documento, bodega_inicio, fecha_emision
            FROM dte
        ''')
        for row in cursor_mysql_dte:
            mysql_dtes_by_id[row['ID']] = {
                'numero': row['n_documento'],
                'tipo': row['tipo_documento'],
                'alias': row['bodega_inicio'],
                'fecha': row['fecha_emision'],
            }
        cursor_mysql_dte.close()
        self.stdout.write(f'  ✓ {len(mysql_dtes_by_id)} DTEs MySQL en cache')

        # Pre-cargar DTEs para búsqueda rápida
        cache_dtes_by_num_tipo = {}
        cache_dtes_by_num_tipo_alias = {}
        cache_dtes_by_num_tipo_date = {}
        cache_dtes_by_num_tipo_alias_date = {}
        cache_dtes_by_num = {}  # Índice por número de documento
        for dte in Dte.objects.select_related('emisor').all():
            alias = dte.sucursal.alias if dte.sucursal else None
            fecha_emision = normalize_date(dte.fecha_emision)
            key_num_tipo = (dte.numero_documento, dte.tipo_documento)
            cache_dtes_by_num_tipo[key_num_tipo] = dte
            if alias:
                cache_dtes_by_num_tipo_alias[(dte.numero_documento, dte.tipo_documento, alias)] = dte
            if fecha_emision:
                cache_dtes_by_num_tipo_date[(dte.numero_documento, dte.tipo_documento, fecha_emision)] = dte
            if alias and fecha_emision:
                cache_dtes_by_num_tipo_alias_date[(dte.numero_documento, dte.tipo_documento, alias, fecha_emision)] = dte
            # Índice adicional solo por número
            if dte.numero_documento not in cache_dtes_by_num:
                cache_dtes_by_num[dte.numero_documento] = dte

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM productos_dte')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit

        cursor.execute(f'''
            SELECT 
                ID, factura_asociada, codigo_asociado, articulo, descripcion,
                talla, cantidad, precio_interno, precio_publico, costo,
                tipo_documento, codigo_barra, bodega_inicio, marca, color,
                IdDte, estado, fecha_creacion
            FROM productos_dte
            ORDER BY ID
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        batch = []
        count = 0
        skipped = 0
        duplicados = 0

        for idx, row in enumerate(cursor, 1):
            # Buscar el DTE asociado
            dte = None

            # ✅ Omitir registros sin IdDte y sin fecha_creacion
            if not row['IdDte'] and not row['fecha_creacion']:
                skipped += 1
                continue
            
            # ✅ 1) Primero intentar por IdDte (ID real de MySQL)
            if row['IdDte']:
                mysql_dte = mysql_dtes_by_id.get(row['IdDte'])
                if mysql_dte:
                    numero = mysql_dte['numero']
                    tipo = self._mapear_tipo_documento(mysql_dte['tipo'] or '')
                    alias = mysql_dte['alias']
                    fecha = normalize_date(mysql_dte['fecha'])
                    if numero:
                        dte = cache_dtes_by_num_tipo_alias_date.get((numero, tipo, alias, fecha))
                        if not dte and alias:
                            dte = cache_dtes_by_num_tipo_alias.get((numero, tipo, alias))
                        if not dte and fecha:
                            dte = cache_dtes_by_num_tipo_date.get((numero, tipo, fecha))
                        if not dte:
                            dte = cache_dtes_by_num_tipo.get((numero, tipo))
                        if not dte:
                            dte = cache_dtes_by_num.get(numero)
            
            # ✅ 2) Luego por factura_asociada + tipo_documento + bodega_inicio + fecha_creacion
            if not dte and row['factura_asociada']:
                numero = row['factura_asociada']
                tipo = self._mapear_tipo_documento(row['tipo_documento'] or '')
                alias = row['bodega_inicio']
                fecha = normalize_date(row['fecha_creacion'])
                dte = cache_dtes_by_num_tipo_alias_date.get((numero, tipo, alias, fecha))
                if not dte and alias:
                    dte = cache_dtes_by_num_tipo_alias.get((numero, tipo, alias))
                if not dte and fecha:
                    dte = cache_dtes_by_num_tipo_date.get((numero, tipo, fecha))
                if not dte:
                    dte = cache_dtes_by_num_tipo.get((numero, tipo))
                if not dte:
                    dte = cache_dtes_by_num.get(numero)
            
            if not dte:
                skipped += 1
                if skipped <= 10:
                    self.log_error(f'DTE no encontrado para producto: IdDte={row["IdDte"]}, factura={row["factura_asociada"]}, tipo={row["tipo_documento"]}')
                continue

            # Buscar Producto_Talla por codigo_asociado (SKU)
            sku = str(row['codigo_asociado']) if row['codigo_asociado'] else None
            producto_talla = None
            
            if sku:
                # Intentar con alias de bodega
                alias = row['bodega_inicio']
                if alias:
                    producto_talla = self.cache_producto_talla.get(f"{sku}:{alias}")
                
                # Si no, buscar solo por SKU
                if not producto_talla:
                    producto_talla = self.cache_producto_talla_by_sku.get(sku)

            if not producto_talla:
                skipped += 1
                continue

            # Calcular sobreprecio
            costo = self.safe_int(row['costo']) or 0
            precio_interno = self.safe_int(row['precio_interno']) or 0
            sobreprecio = max(0, precio_interno - costo)
            
            precio = self.safe_int(row['precio_publico']) or precio_interno
            stock = self.safe_int(row['cantidad']) or 0

            # ✅ Verificar si ya existe esta combinación
            dup_key = (dte.id, producto_talla.id, stock)
            if dup_key in dte_productos_existentes:
                duplicados += 1
                continue

            if not self.dry_run:
                batch.append(Dte_Productos(
                    dte=dte,
                    productoTalla=producto_talla,
                    descripcion=row['descripcion'] or row['articulo'] or '',
                    costo=costo,
                    sobreprecio=sobreprecio,
                    precio=precio,
                    stock=stock,
                    activo=(row['estado'] or '').upper() != 'ANULADO'
                ))
                dte_productos_existentes.add(dup_key)  # ✅ Agregar al set

                if len(batch) >= self.batch_size:
                    Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1

            if idx % 500 == 0:
                self.show_progress(idx, total, f'│ {count} OK, {skipped} skip, {duplicados} dup')

        if batch and not self.dry_run:
            Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()
        self.stats['dte_productos'] = count
        self.stats['dte_productos_skipped'] = skipped
        self.stats['dte_productos_duplicados'] = duplicados
        self.stdout.write('\n' + self.style.SUCCESS(f'  ✓ {count} DTE productos migrados ({skipped} omitidos, {duplicados} duplicados)'))
        
        # ⚡ FAST MODE: Re-habilitar triggers
        self.disable_fast_mode(['app_dte_productos'])

    def migrate_ventas_pagos(self):
        """Migra pagos de ventas para cuadratura desde MySQL.
        Busca DTEs por (numero_documento + sucursal) para evitar cruce entre empresas."""
        self.stdout.write('💳 Migrando pagos de ventas...')

        # =====================================================================
        # PASO 1: CARGAR DTEs DE POSTGRESQL CON MÚLTIPLES ÍNDICES
        # =====================================================================
        self.stdout.write('  ⏳ Cargando cachés de PostgreSQL...')

        # Mapeo direccion MySQL → sucursal_id (ventas.sucursal es dirección)
        cache_suc_por_dir = {}
        cache_suc_por_alias = {}
        cache_suc_empresa = {}  # sucursal_id → empresa_id
        cache_suc_obj = {}      # sucursal_id → Sucursal object (para crear DTE on-the-fly)
        for suc in Sucursal.objects.select_related('empresa').all():
            if suc.direccion:
                cache_suc_por_dir[suc.direccion] = suc.id
            cache_suc_por_alias[suc.alias] = suc.id
            cache_suc_empresa[suc.id] = suc.empresa_id
            cache_suc_obj[suc.id] = suc

        # Emisor por defecto para crear DTEs on-the-fly cuando no hay sucursal
        emisor_pagos_default = None
        for rut in ['78503140-7', '76104936-4', '76337843-8', '7397811-4']:
            emisor_pagos_default = self.cache_empresas_rut.get(rut)
            if emisor_pagos_default:
                break
        if not emisor_pagos_default:
            emisor_pagos_default = Empresa.objects.first()

        # Mapa tipo documento MySQL → Django
        TIPO_DOC_MAP_PAGOS = {
            'Factura Electronica': 'FACTURA ELECTRONICA',
            'Boleta Electronica': 'BOLETA ELECTRONICA',
            'Boleta': 'BOLETA PAPEL',
            'Nota de Credito': 'NOTA DE CREDITO',
        }
        TIPO_TRANSACCION_MAP_PAGOS = {
            'BOLETA ELECTRONICA': 'VENTA_PUBLICO',
            'BOLETA PAPEL': 'VENTA_PUBLICO',
            'FACTURA ELECTRONICA': 'VENTA',
            'NOTA DE CREDITO': 'NOTA_CREDITO',
        }

        dtes_creados_otf = 0  # on-the-fly

        # Cache de fechas por dte_id para validar compatibilidad en todos los pasos
        cache_dte_fechas = {}
        for dte in Dte.objects.only('id', 'fecha_emision'):
            cache_dte_fechas[dte.id] = dte.fecha_emision

        def fechas_compatibles(dte_id_check, venta_fecha, max_days=730):
            """True si las fechas son compatibles o si alguna no está disponible."""
            if not venta_fecha:
                return True  # Sin fecha de venta, no podemos rechazar
            dte_fecha = cache_dte_fechas.get(dte_id_check)
            if not dte_fecha:
                return True  # Sin fecha de DTE, no podemos rechazar
            try:
                vf = venta_fecha.date() if hasattr(venta_fecha, 'date') else venta_fecha
                df = dte_fecha.date() if hasattr(dte_fecha, 'date') else dte_fecha
                return abs((df - vf).days) <= max_days
            except Exception:
                return True  # Error comparando, no rechazar

        # Índices de DTEs: de más preciso a menos preciso
        cache_dtes_num_suc = {}       # (numero_documento, sucursal_id) → dte_id  [MÁS PRECISO]
        cache_dtes_num_tipo_suc = {}  # (numero_documento, tipo_documento, sucursal_id) → dte_id
        cache_dtes_num_empresa = {}   # (numero_documento, empresa_id) → dte_id  [FALLBACK: mismo holding]
        cache_dtes_num = {}           # numero_documento → (dte_id, fecha_emision)  [FALLBACK FINAL]

        for dte in Dte.objects.select_related('sucursal__empresa').only(
            'id', 'numero_documento', 'tipo_documento', 'sucursal_id', 'emisor_id', 'fecha_emision'
        ):
            suc_id = dte.sucursal_id
            num = dte.numero_documento

            if suc_id:
                cache_dtes_num_suc[(num, suc_id)] = dte.id
                cache_dtes_num_tipo_suc[(num, dte.tipo_documento, suc_id)] = dte.id
                empresa_id = cache_suc_empresa.get(suc_id)
                if empresa_id and (num, empresa_id) not in cache_dtes_num_empresa:
                    cache_dtes_num_empresa[(num, empresa_id)] = dte.id

            # Guardar con fecha para evitar asignar pagos antiguos a DTEs nuevos con el mismo folio
            if num not in cache_dtes_num:
                cache_dtes_num[num] = (dte.id, dte.fecha_emision)

        # Cargar DTEs MySQL por ID para resolver ID_dte → n_documento
        cache_mysql_dte_id = {}
        cursor_dte = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor_dte.execute('SELECT ID, n_documento, bodega_inicio FROM dte')
        for row in cursor_dte:
            cache_mysql_dte_id[row['ID']] = row
        cursor_dte.close()

        self.stdout.write(f'  ✓ {len(cache_dtes_num_suc):,} DTEs indexados por (num+sucursal)')

        # Pagos existentes para dedup
        pagos_existentes = set(
            Dte_Detalle_Pago.objects.values_list('dte_id', 'voucher', 'monto')
        )
        self.stdout.write(f'  ✓ {len(pagos_existentes):,} pagos existentes')

        # =====================================================================
        # PASO 2: CARGAR VENTAS DE MySQL
        # =====================================================================
        self.stdout.write('  ⏳ Descargando datos de MySQL...')

        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute(f'''
            SELECT
                ID, tipo_documento, metodo_pago, n_documento, tarjeta,
                sub_total, descuento, monto_pagado, sucursal, fecha,
                voucher, n_convenio, correlativo_ticket, responsable,
                nombre_vendedor, hora, rut_convenio, descuento_tbk, estado, ID_dte
            FROM ventas
            ORDER BY ID
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        ventas_mysql = cursor.fetchall()
        cursor.close()

        total = len(ventas_mysql)
        self.stdout.write(f'  ✓ {total:,} ventas descargadas de MySQL')

        # =====================================================================
        # PASO 3: MAPEOS DE MÉTODOS DE PAGO
        # =====================================================================
        metodo_pago_map = {
            'Efectivo': 'EFECTIVO',
            'Tarjeta TBK': 'TBK_MANUAL',
            'Tarjeta TBK Pos Integrado': 'TBK_POS_INTEGRADO',
            'Tarjeta Comercial': 'TARJETA_COMERCIAL',
            'Convenio': 'CONVENIO',
            'Credito': 'CREDITO_EXTERNO',
            'Credito Trabajador': 'CREDITO_TRABAJADOR',
            'Credito Orden Compra': 'ORDEN_COMPRA',
            'Orden Compra': 'ORDEN_COMPRA',
            'Transferencia': 'TRANSFERENCIA',
            'Venta Internet': 'VENTA_INTERNET',
            'Nota de Credito': 'EFECTIVO',
        }

        tarjeta_metodo_map = {
            'REDCOMPRA DEBITO': 'TBK_DEBITO_POS',
            'VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
            ' VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
            'Tarjeta TBK': 'TBK_CREDITO_POS',
            'HITES': 'TARJETA_COMERCIAL',
            'RIPLEY': 'TARJETA_COMERCIAL',
            'ABCDIN': 'TARJETA_COMERCIAL',
            'PRESTO': 'TARJETA_COMERCIAL',
            'TRICOT': 'TARJETA_COMERCIAL',
            'Mercado Pago': 'VENTA_INTERNET',
            'Mercado Libre': 'VENTA_INTERNET',
            'Paris': 'VENTA_INTERNET',
            'Falabella': 'VENTA_INTERNET',
            'Shopify': 'VENTA_INTERNET',
            'Credito': 'CREDITO_EXTERNO',
            'Web Pay': 'TBK_CREDITO_POS',
            'Transferencia': 'TRANSFERENCIA',
        }

        # =====================================================================
        # PASO 4: PROCESAR CON BÚSQUEDA POR SUCURSAL
        # =====================================================================
        self.stdout.write('  ⏳ Procesando...')

        count = 0
        duplicados = 0
        dte_no_encontrado = 0
        batch = []
        batch_size = 5000

        for idx, row in enumerate(ventas_mysql, 1):
            n_doc = row['n_documento']
            if not n_doc:
                dte_no_encontrado += 1
                continue

            # Resolver sucursal_id desde ventas.sucursal (dirección)
            sucursal_mysql = row['sucursal']
            suc_id = None
            if sucursal_mysql:
                suc_id = cache_suc_por_dir.get(sucursal_mysql) or cache_suc_por_alias.get(sucursal_mysql)

            # Búsqueda escalonada: más preciso → menos preciso
            dte_id = None
            venta_fecha = row.get('fecha')

            # 1) numero + sucursal (más preciso, evita cruce entre empresas)
            if suc_id:
                candidato = cache_dtes_num_suc.get((n_doc, suc_id))
                if candidato and fechas_compatibles(candidato, venta_fecha):
                    dte_id = candidato

            # 2) Resolver via ID_dte de MySQL → n_documento + alias del padre
            if not dte_id and row['ID_dte']:
                padre = cache_mysql_dte_id.get(row['ID_dte'])
                if padre:
                    alias_padre = padre['bodega_inicio']
                    suc_id_padre = cache_suc_por_alias.get(alias_padre) if alias_padre else None
                    if suc_id_padre:
                        candidato = cache_dtes_num_suc.get((padre['n_documento'], suc_id_padre))
                        if candidato and fechas_compatibles(candidato, venta_fecha):
                            dte_id = candidato
                    if not dte_id:
                        fallback_padre = cache_dtes_num.get(padre['n_documento'])
                        if fallback_padre:
                            fp_id, fp_fecha = fallback_padre
                            if fechas_compatibles(fp_id, venta_fecha):
                                dte_id = fp_id

            # 3) Fallback por empresa: DTE del mismo holding aunque bodega_inicio != ventas.sucursal
            # Cubre el caso PAO2/3/4 donde el DTE fue asignado a PA00 pero la venta fue en sucursal específica
            if not dte_id and suc_id:
                empresa_id = cache_suc_empresa.get(suc_id)
                if empresa_id:
                    candidato = cache_dtes_num_empresa.get((n_doc, empresa_id))
                    if candidato and fechas_compatibles(candidato, venta_fecha):
                        dte_id = candidato

            # 4) Fallback: solo por número, validando fecha (evita asignar pagos de 2019 a DTEs 2026)
            if not dte_id:
                fallback = cache_dtes_num.get(n_doc)
                if fallback:
                    fb_dte_id, fb_fecha = fallback
                    # Solo asignar si AMBAS fechas están disponibles y son compatibles
                    if venta_fecha and fb_fecha:
                        try:
                            venta_date = venta_fecha.date() if hasattr(venta_fecha, 'date') else venta_fecha
                            dte_date = fb_fecha.date() if hasattr(fb_fecha, 'date') else fb_fecha
                            diff_days = abs((dte_date - venta_date).days)
                            if diff_days <= 730:  # máximo 2 años de diferencia
                                dte_id = fb_dte_id
                        except Exception:
                            pass  # si hay error comparando fechas, no asignar

            if not dte_id:
                # 5) Crear DTE on-the-fly para esta venta huerfana
                if not self.dry_run and n_doc and row.get('tipo_documento'):
                    try:
                        tipo_mysql = row['tipo_documento'] or 'Boleta Electronica'
                        tipo_pg = TIPO_DOC_MAP_PAGOS.get(tipo_mysql, 'BOLETA ELECTRONICA')
                        tipo_transaccion = TIPO_TRANSACCION_MAP_PAGOS.get(tipo_pg, 'VENTA_PUBLICO')

                        suc_obj = cache_suc_obj.get(suc_id) if suc_id else None
                        emisor_otf = suc_obj.empresa if suc_obj else emisor_pagos_default
                        if not emisor_otf:
                            dte_no_encontrado += 1
                            continue

                        nuevo_dte = Dte.objects.create(
                            numero_documento=n_doc,
                            tipo_documento=tipo_pg,
                            tipo_transaccion=tipo_transaccion,
                            monto_total=self.safe_int(row.get('sub_total')) or 0,
                            sucursal=suc_obj,
                            emisor=emisor_otf,
                            fecha_emision=row.get('fecha'),
                            estado_dte='EMITIDO',
                        )
                        dte_id = nuevo_dte.id
                        # Actualizar caches para filas futuras de esta misma venta
                        cache_dtes_num[n_doc] = (dte_id, nuevo_dte.fecha_emision)
                        if suc_id:
                            cache_dtes_num_suc[(n_doc, suc_id)] = dte_id
                            empresa_id_suc = cache_suc_empresa.get(suc_id)
                            if empresa_id_suc:
                                cache_dtes_num_empresa[(n_doc, empresa_id_suc)] = dte_id
                        dtes_creados_otf += 1
                    except Exception as e:
                        self.log_error(f'Error creando DTE OTF n_doc={n_doc}: {e}')
                        dte_no_encontrado += 1
                        continue
                else:
                    dte_no_encontrado += 1
                    continue

            # Mapear método de pago
            metodo_mysql = row['metodo_pago'] or 'Efectivo'
            metodo_pago = metodo_pago_map.get(metodo_mysql, 'EFECTIVO')

            tarjeta = (row['tarjeta'] or '').strip()
            if tarjeta in tarjeta_metodo_map:
                metodo_pago = tarjeta_metodo_map[tarjeta]
            elif tarjeta.startswith('OrdenCompra'):
                metodo_pago = 'ORDEN_COMPRA'

            # Verificar duplicado
            voucher = str(row['voucher']) if row['voucher'] else f"MIG-{row['ID']}"
            monto = self.safe_int(row['monto_pagado']) or 0
            dup_key = (dte_id, voucher, monto)

            if dup_key in pagos_existentes:
                duplicados += 1
                continue

            # Notas con contexto
            notas_partes = []
            if row['n_convenio']:
                notas_partes.append(f"Convenio: {row['n_convenio']}")
            if row['rut_convenio']:
                notas_partes.append(f"RUT Conv: {row['rut_convenio']}")
            if row['nombre_vendedor']:
                notas_partes.append(f"Vendedor: {row['nombre_vendedor']}")
            if row['correlativo_ticket']:
                notas_partes.append(f"Ticket: {row['correlativo_ticket']}")
            notas = ' | '.join(notas_partes) if notas_partes else None

            if not self.dry_run:
                batch.append(Dte_Detalle_Pago(
                    dte_id=dte_id,
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=row['tarjeta'] or None,
                    voucher=voucher,
                    monto=monto,
                    notas=notas
                ))
                pagos_existentes.add(dup_key)

                if len(batch) >= batch_size:
                    Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
                    self.show_progress(idx, total, f'│ {count:,} insertados')
            else:
                count += 1

            if idx % 10000 == 0:
                self.show_progress(idx, total, f'│ {count:,} OK, {duplicados:,} dup')

        if batch and not self.dry_run:
            Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        del ventas_mysql

        self.stats['ventas_pagos'] = count
        self.stats['ventas_pagos_duplicados'] = duplicados
        self.stats['ventas_pagos_dte_no_encontrado'] = dte_no_encontrado
        self.stats['ventas_pagos_dtes_creados_otf'] = dtes_creados_otf
        self.stdout.write('\n' + self.style.SUCCESS(
            f'  [OK] {count:,} pagos migrados ({dtes_creados_otf:,} DTEs creados on-the-fly, '
            f'{dte_no_encontrado:,} sin DTE, {duplicados:,} duplicados)'
        ))

    def asignar_vendedores_a_dtes(self):
        """Asigna vendedores a DTEs usando (n_documento + sucursal) para evitar
        confundir folios iguales de diferentes tiendas.
        Los vendedores pueden trabajar en sucursales de cualquier empresa del holding."""
        self.stdout.write('👤 Asignando vendedores a DTEs...')

        from django.db import connection

        # Cache de vendedores por codigo_vendedor (sin restricción de empresa)
        cache_vendedores = {}
        for v in Vendedor.objects.all():
            if v.codigo_vendedor:
                cache_vendedores[str(v.codigo_vendedor)] = v.id
        self.stdout.write(f'  ✓ {len(cache_vendedores)} vendedores en cache')

        # Mapeo dirección/alias → sucursal_id
        cache_suc_por_dir = {}
        cache_suc_por_alias = {}
        for suc in Sucursal.objects.all():
            if suc.direccion:
                cache_suc_por_dir[suc.direccion] = suc.id
            cache_suc_por_alias[suc.alias] = suc.id

        # Cargar mapeo ventas MySQL → codigo_vendedor CON sucursal
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT n_documento, codigo_vendedor, sucursal
            FROM ventas
            WHERE n_documento > 0
            AND codigo_vendedor IS NOT NULL
            AND codigo_vendedor != ''
            AND codigo_vendedor != '0'
            ORDER BY n_documento, ID
        ''')

        mysql_vend_by_num_suc = {}   # (n_documento, sucursal_id) → codigo_vendedor
        mysql_vend_by_num = {}       # n_documento → codigo_vendedor (fallback)
        for row in cursor:
            n_doc = row['n_documento']
            codigo = str(row['codigo_vendedor'])
            suc_mysql = row['sucursal']

            suc_id = None
            if suc_mysql:
                suc_id = cache_suc_por_dir.get(suc_mysql) or cache_suc_por_alias.get(suc_mysql)

            if suc_id and (n_doc, suc_id) not in mysql_vend_by_num_suc:
                mysql_vend_by_num_suc[(n_doc, suc_id)] = codigo

            if n_doc not in mysql_vend_by_num:
                mysql_vend_by_num[n_doc] = codigo

        cursor.close()
        self.stdout.write(f'  ✓ {len(mysql_vend_by_num_suc):,} docs con vendedor+sucursal en MySQL')

        # Cargar DTEs sin vendedor
        with connection.cursor() as c:
            c.execute('''
                SELECT id, numero_documento, sucursal_id
                FROM app_dte
                WHERE vendedor_id IS NULL
            ''')
            dtes_sin_vendedor = c.fetchall()

        total = len(dtes_sin_vendedor)
        self.stdout.write(f'  📊 DTEs sin vendedor: {total:,}')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ Todos los DTEs ya tienen vendedor'))
            self.stats['vendedores_asignados'] = 0
            return

        updates = []
        sin_codigo = 0
        codigo_no_existe = 0

        for dte_id, numero_doc, suc_id in dtes_sin_vendedor:
            # Buscar codigo_vendedor: por (num + sucursal) primero, luego por num solo
            codigo = None
            if suc_id:
                codigo = mysql_vend_by_num_suc.get((numero_doc, suc_id))
            if not codigo:
                codigo = mysql_vend_by_num.get(numero_doc)

            if not codigo:
                sin_codigo += 1
                continue

            vendedor_id = cache_vendedores.get(codigo)
            if not vendedor_id:
                codigo_no_existe += 1
                continue

            updates.append((dte_id, vendedor_id))

        self.stdout.write(f'  📋 Para actualizar: {len(updates):,}')
        self.stdout.write(f'  ⚠️  Sin codigo: {sin_codigo:,}')
        self.stdout.write(f'  ⚠️  Codigo no existe: {codigo_no_existe:,}')

        if not updates or self.dry_run:
            self.stats['vendedores_asignados'] = 0
            return

        batch_size = 5000
        actualizados = 0

        with connection.cursor() as c:
            for i in range(0, len(updates), batch_size):
                batch = updates[i:i + batch_size]
                values_list = ', '.join([f'({dte_id}, {vend_id})' for dte_id, vend_id in batch])

                c.execute(f'''
                    UPDATE app_dte AS d
                    SET vendedor_id = v.vendedor_id
                    FROM (VALUES {values_list}) AS v(dte_id, vendedor_id)
                    WHERE d.id = v.dte_id
                ''')
                actualizados += c.rowcount

        self.stats['vendedores_asignados'] = actualizados
        self.stdout.write(self.style.SUCCESS(f'  ✓ {actualizados:,} vendedores asignados a DTEs'))

    # ========================================================================
    # CREAR DTEs FALTANTES DESDE VENTAS
    # ========================================================================

    def crear_dtes_faltantes(self):
        """Crea DTEs genéricos desde ventas MySQL que no tienen DTE en PostgreSQL"""
        self.stdout.write('[DTEs FALTANTES] Creando DTEs desde ventas sin DTE...')

        # Cargar sucursales por dirección (ventas.sucursal es dirección en MySQL)
        cache_sucursales = {}
        for sucursal in Sucursal.objects.select_related('empresa').all():
            if sucursal.direccion:
                cache_sucursales[sucursal.direccion] = sucursal
            # También por alias como fallback
            cache_sucursales[sucursal.alias] = sucursal
        
        # Cargar vendedores
        cache_vendedores = {}
        for vendedor in Vendedor.objects.all():
            if vendedor.codigo_vendedor:
                cache_vendedores[vendedor.codigo_vendedor] = vendedor
        
        # Emisor se determina por sucursal (cada sucursal pertenece a una empresa)
        # Fallback: primera empresa propia (no cliente)
        emisor_default = None
        for rut in ['78503140-7', '76104936-4', '76337843-8', '7397811-4']:
            emisor_default = self.cache_empresas_rut.get(rut)
            if emisor_default:
                break
        if not emisor_default:
            emisor_default = Empresa.objects.first()
        if not emisor_default:
            self.stdout.write(self.style.WARNING('  [!] No hay empresas, saltando'))
            return
        
        # DTEs existentes — clave (numero, tipo, sucursal_id) SIN monto
        # IMPORTANTE: NO usar monto como parte de la clave.
        dtes_existentes_full = set()      # (numero, tipo_documento, sucursal_id)
        dtes_existentes_num_tipo = set()  # (numero, tipo_documento) — cualquier sucursal
        max_folio_suc_tipo = {}           # (sucursal_id, tipo_documento) → folio máximo conocido
        for num, tipo, suc_id in Dte.objects.values_list(
            'numero_documento', 'tipo_documento', 'sucursal_id'
        ):
            if suc_id:
                dtes_existentes_full.add((num, tipo, suc_id))
                key = (suc_id, tipo)
                if max_folio_suc_tipo.get(key, 0) < num:
                    max_folio_suc_tipo[key] = num
            dtes_existentes_num_tipo.add((num, tipo))
        
        # Mapeo tipo documento
        TIPO_DOC_MAP = {
            'Factura Electronica': 'FACTURA ELECTRONICA',
            'Boleta Electronica': 'BOLETA ELECTRONICA',
            'Boleta': 'BOLETA PAPEL',
            'Nota de Credito': 'NOTA DE CREDITO',
        }
        TIPO_TRANSACCION_MAP = {
            'BOLETA ELECTRONICA': 'VENTA_PUBLICO',
            'BOLETA PAPEL': 'VENTA_PUBLICO',
            'FACTURA ELECTRONICA': 'VENTA',
            'NOTA DE CREDITO': 'NOTA_CREDITO',
        }
        
        # Obtener ventas únicas de MySQL
        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT n_documento, tipo_documento, MAX(sub_total) as sub_total, sucursal,
                   MIN(fecha) as fecha, MIN(codigo_vendedor) as codigo_vendedor
            FROM ventas
            GROUP BY n_documento, tipo_documento, sucursal
        ''')
        
        ventas = cursor.fetchall()
        cursor.close()
        
        count = 0
        batch = []
        
        for venta in ventas:
            n_doc = venta['n_documento']
            sub_total = int(venta['sub_total'] or 0)

            sucursal = cache_sucursales.get(venta['sucursal'])
            if not sucursal and cache_sucursales:
                sucursal = list(cache_sucursales.values())[0]

            suc_id = sucursal.id if sucursal else None

            tipo_mysql = venta['tipo_documento'] or 'Boleta'
            tipo_pg = TIPO_DOC_MAP.get(tipo_mysql, 'BOLETA PAPEL')

            venta_fecha = venta['fecha']

            # Guard 1: ya existe DTE exacto (mismo folio + tipo + sucursal) → skip
            if suc_id and (n_doc, tipo_pg, suc_id) in dtes_existentes_full:
                continue
            # Guard 2: ya existe DTE con mismo folio + tipo en CUALQUIER sucursal → skip
            # (evita crear DTEs fantasma por registros ventas con fechas incorrectas)
            if (n_doc, tipo_pg) in dtes_existentes_num_tipo:
                continue
            # Guard 3: el folio ya fue superado en esta misma sucursal+tipo → el registro es un
            # artefacto histórico con fecha incorrecta en MySQL (ej: folio 4731 para PAO1 en 2026
            # cuando PAO1 ya está en folio 410349) → skip
            if suc_id:
                max_conocido = max_folio_suc_tipo.get((suc_id, tipo_pg), 0)
                if max_conocido > 0 and n_doc < max_conocido:
                    continue

            emisor_dte = sucursal.empresa if sucursal else emisor_default

            tipo_transaccion = TIPO_TRANSACCION_MAP.get(tipo_pg, 'VENTA_PUBLICO')

            vendedor = None
            if venta['codigo_vendedor']:
                vendedor = cache_vendedores.get(venta['codigo_vendedor'])

            fecha = venta_fecha

            if not self.dry_run:
                batch.append(Dte(
                    numero_documento=n_doc,
                    tipo_documento=tipo_pg,
                    tipo_transaccion=tipo_transaccion,
                    monto_neto=int(sub_total / 1.19),
                    monto_con_iva=sub_total,
                    fecha_emision=fecha,
                    fecha_vencimiento=fecha,
                    sucursal=sucursal,
                    vendedor=vendedor,
                    emisor=emisor_dte,
                    estado_dte='EMITIDO',
                    estado_pago='PAGADO',
                    bultos=0,
                    unidades_productos=0,
                    diasCredito=0
                ))
                if suc_id:
                    dtes_existentes_full.add((n_doc, tipo_pg, suc_id))
                dtes_existentes_num_tipo.add((n_doc, tipo_pg))
                
                if len(batch) >= self.batch_size:
                    Dte.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
            else:
                count += 1
        
        if batch and not self.dry_run:
            Dte.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)
        
        self.stats['dtes_faltantes'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count:,} DTEs creados desde ventas'))

    # ========================================================================
    # LIMPIAR DTEs DUPLICADOS
    # ========================================================================

    def fix_dtes_duplicados(self):
        """Elimina DTEs duplicados generados por crear_dtes_faltantes cuando el
        monto_pagado (ventas.sub_total) difería del monto_total del DTE original.

        Estrategia por cada grupo (numero_documento, tipo_documento, sucursal_id):
        - Mantiene el DTE con mayor monto_con_iva (el de la tabla dte, que incluye
          el total bruto antes de descuento).
        - Reasigna todos los Dte_Detalle_Pago del duplicado al DTE correcto.
        - Elimina los DTEs duplicados.
        """
        from django.db import connection

        self.stdout.write('[FIX DUPLICADOS] Buscando DTEs duplicados...')

        with connection.cursor() as cur:
            cur.execute('''
                SELECT numero_documento, tipo_documento, sucursal_id,
                       COUNT(*) as cnt,
                       array_agg(id ORDER BY monto_con_iva DESC, id ASC) as ids
                FROM app_dte
                WHERE sucursal_id IS NOT NULL
                GROUP BY numero_documento, tipo_documento, sucursal_id
                HAVING COUNT(*) > 1
            ''')
            grupos = cur.fetchall()

        if not grupos:
            self.stdout.write(self.style.SUCCESS('  ✓ No se encontraron duplicados'))
            self.stats['dtes_duplicados_eliminados'] = 0
            return

        self.stdout.write(f'  📊 {len(grupos):,} grupos con duplicados')

        eliminados = 0
        pagos_reasignados = 0

        for numero_doc, tipo_doc, suc_id, cnt, ids in grupos:
            correcto_id = ids[0]         # mayor monto_con_iva
            duplicados_ids = ids[1:]     # los demás

            if self.dry_run:
                self.stdout.write(
                    f'  [DRY] Folio {numero_doc} {tipo_doc}: '
                    f'mantener id={correcto_id}, eliminar {duplicados_ids}'
                )
                eliminados += len(duplicados_ids)
                continue

            # Reasignar pagos de duplicados → DTE correcto
            from app.models import Dte_Detalle_Pago
            n_pagos = Dte_Detalle_Pago.objects.filter(
                dte_id__in=duplicados_ids
            ).update(dte_id=correcto_id)
            pagos_reasignados += n_pagos

            # Eliminar duplicados
            Dte.objects.filter(id__in=duplicados_ids).delete()
            eliminados += len(duplicados_ids)

        self.stats['dtes_duplicados_eliminados'] = eliminados
        self.stdout.write(
            self.style.SUCCESS(
                f'  ✓ {eliminados:,} DTEs duplicados eliminados, '
                f'{pagos_reasignados:,} pagos reasignados'
            )
        )

    # ========================================================================
    # CORREGIR DESCUENTOS EN DTEs
    # ========================================================================

    def corregir_descuentos_dte(self):
        """Actualiza app_dte.descuento a partir de ventas.descuento de MySQL.

        Muchos DTEs migrados tienen descuento=0 porque la tabla `dte` de MySQL no
        almacenaba el descuento; éste solo estaba en la tabla `ventas`. Este paso
        lee los descuentos de `ventas` y los aplica a los DTEs existentes.
        """
        from django.db import connection

        self.stdout.write('[DESCUENTOS] Corrigiendo descuentos de DTEs desde ventas...')

        if not self.mysql_conn:
            self.stdout.write(self.style.WARNING('  Sin conexión MySQL, omitiendo'))
            self.stats['descuentos_corregidos'] = 0
            return

        # Leer descuentos desde MySQL ventas
        # Agrupar por (n_documento, sucursal) tomando el MAX(descuento)
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT n_documento, sucursal, MAX(descuento) as descuento
            FROM ventas
            WHERE descuento > 0 AND n_documento > 0
            GROUP BY n_documento, sucursal
        ''')
        mysql_rows = cursor.fetchall()
        cursor.close()
        self.stdout.write(f'  📊 {len(mysql_rows):,} folios con descuento en MySQL')

        if not mysql_rows:
            self.stats['descuentos_corregidos'] = 0
            return

        # Mapeo dirección/alias → sucursal_id
        cache_suc_por_dir = {}
        cache_suc_por_alias = {}
        for suc in Sucursal.objects.all():
            if suc.direccion:
                cache_suc_por_dir[suc.direccion] = suc.id
            cache_suc_por_alias[suc.alias] = suc.id

        # Índice: (numero_documento, sucursal_id) → dte_id
        cache_dtes = {}
        for dte_id, num_doc, suc_id in Dte.objects.values_list('id', 'numero_documento', 'sucursal_id'):
            if suc_id:
                cache_dtes[(num_doc, suc_id)] = dte_id

        updates = []
        sin_dte = 0

        for row in mysql_rows:
            n_doc = row['n_documento']
            descuento = int(row['descuento'] or 0)
            if not descuento:
                continue

            suc_mysql = row['sucursal']
            suc_id = None
            if suc_mysql:
                suc_id = cache_suc_por_dir.get(suc_mysql) or cache_suc_por_alias.get(suc_mysql)

            dte_id = cache_dtes.get((n_doc, suc_id)) if suc_id else None

            if not dte_id:
                sin_dte += 1
                continue

            updates.append((descuento, dte_id))

        self.stdout.write(f'  📊 {len(updates):,} DTEs a actualizar ({sin_dte:,} sin DTE)')

        if self.dry_run or not updates:
            self.stats['descuentos_corregidos'] = len(updates)
            return

        # Bulk update via raw SQL
        batch_size = 5000
        corregidos = 0
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            with connection.cursor() as cur:
                # Only update DTEs where descuento IS NULL or 0
                cases = ' '.join([f'WHEN {dte_id} THEN {desc}' for desc, dte_id in batch])
                ids = ', '.join([str(dte_id) for _, dte_id in batch])
                cur.execute(f'''
                    UPDATE app_dte
                    SET descuento = CASE id {cases} END
                    WHERE id IN ({ids})
                    AND (descuento IS NULL OR descuento = 0)
                ''')
            corregidos += len(batch)
            self.show_progress(corregidos, len(updates), f'│ {corregidos:,} actualizados')

        self.stats['descuentos_corregidos'] = corregidos
        self.stdout.write(self.style.SUCCESS(f'  ✓ {corregidos:,} DTEs con descuento corregido'))

    # ========================================================================
    # CORREGIR FECHAS DE DTEs
    # ========================================================================

    def corregir_fechas_dte(self):
        """Corrige fechas de DTEs comparando con MySQL via (n_documento, rut_emisor).
        Solo revisa DTEs de empresas propias para rendimiento."""
        self.stdout.write('[FECHAS] Corrigiendo fechas de DTEs...')

        from django.db import connection

        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute(
            'SELECT n_documento, rut_emisor, fecha_emision FROM dte '
            'WHERE fecha_emision IS NOT NULL AND rut_emisor IN (%s, %s, %s)',
            tuple(RUTS_EMPRESAS_PROPIAS)
        )
        fechas_mysql = {}
        for row in cursor:
            fechas_mysql[(row['n_documento'], row['rut_emisor'])] = row['fecha_emision']
        cursor.close()
        self.stdout.write(f'  {len(fechas_mysql):,} fechas cargadas de MySQL')

        empresa_rut = {e.id: e.rut for e in Empresa.objects.all()}
        ids_propias = [eid for eid, rut in empresa_rut.items() if rut in RUTS_EMPRESAS_PROPIAS]

        actualizaciones = []
        for dte_id, ndoc, emisor_id, fecha_dj in Dte.objects.filter(
            emisor_id__in=ids_propias
        ).values_list('id', 'numero_documento', 'emisor_id', 'fecha_emision'):
            rut = empresa_rut.get(emisor_id, '')
            fecha_mysql = fechas_mysql.get((ndoc, rut))
            if fecha_mysql and str(fecha_mysql) != str(fecha_dj):
                actualizaciones.append((dte_id, str(fecha_mysql)))

        if not actualizaciones:
            self.stdout.write('  [OK] No hay fechas que corregir')
            return

        if self.dry_run:
            self.stdout.write(f'  [DRY-RUN] Se corregirían {len(actualizaciones):,} fechas')
            return

        with connection.cursor() as pg_cursor:
            for i in range(0, len(actualizaciones), self.batch_size):
                batch = actualizaciones[i:i + self.batch_size]
                cases = [f"WHEN {dte_id} THEN '{fecha}'::date" for dte_id, fecha in batch]
                ids = [str(dte_id) for dte_id, _ in batch]
                sql = f'''
                    UPDATE app_dte
                    SET fecha_emision = CASE id {' '.join(cases)} END,
                        fecha_vencimiento = CASE id {' '.join(cases)} END
                    WHERE id IN ({','.join(ids)})
                '''
                pg_cursor.execute(sql)

        self.stats['fechas_corregidas'] = len(actualizaciones)
        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(actualizaciones):,} fechas corregidas'))

    # ========================================================================
    # CORREGIR SUCURSALES DE DTEs
    # ========================================================================

    def corregir_sucursales_dte(self):
        """Corrige sucursales de DTEs cruzando con tabla dte y ventas de MySQL."""
        self.stdout.write('[SUCURSALES] Corrigiendo sucursales de DTEs...')

        from django.db import connection

        # Cargar sucursales de PostgreSQL
        sucursales_pg_por_dir = {}
        sucursales_pg_por_alias = {}
        for suc in Sucursal.objects.all():
            if suc.direccion:
                sucursales_pg_por_dir[suc.direccion] = suc.id
            sucursales_pg_por_alias[suc.alias] = suc.id

        # Fuente 1: tabla dte de MySQL (usa bodega_inicio = alias, más confiable)
        suc_from_dte = {}  # (n_documento, tipo_documento) → sucursal_id
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT n_documento, tipo_documento, bodega_inicio FROM dte WHERE bodega_inicio IS NOT NULL')
        for row in cursor:
            alias = row['bodega_inicio']
            suc_id = sucursales_pg_por_alias.get(alias)
            if suc_id:
                tipo_pg = self._mapear_tipo_documento(row['tipo_documento'] or '')
                suc_from_dte[(row['n_documento'], tipo_pg)] = suc_id
        cursor.close()

        # Fuente 2: tabla ventas de MySQL (ventas.sucursal = dirección)
        suc_from_ventas = {}  # n_documento → sucursal_id
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT n_documento, sucursal
            FROM ventas
            WHERE sucursal IS NOT NULL AND sucursal != ''
            GROUP BY n_documento, sucursal
        ''')
        for row in cursor:
            suc_id = sucursales_pg_por_dir.get(row['sucursal']) or sucursales_pg_por_alias.get(row['sucursal'])
            if suc_id and row['n_documento'] not in suc_from_ventas:
                suc_from_ventas[row['n_documento']] = suc_id
        cursor.close()

        self.stdout.write(f'  ✓ {len(suc_from_dte):,} DTEs con alias, {len(suc_from_ventas):,} ventas con sucursal')

        actualizaciones = []

        for dte in Dte.objects.select_related('sucursal').only('id', 'numero_documento', 'tipo_documento', 'sucursal').iterator():
            if dte.sucursal_id:
                continue

            suc_id = suc_from_dte.get((dte.numero_documento, dte.tipo_documento))
            if not suc_id:
                suc_id = suc_from_ventas.get(dte.numero_documento)

            if suc_id:
                actualizaciones.append((dte.id, suc_id))
        
        if not actualizaciones:
            self.stdout.write('  [OK] No hay sucursales que corregir')
            return
        
        if self.dry_run:
            self.stdout.write(f'  [DRY-RUN] Se corregirían {len(actualizaciones):,} sucursales')
            return
        
        # Actualizar en batch
        with connection.cursor() as pg_cursor:
            for i in range(0, len(actualizaciones), self.batch_size):
                batch = actualizaciones[i:i + self.batch_size]
                cases = [f"WHEN {dte_id} THEN {suc_id}" for dte_id, suc_id in batch]
                ids = [str(dte_id) for dte_id, _ in batch]
                
                sql = f'''
                    UPDATE app_dte 
                    SET sucursal_id = CASE id {' '.join(cases)} END
                    WHERE id IN ({','.join(ids)})
                '''
                pg_cursor.execute(sql)
        
        self.stats['sucursales_corregidas'] = len(actualizaciones)
        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(actualizaciones):,} sucursales corregidas'))

    def corregir_tipo_transaccion_dte(self):
        """Corrige tipo_transaccion de DTEs mal clasificados:
        1. COMPRA cuando emisor es empresa propia → VENTA/VENTA_PUBLICO/NOTA_CREDITO
        2. TRASPASO cuando bodega_destino='0' en MySQL → VENTA/VENTA_PUBLICO
        """
        self.stdout.write('[TIPO_TRANSACCION] Corrigiendo DTEs mal clasificados...')

        from django.db import connection

        ids_propias = set(
            Empresa.objects.filter(rut__in=RUTS_EMPRESAS_PROPIAS).values_list('id', flat=True)
        )
        if not ids_propias:
            self.stdout.write(self.style.WARNING('  [!] No se encontraron empresas propias'))
            return

        total = 0

        # 1) Boletas propias como COMPRA o TRASPASO → VENTA_PUBLICO
        boletas_mal = Dte.objects.filter(
            emisor_id__in=ids_propias,
            tipo_transaccion__in=['COMPRA', 'TRASPASO'],
            tipo_documento__in=['BOLETA ELECTRONICA', 'BOLETA PAPEL', 'BOLETA']
        ).update(tipo_transaccion='VENTA_PUBLICO')
        self.stdout.write(f'  Boletas -> VENTA_PUBLICO: {boletas_mal:,}')
        total += boletas_mal

        # 2) Facturas propias como COMPRA o TRASPASO → VENTA
        facturas_mal = Dte.objects.filter(
            emisor_id__in=ids_propias,
            tipo_transaccion__in=['COMPRA', 'TRASPASO'],
            tipo_documento__in=['FACTURA ELECTRONICA', 'FACTURA EXENTA', 'FACTURA']
        ).update(tipo_transaccion='VENTA')
        self.stdout.write(f'  Facturas -> VENTA: {facturas_mal:,}')
        total += facturas_mal

        # 3) NC propias como COMPRA o TRASPASO → NOTA_CREDITO
        nc_mal = Dte.objects.filter(
            emisor_id__in=ids_propias,
            tipo_transaccion__in=['COMPRA', 'TRASPASO'],
            tipo_documento__in=['NOTA DE CREDITO', 'NOTA DE DEBITO']
        ).update(tipo_transaccion='NOTA_CREDITO')
        self.stdout.write(f'  NC -> NOTA_CREDITO: {nc_mal:,}')
        total += nc_mal

        self.stats['tipo_transaccion_corregidos'] = total
        self.stdout.write(self.style.SUCCESS(f'  ✓ {total:,} DTEs corregidos'))

    # ========================================================================
    # PARÁMETROS MAESTROS (SKU actual, descuentos, límite de cambios)
    # ========================================================================

    def migrate_parametros_maestros(self):
        """Migra parámetros globales desde parametrosmaestros de MySQL"""
        self.stdout.write('⚙️  Migrando parámetros maestros...')

        from app.models import ParametroGlobal

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT * FROM parametrosmaestros LIMIT 1')
        row = cursor.fetchone()
        cursor.close()

        if not row:
            self.stdout.write(self.style.WARNING('  ⚠️ Tabla parametrosmaestros vacía'))
            return

        params = {
            'codigo_asociado_actual': self.safe_int(row.get('codigo_asociado_actual', 0)),
            'N_listado_precios': self.safe_int(row.get('N_listado_precios', 0)),
            'correlativo_job': self.safe_int(row.get('correlativo_job', 0)),
            'descuento_admin': self.safe_int(row.get('descuentoAdmin', 0)),
            'descuento_jefe': self.safe_int(row.get('descuentoJefe', 0)),
            'descuento_cajera': self.safe_int(row.get('descuentoCajera', 0)),
            'cantidad_cambios': self.safe_int(row.get('Cantidad_cambios', row.get('cantidad_cambios', 3))),
        }

        count = 0
        if not self.dry_run:
            for nombre, valor in params.items():
                _, created = ParametroGlobal.objects.update_or_create(
                    nombre=nombre,
                    defaults={'valor_entero': valor}
                )
                if created:
                    count += 1
                self.stdout.write(f'  {"✓" if created else "🔄"} {nombre} = {valor}')

        self.stats['parametros_maestros'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(params)} parámetros migrados'))

    # ========================================================================
    # CORRELATIVOS (Folios SII)
    # ========================================================================

    def migrate_correlativos(self):
        """Migra control de folios SII desde correlativo_documentos de MySQL"""
        self.stdout.write('📋 Migrando correlativos (folios SII)...')

        from app.models import Correlativo

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT Rut, tipo_documento, correlativo_inicio, correlativo_termino,
                   empresa, cantidad_folios, fecha_folio
            FROM correlativo_documentos
            ORDER BY Rut, tipo_documento
        ''')

        # Mapeo de tipo_documento MySQL → tipo_dte Django (Correlativo)
        tipo_dte_map = {
            'Boleta': 'BOLETA',
            'Boleta Electronica': 'BOLETA_ELECTRONICA',
            'Factura Electronica': 'FACTURA_ELECTRONICA',
            'Nota de Credito': 'NOTA_CREDITO',
            'Despacho Electronico': 'GUIA_DESPACHO',
            'ticket': 'TICKET',
            'ticket_cambio': 'TICKET_CAMBIO',
            'cotizacion': 'COTIZACION',
            'orden_compra': 'ORDEN_COMPRA',
        }

        count = 0
        skipped = 0

        for row in cursor:
            rut = row['Rut']
            tipo_mysql = row['tipo_documento']
            tipo_dte = tipo_dte_map.get(tipo_mysql, tipo_mysql)

            # Buscar la empresa y sucursal por RUT
            empresa = self.cache_empresas_rut.get(rut)
            if not empresa:
                skipped += 1
                continue

            # Buscar la primera sucursal de esta empresa
            sucursal = None
            for alias, suc in self.cache_sucursales.items():
                if suc.empresa_id == empresa.id:
                    sucursal = suc
                    break

            if not sucursal:
                skipped += 1
                continue

            if not self.dry_run:
                correlativo, created = Correlativo.objects.update_or_create(
                    sucursal=sucursal,
                    tipo_dte=tipo_dte,
                    defaults={
                        'inicio': self.safe_int(row['correlativo_inicio']),
                        'termino': self.safe_int(row['correlativo_termino']),
                        'fecha_actualizacion': row['fecha_folio'],
                        'alias': sucursal.alias,
                        'responsable': 'Migración',
                    }
                )
                if created:
                    count += 1

        cursor.close()
        self.stats['correlativos'] = count
        self.stdout.write(self.style.SUCCESS(f'  ✓ {count} correlativos migrados ({skipped} sin empresa/sucursal)'))

    # ========================================================================
    # VINCULAR NOTAS DE CRÉDITO CON FACTURAS ORIGINALES
    # ========================================================================

    def migrate_nc_vinculacion(self):
        """Vincula Notas de Crédito con sus DTEs originales usando factura_asociada_nc de MySQL"""
        self.stdout.write('🔗 Vinculando Notas de Crédito con DTEs originales...')

        from django.db import connection

        # Cargar factura_asociada_nc desde MySQL
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT ID, n_documento, tipo_documento, factura_asociada_nc,
                   rut_emisor, monto_nc, estado
            FROM dte
            WHERE factura_asociada_nc IS NOT NULL
              AND factura_asociada_nc > 0
        ''')

        # Cargar DTEs MySQL por ID para resolver factura_asociada_nc → n_documento del padre
        mysql_dtes_by_id = {}
        cursor_ids = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor_ids.execute('SELECT ID, n_documento, tipo_documento, rut_emisor FROM dte')
        for row in cursor_ids:
            mysql_dtes_by_id[row['ID']] = row
        cursor_ids.close()

        # Cargar DTEs de PostgreSQL para buscar el padre
        cache_pg_dtes = {}
        for dte in Dte.objects.select_related('emisor').all():
            key = (dte.numero_documento, dte.tipo_documento, dte.emisor_id)
            cache_pg_dtes[key] = dte.id
            # También indexar solo por número
            if dte.numero_documento not in cache_pg_dtes:
                cache_pg_dtes[dte.numero_documento] = dte.id

        actualizaciones = []
        sin_padre = 0

        for row in cursor:
            nc_mysql_ndoc = row['n_documento']
            padre_mysql_id = row['factura_asociada_nc']

            # Buscar el DTE padre en MySQL
            padre_mysql = mysql_dtes_by_id.get(padre_mysql_id)
            if not padre_mysql:
                sin_padre += 1
                continue

            padre_ndoc = padre_mysql['n_documento']

            # Buscar NC en PostgreSQL
            tipo_nc = self._mapear_tipo_documento(row['tipo_documento'] or '')
            rut_emisor = row['rut_emisor']
            emisor = self.cache_empresas_rut.get(rut_emisor)
            emisor_id = emisor.id if emisor else None

            nc_pg_id = None
            if emisor_id:
                nc_pg_id = cache_pg_dtes.get((nc_mysql_ndoc, tipo_nc, emisor_id))
            if not nc_pg_id:
                nc_pg_id = cache_pg_dtes.get(nc_mysql_ndoc)

            # Buscar DTE padre en PostgreSQL
            tipo_padre = self._mapear_tipo_documento(padre_mysql['tipo_documento'] or '')
            padre_emisor = self.cache_empresas_rut.get(padre_mysql['rut_emisor'])
            padre_emisor_id = padre_emisor.id if padre_emisor else None

            padre_pg_id = None
            if padre_emisor_id:
                padre_pg_id = cache_pg_dtes.get((padre_ndoc, tipo_padre, padre_emisor_id))
            if not padre_pg_id:
                padre_pg_id = cache_pg_dtes.get(padre_ndoc)

            if nc_pg_id and padre_pg_id:
                actualizaciones.append((nc_pg_id, padre_pg_id))

        cursor.close()

        if not actualizaciones:
            self.stdout.write(f'  [OK] No hay NC por vincular ({sin_padre} sin padre en MySQL)')
            return

        if self.dry_run:
            self.stdout.write(f'  [DRY-RUN] Se vincularían {len(actualizaciones):,} NC')
            return

        # Actualizar en batch
        updated = 0
        with connection.cursor() as pg_cursor:
            for i in range(0, len(actualizaciones), self.batch_size):
                batch = actualizaciones[i:i + self.batch_size]
                cases = [f"WHEN {nc_id} THEN {padre_id}" for nc_id, padre_id in batch]
                ids = [str(nc_id) for nc_id, _ in batch]

                sql = f'''
                    UPDATE app_dte
                    SET documento_afectado_id = CASE id {' '.join(cases)} END,
                        es_nota_credito = TRUE
                    WHERE id IN ({','.join(ids)})
                '''
                pg_cursor.execute(sql)
                updated += pg_cursor.rowcount

        self.stats['nc_vinculadas'] = updated
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {updated:,} NC vinculadas con DTEs originales ({sin_padre} sin padre)'
        ))

    # ========================================================================
    # ENRIQUECIMIENTO DATOS PREDICCIÓN
    # ========================================================================

    def enriquecer_datos_prediccion(self):
        """
        Post-migración: enriquece campos de predicción en Producto y genera
        registros StockInicialTemporada a partir de movimientos existentes.
        Subpasos:
          A) temporada + anio_temporada  (desde primer movimiento INGRESO_INICIAL)
          B) rango_precio                (desde precioventa del Producto)
          C) StockInicialTemporada       (agrupando INGRESO_INICIAL por producto)
        """
        self.stdout.write(f'\n{"="*70}')
        self.stdout.write('🔮 Enriqueciendo datos para motor de predicción...')

        updated_temporada = self._enriquecer_temporada()
        updated_rango = self._enriquecer_rango_precio()
        created_stock = self._enriquecer_stock_inicial_temporada()

        self.stats['pred_temporadas'] = updated_temporada
        self.stats['pred_rango_precio'] = updated_rango
        self.stats['pred_stock_inicial'] = created_stock

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Predicción: {updated_temporada} temporadas, '
            f'{updated_rango} rangos precio, {created_stock} stocks iniciales'
        ))

    # --- A) Temporada y año desde primer movimiento INGRESO_INICIAL ----------

    def _enriquecer_temporada(self):
        """
        Para cada Producto sin temporada, busca su primer movimiento
        INGRESO_INICIAL y asigna temporada según el mes:
          Oct-Mar → verano   |   Abr-Sep → invierno
        El año-temporada es el año del movimiento (si Oct-Dic, año+1 porque
        la temporada verano pertenece al siguiente ciclo comercial).
        """
        from django.db import connection
        from django.db.models import Min

        productos_sin_temporada = Producto.objects.filter(
            temporada__isnull=True
        ).values_list('id', flat=True)

        if not productos_sin_temporada:
            self.stdout.write('  [OK] Todos los productos ya tienen temporada')
            return 0

        primer_ingreso = (
            Movimientos_Producto.objects
            .filter(
                concepto='INGRESO_INICIAL',
                fecha__isnull=False,
                ProductoTalla__producto_id__in=productos_sin_temporada,
            )
            .values('ProductoTalla__producto_id')
            .annotate(primera_fecha=Min('fecha'))
        )

        fecha_por_producto = {
            row['ProductoTalla__producto_id']: row['primera_fecha']
            for row in primer_ingreso
        }

        if not fecha_por_producto:
            self.stdout.write('  [OK] No hay movimientos INGRESO_INICIAL para inferir temporada')
            return 0

        if self.dry_run:
            self.stdout.write(f'  [DRY-RUN] Se asignaría temporada a {len(fecha_por_producto)} productos')
            return len(fecha_por_producto)

        updated = 0
        batch = []
        for prod_id, fecha in fecha_por_producto.items():
            mes = fecha.month
            if mes >= 10 or mes <= 3:
                temporada = 'verano'
                anio = fecha.year + 1 if mes >= 10 else fecha.year
            else:
                temporada = 'invierno'
                anio = fecha.year

            batch.append((prod_id, temporada, anio))

        for i in range(0, len(batch), self.batch_size):
            chunk = batch[i:i + self.batch_size]
            with connection.cursor() as cur:
                for prod_id, temporada, anio in chunk:
                    cur.execute(
                        '''UPDATE app_producto
                           SET temporada = %s, anio_temporada = %s
                           WHERE id = %s AND temporada IS NULL''',
                        [temporada, anio, prod_id]
                    )
                    updated += cur.rowcount

        self.stdout.write(f'  → {updated} productos: temporada asignada')
        return updated

    # --- B) Rango de precio desde precioventa --------------------------------

    def _enriquecer_rango_precio(self):
        """
        Asigna rango_precio a productos que aún no lo tienen, usando umbrales:
          < 20000  → economico
          < 50000  → medio
          < 100000 → alto
          >= 100000 → premium
        """
        from django.db import connection

        productos = Producto.objects.filter(rango_precio__isnull=True).only('id', 'precioventa')

        if not productos.exists():
            self.stdout.write('  [OK] Todos los productos ya tienen rango_precio')
            return 0

        if self.dry_run:
            count = productos.count()
            self.stdout.write(f'  [DRY-RUN] Se asignaría rango_precio a {count} productos')
            return count

        updated = 0
        with connection.cursor() as cur:
            cur.execute('''
                UPDATE app_producto
                SET rango_precio = CASE
                    WHEN precioventa < 20000  THEN 'economico'
                    WHEN precioventa < 50000  THEN 'medio'
                    WHEN precioventa < 100000 THEN 'alto'
                    ELSE 'premium'
                END
                WHERE rango_precio IS NULL
            ''')
            updated = cur.rowcount

        self.stdout.write(f'  → {updated} productos: rango_precio asignado')
        return updated

    # --- C) StockInicialTemporada desde movimientos INGRESO_INICIAL ----------

    def _enriquecer_stock_inicial_temporada(self):
        """
        Agrupa movimientos INGRESO_INICIAL por producto, infiriendo temporada
        desde la fecha del movimiento, y crea registros StockInicialTemporada
        con: stock_inicial = cantidad del primer ingreso de esa temporada/año,
             stock_total_ingresado = suma de todos los ingresos de esa temporada/año,
             fecha_primer_ingreso = min(fecha) del grupo.
        """
        from django.db.models import Min, Sum, F

        ingresos = (
            Movimientos_Producto.objects
            .filter(concepto='INGRESO_INICIAL', fecha__isnull=False)
            .values(
                producto_id=F('ProductoTalla__producto_id'),
            )
            .annotate(
                primera_fecha=Min('fecha'),
                total_ingresado=Sum('cantidad'),
            )
        )

        if not ingresos:
            self.stdout.write('  [OK] No hay movimientos INGRESO_INICIAL para stock inicial')
            return 0

        agrupados = {}
        for row in ingresos:
            prod_id = row['producto_id']
            fecha = row['primera_fecha']
            total = row['total_ingresado'] or 0

            mes = fecha.month
            if mes >= 10 or mes <= 3:
                temporada = 'verano'
                anio = fecha.year + 1 if mes >= 10 else fecha.year
            else:
                temporada = 'invierno'
                anio = fecha.year

            key = (prod_id, temporada, anio)
            if key not in agrupados:
                agrupados[key] = {
                    'fecha_primer_ingreso': fecha,
                    'stock_total': 0,
                }
            entry = agrupados[key]
            if fecha < entry['fecha_primer_ingreso']:
                entry['fecha_primer_ingreso'] = fecha
            entry['stock_total'] += total

        if self.dry_run:
            self.stdout.write(f'  [DRY-RUN] Se crearían {len(agrupados)} registros StockInicialTemporada')
            return len(agrupados)

        created = 0
        for (prod_id, temporada, anio), data in agrupados.items():
            _, was_created = StockInicialTemporada.objects.update_or_create(
                producto_id=prod_id,
                temporada=temporada,
                anio=anio,
                defaults={
                    'fecha_primer_ingreso': data['fecha_primer_ingreso'],
                    'stock_inicial': data['stock_total'],
                    'stock_total_ingresado': data['stock_total'],
                },
            )
            if was_created:
                created += 1

        self.stdout.write(f'  → {created} registros StockInicialTemporada creados')
        return created

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
            ('Vendedores', self.stats.get('vendedores', 0)),
            ('Atributos', self.stats.get('atributos', 0)),
            ('Categorías', self.stats.get('categorias', 0)),
            ('Productos (agrupados)', self.stats.get('productos', 0)),
            ('Productos_Talla (SKUs)', self.stats.get('producto_talla', 0)),
            ('Movimientos', self.stats.get('movimientos', 0)),
            ('DTEs', self.stats.get('dtes', 0)),
            ('DTE Productos', self.stats.get('dte_productos', 0)),
            ('DTEs faltantes creados', self.stats.get('dtes_faltantes', 0)),
            ('DTEs duplicados eliminados', self.stats.get('dtes_duplicados_eliminados', 0)),
            ('Descuentos DTEs corregidos', self.stats.get('descuentos_corregidos', 0)),
            ('Fechas DTEs corregidas', self.stats.get('fechas_corregidas', 0)),
            ('Sucursales DTEs corregidas', self.stats.get('sucursales_corregidas', 0)),
            ('Tipo transacción corregidos', self.stats.get('tipo_transaccion_corregidos', 0)),
            ('Pagos DTEs (cuadratura)', self.stats.get('ventas_pagos', 0)),
            ('Vendedores asignados DTEs', self.stats.get('vendedores_asignados', 0)),
            ('Parámetros maestros', self.stats.get('parametros_maestros', 0)),
            ('Correlativos (folios)', self.stats.get('correlativos', 0)),
            ('NC vinculadas', self.stats.get('nc_vinculadas', 0)),
            ('Predicción: temporadas', self.stats.get('pred_temporadas', 0)),
            ('Predicción: rango_precio', self.stats.get('pred_rango_precio', 0)),
            ('Predicción: stock_inicial', self.stats.get('pred_stock_inicial', 0)),
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

    def inicializar_permisos_menu(self):
        """Inicializa módulos, opciones de menú y permisos por rol"""
        from django.core.management import call_command
        
        self.stdout.write(f'\n{"="*70}')
        self.stdout.write('🔐 Inicializando permisos del menú...')
        
        try:
            call_command('inicializar_permisos')
            self.stdout.write(self.style.SUCCESS('  ✓ Permisos del menú inicializados correctamente'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  ⚠️ Error al inicializar permisos: {e}'))
            self.log_error('inicializar_permisos', 0, str(e))