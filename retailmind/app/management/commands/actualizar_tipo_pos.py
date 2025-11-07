"""
Comando para actualizar el tipo de terminales POS en la base de datos
python manage.py actualizar_tipo_pos
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from app.models import ConfiguracionPOS


class Command(BaseCommand):
    help = 'Actualiza el tipo de terminales POS configurados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tipo-actual',
            type=str,
            default='VERIFONE_VX520',
            help='Tipo actual de los terminales a actualizar'
        )
        parser.add_argument(
            '--tipo-nuevo',
            type=str,
            choices=['VERIFONE_VX520', 'INGENICO_3500', 'INGENICO_DESK', 'OTRO'],
            required=True,
            help='Nuevo tipo de terminal a asignar'
        )
        parser.add_argument(
            '--terminal-id',
            type=int,
            help='ID específico de terminal a actualizar (opcional)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular cambios sin aplicarlos'
        )

    def handle(self, *args, **options):
        tipo_actual = options['tipo_actual']
        tipo_nuevo = options['tipo_nuevo']
        terminal_id = options.get('terminal_id')
        dry_run = options['dry_run']

        self.stdout.write(self.style.WARNING('='*60))
        self.stdout.write(self.style.WARNING('  ACTUALIZACIÓN DE TIPO DE TERMINALES POS'))
        self.stdout.write(self.style.WARNING('='*60))
        
        # Construir query
        if terminal_id:
            terminales = ConfiguracionPOS.objects.filter(id=terminal_id)
            self.stdout.write(f'\n📍 Filtrando terminal ID: {terminal_id}')
        else:
            terminales = ConfiguracionPOS.objects.filter(tipo_pos=tipo_actual)
            self.stdout.write(f'\n📍 Filtrando terminales con tipo: {tipo_actual}')
        
        count = terminales.count()
        
        if count == 0:
            self.stdout.write(self.style.WARNING('\n⚠️  No se encontraron terminales para actualizar'))
            return
        
        # Mostrar terminales que se actualizarán
        self.stdout.write(f'\n✅ Se encontraron {count} terminal(es) para actualizar:\n')
        
        for terminal in terminales:
            self.stdout.write(f'   • ID: {terminal.id} | Nombre: {terminal.nombre} | Tipo actual: {terminal.tipo_pos}')
        
        self.stdout.write(f'\n📝 Tipo nuevo a asignar: {tipo_nuevo}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 MODO DRY-RUN - No se aplicarán cambios'))
            self.stdout.write(self.style.SUCCESS(f'\n✅ Se actualizarían {count} terminal(es)'))
            return
        
        # Confirmar
        self.stdout.write(self.style.WARNING(f'\n⚠️  ¿Desea continuar con la actualización? (s/n): '))
        confirmacion = input()
        
        if confirmacion.lower() not in ['s', 'si', 'y', 'yes']:
            self.stdout.write(self.style.ERROR('\n❌ Actualización cancelada'))
            return
        
        # Actualizar
        self.stdout.write('\n🔄 Actualizando terminales...')
        
        actualizados = terminales.update(tipo_pos=tipo_nuevo)
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Se actualizaron {actualizados} terminal(es) exitosamente'))
        
        # Mostrar resultado
        self.stdout.write('\n📊 RESUMEN DE CAMBIOS:')
        self.stdout.write(f'   Tipo anterior: {tipo_actual}')
        self.stdout.write(f'   Tipo nuevo: {tipo_nuevo}')
        self.stdout.write(f'   Terminales actualizados: {actualizados}')
        
        self.stdout.write(self.style.SUCCESS('\n✅ ¡Actualización completada!'))
        self.stdout.write(self.style.WARNING('='*60))


class Command(BaseCommand):
    help = 'Actualiza el tipo de terminales POS - Versión interactiva'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('\n'+'='*70))
        self.stdout.write(self.style.WARNING('  🔧 ACTUALIZACIÓN DE TIPO DE TERMINALES POS'))
        self.stdout.write(self.style.WARNING('='*70))
        
        # Listar terminales actuales
        terminales = ConfiguracionPOS.objects.all()
        
        if not terminales.exists():
            self.stdout.write(self.style.ERROR('\n❌ No hay terminales configurados en la base de datos'))
            return
        
        self.stdout.write(f'\n📋 TERMINALES CONFIGURADOS: {terminales.count()}\n')
        
        for i, terminal in enumerate(terminales, 1):
            activo = '✅' if terminal.activo else '❌'
            principal = '⭐' if terminal.es_principal else '  '
            
            self.stdout.write(
                f'{i}. {principal} {activo} '
                f'ID:{terminal.id} | {terminal.nombre} | '
                f'Tipo: {terminal.get_tipo_pos_display()} | '
                f'Puerto: {terminal.puerto_conexion} | '
                f'Sucursal: {terminal.sucursal.alias}'
            )
        
        # Menú de opciones
        self.stdout.write('\n' + '='*70)
        self.stdout.write('📝 OPCIONES DE ACTUALIZACIÓN:')
        self.stdout.write('='*70)
        self.stdout.write('1. Actualizar todos los terminales')
        self.stdout.write('2. Actualizar un terminal específico')
        self.stdout.write('3. Actualizar por tipo actual')
        self.stdout.write('0. Salir')
        self.stdout.write('='*70)
        
        opcion = input('\n👉 Seleccione una opción (0-3): ').strip()
        
        if opcion == '0':
            self.stdout.write(self.style.WARNING('\n👋 Saliendo...'))
            return
        
        # Mostrar tipos disponibles
        self.stdout.write('\n📦 TIPOS DE TERMINAL DISPONIBLES:')
        self.stdout.write('='*70)
        self.stdout.write('1. VERIFONE_VX520  → Verifone VX520')
        self.stdout.write('2. INGENICO_3500   → Ingenico 3500')
        self.stdout.write('3. INGENICO_DESK   → Ingenico DESK')
        self.stdout.write('4. OTRO            → Otro tipo')
        self.stdout.write('='*70)
        
        tipo_opcion = input('\n👉 Seleccione el NUEVO tipo (1-4): ').strip()
        
        tipos_map = {
            '1': 'VERIFONE_VX520',
            '2': 'INGENICO_3500',
            '3': 'INGENICO_DESK',
            '4': 'OTRO'
        }
        
        if tipo_opcion not in tipos_map:
            self.stdout.write(self.style.ERROR('\n❌ Opción inválida'))
            return
        
        tipo_nuevo = tipos_map[tipo_opcion]
        
        # Aplicar actualización según opción
        if opcion == '1':
            # Actualizar todos
            self._actualizar_todos(tipo_nuevo)
        elif opcion == '2':
            # Actualizar uno específico
            terminal_id = input('\n👉 Ingrese el ID del terminal: ').strip()
            try:
                terminal_id = int(terminal_id)
                self._actualizar_especifico(terminal_id, tipo_nuevo)
            except ValueError:
                self.stdout.write(self.style.ERROR('\n❌ ID inválido'))
        elif opcion == '3':
            # Actualizar por tipo
            self._actualizar_por_tipo(tipo_nuevo)
        else:
            self.stdout.write(self.style.ERROR('\n❌ Opción inválida'))

    def _actualizar_todos(self, tipo_nuevo):
        """Actualizar todos los terminales"""
        terminales = ConfiguracionPOS.objects.all()
        count = terminales.count()
        
        self.stdout.write(self.style.WARNING(f'\n⚠️  Se actualizarán TODOS los {count} terminal(es) a tipo: {tipo_nuevo}'))
        confirmacion = input('¿Está seguro? (si/no): ').strip().lower()
        
        if confirmacion in ['si', 's', 'yes', 'y']:
            actualizados = terminales.update(tipo_pos=tipo_nuevo)
            self.stdout.write(self.style.SUCCESS(f'\n✅ {actualizados} terminal(es) actualizados exitosamente'))
        else:
            self.stdout.write(self.style.WARNING('\n❌ Actualización cancelada'))

    def _actualizar_especifico(self, terminal_id, tipo_nuevo):
        """Actualizar un terminal específico"""
        try:
            terminal = ConfiguracionPOS.objects.get(id=terminal_id)
            
            self.stdout.write(f'\n📍 Terminal seleccionado:')
            self.stdout.write(f'   Nombre: {terminal.nombre}')
            self.stdout.write(f'   Tipo actual: {terminal.get_tipo_pos_display()}')
            self.stdout.write(f'   Tipo nuevo: {tipo_nuevo}')
            
            confirmacion = input('\n¿Confirmar actualización? (si/no): ').strip().lower()
            
            if confirmacion in ['si', 's', 'yes', 'y']:
                terminal.tipo_pos = tipo_nuevo
                terminal.save()
                self.stdout.write(self.style.SUCCESS(f'\n✅ Terminal actualizado exitosamente'))
            else:
                self.stdout.write(self.style.WARNING('\n❌ Actualización cancelada'))
                
        except ConfiguracionPOS.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'\n❌ No existe terminal con ID: {terminal_id}'))

    def _actualizar_por_tipo(self, tipo_nuevo):
        """Actualizar terminales de un tipo específico"""
        self.stdout.write('\n📦 Tipos actuales en la BD:')
        tipos_actuales = ConfiguracionPOS.objects.values_list('tipo_pos', flat=True).distinct()
        
        for i, tipo in enumerate(tipos_actuales, 1):
            count = ConfiguracionPOS.objects.filter(tipo_pos=tipo).count()
            self.stdout.write(f'{i}. {tipo} ({count} terminal/es)')
        
        tipo_seleccionado = input('\n👉 Seleccione el tipo ACTUAL a reemplazar (copiar/pegar): ').strip()
        
        terminales = ConfiguracionPOS.objects.filter(tipo_pos=tipo_seleccionado)
        count = terminales.count()
        
        if count == 0:
            self.stdout.write(self.style.ERROR(f'\n❌ No hay terminales con tipo: {tipo_seleccionado}'))
            return
        
        self.stdout.write(self.style.WARNING(f'\n⚠️  Se actualizarán {count} terminal(es)'))
        self.stdout.write(f'   De: {tipo_seleccionado}')
        self.stdout.write(f'   A:  {tipo_nuevo}')
        
        confirmacion = input('\n¿Confirmar? (si/no): ').strip().lower()
        
        if confirmacion in ['si', 's', 'yes', 'y']:
            actualizados = terminales.update(tipo_pos=tipo_nuevo)
            self.stdout.write(self.style.SUCCESS(f'\n✅ {actualizados} terminal(es) actualizados'))
        else:
            self.stdout.write(self.style.WARNING('\n❌ Cancelado'))

