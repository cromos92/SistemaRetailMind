"""
Script Python para corregir secuencias de PostgreSQL después de migración de base de datos.

Este script puede ejecutarse de dos formas:
1. Como script standalone: python fix_sequences_django.py
2. Como management command de Django (después de copiarlo a management/commands/)

Fecha: 2026-01-26
"""

import psycopg2
from psycopg2 import sql
import sys
import os
from datetime import datetime

# Si se ejecuta como script standalone, configurar Django
if __name__ == "__main__":
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
    django.setup()

from django.db import connection
from django.core.management.base import BaseCommand


def fix_sequences():
    """
    Corrige todas las secuencias de PostgreSQL en el schema público.
    """
    print("=" * 80)
    print("CORRECCIÓN DE SECUENCIAS POSTGRESQL")
    print("=" * 80)
    print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    sequences_fixed = 0
    sequences_ok = 0
    sequences_error = 0
    total_sequences = 0
    
    results = []
    
    with connection.cursor() as cursor:
        # Obtener todas las secuencias
        query = """
            SELECT 
                s.sequencename,
                s.schemaname,
                c.relname as tablename,
                a.attname as columnname
            FROM pg_sequences s
            JOIN pg_class seq_class ON seq_class.relname = s.sequencename
            JOIN pg_depend d ON d.objid = seq_class.oid
            JOIN pg_class c ON c.oid = d.refobjid
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
            WHERE s.schemaname = 'public'
            ORDER BY c.relname, a.attname
        """
        
        cursor.execute(query)
        sequences = cursor.fetchall()
        total_sequences = len(sequences)
        
        print(f"Total de secuencias encontradas: {total_sequences}")
        print()
        print("-" * 80)
        
        for seq_name, schema, table_name, column_name in sequences:
            try:
                # Obtener el valor máximo de la columna
                cursor.execute(
                    sql.SQL("SELECT COALESCE(MAX({column}), 0) FROM {schema}.{table}").format(
                        column=sql.Identifier(column_name),
                        schema=sql.Identifier(schema),
                        table=sql.Identifier(table_name)
                    )
                )
                max_id = cursor.fetchone()[0]
                
                # Obtener el valor actual de la secuencia
                cursor.execute(
                    sql.SQL("SELECT last_value FROM {schema}.{sequence}").format(
                        schema=sql.Identifier(schema),
                        sequence=sql.Identifier(seq_name)
                    )
                )
                current_val = cursor.fetchone()[0]
                
                # Calcular el nuevo valor
                new_val = max_id + 1
                
                # Solo ajustar si es necesario
                if current_val <= max_id:
                    # Ajustar la secuencia
                    cursor.execute(
                        sql.SQL("SELECT setval({seq}, %s, false)").format(
                            seq=sql.Literal(f"{schema}.{seq_name}")
                        ),
                        [new_val]
                    )
                    
                    status = "✓ CORREGIDO"
                    sequences_fixed += 1
                    print(f"[OK] {seq_name}")
                    print(f"     Tabla: {table_name}.{column_name}")
                    print(f"     Anterior: {current_val} -> Nuevo: {new_val} (Max ID: {max_id})")
                    
                    results.append({
                        'tabla': table_name,
                        'columna': column_name,
                        'secuencia': seq_name,
                        'anterior': current_val,
                        'nuevo': new_val,
                        'max_id': max_id,
                        'estado': 'CORREGIDO'
                    })
                else:
                    status = "✓ OK"
                    sequences_ok += 1
                    print(f"[SKIP] {seq_name}")
                    print(f"       Tabla: {table_name}.{column_name}")
                    print(f"       Ya está correcto (Current: {current_val}, Max ID: {max_id})")
                    
                    results.append({
                        'tabla': table_name,
                        'columna': column_name,
                        'secuencia': seq_name,
                        'anterior': current_val,
                        'nuevo': current_val,
                        'max_id': max_id,
                        'estado': 'OK'
                    })
                
                print("-" * 80)
                
            except Exception as e:
                sequences_error += 1
                print(f"[ERROR] {seq_name}")
                print(f"        Tabla: {table_name}.{column_name}")
                print(f"        Error: {str(e)}")
                print("-" * 80)
                
                results.append({
                    'tabla': table_name,
                    'columna': column_name,
                    'secuencia': seq_name,
                    'anterior': None,
                    'nuevo': None,
                    'max_id': None,
                    'estado': f'ERROR: {str(e)}'
                })
    
    # Resumen
    print()
    print("=" * 80)
    print("RESUMEN DE CORRECCIÓN")
    print("=" * 80)
    print(f"Total de secuencias procesadas: {total_sequences}")
    print(f"Secuencias corregidas:          {sequences_fixed}")
    print(f"Secuencias ya correctas:        {sequences_ok}")
    print(f"Secuencias con errores:         {sequences_error}")
    print()
    
    # Mostrar tabla de resultados
    if sequences_fixed > 0:
        print("SECUENCIAS CORREGIDAS:")
        print("-" * 80)
        for r in results:
            if r['estado'] == 'CORREGIDO':
                print(f"  {r['tabla']}.{r['columna']}")
                print(f"    Secuencia: {r['secuencia']}")
                print(f"    {r['anterior']} -> {r['nuevo']}")
        print()
    
    if sequences_error > 0:
        print("SECUENCIAS CON ERRORES:")
        print("-" * 80)
        for r in results:
            if r['estado'].startswith('ERROR'):
                print(f"  {r['tabla']}.{r['columna']}")
                print(f"    Secuencia: {r['secuencia']}")
                print(f"    Error: {r['estado']}")
        print()
    
    return sequences_fixed, sequences_ok, sequences_error


class Command(BaseCommand):
    """
    Django Management Command para corregir secuencias.
    
    Uso: python manage.py fix_sequences
    """
    help = 'Corrige todas las secuencias de PostgreSQL después de migración'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simula la corrección sin aplicar cambios',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('Modo DRY-RUN: No se aplicarán cambios'))
            return
        
        try:
            fixed, ok, errors = fix_sequences()
            
            if errors > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'Proceso completado con {errors} errores. '
                        f'Revise el log anterior.'
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Proceso completado exitosamente. '
                        f'{fixed} secuencias corregidas, {ok} ya estaban correctas.'
                    )
                )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error fatal: {str(e)}')
            )
            raise


if __name__ == "__main__":
    """
    Ejecución standalone del script.
    """
    print()
    print("Ejecutando corrección de secuencias...")
    print()
    
    try:
        fixed, ok, errors = fix_sequences()
        
        if errors > 0:
            print("⚠ Proceso completado con errores.")
            sys.exit(1)
        else:
            print("✓ Proceso completado exitosamente.")
            sys.exit(0)
            
    except Exception as e:
        print(f"✗ Error fatal: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
