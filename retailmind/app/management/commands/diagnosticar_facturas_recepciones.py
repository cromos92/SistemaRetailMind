"""
Comando de Django para diagnosticar y reparar problemas con facturas asociadas en recepciones
Identifica:
- Recepciones con DTEs inexistentes (huérfanas)
- Recepciones con DTEs sin número de documento
- Facturas duplicadas o mal referenciadas
"""

from django.core.management.base import BaseCommand
from django.db.models import Q, Count
from app.models import Productos_Recepcionados, Dte, Compras_Producto_Talla
from collections import defaultdict


class Command(BaseCommand):
    help = 'Diagnostica problemas con facturas asociadas en recepciones de productos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reparar',
            action='store_true',
            help='Repara automáticamente los problemas encontrados (elimina referencias huérfanas)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Muestra información detallada de cada problema'
        )

    def handle(self, *args, **options):
        reparar = options['reparar']
        verbose = options['verbose']
        
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write(self.style.WARNING('🔍 DIAGNÓSTICO DE FACTURAS EN RECEPCIONES'))
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write('')

        # Contador de problemas
        problemas = {
            'dtes_huerfanos': 0,
            'dtes_sin_numero': 0,
            'recepciones_arregladas': 0,
            'dtes_encontrados': 0
        }

        # ===============================
        # 1. RECEPCIONES CON DTE_ID PERO SIN DTE (HUÉRFANAS)
        # ===============================
        self.stdout.write(self.style.HTTP_INFO('📊 1. Buscando recepciones huérfanas (con dte_id pero DTE eliminado)...'))
        
        recepciones_con_dte_id = Productos_Recepcionados.objects.filter(
            dte_id__isnull=False
        ).select_related('dte', 'compra_producto_talla__compra_producto')
        
        recepciones_huerfanas = []
        for recep in recepciones_con_dte_id:
            if not recep.dte:
                recepciones_huerfanas.append(recep)
                problemas['dtes_huerfanos'] += 1
                
                if verbose:
                    self.stdout.write(
                        self.style.ERROR(
                            f"   ❌ Recepción ID {recep.id}: dte_id={recep.dte_id} pero DTE no existe"
                        )
                    )
                    if recep.compra_producto_talla:
                        self.stdout.write(
                            f"      Producto: {recep.compra_producto_talla.compra_producto.nombre}"
                        )
                    self.stdout.write(
                        f"      Stock arribado: {recep.stockArribado}"
                    )
        
        if recepciones_huerfanas:
            self.stdout.write(
                self.style.ERROR(
                    f'\n   ⚠️  Encontradas {len(recepciones_huerfanas)} recepciones huérfanas'
                )
            )
            
            if reparar:
                self.stdout.write(self.style.WARNING('\n   🔧 Reparando recepciones huérfanas...'))
                for recep in recepciones_huerfanas:
                    recep.dte_id = None
                    recep.save()
                    problemas['recepciones_arregladas'] += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'   ✅ {len(recepciones_huerfanas)} recepciones reparadas (dte_id establecido a NULL)'
                    )
                )
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ No se encontraron recepciones huérfanas'))

        # ===============================
        # 2. DTES SIN NÚMERO DE DOCUMENTO O CON NÚMERO INVÁLIDO
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 2. Buscando DTEs sin número de documento...'))
        
        dtes_sin_numero = Dte.objects.filter(
            Q(numero_documento__isnull=True) | Q(numero_documento=0)
        )
        
        if dtes_sin_numero.exists():
            problemas['dtes_sin_numero'] = dtes_sin_numero.count()
            self.stdout.write(
                self.style.ERROR(
                    f'   ⚠️  Encontrados {problemas["dtes_sin_numero"]} DTEs sin número de documento'
                )
            )
            
            if verbose:
                for dte in dtes_sin_numero[:10]:  # Mostrar solo los primeros 10
                    self.stdout.write(
                        self.style.ERROR(
                            f"      ❌ DTE ID {dte.id}: tipo={dte.tipo_documento}, "
                            f"emisor={dte.emisor.nombre if dte.emisor else 'N/A'}, "
                            f"fecha={dte.fecha_emision}"
                        )
                    )
                
                if problemas['dtes_sin_numero'] > 10:
                    self.stdout.write(f"      ... y {problemas['dtes_sin_numero'] - 10} más")
            
            if reparar:
                self.stdout.write(
                    self.style.WARNING(
                        '\n   ⚠️  Los DTEs sin número requieren corrección manual'
                    )
                )
                self.stdout.write(
                    '   💡 Sugerencia: Asigna números de documento válidos a estos DTEs'
                )
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ Todos los DTEs tienen número de documento'))

        # ===============================
        # 3. ESTADÍSTICAS DE RECEPCIONES POR DTE
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 3. Análisis de recepciones por factura...'))
        
        # Agrupar recepciones por DTE
        recepciones_por_dte = defaultdict(list)
        for recep in Productos_Recepcionados.objects.filter(
            dte__isnull=False
        ).select_related('dte', 'compra_producto_talla__compra_producto'):
            recepciones_por_dte[recep.dte_id].append(recep)
        
        if recepciones_por_dte:
            self.stdout.write(
                f'   📦 Total de DTEs con recepciones: {len(recepciones_por_dte)}'
            )
            
            # Mostrar las facturas más usadas
            facturas_mas_usadas = sorted(
                recepciones_por_dte.items(),
                key=lambda x: len(x[1]),
                reverse=True
            )[:5]
            
            self.stdout.write('\n   🏆 Top 5 facturas más usadas:')
            for dte_id, recepciones in facturas_mas_usadas:
                try:
                    dte = Dte.objects.get(id=dte_id)
                    self.stdout.write(
                        f'      • Factura #{dte.numero_documento} (ID {dte_id}): '
                        f'{len(recepciones)} recepciones, '
                        f'{sum(r.stockArribado for r in recepciones)} unidades'
                    )
                except Dte.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(
                            f'      • Factura ID {dte_id} (INEXISTENTE): '
                            f'{len(recepciones)} recepciones'
                        )
                    )

        # ===============================
        # 4. VERIFICAR FACTURAS ESPECÍFICAS
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 4. Verificando factura específica mencionada...'))
        
        # Buscar la factura ID 22 que mencionó el usuario
        try:
            dte_22 = Dte.objects.get(id=22)
            self.stdout.write(
                self.style.SUCCESS(
                    f'   ✅ DTE ID 22 encontrado: Factura #{dte_22.numero_documento}'
                )
            )
            self.stdout.write(
                f'      Tipo: {dte_22.tipo_documento}'
            )
            self.stdout.write(
                f'      Emisor: {dte_22.emisor.nombre if dte_22.emisor else "N/A"}'
            )
            self.stdout.write(
                f'      Fecha: {dte_22.fecha_emision}'
            )
            self.stdout.write(
                f'      Monto: ${dte_22.monto_con_iva:,.0f}'
            )
            
            # Ver cuántas recepciones tienen este DTE
            recepciones_dte_22 = Productos_Recepcionados.objects.filter(dte_id=22)
            if recepciones_dte_22.exists():
                total_unidades = sum(r.stockArribado for r in recepciones_dte_22)
                self.stdout.write(
                    f'      Recepciones asociadas: {recepciones_dte_22.count()} '
                    f'({total_unidades} unidades total)'
                )
            else:
                self.stdout.write('      No tiene recepciones asociadas')
                
        except Dte.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    '   ❌ DTE ID 22 NO EXISTE en la base de datos'
                )
            )
            self.stdout.write(
                '   💡 Esto explica por qué aparece "Factura asociada: 22" sin número'
            )

        # ===============================
        # 5. BUSCAR FACTURA 160000
        # ===============================
        self.stdout.write('\n' + self.style.HTTP_INFO('📊 5. Buscando factura #160000...'))
        
        dte_160000 = Dte.objects.filter(numero_documento=160000)
        if dte_160000.exists():
            for dte in dte_160000:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'   ✅ Factura #160000 encontrada (ID {dte.id})'
                    )
                )
                self.stdout.write(
                    f'      Tipo: {dte.tipo_documento}'
                )
                self.stdout.write(
                    f'      Emisor: {dte.emisor.nombre if dte.emisor else "N/A"}'
                )
                self.stdout.write(
                    f'      Receptor: {dte.receptor.nombre if dte.receptor else "N/A"}'
                )
                self.stdout.write(
                    f'      Fecha: {dte.fecha_emision}'
                )
                
                # Ver recepciones
                recepciones = Productos_Recepcionados.objects.filter(dte_id=dte.id)
                if recepciones.exists():
                    total = sum(r.stockArribado for r in recepciones)
                    self.stdout.write(
                        f'      Recepciones: {recepciones.count()} registros, {total} unidades'
                    )
                    
                    if verbose and total == 5:
                        self.stdout.write(
                            self.style.WARNING(
                            f'      💡 Esto coincide con "160000 / 5" que viste en pantalla'
                            )
                        )
        else:
            self.stdout.write(
                self.style.ERROR(
                    '   ❌ Factura #160000 NO encontrada'
                )
            )

        # ===============================
        # RESUMEN FINAL
        # ===============================
        self.stdout.write('\n' + self.style.WARNING('='*80))
        self.stdout.write(self.style.WARNING('📋 RESUMEN DEL DIAGNÓSTICO'))
        self.stdout.write(self.style.WARNING('='*80))
        
        self.stdout.write(f'Recepciones huérfanas (dte_id sin DTE): {problemas["dtes_huerfanos"]}')
        self.stdout.write(f'DTEs sin número de documento: {problemas["dtes_sin_numero"]}')
        if reparar:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Recepciones reparadas: {problemas["recepciones_arregladas"]}'
                )
            )
        
        self.stdout.write('')
        
        if problemas['dtes_huerfanos'] > 0 or problemas['dtes_sin_numero'] > 0:
            if not reparar:
                self.stdout.write(
                    self.style.WARNING(
                        '💡 Para reparar automáticamente, ejecuta: '
                        'python manage.py diagnosticar_facturas_recepciones --reparar'
                    )
                )
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('🔧 RECOMENDACIONES:'))
            if problemas['dtes_huerfanos'] > 0:
                self.stdout.write(
                    '   1. Las recepciones huérfanas pueden ser eliminadas o mantener dte_id=NULL'
                )
            if problemas['dtes_sin_numero'] > 0:
                self.stdout.write(
                    '   2. Asigna números de documento válidos a los DTEs sin número'
                )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ ¡No se encontraron problemas! La base de datos está en buen estado.')
            )
        
        self.stdout.write('')

