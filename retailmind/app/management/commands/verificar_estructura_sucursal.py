"""
Comando temporal para verificar la estructura de la tabla app_sucursal
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Verifica las columnas de la tabla app_sucursal'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type, character_maximum_length, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'app_sucursal'
                ORDER BY ordinal_position
            """)
            
            self.stdout.write('=' * 80)
            self.stdout.write(self.style.SUCCESS('📊 ESTRUCTURA DE LA TABLA app_sucursal'))
            self.stdout.write('=' * 80)
            
            for row in cursor.fetchall():
                column_name, data_type, max_length, nullable = row
                length_str = f'({max_length})' if max_length else ''
                null_str = 'NULL' if nullable == 'YES' else 'NOT NULL'
                
                self.stdout.write(
                    f'  {column_name:<20} {data_type}{length_str:<15} {null_str}'
                )
            
            self.stdout.write('=' * 80)
            
            # Contar registros
            cursor.execute("SELECT COUNT(*) FROM app_sucursal")
            total = cursor.fetchone()[0]
            self.stdout.write(self.style.SUCCESS(f'\n✅ Total de registros: {total}\n'))

