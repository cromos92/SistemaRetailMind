from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import Productos_Atributos, AtributoOpcion, Categoria

class Command(BaseCommand):
    help = 'Inicializa los atributos básicos para productos (Marca, Color, Género)'

    def handle(self, *args, **options):
        self.stdout.write('🚀 Inicializando atributos básicos para productos...')
        
        try:
            with transaction.atomic():
                # Crear atributos básicos si no existen
                atributos_basicos = [
                    {
                        'nombre': 'Marca',
                        'descripcion': 'Marca del producto',
                        'opciones_default': ['Nike', 'Adidas', 'Puma', 'Reebok', 'Converse']
                    },
                    {
                        'nombre': 'Color',
                        'descripcion': 'Color del producto',
                        'opciones_default': ['Negro', 'Blanco', 'Azul', 'Rojo', 'Verde', 'Amarillo', 'Gris', 'Rosa', 'Morado', 'Naranja']
                    },
                    {
                        'nombre': 'Género',
                        'descripcion': 'Género del producto',
                        'opciones_default': ['Hombre', 'Mujer', 'Unisex', 'Niño', 'Niña']
                    }
                ]
                
                for atributo_data in atributos_basicos:
                    # Crear o obtener el atributo
                    atributo, created = Productos_Atributos.objects.get_or_create(
                        nombre__iexact=atributo_data['nombre'],
                        defaults={
                            'nombre': atributo_data['nombre'],
                            'descripcion': atributo_data['descripcion']
                        }
                    )
                    
                    if created:
                        self.stdout.write(
                            self.style.SUCCESS(f'✅ Atributo "{atributo.nombre}" creado correctamente')
                        )
                        
                        # Crear opciones por defecto
                        for opcion_valor in atributo_data['opciones_default']:
                            opcion, opcion_created = AtributoOpcion.objects.get_or_create(
                                atributo=atributo,
                                valor__iexact=opcion_valor,
                                defaults={'valor': opcion_valor}
                            )
                            if opcion_created:
                                self.stdout.write(f'  ➕ Opción "{opcion_valor}" agregada')
                    else:
                        self.stdout.write(
                            self.style.WARNING(f'⚠️ Atributo "{atributo.nombre}" ya existe')
                        )
                        
                        # Verificar si tiene opciones, si no, agregar las por defecto
                        if not atributo.opciones.exists():
                            self.stdout.write(f'  📝 Agregando opciones por defecto para "{atributo.nombre}"...')
                            for opcion_valor in atributo_data['opciones_default']:
                                opcion, opcion_created = AtributoOpcion.objects.get_or_create(
                                    atributo=atributo,
                                    valor__iexact=opcion_valor,
                                    defaults={'valor': opcion_valor}
                                )
                                if opcion_created:
                                    self.stdout.write(f'    ➕ Opción "{opcion_valor}" agregada')
                
                # Crear categorías básicas si no existen
                self.stdout.write('\n📁 Inicializando categorías básicas...')
                categorias_basicas = [
                    'Calzado',
                    'Ropa',
                    'Accesorios',
                    'Deportes',
                    'Casual'
                ]
                
                for categoria_nombre in categorias_basicas:
                    categoria, created = Categoria.objects.get_or_create(
                        nombre__iexact=categoria_nombre,
                        defaults={'nombre': categoria_nombre}
                    )
                    
                    if created:
                        self.stdout.write(
                            self.style.SUCCESS(f'✅ Categoría "{categoria.nombre}" creada correctamente')
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(f'⚠️ Categoría "{categoria.nombre}" ya existe')
                        )
                
                # Mostrar resumen final
                self.stdout.write('\n📊 Resumen de atributos:')
                for atributo in Productos_Atributos.objects.all():
                    opciones_count = atributo.opciones.count()
                    self.stdout.write(f'  • {atributo.nombre}: {opciones_count} opciones')
                
                categorias_count = Categoria.objects.count()
                self.stdout.write(f'  • Categorías: {categorias_count} disponibles')
                
                self.stdout.write(
                    self.style.SUCCESS('\n🎉 ¡Inicialización de atributos y categorías completada exitosamente!')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error durante la inicialización: {str(e)}')
            )
            raise
