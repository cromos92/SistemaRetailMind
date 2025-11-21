"""
Django management command para limpiar/resetear datos migrados desde MySQL

Uso:
    python manage.py clean_migration
    python manage.py clean_migration --all
    python manage.py clean_migration --tables productos producto_talla
    python manage.py clean_migration --confirm
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import (
    Empresa, Sucursal, Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla, Movimientos_Producto, Dte
)


class Command(BaseCommand):
    help = 'Limpia datos migrados desde MySQL Laravel'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Eliminar TODO (empresas, sucursales, productos, movimientos, DTEs)'
        )
        parser.add_argument(
            '--tables',
            nargs='+',
            help='Eliminar solo tablas específicas (ej: --tables productos producto_talla)'
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirmar eliminación sin preguntar'
        )

    def handle(self, *args, **options):
        all_tables = options.get('all', False)
        specific_tables = options.get('tables', None)
        auto_confirm = options.get('confirm', False)

        # Definir qué se va a eliminar
        if all_tables:
            tables_to_clean = [
                'movimientos',
                'producto_talla',
                'productos',
                'categorias',
                'atributos',
                'sucursales',
                'dtes',
                'empresas',
            ]
        elif specific_tables:
            tables_to_clean = specific_tables
        else:
            # Por defecto, solo productos y dependencias
            tables_to_clean = [
                'movimientos',
                'producto_talla',
                'productos',
            ]

        # Mostrar resumen
        self.stdout.write(self.style.WARNING('\n[ADVERTENCIA] Se eliminarán los siguientes datos:'))
        self.stdout.write('='*70)
        
        for table in tables_to_clean:
            count = self.get_count(table)
            self.stdout.write(f'  - {table.upper()}: {count} registros')
        
        self.stdout.write('='*70)

        # Pedir confirmación
        if not auto_confirm:
            confirm = input('\n¿Continuar con la eliminación? (escriba "SI" para confirmar): ')
            if confirm != 'SI':
                self.stdout.write(self.style.ERROR('[CANCELADO] Operación cancelada por el usuario'))
                return

        # Ejecutar eliminación
        try:
            with transaction.atomic():
                for table in tables_to_clean:
                    self.clean_table(table)
                
                self.stdout.write(self.style.SUCCESS('\n[ÉXITO] Limpieza completada'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'[ERROR] {e}'))

    def get_count(self, table_name):
        """Obtiene el conteo de registros de una tabla"""
        counts = {
            'empresas': Empresa.objects.count(),
            'sucursales': Sucursal.objects.count(),
            'categorias': Categoria.objects.count(),
            'atributos': AtributoOpcion.objects.count(),
            'productos': Producto.objects.count(),
            'producto_talla': Producto_Talla.objects.count(),
            'movimientos': Movimientos_Producto.objects.count(),
            'dtes': Dte.objects.count(),
        }
        return counts.get(table_name, 0)

    def clean_table(self, table_name):
        """Elimina registros de una tabla específica"""
        self.stdout.write(f'\n[LIMPIANDO] {table_name.upper()}...')
        
        deleted = 0
        
        if table_name == 'movimientos':
            deleted, _ = Movimientos_Producto.objects.all().delete()
            
        elif table_name == 'producto_talla':
            deleted, _ = Producto_Talla.objects.all().delete()
            
        elif table_name == 'productos':
            deleted, _ = Producto.objects.all().delete()
            
        elif table_name == 'categorias':
            deleted, _ = Categoria.objects.all().delete()
            
        elif table_name == 'atributos':
            # Eliminar AtributoOpciones
            deleted_opciones, _ = AtributoOpcion.objects.all().delete()
            # Eliminar Atributos
            deleted_attrs, _ = Productos_Atributos.objects.all().delete()
            deleted = deleted_opciones + deleted_attrs
            
        elif table_name == 'sucursales':
            deleted, _ = Sucursal.objects.all().delete()
            
        elif table_name == 'empresas':
            # Solo eliminar empresas que NO sean las principales
            deleted, _ = Empresa.objects.exclude(
                rut__in=['78503140-7', '76104936-4']
            ).delete()
            
        elif table_name == 'dtes':
            deleted, _ = Dte.objects.all().delete()
        
        self.stdout.write(self.style.SUCCESS(f'  OK - Eliminados {deleted} registros'))

