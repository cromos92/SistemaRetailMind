"""Corrige el estado de los arqueos que un depósito marcó mal.

Los tres endpoints de depósito decidían CERRADO / CON_DIFERENCIAS con
`ArqueoCaja.diferencia_efectivo_real`, que resta el teórico DOS veces en cuanto
existe un depósito::

    efectivo_en_caja         = físico - depósitos - fondo_fijo
    diferencia_efectivo_real = efectivo_en_caja - teórico      # <-- doble resta

Como el conteo se hace ANTES de depositar, un día perfectamente cuadrado
quedaba marcado CON_DIFERENCIAS por el solo hecho de haber depositado. El
código ya está corregido (`_reevaluar_estado_arqueo_por_deposito`), pero las
filas que se escribieron mal siguen mal: este comando las vuelve a evaluar con
el criterio correcto (`diferencia_efectivo`, la "Dif. Conteo" que muestra
Revisión de Arqueos).

SEGURO POR DEFECTO: sin `--aplicar` solo informa, no escribe nada.

NO toca arqueos en `REVISADO`, `DEPOSITO_CONFIRMADO` ni `DEPOSITO_DECLARADO`:
esos estados son avance auditado del supervisor y no se pisan.

Uso::

    python manage.py corregir_estado_arqueos_por_deposito                  # informe
    python manage.py corregir_estado_arqueos_por_deposito --sucursal PAO4
    python manage.py corregir_estado_arqueos_por_deposito --aplicar
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import ArqueoCaja, ObservacionArqueo
from app.views_modulo_ventas import (
    ESTADOS_ARQUEO_RECALCULABLES_POR_DEPOSITO,
    TOLERANCIA_ARQUEO_EFECTIVO,
)


def _clp(valor):
    return '$' + f'{int(valor or 0):,}'.replace(',', '.')


class Command(BaseCommand):
    help = ('Reevalúa CERRADO/CON_DIFERENCIAS de los arqueos con depósito, '
            'usando la diferencia de conteo en vez de diferencia_efectivo_real.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Escribe los cambios. Sin esto solo informa.',
        )
        parser.add_argument(
            '--sucursal', type=str, default=None,
            help='Alias de sucursal para acotar (ej: PAO4).',
        )
        parser.add_argument(
            '--desde', type=str, default=None,
            help='Fecha mínima de arqueo (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--incluir-abiertos', action='store_true',
            help=('También reevalúa arqueos en ABIERTO. Por defecto NO se '
                  'tocan: un arqueo abierto sigue en proceso y su conteo no '
                  'es definitivo, así que cerrarlo por backfill es decidir '
                  'por el cajero.'),
        )
        parser.add_argument(
            '--usuario', type=str, default=None,
            help=('Username al que se atribuye la observación de bitácora. '
                  '`ObservacionArqueo.usuario` no acepta null, así que sin '
                  'esto la corrección se aplica SIN dejar bitácora.'),
        )

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        tol = TOLERANCIA_ARQUEO_EFECTIVO

        estados = list(ESTADOS_ARQUEO_RECALCULABLES_POR_DEPOSITO)
        if not opts['incluir_abiertos']:
            estados = [e for e in estados if e != 'ABIERTO']

        qs = (
            ArqueoCaja.objects
            .filter(depositos__isnull=False, estado__in=estados)
            .select_related('sucursal')
            .distinct()
            .order_by('fecha_arqueo')
        )
        if opts['sucursal']:
            qs = qs.filter(sucursal__alias__iexact=opts['sucursal'])
        if opts['desde']:
            qs = qs.filter(fecha_arqueo__gte=opts['desde'])

        self.stdout.write(
            f'Tolerancia: {_clp(tol)}  |  Estados: {", ".join(estados)}  '
            f'|  Arqueos a revisar: {qs.count()}')
        self.stdout.write('')

        cambios = []
        for arqueo in qs:
            cuadra = (
                abs(arqueo.diferencia_efectivo or 0) <= tol
                and abs(arqueo.diferencia_transbank or 0) <= tol
            )
            correcto = 'CERRADO' if cuadra else 'CON_DIFERENCIAS'
            if correcto != arqueo.estado:
                cambios.append((arqueo, arqueo.estado, correcto))

        if not cambios:
            self.stdout.write(self.style.SUCCESS(
                'No hay arqueos con el estado mal calculado.'))
            return

        self.stdout.write(f'{"FECHA":<12} {"SUCURSAL":<10} {"ANTES":<17} '
                          f'{"AHORA":<17} {"DIF.CONTEO":>13} {"DIF.TBK":>12}')
        for arqueo, antes, ahora in cambios:
            self.stdout.write(
                f'{arqueo.fecha_arqueo:%Y-%m-%d} '
                f'{(arqueo.sucursal.alias if arqueo.sucursal_id else "-"):<10} '
                f'{antes:<17} {ahora:<17} '
                f'{_clp(arqueo.diferencia_efectivo):>13} '
                f'{_clp(arqueo.diferencia_transbank):>12}'
            )

        a_cerrado = sum(1 for _, _, d in cambios if d == 'CERRADO')
        a_dif = len(cambios) - a_cerrado
        self.stdout.write('')
        self.stdout.write(f'Total a corregir: {len(cambios)}  '
                          f'(-> CERRADO: {a_cerrado}, -> CON_DIFERENCIAS: {a_dif})')

        if not aplicar:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: no se escribió nada. Repite con --aplicar para corregir.'))
            return

        # `ObservacionArqueo.usuario` es NOT NULL: sin `--usuario` se corrige
        # igual pero sin bitácora, avisando.
        autor = None
        if opts['usuario']:
            from django.contrib.auth import get_user_model
            autor = get_user_model().objects.filter(
                username=opts['usuario']).first()
            if autor is None:
                self.stderr.write(self.style.ERROR(
                    f'Usuario "{opts["usuario"]}" no existe.'))
                return
        else:
            self.stdout.write(self.style.WARNING(
                'Sin --usuario: se corrige el estado pero NO se deja bitácora.'))

        with transaction.atomic():
            for arqueo, antes, ahora in cambios:
                ArqueoCaja.objects.filter(pk=arqueo.pk).update(estado=ahora)
                if autor is not None:
                    ObservacionArqueo.objects.create(
                        arqueo=arqueo,
                        usuario=autor,
                        tipo='SISTEMA',
                        texto=(
                            f'Estado corregido {antes} -> {ahora}. El estado se '
                            f'había calculado con `diferencia_efectivo_real`, que '
                            f'descuenta los depósitos del efectivo contado. '
                            f'Veredicto por diferencia de conteo: '
                            f'{_clp(arqueo.diferencia_efectivo)} '
                            f'(Transbank {_clp(arqueo.diferencia_transbank)}).'
                        ),
                        visible_para_cajera=True,
                    )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{len(cambios)} arqueo(s) corregido(s).'))
