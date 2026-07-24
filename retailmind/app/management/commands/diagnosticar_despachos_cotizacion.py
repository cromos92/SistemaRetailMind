"""
Diagnostico READ-ONLY de la cuadratura de despachos de cotizaciones facturadas.

Lista, por cotizacion FACTURADA, las unidades facturadas vs las despachadas
(salidas de stock: con SKU al facturar + despachos diferidos post-factura) y
marca las que tienen descuadre — incluidas las historicas que el flujo viejo
cerro en falso (despacho parcial que marcaba el item como completado).

NO modifica datos. Uso:

    python manage.py diagnosticar_despachos_cotizacion
    python manage.py diagnosticar_despachos_cotizacion --solo-descuadre
    python manage.py diagnosticar_despachos_cotizacion --sucursal NICK1
"""
from django.core.management.base import BaseCommand

from app.models import Cotizacion_Empresa


class Command(BaseCommand):
    help = ('Lista cotizaciones facturadas con su cuadratura de despacho '
            '(uds facturadas vs despachadas). Solo lectura.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--solo-descuadre', action='store_true',
            help='Mostrar solo cotizaciones con unidades pendientes de despacho',
        )
        parser.add_argument(
            '--sucursal', type=str, default=None,
            help='Filtrar por alias de sucursal (ej: NICK1)',
        )

    def handle(self, *args, **options):
        qs = (
            Cotizacion_Empresa.objects
            .filter(facturada=True)
            .select_related('sucursal', 'cliente', 'despacho_validado_por')
            .prefetch_related('items__skus_asociados')
            .order_by('sucursal__alias', '-fecha_facturacion')
        )
        if options['sucursal']:
            qs = qs.filter(sucursal__alias__iexact=options['sucursal'])

        total = 0
        descuadradas = 0
        completas_sin_ok = 0
        validadas = 0

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{"Cotizacion":<16} {"Sucursal":<8} {"Cliente":<28} '
            f'{"Fact.":>6} {"Desp.":>6} {"Pend.":>6}  Estado'
        ))
        self.stdout.write('-' * 96)

        for cot in qs.iterator():
            facturadas = cot.unidades_facturadas
            pendientes = cot.unidades_pendientes_despacho
            despachadas = facturadas - pendientes
            total += 1

            if pendientes > 0:
                descuadradas += 1
                # estado_despacho stale == COMPLETADO delata un cierre en falso
                # del flujo viejo (parcial que se marco como completado).
                stale = (cot.estado_despacho == Cotizacion_Empresa.DESPACHO_COMPLETADO)
                estado = self.style.WARNING(
                    'DESCUADRE' + (' (cerrado en falso por flujo viejo)' if stale else '')
                )
            elif cot.despacho_validado:
                validadas += 1
                validador = (
                    cot.despacho_validado_por.get_full_name()
                    or cot.despacho_validado_por.username
                ) if cot.despacho_validado_por else '?'
                estado = self.style.SUCCESS(f'VALIDADO por {validador}')
            else:
                completas_sin_ok += 1
                estado = 'completo, falta OK admin'

            if options['solo_descuadre'] and pendientes == 0:
                continue

            self.stdout.write(
                f'{cot.numero_cotizacion:<16} '
                f'{(cot.sucursal.alias if cot.sucursal else "?"):<8} '
                f'{(cot.cliente.nombre or "")[:27]:<28} '
                f'{facturadas:>6} {despachadas:>6} {pendientes:>6}  {estado}'
            )

            # Detalle de los items con saldo (solo cuando hay descuadre)
            if pendientes > 0:
                for item in cot.items.all():
                    saldo = item.unidades_pendientes_despacho
                    if saldo > 0:
                        self.stdout.write(
                            f'{"":<16} > item #{item.numero_linea}: '
                            f'"{item.descripcion[:45]}" '
                            f'facturado {item.cantidad}, '
                            f'despachado {item.unidades_despachadas_post_factura}, '
                            f'PENDIENTE {saldo}'
                        )

        self.stdout.write('-' * 96)
        self.stdout.write(
            f'Total facturadas: {total} | '
            + self.style.WARNING(f'con descuadre: {descuadradas}') + ' | '
            + f'completas sin OK: {completas_sin_ok} | '
            + self.style.SUCCESS(f'validadas: {validadas}')
        )
        if descuadradas:
            self.stdout.write(self.style.WARNING(
                '\n>> Las cotizaciones con DESCUADRE tienen unidades facturadas sin salida '
                'de stock. Darles salida desde /app/cotizaciones/ con el boton "Asignar SKU" '
                '(el saldo pendiente aparece por item).'
            ))
