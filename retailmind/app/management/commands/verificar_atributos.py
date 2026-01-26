"""
Comando para verificar y crear atributos del sistema (Marca, Color, Genero)
"""
from django.core.management.base import BaseCommand
from app.models import Productos_Atributos, AtributoOpcion


class Command(BaseCommand):
    help = 'Verifica y crea atributos del sistema si no existen'

    def handle(self, *args, **options):
        self.stdout.write('=' * 70)
        self.stdout.write('VERIFICACION DE ATRIBUTOS DEL SISTEMA')
        self.stdout.write('=' * 70)
        
        # Definir atributos y sus opciones
        atributos_config = {
            'Marca': {
                'descripcion': 'Marca del producto',
                'opciones': ['Nike', 'Adidas', 'Puma', 'Reebok', 'Converse', 'Vans']
            },
            'Color': {
                'descripcion': 'Color del producto',
                'opciones': ['Negro', 'Blanco', 'Rojo', 'Azul', 'Verde', 'Amarillo', 'Gris', 'Rosa', 'Cafe', 'Beige']
            },
            'Género': {
                'descripcion': 'Género del producto',
                'opciones': ['Hombre', 'Mujer', 'Unisex', 'Niño', 'Niña']
            }
        }
        
        self.stdout.write('\n')
        
        # Verificar cada atributo
        for nombre_atributo, config in atributos_config.items():
            self.stdout.write('-' * 70)
            self.stdout.write(f'\nVerificando atributo: {nombre_atributo}')
            
            # Obtener o crear el atributo
            atributo, created = Productos_Atributos.objects.get_or_create(
                nombre=nombre_atributo,
                defaults={'descripcion': config['descripcion']}
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'  [OK] Atributo "{nombre_atributo}" creado (ID: {atributo.id})'))
            else:
                self.stdout.write(f'  [EXISTE] Atributo "{nombre_atributo}" encontrado (ID: {atributo.id})')
            
            # Verificar opciones
            opciones_existentes = AtributoOpcion.objects.filter(atributo=atributo)
            count_existentes = opciones_existentes.count()
            
            self.stdout.write(f'  Opciones actuales: {count_existentes}')
            
            if count_existentes > 0:
                self.stdout.write('  Opciones existentes:')
                for opcion in opciones_existentes[:10]:  # Mostrar máximo 10
                    self.stdout.write(f'    - {opcion.valor} (ID: {opcion.id})')
                if count_existentes > 10:
                    self.stdout.write(f'    ... y {count_existentes - 10} mas')
            
            # Agregar opciones predefinidas si no hay ninguna
            if count_existentes == 0:
                self.stdout.write(self.style.WARNING(f'  [ATENCION] No hay opciones para "{nombre_atributo}"'))
                self.stdout.write(f'  Agregando opciones predefinidas...')
                
                for valor in config['opciones']:
                    AtributoOpcion.objects.create(
                        atributo=atributo,
                        valor=valor
                    )
                    self.stdout.write(self.style.SUCCESS(f'    + {valor}'))
                
                nuevas_opciones = AtributoOpcion.objects.filter(atributo=atributo).count()
                self.stdout.write(self.style.SUCCESS(f'  [OK] {nuevas_opciones} opciones agregadas'))
            
            self.stdout.write('')
        
        # Resumen final
        self.stdout.write('=' * 70)
        self.stdout.write('RESUMEN')
        self.stdout.write('=' * 70)
        
        for nombre_atributo in atributos_config.keys():
            try:
                atributo = Productos_Atributos.objects.get(nombre=nombre_atributo)
                count = AtributoOpcion.objects.filter(atributo=atributo).count()
                self.stdout.write(self.style.SUCCESS(f'  {nombre_atributo:15s} : {count} opciones'))
            except Productos_Atributos.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'  {nombre_atributo:15s} : NO EXISTE'))
        
        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS('\nVerificacion completada!'))
        self.stdout.write('')
