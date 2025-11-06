"""
Comando para migrar datos desde MySQL (Vicent) a PostgreSQL (RetailMind)

⚠️ IMPORTANTE:
- En Vicent NO existe concepto de "producto padre", solo variaciones
- Cada registro en tabla "talla" ES una variación individual
- Este comando CREA productos padre agrupando por codigo_asociado
- NO modifica MySQL (solo lectura)
"""

import os
import logging
from decimal import Decimal
from typing import Dict, List, Any
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connections
from django.conf import settings

from app.models import (
    Categoria,
    Productos_Atributos,
    AtributoOpcion,
    ProductoAtributoValor,
    Producto,
    Producto_Talla,
    Sucursal,
    Empresa
)

# Configurar logging
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = """
    Migra productos desde MySQL (Vicent) a PostgreSQL (RetailMind).
    
    En Vicent solo existen variaciones en tabla "talla", NO hay productos padre.
    Este comando CREA el concepto de producto padre agrupando por codigo_asociado.
    
    Uso:
        python manage.py migrate_from_vicent --modo=test
        python manage.py migrate_from_vicent --modo=completo
        python manage.py migrate_from_vicent --modo=solo-estructura
        python manage.py migrate_from_vicent --modo=solo-productos
    """

    def __init__(self):
        super().__init__()
        self.estadisticas = {
            'categorias': 0,
            'atributos': 0,
            'opciones_atributo': 0,
            'bodegas': 0,
            'productos_padre': 0,
            'variaciones': 0,
            'errores': 0,
        }
        # Cache para optimizar búsquedas
        self.cache_categorias = {}
        self.cache_atributos = {}
        self.cache_opciones = {}
        self.cache_bodegas = {}

    def add_arguments(self, parser):
        parser.add_argument(
            '--modo',
            type=str,
            default='test',
            choices=['test', 'completo', 'solo-estructura', 'solo-productos'],
            help='Modo de ejecución de la migración'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Tamaño del lote para procesar productos'
        )
        parser.add_argument(
            '--empresa-id',
            type=int,
            default=None,
            help='ID de la empresa a la que pertenecen los productos'
        )
        parser.add_argument(
            '--sucursal-id',
            type=int,
            default=None,
            help='ID de la sucursal por defecto para los productos'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Cantidad de productos a migrar (default: 10 en test, todos en completo)'
        )

    def handle(self, *args, **options):
        modo = options['modo']
        batch_size = options['batch_size']
        empresa_id = options['empresa_id']
        sucursal_id = options['sucursal_id']
        limite = options['limit']

        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('🚀 MIGRACIÓN VICENT → RETAILMIND'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f'\n📋 Modo: {modo}')
        self.stdout.write(f'📦 Tamaño de lote: {batch_size}')
        
        # Determinar límite según modo si no se especificó
        if limite is None:
            limite = 100 if modo == 'test' else None
        
        if limite:
            self.stdout.write(f'🔢 Límite: {limite} productos\n')
        else:
            self.stdout.write(f'🔢 Límite: TODOS los productos\n')

        # Validar que existe la configuración de MySQL
        if not self._validar_configuracion_mysql():
            return

        # Obtener empresa y sucursal
        empresa, sucursal = self._obtener_empresa_sucursal(empresa_id, sucursal_id)
        if not empresa or not sucursal:
            return

        self.stdout.write(f'🏢 Empresa: {empresa.nombre}')
        self.stdout.write(f'🏪 Sucursal por defecto: {sucursal.alias}\n')

        try:
            if modo in ['test', 'completo', 'solo-estructura']:
                self.stdout.write(self.style.WARNING('\n' + '=' * 80))
                self.stdout.write(self.style.WARNING('FASE 1: ESTRUCTURA BASE'))
                self.stdout.write(self.style.WARNING('=' * 80 + '\n'))
                self._migrar_estructura_base(empresa)

            if modo in ['test', 'completo', 'solo-productos']:
                self.stdout.write(self.style.WARNING('\n' + '=' * 80))
                self.stdout.write(self.style.WARNING('FASE 2 y 3: PRODUCTOS + VARIACIONES'))
                self.stdout.write(self.style.WARNING('=' * 80 + '\n'))
                
                self._migrar_productos_y_variaciones(sucursal, batch_size, limite)

            # Mostrar resumen
            self._mostrar_resumen()

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error fatal: {str(e)}'))
            logger.exception("Error durante la migración")
            raise

    def _validar_configuracion_mysql(self) -> bool:
        """Valida que existe la configuración de MySQL en settings"""
        if 'vicent_mysql' not in settings.DATABASES:
            # Agregar configuración de MySQL dinámicamente
            settings.DATABASES['vicent_mysql'] = {
                'ENGINE': 'django.db.backends.mysql',
                'NAME': os.environ.get('MYSQL_DATABASE', 'vicent_software'),
                'USER': os.environ.get('MYSQL_USER', 'root'),
                'PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
                'HOST': os.environ.get('MYSQL_HOST', 'localhost'),
                'PORT': os.environ.get('MYSQL_PORT', '3306'),
                'OPTIONS': {
                    'charset': 'utf8mb4',
                    'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
                },
            }

        # Probar conexión
        try:
            with connections['vicent_mysql'].cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM talla")
                total = cursor.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f'✅ Conexión MySQL establecida'))
                self.stdout.write(f'📊 Total de variaciones en Vicent: {total:,}\n')
                return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error al conectar con MySQL: {str(e)}'))
            self.stdout.write('\n💡 Asegúrate de:')
            self.stdout.write('   1. Tener MySQL instalado y corriendo')
            self.stdout.write('   2. Configurar MYSQL_PASSWORD en variables de entorno')
            self.stdout.write('   3. Que existe la base de datos "vicent_software"')
            self.stdout.write('   4. Que existe la tabla "talla"\n')
            return False

    def _obtener_empresa_sucursal(self, empresa_id, sucursal_id):
        """Obtiene o crea la empresa y sucursal para la migración"""
        try:
            # Obtener o crear empresa
            if empresa_id:
                empresa = Empresa.objects.get(id=empresa_id)
                self.stdout.write(self.style.SUCCESS(f'✅ Empresa encontrada: {empresa.nombre}'))
            else:
                # Buscar primera empresa o crearla
                empresa = Empresa.objects.first()
                if not empresa:
                    empresa = Empresa.objects.create(
                        nombre='Empresa Migración',
                        rut='00000000-0',
                        nombre_fantasia='Empresa de Migración',
                        razon_social='Empresa de Migración desde Vicent',
                        giro='Comercio',
                        direccion='Sin dirección',
                        comuna='Sin comuna',
                        ciudad='Sin ciudad',
                        correoVendedor='vendedor@empresa.com',
                        correoIntercambio='intercambio@empresa.com',
                        correoAdministrador='admin@empresa.com'
                    )
                    self.stdout.write(self.style.WARNING(f'⚠️ Empresa creada por defecto: {empresa.nombre}'))

            # Obtener o crear sucursal
            if sucursal_id:
                sucursal = Sucursal.objects.get(id=sucursal_id, empresa=empresa)
                self.stdout.write(self.style.SUCCESS(f'✅ Sucursal encontrada: {sucursal.alias}'))
            else:
                sucursal = Sucursal.objects.filter(empresa=empresa).first()
                if not sucursal:
                    sucursal = Sucursal.objects.create(
                        empresa=empresa,
                        alias='Principal',
                        direccion='Dirección principal'
                    )
                    self.stdout.write(self.style.WARNING(f'⚠️ Sucursal creada por defecto: {sucursal.alias}'))

            return empresa, sucursal

        except Empresa.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'❌ No existe empresa con ID {empresa_id}'))
            return None, None
        except Sucursal.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'❌ No existe sucursal con ID {sucursal_id}'))
            return None, None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error al obtener empresa/sucursal: {str(e)}'))
            return None, None

    def _ejecutar_query_mysql(self, query: str, params: List = None) -> List[Dict]:
        """Ejecuta una query en MySQL y retorna los resultados como lista de diccionarios"""
        with connections['vicent_mysql'].cursor() as cursor:
            cursor.execute(query, params or [])
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _migrar_estructura_base(self, empresa: Empresa):
        """FASE 1: Migra estructura base (categorías, atributos, bodegas)"""
        
        # 1. Migrar Categorías
        self.stdout.write('\n📁 1. Migrando Categorías...')
        self._migrar_categorias()

        # 2. Migrar Atributos
        self.stdout.write('\n🏷️  2. Migrando Atributos...')
        self._migrar_atributos()

        # 3. Migrar Bodegas (Sucursales)
        self.stdout.write('\n🏪 3. Migrando Bodegas...')
        self._migrar_bodegas(empresa)

    def _migrar_categorias(self):
        """Migra categorías desde valores únicos de familia"""
        query = """
            SELECT DISTINCT familia 
            FROM talla 
            WHERE familia IS NOT NULL AND familia != ''
            ORDER BY familia
        """
        
        try:
            familias = self._ejecutar_query_mysql(query)
            total = len(familias)
            self.stdout.write(f'   📊 Categorías únicas encontradas: {total}')

            with transaction.atomic():
                for idx, row in enumerate(familias, 1):
                    nombre = row['familia'].strip()
                    if not nombre:
                        continue

                    categoria, created = Categoria.objects.get_or_create(
                        nombre__iexact=nombre,
                        defaults={'nombre': nombre}
                    )

                    # Guardar en cache
                    self.cache_categorias[nombre.upper()] = categoria

                    if created:
                        self.estadisticas['categorias'] += 1
                        if idx <= 5 or idx % 20 == 0:
                            self.stdout.write(f'   ✅ [{idx}/{total}] Categoría creada: {nombre}')

            self.stdout.write(self.style.SUCCESS(f'   ✅ Categorías migradas: {self.estadisticas["categorias"]}'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ❌ Error al migrar categorías: {str(e)}'))
            raise

    def _migrar_atributos(self):
        """Migra atributos y sus opciones desde valores únicos"""
        
        atributos_config = [
            {
                'nombre': 'Marca',
                'descripcion': 'Marca del producto',
                'campo_mysql': 'marca',
                'query': "SELECT DISTINCT marca FROM talla WHERE marca IS NOT NULL AND marca != '' ORDER BY marca"
            },
            {
                'nombre': 'Color',
                'descripcion': 'Color del producto',
                'campo_mysql': 'color',
                'query': "SELECT DISTINCT color FROM talla WHERE color IS NOT NULL AND color != '' ORDER BY color"
            },
            {
                'nombre': 'Género',
                'descripcion': 'Género del producto',
                'campo_mysql': 'sexo',
                'query': "SELECT DISTINCT sexo FROM talla WHERE sexo IS NOT NULL AND sexo != '' ORDER BY sexo"
            },
            {
                'nombre': 'Departamento',
                'descripcion': 'Departamento del producto',
                'campo_mysql': 'departamento',
                'query': "SELECT DISTINCT departamento FROM talla WHERE departamento IS NOT NULL AND departamento != '' ORDER BY departamento"
            },
        ]

        try:
            with transaction.atomic():
                for config in atributos_config:
                    self.stdout.write(f'\n   🏷️  Procesando atributo: {config["nombre"]}')
                    
                    # Crear o obtener atributo
                    atributo, created = Productos_Atributos.objects.get_or_create(
                        nombre__iexact=config['nombre'],
                        defaults={
                            'nombre': config['nombre'],
                            'descripcion': config['descripcion']
                        }
                    )

                    if created:
                        self.estadisticas['atributos'] += 1
                        self.stdout.write(f'      ✅ Atributo creado: {config["nombre"]}')
                    else:
                        self.stdout.write(f'      ⚠️  Atributo ya existe: {config["nombre"]}')

                    # Guardar en cache
                    self.cache_atributos[config['nombre'].upper()] = atributo

                    # Obtener opciones desde MySQL
                    opciones = self._ejecutar_query_mysql(config['query'])
                    total_opciones = len(opciones)
                    self.stdout.write(f'      📊 Opciones encontradas: {total_opciones}')

                    # Crear opciones
                    opciones_creadas = 0
                    campo = config['campo_mysql']
                    
                    for idx, row in enumerate(opciones, 1):
                        valor = row[campo].strip() if row[campo] else ''
                        if not valor:
                            continue

                        # Buscar opción existente (puede haber duplicados)
                        opcion = AtributoOpcion.objects.filter(
                            atributo=atributo,
                            valor__iexact=valor
                        ).first()
                        
                        opcion_created = False
                        if not opcion:
                            # Crear nueva opción
                            opcion = AtributoOpcion.objects.create(
                                atributo=atributo,
                                valor=valor
                            )
                            opcion_created = True

                        # Guardar en cache con clave compuesta
                        cache_key = f"{config['nombre'].upper()}:{valor.upper()}"
                        self.cache_opciones[cache_key] = opcion

                        if opcion_created:
                            opciones_creadas += 1
                            self.estadisticas['opciones_atributo'] += 1
                            if idx <= 3 or idx % 50 == 0:
                                self.stdout.write(f'         ➕ [{idx}/{total_opciones}] {valor}')

                    self.stdout.write(self.style.SUCCESS(f'      ✅ Opciones creadas: {opciones_creadas}/{total_opciones}'))

            self.stdout.write(self.style.SUCCESS(f'\n   ✅ Total atributos: {self.estadisticas["atributos"]}'))
            self.stdout.write(self.style.SUCCESS(f'   ✅ Total opciones: {self.estadisticas["opciones_atributo"]}'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ❌ Error al migrar atributos: {str(e)}'))
            raise

    def _migrar_bodegas(self, empresa: Empresa):
        """Carga bodegas/sucursales existentes al cache (NO crea nuevas)"""
        query = """
            SELECT DISTINCT alias 
            FROM talla 
            WHERE alias IS NOT NULL AND alias != ''
            ORDER BY alias
        """
        
        try:
            bodegas_mysql = self._ejecutar_query_mysql(query)
            total = len(bodegas_mysql)
            self.stdout.write(f'   📊 Bodegas en Vicent: {total}')

            # Cargar TODAS las sucursales existentes al cache
            sucursales_existentes = Sucursal.objects.all()
            self.stdout.write(f'   📊 Sucursales existentes en RetailMind: {sucursales_existentes.count()}')

            bodegas_encontradas = 0
            bodegas_no_encontradas = []

            for row in bodegas_mysql:
                alias = row['alias'].strip()
                if not alias:
                    continue

                # Buscar sucursal existente por alias
                bodega = sucursales_existentes.filter(alias__iexact=alias).first()
                
                if bodega:
                    # Guardar en cache
                    self.cache_bodegas[alias.upper()] = bodega
                    bodegas_encontradas += 1
                else:
                    bodegas_no_encontradas.append(alias)

            self.stdout.write(self.style.SUCCESS(f'   ✅ Bodegas encontradas: {bodegas_encontradas}/{total}'))
            
            if bodegas_no_encontradas:
                self.stdout.write(self.style.WARNING(f'   ⚠️  Bodegas NO encontradas ({len(bodegas_no_encontradas)}):'))
                for alias in bodegas_no_encontradas[:10]:
                    self.stdout.write(f'      - {alias}')
                if len(bodegas_no_encontradas) > 10:
                    self.stdout.write(f'      ... y {len(bodegas_no_encontradas) - 10} más')
                self.stdout.write(self.style.WARNING(f'\n   💡 Las variaciones de estas bodegas se omitirán'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ❌ Error al migrar bodegas: {str(e)}'))
            raise

    def _migrar_productos_y_variaciones(self, sucursal: Sucursal, batch_size: int, limite: int = None):
        """
        FASE 2 y 3: Migra productos (agrupando por articulo+atributos+sucursal) y sus variaciones
        
        ⚠️ IMPORTANTE:
        - En Vicent: cada producto puede estar en VARIAS sucursales
        - CREAMOS un producto POR CADA SUCURSAL donde existe
        - Agrupamos por: articulo + marca + color + sexo + costo + descripcion + departamento + SUCURSAL
        - Luego creamos las variaciones (ProductoTalla) para cada producto en su sucursal
        """
        
        # Query para obtener productos agrupados por sucursal
        query_productos = """
            SELECT 
                articulo,
                descripcion,
                marca,
                familia,
                color,
                sexo,
                departamento,
                costo,
                alias,
                AVG(precioventapublico) as precio_promedio,
                COUNT(*) as cantidad_variaciones,
                SUM(stock) as stock_total
            FROM talla
            WHERE articulo IS NOT NULL 
                AND articulo != ''
                AND alias IS NOT NULL 
                AND alias != ''
            GROUP BY articulo, descripcion, marca, familia, color, sexo, departamento, costo, alias
            ORDER BY articulo, alias
        """

        if limite:
            query_productos += f" LIMIT {limite}"

        try:
            productos_agrupados = self._ejecutar_query_mysql(query_productos)
            total_productos = len(productos_agrupados)
            
            self.stdout.write(f'\n📦 Productos a crear: {total_productos:,}')
            self.stdout.write(f'   (Agrupados por: artículo + atributos + sucursal)\n')

            # Procesar en lotes
            for inicio in range(0, total_productos, batch_size):
                fin = min(inicio + batch_size, total_productos)
                lote = productos_agrupados[inicio:fin]
                
                self.stdout.write(f'\n📦 Procesando lote {inicio // batch_size + 1}: productos {inicio + 1} a {fin}...')
                
                self._procesar_lote_productos(lote, sucursal, inicio)

            self.stdout.write(self.style.SUCCESS(f'\n✅ Productos padre migrados: {self.estadisticas["productos_padre"]:,}'))
            self.stdout.write(self.style.SUCCESS(f'✅ Variaciones migradas: {self.estadisticas["variaciones"]:,}'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error al migrar productos: {str(e)}'))
            raise

    def _procesar_lote_productos(self, lote: List[Dict], sucursal_default: Sucursal, offset: int):
        """Procesa un lote de productos y sus variaciones"""
        
        for idx, datos_producto in enumerate(lote, 1):
            numero_global = offset + idx
            articulo = datos_producto['articulo']
            alias_sucursal = datos_producto['alias']

            try:
                with transaction.atomic():
                    # Buscar la sucursal específica de este producto
                    sucursal_producto = self.cache_bodegas.get(alias_sucursal.upper())
                    if not sucursal_producto:
                        # Si no existe la sucursal, omitir este producto
                        continue
                    
                    # FASE 2: Crear producto en su sucursal específica
                    producto = self._crear_producto_padre(datos_producto, sucursal_producto)
                    
                    if producto:
                        self.estadisticas['productos_padre'] += 1
                        
                        # FASE 3: Crear variaciones (ProductoTalla) de este producto en esta sucursal
                        variaciones_creadas = self._crear_variaciones_producto(producto, datos_producto)
                        self.estadisticas['variaciones'] += variaciones_creadas

                        if numero_global <= 5 or numero_global % 20 == 0:
                            self.stdout.write(
                                f'   ✅ [{numero_global}] {articulo} en {alias_sucursal} - {producto.descripcion[:40]} '
                                f'({variaciones_creadas} variaciones)'
                            )

            except Exception as e:
                self.estadisticas['errores'] += 1
                self.stdout.write(self.style.ERROR(
                    f'   ❌ [{numero_global}] Error en {articulo}-{alias_sucursal}: {str(e)}'
                ))
                logger.exception(f"Error procesando producto {articulo} en {alias_sucursal}")

    def _crear_producto_padre(self, datos: Dict, sucursal_producto: Sucursal) -> Producto:
        """
        Crea el producto en la sucursal específica
        Un mismo artículo puede existir en múltiples sucursales
        """
        
        # Buscar categoría
        familia = datos.get('familia', '').strip()
        categoria = self.cache_categorias.get(familia.upper()) if familia else None
        
        if not categoria and familia:
            categoria, _ = Categoria.objects.get_or_create(
                nombre__iexact=familia,
                defaults={'nombre': familia}
            )
            self.cache_categorias[familia.upper()] = categoria

        # Buscar opciones de atributos
        marca = datos.get('marca', '').strip()
        color = datos.get('color', '').strip()
        sexo = datos.get('sexo', '').strip()
        departamento = datos.get('departamento', '').strip()

        # Obtener opciones desde cache
        opcion_marca = self._obtener_opcion_atributo('Marca', marca) if marca else None
        opcion_color = self._obtener_opcion_atributo('Color', color) if color else None
        opcion_genero = self._obtener_opcion_atributo('Género', sexo) if sexo else None
        opcion_depto = self._obtener_opcion_atributo('Departamento', departamento) if departamento else None

        # Precios
        costo = int(datos.get('costo', 0) or 0)
        precio_promedio = int(datos.get('precio_promedio', 0) or 0)
        sobreprecio = max(0, precio_promedio - costo)

        # Crear producto EN LA SUCURSAL ESPECÍFICA
        producto, created = Producto.objects.get_or_create(
            articulo=datos['articulo'],
            sucursal=sucursal_producto,  # ← Sucursal del grupo
            atributo1=opcion_marca,
            atributo2=opcion_color,
            atributo3=opcion_genero,
            defaults={
                'descripcion': datos.get('descripcion', '')[:250],
                'categoria': categoria,
                'atributo4': opcion_depto,
                'costo': costo,
                'sobreprecio': sobreprecio,
                'precioventa': precio_promedio,
                'precioSugerido': precio_promedio,
                'tipo_talla': 'CL',
            }
        )

        return producto

    def _crear_variaciones_producto(self, producto: Producto, datos_grupo: Dict) -> int:
        """
        Crea las variaciones (ProductoTalla) para este producto en esta sucursal específica
        Filtra por TODOS los campos del grupo para obtener solo las tallas de este producto
        """
        
        # Query para obtener las tallas de este producto en esta sucursal
        query = """
            SELECT 
                size,
                stock,
                codigo_asociado
            FROM talla
            WHERE articulo = %s
                AND descripcion = %s
                AND marca = %s
                AND familia = %s
                AND color = %s
                AND sexo = %s
                AND departamento = %s
                AND costo = %s
                AND alias = %s
                AND size IS NOT NULL
            ORDER BY size
        """
        
        # Parámetros del filtro (todos los campos del grupo)
        params = [
            datos_grupo['articulo'],
            datos_grupo['descripcion'],
            datos_grupo['marca'],
            datos_grupo['familia'],
            datos_grupo['color'],
            datos_grupo['sexo'],
            datos_grupo['departamento'],
            datos_grupo['costo'],
            datos_grupo['alias']
        ]
        
        variaciones = self._ejecutar_query_mysql(query, params)
        variaciones_creadas = 0

        for var in variaciones:
            try:
                talla = var['size'].strip() if var['size'] else 'UNICA'
                stock = int(var['stock'] or 0)
                codigo_asociado = var.get('codigo_asociado')  # Obtener SKU real de MySQL

                # Usar codigo_asociado como SKU (si existe), sino generar uno
                if codigo_asociado:
                    try:
                        # Intentar convertir a entero directamente
                        codigo_str = str(codigo_asociado).strip()
                        if codigo_str.isdigit():
                            sku_final = int(codigo_str)
                        else:
                            # Si contiene caracteres no numéricos, usar hash
                            sku_final = abs(hash(codigo_str)) % (10**18)  # BigInt soporta hasta 10^18
                    except (ValueError, OverflowError):
                        # Si falla, generar SKU con hash
                        sku_final = abs(hash(str(codigo_asociado))) % (10**18)
                else:
                    # Fallback: generar SKU si no existe codigo_asociado
                    sku_generado = f"{datos_grupo['articulo']}-{datos_grupo['alias']}-{talla}"
                    sku_final = abs(hash(sku_generado)) % (10**18)

                # Crear variación
                Producto_Talla.objects.get_or_create(
                    producto=producto,
                    sku=sku_final,
                    defaults={
                        'talla': talla,
                        'stock': stock,
                    }
                )
                
                variaciones_creadas += 1

            except Exception as e:
                logger.error(f"Error creando variación {talla} para {datos_grupo['articulo']}: {str(e)}")
                continue

        return variaciones_creadas

    def _obtener_opcion_atributo(self, nombre_atributo: str, valor: str):
        """Obtiene una opción de atributo desde el cache o la base de datos"""
        if not valor:
            return None

        cache_key = f"{nombre_atributo.upper()}:{valor.upper()}"
        
        # Buscar en cache
        if cache_key in self.cache_opciones:
            return self.cache_opciones[cache_key]

        # Buscar en BD
        try:
            atributo = self.cache_atributos.get(nombre_atributo.upper())
            if not atributo:
                atributo = Productos_Atributos.objects.get(nombre__iexact=nombre_atributo)
                self.cache_atributos[nombre_atributo.upper()] = atributo

            # Buscar opción existente (puede haber duplicados)
            opcion = AtributoOpcion.objects.filter(
                atributo=atributo,
                valor__iexact=valor
            ).first()
            
            if not opcion:
                # Crear nueva opción
                opcion = AtributoOpcion.objects.create(
                    atributo=atributo,
                    valor=valor
                )
            
            self.cache_opciones[cache_key] = opcion
            return opcion

        except Productos_Atributos.DoesNotExist:
            return None

    def _mostrar_resumen(self):
        """Muestra un resumen de la migración"""
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 80))
        self.stdout.write(self.style.SUCCESS('📊 RESUMEN DE MIGRACIÓN'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        self.stdout.write(f'\n📁 Categorías migradas: {self.estadisticas["categorias"]:,}')
        self.stdout.write(f'🏷️  Atributos migrados: {self.estadisticas["atributos"]:,}')
        self.stdout.write(f'🔖 Opciones de atributos: {self.estadisticas["opciones_atributo"]:,}')
        self.stdout.write(f'🏪 Bodegas migradas: {self.estadisticas["bodegas"]:,}')
        self.stdout.write(f'📦 Productos padre creados: {self.estadisticas["productos_padre"]:,}')
        self.stdout.write(f'📊 Variaciones migradas: {self.estadisticas["variaciones"]:,}')
        
        if self.estadisticas['errores'] > 0:
            self.stdout.write(self.style.ERROR(f'❌ Errores encontrados: {self.estadisticas["errores"]:,}'))
        
        self.stdout.write(self.style.SUCCESS('\n🎉 ¡Migración completada!\n'))

        # Verificación final
        self.stdout.write('\n🔍 Verificación en base de datos:')
        self.stdout.write(f'   • Categorías: {Categoria.objects.count():,}')
        self.stdout.write(f'   • Atributos: {Productos_Atributos.objects.count():,}')
        self.stdout.write(f'   • Opciones atributos: {AtributoOpcion.objects.count():,}')
        self.stdout.write(f'   • Sucursales/Bodegas: {Sucursal.objects.count():,}')
        self.stdout.write(f'   • Productos: {Producto.objects.count():,}')
        self.stdout.write(f'   • Variaciones: {Producto_Talla.objects.count():,}\n')

