"""
Diagnóstico READ-ONLY del impacto de contar `DESPACHO_COTIZACION` como VENTA.

El problema
-----------
El despacho diferido de una cotización (asignar SKU después de facturar) saca
stock con `concepto='DESPACHO_COTIZACION'`. Ese concepto estaba en
`CONCEPTOS_PERDIDA` — junto a robo, deterioro y donación — y AUSENTE de
`CONCEPTOS_VENTA`. O sea: mercadería facturada, cobrada y entregada que ningún
reporte contaba como venta, y que la predicción de compras leía como merma.

`CONCEPTOS_PERDIDA` no tiene consumidores reales (solo se cita en un
comentario), así que el daño concreto venía de la AUSENCIA en `CONCEPTOS_VENTA`,
que sí consumen dashboards, reportes, predicción de compras y la inteligencia
de compra.

Qué mide este comando
---------------------
Cuánto se mueve al reclasificarlo: unidades, plata a precio de venta, cuántas
cotizaciones y sucursales, y el desglose por mes para ver si el cambio afecta
períodos ya reportados.

NO modifica datos. Uso:

    python manage.py medir_impacto_despacho_cotizacion
    python manage.py medir_impacto_despacho_cotizacion --desde 2026-01-01
    python manage.py medir_impacto_despacho_cotizacion --sucursal NICK1
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum, F, IntegerField
from django.db.models.functions import Coalesce, TruncMonth

from app.models import Movimientos_Producto

CONCEPTO = 'DESPACHO_COTIZACION'


class Command(BaseCommand):
    help = (
        'Mide cuántas unidades y cuánta plata cambian de bucket al contar '
        'DESPACHO_COTIZACION como venta. Solo lectura.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--desde', type=str, default=None,
                            help='Fecha mínima YYYY-MM-DD.')
        parser.add_argument('--hasta', type=str, default=None,
                            help='Fecha máxima YYYY-MM-DD.')
        parser.add_argument('--sucursal', type=str, default=None,
                            help='Alias de sucursal (filtra por sucursal_origen).')

    def handle(self, *args, **options):
        qs = Movimientos_Producto.objects.filter(concepto=CONCEPTO)

        if options['desde']:
            qs = qs.filter(fecha__gte=self._fecha(options['desde'], '--desde'))
        if options['hasta']:
            qs = qs.filter(fecha__lte=self._fecha(options['hasta'], '--hasta'))
        if options['sucursal']:
            qs = qs.filter(sucursal_origen__alias__iexact=options['sucursal'])

        total = qs.count()
        if not total:
            self.stdout.write(self.style.SUCCESS(
                f'No hay movimientos con concepto {CONCEPTO} en el rango pedido. '
                f'Reclasificarlo no cambia ningún número histórico.'
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== Impacto de reclasificar {CONCEPTO} → venta ==='
        ))

        # --- Egresos vs reversas -------------------------------------------
        # El despacho saca stock (EGRESO, cantidad negativa) y
        # `revertir_sku_despachado` lo devuelve (INGRESO, positiva). El NETO es
        # lo realmente entregado: es lo que pasaría a contar como venta.
        por_tipo = (
            qs.values('tipo_movimiento')
            .annotate(n=Count('id'), uds=Sum('cantidad'))
            .order_by('tipo_movimiento')
        )
        self.stdout.write('\nMovimientos por tipo:')
        for r in por_tipo:
            self.stdout.write(
                f'  {r["tipo_movimiento"]:<10} n={r["n"]:<6} unidades={r["uds"]}'
            )

        neto_uds = qs.aggregate(t=Sum('cantidad'))['t'] or 0
        entregadas = max(0, -int(neto_uds))
        self.stdout.write(self.style.WARNING(
            f'\n  NETO entregado (lo que pasaría a contarse como venta): '
            f'{entregadas} unidades'
        ))

        # --- Plata ----------------------------------------------------------
        # `precio` es el precio de venta unitario guardado en el movimiento.
        monto = qs.aggregate(
            t=Coalesce(Sum(F('cantidad') * F('precio'), output_field=IntegerField()), 0)
        )['t'] or 0
        costo = qs.aggregate(
            t=Coalesce(Sum(F('cantidad') * F('costo'), output_field=IntegerField()), 0)
        )['t'] or 0
        self.stdout.write(
            f'  Valorizado a precio de venta: ${abs(int(monto)):,}'.replace(',', '.')
        )
        self.stdout.write(
            f'  Valorizado a costo:           ${abs(int(costo)):,}'.replace(',', '.')
        )

        # --- Cobertura ------------------------------------------------------
        cotizaciones = (
            qs.exclude(referencia_externa__isnull=True)
            .exclude(referencia_externa='')
            .values('referencia_externa').distinct().count()
        )
        sucursales = (
            qs.exclude(sucursal_origen__isnull=True)
            .values('sucursal_origen__alias').distinct().count()
        )
        self.stdout.write(
            f'\n  Cotizaciones involucradas: {cotizaciones}  |  Sucursales: {sucursales}'
        )

        # --- Desglose mensual ------------------------------------------------
        # Lo que importa para decidir: si todo cae en meses ya cerrados y
        # reportados, el cambio reescribe historia que alguien ya miró.
        self.stdout.write('\nPor mes (unidades netas entregadas y monto):')
        por_mes = (
            qs.annotate(mes=TruncMonth('fecha'))
            .values('mes')
            .annotate(
                uds=Sum('cantidad'),
                monto=Coalesce(Sum(F('cantidad') * F('precio'), output_field=IntegerField()), 0),
                n=Count('id'),
            )
            .order_by('mes')
        )
        for r in por_mes:
            mes = r['mes'].strftime('%Y-%m') if r['mes'] else 'sin fecha'
            uds = max(0, -int(r['uds'] or 0))
            mnt = abs(int(r['monto'] or 0))
            self.stdout.write(
                f'  {mes}   uds={uds:>6}   ${mnt:>12,}   movs={r["n"]}'.replace(',', '.')
            )

        # --- Por sucursal ----------------------------------------------------
        self.stdout.write('\nPor sucursal:')
        por_suc = (
            qs.values('sucursal_origen__alias')
            .annotate(
                uds=Sum('cantidad'),
                monto=Coalesce(Sum(F('cantidad') * F('precio'), output_field=IntegerField()), 0),
            )
            .order_by('sucursal_origen__alias')
        )
        for r in por_suc:
            alias = r['sucursal_origen__alias'] or 'SIN SUCURSAL'
            uds = max(0, -int(r['uds'] or 0))
            mnt = abs(int(r['monto'] or 0))
            self.stdout.write(
                f'  {alias:<12} uds={uds:>6}   ${mnt:>12,}'.replace(',', '.')
            )

        self.stdout.write(self.style.NOTICE(
            '\nQué cambia si se reclasifica (ya aplicado en constants_kardex):\n'
            '  · dashboards y reportes de venta suman estas unidades\n'
            '  · la predicción de compras deja de leerlas como merma\n'
            '  · los reportes de pérdidas dejan de incluirlas\n'
            'Los números de arriba son exactamente la magnitud del corrimiento.\n'
        ))

    def _fecha(self, texto, flag):
        from django.core.management.base import CommandError
        try:
            return datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'{flag} debe ser YYYY-MM-DD (recibí "{texto}").')
