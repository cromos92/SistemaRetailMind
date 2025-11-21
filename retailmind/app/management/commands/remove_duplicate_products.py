"""
Django management command para eliminar productos duplicados.

Este comando identifica y elimina productos duplicados dejando solo el primero
de cada grupo.

Uso:
    python manage.py remove_duplicate_products
    python manage.py remove_duplicate_products --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from app.models import Producto


class Command(BaseCommand):
    help = 'Elimina productos duplicados dejando solo el primero de cada grupo'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular eliminación sin guardar cambios',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('🧹 ELIMINACIÓN DE PRODUCTOS DUPLICADOS'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write('')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] Modo simulación activado'))
            self.stdout.write('')
        
        # Encontrar duplicados
        self.stdout.write('[PASO 1] Buscando productos duplicados...')
        
        duplicates = Producto.objects.values(
            'articulo', 'sucursal', 'atributo1', 'atributo2'
        ).annotate(
            count=Count('id')
        ).filter(count__gt=1)
        
        total_groups = duplicates.count()
        
        if total_groups == 0:
            self.stdout.write(self.style.SUCCESS('✅ No se encontraron productos duplicados'))
            return
        
        self.stdout.write(f'[INFO] Se encontraron {total_groups} grupos de productos duplicados')
        self.stdout.write('')
        
        # Eliminar duplicados
        self.stdout.write('[PASO 2] Eliminando duplicados...')
        self.stdout.write('')
        
        total_deleted = 0
        
        try:
            if not dry_run:
                with transaction.atomic():
                    total_deleted = self._remove_duplicates(duplicates)
            else:
                total_deleted = self._remove_duplicates(duplicates, simulate=True)
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write(self.style.SUCCESS('📊 RESUMEN'))
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write(f'Grupos de duplicados encontrados: {total_groups}')
            self.stdout.write(f'Productos eliminados: {total_deleted}')
            self.stdout.write('')
            
            if dry_run:
                self.stdout.write(self.style.WARNING('⚠️  Modo DRY-RUN: No se realizaron cambios'))
            else:
                self.stdout.write(self.style.SUCCESS('✅ Duplicados eliminados exitosamente'))
            
            self.stdout.write(self.style.SUCCESS('=' * 70))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error durante la eliminación: {e}'))
            raise

    def _remove_duplicates(self, duplicates, simulate=False):
        """Elimina duplicados dejando solo el primero de cada grupo"""
        total_deleted = 0
        
        for idx, dup in enumerate(duplicates, 1):
            # Obtener todos los productos de este grupo
            productos = Producto.objects.filter(
                articulo=dup['articulo'],
                sucursal_id=dup['sucursal'],
                atributo1_id=dup['atributo1'],
                atributo2_id=dup['atributo2']
            ).order_by('id')
            
            # Mantener el primero, eliminar el resto
            to_keep = productos.first()
            to_delete = productos.exclude(id=to_keep.id)
            
            count = to_delete.count()
            
            if count > 0:
                self.stdout.write(
                    f'  [{idx}/{len(duplicates)}] Artículo {dup["articulo"]}: '
                    f'Manteniendo ID {to_keep.id}, eliminando {count} duplicados'
                )
                
                if not simulate:
                    # Actualizar referencias de producto_talla antes de eliminar
                    for producto in to_delete:
                        # Mover producto_talla al producto que se mantiene
                        producto.producto_talla_set.all().update(producto=to_keep)
                    
                    # Ahora eliminar los productos duplicados
                    to_delete.delete()
                
                total_deleted += count
        
        return total_deleted

