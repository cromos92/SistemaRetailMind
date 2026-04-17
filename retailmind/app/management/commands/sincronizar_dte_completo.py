"""
Comando para sincronizar DTEs, Métodos de Pago y Productos desde MySQL

Incluye manejo de sucursales sin valor y múltiples estrategias de asignación.

Uso:
    python manage.py sincronizar_dte_completo --dry-run
    python manage.py sincronizar_dte_completo --tables dtes
    python manage.py sincronizar_dte_completo --tables ventas_pagos
    python manage.py sincronizar_dte_completo --tables dte_productos
    python manage.py sincronizar_dte_completo --tables corregir_sucursales
    python manage.py sincronizar_dte_completo --tables asignar_vendedores
    python manage.py sincronizar_dte_completo --tables importar_faltantes
    python manage.py sincronizar_dte_completo  # Todas las tablas
"""

import os
from decimal import Decimal
from datetime import datetime
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.utils import timezone

from app.models import (
    Empresa, Sucursal, Dte, Dte_Productos, Dte_Detalle_Pago,
    Producto_Talla, Vendedor
)


# ============================================================================
# CONFIGURACIÓN MYSQL
# ============================================================================
MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


# ============================================================================
# MAPEOS
# ============================================================================

# RUTs de empresas propias (solo importar DTEs de estas empresas)
EMPRESAS_PROPIAS_RUTS = [
    '78503140-7',   # Vicent Paola
    '76104936-4',   # Importadora Nicolas
    '7397811-4',    # Gilda Tebes
    '76337843-8',   # Edelmira Tebes y Cia. Ltda.
]

# Mapeo de alias de empresa (bodega -> RUT empresa)
EMPRESA_RUT_MAP = {
    'PAO0': '78503140-7',
    'PAO1': '78503140-7',
    'PAO2': '78503140-7',
    'PAO3': '78503140-7',
    'PAO4': '78503140-7',
    'PA00': '78503140-7',  # Variante con typo
    'EDEL': '78503140-7',
    'EDEL FALLADOS': '78503140-7',
    'GILD': '7397811-4',
    'NICK1': '76104936-4',
    'NICK2': '76104936-4',
    'NICK3': '76104936-4',
    'IMP': '76104936-4',
}

# Sucursal por defecto para cada empresa (cuando bodega = "0" o vacío)
SUCURSAL_DEFECTO_POR_RUT = {
    '78503140-7': 'PAO1',   # Vicent Paola -> PAO1 por defecto
    '76104936-4': 'NICK1',  # Importadora -> NICK1 por defecto
    '7397811-4': 'GILD',    # Gilda -> GILD
    '76337843-8': 'EDEL',   # Edelmira Tebes y Cia. Ltda. -> EDEL (Maipu 676)
}

TIPO_DOCUMENTO_MAP = {
    'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
    'FACTURA EXENTA': 'FACTURA EXENTA',
    'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
    'BOLETA': 'BOLETA PAPEL',
    'DESPACHO ELECTRONICO': 'GUIA',
    'GUIA': 'GUIA',
    'GUIA DESPACHO': 'GUIA',
    'NOTA DE CREDITO': 'NOTA DE CREDITO',
    'NOTA DE DEBITO': 'NOTA DE DEBITO',
}

ESTADO_DTE_MAP = {
    'VIGENTE': 'EMITIDO',
    'ANULADA': 'ANULADO',
    'ANULADO': 'ANULADO',
}

METODO_PAGO_MAP = {
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
}

TARJETA_METODO_MAP = {
    'REDCOMPRA DEBITO': 'TBK_DEBITO_POS',
    'VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
    ' VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
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
}


class Command(BaseCommand):
    help = 'Sincroniza DTEs, Métodos de Pago y Productos desde MySQL'

    def __init__(self):
        super().__init__()
        self.mysql_conn = None
        self.dry_run = False
        self.batch_size = 2000
        self.stats = defaultdict(int)
        
        # Cachés
        self.cache_sucursales = {}          # alias -> Sucursal
        self.cache_sucursales_dir = {}      # direccion -> Sucursal
        self.cache_empresas_rut = {}        # rut -> Empresa
        self.cache_producto_talla = {}      # sku:alias -> Producto_Talla
        self.cache_producto_talla_sku = {}  # sku -> Producto_Talla
        self.cache_vendedores = {}          # codigo -> Vendedor
        self.cache_dtes = {}                # (numero, tipo) -> Dte

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular sin guardar datos'
        )
        parser.add_argument(
            '--tables',
            nargs='+',
            choices=['dtes', 'ventas_pagos', 'dte_productos', 'corregir_sucursales', 'asignar_vendedores', 'importar_faltantes'],
            help='Tablas específicas a sincronizar'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=2000,
            help='Tamaño de lote'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limitar registros (para pruebas)'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.batch_size = options.get('batch_size', 2000)
        self.limit = options.get('limit')
        tables = options.get('tables') or ['dtes', 'ventas_pagos', 'dte_productos', 'corregir_sucursales', 'asignar_vendedores', 'importar_faltantes']
        
        self.stdout.write('='*70)
        self.stdout.write('SINCRONIZACION DTEs - METODOS PAGO - PRODUCTOS')
        self.stdout.write('='*70)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] Modo simulacion'))
        
        # Conectar MySQL
        try:
            self.mysql_conn = self.connect_mysql()
            self.stdout.write(self.style.SUCCESS('[OK] Conexion MySQL establecida'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'[ERROR] MySQL: {e}'))
            return
        
        # Cargar cachés
        self.stdout.write('\n[*] Cargando caches...')
        self.preload_caches()
        
        # Ejecutar sincronizaciones
        try:
            if not self.dry_run:
                with transaction.atomic():
                    self._ejecutar_tablas(tables)
            else:
                self._ejecutar_tablas(tables)
            
            self.show_statistics()
            
        finally:
            if self.mysql_conn:
                self.mysql_conn.close()

    def _ejecutar_tablas(self, tables):
        if 'dtes' in tables:
            self.sincronizar_dtes()
        
        if 'dte_productos' in tables:
            self.sincronizar_dte_productos()
        
        if 'ventas_pagos' in tables:
            self.sincronizar_ventas_pagos()
        
        if 'corregir_sucursales' in tables:
            self.corregir_sucursales_sin_valor()

        if 'asignar_vendedores' in tables:
            self.asignar_vendedores()

        if 'importar_faltantes' in tables:
            self.importar_dtes_faltantes()

    # =========================================================================
    # CONEXIÓN Y CACHÉS
    # =========================================================================

    def connect_mysql(self):
        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=600,
            autocommit=True,
        )

    def preload_caches(self):
        # Empresas
        for empresa in Empresa.objects.all():
            self.cache_empresas_rut[empresa.rut] = empresa
        self.stdout.write(f'  - {len(self.cache_empresas_rut)} empresas')
        
        # Sucursales (por alias y dirección)
        for sucursal in Sucursal.objects.select_related('empresa').all():
            self.cache_sucursales[sucursal.alias] = sucursal
            if sucursal.direccion:
                self.cache_sucursales_dir[sucursal.direccion] = sucursal
        self.stdout.write(f'  - {len(self.cache_sucursales)} sucursales')
        
        # Vendedores
        for vendedor in Vendedor.objects.all():
            if vendedor.codigo_vendedor:
                self.cache_vendedores[vendedor.codigo_vendedor] = vendedor
        self.stdout.write(f'  - {len(self.cache_vendedores)} vendedores')
        
        # Productos_Talla
        for pt in Producto_Talla.objects.select_related('producto__sucursal').all():
            alias = pt.producto.sucursal.alias if pt.producto.sucursal else 'SIN_SUCURSAL'
            self.cache_producto_talla[f"{pt.sku}:{alias}"] = pt
            self.cache_producto_talla_sku[str(pt.sku)] = pt
        self.stdout.write(f'  - {len(self.cache_producto_talla_sku)} productos_talla')
        
        # DTEs existentes
        for dte in Dte.objects.all():
            self.cache_dtes[(dte.numero_documento, dte.tipo_documento)] = dte
        self.stdout.write(f'  - {len(self.cache_dtes)} DTEs existentes')

    # =========================================================================
    # FUNCIONES DE BÚSQUEDA INTELIGENTE
    # =========================================================================

    def buscar_sucursal(self, alias=None, direccion=None, rut_emisor=None):
        """
        Busca sucursal con múltiples estrategias:
        1. Por alias directo
        2. Por dirección
        3. Por RUT de empresa (sucursal por defecto)
        """
        # Estrategia 1: Por alias
        if alias and alias not in ('0', '', None):
            sucursal = self.cache_sucursales.get(alias)
            if sucursal:
                return sucursal
        
        # Estrategia 2: Por dirección
        if direccion:
            sucursal = self.cache_sucursales_dir.get(direccion)
            if sucursal:
                return sucursal
        
        # Estrategia 3: Por RUT emisor -> sucursal por defecto
        if rut_emisor:
            alias_defecto = SUCURSAL_DEFECTO_POR_RUT.get(rut_emisor)
            if alias_defecto:
                return self.cache_sucursales.get(alias_defecto)
        
        # Estrategia 4: Por alias en el mapa de empresas
        if alias:
            rut_empresa = EMPRESA_RUT_MAP.get(alias)
            if rut_empresa:
                alias_defecto = SUCURSAL_DEFECTO_POR_RUT.get(rut_empresa)
                if alias_defecto:
                    return self.cache_sucursales.get(alias_defecto)
        
        return None

    def mapear_tipo_documento(self, tipo_mysql):
        """Mapea tipo de documento MySQL -> Django"""
        tipo_upper = (tipo_mysql or '').upper().strip()
        
        # Mapeo directo
        if tipo_upper in TIPO_DOCUMENTO_MAP:
            return TIPO_DOCUMENTO_MAP[tipo_upper]
        
        # Búsqueda parcial
        if 'FACTURA' in tipo_upper and 'EXENTA' in tipo_upper:
            return 'FACTURA EXENTA'
        elif 'FACTURA' in tipo_upper:
            return 'FACTURA ELECTRONICA'
        elif 'BOLETA' in tipo_upper and 'ELECTRONICA' in tipo_upper:
            return 'BOLETA ELECTRONICA'
        elif 'BOLETA' in tipo_upper:
            return 'BOLETA PAPEL'
        elif 'DESPACHO' in tipo_upper or 'GUIA' in tipo_upper:
            return 'GUIA'
        elif 'CREDITO' in tipo_upper:
            return 'NOTA DE CREDITO'
        elif 'DEBITO' in tipo_upper:
            return 'NOTA DE DEBITO'
        
        return 'BOLETA ELECTRONICA'

    def mapear_estado_pago(self, forma_pago):
        """Mapea forma de pago MySQL -> estado_pago Django"""
        forma = (forma_pago or '').upper()
        
        if 'CONTADO' in forma or 'EFECTIVO' in forma or 'PAGADO' in forma:
            return 'PAGADO'
        elif 'CREDITO' in forma:
            return 'PENDIENTE'
        elif 'PARCIAL' in forma:
            return 'PARCIAL'
        
        return 'PENDIENTE'

    def mapear_metodo_pago(self, metodo_mysql, tarjeta=None):
        """Mapea método de pago MySQL -> Django"""
        metodo = METODO_PAGO_MAP.get(metodo_mysql, 'EFECTIVO')
        
        # Refinar por tipo de tarjeta
        if tarjeta:
            tarjeta_clean = tarjeta.strip()
            if tarjeta_clean in TARJETA_METODO_MAP:
                metodo = TARJETA_METODO_MAP[tarjeta_clean]
            elif tarjeta_clean.startswith('OrdenCompra'):
                metodo = 'ORDEN_COMPRA'
        
        return metodo

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
            return timezone.localdate()
        if isinstance(value, datetime):
            return value.date()
        return value

    # =========================================================================
    # SINCRONIZAR DTEs
    # =========================================================================

    def sincronizar_dtes(self):
        """Sincroniza DTEs desde MySQL (solo empresas propias)"""
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[1] SINCRONIZANDO DTEs')
        self.stdout.write('='*70)
        
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        # Filtro de RUTs de empresas propias
        ruts_str = ', '.join([f"'{r}'" for r in EMPRESAS_PROPIAS_RUTS])
        filtro_rut = f"rut_emisor IN ({ruts_str})"
        
        self.stdout.write(f'Filtrando por RUTs emisor: {", ".join(EMPRESAS_PROPIAS_RUTS)}')
        
        # Contar total vs empresas propias
        cursor.execute('SELECT COUNT(*) as total FROM dte')
        total_mysql = cursor.fetchone()['total']
        
        cursor.execute(f'SELECT COUNT(*) as total FROM dte WHERE {filtro_rut}')
        total = cursor.fetchone()['total']
        
        excluidos = total_mysql - total
        self.stdout.write(f'Total en MySQL: {total_mysql:,}')
        self.stdout.write(f'  - Empresas propias (a importar): {total:,}')
        self.stdout.write(f'  - Otros emisores (excluidos): {excluidos:,}')
        
        if self.limit:
            total = min(total, self.limit)
        
        self.stdout.write(f'DTEs existentes en Django: {len(self.cache_dtes):,}')
        self.stdout.write(f'DTEs existentes en Django: {len(self.cache_dtes):,}')
        
        # Obtener datos (solo empresas propias)
        query = f'''
            SELECT 
                ID, rut_emisor, rut_cliente, tipo_documento, n_documento,
                forma_pago, monto_total, iva, neto, fecha_emision, fecha_vence,
                vendedor, responsable, estado, bodega_inicio, bodega_destino,
                descuento, referencia
            FROM dte
            WHERE {filtro_rut}
            ORDER BY fecha_emision, n_documento
        '''
        if self.limit:
            query += f' LIMIT {self.limit}'
        
        cursor.execute(query)
        
        nuevos = 0
        existentes = 0
        sin_sucursal = 0
        batch = []
        
        for idx, row in enumerate(cursor, 1):
            tipo_documento = self.mapear_tipo_documento(row['tipo_documento'])
            numero = self.safe_int(row['n_documento'])
            
            # Verificar si ya existe
            if (numero, tipo_documento) in self.cache_dtes:
                existentes += 1
                continue
            
            # Buscar sucursal
            sucursal = self.buscar_sucursal(
                alias=row['bodega_inicio'],
                rut_emisor=row['rut_emisor']
            )
            
            if not sucursal:
                sin_sucursal += 1
            
            # Buscar emisor y receptor
            emisor = self.cache_empresas_rut.get(row['rut_emisor'])
            receptor = self.cache_empresas_rut.get(row['rut_cliente'])
            
            if not emisor and sucursal:
                emisor = sucursal.empresa
            
            if not emisor:
                continue
            
            # Estado
            estado_dte = ESTADO_DTE_MAP.get(
                (row['estado'] or '').upper(), 
                'EMITIDO'
            )
            estado_pago = self.mapear_estado_pago(row['forma_pago'])
            
            # Tipo transacción
            if row['bodega_inicio'] and row['bodega_destino'] and row['bodega_destino'] != '0':
                tipo_transaccion = 'TRASPASO'
            elif 'BOLETA' in tipo_documento:
                tipo_transaccion = 'VENTA_PUBLICO'
            elif 'FACTURA' in tipo_documento:
                tipo_transaccion = 'VENTA'
            else:
                tipo_transaccion = 'COMPRA'
            
            # Fechas
            fecha_emision = self.safe_date(row['fecha_emision'])
            fecha_vence = self.safe_date(row['fecha_vence'])
            
            # Días crédito
            if fecha_emision and fecha_vence:
                dias_credito = max(0, (fecha_vence - fecha_emision).days)
            else:
                dias_credito = 0
            
            if not self.dry_run:
                dte = Dte(
                    numero_documento=numero,
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
                )
                batch.append(dte)
                
                if len(batch) >= self.batch_size:
                    Dte.objects.bulk_create(batch, ignore_conflicts=True)
                    nuevos += len(batch)
                    batch = []
                    self.stdout.write(f'  Procesados: {idx:,} | Nuevos: {nuevos:,}')
            else:
                nuevos += 1
            
            if idx % 10000 == 0:
                self.stdout.write(f'  Analizando: {idx:,}/{total:,}')
        
        # Último batch
        if batch and not self.dry_run:
            Dte.objects.bulk_create(batch, ignore_conflicts=True)
            nuevos += len(batch)
        
        cursor.close()
        
        self.stats['dtes_nuevos'] = nuevos
        self.stats['dtes_existentes'] = existentes
        self.stats['dtes_sin_sucursal'] = sin_sucursal
        
        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] DTEs nuevos: {nuevos:,} | Existentes: {existentes:,} | Sin sucursal: {sin_sucursal:,}'
        ))
        
        # Actualizar caché de DTEs
        if not self.dry_run:
            self.cache_dtes.clear()
            for dte in Dte.objects.all():
                self.cache_dtes[(dte.numero_documento, dte.tipo_documento)] = dte
            self.stdout.write(f'  Cache actualizado: {len(self.cache_dtes):,} DTEs')

    # =========================================================================
    # SINCRONIZAR DTE_PRODUCTOS
    # =========================================================================

    def sincronizar_dte_productos(self):
        """Sincroniza productos de DTEs desde MySQL"""
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[2] SINCRONIZANDO DTE_PRODUCTOS')
        self.stdout.write('='*70)
        
        # Pre-cargar existentes
        existentes = set(
            Dte_Productos.objects.values_list('dte_id', 'productoTalla_id', 'stock')
        )
        self.stdout.write(f'Productos DTE existentes: {len(existentes):,}')
        
        # =====================================================================
        # CREAR MAPEO: ID MySQL -> DTE Django
        # Usamos la tabla dte de MySQL para obtener el ID original
        # =====================================================================
        self.stdout.write('  Creando mapeo IdDte (MySQL) -> DTE (Django)...')
        
        cursor_map = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor_map.execute('SELECT ID, n_documento, tipo_documento, monto_total, fecha_emision FROM dte')
        
        # Mapeo por ID MySQL
        mysql_id_to_dte = {}  # {mysql_id: (n_documento, tipo_documento)}
        for row in cursor_map:
            mysql_id_to_dte[row['ID']] = {
                'n_documento': row['n_documento'],
                'tipo_documento': row['tipo_documento'],
                'monto': int(row['monto_total'] or 0),
                'fecha': row['fecha_emision'],
            }
        cursor_map.close()
        self.stdout.write(f'  Mapeo creado: {len(mysql_id_to_dte):,} DTEs de MySQL')
        
        # Cache adicional: por (n_documento, sucursal, fecha)
        cache_dte_completo = {}  # (n_documento, sucursal_alias, fecha) -> Dte
        for dte in Dte.objects.select_related('sucursal').all():
            alias = dte.sucursal.alias if dte.sucursal else None
            fecha = dte.fecha_emision
            cache_dte_completo[(dte.numero_documento, alias, fecha)] = dte
        self.stdout.write(f'  Cache completo: {len(cache_dte_completo):,} DTEs')
        
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM productos_dte')
        total = cursor.fetchone()['total']
        if self.limit:
            total = min(total, self.limit)
        
        self.stdout.write(f'Total en MySQL: {total:,}')
        
        query = '''
            SELECT 
                ID, factura_asociada, codigo_asociado, articulo, descripcion,
                talla, cantidad, precio_interno, precio_publico, costo,
                tipo_documento, bodega_inicio, marca, color, IdDte, estado,
                fecha_creacion
            FROM productos_dte
            ORDER BY ID
        '''
        if self.limit:
            query += f' LIMIT {self.limit}'
        
        cursor.execute(query)
        
        nuevos = 0
        dte_no_encontrado = 0
        producto_no_encontrado = 0
        duplicados = 0
        batch = []
        
        # Contadores por método de búsqueda
        encontrado_por = {'iddte': 0, 'sucursal_fecha': 0}
        
        for idx, row in enumerate(cursor, 1):
            dte = None
            metodo = None
            
            # ================================================================
            # ESTRATEGIA 1: Buscar por IdDte (ID de MySQL -> mapeo)
            # ================================================================
            if row['IdDte'] and row['IdDte'] in mysql_id_to_dte:
                info = mysql_id_to_dte[row['IdDte']]
                tipo_doc = self.mapear_tipo_documento(info['tipo_documento'] or '')
                dte = self.cache_dtes.get((info['n_documento'], tipo_doc))
                if dte:
                    metodo = 'iddte'
            
            # ================================================================
            # ESTRATEGIA 2: Buscar por n_documento + sucursal + fecha
            # (para casos donde el número se repite en diferentes sucursales)
            # ================================================================
            if not dte and row['factura_asociada']:
                alias = row['bodega_inicio']  # Alias de sucursal
                fecha = row['fecha_creacion']  # fecha_creacion = fecha_emision
                
                # Intentar con fecha exacta
                key = (row['factura_asociada'], alias, fecha)
                dte = cache_dte_completo.get(key)
                
                # Si no, intentar solo con sucursal (sin fecha)
                if not dte:
                    for (n_doc, suc_alias, f), d in cache_dte_completo.items():
                        if n_doc == row['factura_asociada'] and suc_alias == alias:
                            dte = d
                            break
                
                if dte:
                    metodo = 'sucursal_fecha'
            
            if not dte:
                dte_no_encontrado += 1
                continue
            
            encontrado_por[metodo] += 1
            
            # Buscar Producto_Talla
            sku = str(row['codigo_asociado']) if row['codigo_asociado'] else None
            producto_talla = None
            
            if sku:
                alias = row['bodega_inicio']
                if alias:
                    producto_talla = self.cache_producto_talla.get(f"{sku}:{alias}")
                if not producto_talla:
                    producto_talla = self.cache_producto_talla_sku.get(sku)
            
            if not producto_talla:
                producto_no_encontrado += 1
                continue
            
            stock = self.safe_int(row['cantidad'])
            
            # Verificar duplicado
            dup_key = (dte.id, producto_talla.id, stock)
            if dup_key in existentes:
                duplicados += 1
                continue
            
            # Calcular precios (sobreprecio = precio - costo, misma formula que la UI)
            costo = self.safe_int(row['costo'])
            precio_publico = self.safe_int(row['precio_publico'])
            precio_interno = self.safe_int(row['precio_interno'])
            precio = precio_publico or precio_interno
            sobreprecio = max(0, precio - costo)
            
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
                existentes.add(dup_key)
                
                if len(batch) >= self.batch_size:
                    Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
                    nuevos += len(batch)
                    batch = []
                    self.stdout.write(f'  Procesados: {idx:,} | Nuevos: {nuevos:,}')
            else:
                nuevos += 1
            
            if idx % 20000 == 0:
                self.stdout.write(f'  Analizando: {idx:,}/{total:,}')
        
        if batch and not self.dry_run:
            Dte_Productos.objects.bulk_create(batch, ignore_conflicts=True)
            nuevos += len(batch)
        
        cursor.close()

        self.stats['dte_productos_nuevos'] = nuevos
        self.stats['dte_productos_dte_no_encontrado'] = dte_no_encontrado
        self.stats['dte_productos_prod_no_encontrado'] = producto_no_encontrado
        self.stats['dte_productos_duplicados'] = duplicados

        self.stdout.write(f'\n  DTEs encontrados por metodo:')
        self.stdout.write(f'    - Por IdDte (MySQL ID):             {encontrado_por["iddte"]:,}')
        self.stdout.write(f'    - Por n_doc + sucursal + fecha:     {encontrado_por["sucursal_fecha"]:,}')
        
        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] Nuevos: {nuevos:,} | DTE no encontrado: {dte_no_encontrado:,} | '
            f'Prod no encontrado: {producto_no_encontrado:,} | Duplicados: {duplicados:,}'
        ))

    # =========================================================================
    # SINCRONIZAR VENTAS_PAGOS
    # =========================================================================

    def sincronizar_ventas_pagos(self):
        """
        Sincroniza métodos de pago desde tabla ventas de MySQL.
        
        LÓGICA:
        1. Cada fila de 'ventas' es UN PAGO (no un documento completo)
        2. Un documento puede tener múltiples pagos (Ej: Efectivo + Tarjeta)
        3. Si el DTE no existe, se CREA automáticamente
        4. Usa clave (n_documento, sucursal, fecha) para mejor matching
        """
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[3] SINCRONIZANDO METODOS DE PAGO (ventas -> Dte_Detalle_Pago)')
        self.stdout.write('='*70)
        
        # Mapa local por documento y sucursal (evita mezclar pagos entre sucursales)
        dtes_por_doc_sucursal = {}
        for dte in Dte.objects.select_related('sucursal').only('id', 'numero_documento', 'tipo_documento', 'sucursal'):
            suc_dir = dte.sucursal.direccion if dte.sucursal else ''
            key = (str(dte.numero_documento), dte.tipo_documento, suc_dir)
            dtes_por_doc_sucursal[key] = dte
        
        # Pre-cargar existentes
        existentes = set(
            Dte_Detalle_Pago.objects.values_list('dte_id', 'voucher', 'monto')
        )
        self.stdout.write(f'Pagos existentes: {len(existentes):,}')
        
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM ventas')
        total = cursor.fetchone()['total']
        if self.limit:
            total = min(total, self.limit)
        
        self.stdout.write(f'Total en MySQL: {total:,}')
        
        query = '''
            SELECT 
                ID, tipo_documento, metodo_pago, n_documento, tarjeta,
                sub_total, descuento, monto_pagado, sucursal, fecha,
                voucher, n_convenio, correlativo_ticket, responsable,
                nombre_vendedor, rut_convenio, descuento_tbk, estado, ID_dte,
                codigo_vendedor
            FROM ventas
            ORDER BY fecha, ID
        '''
        if self.limit:
            query += f' LIMIT {self.limit}'
        
        cursor.execute(query)
        
        nuevos = 0
        dte_no_encontrado = 0
        dte_creados = 0
        duplicados = 0
        batch = []
        
        # Cache adicional para DTEs creados en esta sesión
        dtes_creados_sesion = {}  # (n_documento, sub_total, sucursal) -> Dte
        
        for idx, row in enumerate(cursor, 1):
            n_documento = row['n_documento']
            tipo_doc = self.mapear_tipo_documento(row['tipo_documento'] or '')
            sub_total = self.safe_int(row['sub_total'])
            sucursal_dir = row['sucursal']  # Es dirección, no alias
            fecha = row['fecha']
            
            # Buscar DTE con múltiples estrategias
            dte = None
            
            # Estrategia 1: Por (numero, tipo, sucursal_dir)
            if n_documento:
                key_doc = (str(n_documento), tipo_doc, sucursal_dir)
                dte = dtes_por_doc_sucursal.get(key_doc)
                # Fallback solo si no hay sucursal (evitar mezclar sucursales)
                if not dte and not sucursal_dir:
                    dte = self.cache_dtes.get((n_documento, tipo_doc))
            
            # Estrategia 2: Por ID_dte
            if not dte and row['ID_dte']:
                for (num, tipo), d in self.cache_dtes.items():
                    if num == row['ID_dte']:
                        dte = d
                        break
            
            # Estrategia 3: Buscar en DTEs creados en esta sesión
            if not dte:
                key_sesion = (n_documento, sub_total, sucursal_dir)
                dte = dtes_creados_sesion.get(key_sesion)
            
            # Si no existe DTE, CREAR UNO NUEVO
            if not dte and n_documento and n_documento > 0:
                # Buscar sucursal por dirección
                sucursal = self.cache_sucursales_dir.get(sucursal_dir)
                
                # Determinar emisor (empresa propia)
                emisor = None
                if sucursal:
                    emisor = sucursal.empresa
                else:
                    # Usar primera empresa por defecto
                    emisor = list(self.cache_empresas_rut.values())[0] if self.cache_empresas_rut else None
                
                if emisor and not self.dry_run:
                    # Determinar tipo transacción
                    if 'BOLETA' in tipo_doc:
                        tipo_transaccion = 'VENTA_PUBLICO'
                    elif 'FACTURA' in tipo_doc:
                        tipo_transaccion = 'VENTA'
                    else:
                        tipo_transaccion = 'VENTA_PUBLICO'
                    
                    # Buscar vendedor
                    vendedor = None
                    if row['codigo_vendedor']:
                        vendedor = self.cache_vendedores.get(str(row['codigo_vendedor']))
                    
                    # Crear y GUARDAR DTE inmediatamente (necesita ID para los pagos)
                    dte = Dte(
                        numero_documento=n_documento,
                        tipo_documento=tipo_doc,
                        emisor=emisor,
                        receptor=None,
                        monto_con_iva=sub_total,
                        monto_neto=int(sub_total / 1.19),
                        estado_pago='PAGADO',
                        estado_dte='EMITIDO',
                        responsable=row['responsable'] or 'Sistema',
                        fecha_emision=fecha,
                        fecha_vencimiento=fecha,
                        diasCredito=0,
                        bultos=0,
                        unidades_productos=0,
                        descuento=self.safe_int(row['descuento']),
                        sucursal=sucursal,
                        tipo_transaccion=tipo_transaccion,
                        vendedor=vendedor,
                    )
                    dte.save()  # Guardar inmediatamente para obtener ID
                    
                    # Guardar en caches
                    self.cache_dtes[(n_documento, tipo_doc)] = dte
                    dtes_por_doc_sucursal[(str(n_documento), tipo_doc, sucursal_dir)] = dte
                    key_sesion = (n_documento, sub_total, sucursal_dir)
                    dtes_creados_sesion[key_sesion] = dte
                    
                    dte_creados += 1
                    
                    if dte_creados % 1000 == 0:
                        self.stdout.write(f'  DTEs creados: {dte_creados:,}')
                        
                elif self.dry_run:
                    dte_creados += 1
            
            if not dte:
                dte_no_encontrado += 1
                continue
            
            # Mapear método de pago
            metodo_pago = self.mapear_metodo_pago(
                row['metodo_pago'] or 'Efectivo',
                row['tarjeta']
            )
            
            # Verificar duplicado usando ID de MySQL como voucher único
            voucher = str(row['voucher']) if row['voucher'] and row['voucher'] != '0' else f"MIG-{row['ID']}"
            monto = self.safe_int(row['monto_pagado'])
            
            # Clave única: dte_id + voucher + monto
            dup_key = (dte.id if hasattr(dte, 'id') and dte.id else hash(str(dte)), voucher, monto)
            
            if dup_key in existentes:
                duplicados += 1
                continue
            
            # Notas con descuento
            notas_partes = []
            if row['descuento'] and row['descuento'] > 0:
                notas_partes.append(f"Descuento: ${row['descuento']:,}")
            if row['n_convenio']:
                notas_partes.append(f"Convenio: {row['n_convenio']}")
            if row['rut_convenio']:
                notas_partes.append(f"RUT: {row['rut_convenio']}")
            if row['nombre_vendedor']:
                notas_partes.append(f"Vendedor: {row['nombre_vendedor']}")
            if row['descuento_tbk'] and row['descuento_tbk'] > 0:
                notas_partes.append(f"Desc TBK: ${row['descuento_tbk']:,}")
            if row['correlativo_ticket']:
                notas_partes.append(f"Ticket: {row['correlativo_ticket']}")
            notas = ' | '.join(notas_partes) if notas_partes else None
            
            if not self.dry_run:
                batch.append(Dte_Detalle_Pago(
                    dte=dte,
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=row['tarjeta'] or None,
                    voucher=voucher,
                    monto=monto,
                    notas=notas
                ))
                existentes.add(dup_key)
                
                if len(batch) >= self.batch_size:
                    Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
                    nuevos += len(batch)
                    batch = []
                    self.stdout.write(f'  Procesados: {idx:,} | Pagos: {nuevos:,} | DTEs creados: {dte_creados:,}')
            else:
                nuevos += 1
            
            if idx % 20000 == 0:
                self.stdout.write(f'  Analizando: {idx:,}/{total:,}')
        
        # Guardar batch final de pagos
        if batch and not self.dry_run:
            Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
            nuevos += len(batch)
        
        cursor.close()
        
        self.stats['pagos_nuevos'] = nuevos
        self.stats['pagos_dte_no_encontrado'] = dte_no_encontrado
        self.stats['pagos_dte_creados'] = dte_creados
        self.stats['pagos_duplicados'] = duplicados
        
        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] Pagos nuevos: {nuevos:,} | DTEs creados: {dte_creados:,} | '
            f'DTE no encontrado: {dte_no_encontrado:,} | Duplicados: {duplicados:,}'
        ))

    # =========================================================================
    # CORREGIR SUCURSALES SIN VALOR
    # =========================================================================

    def corregir_sucursales_sin_valor(self):
        """Corrige DTEs que tienen sucursal NULL"""
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[4] CORRIGIENDO DTEs SIN SUCURSAL')
        self.stdout.write('='*70)
        
        # Contar DTEs sin sucursal
        sin_sucursal = Dte.objects.filter(sucursal__isnull=True).count()
        self.stdout.write(f'DTEs sin sucursal: {sin_sucursal:,}')
        
        if sin_sucursal == 0:
            self.stdout.write(self.style.SUCCESS('  [OK] No hay DTEs sin sucursal'))
            return
        
        # Cargar info de MySQL para corregir (incluye rut_cliente para compras)
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT n_documento, monto_total, rut_emisor, rut_cliente, bodega_inicio, bodega_destino
            FROM dte
        ''')
        
        info_mysql = {}
        for row in cursor:
            key = (row['n_documento'], int(row['monto_total'] or 0))
            info_mysql[key] = {
                'bodega_inicio': row['bodega_inicio'],
                'bodega_destino': row['bodega_destino'],
                'rut_emisor': row['rut_emisor'],
                'rut_cliente': row['rut_cliente'],  # Para DTEs de compra (proveedor -> empresa)
            }
        cursor.close()
        
        # También cargar de ventas (tiene "sucursal" como dirección)
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('''
            SELECT n_documento, sub_total, sucursal
            FROM ventas
            GROUP BY n_documento, sub_total, sucursal
        ''')
        
        sucursales_ventas = {}
        for row in cursor:
            key = (row['n_documento'], int(row['sub_total'] or 0))
            sucursales_ventas[key] = row['sucursal']
        cursor.close()
        
        self.stdout.write(f'  Datos MySQL cargados: {len(info_mysql):,} DTEs, {len(sucursales_ventas):,} ventas')
        
        # Procesar correcciones
        corregidos = 0
        no_corregidos = 0
        corregido_por = {'bodega': 0, 'ventas': 0, 'rut_emisor': 0, 'rut_cliente': 0}
        
        for dte in Dte.objects.filter(sucursal__isnull=True).select_related('emisor', 'receptor'):
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            
            sucursal = None
            metodo = None
            
            # Estrategia 1: Desde tabla dte (bodega_inicio)
            if key in info_mysql:
                info = info_mysql[key]
                sucursal = self.buscar_sucursal(
                    alias=info['bodega_inicio'],
                    rut_emisor=info['rut_emisor']
                )
                if sucursal:
                    metodo = 'bodega'
            
            # Estrategia 2: Desde tabla ventas (por dirección)
            if not sucursal and key in sucursales_ventas:
                direccion = sucursales_ventas[key]
                sucursal = self.cache_sucursales_dir.get(direccion)
                if sucursal:
                    metodo = 'ventas'
            
            # Estrategia 3: Por RUT del emisor (sucursal por defecto de la empresa emisora)
            if not sucursal and dte.emisor:
                alias_defecto = SUCURSAL_DEFECTO_POR_RUT.get(dte.emisor.rut)
                if alias_defecto:
                    sucursal = self.cache_sucursales.get(alias_defecto)
                    if sucursal:
                        metodo = 'rut_emisor'
            
            # Estrategia 4: Por RUT del receptor/cliente (para DTEs de COMPRA)
            # Si el emisor es un proveedor, el cliente es nuestra empresa
            if not sucursal and key in info_mysql:
                rut_cliente = info_mysql[key].get('rut_cliente')
                if rut_cliente:
                    alias_defecto = SUCURSAL_DEFECTO_POR_RUT.get(rut_cliente)
                    if alias_defecto:
                        sucursal = self.cache_sucursales.get(alias_defecto)
                        if sucursal:
                            metodo = 'rut_cliente'
            
            if sucursal:
                if not self.dry_run:
                    dte.sucursal = sucursal
                    dte.save(update_fields=['sucursal'])
                corregidos += 1
                corregido_por[metodo] += 1
            else:
                no_corregidos += 1
        
        self.stats['sucursales_corregidas'] = corregidos
        self.stats['sucursales_no_corregidas'] = no_corregidos
        
        self.stdout.write(f'\n  Metodo de correccion:')
        self.stdout.write(f'    - Por bodega_inicio: {corregido_por["bodega"]:,}')
        self.stdout.write(f'    - Por direccion ventas: {corregido_por["ventas"]:,}')
        self.stdout.write(f'    - Por rut_emisor: {corregido_por["rut_emisor"]:,}')
        self.stdout.write(f'    - Por rut_cliente (compras): {corregido_por["rut_cliente"]:,}')
        
        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] Corregidos: {corregidos:,} | No corregidos: {no_corregidos:,}'
        ))

    # =========================================================================
    # ASIGNAR VENDEDORES A DTEs
    # =========================================================================

    def asignar_vendedores(self):
        """Asigna/Reasigna vendedores a TODOS los DTEs de venta, buscando en MySQL (ventas)"""
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[5] ASIGNANDO VENDEDORES A DTEs (TODOS)')
        self.stdout.write('='*70)

        # TODOS los DTEs de venta (no solo los sin vendedor)
        todos_dtes = list(Dte.objects.filter(
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
        ).select_related('sucursal', 'vendedor'))
        total_dtes = len(todos_dtes)

        ya_tienen = len([d for d in todos_dtes if d.vendedor])
        sin_vendedor = len([d for d in todos_dtes if not d.vendedor])

        self.stdout.write(f'Total DTEs de venta en Django: {total_dtes:,}')
        self.stdout.write(f'  - Ya tienen vendedor: {ya_tienen:,}')
        self.stdout.write(f'  - Sin vendedor: {sin_vendedor:,}')

        if total_dtes == 0:
            self.stdout.write(self.style.SUCCESS('  [OK] No hay DTEs de venta'))
            return

        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)

        # =====================================================================
        # Cargar vendedores desde MySQL (ventas) - por (n_documento, sucursal, fecha)
        # Usar STRING para las claves para evitar problemas de tipo
        # =====================================================================
        self.stdout.write('  Cargando vendedores desde MySQL (ventas) por (n_doc + sucursal + fecha)...')
        cursor.execute('''
            SELECT
                n_documento,
                sucursal,
                fecha,
                codigo_vendedor,
                nombre_vendedor
            FROM ventas
            WHERE codigo_vendedor IS NOT NULL
              AND codigo_vendedor != ''
              AND codigo_vendedor != '0'
        ''')

        # Mapeo: (n_documento, sucursal_dir, fecha) -> info vendedor
        ventas_vendedor = {}
        for row in cursor:
            n_doc = str(row['n_documento']).strip()
            suc_dir = row['sucursal'] or ''
            fecha = str(row['fecha']) if row['fecha'] else ''
            codigo = str(row['codigo_vendedor']).strip()
            key = (n_doc, suc_dir, fecha)
            if n_doc and codigo and key not in ventas_vendedor:
                ventas_vendedor[key] = {
                    'codigo': codigo,
                    'nombre': row['nombre_vendedor']
                }

        self.stdout.write(f'    {len(ventas_vendedor):,} (n_doc + sucursal + fecha) unicos con vendedor')

        # Mostrar ejemplos de códigos para debug
        ejemplos_codigos = list(set(v['codigo'] for v in list(ventas_vendedor.values())[:100]))[:10]
        self.stdout.write(f'    Ejemplos de codigos en MySQL: {ejemplos_codigos}')

        # Mostrar ejemplos de códigos en cache
        ejemplos_cache = list(self.cache_vendedores.keys())[:10]
        self.stdout.write(f'    Ejemplos de codigos en cache: {ejemplos_cache}')

        cursor.close()

        # =====================================================================
        # Asignar vendedores a TODOS los DTEs (por n_doc + sucursal)
        # =====================================================================
        asignados = 0
        actualizados = 0
        sin_cambio = 0
        no_encontrados = 0
        codigo_no_match = 0

        for dte in todos_dtes:
            n_doc = str(dte.numero_documento).strip()
            suc_dir = dte.sucursal.direccion if dte.sucursal else ''
            fecha = str(dte.fecha_emision) if dte.fecha_emision else ''
            key = (n_doc, suc_dir, fecha)

            # Buscar en ventas_vendedor por (n_doc, sucursal, fecha)
            if key not in ventas_vendedor:
                no_encontrados += 1
                continue

            info = ventas_vendedor[key]
            codigo = info['codigo']
            
            # Buscar vendedor por codigo_vendedor
            vendedor_nuevo = self.cache_vendedores.get(codigo)
            
            if not vendedor_nuevo:
                codigo_no_match += 1
                continue

            # Verificar si es el mismo vendedor
            if dte.vendedor and dte.vendedor.id == vendedor_nuevo.id:
                sin_cambio += 1
                continue

            # Asignar/Actualizar vendedor
            if not self.dry_run:
                dte.vendedor = vendedor_nuevo
                dte.save(update_fields=['vendedor'])
            
            if dte.vendedor:
                actualizados += 1
            else:
                asignados += 1

        total_modificados = asignados + actualizados
        self.stats['vendedores_asignados'] = total_modificados
        self.stats['vendedores_no_encontrados'] = no_encontrados

        self.stdout.write(f'\n  Resultados:')
        self.stdout.write(f'    - Nuevos asignados:       {asignados:,}')
        self.stdout.write(f'    - Actualizados:           {actualizados:,}')
        self.stdout.write(f'    - Sin cambio (correcto):  {sin_cambio:,}')
        self.stdout.write(f'    - Sin info en MySQL:      {no_encontrados:,}')
        self.stdout.write(f'    - Codigo no encontrado:   {codigo_no_match:,}')

        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] Vendedores modificados: {total_modificados:,}'
        ))

    # =========================================================================
    # IMPORTAR DTEs FALTANTES (con métodos de pago y vendedor)
    # =========================================================================

    def importar_dtes_faltantes(self):
        """
        Importa DTEs que existen en MySQL (tabla ventas) pero no en Django.
        Incluye métodos de pago y vendedor.
        OPTIMIZADO: Compara por (n_documento, sucursal) para evitar duplicados.
        """
        self.stdout.write('\n' + '='*70)
        self.stdout.write('[6] IMPORTAR DTEs FALTANTES (con pagos y vendedor)')
        self.stdout.write('='*70)

        # =====================================================================
        # PASO 1: Obtener DTEs existentes en Django por (n_documento, sucursal_id)
        # =====================================================================
        self.stdout.write('  Cargando DTEs existentes de Django (por n_doc + sucursal)...')
        dtes_existentes = set()
        for dte in Dte.objects.select_related('sucursal').only('numero_documento', 'sucursal'):
            suc_dir = dte.sucursal.direccion if dte.sucursal else ''
            dtes_existentes.add((str(dte.numero_documento), suc_dir))
        self.stdout.write(f'    DTEs existentes en Django: {len(dtes_existentes):,}')

        # =====================================================================
        # PASO 2: Obtener TODOS los registros de ventas de MySQL en UNA consulta
        # =====================================================================
        self.stdout.write('  Cargando datos de MySQL (ventas)...')
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        cursor.execute('''
            SELECT 
                n_documento,
                tipo_documento,
                fecha,
                monto_pagado,
                sub_total,
                sucursal,
                codigo_vendedor,
                nombre_vendedor,
                metodo_pago,
                tarjeta,
                voucher,
                descuento,
                descuento_tbk
            FROM ventas
            WHERE n_documento IS NOT NULL
              AND n_documento != ''
              AND n_documento != '0'
            ORDER BY n_documento, fecha, ID
        ''')
        
        # Procesar en memoria: agrupar por (n_documento, sucursal)
        documentos_faltantes = {}  # {(n_documento, sucursal_dir): {info_dte}}
        pagos_por_doc = {}         # {(n_documento, sucursal_dir): [pagos]}
        
        total_mysql = 0
        ya_existen = 0
        for row in cursor:
            total_mysql += 1
            n_doc = str(row['n_documento']).strip()
            suc_dir = row['sucursal'] or ''
            
            # Clave única: (n_documento, sucursal)
            key = (n_doc, suc_dir)
            
            # Solo procesar si NO existe en Django (por n_doc + sucursal)
            if key in dtes_existentes:
                ya_existen += 1
                continue
            
            # Primera vez que vemos este documento+sucursal: guardar info del DTE
            if key not in documentos_faltantes:
                documentos_faltantes[key] = {
                    'n_documento': n_doc,
                    'tipo_documento': row['tipo_documento'],
                    'fecha': row['fecha'],
                    'total': 0,
                    'subtotal': 0,
                    'sucursal_dir': suc_dir,
                    'codigo_vendedor': str(row['codigo_vendedor'] or '').strip(),
                    'nombre_vendedor': row['nombre_vendedor'],
                }
                pagos_por_doc[key] = []
            
            # Acumular montos
            monto = int(row['monto_pagado'] or 0)
            subtotal = int(row['sub_total'] or 0)
            documentos_faltantes[key]['total'] += monto
            documentos_faltantes[key]['subtotal'] += subtotal
            
            # Agregar pago
            pagos_por_doc[key].append({
                'metodo_pago': row['metodo_pago'],
                'tarjeta': row['tarjeta'],
                'monto': monto,
                'voucher': row['voucher'],
                'descuento': int(row['descuento'] or 0),
                'descuento_tbk': int(row['descuento_tbk'] or 0),
            })
        
        cursor.close()
        
        self.stdout.write(f'    Total registros MySQL: {total_mysql:,}')
        self.stdout.write(f'    Ya existen en Django: {ya_existen:,}')
        self.stdout.write(f'    Documentos faltantes: {len(documentos_faltantes):,}')

        if len(documentos_faltantes) == 0:
            self.stdout.write(self.style.SUCCESS('  [OK] No hay documentos faltantes'))
            return

        # =====================================================================
        # PASO 3: Crear DTEs y pagos
        # =====================================================================
        self.stdout.write('  Creando DTEs y pagos...')
        
        dtes_creados = 0
        pagos_creados = 0
        sin_sucursal = 0
        con_vendedor = 0
        
        # Listas para bulk_create
        dtes_batch = []
        
        for key, info in documentos_faltantes.items():
            n_doc, suc_dir = key
            
            # Buscar sucursal por dirección
            sucursal = None
            if suc_dir:
                sucursal = self.cache_sucursales_dir.get(suc_dir)
            
            if not sucursal:
                sin_sucursal += 1
                continue

            # Buscar vendedor por código
            vendedor = None
            if info['codigo_vendedor']:
                vendedor = self.cache_vendedores.get(info['codigo_vendedor'])
                if vendedor:
                    con_vendedor += 1

            # Mapear tipo de documento
            tipo_doc = self.mapear_tipo_documento(info['tipo_documento'] or '')
            
            # Determinar tipo de transacción
            if tipo_doc in ['FACTURA', 'FACTURA ELECTRONICA', 'FACTURA EXENTA']:
                tipo_transaccion = 'VENTA'
            else:
                tipo_transaccion = 'VENTA_PUBLICO'

            # Calcular montos
            total = info['total']
            subtotal = info['subtotal']
            neto = subtotal if subtotal > 0 else int(total / 1.19)

            # Obtener empresa por defecto (la de la sucursal)
            empresa = sucursal.empresa if sucursal and sucursal.empresa else None
            if not empresa:
                # Buscar primera empresa disponible
                empresa = list(self.cache_empresas_rut.values())[0] if self.cache_empresas_rut else None

            if not self.dry_run:
                # Crear DTE con todos los campos requeridos
                dte = Dte(
                    numero_documento=info['n_documento'],
                    tipo_documento=tipo_doc,
                    fecha_emision=info['fecha'],
                    fecha_vencimiento=info['fecha'],  # Mismo día para ventas
                    monto_con_iva=total,
                    monto_neto=neto,
                    estado_pago='PAGADO',
                    estado_dte='EMITIDO',
                    responsable='',
                    diasCredito=0,
                    bultos=0,
                    unidades_productos=0,
                    sucursal=sucursal,
                    tipo_transaccion=tipo_transaccion,
                    vendedor=vendedor,
                    emisor=empresa,
                )
                dte.save()  # Necesitamos el ID para los pagos
                dtes_creados += 1

                # Crear métodos de pago
                pagos = pagos_por_doc.get(key, [])
                pagos_batch = []
                for pago in pagos:
                    metodo = self.mapear_metodo_pago(pago['metodo_pago'], pago['tarjeta'])
                    
                    pagos_batch.append(Dte_Detalle_Pago(
                        dte=dte,
                        metodo_pago=metodo,
                        monto=pago['monto'],
                        voucher=pago['voucher'] or '',
                        tipo_tarjeta=pago['tarjeta'] or '',
                    ))
                
                if pagos_batch:
                    Dte_Detalle_Pago.objects.bulk_create(pagos_batch)
                    pagos_creados += len(pagos_batch)

                # Agregar al cache
                self.cache_dtes[(info['n_documento'], tipo_doc)] = dte
                
                # Mostrar progreso cada 1000
                if dtes_creados % 1000 == 0:
                    self.stdout.write(f'    Progreso: {dtes_creados:,} DTEs creados...')
            else:
                dtes_creados += 1
                pagos_creados += len(pagos_por_doc.get(key, []))

        self.stats['dtes_faltantes_creados'] = dtes_creados
        self.stats['pagos_faltantes_creados'] = pagos_creados

        self.stdout.write(f'\n  Resumen:')
        self.stdout.write(f'    - DTEs creados:       {dtes_creados:,}')
        self.stdout.write(f'    - Pagos creados:      {pagos_creados:,}')
        self.stdout.write(f'    - Con vendedor:       {con_vendedor:,}')
        self.stdout.write(f'    - Sin sucursal:       {sin_sucursal:,}')
        
        self.stdout.write(self.style.SUCCESS(
            f'\n  [OK] DTEs faltantes importados: {dtes_creados:,}'
        ))

    # =========================================================================
    # ESTADÍSTICAS
    # =========================================================================

    def show_statistics(self):
        self.stdout.write('\n' + '='*70)
        self.stdout.write('RESUMEN DE SINCRONIZACION')
        self.stdout.write('='*70)
        
        stats_list = [
            ('DTEs nuevos (desde tabla dte)', self.stats.get('dtes_nuevos', 0)),
            ('DTEs existentes (omitidos)', self.stats.get('dtes_existentes', 0)),
            ('DTEs sin sucursal', self.stats.get('dtes_sin_sucursal', 0)),
            ('Productos DTE nuevos', self.stats.get('dte_productos_nuevos', 0)),
            ('Productos DTE - DTE no encontrado', self.stats.get('dte_productos_dte_no_encontrado', 0)),
            ('Productos DTE - Producto no encontrado', self.stats.get('dte_productos_prod_no_encontrado', 0)),
            ('DTEs creados (desde ventas)', self.stats.get('pagos_dte_creados', 0)),
            ('Pagos nuevos', self.stats.get('pagos_nuevos', 0)),
            ('Pagos - DTE no encontrado', self.stats.get('pagos_dte_no_encontrado', 0)),
            ('Pagos - Duplicados', self.stats.get('pagos_duplicados', 0)),
            ('Sucursales corregidas', self.stats.get('sucursales_corregidas', 0)),
            ('Sucursales no corregidas', self.stats.get('sucursales_no_corregidas', 0)),
            ('Vendedores asignados', self.stats.get('vendedores_asignados', 0)),
            ('Vendedores no encontrados', self.stats.get('vendedores_no_encontrados', 0)),
            ('DTEs faltantes creados', self.stats.get('dtes_faltantes_creados', 0)),
            ('Pagos faltantes creados', self.stats.get('pagos_faltantes_creados', 0)),
        ]
        
        for nombre, valor in stats_list:
            self.stdout.write(f'  {nombre:40}: {valor:>10,}')
        
        self.stdout.write('='*70)
