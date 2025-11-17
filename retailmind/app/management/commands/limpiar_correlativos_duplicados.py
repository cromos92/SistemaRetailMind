"""
Comando de gestión para limpiar correlativos duplicados
Ejecutar con: python manage.py limpiar_correlativos_duplicados
"""

from django.core.management.base import BaseCommand
from django.db.models import Count
from app.models import Correlativo


class Command(BaseCommand):
    help = 'Limpia correlativos duplicados manteniendo el más reciente'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se eliminaría sin hacer cambios reales',
        )
        parser.add_argument(
            '--auto-fix',
            action='store_true',
            help='Elimina automáticamente los duplicados sin pedir confirmación',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        auto_fix = options['auto_fix']
        
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('DETECCIÓN DE CORRELATIVOS DUPLICADOS'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write('')
        
        # Encontrar duplicados
        duplicados = Correlativo.objects.values(
            'sucursal_id', 'tipo_dte'
        ).annotate(
            count=Count('id')
        ).filter(count__gt=1)
        
        if not duplicados:
            self.stdout.write(self.style.SUCCESS('✅ No se encontraron correlativos duplicados'))
            return
        
        self.stdout.write(self.style.ERROR(f'❌ Se encontraron {len(duplicados)} grupos de correlativos duplicados:'))
        self.stdout.write('')
        
        total_eliminados = 0
        
        for dup in duplicados:
            sucursal_id = dup['sucursal_id']
            tipo_dte = dup['tipo_dte']
            count = dup['count']
            
            # Obtener todos los correlativos duplicados
            correlativos_dup = Correlativo.objects.filter(
                sucursal_id=sucursal_id,
                tipo_dte=tipo_dte
            ).order_by('-id')  # El más reciente primero
            
            self.stdout.write(f'  📋 Sucursal ID: {sucursal_id} | Tipo DTE: {tipo_dte} | Total: {count}')
            
            # Mostrar todos los registros
            for i, corr in enumerate(correlativos_dup):
                estado = "🔵 MANTENER" if i == 0 else "🔴 ELIMINAR"
                self.stdout.write(
                    f'      {estado} - ID: {corr.id} | '
                    f'Inicio: {corr.inicio} | Termino: {corr.termino} | '
                    f'Disponibles: {corr.disponibles} | '
                    f'Alias: {corr.alias}'
                )
            
            # Eliminar duplicados (todos excepto el primero/más reciente)
            if not dry_run:
                correlativos_a_eliminar = correlativos_dup[1:]  # Todos excepto el primero
                
                if auto_fix:
                    confirmar = 'yes'
                else:
                    confirmar = input(f'\n  ¿Eliminar {len(correlativos_a_eliminar)} duplicado(s) para {tipo_dte} en sucursal {sucursal_id}? (yes/no): ')
                
                if confirmar.lower() in ['yes', 'y', 'si', 's']:
                    for corr in correlativos_a_eliminar:
                        self.stdout.write(f'      Eliminando ID: {corr.id}...')
                        corr.delete()
                        total_eliminados += 1
                    self.stdout.write(self.style.SUCCESS(f'      ✅ Eliminados {len(correlativos_a_eliminar)} duplicado(s)'))
                else:
                    self.stdout.write(self.style.WARNING('      ⏭️  Omitido'))
            
            self.stdout.write('')
        
        self.stdout.write('=' * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING('ℹ️  Modo DRY-RUN: No se realizaron cambios'))
            self.stdout.write(self.style.WARNING(f'   Se eliminarían {sum(d["count"] - 1 for d in duplicados)} registros'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✅ Proceso completado. Total eliminados: {total_eliminados}'))
        self.stdout.write('=' * 70)
        
        # Resumen final
        duplicados_restantes = Correlativo.objects.values(
            'sucursal_id', 'tipo_dte'
        ).annotate(
            count=Count('id')
        ).filter(count__gt=1).count()
        
        if duplicados_restantes == 0:
            self.stdout.write(self.style.SUCCESS('\n🎉 ¡Base de datos limpia! No hay correlativos duplicados.'))
        else:
            self.stdout.write(self.style.WARNING(f'\n⚠️  Aún quedan {duplicados_restantes} grupos de duplicados.'))



