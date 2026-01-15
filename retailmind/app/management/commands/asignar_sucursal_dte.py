"""
Asigna sucursales a DTEs basándose SOLO en el alias (bodega)
Ignora la verificación de empresa para resolver ventas cruzadas
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

from app.models import Sucursal

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Asigna sucursales a DTEs por alias (ignora empresa)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=5000)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        
        self.stdout.write('=' * 70)
        self.stdout.write('ASIGNAR SUCURSALES A DTEs (POR ALIAS)')
        self.stdout.write('=' * 70)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('\n⚠️ MODO DRY-RUN'))
        
        inicio = datetime.now()
        
        # 1. Cargar sucursales (solo alias -> sucursal_id, primera que encuentre)
        sucursales = self.cargar_sucursales()
        
        # 2. Cargar datos MySQL
        mysql_data = self.cargar_mysql()
        
        # 3. Asignar sucursales
        self.asignar_sucursales(mysql_data, sucursales)
        
        tiempo = (datetime.now() - inicio).total_seconds()
        self.stdout.write(self.style.SUCCESS(f'\n✅ Completado en {tiempo:.1f} segundos'))

    def progress_bar(self, current, total, width=40, extra=''):
        percent = current / total if total > 0 else 0
        filled = int(width * percent)
        bar = '█' * filled + '░' * (width - filled)
        extra_str = f' | {extra}' if extra else ''
        return f'[{bar}] {percent*100:.1f}% ({current:,}/{total:,}){extra_str}'

    def cargar_sucursales(self):
        """Carga sucursales: {alias: sucursal_id} - primera sucursal por alias"""
        self.stdout.write(f'\n📦 Cargando sucursales...')
        sucursales = {}
        
        for s in Sucursal.objects.all():
            # Solo guardar la primera sucursal para cada alias
            if s.alias not in sucursales:
                sucursales[s.alias] = s.id
                self.stdout.write(f'   {s.alias} -> sucursal_id={s.id}')
        
        self.stdout.write(self.style.SUCCESS(f'   ✓ {len(sucursales)} alias cargados'))
        return sucursales

    def cargar_mysql(self):
        """Carga DTEs de MySQL: {n_documento: bodega}"""
        self.stdout.write(f'\n🔌 Conectando a MySQL...')
        
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT COUNT(*) as total FROM dte WHERE n_documento > 0")
            total = cursor.fetchone()['total']
            self.stdout.write(f'   📊 Total DTEs: {total:,}')
            
            cursor.execute("""
                SELECT n_documento, COALESCE(bodega_inicio, bodega_destino) as bodega
                FROM dte
                WHERE n_documento IS NOT NULL AND n_documento > 0
            """)
            
            mysql_data = {}
            count = 0
            
            for row in cursor:
                count += 1
                if count % 50000 == 0:
                    sys.stdout.write(f'\r   {self.progress_bar(count, total)}')
                    sys.stdout.flush()
                
                n_doc = row['n_documento']
                bodega = row['bodega']
                if n_doc and bodega and n_doc not in mysql_data:
                    mysql_data[n_doc] = bodega
            
            sys.stdout.write(f'\r   {self.progress_bar(count, total)}\n')
            
            cursor.close()
            conn.close()
            
            self.stdout.write(self.style.SUCCESS(f'   ✓ {len(mysql_data):,} DTEs con bodega'))
            return mysql_data
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error: {e}'))
            return {}

    def asignar_sucursales(self, mysql_data, sucursales):
        """Asigna sucursales a DTEs sin sucursal"""
        self.stdout.write(f'\n📥 Cargando DTEs sin sucursal...')
        
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL")
            total_sin = cursor.fetchone()[0]
            self.stdout.write(f'   📊 DTEs sin sucursal: {total_sin:,}')
            
            cursor.execute("""
                SELECT id, numero_documento
                FROM app_dte
                WHERE sucursal_id IS NULL
            """)
            dtes_pg = cursor.fetchall()
        
        total = len(dtes_pg)
        self.stdout.write(f'   ✓ {total:,} DTEs cargados')
        
        self.stdout.write(f'\n🔄 Procesando...')
        
        updates = []  # (dte_id, sucursal_id)
        sin_match = 0
        alias_no_existe = 0
        
        for idx, (dte_id, numero_doc) in enumerate(dtes_pg, 1):
            if idx % 10000 == 0 or idx == total:
                sys.stdout.write(f'\r   {self.progress_bar(idx, total, extra=f"{len(updates):,} matches")}')
                sys.stdout.flush()
            
            # Buscar bodega en MySQL
            bodega = mysql_data.get(numero_doc)
            
            if not bodega:
                sin_match += 1
                continue
            
            # Buscar sucursal por alias
            sucursal_id = sucursales.get(bodega)
            
            if not sucursal_id:
                alias_no_existe += 1
                continue
            
            updates.append((dte_id, sucursal_id))
        
        sys.stdout.write('\n')
        
        self.stdout.write(f'\n📊 RESUMEN:')
        self.stdout.write(self.style.SUCCESS(f'   ✅ Para actualizar: {len(updates):,}'))
        self.stdout.write(f'   ⚠️  Sin match MySQL: {sin_match:,}')
        self.stdout.write(f'   ⚠️  Alias no existe: {alias_no_existe:,}')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING(f'\n🔍 DRY-RUN: Se asignarían {len(updates):,} sucursales'))
            return
        
        if not updates:
            self.stdout.write(self.style.WARNING('\n⚠️ No hay DTEs para actualizar'))
            return
        
        # Ejecutar updates
        self.stdout.write(f'\n💾 Ejecutando {len(updates):,} updates...')
        
        actualizados = 0
        total_updates = len(updates)
        
        with connection.cursor() as cursor:
            for i in range(0, total_updates, self.batch_size):
                batch = updates[i:i + self.batch_size]
                
                values_list = ', '.join([f"({dte_id}, {suc_id})" for dte_id, suc_id in batch])
                
                sql = f"""
                    UPDATE app_dte AS d
                    SET sucursal_id = v.sucursal_id
                    FROM (VALUES {values_list}) AS v(dte_id, sucursal_id)
                    WHERE d.id = v.dte_id
                """
                
                try:
                    cursor.execute(sql)
                    actualizados += cursor.rowcount
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'\n   ✗ Error: {e}'))
                    continue
                
                processed = min(i + self.batch_size, total_updates)
                sys.stdout.write(f'\r   {self.progress_bar(processed, total_updates, extra=f"{actualizados:,} OK")}')
                sys.stdout.flush()
        
        sys.stdout.write('\n')
        
        # Estado final
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL')
            sin_suc = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NOT NULL')
            con_suc = cursor.fetchone()[0]
        
        self.stdout.write(f'\n📊 ESTADO FINAL:')
        self.stdout.write(f'   ⚠️  Sin sucursal: {sin_suc:,}')
        self.stdout.write(self.style.SUCCESS(f'   ✅ Con sucursal: {con_suc:,}'))
        self.stdout.write(self.style.SUCCESS(f'   ✅ Actualizados: {actualizados:,}'))
