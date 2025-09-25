from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import (
    Empresa, Sucursal, Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla
)

class Command(BaseCommand):
    help = 'Inicializa datos básicos para el sistema de emisión de DTE'

    def handle(self, *args, **options):
        self.stdout.write('Inicializando datos para emisión de DTE...')
        
        with transaction.atomic():
            # 1. Crear empresa cliente de ejemplo
            cliente, created = Empresa.objects.get_or_create(
                rut='12345678-9',
                defaults={
                    'nombre': 'Cliente Ejemplo S.A.',
                    'nombre_fantasia': 'Cliente Ejemplo',
                    'razon_social': 'Cliente Ejemplo Sociedad Anónima',
                    'giro': 'Comercio al por menor',
                    'direccion': 'Av. Providencia 1234',
                    'comuna': 'Providencia',
                    'ciudad': 'Santiago',
                    'esProveedor': False,
                    'correoVendedor': 'ventas@clienteejemplo.cl',
                    'correoIntercambio': 'intercambio@clienteejemplo.cl',
                    'correoAdministrador': 'admin@clienteejemplo.cl'
                }
            )
            
            if created:
                self.stdout.write(f'✓ Cliente creado: {cliente.nombre}')
            else:
                self.stdout.write(f'✓ Cliente ya existe: {cliente.nombre}')

            # 2. Crear atributos si no existen
            atributo_marca, created = Productos_Atributos.objects.get_or_create(
                nombre='Marca',
                defaults={'descripcion': 'Marca del producto'}
            )
            
            atributo_color, created = Productos_Atributos.objects.get_or_create(
                nombre='Color',
                defaults={'descripcion': 'Color del producto'}
            )
            
            atributo_genero, created = Productos_Atributos.objects.get_or_create(
                nombre='Género',
                defaults={'descripcion': 'Género del producto'}
            )

            # 3. Crear opciones de atributos
            marcas = ['Nike', 'Adidas', 'Puma', 'Reebok', 'Converse']
            for marca_nombre in marcas:
                marca, created = AtributoOpcion.objects.get_or_create(
                    atributo=atributo_marca,
                    valor=marca_nombre
                )
                if created:
                    self.stdout.write(f'✓ Marca creada: {marca_nombre}')

            colores = ['Negro', 'Blanco', 'Azul', 'Rojo', 'Verde', 'Gris']
            for color_nombre in colores:
                color, created = AtributoOpcion.objects.get_or_create(
                    atributo=atributo_color,
                    valor=color_nombre
                )
                if created:
                    self.stdout.write(f'✓ Color creado: {color_nombre}')

            generos = ['Hombre', 'Mujer', 'Unisex', 'Niño', 'Niña']
            for genero_nombre in generos:
                genero, created = AtributoOpcion.objects.get_or_create(
                    atributo=atributo_genero,
                    valor=genero_nombre
                )
                if created:
                    self.stdout.write(f'✓ Género creado: {genero_nombre}')

            # 4. Crear categorías
            categorias = [
                'Zapatillas Deportivas',
                'Zapatillas Casuales',
                'Botas',
                'Sandalias',
                'Zapatos Formales'
            ]
            
            for cat_nombre in categorias:
                categoria, created = Categoria.objects.get_or_create(
                    nombre=cat_nombre
                )
                if created:
                    self.stdout.write(f'✓ Categoría creada: {cat_nombre}')

            # 5. Obtener sucursal (asumiendo que existe al menos una)
            sucursal = Sucursal.objects.first()
            if not sucursal:
                self.stdout.write(self.style.ERROR('No hay sucursales disponibles. Crea una sucursal primero.'))
                return

            # 6. Crear productos de ejemplo
            productos_ejemplo = [
                {
                    'articulo': 'NK-AIR-001',
                    'descripcion': 'Nike Air Max 270 - Zapatilla deportiva',
                    'marca': 'Nike',
                    'color': 'Negro',
                    'genero': 'Hombre',
                    'categoria': 'Zapatillas Deportivas',
                    'costo': 45000,
                    'sobreprecio': 15000,
                    'precioventa': 65000,
                    'tallas': [
                        {'talla': '39', 'stock': 5},
                        {'talla': '40', 'stock': 8},
                        {'talla': '41', 'stock': 12},
                        {'talla': '42', 'stock': 10},
                        {'talla': '43', 'stock': 6},
                        {'talla': '44', 'stock': 4}
                    ]
                },
                {
                    'articulo': 'AD-UB-002',
                    'descripcion': 'Adidas Ultraboost 22 - Running',
                    'marca': 'Adidas',
                    'color': 'Blanco',
                    'genero': 'Mujer',
                    'categoria': 'Zapatillas Deportivas',
                    'costo': 50000,
                    'sobreprecio': 20000,
                    'precioventa': 75000,
                    'tallas': [
                        {'talla': '36', 'stock': 4},
                        {'talla': '37', 'stock': 7},
                        {'talla': '38', 'stock': 9},
                        {'talla': '39', 'stock': 8},
                        {'talla': '40', 'stock': 5}
                    ]
                },
                {
                    'articulo': 'PM-RS-003',
                    'descripcion': 'Puma RS-X3 - Lifestyle',
                    'marca': 'Puma',
                    'color': 'Azul',
                    'genero': 'Unisex',
                    'categoria': 'Zapatillas Casuales',
                    'costo': 35000,
                    'sobreprecio': 10000,
                    'precioventa': 48000,
                    'tallas': [
                        {'talla': '38', 'stock': 6},
                        {'talla': '39', 'stock': 8},
                        {'talla': '40', 'stock': 10},
                        {'talla': '41', 'stock': 7},
                        {'talla': '42', 'stock': 5}
                    ]
                },
                {
                    'articulo': 'CV-CT-004',
                    'descripcion': 'Converse Chuck Taylor All Star',
                    'marca': 'Converse',
                    'color': 'Rojo',
                    'genero': 'Unisex',
                    'categoria': 'Zapatillas Casuales',
                    'costo': 25000,
                    'sobreprecio': 8000,
                    'precioventa': 35000,
                    'tallas': [
                        {'talla': '37', 'stock': 3},
                        {'talla': '38', 'stock': 5},
                        {'talla': '39', 'stock': 7},
                        {'talla': '40', 'stock': 6},
                        {'talla': '41', 'stock': 4},
                        {'talla': '42', 'stock': 3}
                    ]
                }
            ]

            for prod_data in productos_ejemplo:
                # Verificar si el producto ya existe
                if Producto.objects.filter(articulo=prod_data['articulo']).exists():
                    self.stdout.write(f'✓ Producto ya existe: {prod_data["articulo"]}')
                    continue

                # Obtener objetos relacionados
                marca_obj = AtributoOpcion.objects.get(
                    atributo=atributo_marca, 
                    valor=prod_data['marca']
                )
                color_obj = AtributoOpcion.objects.get(
                    atributo=atributo_color, 
                    valor=prod_data['color']
                )
                genero_obj = AtributoOpcion.objects.get(
                    atributo=atributo_genero, 
                    valor=prod_data['genero']
                )
                categoria_obj = Categoria.objects.get(nombre=prod_data['categoria'])

                # Crear producto
                producto = Producto.objects.create(
                    articulo=prod_data['articulo'],
                    descripcion=prod_data['descripcion'],
                    atributo1=marca_obj,
                    atributo2=color_obj,
                    atributo3=genero_obj,
                    categoria=categoria_obj,
                    sucursal=sucursal,
                    costo=prod_data['costo'],
                    sobreprecio=prod_data['sobreprecio'],
                    precioventa=prod_data['precioventa'],
                    tipo_talla='CL'
                )

                # Crear tallas
                for talla_data in prod_data['tallas']:
                    # Generar SKU único
                    sku = int(f"{producto.id}{talla_data['talla'].replace('.', '')}")
                    
                    Producto_Talla.objects.create(
                        producto=producto,
                        sku=sku,
                        stock=talla_data['stock'],
                        talla=talla_data['talla']
                    )

                self.stdout.write(f'✓ Producto creado: {prod_data["articulo"]} con {len(prod_data["tallas"])} tallas')

        self.stdout.write(
            self.style.SUCCESS(
                '\n🎉 ¡Datos inicializados correctamente!\n'
                'Ahora puedes probar el sistema de emisión de DTE con:\n'
                '- 1 Cliente de ejemplo\n'
                f'- {len(productos_ejemplo)} Productos con tallas y stock\n'
                '- Marcas, colores, géneros y categorías\n'
            )
        )
