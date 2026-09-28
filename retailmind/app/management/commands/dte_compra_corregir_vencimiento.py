"""
Corrige fecha_vencimiento de los DTE de COMPRA guardados con vencimiento =
emisión aunque tienen días de crédito (hallazgo B3-04).

crearDteCompras / actualizarDteCompras guardaban fecha_vencimiento =
fecha_emision; el filtro «Vencidos», «Por vencer» y el KPI de deuda leen ese
campo, así que marcaban vencidas facturas con crédito vigente. El código ya
calcula emisión + diasCredito; este comando repara lo histórico.

Solo toca: tipo_transaccion='COMPRA', diasCredito > 0 y
fecha_vencimiento = fecha_emision. No cambia montos ni estados.

Uso:
    python manage.py dte_compra_corregir_vencimiento            # dry-run (por defecto)
    python manage.py dte_compra_corregir_vencimiento --apply    # escribe
    python manage.py dte_compra_corregir_vencimiento --empresa 1802
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F

from app.models import Dte


class Command(BaseCommand):
    help = 'Corrige fecha_vencimiento = emisión + diasCredito en DTE de COMPRA (dry-run por defecto).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios (sin esto solo informa).')
        parser.add_argument('--empresa', type=int, default=None,
                            help='Limita a una empresa receptora (id).')
        parser.add_argument('--muestra', type=int, default=10,
                            help='Cantidad de filas de ejemplo a mostrar.')

    def _qs(self, empresa_id):
        qs = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            diasCredito__gt=0,
            fecha_vencimiento=F('fecha_emision'),
        )
        if empresa_id:
            qs = qs.filter(receptor_id=empresa_id)
        return qs

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        qs = self._qs(opts['empresa'])
        total = qs.count()
        modo = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(f'[{modo}] DTE de COMPRA con vencimiento = emisión y crédito > 0: {total}')

        por_empresa = (qs.values('receptor_id', 'receptor__nombre')
                       .annotate(n=Count('id')).order_by('-n'))
        for fila in por_empresa:
            self.stdout.write(f"  empresa {fila['receptor_id']} {fila['receptor__nombre'] or ''}: {fila['n']}")

        for d in qs.order_by('-fecha_emision', '-id')[:opts['muestra']]:
            nuevo = d.fecha_emision + timedelta(days=d.diasCredito)
            self.stdout.write(
                f'  DTE #{d.id} {d.tipo_documento} N°{d.numero_documento} emisión {d.fecha_emision} '
                f'crédito {d.diasCredito}d: vence {d.fecha_vencimiento} -> {nuevo} (estado_pago={d.estado_pago})'
            )

        if not aplicar:
            self.stdout.write(self.style.WARNING('Dry-run: no se escribió nada. Usa --apply para corregir.'))
            return

        actualizados = 0
        with transaction.atomic():
            for d in qs.select_for_update().only('id', 'fecha_emision', 'diasCredito').iterator():
                Dte.objects.filter(pk=d.pk).update(
                    fecha_vencimiento=d.fecha_emision + timedelta(days=d.diasCredito)
                )
                actualizados += 1
        self.stdout.write(self.style.SUCCESS(f'Actualizados: {actualizados}'))
