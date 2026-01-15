"""
Comando para generar lotes FIFO para productos existentes que no los tienen.

Uso:
    python manage.py generar_lotes_faltantes              # Modo preview (no hace cambios)
    python manage.py generar_lotes_faltantes --ejecutar   # Ejecuta la creación de lotes
    python manage.py generar_lotes_faltantes --sucursal 1 # Solo para una sucursal específica
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from app.models import Producto_Talla, LoteProducto, Productos_Recepcionados, Producto
from datetime import datetime


class Command(BaseCommand):
    help = 'Genera lotes FIFO para productos existentes que no tienen lotes asociados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ejecutar',
            action='store_true',
            help='Ejecutar la creación de lotes (sin esto, solo muestra preview)',
        )
        parser.add_argument(
            '--sucursal',
            type=int,
            help='ID de sucursal específica (opcional)',
        )

    def handle(self, *args, **options):
        ejecutar = options['ejecutar']
        sucursal_id = options.get('sucursal')

        self.stdout.write(self.style.WARNING('=' * 60))
        self.stdout.write(self.style.WARNING('GENERADOR DE LOTES FIFO PARA PRODUCTOS EXISTENTES'))
        self.stdout.write(self.style.WARNING('=' * 60))

        if not ejecutar:
            self.stdout.write(self.style.NOTICE('\n[!] MODO PREVIEW - No se haran cambios'))
            self.stdout.write(self.style.NOTICE('    Use --ejecutar para aplicar los cambios\n'))

        # Obtener productos_talla sin lotes
        productos_talla_qs = Producto_Talla.objects.filter(stock__gt=0)
        
        if sucursal_id:
            productos_talla_qs = productos_talla_qs.filter(producto__sucursal_id=sucursal_id)
            self.stdout.write(f'\n[*] Filtrando por sucursal ID: {sucursal_id}')

        # Filtrar solo los que NO tienen lotes activos
        productos_sin_lotes = []
        for pt in productos_talla_qs.select_related('producto', 'producto__sucursal'):
            lotes_activos = LoteProducto.objects.filter(
                producto_talla=pt, 
                activo=True, 
                agotado=False
            ).aggregate(total=Sum('cantidad_disponible'))['total'] or 0
            
            if lotes_activos == 0 and pt.stock > 0:
                productos_sin_lotes.append(pt)

        self.stdout.write(f'\n[+] Productos con stock pero sin lotes FIFO: {len(productos_sin_lotes)}')

        if not productos_sin_lotes:
            self.stdout.write(self.style.SUCCESS('\n[OK] Todos los productos ya tienen lotes FIFO!'))
            return

        # Mostrar resumen
        self.stdout.write('\n' + '-' * 60)
        self.stdout.write('PRODUCTOS A PROCESAR:')
        self.stdout.write('-' * 60)
        
        total_unidades = 0
        for i, pt in enumerate(productos_sin_lotes[:20], 1):  # Mostrar solo primeros 20
            sucursal_nombre = pt.producto.sucursal.alias if pt.producto.sucursal else "N/A"
            self.stdout.write(
                f'{i:3}. {pt.producto.articulo} - Talla {pt.talla} | '
                f'Stock: {pt.stock} | Costo: ${pt.producto.costo:,} | '
                f'Sucursal: {sucursal_nombre}'
            )
            total_unidades += pt.stock

        if len(productos_sin_lotes) > 20:
            self.stdout.write(f'... y {len(productos_sin_lotes) - 20} productos más')

        self.stdout.write(f'\n[#] Total unidades a registrar en lotes: {total_unidades:,}')

        if not ejecutar:
            self.stdout.write(self.style.WARNING(
                '\n[!] Para ejecutar la creacion de lotes, use:\n'
                '    python manage.py generar_lotes_faltantes --ejecutar'
            ))
            return

        # Ejecutar creación de lotes
        self.stdout.write(self.style.WARNING('\n[>] Iniciando creacion de lotes...'))
        
        creados = 0
        errores = 0
        
        with transaction.atomic():
            for pt in productos_sin_lotes:
                try:
                    # Buscar si hay información de recepción para obtener el DTE
                    recepcion = None
                    dte = None
                    
                    # Intentar encontrar recepción asociada
                    recepcion_qs = Productos_Recepcionados.objects.filter(
                        producto_talla=pt
                    ).select_related('dte').first()
                    
                    if recepcion_qs and recepcion_qs.dte:
                        dte = recepcion_qs.dte

                    # Crear el lote FIFO
                    lote = LoteProducto.objects.create(
                        producto_talla=pt,
                        cantidad_inicial=pt.stock,
                        cantidad_disponible=pt.stock,
                        costo_unitario=pt.producto.costo or 0,
                        sobreprecio_unitario=pt.producto.sobreprecio or 0,
                        precio_venta_unitario=pt.producto.precioventa or 0,
                        dte=dte,
                        activo=True,
                        agotado=False,
                        observaciones=f'Lote generado automáticamente para producto existente. Stock inicial: {pt.stock}'
                    )
                    
                    creados += 1
                    
                    if creados % 100 == 0:
                        self.stdout.write(f'  Procesados: {creados}/{len(productos_sin_lotes)}...')
                        
                except Exception as e:
                    errores += 1
                    self.stdout.write(self.style.ERROR(
                        f'[X] Error en {pt.producto.articulo} - Talla {pt.talla}: {str(e)}'
                    ))

        # Resumen final
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS(f'[OK] Lotes creados exitosamente: {creados}'))
        if errores:
            self.stdout.write(self.style.ERROR(f'[X] Errores: {errores}'))
        self.stdout.write('=' * 60)
        
        self.stdout.write(self.style.SUCCESS(
            '\n[OK] Proceso completado! Los productos ahora tienen lotes FIFO.'
        ))

