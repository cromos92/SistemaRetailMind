"""
Asigna vendedores a DTEs usando codigo_vendedor de MySQL ventas
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
from django.db import connection

from app.models import Vendedor


MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Asigna vendedores a DTEs usando codigo_vendedor'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=5000)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        
        self.stdout.write('=' * 70)
        self.stdout.write('ASIGNAR VENDEDORES A DTEs')
        self.stdout.write('=' * 70)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN]'))
        
        inicio = datetime.now()
        
        # 1. Cargar vendedores de PostgreSQL por codigo_vendedor
        cache_vendedores = self.cargar_vendedores()
        
        # 2. Cargar codigo_vendedor de MySQL ventas
        mysql_data = self.cargar_mysql_ventas()
        
        # 3. Asignar vendedores
        self.asignar_vendedores(mysql_data, cache_vendedores)
        
        tiempo = (datetime.now() - inicio).total_seconds()
        self.stdout.write(self.style.SUCCESS(f'\n[OK] Completado en {tiempo:.1f}s'))

    def progress_bar(self, current, total, width=40, extra=''):
        pct = current / total if total > 0 else 0
        filled = int(width * pct)
        bar = '#' * filled + '-' * (width - filled)
        return f'[{bar}] {pct*100:.1f}% ({current:,}/{total:,}) {extra}'

    def cargar_vendedores(self):
        """Carga vendedores por codigo_vendedor"""
        self.stdout.write('\n[1/3] Cargando vendedores...')
        
        cache = {}
        for v in Vendedor.objects.all():
            # Por codigo_vendedor
            if v.codigo_vendedor:
                cache[str(v.codigo_vendedor)] = v.id
        
        self.stdout.write(f'   Vendedores: {len(cache):,}')
        
        # Mostrar algunos
        for codigo, vid in list(cache.items())[:5]:
            self.stdout.write(f'      codigo={codigo} -> vendedor_id={vid}')
        
        return cache

    def cargar_mysql_ventas(self):
        """Carga n_documento -> codigo_vendedor de MySQL"""
        self.stdout.write('\n[2/3] Cargando ventas de MySQL...')
        
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Cargar ventas con codigo_vendedor, agrupando por n_documento
        # Tomamos el primer codigo_vendedor encontrado para cada documento
        cursor.execute('''
            SELECT n_documento, codigo_vendedor, fecha, sucursal
            FROM ventas
            WHERE n_documento > 0 
            AND codigo_vendedor IS NOT NULL 
            AND codigo_vendedor != ''
            AND codigo_vendedor != '0'
            ORDER BY n_documento, ID
        ''')
        
        # Indexar por n_documento + fecha + sucursal (para match exacto)
        mysql_data = {}  # {(n_documento, fecha, sucursal): codigo_vendedor}
        mysql_data_simple = {}  # {n_documento: codigo_vendedor} fallback
        
        count = 0
        for row in cursor:
            count += 1
            n_doc = row['n_documento']
            codigo = str(row['codigo_vendedor'])
            fecha = str(row['fecha']) if row['fecha'] else ''
            sucursal = row['sucursal'] or ''
            
            # Índice completo
            key_full = (n_doc, fecha, sucursal)
            if key_full not in mysql_data:
                mysql_data[key_full] = codigo
            
            # Índice simple (fallback)
            if n_doc not in mysql_data_simple:
                mysql_data_simple[n_doc] = codigo
        
        cursor.close()
        conn.close()
        
        self.stdout.write(f'   Ventas procesadas: {count:,}')
        self.stdout.write(f'   Indices completos: {len(mysql_data):,}')
        self.stdout.write(f'   Indices simples: {len(mysql_data_simple):,}')
        
        return {'full': mysql_data, 'simple': mysql_data_simple}

    def asignar_vendedores(self, mysql_data, cache_vendedores):
        """Asigna vendedores a DTEs"""
        self.stdout.write('\n[3/3] Asignando vendedores a DTEs...')
        
        # Cargar DTEs sin vendedor con su sucursal
        with connection.cursor() as c:
            c.execute('''
                SELECT d.id, d.numero_documento, d.fecha_emision, s.direccion
                FROM app_dte d
                LEFT JOIN app_sucursal s ON d.sucursal_id = s.id
                WHERE d.vendedor_id IS NULL
            ''')
            dtes = c.fetchall()
        
        total = len(dtes)
        self.stdout.write(f'   DTEs sin vendedor: {total:,}')
        
        updates = []  # (dte_id, vendedor_id)
        sin_codigo = 0
        codigo_no_existe = 0
        codigos_faltantes = set()
        
        for idx, (dte_id, numero_doc, fecha_emision, sucursal_dir) in enumerate(dtes, 1):
            if idx % 50000 == 0:
                sys.stdout.write(f'\r   {self.progress_bar(idx, total, extra=f"{len(updates):,} matches")}')
                sys.stdout.flush()
            
            # Buscar codigo_vendedor en MySQL
            fecha_str = str(fecha_emision) if fecha_emision else ''
            sucursal_str = sucursal_dir or ''
            
            # Primero buscar por clave completa
            key_full = (numero_doc, fecha_str, sucursal_str)
            codigo = mysql_data['full'].get(key_full)
            
            # Si no, buscar solo por numero
            if not codigo:
                codigo = mysql_data['simple'].get(numero_doc)
            
            if not codigo:
                sin_codigo += 1
                continue
            
            # Buscar vendedor en PostgreSQL
            vendedor_id = cache_vendedores.get(codigo)
            
            if not vendedor_id:
                codigo_no_existe += 1
                codigos_faltantes.add(codigo)
                continue
            
            updates.append((dte_id, vendedor_id))
        
        sys.stdout.write(f'\r   {self.progress_bar(total, total, extra=f"{len(updates):,} matches")}\n')
        
        self.stdout.write(f'\n   Resumen:')
        self.stdout.write(self.style.SUCCESS(f'      Para actualizar: {len(updates):,}'))
        self.stdout.write(f'      Sin codigo en MySQL: {sin_codigo:,}')
        self.stdout.write(f'      Codigo no existe en PG: {codigo_no_existe:,}')
        
        if codigos_faltantes:
            self.stdout.write(f'      Codigos faltantes: {sorted(codigos_faltantes)[:20]}')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING(f'\n[DRY-RUN] Se asignarian {len(updates):,} vendedores'))
            return
        
        if not updates:
            self.stdout.write(self.style.WARNING('\n[!] No hay DTEs para actualizar'))
            return
        
        # Ejecutar updates en batches
        self.stdout.write(f'\n   Ejecutando {len(updates):,} updates...')
        
        actualizados = 0
        total_updates = len(updates)
        
        with connection.cursor() as c:
            for i in range(0, total_updates, self.batch_size):
                batch = updates[i:i + self.batch_size]
                
                values_list = ', '.join([f'({dte_id}, {vend_id})' for dte_id, vend_id in batch])
                
                c.execute(f'''
                    UPDATE app_dte AS d
                    SET vendedor_id = v.vendedor_id
                    FROM (VALUES {values_list}) AS v(dte_id, vendedor_id)
                    WHERE d.id = v.dte_id
                ''')
                
                actualizados += c.rowcount
                
                progress = min(i + self.batch_size, total_updates)
                sys.stdout.write(f'\r   {self.progress_bar(progress, total_updates, extra=f"{actualizados:,} OK")}')
                sys.stdout.flush()
        
        sys.stdout.write('\n')
        
        # Verificar resultado
        with connection.cursor() as c:
            c.execute('SELECT COUNT(*) FROM app_dte WHERE vendedor_id IS NOT NULL')
            con_vendedor = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM app_dte WHERE vendedor_id IS NULL')
            sin_vendedor = c.fetchone()[0]
        
        self.stdout.write(f'\n   Estado final:')
        self.stdout.write(self.style.SUCCESS(f'      Con vendedor: {con_vendedor:,}'))
        self.stdout.write(f'      Sin vendedor: {sin_vendedor:,}')
