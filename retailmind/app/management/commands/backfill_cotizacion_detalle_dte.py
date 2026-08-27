"""
Rellena `Dte_Productos.cotizacion_detalle_id` en los DTE emitidos ANTES de que
ese campo existiera.

Para qué sirve
--------------
Cuando se completa un despacho diferido, `asignar_sku_pendiente` cierra la línea
del DTE buscándola por `cotizacion_detalle_id`:

    Dte_Productos.objects.filter(
        dte=dte, cotizacion_detalle_id=detalle.id, es_pendiente_despacho=True
    ).update(productoTalla=..., costo=costo_ponderado, ...)

Si el DTE es anterior a la migración que agregó el campo, ese filtro no
encuentra nada: el código loguea "sin línea de DTE para completar" y sigue. La
línea queda con `productoTalla=None` y **`costo=0` para siempre**, así que el
margen de ese documento sale inflado y el costeo del período miente.

Cómo cruza
----------
Solo mira DTE que tengan cotización enlazada (`Cotizacion_Empresa.dte`). Para
cada línea sin `cotizacion_detalle_id`, busca el ítem de la cotización que
calce por:

    1. descripción exacta (normalizada: sin tildes de espacio, mayúsculas)
    2. cantidad
    3. precio unitario

Solo escribe cuando el match es **único en ambos sentidos**: un solo ítem
candidato para la línea y una sola línea candidata para el ítem. Si hay
ambigüedad (dos líneas iguales en descripción, cantidad y precio) la deja sin
tocar y la reporta: adivinar acá reescribiría el costo de la línea equivocada.

NO modifica costos ni stock: solo escribe `cotizacion_detalle_id`.

Uso:
    python manage.py backfill_cotizacion_detalle_dte                 # dry-run
    python manage.py backfill_cotizacion_detalle_dte --cotizacion COT-202607-0001
    python manage.py backfill_cotizacion_detalle_dte --apply
"""

import logging
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Cotizacion_Empresa, Dte_Productos

logger = logging.getLogger('app')


def _norm(texto):
    """Descripción normalizada para comparar: mayúsculas y espacios colapsados."""
    return re.sub(r'\s+', ' ', (texto or '').strip()).upper()


def _clave(descripcion, cantidad, precio):
    return (_norm(descripcion), int(cantidad or 0), int(round(float(precio or 0))))


class Command(BaseCommand):
    help = (
        'Rellena Dte_Productos.cotizacion_detalle_id en DTE de cotizaciones '
        'anteriores al campo. Por defecto dry-run; usa --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios (sin el flag solo informa).')
        parser.add_argument('--cotizacion', type=str, default=None,
                            help='Limitar a un numero_cotizacion.')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Limitar a una sucursal.')

    def handle(self, *args, **options):
        aplicar = options['apply']

        qs = (
            Cotizacion_Empresa.objects
            .filter(dte__isnull=False)
            .select_related('dte', 'sucursal')
            .prefetch_related('items')
            .order_by('id')
        )
        if options['cotizacion']:
            qs = qs.filter(numero_cotizacion__iexact=options['cotizacion'].strip())
        if options['sucursal']:
            qs = qs.filter(sucursal_id=options['sucursal'])

        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no se escribe nada)'
        self.stdout.write(self.style.WARNING(f'=== {modo} ===\n'))

        tot_cot = tot_lineas = tot_match = tot_ambiguas = tot_sin_match = 0

        for cot in qs.iterator():
            lineas = list(
                Dte_Productos.objects
                .filter(dte_id=cot.dte_id, cotizacion_detalle_id__isnull=True)
            )
            if not lineas:
                continue

            tot_cot += 1
            tot_lineas += len(lineas)

            # Índices por (descripción, cantidad, precio) en los dos sentidos.
            # El match tiene que ser único en ambos para escribir.
            items_por_clave = defaultdict(list)
            for it in cot.items.all():
                items_por_clave[_clave(it.descripcion, it.cantidad, it.precio_unitario)].append(it)

            lineas_por_clave = defaultdict(list)
            for ln in lineas:
                lineas_por_clave[_clave(ln.descripcion, ln.stock, ln.precio)].append(ln)

            resueltas = []
            ambiguas = []
            sin_match = []
            for ln in lineas:
                clave = _clave(ln.descripcion, ln.stock, ln.precio)
                candidatos = items_por_clave.get(clave, [])
                competidoras = lineas_por_clave.get(clave, [])
                if len(candidatos) == 1 and len(competidoras) == 1:
                    resueltas.append((ln, candidatos[0]))
                elif candidatos:
                    ambiguas.append((ln, len(candidatos), len(competidoras)))
                else:
                    sin_match.append(ln)

            tot_match += len(resueltas)
            tot_ambiguas += len(ambiguas)
            tot_sin_match += len(sin_match)

            self.stdout.write(
                f'{cot.numero_cotizacion} ({cot.sucursal.alias if cot.sucursal else "?"}) '
                f'· {cot.dte.tipo_documento} #{cot.dte.numero_documento} '
                f'· líneas sin enlace: {len(lineas)} '
                f'→ resueltas {len(resueltas)}, ambiguas {len(ambiguas)}, '
                f'sin match {len(sin_match)}'
            )
            for ln, n_items, n_lineas in ambiguas:
                self.stdout.write(self.style.NOTICE(
                    f'    ~ AMBIGUA "{(ln.descripcion or "")[:40]}" '
                    f'x{ln.stock} ${int(ln.precio or 0)} '
                    f'({n_items} ítems / {n_lineas} líneas iguales) — se deja sin tocar'
                ))
            for ln in sin_match:
                self.stdout.write(self.style.NOTICE(
                    f'    ~ SIN MATCH "{(ln.descripcion or "")[:40]}" '
                    f'x{ln.stock} ${int(ln.precio or 0)}'
                ))

            if not aplicar or not resueltas:
                continue

            try:
                with transaction.atomic():
                    for ln, item in resueltas:
                        Dte_Productos.objects.filter(
                            pk=ln.pk, cotizacion_detalle_id__isnull=True
                        ).update(cotizacion_detalle_id=item.id)
                logger.info(
                    'Backfill cotizacion_detalle_id cotizacion=%s lineas=%s',
                    cot.numero_cotizacion, len(resueltas),
                )
            except Exception:
                logger.exception(
                    'Error en backfill de cotizacion_detalle_id cotizacion=%s',
                    cot.numero_cotizacion,
                )
                self.stdout.write(self.style.ERROR(
                    f'    ERROR escribiendo {cot.numero_cotizacion} (ver logs)'
                ))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('=== Resumen ==='))
        self.stdout.write(
            f'Cotizaciones con líneas sin enlace: {tot_cot}\n'
            f'Líneas revisadas:                   {tot_lineas}\n'
            f'  · resueltas (match único):        {tot_match}\n'
            f'  · ambiguas (se dejan):            {tot_ambiguas}\n'
            f'  · sin match (se dejan):           {tot_sin_match}'
        )
        if tot_lineas == 0:
            self.stdout.write(self.style.SUCCESS(
                'Todas las líneas de DTE de cotizaciones ya tienen enlace.'
            ))
        elif not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nDry-run: volvé a correr con --apply para escribir.'
            ))
