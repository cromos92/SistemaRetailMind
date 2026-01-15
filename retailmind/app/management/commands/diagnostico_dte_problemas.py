"""
Diagnóstico de DTEs sin sucursal - Analiza por qué no se asignaron
"""
import os
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection

from app.models import Sucursal, Empresa

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Diagnóstico de DTEs sin sucursal'

    def handle(self, *args, **options):
        self.stdout.write('=' * 70)
        self.stdout.write('DIAGNÓSTICO DE DTEs SIN SUCURSAL')
        self.stdout.write('=' * 70)
        
        # 1. Estado actual
        self.mostrar_estado()
        
        # 2. Cargar datos
        sucursales = self.cargar_sucursales()
        mysql_data = self.cargar_mysql()
        
        # 3. Analizar problemas
        self.analizar_problemas(sucursales, mysql_data)

    def mostrar_estado(self):
        self.stdout.write(f'\n📊 ESTADO ACTUAL:')
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NULL')
            sin_suc = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM app_dte WHERE sucursal_id IS NOT NULL')
            con_suc = cursor.fetchone()[0]
        
        self.stdout.write(f'   ⚠️  Sin sucursal: {sin_suc:,}')
        self.stdout.write(f'   ✅ Con sucursal: {con_suc:,}')

    def cargar_sucursales(self):
        self.stdout.write(f'\n📦 SUCURSALES EN POSTGRESQL:')
        sucursales = {}
        
        for s in Sucursal.objects.select_related('empresa').all():
            if s.alias not in sucursales:
                sucursales[s.alias] = {}
            sucursales[s.alias][s.empresa_id] = s.id
            self.stdout.write(f'   {s.alias}: empresa_id={s.empresa_id} ({s.empresa.razon_social if s.empresa else "N/A"})')
        
        return sucursales

    def cargar_mysql(self):
        self.stdout.write(f'\n🔌 Conectando a MySQL...')
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT 
                    n_documento,
                    COALESCE(bodega_inicio, bodega_destino) as bodega,
                    rut_emisor
                FROM dte
                WHERE n_documento IS NOT NULL 
                  AND n_documento > 0
            """)
            
            mysql_data = {}
            for row in cursor.fetchall():
                n_doc = row['n_documento']
                if n_doc and n_doc not in mysql_data:
                    mysql_data[n_doc] = {
                        'bodega': row['bodega'],
                        'rut_emisor': row['rut_emisor']
                    }
            
            cursor.close()
            conn.close()
            
            self.stdout.write(self.style.SUCCESS(f'   ✓ {len(mysql_data):,} DTEs cargados'))
            return mysql_data
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ✗ Error: {e}'))
            return {}

    def analizar_problemas(self, sucursales, mysql_data):
        self.stdout.write(f'\n🔍 ANALIZANDO DTEs SIN SUCURSAL...')
        
        # Cargar empresas para mostrar info
        empresas = {e.id: e for e in Empresa.objects.all()}
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, numero_documento, emisor_id
                FROM app_dte
                WHERE sucursal_id IS NULL
                LIMIT 50000
            """)
            dtes_sin_suc = cursor.fetchall()
        
        self.stdout.write(f'   📊 Analizando {len(dtes_sin_suc):,} DTEs sin sucursal...')
        
        alias_no_existe = Counter()
        empresa_no_coincide = Counter()
        sin_bodega = 0
        sin_match = 0
        
        for dte_id, numero_doc, emisor_id in dtes_sin_suc:
            mysql_info = mysql_data.get(numero_doc)
            
            if not mysql_info:
                sin_match += 1
                continue
            
            alias = mysql_info['bodega']
            
            if not alias:
                sin_bodega += 1
                continue
            
            if alias not in sucursales:
                alias_no_existe[alias] += 1
                continue
            
            # Alias existe pero empresa no coincide
            if emisor_id not in sucursales[alias]:
                empresa_nombre = empresas[emisor_id].razon_social if emisor_id in empresas else f'ID:{emisor_id}'
                empresa_no_coincide[(alias, emisor_id, empresa_nombre)] += 1
        
        # Mostrar resultados
        self.stdout.write(f'\n📋 ALIAS QUE NO EXISTEN EN POSTGRESQL ({len(alias_no_existe)} únicos):')
        for alias, count in alias_no_existe.most_common(20):
            self.stdout.write(self.style.WARNING(f'   ⚠️  "{alias}": {count:,} DTEs'))
        
        self.stdout.write(f'\n📋 EMPRESA NO COINCIDE CON ALIAS ({len(empresa_no_coincide)} combinaciones):')
        self.stdout.write(f'   (El alias existe pero no está asociado a esa empresa)')
        for (alias, emp_id, emp_nombre), count in empresa_no_coincide.most_common(30):
            self.stdout.write(self.style.WARNING(f'   ⚠️  Alias="{alias}" + Empresa={emp_id} ({emp_nombre}): {count:,} DTEs'))
        
        self.stdout.write(f'\n📊 RESUMEN:')
        self.stdout.write(f'   Sin match MySQL: {sin_match:,}')
        self.stdout.write(f'   Sin bodega: {sin_bodega:,}')
        self.stdout.write(f'   Alias no existe: {sum(alias_no_existe.values()):,}')
        self.stdout.write(f'   Empresa no coincide: {sum(empresa_no_coincide.values()):,}')
        
        # Sugerencia
        if empresa_no_coincide:
            self.stdout.write(f'\n💡 POSIBLE SOLUCIÓN:')
            self.stdout.write(f'   Algunos alias pueden estar asociados a otra empresa.')
            self.stdout.write(f'   Revisa si las sucursales deberían tener múltiples empresas.')
