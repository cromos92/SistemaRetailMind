"""
Comando para investigar las recepciones de las zapatillas ADIDAS específicas
que el usuario está viendo en la pantalla
"""

from django.core.management.base import BaseCommand
from app.models import (
    Productos_Recepcionados, Compras_Producto_Talla, 
    Compras_Producto, Compras, Dte
)


class Command(BaseCommand):
    help = 'Investiga las recepciones de zapatillas ADIDAS que aparecen en verGestionCompras'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write(self.style.WARNING('🔍 INVESTIGACIÓN DE RECEPCIONES - ZAPATILLAS ADIDAS'))
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write('')

        # ===============================
        # 1. BUSCAR PRODUCTO "ZAPATILLA" ADIDAS
        # ===============================
        self.stdout.write(self.style.HTTP_INFO('📊 1. Buscando productos ZAPATILLA ADIDAS...'))
        
        productos_adidas = Compras_Producto.objects.filter(
            nombre__icontains='ZAPATILLA',
            atributo1__icontains='ADIDAS'
        )
        
        if productos_adidas.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    f'   ✅ Encontrados {productos_adidas.count()} productos ZAPATILLA ADIDAS'
                )
            )
            
            for producto in productos_adidas:
                self.stdout.write('')
                self.stdout.write(f'   📦 Producto: {producto.nombre}')
                self.stdout.write(f'      ID: {producto.id}')
                self.stdout.write(f'      Descripción: {producto.descripcion}')
                self.stdout.write(f'      Marca: {producto.atributo1}')
                self.stdout.write(f'      Color: {producto.atributo2}')
                self.stdout.write(f'      Género: {producto.atributo3}')
                self.stdout.write(f'      Costo: ${producto.costo:,.0f}')
                self.stdout.write(f'      Precio Sugerido: ${producto.precioSugerido:,.0f}')
                
                # Buscar sus tallas
                tallas = Compras_Producto_Talla.objects.filter(compra_producto=producto)
                if tallas.exists():
                    self.stdout.write(f'      Tallas: {tallas.count()}')
                    
                    for talla in tallas:
                        self.stdout.write(f'         - Talla {talla.talla}: Stock {talla.stock}')
                        
                        # Buscar recepciones para esta talla
                        recepciones = Productos_Recepcionados.objects.filter(
                            compra_producto_talla=talla
                        )
                        
                        if recepciones.exists():
                            for recep in recepciones:
                                self.stdout.write(
                                    f'            ├─ Recepción ID {recep.id}: '
                                    f'{recep.stockArribado} unidades'
                                )
                                
                                if recep.dte_id:
                                    self.stdout.write(
                                        f'            │  dte_id: {recep.dte_id}'
                                    )
                                    
                                    # Intentar obtener el DTE
                                    try:
                                        dte = Dte.objects.get(id=recep.dte_id)
                                        self.stdout.write(
                                            self.style.SUCCESS(
                                                f'            │  ✅ Factura #{dte.numero_documento} '
                                                f'(${dte.monto_con_iva:,.0f})'
                                            )
                                        )
                                    except Dte.DoesNotExist:
                                        self.stdout.write(
                                            self.style.ERROR(
                                                f'            │  ❌ DTE ID {recep.dte_id} NO EXISTE'
                                            )
                                        )
                                        self.stdout.write(
                                            self.style.WARNING(
                                                f'            └─ 💡 Esto causa "Factura asociada: {recep.dte_id}"'
                                            )
                                        )
                                else:
                                    self.stdout.write('            └─ Sin factura asociada')
                        else:
                            self.stdout.write('            └─ Sin recepciones')
        else:
            self.stdout.write(
                self.style.ERROR(
                    '   ❌ No se encontraron productos ZAPATILLA ADIDAS'
                )
            )

        # ===============================
        # 2. BUSCAR TODAS LAS RECEPCIONES CON DTE_ID
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 2. Analizando todas las recepciones con factura...'))
        
        recepciones_con_dte = Productos_Recepcionados.objects.filter(
            dte_id__isnull=False
        ).select_related('compra_producto_talla__compra_producto')
        
        if recepciones_con_dte.exists():
            self.stdout.write(
                f'   Total recepciones con factura: {recepciones_con_dte.count()}'
            )
            
            # Agrupar por dte_id
            from collections import defaultdict
            por_dte = defaultdict(list)
            
            for recep in recepciones_con_dte:
                por_dte[recep.dte_id].append(recep)
            
            self.stdout.write(f'\n   DTEs únicos referenciados: {len(por_dte)}')
            
            for dte_id, recepciones in sorted(por_dte.items()):
                total_unidades = sum(r.stockArribado for r in recepciones)
                
                # Intentar obtener el DTE
                try:
                    dte = Dte.objects.get(id=dte_id)
                    self.stdout.write(
                        f'\n   ✅ Factura #{dte.numero_documento} (ID {dte_id}): '
                        f'{len(recepciones)} recepciones, {total_unidades} unidades'
                    )
                    
                    # Si es la factura 160000, mostrar detalles
                    if dte.numero_documento == 160000:
                        self.stdout.write(
                            self.style.WARNING(
                                f'      🎯 Esta es la factura #160000 que aparece como "160000 / {total_unidades}"'
                            )
                        )
                        for recep in recepciones[:5]:  # Primeras 5
                            if recep.compra_producto_talla:
                                prod = recep.compra_producto_talla.compra_producto
                                self.stdout.write(
                                    f'         - {prod.nombre} ({prod.atributo1}) '
                                    f'Talla {recep.compra_producto_talla.talla}: '
                                    f'{recep.stockArribado} unidades'
                                )
                    
                except Dte.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(
                            f'\n   ❌ DTE ID {dte_id} (INEXISTENTE): '
                            f'{len(recepciones)} recepciones, {total_unidades} unidades'
                        )
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            f'      💡 Esto causa "Factura asociada: {dte_id}" en pantalla'
                        )
                    )
                    
                    # Mostrar qué productos están afectados
                    for recep in recepciones[:5]:  # Primeras 5
                        if recep.compra_producto_talla:
                            prod = recep.compra_producto_talla.compra_producto
                            self.stdout.write(
                                f'         - {prod.nombre} ({prod.atributo1}) '
                                f'Talla {recep.compra_producto_talla.talla}: '
                                f'{recep.stockArribado} unidades'
                            )

        # ===============================
        # 3. BUSCAR DTE 22 Y 160000 ESPECÍFICAMENTE
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 3. Búsqueda específica de DTEs mencionados...'))
        
        # DTE ID 22
        self.stdout.write('\n   Buscando DTE ID 22...')
        try:
            dte_22 = Dte.objects.get(id=22)
            self.stdout.write(
                self.style.SUCCESS(
                    f'   ✅ Encontrado: Factura #{dte_22.numero_documento}'
                )
            )
        except Dte.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    '   ❌ DTE ID 22 no existe'
                )
            )
            
            # Buscar si hay recepciones que lo referencian
            recepciones_22 = Productos_Recepcionados.objects.filter(dte_id=22)
            if recepciones_22.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f'   ⚠️  Pero hay {recepciones_22.count()} recepciones que lo referencian!'
                    )
                )
            else:
                self.stdout.write('   ℹ️  No hay recepciones que lo referencien')
        
        # Factura 160000
        self.stdout.write('\n   Buscando Factura #160000...')
        dte_160000 = Dte.objects.filter(numero_documento=160000)
        if dte_160000.exists():
            for dte in dte_160000:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'   ✅ Encontrada: ID {dte.id}, Emisor: {dte.emisor.nombre}'
                    )
                )
        else:
            self.stdout.write(
                self.style.ERROR(
                    '   ❌ No existe factura con número 160000'
                )
            )

        # ===============================
        # 4. RECOMENDACIONES
        # ===============================
        self.stdout.write('\n' + self.style.WARNING('='*80))
        self.stdout.write(self.style.WARNING('💡 RECOMENDACIONES'))
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write('')
        self.stdout.write('Para solucionar "Factura asociada: 22":')
        self.stdout.write('  1. Si el DTE fue eliminado por error, restaurarlo')
        self.stdout.write('  2. O limpiar las recepciones: python manage.py diagnosticar_facturas_recepciones --reparar')
        self.stdout.write('')
        self.stdout.write('Para entender "160000 / 5":')
        self.stdout.write('  - El formato es: número_factura / total_unidades_recepcionadas')
        self.stdout.write('  - Se construye desde facturas_asociadas en la vista')
        self.stdout.write('')

