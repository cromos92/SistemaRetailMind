"""
Expira los lotes de puntos de fidelización vencidos.

Crea movimientos EXPIRACION (FIFO por lote) para cada lote ACUMULACION/
BIENVENIDA cuya fecha_expiracion ya pasó y que aún tiene saldo. Ajusta el
saldo denormalizado de cada CuentaPuntos.

Pensado para correr a diario por cron / Celery beat:
    python manage.py expirar_puntos
    python manage.py expirar_puntos --dry-run
"""
from django.core.management.base import BaseCommand

from app.services import fidelizacion_service


class Command(BaseCommand):
    help = 'Expira los lotes de puntos de fidelización vencidos (FIFO).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo reporta cuántos puntos se expirarían, sin aplicar cambios.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            from django.utils import timezone
            from django.db.models import F
            from app.models import MovimientoPuntos

            hoy = timezone.localdate()
            lotes = MovimientoPuntos.objects.filter(
                puntos__gt=0,
                fecha_expiracion__lt=hoy,
                puntos_consumidos_del_lote__lt=F('puntos'),
            )
            total = sum(
                max(0, l.puntos - l.puntos_consumidos_del_lote) for l in lotes
            )
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Se expirarían {total} puntos en {lotes.count()} lotes.'
            ))
            return

        total = fidelizacion_service.expirar_lotes_vencidos()
        self.stdout.write(self.style.SUCCESS(
            f'>> {total} puntos expirados correctamente.'
        ))
