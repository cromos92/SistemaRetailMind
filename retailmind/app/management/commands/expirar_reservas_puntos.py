"""
Expira las reservas de puntos vencidas (compra híbrida desde la app).

Marca como EXPIRADA toda ReservaPuntos en estado RESERVADA cuyo TTL ya pasó.
NO toca el ledger (una reserva nunca debita puntos; el débito solo ocurre al
confirmar un pago real). Liberar la reserva devuelve esos puntos al "saldo
disponible para reservar".

Pensado para correr cada pocos minutos por cron / Celery beat:
    python manage.py expirar_reservas_puntos
    python manage.py expirar_reservas_puntos --dry-run
"""
from django.core.management.base import BaseCommand

from app.services import fidelizacion_service


class Command(BaseCommand):
    help = 'Expira las reservas de puntos vencidas de la compra desde la app.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo reporta cuántas reservas se expirarían, sin aplicar cambios.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            from django.utils import timezone
            from app.models import ReservaPuntos

            qs = ReservaPuntos.objects.filter(
                estado='RESERVADA', expira_en__lt=timezone.now(),
            )
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Se expirarían {qs.count()} reservas de puntos.'
            ))
            return

        total = fidelizacion_service.expirar_reservas_vencidas()
        self.stdout.write(self.style.SUCCESS(
            f'>> {total} reservas de puntos expiradas.'
        ))
