"""
Consolida DTEs duplicados: transfiere pagos y elimina duplicados
"""
import os
import sys
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Consolida DTEs duplicados'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write('=' * 70)
        self.stdout.write('CONSOLIDAR DTEs DUPLICADOS')
        self.stdout.write('=' * 70)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN]'))
        
        with connection.cursor() as c:
            # Paso 1: Crear tabla temporal con mapeo de duplicados
            self.stdout.write('\n[1/4] Creando mapeo de duplicados...')
            c.execute('''
                CREATE TEMP TABLE dte_mapping AS
                SELECT 
                    d.id as dte_id_viejo,
                    (SELECT MIN(d2.id) FROM app_dte d2 
                     WHERE d2.numero_documento = d.numero_documento 
                     AND d2.tipo_documento = d.tipo_documento) as dte_id_nuevo
                FROM app_dte d
                WHERE EXISTS (
                    SELECT 1 FROM app_dte d2
                    WHERE d2.numero_documento = d.numero_documento
                    AND d2.tipo_documento = d.tipo_documento
                    AND d2.id < d.id
                )
            ''')
            c.execute('SELECT COUNT(*) FROM dte_mapping')
            total_duplicados = c.fetchone()[0]
            self.stdout.write(f'   DTEs duplicados encontrados: {total_duplicados:,}')
            
            if total_duplicados == 0:
                self.stdout.write(self.style.SUCCESS('\n[OK] No hay duplicados'))
                return
            
            # Paso 2: Transferir pagos
            self.stdout.write('\n[2/4] Transfiriendo pagos...')
            if not dry_run:
                c.execute('''
                    UPDATE app_dte_detalle_pago p
                    SET dte_id = m.dte_id_nuevo
                    FROM dte_mapping m
                    WHERE p.dte_id = m.dte_id_viejo
                ''')
                pagos_movidos = c.rowcount
            else:
                c.execute('''
                    SELECT COUNT(*) FROM app_dte_detalle_pago p
                    JOIN dte_mapping m ON p.dte_id = m.dte_id_viejo
                ''')
                pagos_movidos = c.fetchone()[0]
            self.stdout.write(f'   Pagos transferidos: {pagos_movidos:,}')
            
            # Paso 3: Transferir productos DTE
            self.stdout.write('\n[3/4] Transfiriendo productos...')
            if not dry_run:
                # Primero eliminar productos duplicados que ya existen
                c.execute('''
                    DELETE FROM app_dte_productos dp
                    WHERE dp.dte_id IN (SELECT dte_id_viejo FROM dte_mapping)
                ''')
                productos_eliminados = c.rowcount
                self.stdout.write(f'   Productos de duplicados eliminados: {productos_eliminados:,}')
            
            # Paso 4: Eliminar DTEs duplicados
            self.stdout.write('\n[4/4] Eliminando DTEs duplicados...')
            if not dry_run:
                c.execute('''
                    DELETE FROM app_dte
                    WHERE id IN (SELECT dte_id_viejo FROM dte_mapping)
                ''')
                dtes_eliminados = c.rowcount
            else:
                dtes_eliminados = total_duplicados
            self.stdout.write(f'   DTEs eliminados: {dtes_eliminados:,}')
            
            # Limpiar
            c.execute('DROP TABLE IF EXISTS dte_mapping')
            
            # Verificar
            self.stdout.write('\n[VERIFICACION]')
            c.execute('SELECT COUNT(*) FROM app_dte')
            total_dtes = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM app_dte_detalle_pago')
            total_pagos = c.fetchone()[0]
            c.execute('SELECT COUNT(DISTINCT dte_id) FROM app_dte_detalle_pago')
            dtes_con_pago = c.fetchone()[0]
            
            self.stdout.write(f'   DTEs totales: {total_dtes:,}')
            self.stdout.write(f'   Pagos totales: {total_pagos:,}')
            self.stdout.write(f'   DTEs con pagos: {dtes_con_pago:,}')
            
            # Verificar si aun hay duplicados
            c.execute('''
                SELECT COUNT(*) FROM (
                    SELECT numero_documento, tipo_documento
                    FROM app_dte
                    GROUP BY numero_documento, tipo_documento
                    HAVING COUNT(*) > 1
                ) sub
            ''')
            duplicados_restantes = c.fetchone()[0]
            
            if duplicados_restantes > 0:
                self.stdout.write(self.style.WARNING(f'   Duplicados restantes: {duplicados_restantes}'))
            else:
                self.stdout.write(self.style.SUCCESS('   [OK] Sin duplicados'))
        
        self.stdout.write(self.style.SUCCESS('\n[COMPLETADO]'))
