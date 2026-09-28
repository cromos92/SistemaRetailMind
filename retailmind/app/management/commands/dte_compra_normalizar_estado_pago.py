"""
Normaliza la grafía de Dte.estado_pago en los DTE de COMPRA (hallazgo B3-02).

Históricamente convivían 'PENDIENTE'/'Pendiente', 'PAGADO'/'Pagado', 'Abonado'
y 'Parcial'. Desde sep-2026 las vistas de pagos de compras escriben el valor
canónico (app/utils_estado_pago.py: PENDIENTE / PARCIAL / PAGADO) y leen sin
distinguir mayúsculas. Este comando reescribe lo histórico al valor canónico.

Por defecto SOLO cambia la grafía (mismo significado). Con --recalcular además
recalcula el estado desde los pagos (Dte_Detalle_Pago) para los documentos
cuyo estado no calza con lo pagado; esos casos se listan siempre para revisión.

IMPORTANTE antes de --apply: todo lector de estado_pago debe ser insensible a
mayúsculas (hoy views_modulo_compras.obtener_resumen_pendientes_anio y el JS de
gestionDteCompras comparan con 'Pendiente'/'Parcial'/'Pagado' exactos; ver
pendientes del coordinador). No toca DTE de venta.

Uso:
    python manage.py dte_compra_normalizar_estado_pago              # dry-run
    python manage.py dte_compra_normalizar_estado_pago --apply
    python manage.py dte_compra_normalizar_estado_pago --recalcular --apply
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from app.models import Dte, Dte_Detalle_Pago
from app.utils_estado_pago import calcular_estado_pago, normalizar_estado_pago


class Command(BaseCommand):
    help = 'Normaliza estado_pago de DTE de COMPRA a PENDIENTE/PARCIAL/PAGADO (dry-run por defecto).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Aplica los cambios.')
        parser.add_argument('--recalcular', action='store_true',
                            help='Además recalcula desde los pagos los que no calzan.')
        parser.add_argument('--empresa', type=int, default=None, help='Limita a una empresa receptora.')
        parser.add_argument('--muestra', type=int, default=15, help='Filas de ejemplo de inconsistencias.')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        recalcular = opts['recalcular']
        qs = Dte.objects.filter(tipo_transaccion='COMPRA')
        if opts['empresa']:
            qs = qs.filter(receptor_id=opts['empresa'])

        pagado_por_dte = dict(
            Dte_Detalle_Pago.objects.filter(dte__in=qs)
            .values('dte_id').annotate(t=Sum('monto')).values_list('dte_id', 't')
        )

        transiciones = Counter()
        cambios = {}          # id -> nuevo valor
        inconsistentes = []   # (dte, estado_normalizado, estado_segun_pagos)
        for d in qs.only('id', 'estado_pago', 'monto_con_iva', 'tipo_documento', 'numero_documento').iterator():
            actual = d.estado_pago or ''
            normal = normalizar_estado_pago(actual)
            # VENCIDO no se escribe en compras; se deja tal cual.
            segun_pagos = calcular_estado_pago(pagado_por_dte.get(d.id, 0), d.monto_con_iva)
            if normal != 'VENCIDO' and d.tipo_documento != 'NOTA DE CREDITO' and normal != segun_pagos:
                # Sin pagos y 'PAGADO' puede ser pago histórico fuera del sistema:
                # solo se recalcula con --recalcular y queda listado.
                inconsistentes.append((d, normal, segun_pagos, pagado_por_dte.get(d.id, 0)))
                if recalcular:
                    normal = segun_pagos
            if normal != actual:
                transiciones[(actual, normal)] += 1
                cambios[d.id] = normal

        modo = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(f'[{modo}] DTE de COMPRA revisados: {qs.count()} — a cambiar: {len(cambios)}')
        for (antes, despues), n in sorted(transiciones.items(), key=lambda x: -x[1]):
            self.stdout.write(f'  {antes!r:>12} -> {despues!r:<12} {n}')

        self.stdout.write(f'Estado que no calza con los pagos: {len(inconsistentes)}'
                          + (' (se recalculan)' if recalcular else ' (solo informe; usa --recalcular)'))
        for d, normal, segun, pagado in inconsistentes[:opts['muestra']]:
            self.stdout.write(
                f'  DTE #{d.id} {d.tipo_documento} N°{d.numero_documento} monto {d.monto_con_iva} '
                f'pagado {pagado}: estado {d.estado_pago!r} (según pagos {segun})'
            )

        if not aplicar:
            self.stdout.write(self.style.WARNING('Dry-run: no se escribió nada. Usa --apply para escribir.'))
            return

        with transaction.atomic():
            por_valor = {}
            for dte_id, valor in cambios.items():
                por_valor.setdefault(valor, []).append(dte_id)
            total = 0
            for valor, ids in por_valor.items():
                total += Dte.objects.filter(id__in=ids).update(estado_pago=valor)
        self.stdout.write(self.style.SUCCESS(f'Actualizados: {total}'))
