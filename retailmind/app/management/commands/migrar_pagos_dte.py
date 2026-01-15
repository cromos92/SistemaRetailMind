"""
Django management command para migrar pagos de ventas desde MySQL
Para cuadratura y arqueo de caja

Uso:
    python manage.py migrar_pagos_dte
    python manage.py migrar_pagos_dte --dry-run
    python manage.py migrar_pagos_dte --batch-size 2000
"""

import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Sucursal, Dte, Dte_Detalle_Pago


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

# =============================================================================
# MAPEO DE MÉTODOS DE PAGO MySQL → Django
# Valores válidos en Django (METODO_PAGO_TICKET_CHOICES):
#   EFECTIVO, TARJETA_DEBITO, TARJETA_CREDITO, TRANSFERENCIA, CHEQUE, OTRO,
#   TBK_POS_INTEGRADO, TBK_MANUAL, TBK_DEBITO_POS, TBK_CREDITO_POS, TBK_PREPAGO_POS,
#   TARJETA_COMERCIAL, VENTA_INTERNET, ORDEN_COMPRA, CREDITO_TRABAJADOR,
#   CREDITO_EXTERNO, CONVENIO, MULTIPLE
# =============================================================================

METODO_PAGO_MAP = {
    # Efectivo
    'Efectivo': 'EFECTIVO',
    
    # Transbank
    'Tarjeta TBK': 'TBK_MANUAL',
    'Tarjeta TBK Pos Integrado': 'TBK_POS_INTEGRADO',
    
    # Tarjetas comerciales
    'Tarjeta Comercial': 'TARJETA_COMERCIAL',
    
    # Convenios y créditos
    'Convenio': 'CONVENIO',
    'Credito': 'CREDITO_EXTERNO',
    'Credito Trabajador': 'CREDITO_TRABAJADOR',
    'Credito Orden Compra': 'ORDEN_COMPRA',
    'Orden Compra': 'ORDEN_COMPRA',
    
    # Transferencia
    'Transferencia': 'TRANSFERENCIA',
    
    # Venta Internet
    'Venta Internet': 'VENTA_INTERNET',
}

# Mapeo de tarjetas MySQL → método de pago Django
# Usado para ajustar el método según el tipo de tarjeta
TARJETA_METODO_MAP = {
    # Transbank Débito
    'REDCOMPRA DEBITO': 'TBK_DEBITO_POS',
    
    # Transbank Crédito
    'VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',
    ' VISA-MC-AMEX-DINER': 'TBK_CREDITO_POS',  # Con espacio al inicio
    'Tarjeta TBK': 'TBK_CREDITO_POS',
    
    # Tarjetas Comerciales
    'HITES': 'TARJETA_COMERCIAL',
    'RIPLEY': 'TARJETA_COMERCIAL',
    'ABCDIN': 'TARJETA_COMERCIAL',
    'PRESTO': 'TARJETA_COMERCIAL',
    'TRICOT': 'TARJETA_COMERCIAL',
    
    # Venta Internet
    'Mercado Pago': 'VENTA_INTERNET',
    'Mercado Libre': 'VENTA_INTERNET',
    'Paris': 'VENTA_INTERNET',
    'Falabella': 'VENTA_INTERNET',
    'Shopify': 'VENTA_INTERNET',
    
    # Crédito externo
    'Credito': 'CREDITO_EXTERNO',
}

# Mapeo de tipo documento MySQL → PostgreSQL
TIPO_DOC_MAP = {
    'Factura Electronica': 'FACTURA ELECTRONICA',
    'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
    'Boleta Electronica': 'BOLETA ELECTRONICA',
    'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
    'Boleta': 'BOLETA PAPEL',
    'BOLETA': 'BOLETA PAPEL',
    'Nota de Credito': 'NOTA DE CREDITO',
    'NOTA DE CREDITO': 'NOTA DE CREDITO',
}


class Command(BaseCommand):
    help = 'Migra pagos de ventas desde MySQL para cuadratura'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo mostrar qué se migraría')
        parser.add_argument('--batch-size', type=int, default=1000, help='Tamaño del batch')
        parser.add_argument('--limit', type=int, help='Limitar cantidad de registros')

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.limit = options.get('limit')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] No se guardaran cambios'))
        
        # Conectar a MySQL
        self.stdout.write('[*] Conectando a MySQL...')
        try:
            self.mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD
            )
            self.stdout.write(self.style.SUCCESS('  [OK] Conectado a MySQL'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [ERROR] MySQL: {e}'))
            return

        self.migrar_pagos()
        
        self.mysql_conn.close()
        self.stdout.write(self.style.SUCCESS('\n[COMPLETADO] Migracion de pagos finalizada'))

    def migrar_pagos(self):
        """Migra pagos de ventas para cuadratura"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('[PAGOS] MIGRANDO PAGOS DE VENTAS (CUADRATURA)...')
        self.stdout.write('=' * 70)

        # Pre-cargar sucursales por DIRECCIÓN
        self.stdout.write('  [+] Cargando sucursales por direccion...')
        cache_sucursales = {}
        for sucursal in Sucursal.objects.all():
            if sucursal.direccion:
                cache_sucursales[sucursal.direccion] = sucursal
        self.stdout.write(f'  [OK] {len(cache_sucursales)} sucursales en cache')
        self.stdout.write(f'     Direcciones: {list(cache_sucursales.keys())}')

        # Pre-cargar DTEs para búsqueda rápida con múltiples índices
        self.stdout.write('  [+] Cargando DTEs...')
        cache_dtes_by_id = {}  # Por ID de PostgreSQL
        cache_dtes_by_num_monto = {}  # Por numero_documento + monto (MÁS CONFIABLE)
        cache_dtes_by_num_monto_sucursal = {}  # Por numero_documento + monto + sucursal_dir
        cache_dtes_by_num_tipo = {}  # Por numero_documento + tipo
        cache_dtes_by_num_tipo_sucursal = {}  # Por numero_documento + tipo + sucursal_dir
        cache_dtes_by_num = {}  # Solo por numero (fallback)
        cache_dtes_by_num_sucursal = {}  # Por numero + sucursal_dir
        
        for dte in Dte.objects.select_related('sucursal').all():
            cache_dtes_by_id[dte.id] = dte
            
            # Índice por numero + monto (más confiable - ignora fecha incorrecta)
            monto_dte = int(dte.monto_con_iva or 0)
            key_monto = (dte.numero_documento, monto_dte)
            cache_dtes_by_num_monto[key_monto] = dte
            
            suc_dir = dte.sucursal.direccion if dte.sucursal else ''
            if suc_dir:
                cache_dtes_by_num_monto_sucursal[(dte.numero_documento, monto_dte, suc_dir)] = dte
            
            # Índice por numero + tipo
            key_tipo = (dte.numero_documento, dte.tipo_documento)
            cache_dtes_by_num_tipo[key_tipo] = dte
            if suc_dir:
                cache_dtes_by_num_tipo_sucursal[(dte.numero_documento, dte.tipo_documento, suc_dir)] = dte
            
            # Solo por numero (fallback)
            if dte.numero_documento not in cache_dtes_by_num:
                cache_dtes_by_num[dte.numero_documento] = dte
            if suc_dir:
                cache_dtes_by_num_sucursal[(dte.numero_documento, suc_dir)] = dte
        
        self.stdout.write(f'  [OK] {len(cache_dtes_by_id)} DTEs en cache')
        self.stdout.write(f'     {len(cache_dtes_by_num_monto)} indices por numero+monto')

        # Crear mapeo ID_dte MySQL → DTE PostgreSQL
        # Necesitamos buscar por numero_documento ya que ID_dte de MySQL = ID del DTE en MySQL
        # pero en PostgreSQL los IDs son diferentes
        
        # Pre-cargar pagos existentes
        self.stdout.write('  [+] Cargando pagos existentes...')
        pagos_existentes = set(
            Dte_Detalle_Pago.objects.values_list('dte_id', 'voucher', 'monto')
        )
        self.stdout.write(f'  [OK] {len(pagos_existentes)} pagos ya existen')

        # Contar registros en MySQL
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM ventas')
        total = cursor.fetchone()['total']
        
        if self.limit and self.limit < total:
            total = self.limit
        
        self.stdout.write(f'  [i] Ventas en MySQL: {total:,}')

        # Obtener datos
        cursor.execute(f'''
            SELECT 
                ID, tipo_documento, metodo_pago, n_documento, tarjeta,
                sub_total, descuento, monto_pagado, sucursal, fecha,
                voucher, n_convenio, correlativo_ticket, responsable,
                nombre_vendedor, hora, rut_convenio, descuento_tbk, estado, ID_dte
            FROM ventas
            ORDER BY fecha, ID
            {f"LIMIT {self.limit}" if self.limit else ""}
        ''')

        count = 0
        duplicados = 0
        dte_no_encontrado = 0
        batch = []

        for idx, row in enumerate(cursor, 1):
            # Buscar DTE - priorizar por numero + sub_total (total del DTE, no monto parcial)
            dte = None
            
            if row['n_documento']:
                sucursal_dir = row['sucursal'] or ''
                # sub_total es el total antes de descuento, por lo que usamos total_doc = sub_total - descuento
                sub_total = int(row['sub_total'] or 0)
                descuento = int(row['descuento'] or 0)
                total_doc = max(0, sub_total - descuento)
                
                # PRIMERO: Buscar por numero + total_doc + sucursal (más confiable)
                if sucursal_dir:
                    key_monto_suc = (row['n_documento'], total_doc, sucursal_dir)
                    dte = cache_dtes_by_num_monto_sucursal.get(key_monto_suc)
                
                # SEGUNDO: Buscar por numero + total_doc (fallback)
                if not dte:
                    key_monto = (row['n_documento'], total_doc)
                    dte = cache_dtes_by_num_monto.get(key_monto)
                
                # TERCERO: Si no encuentra, buscar por numero + tipo + sucursal
                if not dte:
                    tipo_mysql = row['tipo_documento'] or ''
                    tipo_pg = TIPO_DOC_MAP.get(tipo_mysql, TIPO_DOC_MAP.get(tipo_mysql.upper(), 'BOLETA ELECTRONICA'))
                    if sucursal_dir:
                        key_suc = (row['n_documento'], tipo_pg, sucursal_dir)
                        dte = cache_dtes_by_num_tipo_sucursal.get(key_suc)
                    if not dte:
                        key = (row['n_documento'], tipo_pg)
                        dte = cache_dtes_by_num_tipo.get(key)
                
                # CUARTO: Fallback - buscar solo por numero (con sucursal si existe)
                if not dte:
                    if sucursal_dir:
                        dte = cache_dtes_by_num_sucursal.get((row['n_documento'], sucursal_dir))
                    if not dte:
                        dte = cache_dtes_by_num.get(row['n_documento'])

            if not dte:
                dte_no_encontrado += 1
                if dte_no_encontrado <= 5:
                    self.stdout.write(self.style.WARNING(
                        f'  [!] DTE no encontrado: n_doc={row["n_documento"]}, tipo={row["tipo_documento"]}'
                    ))
                continue

            # Verificar duplicado
            voucher_val = row['voucher']
            voucher = str(voucher_val) if voucher_val and voucher_val != 0 else None
            monto = int(row['monto_pagado'] or 0)
            dup_key = (dte.id, voucher, monto)
            
            if dup_key in pagos_existentes:
                duplicados += 1
                continue

            # Mapear método de pago
            metodo_mysql = row['metodo_pago'] or 'Efectivo'
            metodo_pago = METODO_PAGO_MAP.get(metodo_mysql, 'EFECTIVO')
            
            # Ajustar método según el tipo de tarjeta
            tarjeta = row['tarjeta'] or ''
            tarjeta_stripped = tarjeta.strip()
            
            # Si la tarjeta tiene un mapeo específico, usarlo
            if tarjeta_stripped in TARJETA_METODO_MAP:
                metodo_pago = TARJETA_METODO_MAP[tarjeta_stripped]
            elif tarjeta.startswith('OrdenCompra'):
                metodo_pago = 'ORDEN_COMPRA'

            # Crear notas con información adicional
            notas_partes = []
            if row['n_convenio'] and row['n_convenio'] != 0:
                notas_partes.append(f"Conv: {row['n_convenio']}")
            if row['rut_convenio']:
                notas_partes.append(f"RUT: {row['rut_convenio']}")
            if row['nombre_vendedor']:
                notas_partes.append(f"Vend: {row['nombre_vendedor']}")
            if row['descuento_tbk'] and row['descuento_tbk'] != 0:
                notas_partes.append(f"DescTBK: {row['descuento_tbk']}")
            if row['correlativo_ticket']:
                notas_partes.append(f"Tkt: {row['correlativo_ticket']}")
            if row['sucursal']:
                notas_partes.append(f"Suc: {row['sucursal']}")
            notas = ' | '.join(notas_partes) if notas_partes else None

            # Tipo tarjeta (limpiar N.N)
            tipo_tarjeta = tarjeta if tarjeta and tarjeta != 'N.N' else None

            if not self.dry_run:
                batch.append(Dte_Detalle_Pago(
                    dte=dte,
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=tipo_tarjeta,
                    voucher=voucher,
                    monto=monto,
                    notas=notas
                ))
                pagos_existentes.add(dup_key)

                if len(batch) >= self.batch_size:
                    Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
                    self.stdout.write(f'  Procesados: {idx:,}/{total:,} ({count:,} migrados)')
            else:
                count += 1

            if idx % 5000 == 0:
                self.stdout.write(f'  Procesados: {idx:,}/{total:,}...')

        # Guardar batch final
        if batch and not self.dry_run:
            Dte_Detalle_Pago.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()

        self.stdout.write(self.style.SUCCESS(f'\n  [OK] Pagos migrados: {count:,}'))
        self.stdout.write(f'  [!] DTE no encontrado: {dte_no_encontrado:,}')
        self.stdout.write(f'  [!] Duplicados omitidos: {duplicados:,}')
