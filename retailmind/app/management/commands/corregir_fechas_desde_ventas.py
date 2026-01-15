"""
Django management command para corregir fechas de DTEs desde tabla ventas de MySQL.
Para DTEs que no matchearon con la tabla dte.

Uso:
    python manage.py corregir_fechas_desde_ventas --dry-run
    python manage.py corregir_fechas_desde_ventas
"""

import os
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection

from app.models import Dte


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Corrige fechas de DTEs desde tabla ventas de MySQL'

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

        self.corregir_fechas()
        
        self.mysql_conn.close()
        self.stdout.write(self.style.SUCCESS('\n[COMPLETADO] Corrección de fechas finalizada'))

    def corregir_fechas(self):
        """Corrige fechas de DTEs desde ventas MySQL"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('[FECHAS] CORRIGIENDO FECHAS DESDE VENTAS...')
        self.stdout.write('=' * 70)

        # Obtener fechas de ventas MySQL (agrupado por n_documento, sub_total)
        cursor = self.mysql_conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT n_documento, sub_total, MIN(fecha) as fecha
            FROM ventas
            GROUP BY n_documento, sub_total
        ''')
        
        # Crear índice: (n_documento, monto) -> fecha
        self.stdout.write('  [+] Cargando fechas de ventas MySQL...')
        fechas_ventas = {}
        for row in cursor:
            key = (row['n_documento'], int(row['sub_total'] or 0))
            fechas_ventas[key] = row['fecha']
        
        self.stdout.write(f'  [OK] {len(fechas_ventas):,} fechas cargadas')
        cursor.close()

        # Obtener DTEs con fecha 2026-01-07 (fecha incorrecta de migración)
        self.stdout.write('  [+] Buscando DTEs con fecha incorrecta (2026-01-07)...')
        
        dtes_incorrectos = Dte.objects.filter(fecha_emision='2026-01-07')
        total_incorrectos = dtes_incorrectos.count()
        self.stdout.write(f'  [i] DTEs con fecha 2026-01-07: {total_incorrectos:,}')

        # Comparar y preparar actualizaciones
        actualizaciones = []
        sin_match = 0
        
        for dte in dtes_incorrectos.iterator():
            key = (dte.numero_documento, int(dte.monto_con_iva or 0))
            fecha_correcta = fechas_ventas.get(key)
            
            if fecha_correcta and str(fecha_correcta) != '2026-01-07':
                actualizaciones.append((dte.id, fecha_correcta))
            else:
                sin_match += 1
        
        self.stdout.write(f'  [i] DTEs a actualizar: {len(actualizaciones):,}')
        self.stdout.write(f'  [i] Sin match en ventas: {sin_match:,}')

        if not actualizaciones:
            self.stdout.write(self.style.SUCCESS('\n  [OK] No hay fechas que corregir'))
            return

        if self.dry_run:
            # Mostrar algunos ejemplos
            self.stdout.write('\n  Ejemplos de correcciones:')
            for dte_id, fecha in actualizaciones[:5]:
                dte = Dte.objects.get(id=dte_id)
                self.stdout.write(f'    DTE {dte.numero_documento}: 2026-01-07 -> {fecha}')
            return

        # Actualizar en batch
        self.stdout.write('\n  [+] Actualizando fechas...')
        
        with connection.cursor() as pg_cursor:
            count = 0
            for i in range(0, len(actualizaciones), self.batch_size):
                batch = actualizaciones[i:i + self.batch_size]
                
                # Construir UPDATE con CASE
                cases = []
                ids = []
                for dte_id, fecha in batch:
                    cases.append(f"WHEN {dte_id} THEN '{fecha}'::date")
                    ids.append(str(dte_id))
                
                sql = f'''
                    UPDATE app_dte 
                    SET fecha_emision = CASE id {' '.join(cases)} END,
                        fecha_vencimiento = CASE id {' '.join(cases)} END
                    WHERE id IN ({','.join(ids)})
                '''
                
                pg_cursor.execute(sql)
                count += len(batch)
                self.stdout.write(f'  Actualizados: {count:,}/{len(actualizaciones):,}...')

        self.stdout.write(self.style.SUCCESS(f'\n  [OK] Fechas corregidas: {len(actualizaciones):,}'))
