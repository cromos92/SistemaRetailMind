from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import F
from app.models import Correlativo, Sucursal, TIPO_DOCUMENTO_CHOICES

class Command(BaseCommand):
    help = 'Inicializa correlativos de ejemplo para el sistema'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Fuerza la creación incluso si ya existen correlativos',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Inicializando correlativos del sistema...'))
        
        # Obtener todas las sucursales
        sucursales = Sucursal.objects.all()
        
        if not sucursales.exists():
            self.stdout.write(
                self.style.ERROR('❌ No hay sucursales registradas. Crea al menos una sucursal primero.')
            )
            return
        
        # Tipos de documento más comunes
        tipos_principales = [
            'FACTURA ELECTRONICA',
            'BOLETA ELECTRONICA', 
            'GUIA',
            'NOTA DE CREDITO',
            'NOTA DE DEBITO',
            'TICKET'
        ]
        
        correlativos_creados = 0
        correlativos_existentes = 0
        
        for sucursal in sucursales:
            self.stdout.write(f'\n📍 Procesando sucursal: {sucursal.nombre}')
            
            for tipo_documento in tipos_principales:
                # Verificar si ya existe
                correlativo_existente = Correlativo.objects.filter(
                    sucursal=sucursal,
                    tipo_dte=tipo_documento
                ).first()
                
                if correlativo_existente and not options['force']:
                    self.stdout.write(
                        f'   ⚠️  Ya existe correlativo para {tipo_documento}'
                    )
                    correlativos_existentes += 1
                    continue
                
                # Definir rangos según el tipo de documento
                if tipo_documento == 'TICKET':
                    inicio = 1
                    termino = 999999
                elif tipo_documento in ['FACTURA ELECTRONICA', 'BOLETA ELECTRONICA']:
                    inicio = 1
                    termino = 100000
                elif tipo_documento == 'GUIA':
                    inicio = 1
                    termino = 50000
                else:
                    inicio = 1
                    termino = 10000
                
                # Crear o actualizar correlativo
                if correlativo_existente and options['force']:
                    correlativo_existente.inicio = inicio
                    correlativo_existente.termino = termino
                    correlativo_existente.alias = f'{tipo_documento}_{sucursal.alias}'
                    correlativo_existente.responsable = 'Sistema'
                    correlativo_existente.fecha_actualizacion = timezone.now().date()
                    correlativo_existente.save()
                    
                    self.stdout.write(
                        f'   🔄 Actualizado: {tipo_documento} ({inicio}-{termino})'
                    )
                else:
                    Correlativo.objects.create(
                        sucursal=sucursal,
                        tipo_dte=tipo_documento,
                        inicio=inicio,
                        termino=termino,
                        alias=f'{tipo_documento}_{sucursal.alias}',
                        responsable='Sistema',
                        fecha_actualizacion=timezone.now().date()
                    )
                    
                    self.stdout.write(
                        f'   ✅ Creado: {tipo_documento} ({inicio}-{termino})'
                    )
                
                correlativos_creados += 1
        
        # Resumen
        self.stdout.write(f'\n📊 RESUMEN:')
        self.stdout.write(f'   • Correlativos creados/actualizados: {correlativos_creados}')
        self.stdout.write(f'   • Correlativos ya existentes: {correlativos_existentes}')
        self.stdout.write(f'   • Sucursales procesadas: {sucursales.count()}')
        
        self.stdout.write(
            self.style.SUCCESS('\n🎉 ¡Inicialización de correlativos completada!')
        )
        
        # Mostrar estadísticas
        total_correlativos = Correlativo.objects.count()
        correlativos_activos = Correlativo.objects.filter(inicio__lt=F('termino')).count()
        
        self.stdout.write(f'\n📈 ESTADÍSTICAS ACTUALES:')
        self.stdout.write(f'   • Total de correlativos: {total_correlativos}')
        self.stdout.write(f'   • Correlativos activos: {correlativos_activos}')
        
        self.stdout.write(
            self.style.SUCCESS('\n🔗 Accede a la gestión de correlativos en: /app/documentos/gestion-correlativos/')
        )
