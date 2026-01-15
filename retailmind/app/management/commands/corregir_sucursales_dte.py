"""
Django management command para corregir sucursales de DTEs desde tabla ventas de MySQL.

Uso:
    python manage.py corregir_sucursales_dte --dry-run
    python manage.py corregir_sucursales_dte
"""

import os
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection

from app.models import Dte, Sucursal


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Corrige sucursales de DTEs desde tabla ventas de MySQL'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo mostrar qué se corregiría')
        parser.add_argument('--batch-size', type=int, default=5000, help='Tamaño del batch')

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] No se guardarán cambios'))
        
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

        self.corregir_sucursales()
        
        self.mysql_conn.close()
        self.stdout.write(self.style.SUCCESS('\n[COMPLETADO] Corrección de sucursales finalizada'))

    def corregir_sucursales(self):
        """Corrige sucursales de DTEs desde ventas MySQL"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('[SUCURSALES] CORRIGIENDO SUCURSALES DE DTEs...')
        self.stdout.write('=' * 70)

        # Cargar sucursales de PostgreSQL
        self.stdout.write('  [+] Cargando sucursales...')
        sucursales_pg = {}
        for suc in Sucursal.objects.all():
            if suc.direccion:
                sucursales_pg[suc.direccion] = suc.id
        self.stdout.write(f'  [OK] {len(sucursales_pg)} sucursales: {list(sucursales_pg.keys())}')

        # Obtener sucursales de ventas MySQL (agrupado por n_documento, sub_total)
        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT n_documento, sub_total, sucursal
            FROM ventas
            GROUP BY n_documento, sub_total, sucursal
        ''')
        
        # Crear índice: (n_documento, monto) -> sucursal
        self.stdout.write('  [+] Cargando sucursales de ventas MySQL...')
        sucursales_ventas = {}
        for row in cursor:
            key = (row['n_documento'], int(row['sub_total'] or 0))
            sucursales_ventas[key] = row['sucursal']
        
        self.stdout.write(f'  [OK] {len(sucursales_ventas):,} registros cargados')
        cursor.close()

        # Comparar y preparar actualizaciones
        self.stdout.write('  [+] Comparando sucursales...')
        
        actualizaciones = []
        sin_match = 0
        ya_correctos = 0
        sucursal_no_existe = 0
        
        for dte in Dte.objects.select_related('sucursal').iterator():
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            sucursal_mysql = sucursales_ventas.get(key)
            
            if not sucursal_mysql:
                sin_match += 1
                continue
            
            # Obtener ID de sucursal en PostgreSQL
            sucursal_id_pg = sucursales_pg.get(sucursal_mysql)
            if not sucursal_id_pg:
                sucursal_no_existe += 1
                continue
            
            # Comparar
            sucursal_actual = dte.sucursal.direccion if dte.sucursal else None
            if sucursal_actual != sucursal_mysql:
                actualizaciones.append((dte.id, sucursal_id_pg))
            else:
                ya_correctos += 1
        
        self.stdout.write(f'  [i] DTEs a actualizar: {len(actualizaciones):,}')
        self.stdout.write(f'  [i] Ya correctos: {ya_correctos:,}')
        self.stdout.write(f'  [i] Sin match en ventas: {sin_match:,}')
        self.stdout.write(f'  [i] Sucursal no existe en PG: {sucursal_no_existe:,}')

        if not actualizaciones:
            self.stdout.write(self.style.SUCCESS('\n  [OK] No hay sucursales que corregir'))
            return

        if self.dry_run:
            # Mostrar algunos ejemplos
            self.stdout.write('\n  Ejemplos de correcciones:')
            for dte_id, suc_id in actualizaciones[:5]:
                dte = Dte.objects.select_related('sucursal').get(id=dte_id)
                suc_nueva = Sucursal.objects.get(id=suc_id)
                suc_actual = dte.sucursal.direccion if dte.sucursal else 'Sin suc'
                self.stdout.write(f'    DTE {dte.numero_documento}: {suc_actual} -> {suc_nueva.direccion}')
            return

        # Actualizar en batch
        self.stdout.write('\n  [+] Actualizando sucursales...')
        
        with connection.cursor() as pg_cursor:
            count = 0
            for i in range(0, len(actualizaciones), self.batch_size):
                batch = actualizaciones[i:i + self.batch_size]
                
                # Construir UPDATE con CASE
                cases = []
                ids = []
                for dte_id, suc_id in batch:
                    cases.append(f"WHEN {dte_id} THEN {suc_id}")
                    ids.append(str(dte_id))
                
                sql = f'''
                    UPDATE app_dte 
                    SET sucursal_id = CASE id {' '.join(cases)} END
                    WHERE id IN ({','.join(ids)})
                '''
                
                pg_cursor.execute(sql)
                count += len(batch)
                self.stdout.write(f'  Actualizados: {count:,}/{len(actualizaciones):,}...')

        self.stdout.write(self.style.SUCCESS(f'\n  [OK] Sucursales corregidas: {len(actualizaciones):,}'))
