"""
Guarda permanente de integridad de inventario (Fase 5 de la auditoría).

SOLO LECTURA. Mide los indicadores de sincronía entre stock plano, lotes FIFO
y kardex, y termina con exit code 1 si algún umbral se excede — pensado para
ejecutarse diario vía cron/scheduler y alertar temprano en vez de descubrir
el descuadre meses después en un reporte.

Uso:
    python manage.py verificar_drift_inventario
    python manage.py verificar_drift_inventario --umbral-skus 100 --umbral-unidades 500
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, F, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce

from app.models import (
    CONCEPTO_MOVIMIENTO_CHOICES,
    LoteProducto,
    Movimientos_Producto,
    Producto_Talla,
)


class Command(BaseCommand):
    help = ('Verifica drift stock/lotes/kardex y conceptos fuera de catálogo. '
            'Solo lectura; exit 1 si se exceden los umbrales (para cron).')

    def add_arguments(self, parser):
        parser.add_argument('--umbral-skus', type=int, default=50,
                            help='Máximo de SKUs con drift stock↔lotes tolerado. Default: 50.')
        parser.add_argument('--umbral-unidades', type=int, default=200,
                            help='Máximo de unidades de drift absoluto tolerado. Default: 200.')

    def handle(self, *args, **opts):
        alertas = []

        # 1. Drift stock plano ↔ lotes FIFO, por SKU (una sola query anotada).
        qs = Producto_Talla.objects.annotate(
            saldo_lotes=Coalesce(
                Sum('lotes__cantidad_disponible', filter=Q(lotes__activo=True)),
                Value(0), output_field=IntegerField(),
            )
        )
        descuadrados = qs.exclude(stock=F('saldo_lotes'))
        # Los SKUs con stock negativo no pueden cuadrar (lotes no bajan de 0):
        # se reportan aparte y no cuentan contra el umbral.
        negativos = descuadrados.filter(stock__lt=0).count()
        drift = descuadrados.filter(stock__gte=0).aggregate(
            n=Count('id'),
            unidades=Sum(F('stock') - F('saldo_lotes'), output_field=IntegerField()),
        )
        n_drift = drift['n'] or 0
        u_drift = drift['unidades'] or 0
        self.stdout.write(f"SKUs con drift stock<->lotes (stock>=0): {n_drift:,} "
                          f"(neto {u_drift:+,} u) | stock negativo: {negativos:,}")
        if n_drift > opts['umbral_skus']:
            alertas.append(f'drift lotes en {n_drift} SKUs (umbral {opts["umbral_skus"]})')

        # 2. Conceptos de kardex fuera del catálogo declarado.
        validos = {c[0] for c in CONCEPTO_MOVIMIENTO_CHOICES}
        fuera = (Movimientos_Producto.objects.exclude(concepto__in=validos)
                 .values('concepto').annotate(n=Count('id')).order_by('-n'))
        fuera = list(fuera)
        if fuera:
            detalle = ', '.join(f"{f['concepto']}={f['n']:,}" for f in fuera[:5])
            self.stdout.write(f"Conceptos fuera de catálogo: {detalle}")
            alertas.append(f'{len(fuera)} conceptos fuera de catálogo')
        else:
            self.stdout.write('Conceptos fuera de catálogo: 0')

        # 3. Coherencia tipo/signo (el save() nuevo lo garantiza hacia
        #    adelante; esto detecta escrituras que lo esquiven, p.ej. bulk).
        mal_tipados = Movimientos_Producto.objects.filter(
            Q(tipo_movimiento='INGRESO', cantidad__lt=0)
            | Q(tipo_movimiento='EGRESO', cantidad__gt=0)
        ).count()
        self.stdout.write(f'Movimientos con tipo/signo incoherente: {mal_tipados:,}')
        if mal_tipados:
            alertas.append(f'{mal_tipados} movimientos tipo/signo incoherente')

        # 4. Lotes negativos (nunca deberían existir).
        lotes_neg = LoteProducto.objects.filter(cantidad_disponible__lt=0).count()
        if lotes_neg:
            self.stdout.write(f'Lotes con cantidad_disponible NEGATIVA: {lotes_neg:,}')
            alertas.append(f'{lotes_neg} lotes negativos')

        if abs(u_drift) > opts['umbral_unidades']:
            alertas.append(f'drift de {u_drift:+,} unidades (umbral {opts["umbral_unidades"]})')

        self.stdout.write('')
        if alertas:
            self.stdout.write(self.style.ERROR('[ALERTA] ' + ' | '.join(alertas)))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('[OK] Inventario dentro de umbrales.'))
