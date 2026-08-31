"""Barre las TransaccionMercadoPago colgadas en PENDIENTE/CREADA.

Un cobro queda colgado cuando el cajero cerró la pestaña o se cayó la red
antes de que el polling/webhook resolviera el estado. Este comando consulta
el estado real en MP para toda transacción PENDIENTE con más de --minutos
(default 30) y la cierra:

- APROBADA sin consumir → queda visible como "aprobada sin venta" (huérfana)
  en la pantalla Dineros MP y en conciliar_mercadopago.
- EXPIRADA / CANCELADA / RECHAZADA → cerrada.

Idempotente y seguro de correr en cron (diario u horario). Read-only hacia
el ERP salvo el estado de las propias transacciones MP.

Uso:
    python manage.py sincronizar_transacciones_mp
    python manage.py sincronizar_transacciones_mp --minutos 10
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import TransaccionMercadoPago
from app.services import mercadopago_service as mp


class Command(BaseCommand):
    help = 'Cierra transacciones Mercado Pago colgadas consultando su estado real en la API'

    def add_arguments(self, parser):
        parser.add_argument('--minutos', type=int, default=30,
                            help='Edad mínima en minutos para considerar una transacción colgada (default 30)')

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(minutes=options['minutos'])
        colgadas = TransaccionMercadoPago.objects.filter(
            tipo='VENTA',
            estado__in=['CREADA', 'PENDIENTE'],
            creado_en__lt=limite,
        ).select_related('config')

        total = colgadas.count()
        self.stdout.write(f'Transacciones colgadas (> {options["minutos"]} min): {total}')

        resueltas = {'APROBADA': 0, 'EXPIRADA': 0, 'CANCELADA': 0, 'RECHAZADA': 0,
                     'sin_cambio': 0, 'error': 0}
        for trx in colgadas:
            try:
                actualizada = mp.consultar_estado(trx, forzar=True)
            except Exception as e:  # noqa: BLE001 — una que falle no corta el barrido
                resueltas['error'] += 1
                self.stderr.write(f'  ERROR {trx.external_reference}: {e}')
                continue
            if actualizada.estado in ('CREADA', 'PENDIENTE'):
                resueltas['sin_cambio'] += 1
            else:
                resueltas[actualizada.estado] = resueltas.get(actualizada.estado, 0) + 1
                marca = ' ⚠️ APROBADA SIN VENTA' if (
                    actualizada.estado == 'APROBADA' and not actualizada.consumida
                ) else ''
                self.stdout.write(
                    f'  {trx.external_reference}: → {actualizada.estado}{marca}'
                )

        self.stdout.write(self.style.SUCCESS(f'Resultado: {resueltas}'))
        huerfanas = TransaccionMercadoPago.objects.filter(
            tipo='VENTA', estado='APROBADA', consumida=False,
        ).count()
        if huerfanas:
            self.stdout.write(self.style.WARNING(
                f'⚠️  {huerfanas} cobro(s) APROBADOS sin venta asociada — '
                'revisar en /app/ventas/dineros-mercadopago/ o correr conciliar_mercadopago'
            ))
