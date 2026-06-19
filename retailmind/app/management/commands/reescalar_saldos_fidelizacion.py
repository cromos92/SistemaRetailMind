"""
Mide y (opcionalmente) reescala los saldos de fidelización por un factor.

Caso de uso: al bajar `ProgramaFidelizacion.valor_punto_en_pesos` (p.ej. de 10 a
1 para la estrategia "1 punto = $1"), el saldo en PUNTOS no cambia pero su valor
en PESOS se divide por ese mismo factor — devaluando los saldos existentes. Para
PRESERVAR el valor en pesos hay que multiplicar todos los saldos (y el ledger
completo) por `factor = valor_viejo / valor_nuevo` (10 en el ejemplo).

Por defecto solo MIDE (dry-run): imprime cuántos puntos/pesos hay circulando y
si conviene reescalar. El módulo es reciente, así que lo más probable es que el
saldo sea ~0 y NO haga falta migrar nada — en ese caso se puede cambiar el valor
del punto directamente sin correr `--apply`.

Idempotencia: al aplicar se siembra un movimiento centinela (puntos=0, inerte
para saldo/FIFO) con `idempotency_key='reescala_saldos:x<factor>'`. Si ya existe,
el comando aborta para no multiplicar dos veces.

Consistencia: se multiplican `CuentaPuntos.saldo_puntos` y, en el ledger
`MovimientoPuntos`, `puntos`, `saldo_resultante` y `puntos_consumidos_del_lote`
(los tres, para mantener la invariante de lote `saldo_lote = puntos -
puntos_consumidos` y el FIFO/expiración). También los compromisos vivos
(`ReservaPuntos` RESERVADA, `CanjeVale` PENDIENTE); sus `valor_pesos` (snapshots)
NO se tocan, por diseño preservan pesos.

Uso:
    python manage.py reescalar_saldos_fidelizacion              # dry-run (mide)
    python manage.py reescalar_saldos_fidelizacion --apply      # aplica (factor 10)
    python manage.py reescalar_saldos_fidelizacion --factor 5 --apply

Correr en ventana de baja/nula actividad de caja (es un reescalado masivo).
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, Sum

from app.models import (
    ProgramaFidelizacion,
    CuentaPuntos,
    MovimientoPuntos,
    ReservaPuntos,
    CanjeVale,
)


class Command(BaseCommand):
    help = (
        "Mide y (opcional, con --apply) reescala los saldos de fidelización por un "
        "factor para preservar el valor en pesos al cambiar valor_punto_en_pesos. "
        "Dry-run por defecto."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica el reescalado. Sin esta bandera solo mide (dry-run).',
        )
        parser.add_argument(
            '--factor', type=int, default=10,
            help='Factor = valor_punto_viejo / valor_punto_nuevo (default 10).',
        )

    def handle(self, *args, **opts):
        apply = opts['apply']
        factor = opts['factor']
        if factor < 1:
            self.stderr.write(self.style.ERROR('El factor debe ser >= 1.'))
            return

        modo = 'APLICANDO' if apply else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(
            f'== Reescalado de saldos de fidelización (x{factor}) — {modo} =='))

        # === MEDICIÓN ===
        programa = ProgramaFidelizacion.get_activo()
        valor_actual = programa.valor_punto_en_pesos if programa else 0
        cuentas = CuentaPuntos.objects.all()
        n_cuentas = cuentas.count()
        n_con_saldo = cuentas.filter(saldo_puntos__gt=0).count()
        total_puntos = cuentas.aggregate(s=Sum('saldo_puntos'))['s'] or 0
        total_ledger = MovimientoPuntos.objects.aggregate(s=Sum('puntos'))['s'] or 0
        n_movs = MovimientoPuntos.objects.count()
        reservas_vivas = ReservaPuntos.objects.filter(estado='RESERVADA').count()
        vales_vivos = CanjeVale.objects.filter(estado='PENDIENTE').count()

        self.stdout.write(f'Programa activo: valor_punto_en_pesos = {valor_actual}')
        self.stdout.write(f'Cuentas: {n_cuentas} (con saldo > 0: {n_con_saldo})')
        self.stdout.write(f'Puntos circulantes (cache):  {total_puntos:,}')
        self.stdout.write(f'Puntos circulantes (ledger): {total_ledger:,}  ({n_movs:,} movimientos)')
        self.stdout.write(f'Pasivo a valor ACTUAL ({valor_actual}/pto): ${total_puntos * valor_actual:,}')
        self.stdout.write(f'Reservas RESERVADA vivas: {reservas_vivas} · Vales PENDIENTE vivos: {vales_vivos}')

        # Si el cache no cuadra con el ledger, NO multiplicar (amplificaría el error).
        if total_puntos != total_ledger:
            self.stdout.write(self.style.ERROR(
                f'  ⚠ Cache ({total_puntos}) ≠ ledger ({total_ledger}): reconciliar ANTES '
                'de reescalar. Abortando.'))
            return

        if total_puntos == 0:
            self.stdout.write(self.style.SUCCESS(
                '  Saldos en cero: no hay nada que reescalar. Puedes cambiar '
                'valor_punto_en_pesos directamente sin migrar.'))
            return

        marker = f'reescala_saldos:x{factor}'
        if MovimientoPuntos.objects.filter(idempotency_key=marker).exists():
            self.stdout.write(self.style.ERROR(
                f'  Ya existe el centinela "{marker}": el reescalado x{factor} ya se aplicó. '
                'Abortando (idempotencia).'))
            return

        if not apply:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: con --apply, los {total_puntos:,} pts pasarían a '
                f'{total_puntos * factor:,} pts (×{factor}), preservando el pasivo en pesos. '
                'Re-corré con --apply.'))
            return

        # === APLICAR (atómico) ===
        with transaction.atomic():
            movs = MovimientoPuntos.objects.update(
                puntos=F('puntos') * factor,
                saldo_resultante=F('saldo_resultante') * factor,
                puntos_consumidos_del_lote=F('puntos_consumidos_del_lote') * factor,
            )
            cnt = CuentaPuntos.objects.update(saldo_puntos=F('saldo_puntos') * factor)
            res = ReservaPuntos.objects.filter(estado='RESERVADA').update(
                puntos_reservados=F('puntos_reservados') * factor,
            )
            val = CanjeVale.objects.filter(estado='PENDIENTE').update(
                puntos=F('puntos') * factor,
            )
            # Centinela de idempotencia: puntos=0 → inerte para saldo y FIFO
            # (_consumir_lotes_fifo / expirar filtran puntos > 0).
            primera = CuentaPuntos.objects.order_by('id').first()
            if primera:
                primera.refresh_from_db()
                MovimientoPuntos.objects.create(
                    cuenta=primera, tipo='AJUSTE', puntos=0,
                    saldo_resultante=primera.saldo_puntos,
                    idempotency_key=marker,
                    observaciones=f'Centinela reescalado de saldos x{factor} (idempotencia, no mueve saldo).',
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'  Cuentas reescaladas: {cnt}\n'
            f'  Movimientos ledger:  {movs}\n'
            f'  Reservas RESERVADA:  {res}\n'
            f'  Vales PENDIENTE:     {val}'))
        nuevo_valor = valor_actual // factor if factor else valor_actual
        self.stdout.write(self.style.WARNING(
            f'Listo. Ahora pon valor_punto_en_pesos = {nuevo_valor} en '
            f'/app/fidelizacion/configuracion/ para cerrar el cambio: el pasivo en pesos '
            f'queda igual y el saldo en puntos quedó ×{factor}.'))
