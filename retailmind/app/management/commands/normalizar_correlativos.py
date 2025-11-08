"""
Comando de gestión para normalizar tipos de documentos en correlativos
Ejecutar con: python manage.py normalizar_correlativos
"""

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from app.models import Correlativo


class Command(BaseCommand):
    help = 'Normaliza los tipos de documentos en correlativos, eliminando duplicados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se normalizaría sin hacer cambios reales',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('NORMALIZACIÓN DE TIPOS DE DOCUMENTOS EN CORRELATIVOS'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write('')
        
        # Mapeo de normalizaciones
        normalizaciones = {
            'Compra': 'COMPRA',
            'Ticket': 'TICKET',
            'Traspaso': 'TRASPASO',
            'Ajuste': 'AJUSTE',
            'compra': 'COMPRA',
            'ticket': 'TICKET',
            'traspaso': 'TRASPASO',
            'ajuste': 'AJUSTE',
        }
        
        # Buscar correlativos con tipos no normalizados
        correlativos_no_normalizados = Correlativo.objects.filter(
            tipo_dte__in=normalizaciones.keys()
        )
        
        total_encontrados = correlativos_no_normalizados.count()
        
        if total_encontrados == 0:
            self.stdout.write(self.style.SUCCESS('✅ Todos los correlativos ya están normalizados'))
            return
        
        self.stdout.write(self.style.WARNING(
            f'📋 Se encontraron {total_encontrados} correlativos con tipos no normalizados:'
        ))
        self.stdout.write('')
        
        total_normalizados = 0
        total_eliminados = 0
        total_errores = 0
        
        for correlativo in correlativos_no_normalizados:
            tipo_original = correlativo.tipo_dte
            tipo_normalizado = normalizaciones.get(tipo_original)
            
            if not tipo_normalizado:
                continue
            
            self.stdout.write(
                f'  🔄 ID: {correlativo.id} | '
                f'Sucursal: {correlativo.sucursal.alias} | '
                f'Tipo: "{tipo_original}" → "{tipo_normalizado}"'
            )
            
            if not dry_run:
                # Verificar si ya existe un correlativo con el tipo normalizado
                existe_normalizado = Correlativo.objects.filter(
                    sucursal_id=correlativo.sucursal_id,
                    tipo_dte=tipo_normalizado
                ).exclude(id=correlativo.id).exists()
                
                if existe_normalizado:
                    # Ya existe uno normalizado, eliminar este duplicado
                    self.stdout.write(
                        f'      🗑️  Eliminando duplicado (ya existe correlativo con tipo {tipo_normalizado})'
                    )
                    try:
                        correlativo.delete()
                        total_eliminados += 1
                        self.stdout.write(self.style.SUCCESS('      ✅ Eliminado'))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'      ❌ Error al eliminar: {str(e)}'))
                        total_errores += 1
                else:
                    # No existe, normalizar
                    correlativo.tipo_dte = tipo_normalizado
                    try:
                        correlativo.save()
                        total_normalizados += 1
                        self.stdout.write(self.style.SUCCESS('      ✅ Normalizado'))
                    except IntegrityError as e:
                        # Si hay error de integridad, intentar eliminar
                        self.stdout.write(
                            f'      ⚠️  Error de integridad: {str(e)}'
                        )
                        self.stdout.write('      🗑️  Eliminando correlativo problemático...')
                        try:
                            correlativo.delete()
                            total_eliminados += 1
                            self.stdout.write(self.style.SUCCESS('      ✅ Eliminado'))
                        except Exception as e2:
                            self.stdout.write(self.style.ERROR(f'      ❌ Error al eliminar: {str(e2)}'))
                            total_errores += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'      ❌ Error: {str(e)}'))
                        total_errores += 1
            
            self.stdout.write('')
        
        self.stdout.write('=' * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING('ℹ️  Modo DRY-RUN: No se realizaron cambios'))
            self.stdout.write(self.style.WARNING(f'   Se normalizarían {total_encontrados} registros'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✅ Proceso completado:'))
            self.stdout.write(f'   • Correlativos normalizados: {total_normalizados}')
            self.stdout.write(f'   • Duplicados eliminados: {total_eliminados}')
            if total_errores > 0:
                self.stdout.write(self.style.ERROR(f'   • Errores: {total_errores}'))
        self.stdout.write('=' * 70)
        
        # Verificación final
        if not dry_run:
            correlativos_pendientes = Correlativo.objects.filter(
                tipo_dte__in=normalizaciones.keys()
            ).count()
            
            if correlativos_pendientes == 0:
                self.stdout.write(self.style.SUCCESS('\n🎉 ¡Todos los correlativos han sido normalizados!'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'\n⚠️  Aún quedan {correlativos_pendientes} correlativos sin normalizar.'
                ))

