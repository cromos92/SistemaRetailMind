"""
Django management command para actualizar campos de sucursal en datos ya migrados

Este comando NO borra datos, solo ACTUALIZA los campos de sucursal que quedaron NULL
después de la migración inicial.

Uso:
    python manage.py actualizar_sucursales_migracion
    python manage.py actualizar_sucursales_migracion --dry-run
    python manage.py actualizar_sucursales_migracion --only-dtes
    python manage.py actualizar_sucursales_migracion --only-movimientos
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

from app.models import Sucursal, Movimientos_Producto, Dte


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Actualiza campos de sucursal en DTEs y Movimientos ya migrados'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo mostrar qué se actualizaría')
        parser.add_argument('--only-dtes', action='store_true', help='Solo actualizar DTEs')
        parser.add_argument('--only-movimientos', action='store_true', help='Solo actualizar Movimientos')
        parser.add_argument('--batch-size', type=int, default=1000, help='Tamaño del batch para updates')

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.only_dtes = options['only_dtes']
        self.only_movimientos = options['only_movimientos']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 MODO DRY-RUN - No se guardarán cambios'))
        
        # Conectar a MySQL
        self.stdout.write('🔌 Conectando a MySQL...')
        try:
            self.mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD
            )
            self.stdout.write(self.style.SUCCESS('  ✓ Conectado a MySQL'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ✗ Error MySQL: {e}'))
            return

        # Cargar caché de sucursales
        self.stdout.write('📦 Cargando sucursales...')
        self.cache_sucursales = {}
        for sucursal in Sucursal.objects.select_related('empresa').all():
            self.cache_sucursales[sucursal.alias] = sucursal
        self.stdout.write(f'  ✓ {len(self.cache_sucursales)} sucursales en caché')
        
        # Mostrar sucursales disponibles
        self.stdout.write('  📍 Sucursales disponibles:')
        for alias in sorted(self.cache_sucursales.keys()):
            self.stdout.write(f'     - {alias}')

        # Ejecutar actualizaciones
        if not self.only_movimientos:
            self.actualizar_dtes()
        
        if not self.only_dtes:
            self.actualizar_movimientos()

        self.mysql_conn.close()
        self.stdout.write(self.style.SUCCESS('\n✅ Proceso completado'))

    def actualizar_dtes(self):
        """Actualiza el campo sucursal en DTEs que lo tienen NULL"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('📄 ACTUALIZANDO DTEs...')
        self.stdout.write('=' * 70)

        # Contar DTEs sin sucursal
        total_sin_sucursal = Dte.objects.filter(sucursal__isnull=True).count()
        self.stdout.write(f'  📊 DTEs sin sucursal asignada: {total_sin_sucursal}')

        if total_sin_sucursal == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ Todos los DTEs ya tienen sucursal'))
            return

        # Obtener datos de MySQL
        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT 
                n_documento, tipo_documento, fecha_emision, 
                bodega_inicio, bodega_destino, rut_emisor
            FROM dte
            WHERE bodega_inicio IS NOT NULL OR bodega_destino IS NOT NULL
        ''')
        
        mysql_data = {}
        mysql_data_by_num_fecha = {}  # Índice alternativo: solo numero + fecha
        
        for row in cursor:
            fecha = row['fecha_emision']
            if hasattr(fecha, 'strftime'):
                fecha_str = fecha.strftime('%Y-%m-%d')
            else:
                fecha_str = str(fecha)
            
            # Normalizar tipo_documento a mayúsculas
            tipo_doc_normalizado = (row['tipo_documento'] or '').upper().strip()
            
            data = {
                'bodega_inicio': row['bodega_inicio'],
                'bodega_destino': row['bodega_destino'],
                'rut_emisor': row['rut_emisor']
            }
            
            # Clave principal: numero + tipo normalizado + fecha
            key = (row['n_documento'], tipo_doc_normalizado, fecha_str)
            mysql_data[key] = data
            
            # Clave alternativa: solo numero + fecha (para casos donde tipo no coincide)
            key_alt = (row['n_documento'], fecha_str)
            if key_alt not in mysql_data_by_num_fecha:
                mysql_data_by_num_fecha[key_alt] = data
        
        cursor.close()
        self.stdout.write(f'  📊 Registros MySQL con bodega: {len(mysql_data)}')

        # Actualizar DTEs en PostgreSQL
        actualizados = 0
        no_encontrados = 0
        sucursal_no_existe = 0
        
        dtes_sin_sucursal = Dte.objects.filter(sucursal__isnull=True).select_related('emisor')
        
        for idx, dte in enumerate(dtes_sin_sucursal, 1):
            # Crear clave para buscar en MySQL
            fecha_str = dte.fecha_emision.strftime('%Y-%m-%d') if dte.fecha_emision else ''
            tipo_doc_normalizado = (dte.tipo_documento or '').upper().strip()
            
            # Intentar clave principal: numero + tipo + fecha
            key = (dte.numero_documento, tipo_doc_normalizado, fecha_str)
            mysql_row = mysql_data.get(key)
            
            # Si no encuentra, intentar clave alternativa: solo numero + fecha
            if not mysql_row:
                key_alt = (dte.numero_documento, fecha_str)
                mysql_row = mysql_data_by_num_fecha.get(key_alt)
            
            if not mysql_row:
                no_encontrados += 1
                continue
            
            # Buscar sucursal por alias
            alias = mysql_row['bodega_inicio'] or mysql_row['bodega_destino']
            sucursal = self.cache_sucursales.get(alias)
            
            if not sucursal:
                sucursal_no_existe += 1
                if sucursal_no_existe <= 10:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Sucursal no encontrada: "{alias}"'))
                continue
            
            # Actualizar
            if not self.dry_run:
                dte.sucursal = sucursal
                dte.save(update_fields=['sucursal'])
            actualizados += 1
            
            if idx % 500 == 0:
                self.stdout.write(f'  Procesados: {idx}/{total_sin_sucursal} ({actualizados} actualizados)')

        self.stdout.write(self.style.SUCCESS(f'\n  ✓ DTEs actualizados: {actualizados}'))
        self.stdout.write(f'  ⚠ No encontrados en MySQL: {no_encontrados}')
        self.stdout.write(f'  ⚠ Sucursal no existe: {sucursal_no_existe}')

    def actualizar_movimientos(self):
        """Actualiza el campo sucursal_origen en Movimientos que lo tienen NULL"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('📦 ACTUALIZANDO MOVIMIENTOS...')
        self.stdout.write('=' * 70)

        # Contar movimientos sin sucursal_origen
        total_sin_sucursal = Movimientos_Producto.objects.filter(
            sucursal_origen__isnull=True,
            referencia_externa__startswith='MIG:'
        ).count()
        self.stdout.write(f'  📊 Movimientos sin sucursal_origen: {total_sin_sucursal}')

        if total_sin_sucursal == 0:
            self.stdout.write(self.style.SUCCESS('  ✓ Todos los movimientos ya tienen sucursal'))
            return

        # Obtener datos de MySQL en batches
        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('SELECT id, alias FROM movimiento_productos WHERE alias IS NOT NULL')
        
        mysql_alias = {}
        for row in cursor:
            mysql_alias[row['id']] = row['alias']
        cursor.close()
        self.stdout.write(f'  📊 Registros MySQL con alias: {len(mysql_alias)}')

        # Actualizar en batches
        actualizados = 0
        no_encontrados = 0
        sucursal_no_existe = 0
        
        movimientos = Movimientos_Producto.objects.filter(
            sucursal_origen__isnull=True,
            referencia_externa__startswith='MIG:'
        ).only('id', 'referencia_externa', 'sucursal_origen')

        batch_updates = []
        
        for idx, mov in enumerate(movimientos, 1):
            # Extraer ID de MySQL desde referencia_externa
            try:
                mysql_id = int(mov.referencia_externa.replace('MIG:', ''))
            except (ValueError, AttributeError):
                no_encontrados += 1
                continue
            
            # Buscar alias en datos de MySQL
            alias = mysql_alias.get(mysql_id)
            if not alias:
                no_encontrados += 1
                continue
            
            # Buscar sucursal
            sucursal = self.cache_sucursales.get(alias)
            if not sucursal:
                sucursal_no_existe += 1
                if sucursal_no_existe <= 10:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Sucursal no encontrada: "{alias}"'))
                continue
            
            if not self.dry_run:
                mov.sucursal_origen = sucursal
                batch_updates.append(mov)
                
                if len(batch_updates) >= self.batch_size:
                    Movimientos_Producto.objects.bulk_update(batch_updates, ['sucursal_origen'])
                    actualizados += len(batch_updates)
                    batch_updates = []
                    self.stdout.write(f'  Procesados: {idx}/{total_sin_sucursal} ({actualizados} actualizados)')
            else:
                actualizados += 1
            
            if idx % 10000 == 0:
                self.stdout.write(f'  Procesados: {idx}/{total_sin_sucursal}...')

        # Guardar batch final
        if batch_updates and not self.dry_run:
            Movimientos_Producto.objects.bulk_update(batch_updates, ['sucursal_origen'])
            actualizados += len(batch_updates)

        self.stdout.write(self.style.SUCCESS(f'\n  ✓ Movimientos actualizados: {actualizados}'))
        self.stdout.write(f'  ⚠ No encontrados en MySQL: {no_encontrados}')
        self.stdout.write(f'  ⚠ Sucursal no existe: {sucursal_no_existe}')

    def _normalizar_tipo_doc(self, tipo):
        """Normaliza tipo de documento para comparación"""
        if not tipo:
            return ''
        tipo = tipo.upper().strip()
        mapeo = {
            'FACTURA ELECTRONICA': 'Factura Electronica',
            'BOLETA ELECTRONICA': 'Boleta Electronica', 
            'GUIA': 'Despacho Electronico',
            'NOTA DE CREDITO': 'Nota Credito',
        }
        return mapeo.get(tipo, tipo)
