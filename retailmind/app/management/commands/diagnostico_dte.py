"""
Diagnóstico para verificar por qué no hacen match los DTEs
"""
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}


class Command(BaseCommand):
    help = 'Diagnóstico de DTEs para verificar match'

    def handle(self, *args, **options):
        self.stdout.write('=' * 70)
        self.stdout.write('DIAGNÓSTICO DE DTEs')
        self.stdout.write('=' * 70)

        # 1. Muestra de PostgreSQL
        self.stdout.write('\n📊 MUESTRA DE POSTGRESQL (10 DTEs sin sucursal):')
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, numero_documento, tipo_documento, fecha_emision::date, emisor_id
                FROM app_dte
                WHERE sucursal_id IS NULL
                ORDER BY fecha_emision DESC
                LIMIT 10
            """)
            pg_samples = cursor.fetchall()
            
            for row in pg_samples:
                self.stdout.write(f'   ID:{row[0]} | num_doc:{row[1]} | tipo:{row[2]} | fecha:{row[3]} | emisor:{row[4]}')
        
        # Obtener un numero_documento para buscar en MySQL
        if pg_samples:
            test_num = pg_samples[0][1]
            test_fecha = str(pg_samples[0][3])
            self.stdout.write(f'\n🔍 Buscando num_doc={test_num} en MySQL...')
        
        # 2. Muestra de MySQL
        self.stdout.write('\n📊 MUESTRA DE MYSQL (10 DTEs):')
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT ID, n_documento, tipo_documento, DATE(fecha_emision) as fecha, 
                       bodega_inicio, bodega_destino, rut_emisor
                FROM dte
                ORDER BY fecha_emision DESC
                LIMIT 10
            """)
            mysql_samples = cursor.fetchall()
            
            for row in mysql_samples:
                self.stdout.write(f'   ID:{row["ID"]} | n_doc:{row["n_documento"]} | tipo:{row["tipo_documento"]} | fecha:{row["fecha"]} | bodega:{row["bodega_inicio"]}')
            
            # 3. Buscar el numero específico
            if pg_samples:
                self.stdout.write(f'\n🔎 Buscando n_documento={test_num} en MySQL:')
                cursor.execute(f"""
                    SELECT ID, n_documento, tipo_documento, DATE(fecha_emision) as fecha,
                           bodega_inicio, bodega_destino
                    FROM dte
                    WHERE n_documento = %s
                """, [test_num])
                
                found = cursor.fetchall()
                if found:
                    for row in found:
                        self.stdout.write(self.style.SUCCESS(f'   ✓ Encontrado: ID:{row["ID"]} fecha:{row["fecha"]} bodega:{row["bodega_inicio"]}'))
                else:
                    self.stdout.write(self.style.WARNING(f'   ✗ NO encontrado en MySQL'))
            
            # 4. Verificar tipos de documento
            self.stdout.write('\n📋 TIPOS DE DOCUMENTO EN MYSQL:')
            cursor.execute("SELECT DISTINCT tipo_documento, COUNT(*) as cnt FROM dte GROUP BY tipo_documento")
            for row in cursor.fetchall():
                self.stdout.write(f'   {row["tipo_documento"]}: {row["cnt"]}')
            
            self.stdout.write('\n📋 TIPOS DE DOCUMENTO EN POSTGRESQL:')
            with connection.cursor() as pg_cursor:
                pg_cursor.execute("SELECT DISTINCT tipo_documento, COUNT(*) as cnt FROM app_dte GROUP BY tipo_documento")
                for row in pg_cursor.fetchall():
                    self.stdout.write(f'   {row[0]}: {row[1]}')
            
            # 5. Verificar rangos de numero_documento
            self.stdout.write('\n📊 RANGOS DE NUMERO_DOCUMENTO:')
            cursor.execute("SELECT MIN(n_documento) as min_n, MAX(n_documento) as max_n FROM dte WHERE n_documento > 0")
            mysql_range = cursor.fetchone()
            self.stdout.write(f'   MySQL: {mysql_range["min_n"]} - {mysql_range["max_n"]}')
            
            with connection.cursor() as pg_cursor:
                pg_cursor.execute("SELECT MIN(numero_documento) as min_n, MAX(numero_documento) as max_n FROM app_dte WHERE numero_documento > 0")
                pg_range = pg_cursor.fetchone()
                self.stdout.write(f'   PostgreSQL: {pg_range[0]} - {pg_range[1]}')
            
            # 6. Verificar overlaps
            self.stdout.write('\n🔗 VERIFICANDO COINCIDENCIAS:')
            with connection.cursor() as pg_cursor:
                pg_cursor.execute("""
                    SELECT numero_documento, fecha_emision::date, COUNT(*)
                    FROM app_dte
                    WHERE sucursal_id IS NULL
                    GROUP BY numero_documento, fecha_emision::date
                    LIMIT 5
                """)
                pg_keys = pg_cursor.fetchall()
                
                for num, fecha, cnt in pg_keys:
                    cursor.execute("""
                        SELECT COUNT(*) as cnt FROM dte 
                        WHERE n_documento = %s AND DATE(fecha_emision) = %s
                    """, [num, str(fecha)])
                    mysql_cnt = cursor.fetchone()['cnt']
                    match_status = '✓' if mysql_cnt > 0 else '✗'
                    self.stdout.write(f'   {match_status} PG(num={num}, fecha={fecha}) -> MySQL matches: {mysql_cnt}')
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error MySQL: {e}'))
