from django.core.management.base import BaseCommand
from django.db.models import Count
from app.models import Producto_Talla, Producto


class Command(BaseCommand):
    help = 'Diagnostica y opcionalmente consolida tallas duplicadas en productos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--consolidar',
            action='store_true',
            help='Consolida los duplicados sumando stocks y eliminando registros extras',
        )
        parser.add_argument(
            '--producto-id',
            type=int,
            help='Analiza solo un producto específico por su ID',
        )

    def handle(self, *args, **options):
        consolidar = options['consolidar']
        producto_id = options.get('producto_id')

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('DIAGNÓSTICO DE TALLAS DUPLICADAS'))
        self.stdout.write(self.style.SUCCESS('=' * 60))

        # Buscar productos con tallas duplicadas
        if producto_id:
            productos_con_duplicados = Producto_Talla.objects.filter(
                producto_id=producto_id
            ).values('producto_id', 'talla').annotate(
                cantidad=Count('id')
            ).filter(cantidad__gt=1)
        else:
            productos_con_duplicados = Producto_Talla.objects.values(
                'producto_id', 'talla'
            ).annotate(
                cantidad=Count('id')
            ).filter(cantidad__gt=1)

        if not productos_con_duplicados.exists():
            self.stdout.write(self.style.SUCCESS('\n✅ No se encontraron tallas duplicadas.'))
            return

        total_duplicados = productos_con_duplicados.count()
        self.stdout.write(self.style.WARNING(f'\n⚠️ Se encontraron {total_duplicados} combinaciones producto-talla con duplicados:\n'))

        productos_afectados = set()
        total_registros_extra = 0

        for dup in productos_con_duplicados:
            producto = Producto.objects.get(id=dup['producto_id'])
            productos_afectados.add(producto.id)
            registros_extra = dup['cantidad'] - 1
            total_registros_extra += registros_extra

            self.stdout.write(f'  📦 Producto: {producto.articulo} (ID: {producto.id})')
            self.stdout.write(f'     Talla: {dup["talla"]} - {dup["cantidad"]} registros ({registros_extra} extra)')

            # Mostrar detalle de los duplicados
            tallas_dup = Producto_Talla.objects.filter(
                producto=producto,
                talla=dup['talla']
            ).order_by('id')

            for pt in tallas_dup:
                self.stdout.write(f'       - ID: {pt.id}, SKU: {pt.sku}, Stock: {pt.stock}')

            if consolidar:
                # Consolidar: mantener el primero, sumar stocks, eliminar el resto
                tallas_list = list(tallas_dup)
                principal = tallas_list[0]
                stock_total = sum(t.stock for t in tallas_list)

                # Actualizar el principal con el stock total
                principal.stock = stock_total
                principal.save()

                # Eliminar los duplicados
                ids_eliminar = [t.id for t in tallas_list[1:]]
                Producto_Talla.objects.filter(id__in=ids_eliminar).delete()

                self.stdout.write(self.style.SUCCESS(
                    f'       ✅ Consolidado: Stock total = {stock_total}, eliminados {len(ids_eliminar)} registros'
                ))

            self.stdout.write('')

        self.stdout.write(self.style.WARNING(f'\n📊 RESUMEN:'))
        self.stdout.write(f'   - Productos afectados: {len(productos_afectados)}')
        self.stdout.write(f'   - Combinaciones duplicadas: {total_duplicados}')
        self.stdout.write(f'   - Registros extra a eliminar: {total_registros_extra}')

        if not consolidar:
            self.stdout.write(self.style.WARNING(
                '\n💡 Para consolidar los duplicados, ejecute con --consolidar:'
            ))
            self.stdout.write('   python manage.py diagnosticar_tallas_duplicadas --consolidar')
            if producto_id:
                self.stdout.write(f'   python manage.py diagnosticar_tallas_duplicadas --consolidar --producto-id {producto_id}')
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Duplicados consolidados correctamente.'))

