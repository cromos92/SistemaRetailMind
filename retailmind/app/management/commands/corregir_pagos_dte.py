"""
Elimina todos los pagos y los vuelve a migrar correctamente
usando la lógica de numero + fecha + sucursal
"""
import os
import sys
from django.core.management.base import BaseCommand
from django.db import connection

from app.models import Dte_Detalle_Pago


class Command(BaseCommand):
    help = 'Elimina pagos existentes para volver a migrar'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--confirm', action='store_true', help='Confirmar eliminacion')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        confirm = options['confirm']
        
        self.stdout.write('=' * 70)
        self.stdout.write('LIMPIAR PAGOS PARA RE-MIGRACION')
        self.stdout.write('=' * 70)
        
        with connection.cursor() as c:
            c.execute('SELECT COUNT(*) FROM app_dte_detalle_pago')
            total = c.fetchone()[0]
            
            self.stdout.write(f'\nPagos existentes: {total:,}')
            
            if dry_run:
                self.stdout.write(self.style.WARNING('\n[DRY-RUN] Se eliminarian todos los pagos'))
                return
            
            if not confirm:
                self.stdout.write(self.style.WARNING('\n[!] Usa --confirm para ejecutar'))
                self.stdout.write('    Comando: python manage.py corregir_pagos_dte --confirm')
                return
            
            self.stdout.write('\n[1/2] Eliminando pagos...')
            c.execute('DELETE FROM app_dte_detalle_pago')
            eliminados = c.rowcount
            self.stdout.write(self.style.SUCCESS(f'   Eliminados: {eliminados:,}'))
            
            self.stdout.write('\n[2/2] Verificando...')
            c.execute('SELECT COUNT(*) FROM app_dte_detalle_pago')
            restantes = c.fetchone()[0]
            self.stdout.write(f'   Pagos restantes: {restantes}')
            
            self.stdout.write(self.style.SUCCESS('\n[OK] Ahora ejecuta:'))
            self.stdout.write('   python manage.py migrar_pagos_dte')
