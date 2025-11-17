"""
Comando para convertir stock legacy al sistema de movimientos

USO:
    python manage.py convertir_stock_legacy --all
    python manage.py convertir_stock_legacy --sucursal 5
    python manage.py convertir_stock_legacy --producto 143829
    python manage.py convertir_stock_legacy --dry-run  # Ver qué se haría sin ejecutar

DESCRIPCIÓN:
    - Lee el campo 'stock' de Producto_Talla (datos migrados/legacy)
    - Crea movimientos de INGRESO inicial por ese stock
    - Permite migrar gradualmente del sistema legacy al nuevo
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from app.models import Producto_Talla, Movimientos_Producto, Sucursal
from decimal import Decimal


class Command(BaseCommand):
    help = 'Convierte stock legacy (campo stock) a movimientos de inventario inicial'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Convertir TODOS los productos (usar con precaución)'
        )
        parser.add_argument(
            '--sucursal',
            type=int,
            help='Convertir solo productos de una sucursal específica'
        )
        parser.add_argument(
            '--producto',
            type=int,
            help='Convertir solo un producto específico (por ID)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular sin hacer cambios reales'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Saltar productos que ya tienen movimientos'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        skip_existing = options['skip_existing']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 MODO DRY-RUN: No se harán cambios reales\n'))
        
        # Determinar qué productos procesar
        if options['all']:
            tallas = Producto_Talla.objects.select_related('producto', 'producto__sucursal').all()
            self.stdout.write(f'📦 Procesando TODOS los productos ({tallas.count()} tallas)')
        elif options['sucursal']:
            sucursal_id = options['sucursal']
            tallas = Producto_Talla.objects.select_related('producto', 'producto__sucursal').filter(
                producto__sucursal_id=sucursal_id
            )
            self.stdout.write(f'📦 Procesando productos de sucursal {sucursal_id} ({tallas.count()} tallas)')
        elif options['producto']:
            producto_id = options['producto']
            tallas = Producto_Talla.objects.select_related('producto', 'producto__sucursal').filter(
                producto_id=producto_id
            )
            self.stdout.write(f'📦 Procesando producto {producto_id} ({tallas.count()} tallas)')
        else:
            self.stdout.write(self.style.ERROR('❌ Error: Debes especificar --all, --sucursal o --producto'))
            return
        
        # Contadores
        procesados = 0
        saltados = 0
        creados = 0
        errores = 0
        
        for talla in tallas:
            try:
                # Verificar si ya tiene movimientos
                tiene_movimientos = talla.movimientos_productos_talla.exists()
                
                if tiene_movimientos and skip_existing:
                    self.stdout.write(f'  ⏭️  {talla.sku}: Ya tiene movimientos, saltando...')
                    saltados += 1
                    continue
                
                if tiene_movimientos:
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  {talla.sku}: Ya tiene movimientos, se agregará inventario inicial adicional'
                    ))
                
                # Verificar si tiene stock legacy
                if talla.stock <= 0:
                    self.stdout.write(f'  ⏭️  {talla.sku}: Stock = 0, saltando...')
                    saltados += 1
                    continue
                
                # Obtener sucursal del producto
                sucursal = talla.producto.sucursal
                
                if not sucursal:
                    self.stdout.write(self.style.ERROR(
                        f'  ❌ {talla.sku}: Producto sin sucursal asignada'
                    ))
                    errores += 1
                    continue
                
                # Mostrar lo que se hará
                self.stdout.write(
                    f'  ✅ {talla.sku} ({talla.producto.articulo} - Talla {talla.talla}): '
                    f'Stock legacy = {talla.stock} en {sucursal.alias}'
                )
                
                if not dry_run:
                    # Crear movimiento de inventario inicial
                    with transaction.atomic():
                        movimiento = Movimientos_Producto.objects.create(
                            ProductoTalla=talla,
                            sucursal_destino=sucursal,
                            sucursal_origen=None,  # Es ingreso inicial, no viene de otra sucursal
                            cantidad=talla.stock,  # Positivo (ingreso)
                            costo=talla.producto.costo or 0,
                            sobreprecio=talla.producto.sobreprecio or 0,
                            precio=talla.producto.precioventa or 0,
                            concepto='INGRESO_INICIAL',  # Usar concepto existente
                            tipo_movimiento='INGRESO',
                            estado='COMPLETADO',
                            responsable='Sistema - Migración',
                            observaciones=f'Inventario inicial migrado desde campo stock legacy: {talla.stock} unidades',
                            fecha=timezone.now()
                        )
                        creados += 1
                        self.stdout.write(f'     ↳ Movimiento #{movimiento.id} creado')
                
                procesados += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ Error en {talla.sku}: {str(e)}'))
                errores += 1
        
        # Resumen final
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('📊 RESUMEN DE CONVERSIÓN:'))
        self.stdout.write(f'  ✅ Procesados: {procesados}')
        self.stdout.write(f'  📝 Movimientos creados: {creados}')
        self.stdout.write(f'  ⏭️  Saltados: {saltados}')
        self.stdout.write(f'  ❌ Errores: {errores}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠️  Esto fue una simulación. Ejecuta sin --dry-run para aplicar cambios.'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Conversión completada!'))

