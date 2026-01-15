"""
Script para actualizar sucursales de DTEs usando el alias de MySQL
Matchea por numero_documento + fecha + alias y verifica que empresa coincida
VERSION OPTIMIZADA V2 - Batch updates masivos
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from app.models import Dte, Sucursal


# Configuracion MySQL
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Actualiza sucursales de DTEs usando alias de MySQL (version optimizada V2)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar que se actualizaria sin hacer cambios'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limitar cantidad de registros a procesar (0 = sin limite)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Tamaño del batch para updates (default: 5000)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        self.batch_size = options['batch_size']
        
        self.stdout.write('=' * 70)
        self.stdout.write('ACTUALIZACION DE SUCURSALES EN DTEs (OPTIMIZADO V2)')
        self.stdout.write('=' * 70)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('MODO DRY-RUN: No se haran cambios'))
        
        inicio = datetime.now()
        
        # 1. Verificar estado inicial
        self.mostrar_estado_inicial()
        
        # 2. Cargar cache de sucursales
        sucursales = self.cargar_sucursales()
        
        # 3. Ejecutar actualizacion con estrategia batch
        self.actualizar_dtes_batch(sucursales, dry_run, limit)
        
        # 4. Mostrar estado final
        if not dry_run:
            self.mostrar_estado_final()
        
        tiempo = (datetime.now() - inicio).total_seconds()
        self.stdout.write(self.style.SUCCESS(f'\n✅ Completado en {tiempo:.1f} segundos'))

    def progress_bar(self, current, total, width=40):
        """Genera una barra de progreso"""
        percent = current / total if total > 0 else 0
        filled = int(width * percent)
        bar = '█' * filled + '░' * (width - filled)
        return f'[{bar}] {percent*100:.1f}% ({current:,}/{total:,})'

    def mostrar_estado_inicial(self):
        """Muestra estado antes de actualizar"""
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL')
            sin_sucursal = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NOT NULL')
            con_sucursal = cursor.fetchone()[0]
        
        self.stdout.write(f'\n📊 ESTADO INICIAL')
        self.stdout.write(f'   ⚠️  DTEs sin sucursal: {sin_sucursal:,}')
        self.stdout.write(f'   ✅ DTEs con sucursal: {con_sucursal:,}')

    def cargar_sucursales(self):
        """Carga sucursales en un diccionario {alias: {empresa_id: sucursal_id}}"""
        sucursales = {}
        for s in Sucursal.objects.all():
            if s.alias not in sucursales:
                sucursales[s.alias] = {}
            sucursales[s.alias][s.empresa_id] = s.id
        
        self.stdout.write(f'\n📦 {len(sucursales)} alias de sucursales cargados')
        return sucursales

    def actualizar_dtes_batch(self, sucursales, dry_run, limit):
        """Actualiza DTEs usando estrategia de batch updates masivos"""
        self.stdout.write(f'\n🔌 Conectando a MySQL...')
        
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            # Obtener DTEs con su bodega (alias)
            query = f"""
                SELECT 
                    n_documento,
                    tipo_documento,
                    bodega_inicio,
                    bodega_destino,
                    DATE(fecha_emision) as fecha_emision
                FROM dte
                WHERE n_documento IS NOT NULL 
                  AND n_documento > 0
                  AND (bodega_inicio IS NOT NULL OR bodega_destino IS NOT NULL)
                ORDER BY fecha_emision DESC
                {f'LIMIT {limit}' if limit > 0 else ''}
            """
            cursor.execute(query)
            datos_mysql = cursor.fetchall()
            
            self.stdout.write(self.style.SUCCESS(f'   ✓ {len(datos_mysql):,} DTEs obtenidos de MySQL'))
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error MySQL: {e}'))
            return
        
        # Crear índice de MySQL: n_documento -> alias
        # ⚠️ IMPORTANTE: Ya no usamos fecha porque las fechas no coinciden entre sistemas
        self.stdout.write(f'\n📋 Indexando datos de MySQL (solo por numero_documento)...')
        mysql_index = {}
        for row in datos_mysql:
            alias = row['bodega_inicio'] if row['bodega_inicio'] else row['bodega_destino']
            if alias and row['n_documento']:
                # Usamos solo n_documento como key
                n_doc = row['n_documento']
                if n_doc not in mysql_index:
                    mysql_index[n_doc] = alias
        
        self.stdout.write(f'   ✓ {len(mysql_index):,} registros indexados')
        
        # Cargar DTEs de PostgreSQL que necesitan actualización
        self.stdout.write(f'\n📥 Cargando DTEs sin sucursal de PostgreSQL...')
        
        with connection.cursor() as pg_cursor:
            pg_cursor.execute("""
                SELECT id, numero_documento, fecha_emision::date, emisor_id
                FROM app_dte
                WHERE sucursal_id IS NULL
            """)
            dtes_pg = pg_cursor.fetchall()
        
        total_pg = len(dtes_pg)
        self.stdout.write(f'   ✓ {total_pg:,} DTEs a procesar')
        
        # Procesar y preparar updates
        self.stdout.write(f'\n🔄 Procesando coincidencias...')
        
        updates = []  # Lista de (dte_id, sucursal_id)
        sin_match = 0
        alias_no_encontrado = 0
        empresa_no_match = 0
        
        for idx, (dte_id, numero_doc, fecha, emisor_id) in enumerate(dtes_pg, 1):
            if idx % 50000 == 0:
                sys.stdout.write(f'\r   {self.progress_bar(idx, total_pg)}')
                sys.stdout.flush()
            
            # Buscar en índice MySQL (solo por numero_documento)
            alias = mysql_index.get(numero_doc)
            
            if not alias:
                sin_match += 1
                continue
            
            # Buscar sucursal por alias y empresa
            if alias not in sucursales:
                alias_no_encontrado += 1
                continue
            
            sucursal_id = sucursales[alias].get(emisor_id)
            if not sucursal_id:
                empresa_no_match += 1
                continue
            
            updates.append((dte_id, sucursal_id))
        
        sys.stdout.write(f'\r   {self.progress_bar(total_pg, total_pg)}\n')
        sys.stdout.flush()
        
        self.stdout.write(f'\n   ✓ {len(updates):,} DTEs encontrados para actualizar')
        self.stdout.write(f'   ⚠️  Sin match en MySQL: {sin_match:,}')
        self.stdout.write(f'   ⚠️  Alias no existe: {alias_no_encontrado:,}')
        self.stdout.write(f'   ⚠️  Empresa no coincide: {empresa_no_match:,}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING(f'\n🔍 DRY-RUN: Se actualizarían {len(updates):,} DTEs'))
            return
        
        # Ejecutar updates en batches
        if not updates:
            self.stdout.write(self.style.WARNING('\n⚠️ No hay DTEs para actualizar'))
            return
        
        self.stdout.write(f'\n💾 Ejecutando {len(updates):,} updates en batches de {self.batch_size:,}...')
        
        total_updates = len(updates)
        actualizados = 0
        
        with connection.cursor() as pg_cursor:
            for i in range(0, total_updates, self.batch_size):
                batch = updates[i:i + self.batch_size]
                
                # Construir UPDATE con CASE para actualizar múltiples registros de una vez
                ids = [str(u[0]) for u in batch]
                
                # Crear tabla temporal de valores
                values_list = ', '.join([f"({dte_id}, {suc_id})" for dte_id, suc_id in batch])
                
                sql = f"""
                    UPDATE app_dte AS d
                    SET sucursal_id = v.sucursal_id
                    FROM (VALUES {values_list}) AS v(dte_id, sucursal_id)
                    WHERE d.id = v.dte_id
                """
                
                try:
                    pg_cursor.execute(sql)
                    actualizados += pg_cursor.rowcount
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'\n   ✗ Error en batch: {e}'))
                    continue
                
                # Mostrar progreso
                processed = min(i + self.batch_size, total_updates)
                sys.stdout.write(f'\r   {self.progress_bar(processed, total_updates)} | {actualizados:,} actualizados')
                sys.stdout.flush()
        
        sys.stdout.write('\n')
        self.stdout.write(self.style.SUCCESS(f'\n✅ Total actualizados: {actualizados:,}'))

    def mostrar_estado_final(self):
        """Muestra estado despues de actualizar"""
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL')
            sin_sucursal = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NOT NULL')
            con_sucursal = cursor.fetchone()[0]
        
        self.stdout.write(f'\n📊 ESTADO FINAL')
        self.stdout.write(f'   ⚠️  DTEs sin sucursal: {sin_sucursal:,}')
        self.stdout.write(f'   ✅ DTEs con sucursal: {con_sucursal:,}')
        
        # Distribucion por sucursal
        self.stdout.write(f'\n📈 Distribución por sucursal:')
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT s.alias, COUNT(d.id) as cantidad
                FROM app_dte d
                JOIN app_sucursal s ON d.sucursal_id = s.id
                GROUP BY s.alias
                ORDER BY cantidad DESC
            ''')
            for row in cursor.fetchall():
                self.stdout.write(f'   {row[0]}: {row[1]:,} DTEs')
