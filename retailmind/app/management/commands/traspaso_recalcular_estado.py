# -*- coding: utf-8 -*-
"""
Cierra los DTE de TRASPASO que quedaron en RECEPCIONADO_PARCIAL /
EN_REGULARIZACION con TODAS sus líneas ya resueltas (hallazgos B7-06 / B14-12).

Causa: `regularizar_dte_masivo` (NC masiva desde "Por regularizar") marca las
líneas REGULARIZADO pero nunca llamaba a `_recalcular_estado_dte`, así que la
cabecera se quedaba en PARCIAL. Efectos: aparecen como "Con problemas" en
Recepción DTE, la lista del receptor los muestra como "pendiente decisión del
emisor" y el Limbo del emisor les ofrece "Corregir" (puerta del doble crédito
B7-01).

Regla (conservadora):
- Solo DTE con tipo_transaccion='TRASPASO' en RECEPCIONADO_PARCIAL o
  EN_REGULARIZACION.
- Deben tener líneas (Productos_Recepcionados) y NINGUNA en OPEN_LINE_STATES
  (la misma definición que usa el cierre canónico en views.py).
- Se EXCLUYEN (y se listan aparte) los DTE con un hijo (NC / AJUSTE POST)
  que exige devolución física sin confirmar
  (requiere_devolucion_fisica=True, fecha_confirmacion_devolucion NULL):
  "Mis Regularizaciones" del receptor (obtener_dtes_regularizacion_receptor_api)
  descarta los RECEPCIONADO_COMPLETO y es el único punto de entrada para
  confirmar ese despacho. Cerrarlos escondería el aviso y dejaría los
  movimientos de devolución en PENDIENTE para siempre (B7-02).
- Solo sube a RECEPCIONADO_COMPLETO; nunca baja un estado ni toca montos,
  stock, NC ni líneas. Deja una marca en `referencias`.

Uso:
    python manage.py traspaso_recalcular_estado            # dry-run (no escribe)
    python manage.py traspaso_recalcular_estado --apply    # aplica
    python manage.py traspaso_recalcular_estado --dte 2182663 --dte 2182664

Medido en la copia local (datos al 2026-06-12), 26-sep-2026: 8 DTE candidatos
(16878, 4748, 16907, 16914, 16916, 16931, 16932, 16933); 16929 queda PARCIAL
porque tiene un FALTANTE real.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app.models import Dte, Productos_Recepcionados

ESTADOS_OBJETIVO = ('RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION')


def _hijo_con_devolucion_pendiente(dte_id):
    """NC / AJUSTE POST del DTE cuya devolución física el receptor aún no
    confirmó (mismo criterio que obtener_dtes_regularizacion_receptor_api)."""
    return (
        Dte.objects
        .filter(documento_afectado_id=dte_id,
                requiere_devolucion_fisica=True,
                fecha_confirmacion_devolucion__isnull=True)
        .order_by('-id')
        .first()
    )


class Command(BaseCommand):
    help = ('Cierra (RECEPCIONADO_COMPLETO) traspasos PARCIAL/EN_REGULARIZACION sin '
            'líneas abiertas. Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin esta bandera solo simula.')
        parser.add_argument('--dte', type=int, action='append', default=[],
                            help='Limita a este id de DTE (repetible).')

    def handle(self, *args, **opts):
        # Fuente única de la definición de "línea abierta".
        from app.views import OPEN_LINE_STATES

        aplicar = opts['apply']
        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(f'== traspaso_recalcular_estado — {modo} =='))

        qs = Dte.objects.filter(tipo_transaccion='TRASPASO', estado_dte__in=ESTADOS_OBJETIVO)
        if opts['dte']:
            qs = qs.filter(id__in=opts['dte'])
        qs = qs.select_related('sucursal').order_by('fecha_emision', 'id')

        candidatos, con_abiertas, sin_lineas, con_devolucion = [], [], [], []
        for dte in qs:
            lineas = Productos_Recepcionados.objects.filter(dte=dte)
            total = lineas.count()
            if total == 0:
                sin_lineas.append(dte)
                continue
            abiertas = lineas.filter(estado__in=OPEN_LINE_STATES).count()
            if abiertas:
                con_abiertas.append((dte, abiertas))
                continue
            hijo = _hijo_con_devolucion_pendiente(dte.id)
            if hijo is not None:
                con_devolucion.append((dte, hijo))
            else:
                candidatos.append((dte, total))

        self.stdout.write(f'DTE revisados: {qs.count()}')
        self.stdout.write(f'  Con líneas abiertas (se dejan igual): {len(con_abiertas)}')
        for dte, n in con_abiertas:
            self.stdout.write(f'    #{dte.numero_documento} (id {dte.id}) {dte.estado_dte}: {n} línea(s) abierta(s)')
        self.stdout.write(f'  Sin líneas de recepción (se dejan igual): {len(sin_lineas)}')
        for dte in sin_lineas:
            self.stdout.write(f'    #{dte.numero_documento} (id {dte.id}) {dte.estado_dte}')
        self.stdout.write(f'  Con devolución física pendiente del receptor (se dejan igual): {len(con_devolucion)}')
        for dte, hijo in con_devolucion:
            self.stdout.write(
                f'    #{dte.numero_documento} (id {dte.id}) {dte.estado_dte}: '
                f'{hijo.tipo_documento} #{hijo.numero_documento} (id {hijo.id}) sin confirmar despacho'
            )
        self.stdout.write(f'  A cerrar como RECEPCIONADO_COMPLETO: {len(candidatos)}')
        for dte, n in candidatos:
            self.stdout.write(
                f'    #{dte.numero_documento} (id {dte.id}) {dte.tipo_documento} '
                f'{dte.sucursal.alias if dte.sucursal else "-"} {dte.estado_dte} -> RECEPCIONADO_COMPLETO '
                f'({n} líneas, todas cerradas)'
            )

        if not aplicar:
            self.stdout.write(self.style.NOTICE('Dry-run: no se escribió nada. Usa --apply para aplicar.'))
            return

        marca = timezone.localtime().strftime('%Y-%m-%d %H:%M')
        aplicados = 0
        for dte, _n in candidatos:
            with transaction.atomic():
                bloqueado = Dte.objects.select_for_update(of=('self',)).get(id=dte.id)
                # Revalidación bajo lock: el estado o las líneas pudieron cambiar.
                if bloqueado.estado_dte not in ESTADOS_OBJETIVO:
                    continue
                if Productos_Recepcionados.objects.filter(
                    dte=bloqueado, estado__in=OPEN_LINE_STATES,
                ).exists():
                    continue
                if _hijo_con_devolucion_pendiente(bloqueado.id) is not None:
                    continue
                anterior = bloqueado.estado_dte
                bloqueado.estado_dte = 'RECEPCIONADO_COMPLETO'
                bloqueado.referencias = (
                    (bloqueado.referencias or '')
                    + f'\n[{marca}] Cierre por traspaso_recalcular_estado: {anterior} -> '
                      f'RECEPCIONADO_COMPLETO (todas las líneas resueltas).'
                ).strip()
                bloqueado.save(update_fields=['estado_dte', 'referencias'])
                aplicados += 1
        self.stdout.write(self.style.SUCCESS(f'Aplicados: {aplicados} DTE.'))
