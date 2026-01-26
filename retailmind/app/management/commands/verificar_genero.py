"""
Comando de management para verificar y agregar opciones al atributo Género
"""
from django.core.management.base import BaseCommand
from app.models import Productos_Atributos, AtributoOpcion


class Command(BaseCommand):
    help = 'Verifica y agrega opciones al atributo Género si no existen'

    def handle(self, *args, **options):
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('🔍 VERIFICANDO ATRIBUTO GÉNERO'))
        self.stdout.write('=' * 60)

        # Verificar si existe el atributo Género
        try:
            genero_attr = Productos_Atributos.objects.get(nombre='Género')
            self.stdout.write(self.style.SUCCESS(f'✅ Atributo Género encontrado (ID: {genero_attr.id})'))
            
            # Contar opciones actuales
            opciones_existentes = AtributoOpcion.objects.filter(atributo=genero_attr)
            count_existentes = opciones_existentes.count()
            
            self.stdout.write(f'\n📊 Opciones actuales: {count_existentes}')
            
            if count_existentes > 0:
                self.stdout.write('\nOpciones existentes:')
                for opcion in opciones_existentes:
                    self.stdout.write(f'  • {opcion.valor} (ID: {opcion.id})')
            else:
                self.stdout.write(self.style.WARNING('⚠️  No hay opciones configuradas'))
            
            # Opciones predefinidas de género
            opciones_genero = [
                'Hombre',
                'Mujer',
                'Unisex',
                'Niño',
                'Niña',
            ]
            
            # Agregar opciones faltantes
            self.stdout.write('\n' + '─' * 60)
            self.stdout.write('🔧 Agregando opciones faltantes...\n')
            
            opciones_agregadas = 0
            for valor in opciones_genero:
                opcion, created = AtributoOpcion.objects.get_or_create(
                    atributo=genero_attr,
                    valor=valor
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(f'✅ Opción agregada: {valor}'))
                    opciones_agregadas += 1
                else:
                    self.stdout.write(f'   Ya existe: {valor}')
            
            # Resumen final
            self.stdout.write('\n' + '=' * 60)
            if opciones_agregadas > 0:
                self.stdout.write(self.style.SUCCESS(f'✅ Se agregaron {opciones_agregadas} opciones nuevas'))
            else:
                self.stdout.write(self.style.SUCCESS('✅ Todas las opciones ya estaban configuradas'))
            
            # Verificación final
            opciones_finales = AtributoOpcion.objects.filter(atributo=genero_attr).count()
            self.stdout.write(f'📊 Total de opciones ahora: {opciones_finales}')
            self.stdout.write('=' * 60)
            
        except Productos_Atributos.DoesNotExist:
            self.stdout.write(self.style.ERROR('❌ El atributo Género no existe en la base de datos'))
            self.stdout.write('\n🔧 Intentando crear el atributo Género...\n')
            
            # Crear el atributo Género
            genero_attr = Productos_Atributos.objects.create(
                nombre='Género',
                descripcion='Género del producto'
            )
            self.stdout.write(self.style.SUCCESS(f'✅ Atributo Género creado (ID: {genero_attr.id})'))
            
            # Agregar las opciones
            opciones_genero = ['Hombre', 'Mujer', 'Unisex', 'Niño', 'Niña']
            for valor in opciones_genero:
                AtributoOpcion.objects.create(
                    atributo=genero_attr,
                    valor=valor
                )
                self.stdout.write(self.style.SUCCESS(f'✅ Opción creada: {valor}'))
            
            self.stdout.write('\n' + '=' * 60)
            self.stdout.write(self.style.SUCCESS('✅ Atributo Género configurado completamente'))
            self.stdout.write('=' * 60)
