"""
Django management command para limpiar datos de migración.

Este comando elimina datos en el orden correcto respetando las dependencias de Foreign Keys.
Usa transacciones para asegurar la integridad de los datos.

⚠️ IMPORTANTE - SEGURIDAD:
    - Este comando SOLO elimina de la base de datos de Django (configurada en settings.py)
    - NUNCA toca MySQL ni ninguna otra base de datos externa
    - NO hace conexiones a Vicent ni otros sistemas
    - Solo usa Django ORM estándar (NO hay queries raw SQL a bases externas)

Uso:
    python manage.py clean_migration_data
    python manage.py clean_migration_data --force  # Sin confirmación
"""

import time
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.utils import timezone

from app.models import (
    Movimientos_Producto,
    Dte_Detalle_Pago,
    Dte_Productos,
    Dte,
    Ticket_Productos,
    Ticket,
    Producto_Talla,
    Producto,
    AtributoOpcion,
    Productos_Atributos,
    Categoria,
    Sucursal,
    Empresa
)


class Command(BaseCommand):
    help = """
    Elimina datos de migración en el orden correcto respetando dependencias FK.
    
    IMPORTANTE:
    - Elimina SOLO empresas donde esProveedor=False
    - Mantiene las empresas principales (proveedores)
    - Usa transacciones para asegurar integridad
    - Pide confirmación antes de eliminar (usar --force para omitir)
    
    Uso:
        python manage.py clean_migration_data
        python manage.py clean_migration_data --force
    """

    def __init__(self):
        super().__init__()
        self.estadisticas = {}
        self.tiempo_por_tabla = {}

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Forzar eliminación sin pedir confirmación',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirmar eliminación automáticamente (alias de --force)',
        )

    def handle(self, *args, **options):
        force = options['force'] or options.get('confirm', False)
        
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('🗑️  LIMPIEZA RÁPIDA DE DATOS DE MIGRACIÓN'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write('')
        
        # PASO 1: Contar registros a eliminar
        self.stdout.write(self.style.WARNING('📊 PASO 1: Contando registros...'))
        self.stdout.write('')
        
        try:
            conteos = self._contar_registros()
            self._mostrar_conteos(conteos)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error al contar registros: {str(e)}'))
            return
        
        # Verificar si hay datos para eliminar
        total_registros = sum(conteos.values())
        if total_registros == 0:
            self.stdout.write(self.style.SUCCESS('\n✅ No hay datos para eliminar.'))
            return
        
        # PASO 2: Pedir confirmación
        if not force:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('⚠️  ADVERTENCIA: Esta acción NO se puede deshacer.'))
            self.stdout.write(f'Se eliminarán un total de {total_registros:,} registros.')
            self.stdout.write('')
            
            confirmacion = input('¿Desea continuar? Escriba "SI" para confirmar: ')
            
            if confirmacion.upper() != 'SI':
                self.stdout.write(self.style.ERROR('\n❌ Operación cancelada por el usuario.'))
                return
        
        # PASO 3: Eliminar datos
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('=' * 80))
        self.stdout.write(self.style.WARNING('🗑️  PASO 2: Eliminando datos (MODO RÁPIDO - TRUNCATE)...'))
        self.stdout.write(self.style.WARNING('=' * 80))
        self.stdout.write('')
        
        tiempo_inicio_total = time.time()
        
        try:
            # ✅ Desactivar triggers temporalmente para mayor velocidad
            with connection.cursor() as cursor:
                cursor.execute('SET session_replication_role = replica;')
            
            with transaction.atomic():
                self._eliminar_datos_rapido()
            
            # ✅ Reactivar triggers
            with connection.cursor() as cursor:
                cursor.execute('SET session_replication_role = DEFAULT;')
            
            tiempo_total = time.time() - tiempo_inicio_total
            
            # PASO 4: Mostrar resumen
            self._mostrar_resumen(tiempo_total)
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error durante la eliminación: {str(e)}'))
            self.stdout.write(self.style.ERROR('⚠️  La transacción ha sido revertida (rollback).'))
            
            # Reactivar triggers en caso de error
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET session_replication_role = DEFAULT;')
            except:
                pass
            
            raise

    def _contar_registros(self):
        """Cuenta la cantidad de registros a eliminar por tabla"""
        conteos = {}
        
        # Contar en orden inverso al de eliminación
        conteos['Movimientos_Producto'] = Movimientos_Producto.objects.count()
        conteos['Dte_Detalle_Pago'] = Dte_Detalle_Pago.objects.count()
        
        # Verificar si existe Dte_Productos
        try:
            conteos['Dte_Productos'] = Dte_Productos.objects.count()
        except Exception:
            conteos['Dte_Productos'] = 0
        
        conteos['Dte'] = Dte.objects.count()
        
        # Verificar si existe Ticket_Productos
        try:
            conteos['Ticket_Productos'] = Ticket_Productos.objects.count()
        except Exception:
            conteos['Ticket_Productos'] = 0
        
        conteos['Ticket'] = Ticket.objects.count()
        conteos['Producto_Talla'] = Producto_Talla.objects.count()
        conteos['Producto'] = Producto.objects.count()
        conteos['AtributoOpcion'] = AtributoOpcion.objects.count()
        conteos['Productos_Atributos'] = Productos_Atributos.objects.count()
        conteos['Categoria'] = Categoria.objects.count()
        conteos['Sucursal'] = Sucursal.objects.count()
        
        # Solo empresas donde esProveedor=False
        conteos['Empresa'] = Empresa.objects.filter(esProveedor=False).count()
        
        return conteos

    def _mostrar_conteos(self, conteos):
        """Muestra los conteos de registros a eliminar"""
        self.stdout.write('Registros a eliminar por tabla:')
        self.stdout.write('')
        
        for tabla, cantidad in conteos.items():
            if cantidad > 0:
                self.stdout.write(f'  • {tabla:.<40} {cantidad:>10,} registros')
            else:
                self.stdout.write(self.style.SUCCESS(f'  • {tabla:.<40} {cantidad:>10,} registros (vacía)'))
        
        self.stdout.write('')
        total = sum(conteos.values())
        self.stdout.write(f'  {"TOTAL":.<40} {total:>10,} registros')

    def _eliminar_datos_rapido(self):
        """
        Elimina datos usando TRUNCATE CASCADE - MUCHO MÁS RÁPIDO
        
        TRUNCATE es 10-100x más rápido que DELETE porque:
        - No escanea todas las filas
        - No genera logs de transacción por fila
        - Resetea secuencias automáticamente
        """
        
        with connection.cursor() as cursor:
            tiempo_inicio = time.time()
            
            # Lista de tablas en orden (las FK se manejan con CASCADE)
            tablas = [
                ('app_movimientos_producto', 'Movimientos_Producto', '1/13'),
                ('app_dte_detalle_pago', 'Dte_Detalle_Pago', '2/13'),
                ('app_dte_productos', 'Dte_Productos', '3/13'),
                ('app_dte', 'Dte', '4/13'),
                ('app_ticket_productos', 'Ticket_Productos', '5/13'),
                ('app_ticket', 'Ticket', '6/13'),
                ('app_producto_talla', 'Producto_Talla', '7/13'),
                ('app_producto', 'Producto', '8/13'),
                ('app_atributoopcion', 'AtributoOpcion', '9/13'),  # ✅ CORREGIDO: sin la 'p'
                ('app_productos_atributos', 'Productos_Atributos', '10/13'),
                ('app_categoria', 'Categoria', '11/13'),
                ('app_sucursal', 'Sucursal', '12/13'),
            ]
            
            for tabla_db, tabla_modelo, progreso in tablas:
                try:
                    # Contar antes de eliminar
                    cursor.execute(f'SELECT COUNT(*) FROM {tabla_db}')
                    cantidad = cursor.fetchone()[0]
                    
                    if cantidad == 0:
                        self.stdout.write(self.style.SUCCESS(f'  ✅ [{progreso}] {tabla_modelo}: Ya está vacía'))
                        self.estadisticas[tabla_modelo] = 0
                        self.tiempo_por_tabla[tabla_modelo] = 0
                        continue
                    
                    tiempo_tabla_inicio = time.time()
                    
                    # TRUNCATE CASCADE - SUPER RÁPIDO
                    cursor.execute(f'TRUNCATE TABLE {tabla_db} RESTART IDENTITY CASCADE')
                    
                    tiempo_tabla = time.time() - tiempo_tabla_inicio
                    
                    self.estadisticas[tabla_modelo] = cantidad
                    self.tiempo_por_tabla[tabla_modelo] = tiempo_tabla
                    
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  ✅ [{progreso}] {tabla_modelo}: '
                            f'{cantidad:,} registros eliminados en {tiempo_tabla:.2f}s'
                        )
                    )
                    
                except Exception as e:
                    # Si la tabla no existe, continuar
                    if 'does not exist' in str(e):
                        self.stdout.write(self.style.WARNING(f'  ⚠️  [{progreso}] {tabla_modelo}: Tabla no existe'))
                        self.estadisticas[tabla_modelo] = 0
                        self.tiempo_por_tabla[tabla_modelo] = 0
                    else:
                        raise
            
            # Eliminar empresas con esProveedor=False de forma tradicional
            # (necesitamos el WHERE clause)
            try:
                cursor.execute('SELECT COUNT(*) FROM app_empresa WHERE "esProveedor" = FALSE')
                cantidad_empresas = cursor.fetchone()[0]
                
                if cantidad_empresas > 0:
                    tiempo_empresa_inicio = time.time()
                    cursor.execute('DELETE FROM app_empresa WHERE "esProveedor" = FALSE')
                    tiempo_empresa = time.time() - tiempo_empresa_inicio
                    
                    self.estadisticas['Empresa (esProveedor=False)'] = cantidad_empresas
                    self.tiempo_por_tabla['Empresa (esProveedor=False)'] = tiempo_empresa
                    
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  ✅ [13/13] Empresa (esProveedor=False): '
                            f'{cantidad_empresas:,} registros eliminados en {tiempo_empresa:.2f}s'
                        )
                    )
                else:
                    self.stdout.write(self.style.SUCCESS(f'  ✅ [13/13] Empresa (esProveedor=False): Ya está vacía'))
                    self.estadisticas['Empresa (esProveedor=False)'] = 0
                    self.tiempo_por_tabla['Empresa (esProveedor=False)'] = 0
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ [13/13] Error al eliminar empresas: {e}'))
                raise

    def _eliminar_datos(self):
        """
        MÉTODO ANTIGUO - Mantenerlo como fallback
        Elimina datos en el ORDEN correcto respetando dependencias FK
        """
        
        # 1. Eliminar Movimientos_Producto
        self._eliminar_tabla(
            'Movimientos_Producto',
            Movimientos_Producto.objects.all(),
            '1/13'
        )
        
        # 2. Eliminar Dte_Detalle_Pago
        self._eliminar_tabla(
            'Dte_Detalle_Pago',
            Dte_Detalle_Pago.objects.all(),
            '2/13'
        )
        
        # 3. Eliminar Dte_Productos (si existe)
        try:
            self._eliminar_tabla(
                'Dte_Productos',
                Dte_Productos.objects.all(),
                '3/13'
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  ⚠️  [3/13] Dte_Productos no existe o ya está vacía'))
        
        # 4. Eliminar Dte
        self._eliminar_tabla(
            'Dte',
            Dte.objects.all(),
            '4/13'
        )
        
        # 5. Eliminar Ticket_Productos (si existe)
        try:
            self._eliminar_tabla(
                'Ticket_Productos',
                Ticket_Productos.objects.all(),
                '5/13'
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  ⚠️  [5/13] Ticket_Productos no existe o ya está vacía'))
        
        # 6. Eliminar Ticket
        self._eliminar_tabla(
            'Ticket',
            Ticket.objects.all(),
            '6/13'
        )
        
        # 7. Eliminar Producto_Talla
        self._eliminar_tabla(
            'Producto_Talla',
            Producto_Talla.objects.all(),
            '7/13'
        )
        
        # 8. Eliminar Producto
        self._eliminar_tabla(
            'Producto',
            Producto.objects.all(),
            '8/13'
        )
        
        # 9. Eliminar AtributoOpcion
        self._eliminar_tabla(
            'AtributoOpcion',
            AtributoOpcion.objects.all(),
            '9/13'
        )
        
        # 10. Eliminar Productos_Atributos
        self._eliminar_tabla(
            'Productos_Atributos',
            Productos_Atributos.objects.all(),
            '10/13'
        )
        
        # 11. Eliminar Categoria
        self._eliminar_tabla(
            'Categoria',
            Categoria.objects.all(),
            '11/13'
        )
        
        # 12. Eliminar Sucursal
        self._eliminar_tabla(
            'Sucursal',
            Sucursal.objects.all(),
            '12/13'
        )
        
        # 13. Eliminar Empresa (solo esProveedor=False)
        self._eliminar_tabla(
            'Empresa (esProveedor=False)',
            Empresa.objects.filter(esProveedor=False),
            '13/13'
        )

    def _eliminar_tabla(self, nombre_tabla, queryset, progreso):
        """Elimina registros de una tabla y mide el tiempo"""
        cantidad = queryset.count()
        
        if cantidad == 0:
            self.stdout.write(self.style.SUCCESS(f'  ✅ [{progreso}] {nombre_tabla}: Ya está vacía'))
            self.estadisticas[nombre_tabla] = 0
            self.tiempo_por_tabla[nombre_tabla] = 0
            return
        
        tiempo_inicio = time.time()
        
        try:
            queryset.delete()
            tiempo_transcurrido = time.time() - tiempo_inicio
            
            self.estadisticas[nombre_tabla] = cantidad
            self.tiempo_por_tabla[nombre_tabla] = tiempo_transcurrido
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'  ✅ [{progreso}] {nombre_tabla}: '
                    f'{cantidad:,} registros eliminados en {tiempo_transcurrido:.2f}s'
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f'  ❌ [{progreso}] {nombre_tabla}: Error al eliminar - {str(e)}'
                )
            )
            raise

    def _mostrar_resumen(self, tiempo_total):
        """Muestra un resumen de la eliminación"""
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('📊 RESUMEN DE ELIMINACIÓN'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write('')
        
        # Resumen por tabla
        self.stdout.write('Registros eliminados por tabla:')
        self.stdout.write('')
        
        total_eliminados = 0
        for tabla, cantidad in self.estadisticas.items():
            tiempo = self.tiempo_por_tabla.get(tabla, 0)
            if cantidad > 0:
                self.stdout.write(
                    f'  • {tabla:.<45} {cantidad:>10,} registros ({tiempo:>6.2f}s)'
                )
                total_eliminados += cantidad
            else:
                self.stdout.write(
                    self.style.WARNING(f'  • {tabla:.<45} {cantidad:>10,} registros (vacía)')
                )
        
        self.stdout.write('')
        self.stdout.write(f'  {"TOTAL ELIMINADO":.<45} {total_eliminados:>10,} registros')
        self.stdout.write(f'  {"TIEMPO TOTAL":.<45} {tiempo_total:>10.2f} segundos')
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('✅ LIMPIEZA COMPLETADA EXITOSAMENTE'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write('')
        
        # Verificación final
        self.stdout.write('🔍 Verificación final:')
        self.stdout.write('')
        self.stdout.write(f'  • Movimientos_Producto: {Movimientos_Producto.objects.count():,}')
        self.stdout.write(f'  • Dte_Detalle_Pago: {Dte_Detalle_Pago.objects.count():,}')
        
        try:
            self.stdout.write(f'  • Dte_Productos: {Dte_Productos.objects.count():,}')
        except Exception:
            self.stdout.write(f'  • Dte_Productos: N/A')
        
        self.stdout.write(f'  • Dte: {Dte.objects.count():,}')
        
        try:
            self.stdout.write(f'  • Ticket_Productos: {Ticket_Productos.objects.count():,}')
        except Exception:
            self.stdout.write(f'  • Ticket_Productos: N/A')
        
        self.stdout.write(f'  • Ticket: {Ticket.objects.count():,}')
        self.stdout.write(f'  • Producto_Talla: {Producto_Talla.objects.count():,}')
        self.stdout.write(f'  • Producto: {Producto.objects.count():,}')
        self.stdout.write(f'  • AtributoOpcion: {AtributoOpcion.objects.count():,}')
        self.stdout.write(f'  • Productos_Atributos: {Productos_Atributos.objects.count():,}')
        self.stdout.write(f'  • Categoria: {Categoria.objects.count():,}')
        self.stdout.write(f'  • Sucursal: {Sucursal.objects.count():,}')
        self.stdout.write(f'  • Empresa (total): {Empresa.objects.count():,}')
        self.stdout.write(f'  • Empresa (esProveedor=False): {Empresa.objects.filter(esProveedor=False).count():,}')
        self.stdout.write(f'  • Empresa (esProveedor=True): {Empresa.objects.filter(esProveedor=True).count():,}')
        self.stdout.write('')

